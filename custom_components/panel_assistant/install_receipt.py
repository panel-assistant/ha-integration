"""What an install job receipt is: its phases, result codes and one-receipt codec.

The durable store and its manager live in ``install_jobs``; running a job lives
in ``install_executor``.  This module holds no state and performs no I/O.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from homeassistant.util.ulid import bytes_to_ulid, ulid_to_bytes_or_none

from .app_identity import (
    INSTALL_DESCRIPTOR_SCHEMAS,
    INSTALL_PLAN_SCHEMAS,
    LEGACY_LAUNCH_COMPONENT,
    LEGACY_PACKAGE_ID,
    is_launch_component,
)
from .client import InvalidAddressError, normalize_address
from .install_network import (
    InstallNetworkError,
    _resolver_hostname,
    is_allowed_install_address,
)
from .release import (
    _MAX_ANDROID_SDK,
    _MAX_APK_BYTES,
    _RELEASE_SIGNER_CERTIFICATE_SHA256,
    _SHA256_PATTERN,
    _SUPPORTED_ABIS,
    artifact_identity_matches,
    database_range_valid,
)

_MAX_ATTEMPTS = 32
_MAX_EXECUTOR_GENERATION = 2**31 - 1
_MAX_ADDRESS_LENGTH = 255
_MAX_MODEL_LENGTH = 128
_MAX_RELEASE_TEXT_LENGTH = 128
_MAX_APK_NAME_LENGTH = 255
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_ADB_SERIAL = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", flags=re.ASCII)
_ABI = re.compile(r"^[A-Za-z0-9_.-]{1,64}$", flags=re.ASCII)
_SUBCODE = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$", flags=re.ASCII)
_APK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.apk(?:\.bin)?$")
_PREFLIGHT_ROOT_MODES = frozenset({"root_adbd", "rootless", "root_su"})


class InstallJobError(Exception):
    """Base error for durable install-job state."""


class InstallJobStoreError(InstallJobError):
    """The receipt store was absent, corrupt, or could not be verified."""


class InstallJobConflictError(InstallJobError):
    """A target already has different active frozen installation facts."""


class InstallJobCapacityError(InstallJobError):
    """The bounded active-job capacity has been reached."""


class InstallJobNotFoundError(InstallJobError):
    """No receipt exists for the supplied opaque identifier."""


class InstallJobRevisionError(InstallJobError):
    """The receipt revision did not match the caller's expected revision."""


class InstallJobCleanupRequiredError(InstallJobError):
    """Local custody must be cleaned before this claim can become terminal."""


class InstallJobTransitionError(InstallJobError):
    """A requested receipt transition or field update is invalid."""


class InstallPhase(StrEnum):
    """Durable phases for the first-install workflow."""

    APPROVED = "approved"
    AUTHORIZING = "authorizing"
    PREFLIGHT = "preflight"
    DOWNLOADING = "downloading"
    ARTIFACT_READY = "artifact_ready"
    REVALIDATING = "revalidating"
    STAGING = "staging"
    INSTALLING = "installing"
    INSTALLED = "installed"
    LAUNCHING = "launching"
    HEALTH_CHECK = "health_check"
    HEALTHY_UNCLAIMED = "healthy_unclaimed"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery_required"


class InstallResultCode(StrEnum):
    """Privacy-safe terminal outcomes; raw exception text is never persisted."""

    ENTRY_CREATED = "entry_created"
    CANCELLED_BY_USER = "cancelled_by_user"
    CANCELLED_AFTER_STAGING_CLEANUP = "cancelled_after_staging_cleanup"
    AUTHORIZATION_FAILED = "authorization_failed"
    PREFLIGHT_REJECTED = "preflight_rejected"
    ARTIFACT_REJECTED = "artifact_rejected"
    TRANSPORT_FAILED = "transport_failed"
    INSTALL_FAILED = "install_failed"
    LAUNCH_FAILED = "launch_failed"
    HEALTH_CHECK_FAILED = "health_check_failed"
    AMBIGUOUS_MUTATION = "ambiguous_mutation"
    VERIFICATION_REQUIRED = "verification_required"


_TERMINAL_PHASES = frozenset(
    {
        InstallPhase.CONSUMED,
        InstallPhase.CANCELLED,
        InstallPhase.FAILED,
        InstallPhase.RECOVERY_REQUIRED,
    }
)
_MUTATION_BARRIERS = frozenset(
    {
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.LAUNCHING,
        InstallPhase.HEALTH_CHECK,
    }
)
_CLEANUP_BARRIER_CANCEL_POLARITY: Mapping[InstallPhase, bool] = {
    InstallPhase.STAGING: True,
    InstallPhase.INSTALLING: False,
    InstallPhase.LAUNCHING: False,
}
_ARTIFACT_REQUIRED_PHASES = frozenset(
    {
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.INSTALLED,
        InstallPhase.LAUNCHING,
        InstallPhase.HEALTH_CHECK,
        InstallPhase.HEALTHY_UNCLAIMED,
        InstallPhase.CONSUMED,
    }
)
_PREFLIGHT_ROOT_MODE_REQUIRED_PHASES = frozenset(
    {
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.INSTALLED,
        InstallPhase.LAUNCHING,
        InstallPhase.HEALTH_CHECK,
        InstallPhase.HEALTHY_UNCLAIMED,
        InstallPhase.CONSUMED,
    }
)
_PREFLIGHT_ROOT_MODE_FORBIDDEN_PHASES = frozenset(
    {
        InstallPhase.APPROVED,
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
    }
)
_CANCELLABLE_PHASES = frozenset(
    {
        InstallPhase.APPROVED,
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.STAGING,
    }
)
_FAILURE_CODES = frozenset(
    {
        InstallResultCode.AUTHORIZATION_FAILED,
        InstallResultCode.PREFLIGHT_REJECTED,
        InstallResultCode.ARTIFACT_REJECTED,
        InstallResultCode.TRANSPORT_FAILED,
        InstallResultCode.INSTALL_FAILED,
        InstallResultCode.LAUNCH_FAILED,
        InstallResultCode.HEALTH_CHECK_FAILED,
    }
)
_RECOVERY_CODES = frozenset(
    {
        InstallResultCode.AMBIGUOUS_MUTATION,
        InstallResultCode.VERIFICATION_REQUIRED,
    }
)
_FAILURE_CODES_BY_PHASE: Mapping[InstallPhase, frozenset[InstallResultCode]] = {
    InstallPhase.AUTHORIZING: frozenset(
        {
            InstallResultCode.AUTHORIZATION_FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.PREFLIGHT: frozenset(
        {
            InstallResultCode.PREFLIGHT_REJECTED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.DOWNLOADING: frozenset(
        {
            InstallResultCode.ARTIFACT_REJECTED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.ARTIFACT_READY: frozenset(
        {
            InstallResultCode.ARTIFACT_REJECTED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.REVALIDATING: frozenset(
        {
            InstallResultCode.ARTIFACT_REJECTED,
            InstallResultCode.PREFLIGHT_REJECTED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.STAGING: frozenset(
        {
            InstallResultCode.ARTIFACT_REJECTED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.INSTALLING: frozenset({InstallResultCode.INSTALL_FAILED}),
    InstallPhase.INSTALLED: frozenset({InstallResultCode.INSTALL_FAILED}),
    InstallPhase.LAUNCHING: frozenset(
        {
            InstallResultCode.LAUNCH_FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
    InstallPhase.HEALTH_CHECK: frozenset(
        {
            InstallResultCode.HEALTH_CHECK_FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        }
    ),
}
_RESTART_AMBIGUOUS_PHASES = frozenset(
    {
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.LAUNCHING,
    }
)
_NEXT_PHASE: Mapping[InstallPhase, frozenset[InstallPhase]] = {
    InstallPhase.APPROVED: frozenset(
        {InstallPhase.AUTHORIZING, InstallPhase.CANCELLED, InstallPhase.FAILED}
    ),
    InstallPhase.AUTHORIZING: frozenset(
        {InstallPhase.PREFLIGHT, InstallPhase.CANCELLED, InstallPhase.FAILED}
    ),
    # INSTALLED is the already-satisfied target: the panel was observed running
    # exactly this artifact's bytes, so there is nothing to download, copy or
    # install, and the job goes straight to launching, health and setup.
    InstallPhase.PREFLIGHT: frozenset(
        {
            InstallPhase.DOWNLOADING,
            InstallPhase.INSTALLED,
            InstallPhase.CANCELLED,
            InstallPhase.FAILED,
        }
    ),
    InstallPhase.DOWNLOADING: frozenset(
        {InstallPhase.ARTIFACT_READY, InstallPhase.CANCELLED, InstallPhase.FAILED}
    ),
    InstallPhase.ARTIFACT_READY: frozenset(
        {InstallPhase.REVALIDATING, InstallPhase.CANCELLED, InstallPhase.FAILED}
    ),
    InstallPhase.REVALIDATING: frozenset(
        {InstallPhase.STAGING, InstallPhase.CANCELLED, InstallPhase.FAILED}
    ),
    InstallPhase.STAGING: frozenset(
        {
            InstallPhase.INSTALLING,
            InstallPhase.CANCELLED,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }
    ),
    InstallPhase.INSTALLING: frozenset(
        {
            InstallPhase.INSTALLED,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }
    ),
    InstallPhase.INSTALLED: frozenset(
        {
            InstallPhase.LAUNCHING,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }
    ),
    InstallPhase.LAUNCHING: frozenset(
        {
            InstallPhase.HEALTH_CHECK,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }
    ),
    InstallPhase.HEALTH_CHECK: frozenset(
        {
            InstallPhase.HEALTHY_UNCLAIMED,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }
    ),
    InstallPhase.HEALTHY_UNCLAIMED: frozenset(
        {InstallPhase.CONSUMED, InstallPhase.RECOVERY_REQUIRED}
    ),
}


@dataclass(frozen=True, slots=True)
class InstallTarget:
    """Frozen identity and compatibility facts for one Android target."""

    address: str
    pinned_address: str
    adb_serial: str
    model: str
    primary_abi: str
    android_sdk: int


@dataclass(frozen=True, slots=True)
class InstallArtifact:
    """Authenticated install descriptor retained without a download URL."""

    descriptor_schema: str
    release_tag: str
    version_name: str
    version_code: int
    apk_name: str
    apk_sha256: str
    apk_size: int
    package_id: str
    signer_certificate_sha256: str
    min_sdk: int
    supported_abis: tuple[str, ...]
    database_compatibility: str
    launch_component: str
    protocol_min: int | None = None
    protocol_max: int | None = None
    prerelease_opt_in: bool = False


@dataclass(frozen=True, slots=True)
class InstallJobReceipt:
    """One immutable snapshot of a durable first-install job."""

    job_id: str
    revision: int
    executor_generation: int
    created_at: str
    updated_at: str
    phase: InstallPhase
    cancel_requested: bool
    attempt: int
    target: InstallTarget
    artifact: InstallArtifact
    plan_sha256: str
    adb_credential_id: str
    preflight_root_mode: str | None = None
    actual_apk_bytes: int | None = None
    health_checked_at: str | None = None
    result_code: InstallResultCode | None = None
    consumed_entry_id: str | None = None
    failure_stage: InstallPhase | None = None
    result_subcode: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Return whether no executor may advance this receipt."""
        return self.phase in _TERMINAL_PHASES


def _now_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _safe_text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise InstallJobStoreError
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InstallJobStoreError
    if not minimum <= value <= maximum:
        raise InstallJobStoreError
    return value


def _timestamp(value: object) -> str:
    text = _safe_text(value, 40)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as err:
        raise InstallJobStoreError from err
    if parsed.tzinfo != UTC or parsed.isoformat(timespec="seconds") != text:
        raise InstallJobStoreError
    return text


def _parse_target(value: object) -> InstallTarget:
    if not isinstance(value, dict) or value.keys() != {
        "address",
        "pinned_address",
        "adb_serial",
        "model",
        "primary_abi",
        "android_sdk",
    }:
        raise InstallJobStoreError
    address = _safe_text(value["address"], _MAX_ADDRESS_LENGTH)
    pinned_address = _safe_text(value["pinned_address"], _MAX_ADDRESS_LENGTH)
    try:
        original = normalize_address(address)
        pinned = normalize_address(pinned_address)
        pinned_ip = ipaddress.ip_address(pinned.host)
        if (
            original.stored_value != address
            or pinned.stored_value != pinned_address
            or pinned.host != str(pinned_ip)
            or pinned.port != original.port
            or not is_allowed_install_address(pinned_ip)
        ):
            raise InstallJobStoreError
        try:
            original_ip = ipaddress.ip_address(original.host)
        except ValueError:
            _resolver_hostname(original.host)
        else:
            if original_ip != pinned_ip:
                raise InstallJobStoreError
    except (InstallNetworkError, InvalidAddressError, ValueError) as err:
        raise InstallJobStoreError from err
    model = _safe_text(value["model"], _MAX_MODEL_LENGTH)
    adb_serial = _safe_text(value["adb_serial"], 128)
    primary_abi = _safe_text(value["primary_abi"], 64)
    if (
        not model.isprintable()
        or _ADB_SERIAL.fullmatch(adb_serial) is None
        or _ABI.fullmatch(primary_abi) is None
    ):
        raise InstallJobStoreError
    return InstallTarget(
        address=address,
        pinned_address=pinned_address,
        adb_serial=adb_serial,
        model=model,
        primary_abi=primary_abi,
        android_sdk=_integer(value["android_sdk"], 1, _MAX_ANDROID_SDK),
    )


def _parse_artifact(value: object) -> InstallArtifact:
    required_keys = {
        "descriptor_schema",
        "release_tag",
        "version_name",
        "version_code",
        "apk_name",
        "apk_sha256",
        "apk_size",
        "package_id",
        "signer_certificate_sha256",
        "min_sdk",
        "supported_abis",
        "database_compatibility",
        "launch_component",
    }
    if not isinstance(value, dict) or value.keys() not in (
        required_keys,
        required_keys | {"prerelease_opt_in"},
        required_keys | {"protocol_min", "protocol_max"},
        required_keys | {"protocol_min", "protocol_max", "prerelease_opt_in"},
    ):
        raise InstallJobStoreError
    prerelease_opt_in = value.get("prerelease_opt_in", False)
    if type(prerelease_opt_in) is not bool:
        raise InstallJobStoreError
    protocol_min = value.get("protocol_min")
    protocol_max = value.get("protocol_max")
    if protocol_min is not None or protocol_max is not None:
        protocol_min = _integer(protocol_min, 1, 2**31 - 1)
        protocol_max = _integer(protocol_max, protocol_min, 2**31 - 1)
    release_tag = _safe_text(value["release_tag"], _MAX_RELEASE_TEXT_LENGTH)
    version_name = _safe_text(value["version_name"], _MAX_RELEASE_TEXT_LENGTH)
    apk_name = _safe_text(value["apk_name"], _MAX_APK_NAME_LENGTH)
    apk_sha256 = _safe_text(value["apk_sha256"], 64)
    signer = _safe_text(value["signer_certificate_sha256"], 64)
    abis = value["supported_abis"]
    database = _safe_text(value["database_compatibility"], 128)
    # A receipt stored by an earlier release may name the old app and its one
    # launcher, so the stored id is read back and re-checked rather than
    # replaced by a constant; one bad record would close the whole store.
    package_id = value["package_id"]
    if (
        value["descriptor_schema"] not in INSTALL_DESCRIPTOR_SCHEMAS
        or (
            not is_launch_component(package_id, value["launch_component"])
            and (package_id, value["launch_component"])
            != (LEGACY_PACKAGE_ID, LEGACY_LAUNCH_COMPONENT)
        )
        or signer != _RELEASE_SIGNER_CERTIFICATE_SHA256
        or not isinstance(abis, (list, tuple))
        or tuple(abis) != _SUPPORTED_ABIS
        or not artifact_identity_matches(
            release_tag,
            version_name,
            value["version_code"],
            apk_name,
            apk_sha256,
            package_id,
        )
        or _APK_NAME.fullmatch(apk_name) is None
        or _SHA256_PATTERN.fullmatch(apk_sha256) is None
        or not database_range_valid(database)
    ):
        raise InstallJobStoreError
    return InstallArtifact(
        descriptor_schema=value["descriptor_schema"],
        release_tag=release_tag,
        version_name=version_name,
        version_code=_integer(value["version_code"], 1, 2**31 - 1),
        apk_name=apk_name,
        apk_sha256=apk_sha256,
        apk_size=_integer(value["apk_size"], 1, _MAX_APK_BYTES),
        package_id=package_id,
        signer_certificate_sha256=signer,
        min_sdk=_integer(value["min_sdk"], 1, _MAX_ANDROID_SDK),
        supported_abis=_SUPPORTED_ABIS,
        database_compatibility=database,
        launch_component=value["launch_component"],
        protocol_min=protocol_min,
        protocol_max=protocol_max,
        prerelease_opt_in=prerelease_opt_in,
    )


def _artifact_document(artifact: InstallArtifact) -> dict[str, Any]:
    """Preserve historical plan hashes when no range proof was recorded."""
    document = asdict(artifact)
    if artifact.protocol_min is None and artifact.protocol_max is None:
        document.pop("protocol_min")
        document.pop("protocol_max")
    if not artifact.prerelease_opt_in:
        document.pop("prerelease_opt_in")
    return document


def install_plan_sha256(
    target: InstallTarget,
    artifact: InstallArtifact,
    adb_credential_id: str,
    schema: str = INSTALL_PLAN_SCHEMAS[0],
) -> str:
    """Bind every frozen mutation input into one canonical plan digest.

    A receipt written before 0.9.0 was bound under the legacy schema name, so
    loading one checks that name too (see ``_parse_receipt``).
    """
    parsed_target = _parse_target(asdict(target))
    parsed_artifact = _parse_artifact(asdict(artifact))
    credential_id = _safe_text(adb_credential_id, 64)
    if _SHA256_PATTERN.fullmatch(credential_id) is None:
        raise InstallJobStoreError
    document = {
        "schema": schema,
        "target": asdict(parsed_target),
        "artifact": _artifact_document(parsed_artifact),
        "adb_credential_id": credential_id,
    }
    canonical = (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return sha256(canonical).hexdigest()


def _is_config_entry_id(value: str) -> bool:
    """Accept both legacy hexadecimal IDs and canonical current HA ULIDs."""
    if _HEX_32.fullmatch(value) is not None:
        return True
    if len(value) != 26:
        return False
    decoded = ulid_to_bytes_or_none(value)
    return decoded is not None and bytes_to_ulid(decoded) == value


def _parse_receipt(value: object) -> InstallJobReceipt:
    old_keys = {
        "job_id",
        "revision",
        "executor_generation",
        "created_at",
        "updated_at",
        "phase",
        "cancel_requested",
        "attempt",
        "target",
        "artifact",
        "plan_sha256",
        "adb_credential_id",
        "preflight_root_mode",
        "actual_apk_bytes",
        "health_checked_at",
        "result_code",
        "consumed_entry_id",
    }
    if not isinstance(value, dict) or value.keys() != old_keys:
        raise InstallJobStoreError
    job_id = _safe_text(value["job_id"], 32)
    plan_sha256 = _safe_text(value["plan_sha256"], 64)
    credential_id = _safe_text(value["adb_credential_id"], 64)
    if (
        _HEX_32.fullmatch(job_id) is None
        or _SHA256_PATTERN.fullmatch(plan_sha256) is None
        or _SHA256_PATTERN.fullmatch(credential_id) is None
    ):
        raise InstallJobStoreError
    try:
        phase = InstallPhase(value["phase"])
        result = (
            None
            if value["result_code"] is None
            else InstallResultCode(value["result_code"])
        )
    except (TypeError, ValueError) as err:
        raise InstallJobStoreError from err
    cancel_requested = value["cancel_requested"]
    if not isinstance(cancel_requested, bool):
        raise InstallJobStoreError
    preflight_root_mode = value["preflight_root_mode"]
    if preflight_root_mode is not None and (
        not isinstance(preflight_root_mode, str)
        or preflight_root_mode not in _PREFLIGHT_ROOT_MODES
    ):
        raise InstallJobStoreError
    actual = value["actual_apk_bytes"]
    if actual is not None:
        actual = _integer(actual, 1, _MAX_APK_BYTES)
    health = value["health_checked_at"]
    if health is not None:
        health = _timestamp(health)
    entry_id = value["consumed_entry_id"]
    if entry_id is not None:
        entry_id = _safe_text(entry_id, 32)
        if not _is_config_entry_id(entry_id):
            raise InstallJobStoreError
    receipt = InstallJobReceipt(
        job_id=job_id,
        revision=_integer(value["revision"], 0, 2**63 - 1),
        executor_generation=_integer(
            value["executor_generation"], 0, _MAX_EXECUTOR_GENERATION
        ),
        created_at=_timestamp(value["created_at"]),
        updated_at=_timestamp(value["updated_at"]),
        phase=phase,
        cancel_requested=cancel_requested,
        attempt=_integer(value["attempt"], 0, _MAX_ATTEMPTS),
        target=_parse_target(value["target"]),
        artifact=_parse_artifact(value["artifact"]),
        plan_sha256=plan_sha256,
        adb_credential_id=credential_id,
        preflight_root_mode=preflight_root_mode,
        actual_apk_bytes=actual,
        health_checked_at=health,
        result_code=result,
        consumed_entry_id=entry_id,
    )
    _validate_receipt_invariants(receipt)
    return receipt


def _validate_receipt_invariants(receipt: InstallJobReceipt) -> None:
    if receipt.failure_stage is not None and not isinstance(
        receipt.failure_stage, InstallPhase
    ):
        raise InstallJobStoreError
    if receipt.result_subcode is not None and receipt.failure_stage is None:
        raise InstallJobStoreError
    if receipt.result_subcode is not None and (
        not isinstance(receipt.result_subcode, str)
        or len(receipt.result_subcode) > 96
        or _SUBCODE.fullmatch(receipt.result_subcode) is None
        or receipt.failure_stage in _TERMINAL_PHASES
    ):
        raise InstallJobStoreError
    if receipt.phase not in {InstallPhase.FAILED, InstallPhase.RECOVERY_REQUIRED} and (
        receipt.failure_stage is not None or receipt.result_subcode is not None
    ):
        raise InstallJobStoreError
    if (
        receipt.phase is InstallPhase.FAILED
        and receipt.failure_stage is not None
        and receipt.result_code
        not in _FAILURE_CODES_BY_PHASE.get(receipt.failure_stage, frozenset())
    ):
        raise InstallJobStoreError
    if receipt.updated_at < receipt.created_at:
        raise InstallJobStoreError
    if (
        receipt.actual_apk_bytes is not None
        and receipt.actual_apk_bytes != receipt.artifact.apk_size
    ):
        raise InstallJobStoreError
    if not any(
        receipt.plan_sha256
        == install_plan_sha256(
            receipt.target, receipt.artifact, receipt.adb_credential_id, schema
        )
        for schema in INSTALL_PLAN_SCHEMAS
    ):
        raise InstallJobStoreError
    if (
        receipt.target.primary_abi not in receipt.artifact.supported_abis
        or receipt.target.android_sdk < receipt.artifact.min_sdk
    ):
        raise InstallJobStoreError
    if (
        receipt.attempt != receipt.executor_generation
        or receipt.revision < receipt.executor_generation
    ):
        raise InstallJobStoreError
    if (
        receipt.phase in _PREFLIGHT_ROOT_MODE_FORBIDDEN_PHASES
        and receipt.preflight_root_mode is not None
    ) or (
        receipt.phase in _PREFLIGHT_ROOT_MODE_REQUIRED_PHASES
        and receipt.preflight_root_mode not in _PREFLIGHT_ROOT_MODES
    ):
        raise InstallJobStoreError
    if receipt.phase in _ARTIFACT_REQUIRED_PHASES and receipt.actual_apk_bytes is None:
        raise InstallJobStoreError
    if (
        receipt.phase not in _ARTIFACT_REQUIRED_PHASES
        and receipt.phase not in _TERMINAL_PHASES
        and receipt.actual_apk_bytes is not None
    ):
        raise InstallJobStoreError
    if receipt.phase == InstallPhase.CONSUMED:
        if (
            receipt.result_code != InstallResultCode.ENTRY_CREATED
            or receipt.consumed_entry_id is None
            or receipt.health_checked_at is None
        ):
            raise InstallJobStoreError
    elif receipt.consumed_entry_id is not None:
        raise InstallJobStoreError
    if receipt.phase == InstallPhase.CANCELLED:
        if (
            receipt.result_code
            not in {
                InstallResultCode.CANCELLED_BY_USER,
                InstallResultCode.CANCELLED_AFTER_STAGING_CLEANUP,
            }
            or not receipt.cancel_requested
        ):
            raise InstallJobStoreError
    elif receipt.phase == InstallPhase.FAILED:
        if receipt.result_code not in _FAILURE_CODES:
            raise InstallJobStoreError
    elif receipt.phase == InstallPhase.RECOVERY_REQUIRED:
        if receipt.result_code not in _RECOVERY_CODES:
            raise InstallJobStoreError
    elif receipt.phase != InstallPhase.CONSUMED and receipt.result_code is not None:
        raise InstallJobStoreError
    if receipt.phase in {
        InstallPhase.HEALTHY_UNCLAIMED,
        InstallPhase.CONSUMED,
    }:
        if receipt.health_checked_at is None:
            raise InstallJobStoreError
    elif receipt.health_checked_at is not None:
        raise InstallJobStoreError
    if (
        receipt.health_checked_at is not None
        and not receipt.created_at <= receipt.health_checked_at <= receipt.updated_at
    ):
        raise InstallJobStoreError


def _serialize_receipt(receipt: InstallJobReceipt) -> dict[str, Any]:
    data = asdict(receipt)
    data["artifact"] = _artifact_document(receipt.artifact)
    # Failure details remain separate from the primary receipt Store.
    data.pop("failure_stage")
    data.pop("result_subcode")
    data["phase"] = receipt.phase.value
    data["artifact"]["supported_abis"] = list(receipt.artifact.supported_abis)
    data["result_code"] = (
        None if receipt.result_code is None else receipt.result_code.value
    )
    return data

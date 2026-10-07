"""Pure binding of authenticated installer inputs into one durable plan."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from enum import StrEnum

from .client import InvalidAddressError, PanelAddress, normalize_address
from .install_jobs import (
    InstallArtifact,
    InstallJobStoreError,
    InstallTarget,
    install_plan_sha256,
)
from .install_network import PinnedPanelTarget, is_allowed_install_address
from .provisioning import InstallTargetProbe, InstallTargetState
from .release import (
    _SHA256_PATTERN,
    InstallDescriptor,
    ReleaseArtifact,
    install_descriptor_valid,
    is_feed_build_tag,
    is_install_release_tag,
)
from .update_policy import build_allowed, prereleases_allowed

_MAX_SDK = 100
_MAX_ADDRESS_LENGTH = 255
_MAX_MODEL_LENGTH = 128
_MAX_APK_NAME_LENGTH = 255
_MAX_RELEASE_TEXT_LENGTH = 128
_ADB_SERIAL = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", flags=re.ASCII)
_ABI = re.compile(r"^[A-Za-z0-9_.-]{1,64}$", flags=re.ASCII)
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class InstallPlanErrorCode(StrEnum):
    """Stable, privacy-safe failures while binding an installation plan."""

    INVALID_TARGET = "invalid_target"
    PROBE_NOT_INSTALL_CANDIDATE = "probe_not_install_candidate"
    INCOMPLETE_PROBE = "incomplete_probe"
    PREVIEW_ONLY_RELEASE = "preview_only_release"
    INVALID_RELEASE = "invalid_release"
    INCOMPATIBLE_RELEASE = "incompatible_release"
    INVALID_CREDENTIAL = "invalid_credential"
    INVALID_PLAN = "invalid_plan"


class InstallPlanError(Exception):
    """Raised with only a stable code, never input or exception details."""

    def __init__(self, code: InstallPlanErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """Frozen job inputs and their deterministic authorization digest."""

    target: InstallTarget
    artifact: InstallArtifact
    plan_sha256: str
    adb_credential_id: str


def _bounded_integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _safe_text(value: object, maximum: int) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or not value.isprintable()
    ):
        return None
    return value


def _canonical_address(address: object) -> PanelAddress | None:
    if not isinstance(address, PanelAddress):
        return None
    if (
        not isinstance(address.host, str)
        or isinstance(address.port, bool)
        or not isinstance(address.port, int)
        or not 1 <= address.port <= 65535
    ):
        return None
    stored = address.stored_value
    if len(stored) > _MAX_ADDRESS_LENGTH:
        return None
    try:
        normalized = normalize_address(stored)
    except InvalidAddressError:
        return None
    return address if normalized == address else None


def _valid_dns_name(host: str) -> bool:
    if len(host) > 253 or not host.isascii() or "%" in host:
        return False
    try:
        socket.inet_aton(host)
    except OSError, ValueError:
        pass
    else:
        return False
    labels = host[:-1].split(".") if host.endswith(".") else host.split(".")
    return bool(labels) and all(_DNS_LABEL.fullmatch(label) for label in labels)


def _build_target(
    pinned_target: PinnedPanelTarget, probe: InstallTargetProbe
) -> InstallTarget:
    if not isinstance(pinned_target, PinnedPanelTarget):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET)
    original = _canonical_address(pinned_target.original)
    pinned = _canonical_address(pinned_target.pinned)
    if original is None or pinned is None or original.port != pinned.port:
        raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET)

    try:
        pinned_ip = ipaddress.ip_address(pinned.host)
    except ValueError:
        raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET) from None
    if str(pinned_ip) != pinned.host or not is_allowed_install_address(pinned_ip):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET)

    try:
        original_ip = ipaddress.ip_address(original.host)
    except ValueError:
        if not _valid_dns_name(original.host):
            raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET) from None
    else:
        if original_ip != pinned_ip:
            raise InstallPlanError(InstallPlanErrorCode.INVALID_TARGET)

    if not isinstance(probe, InstallTargetProbe):
        raise InstallPlanError(InstallPlanErrorCode.INCOMPLETE_PROBE)
    if probe.state not in {
        InstallTargetState.INSTALL_CANDIDATE,
        InstallTargetState.INSTALLED,
        InstallTargetState.MIGRATION_CANDIDATE,
    }:
        raise InstallPlanError(InstallPlanErrorCode.PROBE_NOT_INSTALL_CANDIDATE)

    model = _safe_text(probe.model, _MAX_MODEL_LENGTH)
    serial = _safe_text(probe.serial, 128)
    primary_abi = _safe_text(probe.primary_abi, 64)
    android_sdk = _bounded_integer(probe.android_sdk, 1, _MAX_SDK)
    if (
        model is None
        or serial is None
        or _ADB_SERIAL.fullmatch(serial) is None
        or primary_abi is None
        or _ABI.fullmatch(primary_abi) is None
        or android_sdk is None
    ):
        raise InstallPlanError(InstallPlanErrorCode.INCOMPLETE_PROBE)

    return InstallTarget(
        address=original.stored_value,
        pinned_address=pinned.stored_value,
        adb_serial=serial,
        model=model,
        primary_abi=primary_abi,
        android_sdk=android_sdk,
    )


def _selection_matches(release_tag: str, expected_tag: str | None) -> bool:
    """Bind an explicit selection exactly; the default follows running PA."""
    if expected_tag is None:
        return is_install_release_tag(release_tag) or is_feed_build_tag(release_tag)
    return release_tag == expected_tag and (
        is_install_release_tag(expected_tag) or is_feed_build_tag(expected_tag)
    )


def _build_artifact(
    release: ReleaseArtifact,
    expected_rc_tag: str | None,
    *,
    prerelease_opt_in: bool | None = None,
) -> InstallArtifact:
    if not isinstance(release, ReleaseArtifact):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_RELEASE)
    descriptor = release.descriptor
    if descriptor is None:
        raise InstallPlanError(InstallPlanErrorCode.PREVIEW_ONLY_RELEASE)
    if not isinstance(descriptor, InstallDescriptor):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_RELEASE)

    release_tag = _safe_text(descriptor.release_tag, _MAX_RELEASE_TEXT_LENGTH)
    if (
        not install_descriptor_valid(descriptor)
        or release_tag is None
        or _safe_text(descriptor.version_name, _MAX_RELEASE_TEXT_LENGTH) is None
        or _safe_text(descriptor.apk_name, _MAX_APK_NAME_LENGTH) is None
        or not _selection_matches(release_tag, expected_rc_tag)
        or release.tag != release_tag
        or release.version != descriptor.version_name
        or release.apk_name != descriptor.apk_name
        or release.sha256 != descriptor.apk_sha256
    ):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_RELEASE)

    if prerelease_opt_in is None:
        prerelease_opt_in = expected_rc_tag is not None and "-" in release.version
    elif type(prerelease_opt_in) is not bool:
        raise InstallPlanError(InstallPlanErrorCode.INVALID_RELEASE)
    if not build_allowed(
        release.version,
        release.protocol_min,
        release.protocol_max,
        allow_prerelease=prereleases_allowed() or prerelease_opt_in,
    ):
        raise InstallPlanError(InstallPlanErrorCode.INCOMPATIBLE_RELEASE)

    return InstallArtifact(
        descriptor_schema=descriptor.schema,
        release_tag=descriptor.release_tag,
        version_name=descriptor.version_name,
        version_code=descriptor.version_code,
        apk_name=descriptor.apk_name,
        apk_sha256=descriptor.apk_sha256,
        apk_size=descriptor.apk_size,
        package_id=descriptor.package_id,
        signer_certificate_sha256=descriptor.signer_certificate_sha256,
        min_sdk=descriptor.min_sdk,
        supported_abis=descriptor.supported_abis,
        database_compatibility=descriptor.database_compatibility,
        launch_component=descriptor.launch_component,
        protocol_min=release.protocol_min,
        protocol_max=release.protocol_max,
        prerelease_opt_in=prerelease_opt_in,
    )


def build_install_plan(
    pinned_target: PinnedPanelTarget,
    probe: InstallTargetProbe,
    release: ReleaseArtifact,
    adb_credential_id: str,
    *,
    expected_rc_tag: str | None = None,
    prerelease_opt_in: bool | None = None,
) -> InstallPlan:
    """Validate and bind one exact target, release, and ADB key generation."""
    # Target first, as before: the package id no longer has to be known to
    # validate the target, so an input bad in both ways reports the target
    # error a consumer branching on the code already expects.
    target = _build_target(pinned_target, probe)
    artifact = _build_artifact(
        release, expected_rc_tag, prerelease_opt_in=prerelease_opt_in
    )
    if (
        probe.state is not InstallTargetState.INSTALL_CANDIDATE
        and probe.installed_artifact_size != artifact.apk_size
    ):
        # An installed package enters a plan only after the config flow has
        # proved its size and digest against this signed descriptor. The
        # executor repeats that proof before it adopts anything.
        raise InstallPlanError(InstallPlanErrorCode.PROBE_NOT_INSTALL_CANDIDATE)
    if (
        not isinstance(adb_credential_id, str)
        or _SHA256_PATTERN.fullmatch(adb_credential_id) is None
    ):
        raise InstallPlanError(InstallPlanErrorCode.INVALID_CREDENTIAL)
    if (
        target.android_sdk < artifact.min_sdk
        or target.primary_abi not in artifact.supported_abis
    ):
        raise InstallPlanError(InstallPlanErrorCode.INCOMPATIBLE_RELEASE)
    try:
        plan_sha256 = install_plan_sha256(target, artifact, adb_credential_id)
    except InstallJobStoreError:
        raise InstallPlanError(InstallPlanErrorCode.INVALID_PLAN) from None

    return InstallPlan(
        target=target,
        artifact=artifact,
        plan_sha256=plan_sha256,
        adb_credential_id=adb_credential_id,
    )

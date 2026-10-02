"""Bounded ADB primitives for a clean install or in-place app update.

This module deliberately does not own orchestration.  Every public operation
opens a fresh authenticated ADB connection and either returns a small,
privacy-safe result or raises :class:`InstallAdbError` with a stable code.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import math
import os
import re
import shlex
import stat
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from secrets import token_hex
from time import time

from adb_shell.adb_device_async import AdbDeviceAsync
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
    AdbTimeoutError,
    DeviceAuthError,
    DevicePathInvalidError,
    InvalidChecksumError,
    InvalidCommandError,
    InvalidResponseError,
    InvalidTransportError,
    PushFailedError,
    TcpTimeoutException,
)
from adb_shell.transport.tcp_transport_async import TcpTransportAsync

from .app_identity import (
    ACCEPTED_PACKAGE_IDS,
    ACCESSIBILITY_COMPONENTS,
    EQUIVALENT_ACCESSIBILITY_COMPONENTS,
    HOME_COMPONENTS,
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
    counterpart_of,
    is_accepted_package_id,
    launch_component_for,
)
from .client import PanelAddress
from .install_network import is_allowed_install_address
from .release import InstallDescriptor

ADB_PORT = 5555
_LOGGER = logging.getLogger(__name__)

_ADB_BANNER = "ha-paneld-home-assistant"
_PACKAGE_MANAGER_LIVENESS_PACKAGE = "android"
# Frozen on the legacy spelling: released integrations compare it byte for byte.
_DESCRIPTOR_SCHEMA = "io.github.maxlyth.hapaneld.install.v1"
_RELEASE_SIGNER_SHA256 = (
    "ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339"
)
_SUPPORTED_ABIS = ("arm64-v8a", "armeabi-v7a")
_REMOTE_PREFIX = "/data/local/tmp/ha-paneld-install-"
_MAX_APK_BYTES = 64 * 1024 * 1024
_MIN_ADB_MAXDATA = 4 * 1024
_MAX_ADB_MAXDATA = 1024 * 1024
_MAX_ADB_PACKET_BYTES = _MAX_ADB_MAXDATA
_MAX_CONNECTION_READ_BYTES = 2 * 1024 * 1024
_MAX_CONNECTION_WRITE_BYTES = _MAX_APK_BYTES + 4 * 1024 * 1024
_MAX_SHELL_RESPONSE_BYTES = 32 * 1024
_CONNECT_TIMEOUT_SECONDS = 5.0
_TRANSPORT_TIMEOUT_SECONDS = 10.0
_MAX_TRANSPORT_TIMEOUT_SECONDS = 180.0
_READ_TIMEOUT_SECONDS = 10.0
_INSTALL_READ_TIMEOUT_SECONDS = 30.0
_CLOSE_TIMEOUT_SECONDS = 2.0
_PREFLIGHT_TIMEOUT_SECONDS = 30.0
_STAGE_TIMEOUT_SECONDS = 180.0
_INSTALL_TIMEOUT_SECONDS = 180.0
_LAUNCH_TIMEOUT_SECONDS = 30.0
_CLEANUP_TIMEOUT_SECONDS = 30.0
_UPDATE_TIMEOUT_SECONDS = (
    _PREFLIGHT_TIMEOUT_SECONDS
    + _STAGE_TIMEOUT_SECONDS
    + _INSTALL_TIMEOUT_SECONDS
    + _CLEANUP_TIMEOUT_SECONDS
)
_REMOTE_MODE = stat.S_IFREG | 0o644
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$", flags=re.ASCII)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", flags=re.ASCII)
_ABI_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$", flags=re.ASCII)
_APK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.apk$", re.ASCII)
_ROOT_DATA_BASES = ("/data/user/0", "/data/data", "/data/user_de/0")
# Residue is probed per accepted application id: during the identity migration
# a panel may hold the data directory of either package, and only the data of
# the package being installed makes the target unclean.
_RESIDUE_PROBES: tuple[tuple[str, str], ...] = tuple(
    (package_id, f"{base}/{package_id}")
    for package_id in ACCEPTED_PACKAGE_IDS
    for base in _ROOT_DATA_BASES
)
_RESIDUE_PATHS = tuple(path for _package_id, path in _RESIDUE_PROBES)
_SU_PREFIXES = ("su 0", "su 0 sh -c", "su root", "su root sh -c", "su -c")
_SU_TIMEOUT_SECONDS = 3.0

_ADB_EXCEPTIONS = (
    AdbConnectionError,
    AdbTimeoutError,
    DevicePathInvalidError,
    InvalidChecksumError,
    InvalidCommandError,
    InvalidResponseError,
    InvalidTransportError,
    OSError,
    PushFailedError,
    TcpTimeoutException,
)


class InstallAdbErrorCode(StrEnum):
    """Stable, privacy-safe failure codes for the installer executor."""

    INVALID_REQUEST = "invalid_request"
    TARGET_UNREACHABLE = "target_unreachable"
    AUTHORIZATION_REQUIRED = "authorization_required"
    TARGET_CHANGED = "target_changed"
    ROOT_MODE_CHANGED = "root_mode_changed"
    TARGET_RESPONSE_INVALID = "target_response_invalid"
    TARGET_INCOMPATIBLE = "target_incompatible"
    TARGET_NOT_CLEAN = "target_not_clean"
    INSTALLED_PACKAGE_MISSING = "installed_package_missing"
    ROOT_STATE_AMBIGUOUS = "root_state_ambiguous"
    LOCAL_ARTIFACT_INVALID = "local_artifact_invalid"
    FILESYNC_UNSAFE = "filesync_unsafe"
    STAGING_PATH_OCCUPIED = "staging_path_occupied"
    STAGE_AMBIGUOUS = "stage_ambiguous"
    STAGE_VERIFICATION_FAILED = "stage_verification_failed"
    INSTALL_AMBIGUOUS = "install_ambiguous"
    LAUNCH_AMBIGUOUS = "launch_ambiguous"
    CLEANUP_AMBIGUOUS = "cleanup_ambiguous"


class InstallAdbError(Exception):
    """An ADB install failure which never includes peer or filesystem text."""

    def __init__(self, code: InstallAdbErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class AdbRootMode(StrEnum):
    """ADB privilege postures; delegated root requires a fresh runtime proof."""

    ROOT_ADBD = "root_adbd"
    ROOTLESS = "rootless"
    ROOT_SU = "root_su"


class InstallOutcome(StrEnum):
    """Definite package-manager outcomes."""

    INSTALLED = "installed"
    REFUSED = "refused"


class LaunchOutcome(StrEnum):
    """Definite activity-manager outcomes."""

    STARTED = "started"
    REFUSED = "refused"


class DefiniteCleanupReason(StrEnum):
    """States which allow deletion of the one job-owned staging path."""

    CANCELLED = "cancelled"
    INSTALL_SUCCEEDED = "install_succeeded"
    INSTALL_REFUSED = "install_refused"


@dataclass(frozen=True, slots=True)
class AdbInstallTarget:
    """One pre-pinned private address and its previously observed identity."""

    address: PanelAddress
    serial: str
    model: str
    primary_abi: str
    android_sdk: int


@dataclass(frozen=True, slots=True)
class AdbPreflight:
    """Fresh clean-install admission facts from one ADB connection."""

    serial: str
    model: str
    primary_abi: str
    android_sdk: int
    root_mode: AdbRootMode
    # True when the panel already runs the legacy package and the successor is
    # being installed beside it. The panel migrates itself afterwards; this
    # integration only records that the handover window is expected.
    migration_candidate: bool = False
    # True when the panel already holds exactly the package being installed and
    # nothing else. Only a caller that asked to admit that case ever sees it,
    # and it is not permission to install: whether the panel runs these exact
    # bytes is a separate observation.
    target_installed: bool = False


@dataclass(frozen=True, slots=True)
class StagedApk:
    """Exact remote artifact verified after a completed FileSync push."""

    job_id: str
    remote_path: str
    apk_size: int
    apk_sha256: str


class _MalformedAdbResponse(Exception):
    """The peer did not return a complete bounded protocol frame."""


class _UnsafeAdbPacket(Exception):
    """The peer or library requested an unsafe ADB transport operation."""


@dataclass(frozen=True, slots=True)
class _ObservedTarget:
    model: str
    serial: str
    primary_abi: str
    android_sdk: int


class _BoundedTcpTransportAsync(TcpTransportAsync):
    """Enforce finite per-call and per-connection ADB byte bounds."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(host, port)
        self._received_bytes = 0
        self._written_bytes = 0

    async def bulk_read(
        self, numbytes: int, transport_timeout_s: float | None
    ) -> bytes:
        if (
            isinstance(numbytes, bool)
            or not isinstance(numbytes, int)
            or not 1 <= numbytes <= _MAX_ADB_PACKET_BYTES
        ):
            raise _UnsafeAdbPacket
        remaining = _MAX_CONNECTION_READ_BYTES - self._received_bytes
        if remaining < 1:
            raise _UnsafeAdbPacket
        timeout = _bounded_transport_timeout(transport_timeout_s)
        data = await super().bulk_read(min(numbytes, remaining), timeout)
        if not data:
            raise AdbConnectionError("bounded ADB connection closed")
        self._received_bytes += len(data)
        return data

    async def bulk_write(
        self, data: bytes | bytearray, transport_timeout_s: float | None
    ) -> int:
        if (
            not isinstance(data, (bytes, bytearray))
            or not data
            or len(data) > _MAX_ADB_PACKET_BYTES + 24
            or self._written_bytes + len(data) > _MAX_CONNECTION_WRITE_BYTES
        ):
            raise _UnsafeAdbPacket
        timeout = _bounded_transport_timeout(transport_timeout_s)
        written = await super().bulk_write(data, timeout)
        if written != len(data):
            raise _UnsafeAdbPacket
        self._written_bytes += written
        return written


def _bounded_transport_timeout(value: float | None) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= _MAX_TRANSPORT_TIMEOUT_SECONDS
    ):
        raise _UnsafeAdbPacket
    return float(value)


def _remote_path(job_id: str) -> str:
    if not isinstance(job_id, str) or _JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    return f"{_REMOTE_PREFIX}{job_id}.apk"


def _validate_target(target: AdbInstallTarget) -> None:
    if (
        not isinstance(target.address, PanelAddress)
        or not isinstance(target.address.host, str)
        or isinstance(target.address.port, bool)
        or not isinstance(target.address.port, int)
        or not 1 <= target.address.port <= 65535
        or not isinstance(target.serial, str)
        or not isinstance(target.model, str)
        or not isinstance(target.primary_abi, str)
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    try:
        address = ipaddress.ip_address(target.address.host)
    except ValueError:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST) from None
    if (
        not is_allowed_install_address(address)
        or _SERIAL_PATTERN.fullmatch(target.serial) is None
        or target.model != target.model.strip()
        or not 1 <= len(target.model) <= 128
        or not target.model.isprintable()
        or _ABI_PATTERN.fullmatch(target.primary_abi) is None
        or isinstance(target.android_sdk, bool)
        or not isinstance(target.android_sdk, int)
        or not 1 <= target.android_sdk <= 100
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _validate_descriptor(descriptor: InstallDescriptor) -> None:
    if (
        not isinstance(descriptor.schema, str)
        or not isinstance(descriptor.package_id, str)
        or not isinstance(descriptor.launch_component, str)
        or not isinstance(descriptor.signer_certificate_sha256, str)
        or not isinstance(descriptor.apk_name, str)
        or not isinstance(descriptor.apk_sha256, str)
        or descriptor.schema != _DESCRIPTOR_SCHEMA
        or not is_accepted_package_id(descriptor.package_id)
        or descriptor.launch_component != launch_component_for(descriptor.package_id)
        or descriptor.signer_certificate_sha256 != _RELEASE_SIGNER_SHA256
        or _APK_NAME_PATTERN.fullmatch(descriptor.apk_name) is None
        or _SHA256_PATTERN.fullmatch(descriptor.apk_sha256) is None
        or isinstance(descriptor.apk_size, bool)
        or not isinstance(descriptor.apk_size, int)
        or not 1 <= descriptor.apk_size <= _MAX_APK_BYTES
        or isinstance(descriptor.min_sdk, bool)
        or not isinstance(descriptor.min_sdk, int)
        or not 1 <= descriptor.min_sdk <= 100
        or not isinstance(descriptor.supported_abis, tuple)
        or descriptor.supported_abis != _SUPPORTED_ABIS
        or any(
            not isinstance(abi, str) or _ABI_PATTERN.fullmatch(abi) is None
            for abi in descriptor.supported_abis
        )
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _validate_request(target: AdbInstallTarget, descriptor: InstallDescriptor) -> None:
    if not isinstance(target, AdbInstallTarget) or not isinstance(
        descriptor, InstallDescriptor
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_descriptor(descriptor)


def _property_sections() -> tuple[tuple[str, str], ...]:
    return (
        ("MODEL", "ro.product.model"),
        ("SERIAL", "ro.serialno"),
        ("ABI", "ro.product.cpu.abi"),
        ("SDK", "ro.build.version.sdk"),
    )


def _framed_value_commands(prefix: str, nonce: str) -> list[str]:
    commands = [f"echo HAPANELD_{prefix}_BEGIN:{nonce}"]
    for name, android_property in _property_sections():
        commands.extend(
            (
                f"echo HAPANELD_{prefix}_{name}_BEGIN:{nonce}",
                f"getprop {android_property}",
                f"echo HAPANELD_{prefix}_{name}_END:{nonce}:$?",
            )
        )
    commands.append(f"echo HAPANELD_{prefix}_END:{nonce}")
    return commands


def _preflight_command(nonce: str) -> str:
    commands = _framed_value_commands("PREFLIGHT", nonce)
    sections = (
        ("UID", "id -u"),
        ("SECURE", "getprop ro.secure"),
        ("DEBUGGABLE", "getprop ro.debuggable"),
        (
            "SU",
            _su_observation_command(),
        ),
        ("LIVE", f"pm path {_PACKAGE_MANAGER_LIVENESS_PACKAGE}"),
        *(
            section
            for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS)
            for section in (
                (f"PACKAGE{index}", f"pm path {package_id}"),
                (f"RETAINED{index}", f"pm list packages -u {package_id}"),
            )
        ),
    )
    commands.pop()
    for name, command in sections:
        commands.extend(
            (
                f"echo HAPANELD_PREFLIGHT_{name}_BEGIN:{nonce}",
                command,
                f"echo HAPANELD_PREFLIGHT_{name}_END:{nonce}:$?",
            )
        )
    for index, path in enumerate(_ROOT_DATA_BASES):
        commands.extend(
            (
                f"echo HAPANELD_PREFLIGHT_BASE{index}_BEGIN:{nonce}",
                f"if [ -L {path} ] || [ -d {path} ]; then "
                f"if ls -1A {path} >/dev/null 2>&1; then echo readable; "
                "else echo unreadable; fi; else echo unreadable; fi",
                f"echo HAPANELD_PREFLIGHT_BASE{index}_END:{nonce}:$?",
            )
        )
    for index, path in enumerate(_RESIDUE_PATHS):
        commands.extend(
            (
                f"echo HAPANELD_PREFLIGHT_RESIDUE{index}_BEGIN:{nonce}",
                f"if [ -e {path} ] || [ -L {path} ]; then "
                "echo present; else echo absent; fi",
                f"echo HAPANELD_PREFLIGHT_RESIDUE{index}_END:{nonce}:$?",
            )
        )
    commands.append(f"echo HAPANELD_PREFLIGHT_END:{nonce}")
    return "; ".join(commands)


def _su_observation_command() -> str:
    return (
        "if command -v su >/dev/null 2>&1; then echo present; "
        "else hapaneld_su_status=$?; "
        'if [ "$hapaneld_su_status" -eq 1 ]; then echo absent; '
        "else echo abnormal; fi; fi"
    )


def _su_inspection_command(nonce: str, *, clean: bool) -> str:
    """Build only fixed read-only operations for the delegated root shell."""
    commands = [f"echo HAPANELD_SU_BEGIN:{nonce}"]
    sections = [("UID", "id -u")]
    if clean:
        for index, path in enumerate(_ROOT_DATA_BASES):
            sections.append(
                (
                    f"BASE{index}",
                    f"if [ -d {path} ] && ls -1A {path} >/dev/null 2>&1; "
                    "then echo readable; else echo unreadable; fi",
                )
            )
        for index, path in enumerate(_RESIDUE_PATHS):
            sections.append(
                (
                    f"RESIDUE{index}",
                    f"if [ -e {path} ] || [ -L {path} ]; then "
                    "echo present; else echo absent; fi",
                )
            )
    for name, command in sections:
        commands.extend(
            (
                f"echo HAPANELD_SU_{name}_BEGIN:{nonce}",
                command,
                f"echo HAPANELD_SU_{name}_END:{nonce}:$?",
            )
        )
    commands.append(f"echo HAPANELD_SU_END:{nonce}")
    return "; ".join(commands)


def _su_attempt_command(prefix: str, nonce: str, *, clean: bool) -> str:
    payload = shlex.quote(_su_inspection_command(nonce, clean=clean))
    return (
        f"echo HAPANELD_DELEGATE_BEGIN:{nonce}; {prefix} {payload}; "
        f"echo HAPANELD_DELEGATE_END:{nonce}:$?"
    )


def _identity_root_command(nonce: str) -> str:
    commands = _framed_value_commands("POSTURE", nonce)
    commands.pop()
    for name, command in (
        ("UID", "id -u"),
        ("SECURE", "getprop ro.secure"),
        ("DEBUGGABLE", "getprop ro.debuggable"),
        ("SU", _su_observation_command()),
    ):
        commands.extend(
            (
                f"echo HAPANELD_POSTURE_{name}_BEGIN:{nonce}",
                command,
                f"echo HAPANELD_POSTURE_{name}_END:{nonce}:$?",
            )
        )
    commands.append(f"echo HAPANELD_POSTURE_END:{nonce}")
    return "; ".join(commands)


def _path_state_command(nonce: str, remote_path: str) -> str:
    return "; ".join(
        (
            f"echo HAPANELD_PATH_BEGIN:{nonce}",
            f"if [ -e {remote_path} ] || [ -L {remote_path} ]; then "
            "echo present; else echo absent; fi",
            f"echo HAPANELD_PATH_END:{nonce}:$?",
        )
    )


# The installed application's own APK is read, never prepared: `prepare` is
# for this integration's own staged file, whose mode it owns.
def _artifact_observation_command(
    nonce: str, remote_path: str, *, prepare: bool
) -> str:
    prepared = (
        (
            f"echo HAPANELD_ARTIFACT_CHMOD_BEGIN:{nonce}",
            f"chmod 0644 {remote_path}",
            f"echo HAPANELD_ARTIFACT_CHMOD_END:{nonce}:$?",
        )
        if prepare
        else ()
    )
    return "; ".join(
        (
            f"echo HAPANELD_ARTIFACT_BEGIN:{nonce}",
            *prepared,
            f"echo HAPANELD_ARTIFACT_MODE_BEGIN:{nonce}",
            f"stat -c %f {remote_path}",
            f"echo HAPANELD_ARTIFACT_MODE_END:{nonce}:$?",
            f"echo HAPANELD_ARTIFACT_SIZE_BEGIN:{nonce}",
            f"wc -c < {remote_path}",
            f"echo HAPANELD_ARTIFACT_SIZE_END:{nonce}:$?",
            f"echo HAPANELD_ARTIFACT_SHA_BEGIN:{nonce}",
            f"sha256sum {remote_path}",
            f"echo HAPANELD_ARTIFACT_SHA_END:{nonce}:$?",
            f"echo HAPANELD_ARTIFACT_END:{nonce}",
        )
    )


def _remote_artifact_command(nonce: str, remote_path: str) -> str:
    return _artifact_observation_command(nonce, remote_path, prepare=True)


def _package_command(nonce: str, package_id: str) -> str:
    return "; ".join(
        (
            f"echo HAPANELD_PACKAGE_BEGIN:{nonce}",
            f"pm path {package_id}",
            f"echo HAPANELD_PACKAGE_END:{nonce}:$?",
        )
    )


def _install_command(nonce: str, remote_path: str, android_sdk: int) -> str:
    no_replace = "-R " if android_sdk >= 28 else ""
    return "; ".join(
        (
            f"echo HAPANELD_INSTALL_BEGIN:{nonce}",
            f"pm install {no_replace}{remote_path}",
            f"echo HAPANELD_INSTALL_END:{nonce}:$?",
        )
    )


def _update_install_command(nonce: str, remote_path: str) -> str:
    """Replace only the installed package; Android checks signer and version."""
    return "; ".join(
        (
            f"echo HAPANELD_INSTALL_BEGIN:{nonce}",
            # Deliberately omit -d and -g: package manager refuses a downgrade
            # or a signer mismatch, and existing permissions are left alone.
            f"pm install -r {remote_path}",
            f"echo HAPANELD_INSTALL_END:{nonce}:$?",
        )
    )


# POST_NOTIFICATIONS became a runtime permission in Android 13 (API 33).
_NOTIFICATIONS_RUNTIME_SDK = 33
_NOTIFICATIONS_PERMISSION = "android.permission.POST_NOTIFICATIONS"
_NOTIFICATIONS_GRANTED = re.compile(
    r"\s*android\.permission\.POST_NOTIFICATIONS: granted=true"
    r"(?:, flags=\[[ A-Z0-9_|]*\])?\s*",
    flags=re.ASCII,
)


def _runtime_permission_repair_command(package_id: str, permission: str) -> str:
    """Read a runtime grant, repair a proved denial, and read what Android kept."""
    permission_pattern = re.escape(permission)
    denied = (
        rf"[[:space:]]*{permission_pattern}: granted=false"
        r"(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*"
    )
    read = (
        f"runtime_dump=$(dumpsys package {package_id}); "
        'runtime_status=$?; runtime=; if [ "$runtime_status" -eq 0 ]; then '
        "runtime=$(printf '%s\\n' \"$runtime_dump\" | "
        f"grep -F '{permission}: granted='); fi"
    )
    # A missing or malformed dump is unknown, never evidence of a missing grant.
    return (
        f"{read}; "
        'if [ -n "$runtime" ] && ! printf \'%s\\n\' "$runtime" | '
        f"grep -Evq '^{denied}$'; then "
        f"pm grant {package_id} {permission} >/dev/null 2>&1; {read}; fi"
    )


def _notification_grant_command(nonce: str, package_id: str) -> str:
    """Repair missing notifications before the first start, then read them back.

    The service notification is part of keeping a wall panel working, so the
    installer grants it whatever a person once answered: a "Don't allow" may have
    been a mis-tap or a prompt nobody saw, and a shell grant overrides it. The
    grant's output is discarded; only the package manager's readback decides.
    """
    return "; ".join(
        (
            f"echo HAPANELD_NOTIFICATIONS_BEGIN:{nonce}",
            _runtime_permission_repair_command(package_id, _NOTIFICATIONS_PERMISSION),
            "printf '%s\\n' \"$runtime\"",
            f"echo HAPANELD_NOTIFICATIONS_END:{nonce}:$?",
        )
    )


def _parse_notification_grant(body: bytes, nonce: str) -> bool:
    """Granted only when every runtime-permission line Android printed says so.

    Only the complete permission lines matter; failed or empty reads prove nothing.
    """
    try:
        lines, _status = _parse_single_section(
            body, prefix="NOTIFICATIONS", nonce=nonce
        )
    except _MalformedAdbResponse:
        return False
    return bool(lines) and all(_NOTIFICATIONS_GRANTED.fullmatch(line) for line in lines)


_SERVICES_SETTING = "settings get secure enabled_accessibility_services"
_SERVICE_COMPONENT = re.compile(
    r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z.][A-Za-z0-9_.$]*", flags=re.ASCII
)
_APPOP_ALLOWED = r"{}: allow(?:; [\x20-\x7e]{{1,1024}})?"


def _permission_grant_command(nonce: str, package_id: str) -> str:
    """Repair only grants Android proves missing before an install or update start.

    Settings writes, the overlay and the accessibility service are what the
    panel's controls need, and the browser installer's permission contract grants
    them the same way. The service is appended to the device-wide list, never
    replacing it, and only after validating it and finding no valid spelling.
    A rerun writes nothing and other apps' services stay enabled. The list read before
    the write is printed so the readback can prove nothing was dropped. Grant
    output is discarded; only the readback decides.
    """
    component = ACCESSIBILITY_COMPONENTS[package_id]
    known = "|".join(
        f"*:{name}:*" for name in EQUIVALENT_ACCESSIBILITY_COMPONENTS[package_id]
    )
    quiet = ">/dev/null 2>&1"
    runtime_grants = []
    for feature, permission in (
        ("camera.any", "CAMERA"),
        ("microphone", "RECORD_AUDIO"),
    ):
        granted = (
            rf"[[:space:]]*android\.permission\.{permission}: granted=true"
            r"(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*"
        )
        runtime_grants.append(
            f"feature=$(pm has-feature android.hardware.{feature}); feature_status=$?; "
            'if [ "$feature" = true ] && [ "$feature_status" -eq 0 ]; then '
            + _runtime_permission_repair_command(
                package_id, f"android.permission.{permission}"
            )
            + '; if [ -n "$runtime" ] && ! printf \'%s\\n\' "$runtime" | '
            f"grep -Evq '^{granted}$'; then echo granted; else echo unknown; fi; "
            'elif [ "$feature" = false ] && [ "$feature_status" -le 1 ]; '
            "then echo unsupported; else echo unknown; fi"
        )
    return "; ".join(
        (
            f"echo HAPANELD_PERMISSIONS_BEGIN:{nonce}",
            f"before=$({_SERVICES_SETTING})",
            "before_status=$?",
            'echo "$before"',
            'after="$before"',
            'valid=1; [ "${#before}" -le 4096 ] || valid=0; '
            "case \"$before\" in *'\n'*) valid=0 ;; esac; "
            'case "$before" in ""|null) ;; *) rest="$before"; seen=:; count=0; '
            "while :; do service=${rest%%:*}; count=$((count+1)); "
            'if [ "$count" -gt 64 ] || ! printf \'%s\\n\' "$service" | '
            "grep -Eq '^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z.][A-Za-z0-9_.$]*$'; "
            "then valid=0; break; fi; "
            'case "$seen" in *:"$service":*) valid=0; break ;; esac; '
            'seen="$seen$service:"; case "$rest" in *:*) rest=${rest#*:} ;; '
            "*) break ;; esac; done ;; esac; "
            'if [ "$before_status" -eq 0 ] && [ "$valid" -eq 1 ]; then '
            'case ":$before:" in '
            f"{known}) ;; "
            f'*) case "$before" in ""|null) after=\'{component}\' ;; '
            f'*) after="$before:{component}" ;; esac; '
            f"current=$({_SERVICES_SETTING}); current_status=$?; "
            '[ "$current_status" -eq 0 ] && [ "$current" = "$before" ] && '
            f'settings put secure enabled_accessibility_services "$after" {quiet} ;; '
            "esac; fi",
            "for operation in WRITE_SETTINGS SYSTEM_ALERT_WINDOW; do "
            f'mode=$(appops get {package_id} "$operation"); mode_status=$?; '
            'if [ "$mode_status" -eq 0 ] && [ -n "$mode" ] && '
            '! printf \'%s\\n\' "$mode" | grep -Evq "^($operation: '
            "(default|deny|ignore|foreground|errored)(; [ -~]+)?|"
            'No operations\\.)$"; then '
            f'appops set {package_id} "$operation" allow {quiet}; fi; done',
            f"observed=$({_SERVICES_SETTING}); observed_status=$?; "
            'if [ "$before_status" -eq 0 ] && [ "$valid" -eq 1 ] && '
            '[ "$observed_status" -eq 0 ] && [ "$observed" = "$after" ]; then '
            f'case ":$observed:" in {known}) '
            "enabled=$(settings get secure accessibility_enabled); enabled_status=$?; "
            'if [ "$enabled_status" -eq 0 ]; then case "$enabled" in 0|null) '
            f"settings put secure accessibility_enabled 1 {quiet} ;; esac; fi ;; "
            "esac; fi",
            _SERVICES_SETTING,
            "settings get secure accessibility_enabled",
            f"appops get {package_id} WRITE_SETTINGS",
            f"appops get {package_id} SYSTEM_ALERT_WINDOW",
            *runtime_grants,
            f"echo HAPANELD_PERMISSIONS_END:{nonce}:$?",
        )
    )


def _expected_services(before: str, package_id: str) -> str | None:
    """The list after a grant, as the browser contract's ``expectedServices``."""
    services = [] if before in ("", "null") else before.split(":")
    if (
        len(before) > 4096
        or len(services) > 64
        or len(set(services)) != len(services)
        or not all(_SERVICE_COMPONENT.fullmatch(name) for name in services)
    ):
        return None
    known = EQUIVALENT_ACCESSIBILITY_COMPONENTS[package_id]
    if any(name in known for name in services):
        return before
    return ":".join((*services, ACCESSIBILITY_COMPONENTS[package_id]))


def _parse_permission_grant(body: bytes, nonce: str, package_id: str) -> bool:
    """Granted only when every readback line shows the grant and no service lost."""
    try:
        lines, status = _parse_single_section(body, prefix="PERMISSIONS", nonce=nonce)
    except _MalformedAdbResponse:
        return False
    if status != 0 or len(lines) != 7:
        return False
    before, after, enabled, write_settings, overlay, camera, microphone = lines
    expected = _expected_services(before, package_id)
    return (
        expected is not None
        and after == expected
        and enabled == "1"
        and re.fullmatch(_APPOP_ALLOWED.format("WRITE_SETTINGS"), write_settings)
        is not None
        and re.fullmatch(_APPOP_ALLOWED.format("SYSTEM_ALERT_WINDOW"), overlay)
        is not None
        and camera in ("granted", "unsupported")
        and microphone in ("granted", "unsupported")
    )


def _launch_command(nonce: str, package_id: str) -> str:
    """Start the successor, or the legacy app, by its own exact component.

    The ``<id>/.Class`` shorthand resolves against the application id while the
    classes stay in the legacy namespace, so the component is looked up rather
    than built from the package id.
    """
    return "; ".join(
        (
            f"echo HAPANELD_LAUNCH_BEGIN:{nonce}",
            f"am start -W -n {launch_component_for(package_id)} -p {package_id}",
            f"echo HAPANELD_LAUNCH_END:{nonce}:$?",
        )
    )


def _cleanup_command(nonce: str, remote_path: str) -> str:
    return "; ".join(
        (
            f"echo HAPANELD_CLEANUP_BEGIN:{nonce}",
            f"echo HAPANELD_CLEANUP_RM_BEGIN:{nonce}",
            f"rm -f {remote_path}",
            f"echo HAPANELD_CLEANUP_RM_END:{nonce}:$?",
            f"echo HAPANELD_CLEANUP_STATE_BEGIN:{nonce}",
            f"if [ -e {remote_path} ] || [ -L {remote_path} ]; then "
            "echo present; else echo absent; fi",
            f"echo HAPANELD_CLEANUP_STATE_END:{nonce}:$?",
            f"echo HAPANELD_CLEANUP_END:{nonce}",
        )
    )


def _decode_lines(body: bytes) -> list[str]:
    if not body or len(body) > _MAX_SHELL_RESPONSE_BYTES:
        raise _MalformedAdbResponse
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as err:
        raise _MalformedAdbResponse from err
    text = text.replace("\r\n", "\n")
    if "\r" in text or not text.endswith("\n"):
        raise _MalformedAdbResponse
    return text.splitlines()


def _parse_status(line: str, prefix: str, nonce: str) -> int | None:
    match = re.fullmatch(rf"HAPANELD_{prefix}_END:{nonce}:([0-9]{{1,3}})", line)
    return None if match is None else int(match.group(1))


def _parse_sections(
    body: bytes,
    *,
    prefix: str,
    nonce: str,
    names: tuple[str, ...],
) -> dict[str, tuple[list[str], int]]:
    lines = _decode_lines(body)
    if (
        lines[0] != f"HAPANELD_{prefix}_BEGIN:{nonce}"
        or lines[-1] != f"HAPANELD_{prefix}_END:{nonce}"
    ):
        raise _MalformedAdbResponse
    offset = 1
    parsed: dict[str, tuple[list[str], int]] = {}
    for name in names:
        begin = f"HAPANELD_{prefix}_{name}_BEGIN:{nonce}"
        if offset >= len(lines) - 1 or lines[offset] != begin:
            raise _MalformedAdbResponse
        offset += 1
        values: list[str] = []
        status: int | None = None
        while offset < len(lines) - 1:
            status = _parse_status(lines[offset], f"{prefix}_{name}", nonce)
            if status is not None:
                offset += 1
                break
            if lines[offset].startswith("HAPANELD_"):
                raise _MalformedAdbResponse
            values.append(lines[offset])
            offset += 1
        if status is None:
            raise _MalformedAdbResponse
        parsed[name] = (values, status)
    if offset != len(lines) - 1:
        raise _MalformedAdbResponse
    return parsed


def _parse_identity(body: bytes, nonce: str, prefix: str) -> _ObservedTarget:
    names = tuple(name for name, _property in _property_sections())
    sections = _parse_sections(body, prefix=prefix, nonce=nonce, names=names)
    values: dict[str, str] = {}
    for name in names:
        lines, status_code = sections[name]
        if status_code != 0 or len(lines) != 1:
            raise _MalformedAdbResponse
        values[name] = lines[0]
    model = values["MODEL"]
    serial = values["SERIAL"]
    primary_abi = values["ABI"]
    sdk = values["SDK"]
    if (
        model != model.strip()
        or not 1 <= len(model) <= 128
        or not model.isprintable()
        or _SERIAL_PATTERN.fullmatch(serial) is None
        or _ABI_PATTERN.fullmatch(primary_abi) is None
        or re.fullmatch(r"[0-9]{1,3}", sdk, flags=re.ASCII) is None
    ):
        raise _MalformedAdbResponse
    android_sdk = int(sdk)
    if not 1 <= android_sdk <= 100:
        raise _MalformedAdbResponse
    return _ObservedTarget(model, serial, primary_abi, android_sdk)


def _is_package_path(line: str) -> bool:
    return re.fullmatch(r"package:/[^ \t]+", line) is not None


def _require_same_target(observed: _ObservedTarget, expected: AdbInstallTarget) -> None:
    if (
        observed.model != expected.model
        or observed.serial != expected.serial
        or observed.primary_abi != expected.primary_abi
        or observed.android_sdk != expected.android_sdk
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)


def _parse_root_mode(
    sections: dict[str, tuple[list[str], int]],
) -> AdbRootMode:
    uid_lines, uid_status = sections["UID"]
    secure_lines, secure_status = sections["SECURE"]
    debuggable_lines, debuggable_status = sections["DEBUGGABLE"]
    su_lines, su_status = sections["SU"]
    if (
        uid_status != 0
        or len(uid_lines) != 1
        or secure_status != 0
        or secure_lines not in (["0"], ["1"])
        or debuggable_status != 0
        or debuggable_lines not in (["0"], ["1"])
        or su_status != 0
        or su_lines not in (["absent"], ["present"], ["abnormal"])
    ):
        raise _MalformedAdbResponse
    if su_lines == ["abnormal"]:
        raise _MalformedAdbResponse
    if uid_lines == ["0"]:
        return AdbRootMode.ROOT_ADBD
    # This is only a candidate. Async admission must prove delegated UID0.
    if uid_lines == ["2000"] and su_lines == ["present"]:
        return AdbRootMode.ROOT_SU
    if (
        uid_lines == ["2000"]
        and secure_lines == ["1"]
        and debuggable_lines == ["0"]
        and su_lines == ["absent"]
    ):
        return AdbRootMode.ROOTLESS
    raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)


def _require_expected_root_mode(observed: AdbRootMode, expected: AdbRootMode) -> None:
    if observed is not expected:
        raise InstallAdbError(InstallAdbErrorCode.ROOT_MODE_CHANGED)


def _validate_expected_root_mode(expected: AdbRootMode) -> None:
    if not isinstance(expected, AdbRootMode):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _classify_target_packages(
    target_package_id: str,
    installed: tuple[str, ...],
    residue: frozenset[str],
    *,
    admit_installed_target: bool = False,
) -> tuple[bool, bool]:
    """Decide whether this panel may receive this package, and how.

    A clean target holds neither accepted package.

    A panel that already holds the package being installed is refused, because
    this integration never replaces an installed panel app from the
    clean-install path. `admit_installed_target` reports that panel instead of
    refusing it, for the one caller that asks: a job whose target is already
    satisfied has to converge rather than tell its owner the panel is dirty
    when it is in fact exactly where they asked it to be. Reporting is not
    admission. Whether the panel runs the very bytes of this artifact is a
    separate observation the caller must still make, and nothing else may be
    present: another package, or another package's data, is unclean either way.
    Data belonging to the installed target is its own, not residue.

    The other admitted exception is the identity migration. A panel running the
    legacy package and not the successor may receive the successor beside it,
    because the successor is a different package to Android and the panel
    performs the handover itself. Residue belonging to that legacy package is
    expected there; legacy residue with no legacy package to migrate from is
    still an unclean target.
    """
    if target_package_id in installed:
        if not admit_installed_target or any(
            package_id != target_package_id for package_id in (*installed, *residue)
        ):
            raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
        return False, True
    if target_package_id in residue:
        # Data with no application to own it. Never an installed target.
        raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
    counterpart = counterpart_of(target_package_id)
    migration_candidate = (
        target_package_id == SUCCESSOR_PACKAGE_ID and counterpart in installed
    )
    # Anything else present is refused. Once a migration is admitted, the only
    # package left that can be installed or have left data behind is that
    # counterpart, so this one check covers both.
    if not migration_candidate and (installed or residue):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
    return migration_candidate, False


def _parse_preflight(
    body: bytes,
    nonce: str,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    *,
    admit_installed_target: bool = False,
) -> AdbPreflight:
    property_names = tuple(name for name, _property in _property_sections())
    other_names = (
        "UID",
        "SECURE",
        "DEBUGGABLE",
        "SU",
        "LIVE",
        *(
            name
            for index in range(len(ACCEPTED_PACKAGE_IDS))
            for name in (f"PACKAGE{index}", f"RETAINED{index}")
        ),
    )
    residue_names = tuple(f"RESIDUE{index}" for index in range(len(_RESIDUE_PATHS)))
    base_names = tuple(f"BASE{index}" for index in range(len(_ROOT_DATA_BASES)))
    sections = _parse_sections(
        body,
        prefix="PREFLIGHT",
        nonce=nonce,
        names=property_names + other_names + base_names + residue_names,
    )

    identity_body = _sections_as_frame(
        sections, prefix="PREFLIGHT", nonce=nonce, names=property_names
    )
    observed = _parse_identity(identity_body, nonce, "PREFLIGHT")
    _require_same_target(observed, target)

    live_lines, live_status = sections["LIVE"]
    if (
        live_status != 0
        or not live_lines
        or any(not _is_package_path(line) for line in live_lines)
    ):
        raise _MalformedAdbResponse

    # Each accepted application id is observed on its own. Reading them apart is
    # what lets an old package on a panel that has no new package be a migration
    # candidate instead of an unclean target.
    installed: list[str] = []
    for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS):
        package_lines, package_status = sections[f"PACKAGE{index}"]
        retained_lines, retained_status = sections[f"RETAINED{index}"]
        present = any(
            line == f"package:{package_id}" for line in retained_lines
        ) or any(_is_package_path(line) for line in package_lines)
        if present:
            installed.append(package_id)
            continue
        if (
            package_status not in (0, 1)
            or package_lines
            or retained_status != 0
            or retained_lines
        ):
            raise _MalformedAdbResponse

    bases_readable = True
    for name in base_names:
        lines, status_code = sections[name]
        if status_code != 0 or lines not in (["readable"], ["unreadable"]):
            raise _MalformedAdbResponse
        bases_readable = bases_readable and lines == ["readable"]

    residue: set[str] = set()
    for index, name in enumerate(residue_names):
        lines, status_code = sections[name]
        if status_code != 0 or lines not in (["absent"], ["present"]):
            raise _MalformedAdbResponse
        if lines == ["present"]:
            residue.add(_RESIDUE_PROBES[index][0])

    migration_candidate, target_installed = _classify_target_packages(
        descriptor.package_id,
        tuple(installed),
        frozenset(residue),
        admit_installed_target=admit_installed_target,
    )

    root_mode = _parse_root_mode(sections)
    if root_mode is AdbRootMode.ROOT_ADBD and not bases_readable:
        raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)

    if (
        observed.android_sdk < descriptor.min_sdk
        or observed.primary_abi not in descriptor.supported_abis
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_INCOMPATIBLE)
    return AdbPreflight(
        serial=observed.serial,
        model=observed.model,
        primary_abi=observed.primary_abi,
        android_sdk=observed.android_sdk,
        root_mode=root_mode,
        migration_candidate=migration_candidate,
        target_installed=target_installed,
    )


def _sections_as_frame(
    sections: dict[str, tuple[list[str], int]],
    *,
    prefix: str,
    nonce: str,
    names: tuple[str, ...],
) -> bytes:
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    for name in names:
        values, status_code = sections[name]
        lines.append(f"HAPANELD_{prefix}_{name}_BEGIN:{nonce}")
        lines.extend(values)
        lines.append(f"HAPANELD_{prefix}_{name}_END:{nonce}:{status_code}")
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _parse_identity_root(
    body: bytes,
    nonce: str,
    target: AdbInstallTarget,
) -> AdbRootMode:
    property_names = tuple(name for name, _property in _property_sections())
    root_names = ("UID", "SECURE", "DEBUGGABLE", "SU")
    sections = _parse_sections(
        body,
        prefix="POSTURE",
        nonce=nonce,
        names=property_names + root_names,
    )
    identity_body = _sections_as_frame(
        sections, prefix="POSTURE", nonce=nonce, names=property_names
    )
    observed = _parse_identity(identity_body, nonce, "POSTURE")
    _require_same_target(observed, target)
    return _parse_root_mode(sections)


def _parse_single_section(
    body: bytes, *, prefix: str, nonce: str
) -> tuple[list[str], int]:
    lines = _decode_lines(body)
    begin = f"HAPANELD_{prefix}_BEGIN:{nonce}"
    if not lines or lines[0] != begin:
        raise _MalformedAdbResponse
    status = _parse_status(lines[-1], prefix, nonce)
    if status is None or any(line.startswith("HAPANELD_") for line in lines[1:-1]):
        raise _MalformedAdbResponse
    return lines[1:-1], status


def _parse_path_state(body: bytes, nonce: str) -> bool:
    lines, status_code = _parse_single_section(body, prefix="PATH", nonce=nonce)
    if status_code != 0 or lines not in (["absent"], ["present"]):
        raise _MalformedAdbResponse
    return lines == ["present"]


def _parse_artifact_observation(
    body: bytes, nonce: str, remote_path: str, *, prepare: bool
) -> tuple[int, int, str]:
    sections = _parse_sections(
        body,
        prefix="ARTIFACT",
        nonce=nonce,
        names=("CHMOD", "MODE", "SIZE", "SHA") if prepare else ("MODE", "SIZE", "SHA"),
    )
    chmod_lines, chmod_status = sections["CHMOD"] if prepare else ([], 0)
    mode_lines, mode_status = sections["MODE"]
    size_lines, size_status = sections["SIZE"]
    sha_lines, sha_status = sections["SHA"]
    if (
        chmod_lines
        or chmod_status != 0
        or mode_status != 0
        or size_status != 0
        or sha_status != 0
        or len(mode_lines) != 1
        or re.fullmatch(r"[0-9a-fA-F]{1,8}", mode_lines[0]) is None
        or len(size_lines) != 1
        or re.fullmatch(r"[ \t]*[0-9]{1,10}[ \t]*", size_lines[0], flags=re.ASCII)
        is None
        or len(sha_lines) != 1
    ):
        raise _MalformedAdbResponse
    mode = int(mode_lines[0], 16)
    size = int(size_lines[0].strip())
    sha_match = re.fullmatch(
        rf"([0-9a-f]{{64}})[ \t]+{re.escape(remote_path)}", sha_lines[0]
    )
    if sha_match is None or not 0 <= size <= _MAX_APK_BYTES:
        raise _MalformedAdbResponse
    return mode, size, sha_match.group(1)


def _parse_remote_artifact(
    body: bytes, nonce: str, remote_path: str
) -> tuple[int, int, str]:
    return _parse_artifact_observation(body, nonce, remote_path, prepare=True)


# One installed base APK, at a path the package manager chose. The allowlist
# keeps that path out of shell syntax and rejects anything that is not an
# ordinary installed application directory.
_INSTALLED_APK_PATH_PATTERN = re.compile(
    r"^/data/app/[A-Za-z0-9_./=+~-]+/base\.apk$", flags=re.ASCII
)


def _parse_installed_apk_path(body: bytes, nonce: str) -> str | None:
    """Return the one base APK path of an installed package, or None."""
    lines, status_code = _parse_single_section(body, prefix="PACKAGE", nonce=nonce)
    if status_code == 1 and not lines:
        return None
    if status_code != 0 or len(lines) != 1 or not _is_package_path(lines[0]):
        raise _MalformedAdbResponse
    path = lines[0].removeprefix("package:")
    if (
        _INSTALLED_APK_PATH_PATTERN.fullmatch(path) is None
        or "//" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise _MalformedAdbResponse
    return path


def _parse_package_present(body: bytes, nonce: str) -> None:
    lines, status_code = _parse_single_section(body, prefix="PACKAGE", nonce=nonce)
    if status_code == 1 and not lines:
        raise InstallAdbError(InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING)
    if (
        status_code != 0
        or not lines
        or any(not _is_package_path(line) for line in lines)
    ):
        raise _MalformedAdbResponse


def _parse_install_outcome(body: bytes, nonce: str) -> InstallOutcome:
    lines, status_code = _parse_single_section(body, prefix="INSTALL", nonce=nonce)
    if status_code == 0 and lines == ["Success"]:
        return InstallOutcome.INSTALLED
    if (
        status_code == 1
        and len(lines) == 1
        and re.fullmatch(
            r"Failure \[INSTALL_[A-Z0-9_]+(?:: [ -~]{1,1024})?\]", lines[0]
        )
        is not None
    ):
        return InstallOutcome.REFUSED
    raise _MalformedAdbResponse


def _validate_staged_apk(staged: StagedApk) -> str:
    if (
        not isinstance(staged, StagedApk)
        or not isinstance(staged.job_id, str)
        or not isinstance(staged.remote_path, str)
        or isinstance(staged.apk_size, bool)
        or not isinstance(staged.apk_size, int)
        or not isinstance(staged.apk_sha256, str)
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    remote_path = _remote_path(staged.job_id)
    if (
        staged.remote_path != remote_path
        or not 1 <= staged.apk_size <= _MAX_APK_BYTES
        or _SHA256_PATTERN.fullmatch(staged.apk_sha256) is None
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    return remote_path


def _parse_launch_outcome(body: bytes, nonce: str) -> LaunchOutcome:
    _lines, status_code = _parse_single_section(body, prefix="LAUNCH", nonce=nonce)
    if status_code == 0:
        return LaunchOutcome.STARTED
    if status_code == 1:
        return LaunchOutcome.REFUSED
    raise _MalformedAdbResponse


def _parse_cleanup(body: bytes, nonce: str) -> None:
    sections = _parse_sections(
        body,
        prefix="CLEANUP",
        nonce=nonce,
        names=("RM", "STATE"),
    )
    rm_lines, rm_status = sections["RM"]
    state_lines, state_status = sections["STATE"]
    if rm_lines or rm_status != 0 or state_status != 0 or state_lines != ["absent"]:
        raise _MalformedAdbResponse


async def _async_shell(
    device: AdbDeviceAsync,
    command: str,
    *,
    read_timeout: float,
    transport_timeout: float = _TRANSPORT_TIMEOUT_SECONDS,
) -> bytes:
    body = bytearray()
    async for chunk in device.streaming_shell(
        command,
        transport_timeout_s=transport_timeout,
        read_timeout_s=read_timeout,
        decode=False,
    ):
        if (
            not isinstance(chunk, bytes)
            or len(body) + len(chunk) > _MAX_SHELL_RESPONSE_BYTES
        ):
            raise _MalformedAdbResponse
        body.extend(chunk)
    return bytes(body)


async def _async_connect(
    target: AdbInstallTarget, signer: PythonRSASigner
) -> AdbDeviceAsync:
    if not isinstance(signer, PythonRSASigner):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    device = AdbDeviceAsync(
        _BoundedTcpTransportAsync(target.address.host, ADB_PORT),
        default_transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
        banner=_ADB_BANNER,
    )
    authorization_prompted = False

    def _authorization_prompted(_device: AdbDeviceAsync) -> None:
        nonlocal authorization_prompted
        authorization_prompted = True

    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            connected = await device.connect(
                rsa_keys=[signer],
                transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
                auth_timeout_s=_CONNECT_TIMEOUT_SECONDS,
                read_timeout_s=_READ_TIMEOUT_SECONDS,
                auth_callback=_authorization_prompted,
            )
    except asyncio.CancelledError:
        with suppress(asyncio.CancelledError):
            await _async_close(device)
        raise
    except DeviceAuthError:
        await _async_close(device)
        raise InstallAdbError(InstallAdbErrorCode.AUTHORIZATION_REQUIRED) from None
    except (
        TimeoutError,
        AdbConnectionError,
        AdbTimeoutError,
        OSError,
        TcpTimeoutException,
    ):
        await _async_close(device)
        code = (
            InstallAdbErrorCode.AUTHORIZATION_REQUIRED
            if authorization_prompted
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    except (
        _UnsafeAdbPacket,
        DevicePathInvalidError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        InvalidTransportError,
        PushFailedError,
    ):
        await _async_close(device)
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID) from None
    if not connected:
        await _async_close(device)
        code = (
            InstallAdbErrorCode.AUTHORIZATION_REQUIRED
            if authorization_prompted
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code)
    return device


async def _async_wait_for_completion[T](task: asyncio.Task[T]) -> bool:
    """Drain one owned task while recording every caller cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return cancelled


async def _async_close_once(device: AdbDeviceAsync) -> None:
    with suppress(Exception):
        async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
            await device.close()


async def _async_close(device: AdbDeviceAsync | None) -> None:
    if device is None:
        return
    close_task = asyncio.create_task(_async_close_once(device))
    cancelled = await _async_wait_for_completion(close_task)
    close_task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _async_preflight_on_device(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    *,
    admit_installed_target: bool = False,
) -> AdbPreflight:
    nonce = token_hex(16)
    body = await _async_shell(
        device, _preflight_command(nonce), read_timeout=_READ_TIMEOUT_SECONDS
    )
    preflight = _parse_preflight(
        body, nonce, target, descriptor, admit_installed_target=admit_installed_target
    )
    if preflight.root_mode is AdbRootMode.ROOT_SU:
        await _async_prove_su(
            device, admitted=preflight, target_package_id=descriptor.package_id
        )
    return preflight


async def _async_prove_su(
    device: AdbDeviceAsync,
    *,
    admitted: AdbPreflight | None,
    target_package_id: str | None = None,
) -> None:
    """Prove delegated root afresh without elevating any mutation command.

    ``admitted`` is the preflight this root reading has to agree with, or None
    when only root itself is being re-proved. ``target_package_id`` names the
    package that preflight was about, because the data an installed target owns
    is expected exactly where that target was reported.
    """
    clean = admitted is not None
    for prefix in _SU_PREFIXES:
        nonce = token_hex(16)
        async with asyncio.timeout(_SU_TIMEOUT_SECONDS):
            body = await _async_shell(
                device,
                _su_attempt_command(prefix, nonce, clean=clean),
                read_timeout=_SU_TIMEOUT_SECONDS,
                transport_timeout=_SU_TIMEOUT_SECONDS,
            )
        lines = _decode_lines(body)
        if not lines or lines[0] != f"HAPANELD_DELEGATE_BEGIN:{nonce}":
            raise _MalformedAdbResponse
        status = _parse_status(lines[-1], "DELEGATE", nonce)
        if status is None:
            raise _MalformedAdbResponse
        if status != 0:
            continue  # A different vendor dialect may be needed.
        names: tuple[str, ...] = ("UID",)
        if clean:
            names += tuple(f"BASE{i}" for i in range(len(_ROOT_DATA_BASES)))
            names += tuple(f"RESIDUE{i}" for i in range(len(_RESIDUE_PATHS)))
        sections = _parse_sections(
            ("\n".join(lines[1:-1]) + "\n").encode(),
            prefix="SU",
            nonce=nonce,
            names=names,
        )
        if sections["UID"] != (["0"], 0):
            raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)
        if admitted is not None:
            for index in range(len(_ROOT_DATA_BASES)):
                if sections[f"BASE{index}"] != (["readable"], 0):
                    raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)
            # Root can see data directories the unprivileged shell cannot, so
            # this is the authoritative residue reading. It is judged against
            # the same rule, including the legacy residue a migration expects.
            for index in range(len(_RESIDUE_PATHS)):
                values, status = sections[f"RESIDUE{index}"]
                if status != 0 or values not in (["absent"], ["present"]):
                    raise _MalformedAdbResponse
                owner = _RESIDUE_PROBES[index][0]
                expected = (
                    admitted.migration_candidate and owner == LEGACY_PACKAGE_ID
                ) or (admitted.target_installed and owner == target_package_id)
                if values == ["present"] and not expected:
                    raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
        return
    raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)


async def _async_require_identity_root(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    expected_root_mode: AdbRootMode,
) -> None:
    nonce = token_hex(16)
    observed_root_mode = _parse_identity_root(
        await _async_shell(
            device,
            _identity_root_command(nonce),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
        target,
    )
    _require_expected_root_mode(observed_root_mode, expected_root_mode)
    if observed_root_mode is AdbRootMode.ROOT_SU:
        await _async_prove_su(device, admitted=None)


def _validate_filesync_maxdata(device: AdbDeviceAsync) -> None:
    maxdata = getattr(device, "_maxdata", None)
    if (
        isinstance(maxdata, bool)
        or not isinstance(maxdata, int)
        or not _MIN_ADB_MAXDATA <= maxdata <= _MAX_ADB_MAXDATA
    ):
        raise InstallAdbError(InstallAdbErrorCode.FILESYNC_UNSAFE)


def _verified_apk_identity(
    status: os.stat_result, expected_size: int
) -> tuple[int, int, int, int, int, int, int, int]:
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_uid != os.geteuid()
        or status.st_nlink != 1
        or status.st_size != expected_size
    ):
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_uid,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _open_verified_apk(path: Path, descriptor: InstallDescriptor) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        path_stat = os.lstat(path)
        file_descriptor = os.open(path, flags)
    except OSError, TypeError, ValueError:
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID) from None
    try:
        path_identity = _verified_apk_identity(path_stat, descriptor.apk_size)
        opened_identity = _verified_apk_identity(
            os.fstat(file_descriptor), descriptor.apk_size
        )
        if opened_identity != path_identity:
            raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
        digest = hashlib.sha256()
        actual_size = 0
        while chunk := os.read(file_descriptor, 1024 * 1024):
            actual_size += len(chunk)
            if actual_size > descriptor.apk_size:
                raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
            digest.update(chunk)
        if (
            actual_size != descriptor.apk_size
            or digest.hexdigest() != descriptor.apk_sha256
            or _verified_apk_identity(os.fstat(file_descriptor), descriptor.apk_size)
            != opened_identity
            or _verified_apk_identity(os.lstat(path), descriptor.apk_size)
            != opened_identity
        ):
            raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        return file_descriptor
    except InstallAdbError:
        os.close(file_descriptor)
        raise
    except OSError, TypeError, ValueError:
        os.close(file_descriptor)
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID) from None
    except BaseException:
        os.close(file_descriptor)
        raise


async def _async_open_verified_apk(path: Path, descriptor: InstallDescriptor) -> int:
    """Retain custody of a raw descriptor across caller cancellation."""
    worker = asyncio.create_task(
        asyncio.to_thread(_open_verified_apk, path, descriptor)
    )
    cancelled = await _async_wait_for_completion(worker)
    if cancelled:
        try:
            file_descriptor = worker.result()
        except BaseException:
            pass
        else:
            with suppress(OSError):
                os.close(file_descriptor)
        raise asyncio.CancelledError
    return worker.result()


async def _async_verify_remote_artifact(
    device: AdbDeviceAsync, remote_path: str, descriptor: InstallDescriptor
) -> None:
    nonce = token_hex(16)
    body = await _async_shell(
        device,
        _remote_artifact_command(nonce, remote_path),
        read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
        transport_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
    )
    try:
        mode, size, sha256 = _parse_remote_artifact(body, nonce, remote_path)
    except _MalformedAdbResponse:
        raise InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED) from None
    if (
        not stat.S_ISREG(mode)
        or stat.S_IMODE(mode) != 0o644
        or size != descriptor.apk_size
        or sha256 != descriptor.apk_sha256
    ):
        raise InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED)


async def async_preflight_install(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    admit_installed_target: bool = False,
) -> AdbPreflight:
    """Re-prove clean admission without mutating the panel.

    `admit_installed_target` reports a panel already holding exactly this
    package as `target_installed` instead of refusing it. The caller then owns
    what happens next, and may not stage or install on that panel.
    """
    _validate_request(target, descriptor)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            return await _async_preflight_on_device(
                device,
                target,
                descriptor,
                admit_installed_target=admit_installed_target,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    finally:
        await _async_close(device)


async def async_verify_installed_target(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    *,
    expected_root_mode: AdbRootMode,
    package_id: str,
) -> None:
    """Re-prove the exact target and installed package without mutation."""
    if not isinstance(target, AdbInstallTarget) or not is_accepted_package_id(
        package_id
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        AdbConnectionError,
        AdbTimeoutError,
        OSError,
        TcpTimeoutException,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    except (
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        DevicePathInvalidError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        InvalidTransportError,
        PushFailedError,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID) from None
    finally:
        await _async_close(device)


async def async_installed_artifact_size(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> int | None:
    """Return the installed APK's size when the panel runs exactly these bytes.

    The installed package's own APK is measured and hashed, and compared with
    the signed descriptor. Equal size and digest mean the panel is already
    running the very bytes this job would install, so installing them again
    would change nothing on it. Anything else returns None: a different build
    of the same application is never admitted here, and this observation
    mutates nothing.
    """
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            nonce = token_hex(16)
            path = _parse_installed_apk_path(
                await _async_shell(
                    device,
                    _package_command(nonce, descriptor.package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            if path is None:
                return None
            nonce = token_hex(16)
            mode, size, digest = _parse_artifact_observation(
                await _async_shell(
                    device,
                    _artifact_observation_command(nonce, path, prepare=False),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
                path,
                prepare=False,
            )
            if (
                not stat.S_ISREG(mode)
                or size != descriptor.apk_size
                or digest != descriptor.apk_sha256
            ):
                return None
            return size
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        AdbConnectionError,
        AdbTimeoutError,
        OSError,
        TcpTimeoutException,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    except (
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        DevicePathInvalidError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        InvalidTransportError,
        PushFailedError,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID) from None
    finally:
        await _async_close(device)


async def async_stage_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    job_id: str,
    local_apk: Path,
    *,
    expected_root_mode: AdbRootMode,
) -> StagedApk:
    """Revalidate, push and authenticate one exact job-owned APK."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _remote_path(job_id)
    if not isinstance(local_apk, Path):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    device: AdbDeviceAsync | None = None
    file_descriptor: int | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_STAGE_TIMEOUT_SECONDS):
            file_descriptor = await _async_open_verified_apk(local_apk, descriptor)
            device = await _async_connect(target, signer)
            preflight = await _async_preflight_on_device(device, target, descriptor)
            _require_expected_root_mode(preflight.root_mode, expected_root_mode)
            nonce = token_hex(16)
            occupied = _parse_path_state(
                await _async_shell(
                    device,
                    _path_state_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            if occupied:
                raise InstallAdbError(InstallAdbErrorCode.STAGING_PATH_OCCUPIED)
            _validate_filesync_maxdata(device)
            mutation_started = True
            await device.push(
                f"/proc/self/fd/{file_descriptor}",
                remote_path,
                st_mode=_REMOTE_MODE,
                # The sync protocol carries this as the staged file's
                # modification time in whole seconds. The bytes come from a
                # file descriptor with no meaningful time of its own, so send
                # the time of the copy rather than a constant that dates the
                # file to 1970 and tells a consumer nothing.
                mtime=int(time()),
                transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
                read_timeout_s=_INSTALL_READ_TIMEOUT_SECONDS,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            return StagedApk(
                job_id=job_id,
                remote_path=remote_path,
                apk_size=descriptor.apk_size,
                apk_sha256=descriptor.apk_sha256,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.STAGE_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        try:
            await _async_close(device)
        finally:
            if file_descriptor is not None:
                descriptor_to_close = file_descriptor
                file_descriptor = None
                os.close(descriptor_to_close)


async def async_install_staged_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    job_id: str,
    *,
    expected_root_mode: AdbRootMode,
) -> InstallOutcome:
    """Revalidate and run exactly one non-replacing package installation."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _remote_path(job_id)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_INSTALL_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            preflight = await _async_preflight_on_device(device, target, descriptor)
            _require_expected_root_mode(preflight.root_mode, expected_root_mode)
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            nonce = token_hex(16)
            mutation_started = True
            body = await _async_shell(
                device,
                _install_command(nonce, remote_path, target.android_sdk),
                read_timeout=_INSTALL_TIMEOUT_SECONDS,
                transport_timeout=_INSTALL_TIMEOUT_SECONDS,
            )
            return _parse_install_outcome(body, nonce)
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


async def async_update_installed_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    local_apk: Path,
) -> InstallOutcome:
    """Replace one already installed app using a fresh, pinned ADB target.

    This is deliberately separate from clean installation. A verified APK is
    pushed to a random owned path, then both panel identity and exclusive
    ownership of the target package are re-proved before package mutation.
    Android's package manager enforces signer and version checks for ``-r``;
    this integration never supplies its downgrade override.
    """
    _validate_request(target, descriptor)
    if not isinstance(local_apk, Path):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    file_descriptor = await _async_open_verified_apk(local_apk, descriptor)
    remote_path = _remote_path(token_hex(16))
    device: AdbDeviceAsync | None = None
    stage_started = False
    install_started = False
    install_definite = False
    admitted_root_mode: AdbRootMode | None = None
    try:
        async with asyncio.timeout(_UPDATE_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            admitted = await _async_preflight_on_device(
                device, target, descriptor, admit_installed_target=True
            )
            if not admitted.target_installed:
                raise InstallAdbError(InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING)
            admitted_root_mode = admitted.root_mode
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, descriptor.package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            nonce = token_hex(16)
            occupied = _parse_path_state(
                await _async_shell(
                    device,
                    _path_state_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            if occupied:
                raise InstallAdbError(InstallAdbErrorCode.STAGING_PATH_OCCUPIED)
            _validate_filesync_maxdata(device)
            stage_started = True
            await device.push(
                f"/proc/self/fd/{file_descriptor}",
                remote_path,
                st_mode=_REMOTE_MODE,
                mtime=int(time()),
                transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
                read_timeout_s=_INSTALL_READ_TIMEOUT_SECONDS,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            # The package and identity can change while the file crosses ADB.
            admitted = await _async_preflight_on_device(
                device, target, descriptor, admit_installed_target=True
            )
            assert admitted_root_mode is not None
            _require_expected_root_mode(admitted.root_mode, admitted_root_mode)
            if not admitted.target_installed:
                raise InstallAdbError(InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING)
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, descriptor.package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            nonce = token_hex(16)
            install_started = True
            outcome = _parse_install_outcome(
                await _async_shell(
                    device,
                    _update_install_command(nonce, remote_path),
                    read_timeout=_INSTALL_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            install_definite = True
            nonce = token_hex(16)
            _parse_cleanup(
                await _async_shell(
                    device,
                    _cleanup_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            return outcome
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.CLEANUP_AMBIGUOUS
            if install_definite
            else InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if install_started
            else InstallAdbErrorCode.STAGE_AMBIGUOUS
            if stage_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        try:
            # Before package mutation, the random path is ours to remove. Once
            # pm install starts, an unknown outcome may still be reading it.
            if stage_started and not install_started and device is not None:
                try:
                    assert admitted_root_mode is not None
                    async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
                        await _async_require_identity_root(
                            device, target, admitted_root_mode
                        )
                        nonce = token_hex(16)
                        _parse_cleanup(
                            await _async_shell(
                                device,
                                _cleanup_command(nonce, remote_path),
                                read_timeout=_READ_TIMEOUT_SECONDS,
                            ),
                            nonce,
                        )
                except (
                    InstallAdbError,
                    TimeoutError,
                    _MalformedAdbResponse,
                    _UnsafeAdbPacket,
                    *_ADB_EXCEPTIONS,
                ):
                    _LOGGER.warning("Could not clear an interrupted ADB update stage")
            await _async_close(device)
        finally:
            os.close(file_descriptor)


async def _async_repair_app_permissions(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """Share observed grant repair between startup and an already-running update."""
    nonce = token_hex(16)
    _parse_package_present(
        await _async_shell(
            device,
            _package_command(nonce, descriptor.package_id),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
    )
    if target.android_sdk >= _NOTIFICATIONS_RUNTIME_SDK:
        # Never a reason not to start: a refusal is reported, and the app
        # still runs without notification visibility.
        nonce = token_hex(16)
        if not _parse_notification_grant(
            await _async_shell(
                device,
                _notification_grant_command(nonce, descriptor.package_id),
                read_timeout=_READ_TIMEOUT_SECONDS,
            ),
            nonce,
        ):
            _LOGGER.warning(
                "The panel did not grant %s the notification permission. "
                "The app runs without it; allow it on the panel in Android "
                "Settings, under the app's Notifications",
                descriptor.package_id,
            )
    # The same rule as notifications: a refusal is reported, never fatal.
    nonce = token_hex(16)
    if not _parse_permission_grant(
        await _async_shell(
            device,
            _permission_grant_command(nonce, descriptor.package_id),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
        descriptor.package_id,
    ):
        _LOGGER.warning(
            "The panel did not grant %s every permission it needs: "
            "modify system settings, display over other apps and its "
            "accessibility service, camera and microphone where present. "
            "Controls that depend on them, such as "
            "touch sounds, stay unavailable; allow them on the panel in "
            "Android Settings, under the app",
            descriptor.package_id,
        )


async def async_repair_installed_app_permissions(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> None:
    """Repair an installed app's grants without relaunching or replacing it."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            await _async_repair_app_permissions(device, target, descriptor)
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    finally:
        await _async_close(device)


async def async_launch_installed_app(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> LaunchOutcome:
    """Launch the descriptor's one fixed package component exactly once."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            await _async_repair_app_permissions(device, target, descriptor)
            nonce = token_hex(16)
            mutation_started = True
            return _parse_launch_outcome(
                await _async_shell(
                    device,
                    _launch_command(nonce, descriptor.package_id),
                    read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.LAUNCH_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


async def async_cleanup_staged_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    staged: StagedApk,
    reason: DefiniteCleanupReason,
    *,
    expected_root_mode: AdbRootMode,
) -> None:
    """Delete one verified job-owned stage after a definite disposition."""
    if not isinstance(target, AdbInstallTarget) or not isinstance(
        reason, DefiniteCleanupReason
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _validate_staged_apk(staged)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            nonce = token_hex(16)
            mutation_started = True
            _parse_cleanup(
                await _async_shell(
                    device,
                    _cleanup_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.CLEANUP_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


class MoveStep(StrEnum):
    """One package-manager step of moving a panel from the legacy app id."""

    OBSERVE = "OBSERVE"
    REMOVE_SUCCESSOR = "REMOVE_SUCCESSOR"
    CLAIM_HOME = "CLAIM_HOME"
    RETIRE_LEGACY = "RETIRE_LEGACY"
    RESET_SUCCESSOR = "RESET_SUCCESSOR"


@dataclass(frozen=True, slots=True)
class MoveObservation:
    """What the panel reports after a move step, read in the same shell."""

    legacy_installed: bool
    successor_installed: bool
    #: The package HOME resolves to now, or None when nothing single answers.
    home: str | None
    #: The new app is installed and has never run: Android still marks it not
    #: launched, which no start of any of its components leaves set.
    successor_unlaunched: bool = False
    #: Read through the panel's root: the new app's own migration records,
    #: or None when they could not be read. A new app that has run only while
    #: waiting for the old app's handover holds no state of its own; one that
    #: finished, or started on its own, records ``complete``.
    successor_records: frozenset[str] | None = None


_HOME_QUERY = (
    "cmd package resolve-activity --brief "
    "-a android.intent.action.MAIN -c android.intent.category.HOME 2>/dev/null"
    " | tail -n 1"
)
_MOVE_ACTIONS: dict[MoveStep, tuple[str, ...]] = {
    MoveStep.OBSERVE: (),
    # A successor beside a running legacy app holds only a copy of that app's
    # state, so removing it loses nothing the legacy app does not still hold.
    MoveStep.REMOVE_SUCCESSOR: (
        f"am force-stop {SUCCESSOR_PACKAGE_ID}",
        f"pm uninstall {SUCCESSOR_PACKAGE_ID}",
    ),
    MoveStep.CLAIM_HOME: (
        f"cmd package set-home-activity {HOME_COMPONENTS[SUCCESSOR_PACKAGE_ID]}",
    ),
    MoveStep.RETIRE_LEGACY: (
        f"am force-stop {LEGACY_PACKAGE_ID}",
        f"pm uninstall {LEGACY_PACKAGE_ID}",
    ),
    # A successor that first starts with no legacy app and no migration record
    # beside it runs as an ordinary app; clearing it makes that start certain.
    MoveStep.RESET_SUCCESSOR: (
        f"am force-stop {SUCCESSOR_PACKAGE_ID}",
        f"pm clear {SUCCESSOR_PACKAGE_ID}",
    ),
}


_SUCCESSOR_RECORDS_DIRECTORY = (
    f"/data/data/{SUCCESSOR_PACKAGE_ID}/no_backup/identity-migration"
)
# The app's data is private to it, so only root can read its records. The
# script goes to su on standard input, which both su styles on the panels
# accept, and each root route is tried without waiting on a prompt; none
# answering leaves the records unknown rather than empty.
_SUCCESSOR_RECORDS_COMMAND = (
    "for p in 'su 0' 'su root'; do "
    "r=$(printf '%s\\n' '"
    # Only a directory that exists and can be listed counts as read: an
    # absent or unlistable one is unknown, never an empty set of records.
    f"[ -d {_SUCCESSOR_RECORDS_DIRECTORY} ] || exit 3; "
    f"ls {_SUCCESSOR_RECORDS_DIRECTORY} >/dev/null 2>&1 || exit 3; "
    f"for f in {_SUCCESSOR_RECORDS_DIRECTORY}/*; do "
    '[ -e "$f" ] && echo "record:${f##*/}"; done; echo records:read'
    "' | timeout 3 $p sh 2>/dev/null) && "
    'case "$r" in *records:read*) echo "$r"; break ;; esac; done'
)


def _move_command(nonce: str, step: MoveStep) -> str:
    quiet = ">/dev/null 2>&1"
    return "; ".join(
        (
            f"echo HAPANELD_MOVE_BEGIN:{nonce}",
            *(f"{action} {quiet}" for action in _MOVE_ACTIONS[step]),
            # No pipe into grep: toybox grep on some panels reports an error
            # on an empty pipe, which would corrupt the frame.
            *(
                f'case "$(pm path {package} 2>/dev/null)" in '
                f"package:*) echo installed:{package} ;; esac"
                for package in (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID)
            ),
            f'case "$(dumpsys package {SUCCESSOR_PACKAGE_ID} 2>/dev/null)" in '
            f"*notLaunched=true*) echo unlaunched:{SUCCESSOR_PACKAGE_ID} ;; esac",
            _SUCCESSOR_RECORDS_COMMAND,
            f'echo "home:$({_HOME_QUERY})"',
            f"echo HAPANELD_MOVE_END:{nonce}:0",
        )
    )


def _parse_move(body: bytes, nonce: str) -> MoveObservation:
    lines, status_code = _parse_single_section(body, prefix="MOVE", nonce=nonce)
    homes = [line for line in lines if line.startswith("home:")]
    installed = {
        line.removeprefix("installed:")
        for line in lines
        if line.startswith("installed:")
    }
    unlaunched = [
        line for line in lines if line == f"unlaunched:{SUCCESSOR_PACKAGE_ID}"
    ]
    read = [line for line in lines if line == "records:read"]
    records = [line for line in lines if line.startswith("record:")]
    if (
        status_code != 0
        or len(homes) != 1
        or not installed <= {LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID}
        or len(unlaunched) > 1
        or len(read) > 1
        or (records and not read)
        or len(homes) + len(installed) + len(unlaunched) + len(read) + len(records)
        != len(lines)
    ):
        raise _MalformedAdbResponse
    home = homes[0].removeprefix("home:").strip()
    package, slash, _activity = home.partition("/")
    return MoveObservation(
        legacy_installed=LEGACY_PACKAGE_ID in installed,
        successor_installed=SUCCESSOR_PACKAGE_ID in installed,
        # The system chooser and an empty answer are not a HOME.
        home=package if slash and package and package != "android" else None,
        successor_unlaunched=bool(unlaunched) and SUCCESSOR_PACKAGE_ID in installed,
        successor_records=(
            frozenset(line.removeprefix("record:") for line in records)
            if read and SUCCESSOR_PACKAGE_ID in installed
            else None
        ),
    )


async def async_move_step(
    target: AdbInstallTarget, signer: PythonRSASigner, step: MoveStep
) -> MoveObservation:
    """Run one move step on the pinned panel and observe its result."""
    _validate_target(target)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_INSTALL_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            # Every connection proves the device at the address is the one the
            # move started on before any command can change it.
            nonce = token_hex(16)
            _parse_identity_root(
                await _async_shell(
                    device,
                    _identity_root_command(nonce),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
                target,
            )
            nonce = token_hex(16)
            mutation_started = step is not MoveStep.OBSERVE
            return _parse_move(
                await _async_shell(
                    device,
                    _move_command(nonce, step),
                    read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)

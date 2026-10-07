"""Read-only Android Debug Bridge preflight for panel installation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from re import fullmatch
from secrets import token_hex

from adb_shell.adb_device_async import AdbDeviceAsync
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
    AdbTimeoutError,
    DeviceAuthError,
    InvalidChecksumError,
    InvalidCommandError,
    InvalidResponseError,
    TcpTimeoutException,
)

from .app_identity import ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID
from .client import PanelAddress
from .install_adb import (
    _ADB_BANNER,
    ADB_PORT,
    AdbInstallTarget,
    _async_close,
    _BoundedTcpTransportAsync,
    _framed_value_commands,
    _is_package_path,
    _MalformedAdbResponse,
    _ObservedTarget,
    _parse_identity,
    _UnsafeAdbPacket,
)

_CONNECT_TIMEOUT_SECONDS = 5.0
# Composite package-manager observations can take over 13 seconds on panels.
_SHELL_TIMEOUT_SECONDS = 30.0
_MAX_SHELL_RESPONSE_BYTES = 16 * 1024
_PACKAGE_MANAGER_LIVENESS_PACKAGE = "android"
_MIN_ANDROID_SDK = 26
_SUPPORTED_PRIMARY_ABIS = frozenset({"arm64-v8a", "armeabi-v7a"})


class InstallTargetState(StrEnum):
    """Read-only classification of an Android installation target."""

    ADB_UNREACHABLE = "adb_unreachable"
    ADB_UNAUTHORIZED = "adb_unauthorized"
    INCOMPATIBLE = "incompatible"
    INSTALL_CANDIDATE = "install_candidate"
    INSTALLED = "installed"
    # The panel runs the legacy package and not the successor. The successor is
    # a different package to Android, so it installs beside it and the panel
    # performs the handover itself; this is not a clean target and not a refusal.
    MIGRATION_CANDIDATE = "migration_candidate"
    RETAINED_OR_AMBIGUOUS = "retained_or_ambiguous"


@dataclass(frozen=True, slots=True)
class InstallTargetProbe:
    """Result of the read-only ADB package-state preflight.

    ``INSTALL_CANDIDATE`` proves only package-manager absence. It is not
    installation admission because this rootless probe cannot exclude residual
    databases or recovery state outside package-manager visibility.
    """

    state: InstallTargetState
    model: str | None = None
    serial: str | None = None
    primary_abi: str | None = None
    android_sdk: int | None = None

    def adb_target(self, address: PanelAddress) -> AdbInstallTarget | None:
        """The ADB target at ``address`` this probe identified, or None."""
        if (
            self.serial is None
            or self.model is None
            or self.primary_abi is None
            or self.android_sdk is None
        ):
            return None
        return AdbInstallTarget(
            address=address,
            serial=self.serial,
            model=self.model,
            primary_abi=self.primary_abi,
            android_sdk=self.android_sdk,
        )

    # Set only after the selected descriptor has proved the installed APK's
    # size and digest. The byte count carries that proof into plan creation.
    installed_artifact_size: int | None = None


class _MalformedProbeResponse(Exception):
    """Raised when ADB answered but did not prove a safe classification."""


class _PackagePresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class _RetainedPackageData(StrEnum):
    RETAINED = "retained"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def _parse_status_marker(line: str, prefix: str, nonce: str) -> int | None:
    """Parse one nonce-bound child exit-status marker."""
    match = fullmatch(rf"{prefix}:{nonce}:([0-9]{{1,3}})", line)
    if match is None:
        return None
    return int(match.group(1))


def _parse_package_presence(
    output: str, nonce: str
) -> dict[str, _PackagePresence] | None:
    """Classify each accepted package only from a complete nonce-bound frame.

    Every accepted application id is read in its own segment. Reading them apart
    is what lets the caller tell a clean panel from one that is still running
    the legacy package and can be migrated.
    """
    count = len(ACCEPTED_PACKAGE_IDS)
    begin = f"HAPANELD_PKG_BEGIN:{nonce}"
    live_prefix = "HAPANELD_PKG_LIVE"
    end = f"HAPANELD_PKG_END:{nonce}"
    segment = 0
    target_status: list[int | None] = [None] * count
    target_path = [False] * count
    live_status: int | None = None
    live_path = False

    for line in output.replace("\r", "").splitlines():
        if line == begin:
            if segment != 0:
                return None
            segment = 1
            continue

        marker = None
        for index in range(count):
            marker = _parse_status_marker(line, f"HAPANELD_PKG_TARGET{index}", nonce)
            if marker is not None:
                if segment != index + 1:
                    return None
                target_status[index] = marker
                segment = index + 2
                break
        if marker is not None:
            continue

        status = _parse_status_marker(line, live_prefix, nonce)
        if status is not None:
            if segment != count + 1:
                return None
            live_status = status
            segment = count + 2
            continue

        if line == end:
            if segment != count + 2:
                return None
            segment = count + 3
            continue

        if line.startswith("HAPANELD_PKG_"):
            return None
        if line.startswith("package:"):
            if not _is_package_path(line):
                return None
            if 1 <= segment <= count:
                target_path[segment - 1] = True
            elif segment == count + 1:
                live_path = True
            else:
                return None
            continue
        if line:
            return None

    if segment != count + 3 or live_status != 0 or not live_path:
        return None
    presence: dict[str, _PackagePresence] = {}
    for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS):
        if target_path[index]:
            if target_status[index] != 0:
                return None
            presence[package_id] = _PackagePresence.PRESENT
        elif target_status[index] in (0, 1):
            presence[package_id] = _PackagePresence.ABSENT
        else:
            return None
    return presence


def _parse_retained_package_data(
    output: str, nonce: str
) -> dict[str, _RetainedPackageData] | None:
    """Classify retained data per accepted id from the uninstalled-package list."""
    count = len(ACCEPTED_PACKAGE_IDS)
    begin = f"HAPANELD_DATA_BEGIN:{nonce}"
    live_prefix = "HAPANELD_DATA_LIVE"
    end = f"HAPANELD_DATA_END:{nonce}"
    segment = 0
    target_status: list[int | None] = [None] * count
    retained = [False] * count
    live_status: int | None = None
    live_path = False

    for line in output.replace("\r", "").splitlines():
        if line == begin:
            if segment != 0:
                return None
            segment = 1
            continue

        marker = None
        for index in range(count):
            marker = _parse_status_marker(line, f"HAPANELD_DATA_TARGET{index}", nonce)
            if marker is not None:
                if segment != index + 1:
                    return None
                target_status[index] = marker
                segment = index + 2
                break
        if marker is not None:
            continue

        status = _parse_status_marker(line, live_prefix, nonce)
        if status is not None:
            if segment != count + 1:
                return None
            live_status = status
            segment = count + 2
            continue

        if line == end:
            if segment != count + 2:
                return None
            segment = count + 3
            continue

        if line.startswith("HAPANELD_DATA_"):
            return None
        if 1 <= segment <= count:
            if line == f"package:{ACCEPTED_PACKAGE_IDS[segment - 1]}":
                retained[segment - 1] = True
            elif line:
                return None
        elif line.startswith("package:"):
            if segment != count + 1 or not _is_package_path(line):
                return None
            live_path = True
        elif line:
            return None

    if (
        segment != count + 3
        or live_status != 0
        or not live_path
        or any(status != 0 for status in target_status)
    ):
        return None
    return {
        package_id: (
            _RetainedPackageData.RETAINED
            if retained[index]
            else _RetainedPackageData.ABSENT
        )
        for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS)
    }


def _package_presence_command(nonce: str) -> str:
    """Build the static read-only installed-package observation."""
    targets = "".join(
        f"pm path {package_id}; echo HAPANELD_PKG_TARGET{index}:{nonce}:$?; "
        for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS)
    )
    return (
        f"echo HAPANELD_PKG_BEGIN:{nonce}; "
        f"{targets}"
        f"pm path {_PACKAGE_MANAGER_LIVENESS_PACKAGE}; "
        f"echo HAPANELD_PKG_LIVE:{nonce}:$?; "
        f"echo HAPANELD_PKG_END:{nonce}"
    )


def _retained_package_data_command(nonce: str) -> str:
    """Build the static read-only retained-package observation."""
    targets = "".join(
        f"pm list packages -u {package_id}; "
        f"echo HAPANELD_DATA_TARGET{index}:{nonce}:$?; "
        for index, package_id in enumerate(ACCEPTED_PACKAGE_IDS)
    )
    return (
        f"echo HAPANELD_DATA_BEGIN:{nonce}; "
        f"{targets}"
        f"pm path {_PACKAGE_MANAGER_LIVENESS_PACKAGE}; "
        f"echo HAPANELD_DATA_LIVE:{nonce}:$?; "
        f"echo HAPANELD_DATA_END:{nonce}"
    )


async def _async_target_facts(device: AdbDeviceAsync) -> _ObservedTarget:
    """Read the panel's model, serial, ABI and SDK as the installer frames them."""
    nonce = token_hex(16)
    output = await _async_bounded_shell(
        device, "; ".join(_framed_value_commands("ID", nonce))
    )
    return _parse_identity(output.encode(), nonce, "ID")


async def _async_bounded_shell(device: AdbDeviceAsync, command: str) -> str:
    """Run a read-only shell observation with time and output bounds."""
    body = bytearray()
    async with asyncio.timeout(_SHELL_TIMEOUT_SECONDS):
        async for chunk in device.streaming_shell(
            command,
            transport_timeout_s=_SHELL_TIMEOUT_SECONDS,
            read_timeout_s=_SHELL_TIMEOUT_SECONDS,
            decode=False,
        ):
            if not isinstance(chunk, bytes):
                raise _MalformedProbeResponse
            if len(body) + len(chunk) > _MAX_SHELL_RESPONSE_BYTES:
                raise _MalformedProbeResponse
            body.extend(chunk)

    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as err:
        raise _MalformedProbeResponse from err


def _refuse_authorization(_device: object) -> None:
    """Stop before offering a public key outside an explicit consent step."""
    raise DeviceAuthError("ADB authorization requires explicit consent")


async def async_probe_install_target(
    address: PanelAddress,
    signer: PythonRSASigner | None = None,
    *,
    authorize: bool = False,
) -> InstallTargetProbe:
    """Probe package absence without claiming installation admission.

    A signer authenticates existing trust without prompting. Only an explicit
    authorization step may set ``authorize`` to offer its public key for the
    owner to approve on the panel. An
    ``INSTALL_CANDIDATE`` result still requires a later privileged residual-state
    check before any installation may be admitted.
    """
    transport = _BoundedTcpTransportAsync(address.host, ADB_PORT)
    device = AdbDeviceAsync(
        transport,
        default_transport_timeout_s=_CONNECT_TIMEOUT_SECONDS,
        banner=_ADB_BANNER,
    )
    state = InstallTargetState.ADB_UNREACHABLE
    facts: _ObservedTarget | None = None

    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            connected = await device.connect(
                rsa_keys=[] if signer is None else [signer],
                auth_callback=None if authorize else _refuse_authorization,
                transport_timeout_s=_CONNECT_TIMEOUT_SECONDS,
                auth_timeout_s=_CONNECT_TIMEOUT_SECONDS,
                read_timeout_s=_CONNECT_TIMEOUT_SECONDS,
            )
        if not connected:
            return InstallTargetProbe(state=state)

        nonce = token_hex(16)
        presence = _parse_package_presence(
            await _async_bounded_shell(device, _package_presence_command(nonce)),
            nonce,
        )
        if presence is None:
            # An unreadable frame is never admission.
            state = InstallTargetState.RETAINED_OR_AMBIGUOUS
        elif presence[SUCCESSOR_PACKAGE_ID] is _PackagePresence.PRESENT:
            state = InstallTargetState.INSTALLED
            facts = await _async_target_facts(device)
        elif presence[LEGACY_PACKAGE_ID] is _PackagePresence.PRESENT:
            # A legacy panel with no successor can take the successor beside it.
            # Its own data is what the handover migrates, so it is not residue.
            facts = await _async_target_facts(device)
            if (
                facts.android_sdk < _MIN_ANDROID_SDK
                or facts.primary_abi not in _SUPPORTED_PRIMARY_ABIS
            ):
                state = InstallTargetState.INCOMPATIBLE
            else:
                state = InstallTargetState.MIGRATION_CANDIDATE
        else:
            nonce = token_hex(16)
            retained_data = _parse_retained_package_data(
                await _async_bounded_shell(
                    device, _retained_package_data_command(nonce)
                ),
                nonce,
            )
            if retained_data is not None and all(
                value is _RetainedPackageData.ABSENT for value in retained_data.values()
            ):
                facts = await _async_target_facts(device)
                if (
                    facts.android_sdk < _MIN_ANDROID_SDK
                    or facts.primary_abi not in _SUPPORTED_PRIMARY_ABIS
                ):
                    state = InstallTargetState.INCOMPATIBLE
                else:
                    state = InstallTargetState.INSTALL_CANDIDATE
            else:
                state = InstallTargetState.RETAINED_OR_AMBIGUOUS
    except DeviceAuthError:
        state = InstallTargetState.ADB_UNAUTHORIZED
    except _MalformedProbeResponse, _MalformedAdbResponse, _UnsafeAdbPacket:
        state = InstallTargetState.RETAINED_OR_AMBIGUOUS
    except (
        AdbConnectionError,
        AdbTimeoutError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        OSError,
        TimeoutError,
        TcpTimeoutException,
    ):
        state = InstallTargetState.ADB_UNREACHABLE
    finally:
        await _async_close(device)

    if facts is not None:
        return InstallTargetProbe(
            state=state,
            model=facts.model,
            serial=facts.serial,
            primary_abi=facts.primary_abi,
            android_sdk=facts.android_sdk,
        )
    return InstallTargetProbe(state=state)

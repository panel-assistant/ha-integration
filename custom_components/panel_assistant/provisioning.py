"""Read-only Android Debug Bridge preflight for panel installation."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from re import ASCII, fullmatch
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
from adb_shell.transport.tcp_transport_async import TcpTransportAsync

from .app_identity import ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID
from .client import PanelAddress

ADB_PORT = 5555
_ADB_BANNER = "ha-paneld-home-assistant"
_CONNECT_TIMEOUT_SECONDS = 5.0
_SHELL_TIMEOUT_SECONDS = 5.0
_CLOSE_TIMEOUT_SECONDS = 2.0
_MAX_SHELL_RESPONSE_BYTES = 16 * 1024
_MAX_ADB_PACKET_BODY_BYTES = _MAX_SHELL_RESPONSE_BYTES
_MAX_ADB_CONNECTION_READ_BYTES = 64 * 1024
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


class _MalformedProbeResponse(Exception):
    """Raised when ADB answered but did not prove a safe classification."""


class _OversizedAdbPacket(Exception):
    """Raised before adb-shell reads an excessive peer-declared packet body."""


class _BoundedTcpTransportAsync(TcpTransportAsync):
    """TCP transport that bounds every adb-shell packet read at its source."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(host, port)
        self._received_bytes = 0

    async def bulk_read(
        self, numbytes: int, transport_timeout_s: float | None
    ) -> bytes:
        """Refuse an excessive requested read before touching the socket reader."""
        if (
            not isinstance(numbytes, int)
            or numbytes < 1
            or numbytes > _MAX_ADB_PACKET_BODY_BYTES
        ):
            raise _OversizedAdbPacket
        remaining = _MAX_ADB_CONNECTION_READ_BYTES - self._received_bytes
        data = await super().bulk_read(
            min(numbytes, remaining + 1), transport_timeout_s
        )
        if not data:
            raise AdbConnectionError("ADB peer closed during a bounded read")
        if len(data) > remaining:
            raise _OversizedAdbPacket
        self._received_bytes += len(data)
        return data


class _PackagePresence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class _RetainedPackageData(StrEnum):
    RETAINED = "retained"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _TargetFacts:
    model: str
    serial: str
    primary_abi: str
    android_sdk: int


def _parse_status_marker(line: str, prefix: str, nonce: str) -> int | None:
    """Parse one nonce-bound child exit-status marker."""
    match = fullmatch(rf"{prefix}:{nonce}:([0-9]{{1,3}})", line)
    if match is None:
        return None
    return int(match.group(1))


def _is_package_path(line: str) -> bool:
    """Return whether a package-manager path is absolute and unambiguous."""
    return fullmatch(r"package:/[^ \t]+", line) is not None


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


def _parse_target_facts(output: str, nonce: str) -> _TargetFacts:
    """Parse four bounded properties from an exact nonce-bound response."""
    property_names = ("MODEL", "SERIAL", "ABI", "SDK")
    lines = output.replace("\r", "").splitlines()
    expected_lines = 2 + 3 * len(property_names)
    if (
        len(lines) != expected_lines
        or lines[0] != f"HAPANELD_ID_BEGIN:{nonce}"
        or lines[-1] != f"HAPANELD_ID_END:{nonce}"
    ):
        raise _MalformedProbeResponse

    values: dict[str, str] = {}
    offset = 1
    for name in property_names:
        if lines[offset] != f"HAPANELD_ID_{name}_BEGIN:{nonce}":
            raise _MalformedProbeResponse
        value = lines[offset + 1]
        status = _parse_status_marker(
            lines[offset + 2], f"HAPANELD_ID_{name}_END", nonce
        )
        if status != 0:
            raise _MalformedProbeResponse
        values[name] = value
        offset += 3

    model = values["MODEL"]
    serial = values["SERIAL"]
    primary_abi = values["ABI"]
    sdk_text = values["SDK"]
    if (
        model != model.strip()
        or not 1 <= len(model) <= 128
        or not model.isprintable()
        or fullmatch(r"[A-Za-z0-9._:-]{1,128}", serial, flags=ASCII) is None
        or fullmatch(r"[A-Za-z0-9_.-]{1,64}", primary_abi, flags=ASCII) is None
        or fullmatch(r"[0-9]{1,3}", sdk_text, flags=ASCII) is None
    ):
        raise _MalformedProbeResponse

    android_sdk = int(sdk_text)
    if not 1 <= android_sdk <= 100:
        raise _MalformedProbeResponse
    return _TargetFacts(
        model=model,
        serial=serial,
        primary_abi=primary_abi,
        android_sdk=android_sdk,
    )


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


def _target_facts_command(nonce: str) -> str:
    """Build the static read-only physical-target identification observation."""
    properties = (
        ("MODEL", "ro.product.model"),
        ("SERIAL", "ro.serialno"),
        ("ABI", "ro.product.cpu.abi"),
        ("SDK", "ro.build.version.sdk"),
    )
    commands = [f"echo HAPANELD_ID_BEGIN:{nonce}"]
    for name, android_property in properties:
        commands.extend(
            (
                f"echo HAPANELD_ID_{name}_BEGIN:{nonce}",
                f"getprop {android_property}",
                f"echo HAPANELD_ID_{name}_END:{nonce}:$?",
            )
        )
    commands.append(f"echo HAPANELD_ID_END:{nonce}")
    return "; ".join(commands)


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


async def _async_close(device: AdbDeviceAsync) -> None:
    """Bound cleanup without allowing it to hide the probe result."""
    with suppress(Exception):
        async with asyncio.timeout(_CLOSE_TIMEOUT_SECONDS):
            await device.close()


async def async_probe_install_target(
    address: PanelAddress, signer: PythonRSASigner | None = None
) -> InstallTargetProbe:
    """Probe package absence without claiming installation admission.

    When a signer is supplied, ADB may present its public key to the panel so the
    user can approve this Home Assistant instance explicitly. An
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
    facts: _TargetFacts | None = None

    try:
        async with asyncio.timeout(_CONNECT_TIMEOUT_SECONDS):
            connected = await device.connect(
                rsa_keys=[] if signer is None else [signer],
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
        elif presence[LEGACY_PACKAGE_ID] is _PackagePresence.PRESENT:
            # A legacy panel with no successor can take the successor beside it.
            # Its own data is what the handover migrates, so it is not residue.
            nonce = token_hex(16)
            facts = _parse_target_facts(
                await _async_bounded_shell(device, _target_facts_command(nonce)),
                nonce,
            )
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
                nonce = token_hex(16)
                facts = _parse_target_facts(
                    await _async_bounded_shell(device, _target_facts_command(nonce)),
                    nonce,
                )
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
    except _MalformedProbeResponse, _OversizedAdbPacket:
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

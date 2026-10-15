"""ADB wire protocol for Panel Assistant's network installer.

The bounded transport, the authenticated connection, and every shell program
the installer sends with the parser of its framed answer.  A command and its
parser share one nonce and section contract, so they live together here; the
install steps in :mod:`install_adb` decide what to run and in what order.
"""

from __future__ import annotations

import asyncio
import math
import re
import shlex
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

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
    EQUIVALENT_HOME_COMPONENTS,
    OBSOLETE_ACCESSIBILITY_COMPONENTS,
    SUCCESSOR_PACKAGE_ID,
    counterpart_of,
)
from .client import PanelAddress
from .home_chooser import CHOOSER_OUTCOMES, home_claim_command
from .home_chooser import HOME_QUERY as _HOME_QUERY
from .release import (
    _MAX_APK_BYTES,
    InstallDescriptor,
)
from .vendor_provisioning import (
    VENDOR_PROBE,
    needs_vendor_setup,
)

ADB_PORT = 5555


_ADB_BANNER = "ha-paneld-home-assistant"


_PACKAGE_MANAGER_LIVENESS_PACKAGE = "android"


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


_CLOSE_TIMEOUT_SECONDS = 2.0


_SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", flags=re.ASCII)


_ABI_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$", flags=re.ASCII)


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


@dataclass(frozen=True, slots=True)
class AdbInstallTarget:
    """One pre-pinned private address and its previously observed identity."""

    address: PanelAddress
    serial: str
    model: str
    primary_abi: str
    android_sdk: int


def same_physical_target(observed: Any, expected: Any) -> bool:
    """Whether two observations name one Android device: serial, model, ABI, SDK."""
    return (
        observed.serial,
        observed.model,
        observed.primary_abi,
        observed.android_sdk,
    ) == (expected.serial, expected.model, expected.primary_abi, expected.android_sdk)


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


#: The runtime permissions the panel's features use, each granted only where
#: Android lists the hardware feature; the browser installer's permission
#: contract grants the same pairs (``RUNTIME_GRANTS``).
RUNTIME_GRANTS = (("camera.any", "CAMERA"), ("microphone", "RECORD_AUDIO"))


_SERVICE_COMPONENT = re.compile(
    r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z.][A-Za-z0-9_.$]*", flags=re.ASCII
)


_APPOP_ALLOWED = r"{}: allow(?:; [\x20-\x7e]{{1,1024}})?"


_GRANTED_APP_OPS = "WRITE_SETTINGS SYSTEM_ALERT_WINDOW REQUEST_INSTALL_PACKAGES"


_SECURE_SETTINGS_PERMISSION = "android.permission.WRITE_SECURE_SETTINGS"


def _secure_settings_grant_command(package_id: str) -> str:
    """Grant the secure-settings permission the build asks for, once.

    Full screen-off on a panel whose firmware keeps the screen on while
    plugged in (the Shelly X2i) lifts and restores that Global setting, which
    only this permission allows; without it the app dims instead. It is an
    install-time permission Android lists only once granted, so a dump that
    names the request but no grant is the proof it is missing. A build that
    does not ask for it is ``unsupported``; an unreadable dump is ``unknown``.
    """
    permission = re.escape(_SECURE_SETTINGS_PERMISSION)
    read = f"secure=$(dumpsys package {package_id} 2>/dev/null); secure_status=$?"
    granted = f"grep -Eq '^[[:space:]]*{permission}: granted=true'"
    return (
        f"{read}; "
        'if [ "$secure_status" -ne 0 ] || [ -z "$secure" ]; then '
        "echo secure_settings:unknown; "
        f"elif ! printf '%s\\n' \"$secure\" | grep -Eqx '[[:space:]]*{permission}'; "
        "then echo secure_settings:unsupported; else "
        f"printf '%s\\n' \"$secure\" | {granted} || "
        f"{{ pm grant {package_id} {_SECURE_SETTINGS_PERMISSION} >/dev/null 2>&1; "
        f"{read}; }}; "
        f"if printf '%s\\n' \"$secure\" | {granted}; "
        "then echo secure_settings:granted; else echo secure_settings:unknown; fi; fi"
    )


def _permission_grant_command(
    nonce: str, package_id: str, launch_component: str
) -> str:
    """Repair only grants Android proves missing before an install or update start.

    Settings writes, the overlay and the accessibility service are what the
    panel's controls need. The install-packages app-op lets a panel without
    network ADB offer its own signed update for the owner to confirm. The
    browser installer's permission contract grants them the same way.
    Keep unrelated services and remove only the obsolete
    class of the same app, appending the current service when no valid spelling exists.
    A rerun writes nothing and other apps' services stay enabled. The list read before
    the write is printed so the readback can prove nothing was dropped. Grant
    output is discarded; only the readback decides.

    The build's dashboard is made the panel's HOME whenever HOME resolves to
    anything else: Android forgets a preferred HOME when the set of packages
    changes, so every install, update and repair asserts it again. Where the
    firmware only preselects it in Android's chooser, the chooser's Always is
    pressed (``home_chooser``). The custom-support probe is read last, for the
    vendor setup that follows (``vendor_provisioning``).
    """
    component = ACCESSIBILITY_COMPONENTS[launch_component]
    known = "|".join(
        f"*:{name}:*" for name in EQUIVALENT_ACCESSIBILITY_COMPONENTS[launch_component]
    )
    obsolete = "|".join(OBSOLETE_ACCESSIBILITY_COMPONENTS.get(launch_component, ()))
    quiet = ">/dev/null 2>&1"
    # Android 14 reports an unset operation as two lines, including its default.
    default_modes = "|".join(
        f"'No operations.\nDefault mode: {mode}'"
        for mode in ("allow", "default", "deny", "ignore", "foreground", "errored")
    )
    read_operation = (
        f'mode=$(appops get {package_id} "$operation"); mode_status=$?; '
        f'case "$mode" in {default_modes}) mode="$operation: ${{mode##*: }}" ;; esac; '
    )
    runtime_grants = []
    for feature, permission in RUNTIME_GRANTS:
        granted = (
            rf"[[:space:]]*android\.permission\.{permission}: granted=true"
            r"(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*"
        )
        listed = rf"feature:android\.hardware\.{re.escape(feature)}(=[0-9]+)?"
        runtime_grants.append(
            'if [ "$features_status" -ne 0 ] || [ -z "$features" ]; then echo unknown; '
            f"elif printf '%s\\n' \"$features\" | grep -Eqx '{listed}'; then "
            + _runtime_permission_repair_command(
                package_id, f"android.permission.{permission}"
            )
            + '; if [ -n "$runtime" ] && ! printf \'%s\\n\' "$runtime" | '
            f"grep -Evq '^{granted}$'; then echo granted; else echo unknown; fi; "
            "else echo unsupported; fi"
        )
    return "; ".join(
        (
            f"echo HAPANELD_PERMISSIONS_BEGIN:{nonce}",
            f"before=$({_SERVICES_SETTING})",
            "before_status=$?",
            'echo "$before"',
            'after=""',
            'valid=1; [ "${#before}" -le 4096 ] || valid=0; '
            "case \"$before\" in *'\n'*) valid=0 ;; esac; "
            'case "$before" in ""|null) ;; *) rest="$before"; seen=:; count=0; '
            "while :; do service=${rest%%:*}; count=$((count+1)); "
            'if [ "$count" -gt 64 ] || ! printf \'%s\\n\' "$service" | '
            "grep -Eq '^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z.][A-Za-z0-9_.$]*$'; "
            "then valid=0; break; fi; "
            'case "$seen" in *:"$service":*) valid=0; break ;; esac; '
            f'case "$service" in {obsolete or "never-a-component"}) ;; '
            '*) after="${after:+$after:}$service" ;; esac; '
            'seen="$seen$service:"; case "$rest" in *:*) rest=${rest#*:} ;; '
            "*) break ;; esac; done ;; esac; "
            'if [ "$before_status" -eq 0 ] && [ "$valid" -eq 1 ]; then '
            'case ":$after:" in '
            f"{known}) ;; "
            f'*) after="${{after:+$after:}}{component}" ;; esac; '
            'if [ "$after" != "$before" ]; then '
            f"current=$({_SERVICES_SETTING}); current_status=$?; "
            '[ "$current_status" -eq 0 ] && [ "$current" = "$before" ] && '
            f'settings put secure enabled_accessibility_services "$after" {quiet}; '
            "fi; fi",
            f"for operation in {_GRANTED_APP_OPS}; do "
            + read_operation
            + 'if [ "$mode_status" -eq 0 ] && [ -n "$mode" ] && '
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
            home_claim_command(launch_component),
            _SERVICES_SETTING,
            "settings get secure accessibility_enabled",
            f"for operation in {_GRANTED_APP_OPS}; do "
            + read_operation
            + 'if [ "$mode_status" -eq 0 ]; then printf \'%s\\n\' "$mode"; '
            "else echo unknown; fi; done",
            # `pm list features`, not `pm has-feature`: Android 8.1 has no
            # has-feature and answers it with its usage text.
            "features=$(pm list features 2>/dev/null); features_status=$?",
            *runtime_grants,
            'echo "home_chooser:$hp_chooser"',
            _secure_settings_grant_command(package_id),
            f'echo "home:$({_HOME_QUERY})"',
            *VENDOR_PROBE,
            f"echo HAPANELD_PERMISSIONS_END:{nonce}:$?",
        )
    )


def _expected_services(before: str, launch_component: str) -> str | None:
    """The list after a grant, as the browser contract's ``expectedServices``."""
    services = [] if before in ("", "null") else before.split(":")
    if (
        len(before) > 4096
        or len(services) > 64
        or len(set(services)) != len(services)
        or not all(_SERVICE_COMPONENT.fullmatch(name) for name in services)
    ):
        return None
    known = EQUIVALENT_ACCESSIBILITY_COMPONENTS[launch_component]
    obsolete = OBSOLETE_ACCESSIBILITY_COMPONENTS.get(launch_component, ())
    services = [name for name in services if name not in obsolete]
    if any(name in known for name in services):
        return ":".join(services)
    return ":".join((*services, ACCESSIBILITY_COMPONENTS[launch_component]))


@dataclass(frozen=True, slots=True)
class _PermissionGrant:
    """The readback of one grant program."""

    #: The grants that did not read back as granted; empty when all did.
    missing: tuple[str, ...]
    #: HOME resolves to this build's dashboard.
    home: bool
    #: What the HOME claim did (``home_chooser.CHOOSER_OUTCOMES``), or None.
    chooser: str | None
    #: ``vendor_provisioning`` has a setup for this panel.
    vendor_setup: bool = False

    @property
    def granted(self) -> bool:
        """Every permission the panel's controls need reads back as granted."""
        return not self.missing


def _parse_permission_grant(
    body: bytes, nonce: str, launch_component: str
) -> _PermissionGrant:
    """Granted only when every readback line shows the grant and no service lost."""
    refused = _PermissionGrant(missing=("grant readback",), home=False, chooser=None)
    try:
        lines, status = _parse_single_section(body, prefix="PERMISSIONS", nonce=nonce)
    except _MalformedAdbResponse:
        return refused
    if status != 0 or len(lines) != 13:
        return refused
    before, after, enabled, write_settings, overlay, installs = lines[:6]
    camera, microphone = lines[6:8]
    chooser, secure_settings, home = (line.partition(":")[2] for line in lines[8:11])
    vendor_setup = needs_vendor_setup(lines[11], lines[12])
    if (
        not lines[8].startswith("home_chooser:")
        or not lines[9].startswith("secure_settings:")
        or not lines[10].startswith("home:")
        or vendor_setup is None
    ):
        return refused
    expected = _expected_services(before, launch_component)
    checks = (
        (
            "accessibility service",
            expected is not None and after == expected and enabled == "1",
        ),
        (
            "modify system settings",
            re.fullmatch(_APPOP_ALLOWED.format("WRITE_SETTINGS"), write_settings)
            is not None,
        ),
        (
            "display over other apps",
            re.fullmatch(_APPOP_ALLOWED.format("SYSTEM_ALERT_WINDOW"), overlay)
            is not None,
        ),
        (
            "install packages",
            re.fullmatch(_APPOP_ALLOWED.format("REQUEST_INSTALL_PACKAGES"), installs)
            is not None,
        ),
        ("camera", camera in ("granted", "unsupported")),
        ("microphone", microphone in ("granted", "unsupported")),
        ("secure settings", secure_settings in ("granted", "unsupported")),
    )
    return _PermissionGrant(
        missing=tuple(name for name, ok in checks if not ok),
        home=home in EQUIVALENT_HOME_COMPONENTS[launch_component],
        chooser=chooser if chooser in CHOOSER_OUTCOMES else None,
        vendor_setup=vendor_setup,
    )


def _launch_command(nonce: str, package_id: str, launch_component: str) -> str:
    """Start the installed build by the exact component its descriptor names.

    One application id can carry its classes in either Kotlin package, so the
    component is the descriptor's, never built from the package id.
    """
    return "; ".join(
        (
            f"echo HAPANELD_LAUNCH_BEGIN:{nonce}",
            f"am start -W -n {launch_component} -p {package_id}",
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
    if not same_physical_target(observed, expected):
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
    target: AdbInstallTarget, signer: PythonRSASigner, *, authorize: bool = True
) -> AdbDeviceAsync:
    """Connect with existing trust; offer the public key only when ``authorize``."""
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
        if not authorize:
            # Raised before the library sends the key, so nothing is prompted.
            raise DeviceAuthError("ADB authorization requires explicit consent")

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


def _validate_filesync_maxdata(device: AdbDeviceAsync) -> None:
    maxdata = getattr(device, "_maxdata", None)
    if (
        isinstance(maxdata, bool)
        or not isinstance(maxdata, int)
        or not _MIN_ADB_MAXDATA <= maxdata <= _MAX_ADB_MAXDATA
    ):
        raise InstallAdbError(InstallAdbErrorCode.FILESYNC_UNSAFE)

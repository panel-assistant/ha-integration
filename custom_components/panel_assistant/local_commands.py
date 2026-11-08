"""Typed helper commands: their names, arguments and reply-field mappers.

A panel whose app offers ``local_adb_commands_v1`` runs fixed helper commands
(``adb.cmd``) in place of the shell programs ``adb.shell`` carries. Each reply
is ``key=value`` fields, identity first. The mappers here turn those fields
into the same result types the shell parsers produce, bounded by the same
rules, so every caller sees the same outcomes on either route.
"""

from __future__ import annotations

import re
from typing import Any, Final

from .adb_protocol import (
    _SERVICE_COMPONENT,
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    _MalformedAdbResponse,
    _observed_target,
    _ObservedTarget,
    _parse_root_mode,
    _PermissionGrant,
    _require_same_target,
)
from .app_identity import EQUIVALENT_HOME_COMPONENTS, LAUNCH_COMPONENTS
from .home_chooser import CHOOSER_OUTCOMES
from .vendor_provisioning import (
    META_PORTAL_DEVICES,
    UNREAD_SETUP_WARNING,
    ShellySetup,
    VendorSetup,
)

VERB_IDENTITY: Final = "PA_IDENTITY"
VERB_REPAIR: Final = "PA_REPAIR"
VERB_HOME: Final = "PA_HOME"
VERB_ADMIN: Final = "PA_ADMIN"
VERB_VENDOR: Final = "PA_VENDOR"
VERB_RESTORE: Final = "PA_RESTORE"
VERB_OBSERVE: Final = "PA_OBSERVE"
VERB_HANDBACK: Final = "PA_HANDBACK"
VERBS: Final = frozenset(
    {
        VERB_IDENTITY,
        VERB_REPAIR,
        VERB_HOME,
        VERB_ADMIN,
        VERB_VENDOR,
        VERB_RESTORE,
        VERB_OBSERVE,
        VERB_HANDBACK,
    }
)

#: How long Panel Assistant waits for each verb's final outcome. The app
#: applies its own fixed deadline per verb; HOME waits on the chooser.
WAIT_SECONDS: Final = {verb: 40.0 for verb in VERBS} | {VERB_HOME: 75.0}

#: The app's request grammar: at most three arguments, each printable ASCII
#: without spaces and at most 159 bytes.
MAX_ARGS: Final = 3
_ARG = re.compile(r"[\x21-\x7e]{1,159}", flags=re.ASCII)

#: The reply bounds the helper applies, checked again here.
MAX_FIELDS: Final = 64
MAX_REPLY_BYTES: Final = 4096
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,63}", flags=re.ASCII)
_VALUE = re.compile(r"[\x20-\x3c\x3e-\x7e]{0,255}", flags=re.ASCII)

# Refusal codes the app sends that decide routing. ``expired`` means the
# helper is being replaced: it is never read as "unsupported".
REFUSAL_UNKNOWN_COMMAND: Final = "unknown_command"
REFUSAL_EXPIRED: Final = "expired"

_GRANT_STATES: Final = ("granted", "unsupported", "unknown")
_OP_STATE = re.compile(r"[a-z_]{1,32}", flags=re.ASCII)
_DEVICE = re.compile(r"[A-Za-z0-9_.-]{1,64}", flags=re.ASCII)
# "none" means no policy broadcast reply was recognised; the shell route
# deferred then too.
_HOME_POLICIES: Final = ("builtin", "retained", "restore", "claimed", "shell", "none")
_APP_OPS: Final = ("WRITE_SETTINGS", "SYSTEM_ALERT_WINDOW", "REQUEST_INSTALL_PACKAGES")
_RESTORE_RESULTS: Final = ("restored", "unrestored", "none")


class LocalCommandRefused(InstallAdbError):
    """The app or its helper refused a typed command; never a shell fallback.

    ``refusal`` is the helper's code (``args``, ``busy``, ``failed``, ...) or
    the app's own (``expired``, ``unknown_command``, ...).
    """

    def __init__(self, refusal: str) -> None:
        # PA_REPAIR's ``absent`` is the shell route's installed-package-missing.
        super().__init__(
            InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING
            if refusal == "absent"
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        self.refusal = refusal


def valid_args(verb: str, args: tuple[str, ...]) -> bool:
    """Whether a request meets the app's grammar; table checks stay in C."""
    return (
        verb in VERBS
        and len(args) <= MAX_ARGS
        and all(isinstance(arg, str) and _ARG.fullmatch(arg) for arg in args)
    )


def bounded_fields(result: Any) -> dict[str, str]:
    """Accept only ``{"ok": true, "fields": {...}}`` within the reply bounds."""
    if (
        not isinstance(result, dict)
        or set(result) != {"ok", "fields"}
        or result["ok"] is not True
        or not isinstance(result["fields"], dict)
        or len(result["fields"]) > MAX_FIELDS
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID)
    fields: dict[str, str] = {}
    size = 0
    for key, value in result["fields"].items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or _KEY.fullmatch(key) is None
            or _VALUE.fullmatch(value) is None
        ):
            raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID)
        size += len(key) + len(value) + 2
        fields[key] = value
    if size > MAX_REPLY_BYTES:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID)
    return fields


def _field(fields: dict[str, str], key: str) -> str:
    try:
        return fields[key]
    except KeyError:
        raise _MalformedAdbResponse from None


def identity_from_fields(fields: dict[str, str]) -> _ObservedTarget:
    """The identity every reply starts with, bounded like the shell's."""
    return _observed_target(
        _field(fields, "model"),
        _field(fields, "serial"),
        _field(fields, "abi"),
        _field(fields, "sdk"),
    )


def root_mode_from_fields(fields: dict[str, str]) -> AdbRootMode:
    """The posture, classified by the very rules the shell readback uses."""
    identity_from_fields(fields)
    return _parse_root_mode(
        {
            name: ([_field(fields, key)], 0)
            for name, key in (
                ("UID", "uid"),
                ("SECURE", "secure"),
                ("DEBUGGABLE", "debuggable"),
                ("SU", "su"),
            )
        }
    )


def require_target(fields: dict[str, str], target: AdbInstallTarget) -> AdbRootMode:
    """Pin the reply to the panel the session proved, then read its posture."""
    _require_same_target(identity_from_fields(fields), target)
    return root_mode_from_fields(fields)


def launch_index(package_id: str, launch_component: str) -> str:
    """The launcher's position, which selects the helper's component tables."""
    try:
        return str(LAUNCH_COMPONENTS[package_id].index(launch_component))
    except KeyError, ValueError:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST) from None


def permission_grant_from_fields(
    repair: dict[str, str], home: dict[str, str], launch_component: str
) -> _PermissionGrant:
    """Compose REPAIR and HOME into the shell's one grant readback."""
    refused = _PermissionGrant(missing=("grant readback",), home=False, chooser=None)
    try:
        ops = [_field(repair, f"op.{op}") for op in _APP_OPS]
        states = [
            _field(repair, key) for key in ("camera", "microphone", "secure_settings")
        ]
        accessibility = _field(repair, "accessibility")
        enabled = _field(repair, "accessibility_enabled")
        device = _field(repair, "device")
        vendor = _field(repair, "vendor")
        policy = _field(home, "policy")
        chooser = _field(home, "chooser")
        current = _field(home, "home")
    except _MalformedAdbResponse:
        return refused
    if (
        accessibility not in ("granted", "unknown")
        or enabled not in ("0", "1", "unknown")
        or any(_OP_STATE.fullmatch(op) is None for op in ops)
        or any(state not in _GRANT_STATES for state in states)
        or (device != "unknown" and _DEVICE.fullmatch(device) is None)
        or vendor not in ("shelly", "meta", "other")
        or policy not in _HOME_POLICIES
        or (current != "unknown" and _SERVICE_COMPONENT.fullmatch(current) is None)
    ):
        return refused
    camera, microphone, secure_settings = states
    checks = (
        ("accessibility service", accessibility == "granted" and enabled == "1"),
        ("modify system settings", ops[0] == "allow"),
        ("display over other apps", ops[1] == "allow"),
        ("install packages", ops[2] == "allow"),
        ("camera", camera in ("granted", "unsupported")),
        ("microphone", microphone in ("granted", "unsupported")),
        ("secure settings", secure_settings in ("granted", "unsupported")),
    )
    return _PermissionGrant(
        missing=tuple(name for name, ok in checks if not ok),
        home=current in EQUIVALENT_HOME_COMPONENTS[launch_component],
        chooser=chooser if chooser in CHOOSER_OUTCOMES else None,
        vendor_setup=vendor in ("shelly", "meta") or device in META_PORTAL_DEVICES,
    )


def notifications_granted(repair: dict[str, str]) -> bool:
    """Only an explicit ``granted`` proves the notification permission."""
    return repair.get("notifications") == "granted"


def vendor_setup_warning(fields: dict[str, str]) -> str | None:
    """The shell route's warning for this vendor setup, or None when it took."""
    device = fields.get("device")
    converged = fields.get("converged")
    if device not in ("meta", "shelly", "other") or converged not in ("0", "1"):
        return UNREAD_SETUP_WARNING
    if device == "other" or converged == "1":
        return None
    return VendorSetup.warning if device == "meta" else ShellySetup.warning


def restore_from_fields(fields: dict[str, str]) -> str:
    """``restored``, ``unrestored`` or ``none``; anything else is unreadable."""
    result = fields.get("result")
    if result not in _RESTORE_RESULTS:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
    return result


def admin_from_fields(fields: dict[str, str]) -> tuple[bool, str]:
    """Whether the device admin read back active, and the HOME it reported."""
    return fields.get("admin") == "active", fields.get("home", "")

"""Existing panel shell programs carried by their authenticated live session."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from secrets import token_hex
from typing import Any

from adb_shell.adb_device_async import AdbDeviceAsync
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import adb_protocol as adb
from . import local_commands as typed
from .client import (
    _PACKAGE_NAME_PATTERN,
    HandBackRefusedError,
    HaPaneldClient,
    HaPaneldError,
    PanelAddress,
    UpdateApprovalRequiredError,
    check_vendor_brightness_restore,
)
from .transport_protocol import (
    CAPABILITY_LOCAL_ADB,
    CAPABILITY_LOCAL_ADB_COMMANDS,
    ERR_APPROVAL_PENDING,
    OUTCOME_APPLIED,
    OUTCOME_REFUSED,
)
from .transport_sessions import (
    PanelSession,
    async_get_sessions,
    async_manage_outcome,
    session_manages,
)

LOCAL_ADB_APPROVAL_SECONDS = 600.0
_LOGGER = logging.getLogger(__name__)

LOCAL_ADB_RECOVERY_SECONDS = 300
LOCAL_ADB_RECOVERY_OVERHEAD_SECONDS = 15
LOCAL_ADB_RECOVERY_RETRY_SECONDS = 15


def approval_pending(err: BaseException) -> bool:
    """Whether a local request failed because the panel asked for approval."""
    cause = err.__cause__
    return (
        isinstance(cause, HomeAssistantError)
        and cause.translation_key == ERR_APPROVAL_PENDING
    )


def _failure() -> adb.InstallAdbError:
    return adb.InstallAdbError(adb.InstallAdbErrorCode.TARGET_UNREACHABLE)


def session_for(hass: HomeAssistant, entry: ConfigEntry) -> PanelSession | None:
    """Select only this entry's current, authenticated local command carrier."""
    session = async_get_sessions(hass).get(entry.entry_id)
    if (
        hass.config_entries.async_get_entry(entry.entry_id) is not entry
        or session is None
        or session.did != entry.unique_id
        or not session_manages(session)
        or CAPABILITY_LOCAL_ADB not in session.capabilities
    ):
        return None
    return session


async def _request(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: PanelSession,
    op: str,
    args: dict[str, Any],
    wait_seconds: float,
    approval: bool = True,
) -> dict[str, Any]:
    if session_for(hass, entry) is not session:
        raise _failure()
    try:
        outcome = await async_manage_outcome(
            hass,
            session,
            op,
            wait_seconds,
            args=args,
            approval_seconds=LOCAL_ADB_APPROVAL_SECONDS
            if op in ("adb.shell", "adb.recover", "adb.cmd")
            else 0,
            wait_for_approval=approval,
        )
    except HomeAssistantError as err:
        raise _failure() from err
    if (
        op == "adb.ready"
        and args == {"include_build_id": True}
        and outcome.outcome == OUTCOME_REFUSED
        and outcome.code == "invalid_value"
    ):
        # Older apps reject any ready arguments. Only their explicit read-only
        # shape refusal permits a same-session retry without optional metadata.
        return await _request(hass, entry, session, op, {}, wait_seconds)
    if outcome.outcome != OUTCOME_APPLIED or not isinstance(outcome.result, dict):
        raise _failure()
    return outcome.result


async def async_shell(
    hass: HomeAssistant,
    entry: ConfigEntry,
    command: str,
    timeout_ms: int,
    *,
    session: PanelSession | None = None,
    approval: bool = True,
) -> bytes:
    """Execute once; an incomplete or ambiguous result is never retried.

    Without ``approval``, a panel that asks for on-screen approval fails the
    request at once instead of waiting for someone to answer it.
    """
    session = session or session_for(hass, entry)
    if (
        session is None
        or not command
        or len(command.encode()) > 65536
        or type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 180000
    ):
        raise _failure()
    result = await _request(
        hass,
        entry,
        session,
        "adb.shell",
        {"command": command, "timeout_ms": timeout_ms},
        timeout_ms / 1000 + 10,
        approval,
    )
    if (
        set(result) != {"stdout_b64", "exit_code", "timed_out", "truncated"}
        or type(result["exit_code"]) is not int
        or result["exit_code"] != 0
        or result["timed_out"] is not False
        or result["truncated"] is not False
        or not isinstance(result["stdout_b64"], str)
        or len(result["stdout_b64"]) > 43692
    ):
        raise _failure()
    try:
        body = base64.b64decode(result["stdout_b64"], validate=True)
    except (ValueError, binascii.Error) as err:
        raise _failure() from err
    if len(body) > 32768:
        raise _failure()
    return body


class LocalAdbDevice(AdbDeviceAsync):
    """The shell interface existing command parsers use, with no TCP fallback."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: PanelSession,
        approval: bool = True,
    ) -> None:
        self.hass, self.entry, self.session = hass, entry, session
        self.approval = approval

    async def streaming_shell(
        self,
        command: str,
        transport_timeout_s: float | None = None,
        read_timeout_s: float = 10,
        decode: bool = True,
    ) -> AsyncIterator[Any]:
        body = await async_shell(
            self.hass,
            self.entry,
            command,
            min(180000, max(1, int(read_timeout_s * 1000))),
            session=self.session,
            approval=self.approval,
        )
        yield body.decode() if decode else body

    async def close(self) -> None:
        """Closing an operation does not end the panel's shared session."""

    async def push(self, *args: Any, **kwargs: Any) -> None:
        """File streaming belongs to the original admitted installation route."""
        raise adb.InstallAdbError(adb.InstallAdbErrorCode.INVALID_REQUEST)


class LocalAdbCommands:
    """Typed helper commands on the session, approved exactly like a shell.

    Chosen from the session's capability, never from a refusal: ``expired``
    (the helper is being replaced) fails the call and changes nothing, and
    only ``unknown_command`` turns typed commands off for this session.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: PanelSession,
        approval: bool = True,
    ) -> None:
        self.hass, self.entry, self.session = hass, entry, session
        self.approval = approval

    async def run(self, verb: str, args: tuple[str, ...] = ()) -> dict[str, str]:
        """Run one command; return its bounded fields or raise its refusal."""
        if not typed.valid_args(verb, args):
            raise adb.InstallAdbError(adb.InstallAdbErrorCode.INVALID_REQUEST)
        session = self.session
        if (
            session_for(self.hass, self.entry) is not session
            or CAPABILITY_LOCAL_ADB_COMMANDS not in session.capabilities
            or session.local_adb_commands_disabled
        ):
            raise _failure()
        try:
            outcome = await async_manage_outcome(
                self.hass,
                session,
                "adb.cmd",
                typed.WAIT_SECONDS[verb],
                args={"verb": verb, "args": list(args)},
                approval_seconds=LOCAL_ADB_APPROVAL_SECONDS,
                wait_for_approval=self.approval,
            )
        except HomeAssistantError as err:
            raise _failure() from err
        if outcome.outcome == OUTCOME_REFUSED:
            if outcome.code == typed.REFUSAL_UNKNOWN_COMMAND:
                # A backstop only: the capability promised the command.
                session.local_adb_commands_disabled = True
            result = outcome.result if isinstance(outcome.result, dict) else {}
            helper = result.get("code")
            raise typed.LocalCommandRefused(
                helper
                if isinstance(helper, str) and helper
                else str(outcome.code or "refused")
            )
        if outcome.outcome != OUTCOME_APPLIED:
            result = outcome.result if isinstance(outcome.result, dict) else {}
            if outcome.code == "busy" or result.get("code") == "busy":
                # A held helper lock (the app answers failed/busy) clears by
                # itself; callers defer, as the shell's failed flock did.
                raise typed.LocalCommandRefused("busy")
            raise _failure()
        return typed.bounded_fields(outcome.result)


def commands_for(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: PanelSession,
    approval: bool = True,
) -> LocalAdbCommands | None:
    """Typed commands when the session was granted them and has not lost them."""
    if (
        CAPABILITY_LOCAL_ADB_COMMANDS not in session.capabilities
        or session.local_adb_commands_disabled
    ):
        return None
    return LocalAdbCommands(hass, entry, session, approval)


def _ready(result: dict[str, Any]) -> bool:
    required = {"ready", "managed", "recovery_hold"}
    optional = {"security_unverified", "build_id"}
    if not required <= result.keys() or result.keys() - required - optional:
        raise _failure()
    if any(type(result[key]) is not bool for key in result if key != "build_id"):
        raise _failure()
    if "build_id" in result:
        build = result["build_id"]
        if not (
            (isinstance(build, str) and re.fullmatch(r"[0-9a-f]{64}", build))
            or (build is None and result["ready"] is False)
        ):
            raise _failure()
    return result["ready"] is True


async def _bootstrap(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: PanelSession,
    address: PanelAddress,
    prepared: tuple[str, str, str] | None = None,
) -> str | None:
    """Keep the original admitted network route until independent recovery works."""
    from .adb_credentials import AdbCredentialError, async_get_durable_adb_credential
    from .install_network import (
        InstallNetworkError,
        async_pin_entry_target,
        async_revalidate_install_target,
    )
    from .local_adb_bootstrap import async_bootstrap, prepared_asset
    from .provisioning import InstallTargetState, async_probe_install_target

    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    snapshot = getattr(coordinator, "data", None)
    if snapshot is None or getattr(coordinator, "identity_mismatch", False):
        return None
    try:
        credential = await async_get_durable_adb_credential(hass)
        pinned = await async_pin_entry_target(
            hass, address, snapshot.health, entry.unique_id
        )
        if pinned is None:
            return None
        probe = await async_probe_install_target(pinned.pinned, credential.signer)
        target = probe.adb_target(pinned.pinned)
        if target is None or probe.state is not InstallTargetState.INSTALLED:
            return None
        require_recovery_identity(hass, entry, target)
        if prepared is None:
            result = await _request(hass, entry, session, "adb.prepare", {}, 30)
            prepared = prepared_asset(result)
        path, sha, build = prepared
        await async_revalidate_install_target(hass, pinned)
        launched = await async_bootstrap(
            target,
            credential.signer,
            path,
            sha,
            before_mutation=lambda: session_for(hass, entry) is session,
        )
        return build if launched else None
    except AdbCredentialError, InstallNetworkError:
        return None


async def _async_start_recovery(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: PanelSession,
    address: PanelAddress,
    prepared: tuple[str, str, str] | None,
    legacy: bool,
) -> bool:
    """Bootstrap over network ADB, then require the launched build to answer."""
    build = await _bootstrap(hass, entry, session, address, prepared)
    if build is None:
        return False
    for _attempt in range(10):
        ready = await _request(
            hass, entry, session, "adb.ready", {"include_build_id": True}, 10
        )
        if _ready(ready) and ready.get("build_id", build if legacy else None) == build:
            return True
        await asyncio.sleep(0.2)
    raise _failure()


async def async_ensure_recovery(
    hass: HomeAssistant, entry: ConfigEntry, address: PanelAddress
) -> bool:
    """Start recovery again on a live channel whose daemon is not running.

    Only ``adb.ready``, ``adb.prepare`` and the pinned network route are used,
    so no on-panel approval is ever requested. A daemon that answers ready is
    left alone. False, or an error, means recovery is still not proved.
    """
    session = session_for(hass, entry)
    if session is None:
        return False
    ready = await _request(
        hass, entry, session, "adb.ready", {"include_build_id": True}, 10
    )
    return _ready(ready) or await _async_start_recovery(
        hass, entry, session, address, None, "build_id" not in ready
    )


async def async_target(
    hass: HomeAssistant,
    entry: ConfigEntry,
    address: PanelAddress,
    *,
    approval: bool = True,
) -> adb.AdbInstallTarget | None:
    """Prove actual readiness and physical identity before selecting local I/O.

    ``approval`` applies to every shell the returned target carries.
    """
    session = session_for(hass, entry)
    if session is None:
        return None
    from .local_adb_bootstrap import prepared_asset

    ready = await _request(
        hass, entry, session, "adb.ready", {"include_build_id": True}, 10
    )
    is_ready = _ready(ready)
    legacy = "build_id" not in ready
    prepared = None
    if is_ready and not legacy:
        prepared = prepared_asset(
            await _request(hass, entry, session, "adb.prepare", {}, 30)
        )
        if ready["build_id"] != prepared[2]:
            # Explicitly reopen the original route before replacing its recovery
            # owner. The root bootstrap still pins and revalidates this panel.
            await async_recover(hass, entry, approval=approval)
            is_ready = False
    if not is_ready and not await _async_start_recovery(
        hass, entry, session, address, prepared, legacy
    ):
        return None
    device = LocalAdbDevice(hass, entry, session, approval)
    commands = commands_for(hass, entry, session, approval)
    if commands is not None:
        target = await _typed_identity(commands, address)
    else:
        target = await _shell_identity(hass, entry, session, address, approval)
    identity = (target.serial, target.model, target.primary_abi, target.android_sdk)
    if (
        session.local_adb_identity is not None
        and session.local_adb_identity != identity
    ):
        raise adb.InstallAdbError(adb.InstallAdbErrorCode.TARGET_CHANGED)
    session.local_adb_identity = identity
    return replace(target, local_shell=device, local_commands=commands)


async def _typed_identity(
    commands: LocalAdbCommands, address: PanelAddress
) -> adb.AdbInstallTarget:
    """One typed read gives the identity and posture the two shells read."""
    fields = await commands.run(typed.VERB_IDENTITY)
    try:
        observed = typed.identity_from_fields(fields)
        typed.root_mode_from_fields(fields)
    except adb._MalformedAdbResponse as err:
        raise _failure() from err
    return adb.AdbInstallTarget(
        address,
        observed.serial,
        observed.model,
        observed.primary_abi,
        observed.android_sdk,
    )


async def _shell_identity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    session: PanelSession,
    address: PanelAddress,
    approval: bool,
) -> adb.AdbInstallTarget:
    nonce = token_hex(16)
    try:
        observed = adb._parse_identity(
            await async_shell(
                hass,
                entry,
                "; ".join(adb._framed_value_commands("ID", nonce)),
                10000,
                session=session,
                approval=approval,
            ),
            nonce,
            "ID",
        )
        target = adb.AdbInstallTarget(
            address,
            observed.serial,
            observed.model,
            observed.primary_abi,
            observed.android_sdk,
        )
        nonce = token_hex(16)
        adb._parse_identity_root(
            await async_shell(
                hass,
                entry,
                adb._identity_root_command(nonce),
                10000,
                session=session,
                approval=approval,
            ),
            nonce,
            target,
        )
    except adb._MalformedAdbResponse as err:
        raise _failure() from err
    return target


async def async_recover(
    hass: HomeAssistant, entry: ConfigEntry, *, approval: bool = True
) -> None:
    """Request independent network recovery; callers still prove its identity."""
    session = session_for(hass, entry)
    if session is None:
        raise _failure()
    result = await _request(hass, entry, session, "adb.recover", {}, 30, approval)
    if set(result) != {"enabled"} or result["enabled"] is not True:
        raise _failure()


async def async_hand_back_home(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Request the app's existing HOME handback through its current session."""
    session = session_for(hass, entry)
    if session is None:
        raise HaPaneldError
    try:
        outcome = await async_manage_outcome(
            hass,
            session,
            "hand_back_home",
            30,
            args={},
            approval_seconds=LOCAL_ADB_APPROVAL_SECONDS,
        )
    except HomeAssistantError as err:
        if err.translation_key == "approval_pending":
            raise UpdateApprovalRequiredError from err
        raise HaPaneldError from err
    result = outcome.result if isinstance(outcome.result, dict) else {}
    if outcome.outcome != OUTCOME_APPLIED:
        raise HandBackRefusedError.from_refusal(
            result.get("error"), result.get("original_home")
        )
    check_vendor_brightness_restore(result)
    handed = result.get("home_handed_to")
    if not isinstance(handed, str) or _PACKAGE_NAME_PATTERN.fullmatch(handed) is None:
        raise HandBackRefusedError.from_not_handed(result)


def installation_finished(hass: HomeAssistant, entry_id: str) -> None:
    """Re-negotiate a deferred grant after the existing finalizer consumes it."""
    sessions = async_get_sessions(hass)
    session = sessions.get(entry_id)
    if session is not None and session.local_adb_deferred:
        sessions.close(session, "binding_changed")


def recovery_wait(hass: HomeAssistant, entry: ConfigEntry) -> float:
    """Allow the independent lease to expire for a managed local channel."""
    session = async_get_sessions(hass).latest(entry.entry_id)
    if (
        session is None
        or session.did != entry.unique_id
        or (
            session.local_adb_identity is None
            and (
                not session_manages(session)
                or CAPABILITY_LOCAL_ADB not in session.capabilities
            )
        )
    ):
        return 0
    return LOCAL_ADB_RECOVERY_SECONDS + LOCAL_ADB_RECOVERY_OVERHEAD_SECONDS


@dataclass(frozen=True, slots=True)
class HeldLease:
    """The panel app answers without a session and keeps ADB off for us."""

    #: Seconds until the owner's own Network ADB switch works; None: it does not.
    switch_wait_s: int | None

    def reason(self, prefix: str = "") -> tuple[str, dict[str, str]]:
        """Name the text for this lease and the placeholders it needs."""
        if self.switch_wait_s is None:
            return f"{prefix}adb_lease", {}
        if self.switch_wait_s == 0:
            return f"{prefix}adb_lease_switch_now", {}
        return f"{prefix}adb_lease_switch", {
            "minutes": str(math.ceil(self.switch_wait_s / 60))
        }


async def async_held_lease(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    address: PanelAddress,
    purpose: str,
    *,
    reported_only: bool = False,
) -> HeldLease | None:
    """Whether ADB is closed by our own lease that no lapse will end.

    Only without a session: the app answering on its own port is still
    renewing, so waiting for the lease to lapse cannot help. An app that does
    not answer, or reports it is not renewing, lets the lease lapse by itself.
    """
    if entry is not None and session_for(hass, entry) is not None:
        return None
    try:
        status = await HaPaneldClient(
            async_get_clientsession(hass), address
        ).async_get_status()
    except HaPaneldError, OSError, TimeoutError:
        return None
    if status.network_adb_managed is False or (
        reported_only and status.network_adb_managed is not True
    ):
        return None
    if status.network_adb_managed is None and (
        entry is None or recovery_wait(hass, entry) == 0
    ):
        return None
    _LOGGER.warning(
        "%s on the panel at %s needs its Android Debug Bridge, which the panel "
        "app keeps off while this Home Assistant manages it; the panel has no "
        "session with Home Assistant, and its app answered on port %s (%s)",
        purpose,
        address.host,
        address.port,
        "reports managed"
        if status.network_adb_managed
        else "last session granted local ADB",
    )
    return HeldLease(status.network_adb_switch_wait_s)


def require_recovery_identity(
    hass: HomeAssistant, entry: ConfigEntry, target: adb.AdbInstallTarget
) -> None:
    """A reopened address cannot replace the panel the local channel proved."""
    session = async_get_sessions(hass).latest(entry.entry_id)
    if (
        session is not None
        and session.did == entry.unique_id
        and session.local_adb_identity is not None
        and session.local_adb_identity
        != (
            target.serial,
            target.model,
            target.primary_abi,
            target.android_sdk,
        )
    ):
        raise adb.InstallAdbError(adb.InstallAdbErrorCode.TARGET_CHANGED)

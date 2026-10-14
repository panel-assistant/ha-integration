"""One authorized HOME navigation after Shelly stops its dashboard app."""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
from collections.abc import Coroutine
from dataclasses import dataclass
from secrets import token_hex
from typing import TYPE_CHECKING, Any
from uuid import UUID

from homeassistant.const import CONF_ADDRESS

from . import adb_protocol as adb
from .adb_credentials import AdbCredentialError, async_get_durable_adb_credential
from .app_identity import EQUIVALENT_HOME_COMPONENTS, LAUNCH_COMPONENTS
from .client import CannotConnectError, HaPaneldError
from .home_chooser import HOME_QUERY
from .install_adb import _async_target_section
from .install_jobs import async_get_install_job_manager
from .install_network import (
    InstallNetworkError,
    PinnedPanelTarget,
    async_pin_entry_target,
    async_revalidate_install_target,
)
from .install_receipt import InstallJobError
from .provisioning import InstallTargetState, async_probe_install_target
from .transport import async_get_sessions

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
    from .transport import PanelSession

_LOGGER = logging.getLogger(__name__)
_VENDOR = "cloud.shelly.stargate"
_GRACE_SECONDS = 1.0
_RECOVERY_SECONDS = 8.0
_STOP_AGE_SECONDS = 10.0
_LOG_LINE = re.compile(
    r"^\s*(-?\d+\.\d+)\s+\d+\s+\d+\s+[VDIWEF]\s+ActivityManager:\s+(.*)$"
)


@dataclass(frozen=True, slots=True)
class _Context:
    entry: ConfigEntry
    did: str
    address: str
    session: PanelSession
    package: str


@dataclass(frozen=True, slots=True)
class _Witness:
    context: _Context
    target: adb.AdbInstallTarget
    credential: str
    boot: str
    uptime: float
    home: str
    vendor_pid: int


def _observation(nonce: str, package: str, *, logs: bool = False) -> str:
    # All package/component inputs come from the accepted identity catalogue.
    commands = [
        f"echo HAPANELD_RECOVERY_BEGIN:{nonce}",
        "cat /proc/sys/kernel/random/boot_id || exit 1",
        "cut -d' ' -f1 /proc/uptime || exit 1",
        HOME_QUERY,
        f'hp_pid=$(pidof {_VENDOR}); echo "${{hp_pid:-0}}"',
        f"dumpsys package {package} | grep '^ *User 0:.* installed=true'",
    ]
    if logs:
        # Retain all process reassignments, including other packages, to reject
        # recycled actor PIDs. Missing history at the arm cursor refuses recovery.
        commands.append(
            "logcat -d -v monotonic -s ActivityManager:I "
            "| grep -E 'Start proc |Force stopping ' | tail -n 120"
        )
        commands.append("cut -d' ' -f1 /proc/uptime || exit 1")
    return "; ".join([*commands, f"echo HAPANELD_RECOVERY_END:{nonce}:$?"])


def _read(lines: list[str]) -> tuple[str, float, str, int, bool]:
    if len(lines) < 5 or str(UUID(lines[0])) != lines[0]:
        raise ValueError("incomplete vendor observation")
    uptime = float(lines[1])
    if not re.fullmatch(r"\d+\.\d+", lines[1]) or uptime <= 0:
        raise ValueError("invalid uptime")
    pid = int(lines[3])
    if pid < 0 or not re.fullmatch(r"\d+", lines[3]):
        raise ValueError("ambiguous vendor process")
    if not re.match(r"^\s*User 0:.*\binstalled=true\b", lines[4]):
        raise ValueError("missing installed package")
    stopped = re.findall(r"\bstopped=(true|false)\b", lines[4])
    if len(stopped) != 1:
        raise ValueError("unknown stopped state")
    return lines[0], uptime, lines[2], pid, stopped[0] == "true"


def _vendor_stop(lines: list[str], witness: _Witness, now: float) -> str | None:
    actors = {witness.vendor_pid: _VENDOR}
    previous = witness.uptime
    first = None
    latest: tuple[float, str | None, str] | None = None
    for line in lines:
        match = _LOG_LINE.fullmatch(line)
        if match is None:
            return None
        when, message = float(match[1]), match[2]
        if first is None:
            first = when
        if when <= witness.uptime:
            continue
        if when < previous:
            return None
        previous = when
        if proc := re.match(r"Start proc (\d+):([^/\s]+)/", message):
            actors[int(proc[1])] = proc[2]
        if message.startswith(f"Force stopping {witness.context.package} "):
            actor = re.fullmatch(
                rf"Force stopping {re.escape(witness.context.package)} "
                r"appid=\d+ user=0: from pid (\d+)",
                message,
            )
            latest = (when, actors.get(int(actor[1])) if actor else None, message)
    if (
        first is not None
        and first <= witness.uptime
        and latest is not None
        and latest[1] == _VENDOR
        and 0 <= now - latest[0] <= _STOP_AGE_SECONDS
    ):
        return latest[2]
    return None


class VendorRecovery:
    """The coordinator owns one witness and at most one bounded task."""

    def __init__(self, coordinator: HaPaneldDataUpdateCoordinator) -> None:
        self._coordinator = coordinator
        self._task: asyncio.Task[None] | None = None
        self._key: tuple[str, str, str, str] | None = None
        self._witness: _Witness | None = None
        self._closed = False

    def _current(self, context: _Context, *, connected: bool) -> bool:
        coordinator = self._coordinator
        entry = coordinator.hass.config_entries.async_get_entry(context.entry.entry_id)
        sessions = async_get_sessions(coordinator.hass)
        session = sessions.get(context.entry.entry_id)
        return (
            not self._closed
            and not coordinator.hass.is_stopping
            and entry is context.entry
            and entry.unique_id == context.did
            and entry.data[CONF_ADDRESS] == context.address
            and coordinator.client.address.stored_value == context.address
            and not coordinator.identity_mismatch
            and (session is context.session if connected else session is None)
            and sessions.latest(context.entry.entry_id) is context.session
        )

    def observe(self, snapshot: PanelSnapshot | None) -> None:
        """Arm once for a healthy, identity-accepted Shelly session generation."""
        coordinator = self._coordinator
        if snapshot is None or snapshot.status is None or coordinator._entry_id is None:
            return
        device = snapshot.status.panel_assistant_device
        entry = coordinator.hass.config_entries.async_get_entry(coordinator._entry_id)
        session = async_get_sessions(coordinator.hass).get(coordinator._entry_id)
        health = snapshot.health
        if (
            device is None
            or device.manufacturer != "Shelly"
            or device.model != "Wall Display X2i"
            or entry is None
            or session is None
            or not health.discovery_id
            or health.discovery_id != entry.unique_id
            or session.did != entry.unique_id
            or health.package is None
            or health.package not in LAUNCH_COMPONENTS
        ):
            return
        context = _Context(
            entry,
            health.discovery_id,
            entry.data[CONF_ADDRESS],
            session,
            health.package,
        )
        key = (context.did, context.address, session.token, context.package)
        if key == self._key or not self._current(context, connected=True):
            return
        self._key = key
        self._witness = None
        self._replace(self._arm(context, snapshot))

    def changed(self) -> None:
        """Use a completed witness once, only after its actual session ends."""
        witness = self._witness
        if witness is not None and self._current(witness.context, connected=False):
            self._witness = None
            self._replace(self._recover(witness))
        elif self._coordinator.connected:
            self.observe(self._coordinator.data)

    def _replace(self, work: Coroutine[Any, Any, None]) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = self._coordinator.hass.async_create_task(
            work, "Shelly dashboard recovery"
        )

    def close(self) -> None:
        """Retire the entry before its session unload signal can trigger recovery."""
        self._closed = True
        self._witness = None
        if self._task is not None:
            self._task.cancel()

    async def _arm(self, context: _Context, snapshot: PanelSnapshot) -> None:
        hass = self._coordinator.hass
        try:
            async with asyncio.timeout(15):
                credential = await async_get_durable_adb_credential(hass)
                pinned = await async_pin_entry_target(
                    hass, self._coordinator.client.address, snapshot.health, context.did
                )
                if pinned is None or not self._current(context, connected=True):
                    return
                probe = await async_probe_install_target(
                    pinned.pinned, credential.signer
                )
                target = probe.adb_target(pinned.pinned)
                if probe.state is not InstallTargetState.INSTALLED or target is None:
                    return
                lines = await _async_target_section(
                    target,
                    credential.signer,
                    lambda nonce: _observation(nonce, context.package),
                    "RECOVERY",
                )
                boot, uptime, home, vendor_pid, stopped = _read(lines)
                homes = {
                    home
                    for launch in LAUNCH_COMPONENTS[context.package]
                    for home in EQUIVALENT_HOME_COMPONENTS[launch]
                }
                await async_revalidate_install_target(hass, pinned)
                # Bind physical ADB facts to a fresh HTTP identity at the same pin.
                proven = await async_pin_entry_target(
                    hass, self._coordinator.client.address, snapshot.health, context.did
                )
                if (
                    not stopped
                    and vendor_pid > 0
                    and home in homes
                    and proven == pinned
                    and self._current(context, connected=True)
                ):
                    self._witness = _Witness(
                        context,
                        target,
                        credential.generation_id,
                        boot,
                        uptime,
                        home,
                        vendor_pid,
                    )
        except (
            AdbCredentialError,
            InstallNetworkError,
            HaPaneldError,
            adb.InstallAdbError,
            ValueError,
            OSError,
            TimeoutError,
        ):
            _LOGGER.debug("Shelly dashboard recovery could not be armed")

    async def _recover(self, witness: _Witness) -> None:
        from .panel_move import claim_panel_operation, release_panel_operation

        coordinator = self._coordinator
        hass, context = coordinator.hass, witness.context
        entry_id = context.entry.entry_id
        claimed = False
        device = None
        try:
            async with asyncio.timeout(_RECOVERY_SECONDS):
                await asyncio.sleep(_GRACE_SECONDS)
                if not self._allowed(context) or not claim_panel_operation(
                    hass, entry_id
                ):
                    return
                claimed = True
                try:
                    await coordinator.client.async_get_health()
                    return  # Any answering app needs no navigation recovery.
                except CannotConnectError:
                    pass
                manager = await async_get_install_job_manager(hass)
                if await manager.async_find_active(
                    context.address, witness.target.address.stored_value
                ):
                    return
                credential = await async_get_durable_adb_credential(hass)
                if credential.generation_id != witness.credential or not self._allowed(
                    context
                ):
                    return
                await async_revalidate_install_target(
                    hass,
                    PinnedPanelTarget(
                        coordinator.client.address, witness.target.address
                    ),
                )
                if not self._allowed(context):
                    return
                device = await adb._async_connect(
                    witness.target, credential.signer, authorize=False
                )
                nonce = token_hex(16)
                adb._parse_identity_root(
                    await adb._async_shell(
                        device, adb._identity_root_command(nonce), read_timeout=2
                    ),
                    nonce,
                    witness.target,
                )
                nonce = token_hex(16)
                body = await adb._async_shell(
                    device,
                    _observation(nonce, context.package, logs=True),
                    read_timeout=2,
                )
                lines, status = adb._parse_single_section(
                    body, prefix="RECOVERY", nonce=nonce
                )
                if len(lines) < 6:
                    return
                # Monotonic log rendering can move slightly between logcat
                # invocations. Sample uptime after the scan, and compare the
                # final stop's message rather than its rendered timestamp.
                boot, now, home, _pid, stopped = _read(
                    [lines[0], lines[-1], *lines[2:5]]
                )
                stop = _vendor_stop(lines[5:-1], witness, now)
                if (
                    status != 0
                    or boot != witness.boot
                    or home != witness.home
                    or not stopped
                    or stop is None
                    or not self._allowed(context)
                ):
                    return
                fresh = await async_get_durable_adb_credential(hass)
                if (
                    fresh.generation_id != witness.credential
                    or await manager.async_find_active(
                        context.address, witness.target.address.stored_value
                    )
                    or not self._allowed(context)
                ):
                    return
                nonce = token_hex(16)
                # Repeat the mutable navigation guards in the shell that sends
                # HOME; never repair the default or bypass an explicit Admin HOME.
                command = "; ".join(
                    [
                        f"echo HAPANELD_LAUNCH_BEGIN:{nonce}",
                        f"if [ \"$({HOME_QUERY})\" = '{witness.home}' ] && "
                        f"dumpsys package {context.package} "
                        "| grep '^ *User 0:.* installed=true' "
                        "| grep -q ' stopped=true' && "
                        '[ "$(logcat -d -v monotonic -s ActivityManager:I '
                        f"| grep -F 'Force stopping {context.package} ' "
                        "| tail -n 1 | sed 's/^.*ActivityManager: //')\" = "
                        f"{shlex.quote(stop)} ]; "
                        "then input keyevent 3; else false; fi",
                        f"echo HAPANELD_LAUNCH_END:{nonce}:$?",
                    ]
                )
                outcome = adb._parse_launch_outcome(
                    await adb._async_shell(device, command, read_timeout=2), nonce
                )
                if outcome is adb.LaunchOutcome.STARTED:
                    _LOGGER.info(
                        "Sent HOME to %s after Shelly stopped its app",
                        context.entry.title,
                    )
                    await coordinator.async_request_refresh()
        except (
            AdbCredentialError,
            InstallJobError,
            InstallNetworkError,
            HaPaneldError,
            adb.InstallAdbError,
            adb._MalformedAdbResponse,
            adb._UnsafeAdbPacket,
            *adb._ADB_EXCEPTIONS,
            ValueError,
            TimeoutError,
        ):
            _LOGGER.debug("Shelly dashboard recovery was not completed")
        finally:
            try:
                await adb._async_close(device)
            finally:
                if claimed:
                    release_panel_operation(hass, entry_id)

    def _allowed(self, context: _Context) -> bool:
        runtime = getattr(context.entry, "runtime_data", None)
        update = getattr(getattr(runtime, "update_coordinator", None), "data", None)
        operation = getattr(update, "operation", None)
        return (
            self._current(context, connected=False)
            and self._coordinator.restart_notice is None
            and not (operation is not None and operation.running)
        )

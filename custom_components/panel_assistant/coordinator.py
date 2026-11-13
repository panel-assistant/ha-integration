"""Data coordinator for the ha-paneld integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from secrets import token_hex
from time import monotonic
from typing import Any

from adb_shell.adb_device_async import AdbDeviceAsync
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .address import (
    ISSUE_PANEL_ADDRESS_UNREACHABLE,
    ISSUE_PANEL_ADDRESS_UNVERIFIED,
    async_delete_address_issue,
    async_probe_addresses,
    async_raise_address_issue,
    session_candidates,
)
from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    PanelHealth,
    parse_health_response,
)
from .const import (
    CONF_DEVICE_SETUP,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_SETUP_ADOPT,
    DOMAIN,
    MAX_STATUS_RESPONSE_BYTES,
)
from .feed_coordinator import async_get_feed_coordinator
from .identity import accept_health, is_installation, reinstalled
from .repairs_entity_hold import async_reconcile_entity_hold_issue
from .repairs_permission import (
    async_reconcile_permission_issue,
    turned_off_reporter,
)
from .repairs_proximity import async_reconcile_proximity_issue
from .repairs_root import async_reconcile_root_issue
from .repairs_webview import async_reconcile_webview_issue
from .saved_account import SavedAccount
from .session_loss import SessionLossLog
from .status import PanelStatus, parse_status_response
from .transport_protocol import ERR_PANEL_UNAVAILABLE, MANAGE_SNAPSHOT
from .transport_sessions import (
    PanelSession,
    RestartNotice,
    async_get_sessions,
    async_manage,
    session_manages,
    signal_session_changed,
)
from .update_policy import prereleases_allowed

_LOGGER = logging.getLogger(__name__)
# Longer than an on-panel approval lives (600 s), so prompts never stack.
_ADMIN_APPROVAL_BACKOFF = 3600.0
_ADMIN_APPROVAL_BACKOFF_MAX = 86400.0
# Refused senders are hints, not sessions. Repeated hellos replace only their
# own hint, and a bound account cannot grow an unreachable entry's memory forever.
_MAX_REFUSED_HELLOS = 16
# How long a panel may answer polls without a session before it is reported as
# not signed in: a fresh install signs in within this, and a restart reconnects.
# It also applies again from the moment a once-connected panel's session ends:
# twice the app's reconnect backoff ceiling, so a panel whose path to Home
# Assistant works has always reconnected before it.
NOT_SIGNED_IN_GRACE = timedelta(minutes=2)
# A reported port the stored endpoint does not use is tried once per session,
# then this often: a forwarded port keeps answering while those never do.
_PORT_PROBE_INTERVAL = 3600.0


def _webview_done(status: PanelStatus) -> bool:
    """The panel reports no WebView install running or left to finish.

    A panel with no candidate (or none it can install) never reports `current`,
    so `current` alone would keep an interrupted update's Repair forever.
    """
    webview = status.webview
    return webview is not None and webview.settled


async def _async_admin_claim(
    device: AdbDeviceAsync, target: Any, package: str
) -> tuple[bool, str]:
    """Claim the device admin if absent; return its readback and the HOME seen.

    Only a successful, matching readback proves the admin, on either route.
    """
    from . import adb_protocol as adb
    from . import local_commands as typed
    from .app_identity import DEVICE_ADMIN_RECEIVER
    from .home_chooser import HOME_QUERY
    from .install_adb import _async_typed

    if target.local_commands is not None:
        return typed.admin_from_fields(await _async_typed(target, typed.VERB_ADMIN))
    listed = f"{package}/{DEVICE_ADMIN_RECEIVER.removeprefix(package)}:"

    def command(nonce: str) -> str:
        return "; ".join(
            (
                f"echo HAPANELD_ADMIN_BEGIN:{nonce}",
                adb.device_admin_claim_command(package),
                # Only a successful, matching readback proves admin.
                "policy=$(dumpsys device_policy 2>/dev/null); result=$?",
                f'case "$result:$policy" in 0:*"{listed}"*) echo active ;; '
                "*) echo unknown ;; esac",
                f'echo "home:$({HOME_QUERY})"',
                f"echo HAPANELD_ADMIN_END:{nonce}:0",
            )
        )

    nonce = token_hex(16)
    lines, status = adb._parse_single_section(
        await adb._async_shell(
            device, command(nonce), read_timeout=adb._READ_TIMEOUT_SECONDS
        ),
        prefix="ADMIN",
        nonce=nonce,
    )
    if status != 0 or len(lines) != 2:
        return False, ""
    return lines[0] == "active", lines[1].removeprefix("home:")


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    """Cached health authority and optional sanitized status diagnostics."""

    health: PanelHealth
    status: PanelStatus | None
    status_error: str | None


class HaPaneldDataUpdateCoordinator(DataUpdateCoordinator[PanelSnapshot]):
    """Read the panel's health and status, over its session where it can.

    A panel whose session grants management answers on that session, so Home
    Assistant needs no route back to it. Any other panel is polled at its
    stored address over HTTP. `last_update_success` says the latest snapshot
    was read, by either carrier; `reachable` says the stored address answered,
    which is what the operations still carried over HTTP (updates, backups,
    the camera) need. Whether the panel is available is `available`, which
    also counts the panel's own session, since a panel talking to Home
    Assistant is connected whatever the poll says. A failed poll while the
    panel is connected is repaired from the session where it can be, and
    reported where it cannot; it never takes the panel unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: HaPaneldClient,
        entry_id: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN} {entry.title}" if entry is not None else DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._entry_id = entry_id
        self.identity_mismatch = False
        # A Panel Assistant-driven update restarts the app on purpose, so its
        # failed polls are not reported as errors.
        self.expected_restart = False
        self._admin_key: tuple[str, str, int | None, str] | None = None
        self._admin_done = False
        self._admin_retry_at = 0.0
        self._admin_backoff = 0.0
        # The live session whose ADB recovery daemon was last proved running.
        self._recovery_session: PanelSession | None = None
        # Whether the finished pass saw root over ADB, which recovery needs.
        # Each pass sets it before it can finish, or the entry's device setup
        # record restores it, so a key change needs no reset.
        self._admin_root = False
        # True until the first poll answers otherwise, as `last_update_success` starts.
        self.reachable = True
        # Where the panel last said it was in an accepted session:
        # (remote, reported, listener port). Kept after the session ends, so a
        # panel that moved while disconnected is still looked for there.
        self._last_hello: tuple[str | None, tuple[str, ...], int | None] | None = None
        self._refused_hellos: dict[str, tuple[str | None, tuple[str, ...]]] = {}
        from .vendor_recovery import VendorRecovery

        self._vendor_recovery = VendorRecovery(self)
        self._port_probe: tuple[PanelSession, float] | None = None
        # Since setup, or since the panel's last session ended; None while
        # it is connected.
        self._no_session_since: datetime | None = dt_util.utcnow()
        self._saved_account = SavedAccount(hass, lambda: self._no_session_since is None)

    @property
    def connected(self) -> bool:
        """Return whether the panel holds an open session with Home Assistant."""
        return self._session() is not None

    @property
    def available(self) -> bool:
        """Return whether the panel is reachable outbound or connected inbound."""
        return self.reachable or self.connected

    @property
    def not_signed_in(self) -> bool:
        """Return whether a panel that has not connected holds no account here."""
        return self.unreached_ha_url is None and self._awaiting_session(True)

    @property
    def unreached_ha_url(self) -> str | None:
        """Return the address a signed-in panel that never connected is trying."""
        return self._saved_account.trying if self._awaiting_session(True) else None

    def _awaiting_session(self, asked: bool = False) -> bool:
        """Return whether the panel answers polls but has not connected for a while.

        Every entity beyond the static ones, and the sidebar page, need the
        panel's session, so a panel that answers health without one is not
        online. An app too old to connect, or one waiting on a user binding,
        has its own Repair and is left to it.
        """
        from .repairs_upgrade import runs_old_app, speaks_this_protocol
        from .transport import _panel_did
        from .transport_binding import async_binding_request

        since = self._no_session_since
        entry = self._entry()
        if (
            entry is None
            or since is None
            or self.connected
            or not self.reachable
            or self.data is None
            or (asked and not self._saved_account.read)
            or dt_util.utcnow() - since < NOT_SIGNED_IN_GRACE
        ):
            return False
        health = self.data.health
        return (
            speaks_this_protocol(health)
            and not runs_old_app(health)
            and not async_binding_request(self.hass, _panel_did(entry))
        )

    @property
    def restart_notice(self) -> RestartNotice | None:
        """Return the active, bounded restart announcement for this entry."""
        return (
            async_get_sessions(self.hass).restart_notice(self._entry_id)
            if self._entry_id is not None
            else None
        )

    @property
    def app_build(self) -> tuple[str, int] | None:
        """Return the app version and build number the open session declared."""
        session = self._session()
        if session is None:
            return None
        return session.app_version, session.app_version_code

    def _session(self) -> PanelSession | None:
        if self._entry_id is None:
            return None
        return async_get_sessions(self.hass).get(self._entry_id)

    def _panel_name(self) -> str:
        from .device import panel_display_name

        entry = self._entry()
        return panel_display_name(self.hass, entry) if entry else str(self._entry_id)

    def _entry(self) -> ConfigEntry | None:
        if self._entry_id is None:
            return None
        return self.hass.config_entries.async_get_entry(self._entry_id)

    @callback
    def async_follow_session(self) -> Callable[[], None]:
        """Re-answer availability when the panel's session opens or closes.

        A session that opens while the stored address is failing is the moment
        the address can be repaired, so a poll is asked for at once rather than
        at the next interval.
        """
        assert self._entry_id is not None
        entry_id = self._entry_id
        loss = SessionLossLog(
            self.hass, entry_id, self._panel_name, lambda: self.expected_restart
        )

        @callback
        def _remember() -> None:
            if (session := self._session()) is None:
                # A session that drops and returns is a recovered fault, logged
                # and never a Repair; one that stays away for the whole grace
                # is reported as a panel that has not connected.
                if self._no_session_since is None:
                    self._no_session_since = dt_util.utcnow()
                return
            self._no_session_since = None
            self._last_hello = (
                session.remote,
                session.addresses,
                session.http_port,
            )
            self._refused_hellos.clear()

        @callback
        def _changed() -> None:
            _remember()
            loss.changed()
            self.async_update_listeners()
            self._vendor_recovery.changed()
            session = self._session()
            if session is None:
                # The address report says the panel is connected. A panel
                # switched off, or whose session simply ended, is unavailable,
                # which needs no Repair.
                async_delete_address_issue(self.hass, entry_id)
            if session is not None and (
                not self.reachable
                or (
                    session.http_port is not None
                    and session.http_port != self.client.address.port
                )
            ):
                self.hass.async_create_task(
                    self.async_request_refresh(),
                    f"{DOMAIN} poll after the panel connected",
                )

        _remember()
        unsubscribe = async_dispatcher_connect(
            self.hass, signal_session_changed(self._entry_id), _changed
        )
        self._vendor_recovery.observe(self.data)

        def stop_following() -> None:
            loss.close()
            self._vendor_recovery.close()
            unsubscribe()

        return stop_following

    @callback
    def async_follow_refused_hello(
        self, did: str, remote: str | None, addresses: tuple[str, ...]
    ) -> None:
        """Remember where a panel refused a session is, and poll it there now.

        Its app is too old for a session, or it was reinstalled under an
        identity no entry holds yet, so the poll is all that reaches it, and a
        panel that moved has no other way to say where it went.
        """
        # An unknown sender may be a reinstall of any linked entry on this
        # account. Keep each sender until health or a session proves ownership.
        self._refused_hellos.pop(did, None)
        self._refused_hellos[did] = (remote, addresses)
        if len(self._refused_hellos) > _MAX_REFUSED_HELLOS:
            del self._refused_hellos[next(iter(self._refused_hellos))]
        if not self.reachable:
            self.hass.async_create_task(
                self.async_request_refresh(),
                f"{DOMAIN} poll after a refused hello",
            )

    async def _async_update_data(self) -> PanelSnapshot:
        """Read the snapshot from the session that manages, else over HTTP."""
        session = self._session()
        if (
            session is not None
            and session.http_port is not None
            and session.http_port != self.client.address.port
            and (
                self._port_probe is None
                or self._port_probe[0] is not session
                or monotonic() >= self._port_probe[1]
            )
        ):
            self._port_probe = (session, monotonic() + _PORT_PROBE_INTERVAL)
            # Only the stored address failing as well needs the owner.
            await self._async_recover_address(report=False)
        if session_manages(session):
            assert session is not None
            entry = self._entry()
            address = self.client.address
            stored_value = entry.data[CONF_ADDRESS] if entry is not None else None

            def probe_current() -> bool:
                return (
                    self._session() is session
                    and self._entry() is entry
                    and self.client.address == address
                    and (
                        entry is None
                        or (
                            entry.data[CONF_ADDRESS] == stored_value
                            and entry.unique_id == session.did
                        )
                    )
                )

            # A dead stored address is reported only once the session has
            # answered this same read: a panel losing power still holds its
            # session until the heartbeat notices, and is not "connected".
            withheld: list[tuple[str, str]] = []
            health, snapshot = await asyncio.gather(
                self._async_probe_address(session, withheld),
                self._async_read_session(session),
                return_exceptions=True,
            )
            self.reachable = isinstance(health, PanelHealth)
            if (
                isinstance(health, PanelHealth)
                and isinstance(snapshot, UpdateFailed)
                and isinstance(snapshot.__cause__, HomeAssistantError)
                and snapshot.__cause__.translation_key == ERR_PANEL_UNAVAILABLE
                and probe_current()
                and (entry is None or accept_health(self.hass, entry, health))
            ):
                # A snapshot deadline also covers optional diagnostics. Keep the
                # health this same panel just answered, without another request.
                await self._async_clear_update_failure(
                    health, allow_http=True, is_current=probe_current
                )
                if probe_current():
                    self.identity_mismatch = False
                    snapshot = PanelSnapshot(
                        health=health, status=None, status_error="unavailable"
                    )
            if not (
                isinstance(snapshot, UpdateFailed)
                and isinstance(snapshot.__cause__, HomeAssistantError)
                and snapshot.__cause__.translation_key == ERR_PANEL_UNAVAILABLE
                and not session_manages(self._session())
            ):
                for outcome in (health, snapshot):
                    if isinstance(outcome, BaseException):
                        raise outcome
                assert isinstance(snapshot, PanelSnapshot)
                if withheld and entry is not None and probe_current():
                    self._report(entry, *withheld[-1])
                self._vendor_recovery.observe(snapshot)
                await self._async_activate_completed_admin(snapshot.health)
                return snapshot
            # The session ended before its reads returned: its failure says
            # nothing about the panel. Finish over HTTP, as for a panel
            # without a session.
        self.reachable = False
        snapshot = await self._async_poll_address()
        self.reachable = True
        if self._awaiting_session() and (entry := self._entry()) is not None:
            await self._saved_account.async_read(
                self.client, entry.title, snapshot.status
            )
        self._vendor_recovery.observe(snapshot)
        await self._async_activate_completed_admin(snapshot.health)
        return snapshot

    async def _async_activate_completed_admin(self, health: PanelHealth) -> None:
        """Finish setup-owned HOME and the screen-lock fallback after completion.

        The install programs never claim the device admin, and the panel's own
        setup finishes after they return, so the poll is what sees it finish.
        The pass runs once per installed build: its success is recorded in the
        entry (``CONF_DEVICE_SETUP``), so a later poll, a Home Assistant
        restart or a moved address never claims the admin or runs the vendor
        setup again, and an owner who changes those settings back on the panel
        keeps the change until the next install or update. Only the panel's own
        pending HOME intent, which setup leaves until HOME is claimed, runs it
        again. Attempts are at most once every five minutes. Success needs
        Android readback and ends further ADB work. Nothing keeps an ADB
        connection or adds a timer.
        """
        from . import adb_protocol as adb
        from . import local_adb
        from .adb_credentials import (
            AdbCredentialError,
            async_get_durable_adb_credential,
        )
        from .app_identity import (
            EQUIVALENT_HOME_COMPONENTS,
            LAUNCH_COMPONENTS,
        )
        from .install_adb import (
            _async_identity_root,
            _async_repair_app_permissions,
            _async_vendor_setup,
        )
        from .install_jobs import async_get_install_job_manager
        from .install_network import (
            InstallNetworkError,
            async_pin_entry_target,
            async_revalidate_install_target,
        )
        from .install_receipt import InstallJobError
        from .panel_operations import claim_panel_operation, release_panel_operation
        from .provisioning import InstallTargetState, async_probe_install_target

        entry = self._entry()
        if (
            entry is not None
            and not health.setup_complete
            and entry.data.get(CONF_DEVICE_SETUP) == DEVICE_SETUP_ADOPT
            and health.discovery_id == entry.unique_id
            and not self.identity_mismatch
        ):
            # Seen before its setup finished: this panel still owes the pass.
            self._record_device_setup(entry, None)
        if (
            not health.setup_complete
            or not self.available
            or entry is None
            or not health.discovery_id
            or health.discovery_id != entry.unique_id
            or health.package not in LAUNCH_COMPONENTS
            or self.identity_mismatch
            or self.hass.is_stopping
        ):
            return
        package = health.package
        assert package is not None
        address = entry.data[CONF_ADDRESS]
        key = (health.discovery_id, package, health.version_code, address)
        if self._admin_key != key:
            self._admin_key = key
            self._admin_done = False
            self._admin_retry_at = 0.0
            self._admin_backoff = 0.0
            self._recovery_session = None
        build: dict[str, Any] = {
            "did": health.discovery_id,
            "package": package,
            "version_code": health.version_code,
        }
        recorded = entry.data.get(CONF_DEVICE_SETUP)
        if not self._admin_done:
            if recorded == DEVICE_SETUP_ADOPT:
                # Root is unknown without ADB, so recovery is left to the
                # operations that need it until the next install or update.
                self._record_device_setup(entry, {**build, "root": None})
                self._admin_done = True
                self._admin_root = False
            elif isinstance(recorded, dict) and all(
                recorded.get(field) == value for field, value in build.items()
            ):
                self._admin_done = True
                self._admin_root = recorded.get("root") is True
        session = local_adb.session_for(self.hass, entry)
        # A finished pass still restarts recovery once per new session: after a
        # reboot only the network route may have the root to start it.
        recovering = self._admin_done and not health.home_pending
        if (
            (
                recovering
                and (
                    not self._admin_root
                    or session is None
                    or session is self._recovery_session
                )
            )
            or monotonic() < self._admin_retry_at
            or not claim_panel_operation(self.hass, entry.entry_id)
        ):
            return
        device: AdbDeviceAsync | None = None
        try:
            async with asyncio.timeout(None if session is not None else 45):
                manager = await async_get_install_job_manager(self.hass)
                self._admin_retry_at = monotonic() + 300
                if recovering:
                    if await local_adb.async_ensure_recovery(
                        self.hass, entry, self.client.address
                    ):
                        self._recovery_session = session
                    return
                # A background pass never waits on an on-panel approval.
                target = await local_adb.async_target(
                    self.hass, entry, self.client.address, approval=False
                )
                signer = None
                if target is None:
                    credential = await async_get_durable_adb_credential(self.hass)
                    pinned = await async_pin_entry_target(
                        self.hass, self.client.address, health, entry.unique_id
                    )
                    if pinned is None:
                        return
                    if await manager.async_find_active(
                        address, pinned.pinned.stored_value
                    ):
                        return
                    probe = await async_probe_install_target(
                        pinned.pinned, credential.signer
                    )
                    target = probe.adb_target(pinned.pinned)
                    if (
                        probe.state is not InstallTargetState.INSTALLED
                        or target is None
                    ):
                        return
                    await async_revalidate_install_target(self.hass, pinned)
                    if (
                        self._entry() is not entry
                        or entry.unique_id != health.discovery_id
                        or entry.data[CONF_ADDRESS] != address
                        or await manager.async_find_active(
                            address, pinned.pinned.stored_value
                        )
                    ):
                        return
                    signer = credential.signer
                elif any(
                    not receipt.is_terminal
                    and address
                    in (receipt.target.address, receipt.target.pinned_address)
                    for receipt in await manager.async_list()
                ):
                    return
                if self._entry() is not entry or entry.data[CONF_ADDRESS] != address:
                    return
                device = await adb._async_connect(target, signer, authorize=False)
                self._admin_root = await _async_identity_root(device, target) in (
                    adb.AdbRootMode.ROOT_ADBD,
                    adb.AdbRootMode.ROOT_SU,
                )
                if health.home_pending:
                    # Only initial setup's durable pending intent permits a HOME
                    # takeover here. Explicit handback clears it, across HA reloads.
                    await _async_repair_app_permissions(
                        device,
                        target,
                        package,
                        LAUNCH_COMPONENTS[package][-1],
                        deferred_setup=True,
                        report_denials=turned_off_reporter(self.hass, entry.entry_id),
                    )
                self._admin_done, home = await _async_admin_claim(
                    device, target, package
                )
                own_homes = {
                    home
                    for launch in LAUNCH_COMPONENTS[package]
                    for home in EQUIVALENT_HOME_COMPONENTS[launch]
                }
                if self._admin_done and not health.home_pending and home in own_homes:
                    # Native completion can finish HOME before this poll. Finish
                    # vendor commissioning only while the app still owns HOME;
                    # a deliberate handback must never be undone by HA reload.
                    await _async_vendor_setup(device, target)
                if self._admin_done and self._entry() is entry:
                    self._record_device_setup(
                        entry, {**build, "root": self._admin_root}
                    )
        except (
            AdbCredentialError,
            InstallNetworkError,
            InstallJobError,
            HaPaneldError,
            adb.InstallAdbError,
            adb._MalformedAdbResponse,
            adb._UnsafeAdbPacket,
            *adb._ADB_EXCEPTIONS,
            OSError,
            TimeoutError,
        ) as err:
            # Admin readback alone cannot finish a pass whose vendor I/O failed.
            # A failed recovery restart leaves a finished pass finished.
            self._admin_done = recovering
            _LOGGER.debug("Deferred panel device admin could not be activated")
            if local_adb.approval_pending(err):
                # Hardened mode: each shell needs someone at the panel. Ask
                # again only after a doubling back-off, past the prompt's life.
                self._admin_backoff = min(
                    max(_ADMIN_APPROVAL_BACKOFF, self._admin_backoff * 2),
                    _ADMIN_APPROVAL_BACKOFF_MAX,
                )
                self._admin_retry_at = monotonic() + self._admin_backoff
        finally:
            try:
                await adb._async_close(device)
            finally:
                release_panel_operation(self.hass, entry.entry_id)

    @callback
    def _record_device_setup(
        self, entry: ConfigEntry, record: dict[str, Any] | None
    ) -> None:
        """Keep which build had the device setup pass, or that one is owed."""
        if entry.data.get(CONF_DEVICE_SETUP) == record:
            return
        data = {
            key: value for key, value in entry.data.items() if key != CONF_DEVICE_SETUP
        }
        if record is not None:
            data[CONF_DEVICE_SETUP] = record
        self.hass.config_entries.async_update_entry(entry, data=data)

    async def _async_read_session(self, session: PanelSession) -> PanelSnapshot:
        """Read health and status from the panel's own session."""
        entry = self._entry()
        try:
            result = await async_manage(self.hass, session, MANAGE_SNAPSHOT)
            health_line, status_body = result["health"], result["status"]
            # The bounds HTTP applies before parsing; the health parser checks its own.
            if (
                not isinstance(health_line, str)
                or not isinstance(status_body, str)
                or len(status_body.encode()) > MAX_STATUS_RESPONSE_BYTES
            ):
                raise InvalidResponseError
            health = parse_health_response(health_line)
            status = parse_status_response(status_body)
        except (HomeAssistantError, HaPaneldError, KeyError) as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_update_failed"
            ) from err
        if entry is not None and not accept_health(self.hass, entry, health):
            self.identity_mismatch = True
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_update_failed"
            )
        self.identity_mismatch = False
        await self._async_clear_update_failure(
            health, webview_completed=_webview_done(status)
        )
        # An entry removed during the awaits above has no owner left to clear
        # a Repair raised from this observation.
        if self._entry() is not entry:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_address_retired"
            )
        if entry is not None:
            async_reconcile_permission_issue(self.hass, entry, status)
            async_reconcile_proximity_issue(self.hass, entry, status)
            async_reconcile_entity_hold_issue(self.hass, entry, status)
            async_reconcile_root_issue(self.hass, entry, status)
            await async_reconcile_webview_issue(self.hass, entry, status)
        return PanelSnapshot(health=health, status=status, status_error=None)

    async def _async_probe_address(
        self, session: PanelSession, withheld: list[tuple[str, str]]
    ) -> PanelHealth | None:
        """The health that proves the stored address answers as this panel.

        Only the operations still carried over HTTP need it, and the address
        repair follows from it. Delete it with them.
        """
        entry = self._entry()
        address = self.client.address
        stored_value = entry.data[CONF_ADDRESS] if entry is not None else None

        def retired() -> bool:
            # An address edit, a moved-panel adoption, a removed entry or a newer
            # session during the read leaves this answer no authority.
            return (
                self._entry() is not entry
                or self.client.address != address
                or (entry is not None and entry.data[CONF_ADDRESS] != stored_value)
                or self._session() is not session
            )

        try:
            health: PanelHealth | None = await self.client.async_get_health()
        except HaPaneldError:
            health = None
        if retired():
            return None
        if health is None or health.discovery_id != session.did:
            # Recovery fences its own answer (entry, address and session) after
            # its I/O, and moves the address when it adopts one.
            health = await self._async_recover_address(withheld=withheld)
            if health is None:
                return None
        if self._entry_id is not None:
            async_delete_address_issue(self.hass, self._entry_id)
        self._refused_hellos.clear()
        return health

    async def _async_poll_address(self) -> PanelSnapshot:
        """Read health, then best-effort sanitized status, over HTTP.

        The carrier for a panel whose session does not manage. Delete it once
        every supported panel offers management.
        """
        entry = self._entry()
        address = self.client.address
        stored_value = entry.data[CONF_ADDRESS] if entry is not None else None
        try:
            health = await self.client.async_get_health()
        except HaPaneldError as err:
            recovered = await self._async_recover_address()
            if recovered is None:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="health_update_failed",
                ) from err
            health = recovered
        else:
            # A moved-panel hello or an address edit can retire this request
            # while it is in flight. Its answer has no authority at the new address.
            if (
                self._entry() is not entry
                or self.client.address != address
                or (entry is not None and entry.data[CONF_ADDRESS] != stored_value)
            ):
                self.client.health_peer = None
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="health_address_retired"
                )
        entry = self._entry()
        if entry is not None and not accept_health(self.hass, entry, health):
            self.identity_mismatch = True
            self.client.health_peer = None
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_update_failed"
            )
        self.identity_mismatch = False
        self._refused_hellos.clear()
        status_address = self.client.address
        status_entry_address = entry.data[CONF_ADDRESS] if entry is not None else None
        status_identity = entry.unique_id if entry is not None else None
        if self._entry_id is not None:
            await self._async_clear_update_failure(health, allow_http=True)
            async_delete_address_issue(self.hass, self._entry_id)
            if not self.connected:
                sessions = async_get_sessions(self.hass)
                if health.restart is None:
                    sessions.clear_restart_notice(self._entry_id)
                else:
                    sessions.set_restart_notice(self._entry_id, *health.restart)

        try:
            status = await self.client.async_get_status()
        except CannotConnectError:
            return PanelSnapshot(health=health, status=None, status_error="unavailable")
        except InvalidResponseError:
            return PanelSnapshot(
                health=health, status=None, status_error="invalid_response"
            )
        # An entry removal, move or identity change after health was accepted
        # retires the observation before it can clear a commissioning warning.
        if (
            self._entry() is not entry
            or self.client.address != status_address
            or (
                entry is not None
                and (
                    entry.data[CONF_ADDRESS] != status_entry_address
                    or entry.unique_id != status_identity
                )
            )
        ):
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_address_retired"
            )
        if self._entry_id is not None and _webview_done(status):
            # The earlier health-only clear could not see the WebView finish.
            await self._async_clear_update_failure(
                health, allow_http=True, webview_completed=True
            )
        if entry is not None:
            async_reconcile_permission_issue(self.hass, entry, status)
            async_reconcile_proximity_issue(self.hass, entry, status)
            async_reconcile_entity_hold_issue(self.hass, entry, status)
            async_reconcile_root_issue(self.hass, entry, status)
            await async_reconcile_webview_issue(self.hass, entry, status)
        return PanelSnapshot(health=health, status=status, status_error=None)

    async def _async_refresh(
        self,
        log_failures: bool = True,
        raise_on_auth_failed: bool = False,
        scheduled: bool = False,
        raise_on_entry_error: bool = False,
    ) -> None:
        await super()._async_refresh(
            log_failures and not self.expected_restart,
            raise_on_auth_failed,
            scheduled,
            raise_on_entry_error,
        )

    async def _async_clear_update_failure(
        self,
        health: PanelHealth,
        *,
        allow_http: bool = False,
        is_current: Callable[[], bool] | None = None,
        webview_completed: bool = False,
    ) -> None:
        """Withdraw a failed-update repair once the panel runs the build it named.

        An older panel's health line carries no build number; only the HTTP
        carrier can then read it from the diagnostics dump.
        """
        if self._entry_id is None:
            return
        from .repairs_failure import (
            async_clear_update_failure_if_installed,
            panel_failure_issue_id,
        )

        issue_id = panel_failure_issue_id(f"update:{self._entry_id}")
        if ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id) is None:
            return
        installed_code = health.version_code
        if installed_code is None and allow_http:
            try:
                name, installed_code = await self.client.async_get_version_code()
            except HaPaneldError:
                installed_code = None
            else:
                if name != health.version:
                    installed_code = None
        if is_current is not None and not is_current():
            return
        feed = async_get_feed_coordinator(self.hass)
        current = (
            feed.verified_newest(
                health.package,
                allow_prerelease=prereleases_allowed(self._entry()),
            )
            if feed is not None and feed.last_update_success and feed.data is not None
            else None
        )
        current_code = current.version_code if current is not None else None
        await async_clear_update_failure_if_installed(
            self.hass,
            self._entry_id,
            health.version,
            installed_code,
            verified_current_code=current_code,
            is_current=is_current,
            webview_completed=webview_completed,
        )

    async def _async_recover_address(
        self,
        *,
        report: bool = True,
        withheld: list[tuple[str, str]] | None = None,
    ) -> PanelHealth | None:
        """Try the address a connected panel is talking from, when it has one.

        A panel refused a session for its protocol, reinstalled under a new
        identity linked to this entry, or whose session has ended, still said
        where it is in its hello, and is tried there. Returns the health
        read at the adopted address, or nothing when there was neither, no
        other address to try, or the address did not answer as this panel.
        Only a session's last two are reported: without one the panel is
        unavailable, which the failed poll already says. A caller that passes
        ``withheld`` gets the report there instead, to raise once the session
        has proved itself live.
        """
        session = self._session()
        entry = self._entry()
        if entry is None or not is_installation(entry):
            return None
        if session is not None:
            hellos = [(session.remote, session.addresses, session.http_port)]
        else:
            hellos = ([self._last_hello] if self._last_hello is not None else []) + [
                (remote, addresses, None)
                for remote, addresses in self._refused_hellos.values()
            ]
        stored = self.client.address
        stored_value = entry.data[CONF_ADDRESS]
        candidates = tuple(
            dict.fromkeys(
                candidate
                for remote, reported, http_port in hellos
                for candidate in session_candidates(remote, stored, reported, http_port)
            )
        )
        if not candidates:
            if session is not None and report:
                self._report_or_withhold(
                    entry,
                    ISSUE_PANEL_ADDRESS_UNREACHABLE,
                    session.remote or stored.host,
                    withheld,
                )
            return None
        answers = await async_probe_addresses(self.hass, candidates)
        for candidate, health in zip(candidates, answers, strict=True):  # noqa: B007 — used after the loop
            if health is not None and (
                health.discovery_id == entry.unique_id
                or reinstalled(self.hass, entry, health)
            ):
                break
        else:
            # A session that ended during the probe no longer says "connected".
            if session is not None and report and self._session() is session:
                self._report_or_withhold(
                    entry, ISSUE_PANEL_ADDRESS_UNVERIFIED, candidates[0].host, withheld
                )
            return None
        # A superseded session or edited entry cannot move an endpoint after I/O.
        if (
            self._session() is not session
            or self._entry() is not entry
            or self.client.address != stored
            or entry.data[CONF_ADDRESS] != stored_value
            or not is_installation(entry)
            or (session is not None and entry.unique_id != session.did)
        ):
            return None
        # The entry is the one record of the address. Its update listener
        # moves the running client, before this returns, so the status read
        # that follows already goes to the adopted address.
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: candidate.stored_value}
        )
        _LOGGER.info(
            "%s is now polled at its verified session-reported endpoint",
            entry.title,
        )
        return health

    @callback
    def _report_or_withhold(
        self,
        entry: ConfigEntry,
        issue: str,
        session_address: str,
        withheld: list[tuple[str, str]] | None,
    ) -> None:
        if withheld is None:
            self._report(entry, issue, session_address)
        else:
            withheld.append((issue, session_address))

    @callback
    def _report(self, entry: ConfigEntry, issue: str, session_address: str) -> None:
        from .device import panel_display_name

        async_raise_address_issue(
            self.hass,
            entry.entry_id,
            issue,
            panel=panel_display_name(self.hass, entry),
            address=self.client.address.stored_value,
            session_address=session_address,
        )


class PanelCoordinatorEntity(CoordinatorEntity[HaPaneldDataUpdateCoordinator]):
    """An entity of the panel's device, available while the panel is."""

    @property
    def available(self) -> bool:
        """Available while the panel answers polls or holds a session."""
        return self.coordinator.available

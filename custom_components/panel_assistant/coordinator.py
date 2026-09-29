"""Data coordinator for the ha-paneld integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .address import (
    ISSUE_PANEL_ADDRESS_UNREACHABLE,
    ISSUE_PANEL_ADDRESS_UNVERIFIED,
    async_delete_address_issue,
    async_probe_addresses,
    async_raise_address_issue,
    session_candidates,
)
from .app_identity import LEGACY_PACKAGE_ID
from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    PanelHealth,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, update_unique_id
from .feed_coordinator import async_get_feed_coordinator
from .identity import accept_health, is_installation
from .status import PanelStatus
from .transport import (
    PanelSession,
    RestartNotice,
    async_get_sessions,
    signal_session_changed,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PanelSnapshot:
    """Cached health authority and optional sanitized status diagnostics."""

    health: PanelHealth
    status: PanelStatus | None
    status_error: str | None


class HaPaneldDataUpdateCoordinator(DataUpdateCoordinator[PanelSnapshot]):
    """Poll stable health plus optional read-only status diagnostics.

    `last_update_success` keeps its meaning: the stored address answered the
    last poll. Whether the panel is available is `available`, which also
    counts the panel's own session, since a panel talking to Home Assistant is
    connected whatever the poll says. A failed poll while the panel is
    connected is repaired from the session where it can be, and reported
    where it cannot; it never takes the panel unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: HaPaneldClient,
        entry_id: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._entry_id = entry_id
        self.identity_mismatch = False

    @property
    def connected(self) -> bool:
        """Return whether the panel holds an open session with Home Assistant."""
        return self._session() is not None

    @property
    def available(self) -> bool:
        """Return whether the panel is reachable outbound or connected inbound."""
        return self.last_update_success or self.connected

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

        @callback
        def _changed() -> None:
            self.async_update_listeners()
            if self.connected and not self.last_update_success:
                self.hass.async_create_task(
                    self.async_request_refresh(),
                    f"{DOMAIN} poll after the panel connected",
                )

        return async_dispatcher_connect(
            self.hass, signal_session_changed(self._entry_id), _changed
        )

    def _shows_panel_update(self) -> bool:
        """Return whether this entry's update entity is registered and enabled."""
        if self._entry_id is None:
            return False
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            "update", DOMAIN, update_unique_id(self._entry_id)
        )
        entry = registry.async_get(entity_id) if entity_id else None
        return entry is not None and entry.disabled_by is None

    async def _async_update_data(self) -> PanelSnapshot:
        """Fetch health authority, then best-effort sanitized status."""
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
                    translation_domain=DOMAIN, translation_key="health_update_failed"
                )
        entry = self._entry()
        if entry is not None and not accept_health(self.hass, entry, health):
            self.identity_mismatch = True
            self.client.health_peer = None
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="health_update_failed"
            )
        self.identity_mismatch = False
        if self._entry_id is not None:
            from .failure_repair import (
                async_clear_update_failure_if_installed,
                panel_failure_issue_id,
            )

            issue_id = panel_failure_issue_id(f"update:{self._entry_id}")
            if ir.async_get(self.hass).async_get_issue(DOMAIN, issue_id) is not None:
                try:
                    name, installed_code = await self.client.async_get_version_code()
                except HaPaneldError:
                    installed_code = None
                else:
                    if name != health.version:
                        installed_code = None
                feed = async_get_feed_coordinator(self.hass)
                current = (
                    feed.verified_newest(health.package or LEGACY_PACKAGE_ID)
                    if feed is not None
                    and feed.last_update_success
                    and feed.data is not None
                    else None
                )
                current_code = current.version_code if current is not None else None
                await async_clear_update_failure_if_installed(
                    self.hass,
                    self._entry_id,
                    health.version,
                    installed_code,
                    verified_current_code=current_code,
                )
            async_delete_address_issue(self.hass, self._entry_id)
            if not self.connected:
                sessions = async_get_sessions(self.hass)
                if health.restart is None:
                    sessions.clear_restart_notice(self._entry_id)
                else:
                    sessions.set_restart_notice(self._entry_id, *health.restart)

        try:
            status = await self.client.async_get_status(
                update_owner=self._shows_panel_update()
            )
        except CannotConnectError:
            return PanelSnapshot(health=health, status=None, status_error="unavailable")
        except InvalidResponseError:
            return PanelSnapshot(
                health=health, status=None, status_error="invalid_response"
            )
        return PanelSnapshot(health=health, status=status, status_error=None)

    async def _async_recover_address(self) -> PanelHealth | None:
        """Try the address a connected panel is talking from, when it has one.

        Returns the health read at the adopted address, or nothing when there
        was no session, no other address to try, or the address did not answer
        as this panel. Only the last two are reported: with no session the
        panel is unavailable, which the failed poll already says.
        """
        session = self._session()
        entry = self._entry()
        if session is None or entry is None or not is_installation(entry):
            return None
        stored = self.client.address
        stored_value = entry.data[CONF_ADDRESS]
        candidates = session_candidates(session.remote, stored, session.addresses)
        if not candidates:
            self._report(
                entry, ISSUE_PANEL_ADDRESS_UNREACHABLE, session.remote or stored.host
            )
            return None
        answers = await async_probe_addresses(self.hass, candidates)
        for candidate, health in zip(candidates, answers, strict=True):  # noqa: B007 — used after the loop
            if health is not None and health.discovery_id == session.did:
                break
        else:
            self._report(entry, ISSUE_PANEL_ADDRESS_UNVERIFIED, candidates[0].host)
            return None
        # A superseded session or edited entry cannot move an endpoint after I/O.
        if (
            self._session() is not session
            or self._entry() is not entry
            or self.client.address != stored
            or entry.data[CONF_ADDRESS] != stored_value
            or not is_installation(entry)
            or entry.unique_id != session.did
        ):
            return None
        # The entry is the one record of the address. Its update listener
        # moves the running client, before this returns, so the status read
        # that follows already goes to the adopted address.
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: candidate.stored_value}
        )
        _LOGGER.info(
            "%s is now polled at the address it connected from; the stored"
            " address stopped answering",
            entry.title,
        )
        return health

    @callback
    def _report(self, entry: ConfigEntry, issue: str, session_address: str) -> None:
        async_raise_address_issue(
            self.hass,
            entry.entry_id,
            issue,
            panel=entry.title,
            address=self.client.address.stored_value,
            session_address=session_address,
        )


class PanelCoordinatorEntity(CoordinatorEntity[HaPaneldDataUpdateCoordinator]):
    """An entity of the panel's device, available while the panel is."""

    @property
    def available(self) -> bool:
        """Available while the panel answers polls or holds a session."""
        return self.coordinator.available

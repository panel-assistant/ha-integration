"""Data coordinator for the ha-paneld integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    async_raise_address_issue,
    session_candidate,
)
from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    PanelHealth,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, update_unique_id
from .status import PanelStatus
from .transport import PanelSession, async_get_sessions, signal_session_changed

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

    @property
    def connected(self) -> bool:
        """Return whether the panel holds an open session with Home Assistant."""
        return self._session() is not None

    @property
    def available(self) -> bool:
        """Return whether the panel is reachable outbound or connected inbound."""
        return self.last_update_success or self.connected

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
        if self._entry_id is not None:
            async_delete_address_issue(self.hass, self._entry_id)
            self._learn_identity(health)

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
        if session is None or entry is None:
            return None
        stored = self.client.address
        candidate = session_candidate(session.remote, stored)
        if candidate is None:
            self._report(
                entry, ISSUE_PANEL_ADDRESS_UNREACHABLE, session.remote or stored.host
            )
            return None
        try:
            health = await HaPaneldClient(
                async_get_clientsession(self.hass), candidate
            ).async_get_health()
        except HaPaneldError:
            health = None
        # A panel that does not report its identity cannot prove it is the one
        # holding the session, so its address is never adopted from one.
        if health is None or health.discovery_id != session.did:
            self._report(entry, ISSUE_PANEL_ADDRESS_UNVERIFIED, candidate.host)
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
    def _learn_identity(self, health: PanelHealth) -> None:
        """Make the reported identity the entry's own, so it outlives the address.

        A discovered entry carries its panel's identity from the start; one
        added by address did not, and could match its panel's hello only
        through a snapshot a successful poll had left behind. After a restart
        with the stored address dead there was no snapshot, so the panel was
        refused as unknown and could never repair the address. The identity
        is recorded once, and never one another entry already holds.
        """
        entry = self._entry()
        if (
            entry is None
            or entry.unique_id is not None
            or health.discovery_id is None
            or self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, health.discovery_id
            )
            is not None
        ):
            return
        self.hass.config_entries.async_update_entry(
            entry, unique_id=health.discovery_id
        )

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

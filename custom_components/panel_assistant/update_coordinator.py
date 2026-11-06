"""Local operation recovery for configured Panel Assistant updates."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    PanelAddress,
    PanelInstallStatus,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .identity import accept_health

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RestoreOwner:
    """The immutable entry/address context that supplied an observation."""

    entry: ConfigEntry
    address: PanelAddress
    data: object = field(repr=False)
    identity: str | None

    def current(self, hass: HomeAssistant, client: HaPaneldClient) -> bool:
        """Even moving away and back retires the old entry data/address objects."""
        return (
            hass.config_entries.async_get_entry(self.entry.entry_id) is self.entry
            and client.address is self.address
            and self.address.stored_value == self.entry.data[CONF_ADDRESS]
            and self.entry.data is self.data
            and self.entry.unique_id == self.identity
        )


@dataclass(frozen=True, slots=True)
class PanelUpdateSnapshot:
    """One local operation-status observation, without a release lookup."""

    operation: PanelInstallStatus | None
    error: str | None
    _restore_owner: _RestoreOwner | None = field(default=None, repr=False)


class PanelUpdateCoordinator(DataUpdateCoordinator[PanelUpdateSnapshot]):
    """Follow only the panel's local destructive-operation state."""

    def __init__(
        self, hass: HomeAssistant, client: HaPaneldClient, entry_id: str | None = None
    ) -> None:
        """Initialize a local-only companion to the health coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_update",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._entry_id = entry_id

    @property
    def restore_snapshot(self) -> PanelUpdateSnapshot | None:
        """Only the current runtime owner may use admitted data for a Repair."""
        snapshot = self.data
        if snapshot is None or (owner := snapshot._restore_owner) is None:
            return None
        runtime = getattr(owner.entry, "runtime_data", None)
        if (
            runtime is None
            or runtime.update_coordinator is not self
            or runtime.coordinator.identity_mismatch
            or not owner.current(self.hass, self.client)
        ):
            return None
        return snapshot

    async def _async_restore_owner(self, entry_id: str) -> _RestoreOwner | None:
        """Admit fresh health without making progress depend on its success."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return None
        owner = _RestoreOwner(entry, self.client.address, entry.data, entry.unique_id)
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None and runtime.update_coordinator is not self:
            return None
        try:
            health = await self.client.async_get_health()
        except HaPaneldError:
            return None
        except Exception:  # A failed identity proof must not fail entry setup.
            _LOGGER.debug("Health for the restore Repair owner failed", exc_info=True)
            return None
        if (
            not owner.current(self.hass, self.client)
            or getattr(entry, "runtime_data", None) is not runtime
            or not health.installation_identity
            or health.discovery_id is None
            or not accept_health(self.hass, entry, health)
        ):
            return None
        # Admission may adopt an installation identity; bind its accepted state.
        return _RestoreOwner(entry, self.client.address, entry.data, entry.unique_id)

    async def _async_update_data(self) -> PanelUpdateSnapshot:
        """Keep progress separate from the identity proof required by Repairs."""
        if self._entry_id is None:
            return await self._async_operation()
        owner, snapshot = await asyncio.gather(
            self._async_restore_owner(self._entry_id), self._async_operation()
        )
        return replace(snapshot, _restore_owner=owner)

    async def _async_operation(self) -> PanelUpdateSnapshot:
        """Keep an unavailable local progress endpoint out of normal entry setup."""
        try:
            operation = await self.client.async_get_panel_install_status()
        except CannotConnectError:
            return PanelUpdateSnapshot(operation=None, error="unavailable")
        except InvalidResponseError:
            return PanelUpdateSnapshot(operation=None, error="invalid_response")
        return PanelUpdateSnapshot(operation=operation, error=None)

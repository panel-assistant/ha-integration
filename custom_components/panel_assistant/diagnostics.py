"""Diagnostics support for ha-paneld."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import HaPaneldConfigEntry
from .build import INTEGRATION_BUILD
from .const import CONF_CUTOVER, CONF_TRANSPORT_USER_ID, DOMAIN
from .coordinator import PanelSnapshot
from .repairs_restart import restart_pending
from .transport_sessions import session_diagnostics

# The record of an earlier move off MQTT names identities and MQTT IDs, and is
# not shown. The supported channels are shown, but not the identity that
# described them.
_ENTRY_KEYS_TO_REDACT = {
    CONF_ADDRESS,
    CONF_TRANSPORT_USER_ID,
    CONF_CUTOVER,
    "did",
    "identity_pending",
    "previous_installation_identity",
}
_HEALTH_KEYS_TO_REDACT = {"panel_id", "discovery_id", "legacy_discovery_id"}
_STATUS_KEYS_TO_REDACT = {"serial_number", "network_macs"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HaPaneldConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    transport = session_diagnostics(hass, entry.entry_id)
    return {
        **_diagnostics(entry),
        "device": _device(hass, entry),
        "restart_pending": restart_pending(hass),
        "transport": transport,
    }


def _device(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> dict[str, Any] | None:
    """Name the panel model even when its status read is failing; the card keeps it."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    if device is None:
        return None
    return {
        "manufacturer": device.manufacturer,
        "model": device.model,
        "hw_version": device.hw_version,
        "sw_version": device.sw_version,
    }


def _diagnostics(entry: HaPaneldConfigEntry) -> dict[str, Any]:
    """Build diagnostics only from cached data."""
    coordinator = entry.runtime_data.coordinator
    snapshot: PanelSnapshot | None = coordinator.data
    return {
        "integration_build": INTEGRATION_BUILD,
        "entry": async_redact_data(dict(entry.data), _ENTRY_KEYS_TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "reachable": coordinator.reachable,
        "connected": coordinator.connected,
        "health": (
            None
            if snapshot is None
            else async_redact_data(snapshot.health.as_dict(), _HEALTH_KEYS_TO_REDACT)
        ),
        "status": (
            async_redact_data(snapshot.status.as_dict(), _STATUS_KEYS_TO_REDACT)
            if snapshot is not None and snapshot.status is not None
            else None
        ),
        "status_error": None if snapshot is None else snapshot.status_error,
    }

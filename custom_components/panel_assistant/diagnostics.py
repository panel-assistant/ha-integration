"""Diagnostics support for ha-paneld."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import HaPaneldConfigEntry
from .const import CONF_TRANSPORT_USER_ID, INTEGRATION_BUILD
from .transport import session_diagnostics

_ENTRY_KEYS_TO_REDACT = {CONF_ADDRESS, CONF_TRANSPORT_USER_ID}
_HEALTH_KEYS_TO_REDACT = {"panel_id", "discovery_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HaPaneldConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    return {
        **_diagnostics(entry),
        "transport": session_diagnostics(hass, entry.entry_id),
    }


def _diagnostics(entry: HaPaneldConfigEntry) -> dict[str, Any]:
    """Build diagnostics only from cached data."""
    snapshot = entry.runtime_data.coordinator.data
    return {
        "integration_build": INTEGRATION_BUILD,
        "entry": async_redact_data(dict(entry.data), _ENTRY_KEYS_TO_REDACT),
        "last_update_success": entry.runtime_data.coordinator.last_update_success,
        "health": async_redact_data(snapshot.health.as_dict(), _HEALTH_KEYS_TO_REDACT),
        "status": snapshot.status.as_dict() if snapshot.status is not None else None,
        "status_error": snapshot.status_error,
    }

"""The panels whose entry was removed, remembered across restarts.

A removed entry cannot tell its panel anything, because Home Assistant unloads
an entry, ending its session, before it removes it. So the panel's identity is
remembered, and its next hello is refused as removed rather than as unknown;
an entry that loads with the identity again forgets it.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import is_valid_discovery_id
from .const import DOMAIN
from .transport import DATA_REMOVED_PANELS, _panel_did

_LOGGER = logging.getLogger(__name__)

# The panels whose entry was removed, newest last.
REMOVED_PANELS_STORAGE_KEY: Final = f"{DOMAIN}.removed_panels"
REMOVED_PANELS_STORAGE_VERSION: Final = 1
MAX_REMOVED_PANELS: Final = 64


class RemovedPanels:
    """The identities of panels whose entry was removed, kept across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty list backed by Home Assistant's storage."""
        self._store: Store[dict[str, Any]] = Store(
            hass, REMOVED_PANELS_STORAGE_VERSION, REMOVED_PANELS_STORAGE_KEY
        )
        # Identity to removal time, oldest first.
        self._removed: dict[str, str] = {}

    def __contains__(self, did: object) -> bool:
        """Return whether a panel's entry was removed."""
        return did in self._removed

    async def async_load(self) -> None:
        """Read the stored list, ignoring anything malformed."""
        data = await self._store.async_load()
        panels = data.get("panels") if isinstance(data, dict) else None
        self._removed = {}
        for item in panels if isinstance(panels, list) else ():
            if (
                isinstance(item, dict)
                and isinstance(did := item.get("did"), str)
                and is_valid_discovery_id(did)
                and isinstance(removed_at := item.get("removed_at"), str)
            ):
                self._removed[did] = removed_at
        self._trim()

    async def async_add(self, did: str) -> None:
        """Remember a removed panel as the newest, and save."""
        self._removed.pop(did, None)
        self._removed[did] = dt_util.utcnow().isoformat()
        self._trim()
        await self._async_save()

    async def async_discard(self, did: str) -> None:
        """Forget a panel whose entry is loaded again, and save."""
        if self._removed.pop(did, None) is not None:
            await self._async_save()

    def _trim(self) -> None:
        while len(self._removed) > MAX_REMOVED_PANELS:
            del self._removed[next(iter(self._removed))]

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "panels": [
                    {"did": did, "removed_at": removed_at}
                    for did, removed_at in self._removed.items()
                ]
            }
        )


def _removed_panels(hass: HomeAssistant) -> RemovedPanels:
    removed: RemovedPanels = hass.data[DOMAIN][DATA_REMOVED_PANELS]
    return removed


async def async_load_removed_panels(hass: HomeAssistant) -> None:
    """Load the removed panels before any hello can ask about one."""
    removed = RemovedPanels(hass)
    await removed.async_load()
    hass.data.setdefault(DOMAIN, {})[DATA_REMOVED_PANELS] = removed


async def async_forget_removed_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget a removed panel once an entry reporting its identity loads."""
    did = _panel_did(entry)
    if did is None:
        return
    try:
        await _removed_panels(hass).async_discard(did)
    except Exception:
        _LOGGER.warning("The removed panels could not be saved", exc_info=True)


async def async_remember_removed_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remember a removed entry's panel, so its next hello is told. Never raises."""
    did = _panel_did(entry)
    if did is None or not is_valid_discovery_id(did):
        return
    try:
        await _removed_panels(hass).async_add(did)
    except Exception:
        _LOGGER.warning(
            "The removal of %s could not be saved for its panel",
            entry.title,
            exc_info=True,
        )

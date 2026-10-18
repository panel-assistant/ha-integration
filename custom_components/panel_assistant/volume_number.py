"""The volume number a panel with a media player no longer has.

The media player shows and sets the volume through the volume channel, so the
number an earlier version added beside it is removed. Removal waits until Home
Assistant has started: automations and scripts are loaded by then, and a
device action still resolves the number, so each one still using it can be
named in a Repair. The Repair stays until dismissed; removing the panel
withdraws it.
"""

from __future__ import annotations

from typing import Final

from homeassistant.components.automation import automations_with_entity
from homeassistant.components.script import scripts_with_entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN
from .transport_sessions import signal_native_removed

ISSUE_VOLUME_NUMBER_REMOVED: Final = "volume_number_removed"


@callback
def async_retire_volume_number(
    hass: HomeAssistant, entry: ConfigEntry, did: str
) -> None:
    """Remove this panel's own volume number once Home Assistant has started."""

    @callback
    def _retire(hass: HomeAssistant) -> None:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            Platform.NUMBER, DOMAIN, f"{did}_volume"
        )
        item = None if entity_id is None else registry.async_get(entity_id)
        if item is None or item.config_entry_id != entry.entry_id:
            return
        users = sorted(
            {
                *automations_with_entity(hass, item.entity_id),
                *scripts_with_entity(hass, item.entity_id),
            }
        )
        registry.async_remove(item.entity_id)
        # So that a later hello from the panel without a speaker adds it anew.
        async_dispatcher_send(
            hass, signal_native_removed(entry.entry_id), frozenset({item.unique_id})
        )
        if not users:
            return
        from .device import panel_display_name

        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_VOLUME_NUMBER_REMOVED}_{entry.entry_id}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_VOLUME_NUMBER_REMOVED,
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "entity": item.entity_id,
                "users": ", ".join(users),
            },
            data={"entry_id": entry.entry_id},
        )

    async_at_started(hass, _retire)

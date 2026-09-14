"""Native event entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform
from .transport import signal_event


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an event entity for each event channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.EVENT, async_add_entities, NativeEvent
    )


class NativeEvent(NativeEntity, EventEntity):
    """A panel button press, fired once per event the session counts."""

    @property
    def event_types(self) -> list[str]:
        """Return the event type codes the panel declared."""
        return list(self.descriptor["options"] or ())

    async def async_added_to_hass(self) -> None:
        """Also follow this entry's counted events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_event(self._entry_id), self._handle_event
            )
        )

    @callback
    def _handle_event(self, channel: str, event_type: str) -> None:
        if channel != self._channel or not self.available:
            return
        if event_type not in self.event_types:
            return
        self._trigger_event(event_type)
        self.async_write_ha_state()

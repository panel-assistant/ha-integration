"""Native text entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform, commands_refused
from .transport import MAX_STRING_LENGTH


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a text entity for each text channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.TEXT, async_add_entities, NativeText
    )


class NativeText(NativeEntity, TextEntity):
    """A panel route or dashboard path."""

    _attr_native_max = MAX_STRING_LENGTH

    @property
    def native_value(self) -> str | None:
        """Return the reported text."""
        value: str | None = self.reported_value
        return value

    async def async_set_value(self, value: str) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

"""Native select entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform, commands_refused


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a select for each select channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.SELECT, async_add_entities, NativeSelect
    )


class NativeSelect(NativeEntity, SelectEntity):
    """A panel setting chosen from option codes, translated by state key."""

    @property
    def options(self) -> list[str]:
        """Return the option codes the panel declared."""
        return list(self.descriptor["options"] or ())

    @property
    def current_option(self) -> str | None:
        """Return the reported option code."""
        value: str | None = self.reported_value
        return value

    async def async_select_option(self, option: str) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

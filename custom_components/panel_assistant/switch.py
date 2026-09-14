"""Native switch entities, dormant unless native entities are turned on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch for each switch channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.SWITCH, async_add_entities, NativeSwitch
    )


class NativeSwitch(NativeEntity, SwitchEntity):
    """A panel setting or relay that is on or off."""

    @property
    def is_on(self) -> bool | None:
        """Return the reported state."""
        value: bool | None = self.reported_value
        return value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the panel's switch on."""
        await self.async_command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the panel's switch off."""
        await self.async_command(False)

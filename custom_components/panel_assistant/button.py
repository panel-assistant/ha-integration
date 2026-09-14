"""Native button entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import (
    NativeEntity,
    async_setup_native_platform,
    commands_refused,
    enum_or_none,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a button for each button channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.BUTTON, async_add_entities, NativeButton
    )


class NativeButton(NativeEntity, ButtonEntity):
    """A panel action, such as reloading the dashboard."""

    @property
    def device_class(self) -> ButtonDeviceClass | None:
        """Return the device class the panel declared."""
        return enum_or_none(ButtonDeviceClass, self.descriptor["device_class"])

    async def async_press(self) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

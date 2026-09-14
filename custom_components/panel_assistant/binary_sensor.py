"""Native binary sensor entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform, enum_or_none


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a binary sensor for each binary sensor channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.BINARY_SENSOR, async_add_entities, NativeBinarySensor
    )


class NativeBinarySensor(NativeEntity, BinarySensorEntity):
    """A panel observation that is on or off, such as proximity."""

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return the device class the panel declared."""
        return enum_or_none(BinarySensorDeviceClass, self.descriptor["device_class"])

    @property
    def is_on(self) -> bool | None:
        """Return the reported state."""
        value: bool | None = self.reported_value
        return value

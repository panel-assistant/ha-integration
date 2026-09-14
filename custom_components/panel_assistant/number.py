"""Native number entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.number import (
    DEFAULT_MAX_VALUE,
    DEFAULT_MIN_VALUE,
    NumberEntity,
    NumberMode,
)
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
    """Add a number for each number channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.NUMBER, async_add_entities, NativeNumber
    )


class NativeNumber(NativeEntity, NumberEntity):
    """A bounded panel setting, such as volume."""

    _attr_mode = NumberMode.SLIDER

    @property
    def native_min_value(self) -> float:
        """Return the declared minimum, else Home Assistant's default."""
        low = self.descriptor["min"]
        return DEFAULT_MIN_VALUE if low is None else float(low)

    @property
    def native_max_value(self) -> float:
        """Return the declared maximum, else Home Assistant's default."""
        high = self.descriptor["max"]
        return DEFAULT_MAX_VALUE if high is None else float(high)

    @property
    def native_step(self) -> float | None:
        """Return the declared step, if any."""
        step = self.descriptor["step"]
        return None if step is None else float(step)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the declared unit."""
        unit: str | None = self.descriptor["unit"]
        return unit

    @property
    def native_value(self) -> float | None:
        """Return the reported value."""
        value: float | None = self.reported_value
        return value

    async def async_set_native_value(self, value: float) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

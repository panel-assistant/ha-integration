"""Diagnostic sensor for ha-paneld, and native sensors when turned on."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import HaPaneldConfigEntry
from .coordinator import HaPaneldDataUpdateCoordinator
from .device import panel_device_info
from .native import NativeEntity, async_setup_native_platform, enum_or_none


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ha-paneld diagnostic sensor, and any native sensors."""
    async_add_entities(
        [HaPaneldStatusSensor(entry.entry_id, entry.runtime_data.coordinator)]
    )
    async_setup_native_platform(
        hass, entry, Platform.SENSOR, async_add_entities, NativeSensor
    )


class HaPaneldStatusSensor(
    CoordinatorEntity[HaPaneldDataUpdateCoordinator], SensorEntity
):
    """Represent the health of one ha-paneld panel."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "status"

    def __init__(
        self, entry_id: str, coordinator: HaPaneldDataUpdateCoordinator
    ) -> None:
        """Initialize the sensor from cached coordinator data."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_status"

    @property
    def native_value(self) -> str:
        """Return the cached panel status."""
        return "online"

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        """Return the cached health diagnostics."""
        health = self.coordinator.data.health
        return {
            "build": health.build,
            "config_hash": health.config_hash,
            "home_assistant_state": health.ha_state,
            "home_assistant_source": health.ha_source,
            "home_assistant_subscription_refused": health.ha_subscription_refused,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Return API-backed device information."""
        return panel_device_info(
            self._entry_id,
            self.coordinator.data,
            self.coordinator.client.configuration_url,
        )


class NativeSensor(NativeEntity, SensorEntity):
    """A panel measurement, enum code, time or text, as its descriptor declares."""

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return the declared device class; declared options make an enum."""
        descriptor = self.descriptor
        if descriptor["options"] is not None:
            return SensorDeviceClass.ENUM
        return enum_or_none(SensorDeviceClass, descriptor["device_class"])

    @property
    def options(self) -> list[str] | None:
        """Return the enum codes the panel declared."""
        options = self.descriptor["options"]
        return None if options is None else list(options)

    @property
    def state_class(self) -> SensorStateClass | None:
        """Return the declared state class."""
        return enum_or_none(SensorStateClass, self.descriptor["state_class"])

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the declared unit."""
        unit: str | None = self.descriptor["unit"]
        return unit

    @property
    def force_update(self) -> bool:
        """Record every periodic report of a measurement that declares it."""
        force: bool = self.descriptor["force_update"]
        return force

    @property
    def native_value(self) -> str | int | float | datetime | None:
        """Return the reported value, parsing a declared timestamp."""
        value: Any = self.reported_value
        if value is not None and self.device_class is SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(value)
        return value  # type: ignore[no-any-return]

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
from homeassistant.util import dt as dt_util

from . import HaPaneldConfigEntry
from .const import DOMAIN, INTEGRATION_BUILD, INTEGRATION_VERSION
from .coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelCoordinatorEntity,
    PanelSnapshot,
)
from .device import version_with_build
from .native import NativeEntity, async_setup_native_platform, enum_or_none


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ha-paneld diagnostic sensors, and any native sensors."""
    async_add_entities(
        [
            HaPaneldStatusSensor(entry.entry_id, entry.runtime_data.coordinator),
            PanelAssistantVersionSensor(entry.entry_id),
        ]
    )
    async_setup_native_platform(
        hass, entry, Platform.SENSOR, async_add_entities, NativeSensor
    )


class HaPaneldStatusSensor(PanelCoordinatorEntity, SensorEntity):
    """Represent the health of one ha-paneld panel.

    Online is the ordinary answer. A panel that holds a session while its
    stored address does not answer polls is connected, and says so, rather
    than being folded into one flat answer that sends a person to look at the
    network when it is the address record that is stale.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "status"

    def __init__(
        self,
        entry_id: str,
        coordinator: HaPaneldDataUpdateCoordinator,
    ) -> None:
        """Initialize the sensor from cached coordinator data."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_status"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    @property
    def native_value(self) -> str:
        """Return whether the panel answers polls, or only holds its session."""
        if (notice := self.coordinator.restart_notice) is not None:
            return f"restarting_{notice.reason}"
        return "online" if self.coordinator.last_update_success else "connected"

    @property
    def available(self) -> bool:
        """Keep the restart state visible while the panel is temporarily away."""
        return self.coordinator.available or self.coordinator.restart_notice is not None

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | int | None]:
        """Return the cached health diagnostics and both halves of availability."""
        availability: dict[str, str | bool | int | None] = {
            "reachable": self.coordinator.last_update_success,
            "connected": self.coordinator.connected,
        }
        if (notice := self.coordinator.restart_notice) is not None:
            availability.update(
                scope=notice.scope,
                reason=notice.reason,
                expected_back_ms=notice.expected_back_ms,
            )
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return availability
        health = snapshot.health
        return {
            **availability,
            "build": health.build,
            "config_hash": health.config_hash,
            "home_assistant_state": health.ha_state,
            "home_assistant_source": health.ha_source,
            "home_assistant_subscription_refused": health.ha_subscription_refused,
        }


class PanelAssistantVersionSensor(SensorEntity):
    """Name the Panel Assistant build this panel is connected through.

    A device card has no field for the integration that connects it:
    `sw_version` is the panel's own software and `via_device` names a parent
    device, which Panel Assistant is not. A diagnostic entity on the panel's
    device is where Home Assistant puts such a fact, beside the Update entity
    that shows the app's own build.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "panel_assistant_version"
    _attr_should_poll = False
    _attr_native_value = version_with_build(INTEGRATION_VERSION, INTEGRATION_BUILD)

    def __init__(self, entry_id: str) -> None:
        """Attach to the entry's device, leaving its card to the status sensor."""
        self._attr_unique_id = f"{entry_id}_panel_assistant_version"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})


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

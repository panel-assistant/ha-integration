"""Native button entities, dormant unless native entities are turned on."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .const import DOMAIN
from .native import NativeEntity, async_setup_native_platform, enum_or_none
from .repairs_removal import async_request_removal


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a button for each button channel a panel describes."""
    async_add_entities([RemoveFromPanelButton(entry)])
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
        """Press the panel's button, which carries no value."""
        await self.async_command(None)


class RemoveFromPanelButton(ButtonEntity):
    """Ask the owner to confirm removal before changing the panel."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "remove_from_panel"
    _attr_icon = "mdi:delete-outline"

    def __init__(self, entry: HaPaneldConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_remove_from_panel"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        """Open a persistent confirmation; pressing alone removes nothing."""
        async_request_removal(self.hass, self._entry)

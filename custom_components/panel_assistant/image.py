"""Native image entities, dormant unless native entities are turned on."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform
from .transport import PanelSession


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an image for each image channel a panel describes."""

    def factory(
        entry_id: str, session: PanelSession, descriptor: Mapping[str, Any]
    ) -> NativeImage:
        return NativeImage(hass, entry_id, session, descriptor)

    async_setup_native_platform(
        hass, entry, Platform.IMAGE, async_add_entities, factory
    )


class NativeImage(NativeEntity, ImageEntity):
    """The panel camera snapshot, unavailable while the camera is stopped."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        session: PanelSession,
        descriptor: Mapping[str, Any],
    ) -> None:
        """Initialize the image entity and its HTTP client."""
        NativeEntity.__init__(self, entry_id, session, descriptor)
        ImageEntity.__init__(self, hass)

    @property
    def image_url(self) -> str | None:
        """Return the reported snapshot URL."""
        value = self.reported_value
        return None if value is None else str(value["url"])

    @callback
    def handle_observation(self) -> None:
        """Each known report is a new snapshot, so fetch it again."""
        if self.reported_value is None:
            return
        self._cached_image = None
        self._attr_image_last_updated = dt_util.utcnow()

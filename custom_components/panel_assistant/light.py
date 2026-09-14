"""Native light entities, dormant unless native entities are turned on."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import HaPaneldConfigEntry
from .contract import catalogue_entry
from .native import NativeEntity, async_setup_native_platform
from .transport import PanelSession


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a light for each light channel a panel describes."""
    async_setup_native_platform(
        hass, entry, Platform.LIGHT, async_add_entities, NativeLight
    )


class NativeLight(NativeEntity, LightEntity, RestoreEntity):
    """The screen, the LED, the button backlight or one button LED.

    Descriptors do not say whether a light takes a colour, which depends on the
    hardware, so the catalogue names the modes a channel can have and a
    reported colour selects RGB. The mode, never the value, is restored, so a
    reload or restart does not show a colour light as brightness-only again.
    """

    def __init__(
        self, entry_id: str, session: PanelSession, descriptor: Mapping[str, Any]
    ) -> None:
        """Take the modes this channel can have from the catalogue."""
        super().__init__(entry_id, session, descriptor)
        entry = catalogue_entry(descriptor)
        self._modes = frozenset(
            (entry or {}).get("color_modes") or (ColorMode.BRIGHTNESS,)
        )
        self._color_seen = False

    @property
    def color_mode(self) -> ColorMode:
        """Return RGB once a colour is reported, else the plainest declared mode."""
        if self._color_seen:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in self._modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return the one mode this light is in."""
        return {self.color_mode}

    @property
    def supported_features(self) -> LightEntityFeature:
        """Offer effects when the panel declared them."""
        if self.descriptor["options"]:
            return LightEntityFeature.EFFECT
        return LightEntityFeature(0)

    @property
    def effect_list(self) -> list[str] | None:
        """Return the effect codes the panel declared."""
        options = self.descriptor["options"]
        return None if not options else list(options)

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is on."""
        value = self.reported_value
        return None if value is None else bool(value["on"])

    @property
    def brightness(self) -> int | None:
        """Return the reported brightness."""
        value = self.reported_value
        if value is None or self.color_mode is ColorMode.ONOFF:
            return None
        brightness: int | None = value.get("brightness")
        return brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the reported colour."""
        value = self.reported_value
        if value is None or "color" not in value:
            return None
        color = value["color"]
        return (color["r"], color["g"], color["b"])

    @property
    def effect(self) -> str | None:
        """Return the reported effect code."""
        value = self.reported_value
        return None if value is None else value.get("effect")

    async def async_added_to_hass(self) -> None:
        """Restore whether this light was already seen taking a colour."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if (
            last is not None
            and ColorMode.RGB in self._modes
            and ColorMode.RGB in (last.attributes.get("supported_color_modes") or ())
        ):
            self._color_seen = True
            self.async_write_ha_state()

    @callback
    def handle_observation(self) -> None:
        """Remember that this light takes a colour once it reports one."""
        value = self.reported_value
        if value is not None and "color" in value and ColorMode.RGB in self._modes:
            self._color_seen = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, with any brightness, colour or effect asked for."""
        value: dict[str, Any] = {"on": True}
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            value["brightness"] = int(brightness)
        if (color := kwargs.get(ATTR_RGB_COLOR)) is not None:
            value["color"] = dict(
                zip("rgb", (int(part) for part in color), strict=True)
            )
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            value["effect"] = effect
        await self.async_command(value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.async_command({"on": False})

"""Native light entities, dormant unless native entities are turned on."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.light import LightEntity
from homeassistant.components.light.const import ColorMode, LightEntityFeature
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .contract import catalogue_entry
from .native import NativeEntity, async_setup_native_platform, commands_refused
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


class NativeLight(NativeEntity, LightEntity):
    """The screen, the LED, the button backlight or one button LED.

    Descriptors do not say whether a light takes a colour, which depends on the
    hardware, so the catalogue names the modes a channel can have and a
    reported colour selects RGB for the rest of the entity's life.
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

    @callback
    def handle_observation(self) -> None:
        """Remember that this light takes a colour once it reports one."""
        value = self.reported_value
        if value is not None and "color" in value and ColorMode.RGB in self._modes:
            self._color_seen = True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Refuse: commands still travel over MQTT."""
        raise commands_refused()

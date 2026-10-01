"""Panel cameras controlled by the same setting as the panel's camera UI."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import Stream
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from yarl import URL

from . import HaPaneldConfigEntry
from .client import HaPaneldError
from .native import NativeEntity, async_setup_native_platform
from .transport import PanelSession


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the camera only where the panel describes its camera capability."""

    def factory(
        entry_id: str, session: PanelSession, descriptor: Mapping[str, Any]
    ) -> NativeCamera:
        return NativeCamera(entry, session, descriptor)

    async_setup_native_platform(
        hass, entry, Platform.CAMERA, async_add_entities, factory
    )


class NativeCamera(NativeEntity, Camera):
    """Fresh JPEG and RTSP media from the currently verified panel address."""

    def __init__(
        self,
        entry: HaPaneldConfigEntry,
        session: PanelSession,
        descriptor: Mapping[str, Any],
    ) -> None:
        NativeEntity.__init__(self, entry.entry_id, session, descriptor)
        Camera.__init__(self)
        self._entry = entry
        self._media_generation = 0
        self._provider_refresh_lock = asyncio.Lock()
        self._media_owner: tuple[PanelSession | None, str] | None = None

    @property
    def available(self) -> bool:
        """Do not admit media while Core removes the entity."""
        return (
            self._platform_state is not EntityPlatformState.REMOVED
            and super().available
        )

    async def async_refresh_providers(self, *, write_state: bool = True) -> None:
        """Serialize Core provider lifecycle and recheck after awaited registration."""
        async with self._provider_refresh_lock:
            await super().async_refresh_providers(
                write_state=write_state and self.available
            )
            if not self.available or not self.is_on:
                await super().async_refresh_providers(
                    write_state=write_state and self.available
                )

    @property
    def is_on(self) -> bool:
        """Deliberate off remains available and can still receive commands."""
        return self.reported_value is True

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Withdraw streaming providers while media is unavailable or disabled."""
        features = CameraEntityFeature.ON_OFF
        if self.available and self.is_on:
            features |= CameraEntityFeature.STREAM
        return features

    def _media_identity(self) -> tuple[int, PanelSession | None, str]:
        return (
            self._media_generation,
            self.session,
            str(self._entry.runtime_data.client.address.base_url),
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch without caching and discard an image whose owner changed."""
        if not self.available or not self.is_on:
            return None
        identity = self._media_identity()
        try:
            image = await self._entry.runtime_data.client.async_get_camera_snapshot()
        except HaPaneldError:
            return None
        if not self.available or not self.is_on or self._media_identity() != identity:
            return None
        return image

    async def stream_source(self) -> str | None:
        """Resolve only the integration's current panel, never a reported URL."""
        if not self.available or not self.is_on:
            return None
        return str(
            URL.build(
                scheme="rtsp",
                host=self._entry.runtime_data.client.address.host,
                port=8554,
                path="/live",
            )
        )

    async def async_create_stream(self) -> Stream | None:
        """Do not reuse an old stream or complete creation across a privacy stop."""
        if not self.available or not self.is_on:
            return None
        identity = self._media_identity()
        stream = await super().async_create_stream()
        if not self.available or not self.is_on or self._media_identity() != identity:
            if stream is not None:
                if self.stream is stream:
                    self.stream = None
                await self._async_stop_stream(stream)
            return None
        return stream

    @staticmethod
    async def _async_stop_stream(stream: Stream) -> None:
        # stop() intentionally preserves a preloaded worker; privacy stops must
        # stop that worker too without changing the user's stored preference.
        stream.dynamic_stream_settings = replace(
            stream.dynamic_stream_settings, preload_stream=False
        )
        await stream.stop()

    @callback
    def _invalidate_media(self) -> None:
        self._media_generation += 1
        if (stream := self.stream) is not None:
            self.stream = None
            self.hass.async_create_task(self._async_stop_stream(stream))
        self._cache.clear()

    @callback
    def handle_observation(self) -> None:
        if not self.available or not self.is_on:
            self._invalidate_media()

    @callback
    def _refresh(self) -> None:
        if self._platform_state is EntityPlatformState.REMOVED:
            return
        owner = (self.session, str(self._entry.runtime_data.client.address.base_url))
        if owner != self._media_owner or not self.available or not self.is_on:
            self._invalidate_media()
        self._media_owner = owner
        super()._refresh()

    async def async_turn_on(self) -> None:
        """Enable the panel's existing camera setting through native approval."""
        await self.async_command(True)

    async def async_turn_off(self) -> None:
        """Disable capture using the same native setting as the panel UI."""
        await self.async_command(False)

    async def async_will_remove_from_hass(self) -> None:
        """Release media when the entry or entity is unloaded."""
        self._media_generation += 1
        if (stream := self.stream) is not None:
            self.stream = None
            await self._async_stop_stream(stream)
        self._cache.clear()
        await super().async_will_remove_from_hass()

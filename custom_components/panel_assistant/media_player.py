"""The panel's speaker as a media player, dormant unless native entities are on."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components import media_source
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.browse_media import (
    BrowseMedia,
    async_process_play_media_url,
)
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_ANNOUNCE,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .native import NativeEntity, async_setup_native_platform
from .transport import STATE_KNOWN, async_send_command
from .voice import panel_url

# The volume has one path, the volume channel, whose number stays beside this.
VOLUME_CHANNEL: Final = "volume"

_STATES: Final = {
    "idle": MediaPlayerState.IDLE,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the media player a panel with a speaker describes."""
    async_setup_native_platform(
        hass, entry, Platform.MEDIA_PLAYER, async_add_entities, NativeMediaPlayer
    )


class NativeMediaPlayer(NativeEntity, MediaPlayerEntity):
    """What the panel plays: media, announcements and voice replies."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    @property
    def state(self) -> MediaPlayerState | None:
        """Return what the panel reports it is doing."""
        value = self.reported_value
        return None if value is None else _STATES[value["state"]]

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the panel's media stream is muted."""
        value = self.reported_value
        return None if value is None else bool(value["muted"])

    @property
    def volume_level(self) -> float | None:
        """Return the volume channel's reported value as a fraction."""
        session = self.session
        if session is None:
            return None
        observation = session.observations.get(VOLUME_CHANNEL)
        if observation is None or observation.state != STATE_KNOWN:
            return None
        return float(observation.value) / 100

    @callback
    def _observed(self, channels: frozenset[str]) -> None:
        if VOLUME_CHANNEL in channels and self._channel not in channels:
            self.async_write_ha_state()
        super()._observed(channels)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume through the volume channel."""
        await async_send_command(
            self.hass, self.session, VOLUME_CHANNEL, round(volume * 100)
        )

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the panel's media stream."""
        await self.async_command({"action": "mute", "muted": mute})

    async def async_media_play(self) -> None:
        """Resume paused media."""
        await self.async_command({"action": "resume"})

    async def async_media_pause(self) -> None:
        """Pause the media."""
        await self.async_command({"action": "pause"})

    async def async_media_stop(self) -> None:
        """End the media and the current announcement."""
        await self.async_command({"action": "stop"})

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a URL or a media source, as media or as an announcement."""
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = item.url
        url = async_process_play_media_url(self.hass, media_id, allow_relative_url=True)
        await self.async_command(
            {
                "action": "play",
                "url": panel_url(self.hass, url),
                "announce": bool(kwargs.get(ATTR_MEDIA_ANNOUNCE)),
            }
        )

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        """Browse the media sources, audio only."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

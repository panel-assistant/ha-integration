"""The panel's Assist satellite entity (see ``voice.py`` for the wire)."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Final

from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
    async_get_pipelines,
)
from homeassistant.components.assist_satellite import (
    AssistSatelliteAnnouncement,
    AssistSatelliteConfiguration,
    AssistSatelliteEntity,
    AssistSatelliteEntityFeature,
    AssistSatelliteWakeWord,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .client import HaPaneldError
from .const import DOMAIN
from .transport import (
    CAPABILITY_VOICE,
    PanelSession,
    async_get_sessions,
    signal_session_changed,
)
from .voice import (
    EVENT_VOICE_ANNOUNCE,
    VoiceConfiguration,
    VoiceRun,
    panel_url,
    satellite_unique_id,
    satellites,
)

_LOGGER = logging.getLogger(__name__)

# How long an announcement may take to play before Home Assistant gives up.
ANNOUNCE_TIMEOUT: Final = 5 * 60.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the panel's satellite; the platform loads only once it offers voice."""
    async_add_entities([PanelAssistSatellite(entry.entry_id)])


class PanelAssistSatellite(AssistSatelliteEntity):
    """A panel that listens for its own wake words and speaks the replies.

    The panel owns its wake words and the pipeline each runs; this entity
    reads them from the panel's session and writes changes back to the
    panel's own settings, so there is one store.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        AssistSatelliteEntityFeature.ANNOUNCE
        | AssistSatelliteEntityFeature.START_CONVERSATION
    )

    def __init__(self, entry_id: str) -> None:
        """Initialize the satellite of one panel entry."""
        self._entry_id = entry_id
        self._attr_unique_id = satellite_unique_id(entry_id)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})
        self._run: VoiceRun | None = None
        # Announcements the panel has not finished, with the session each was
        # sent on: one that ends first cannot finish it.
        self._announcements: dict[str, tuple[str, asyncio.Future[bool]]] = {}
        self._continue_conversation = False

    @property
    def _session(self) -> PanelSession | None:
        session = async_get_sessions(self.hass).get(self._entry_id)
        if session is None or CAPABILITY_VOICE not in session.capabilities:
            return None
        return session

    @property
    def _voice(self) -> VoiceConfiguration | None:
        session = self._session
        return None if session is None else session.voice

    @property
    def available(self) -> bool:
        """Return whether the panel is connected with its voice assistant turned on."""
        voice = self._voice
        return voice is not None and voice.enabled

    async def async_added_to_hass(self) -> None:
        """Follow the panel's session."""
        await super().async_added_to_hass()
        satellites(self.hass)[self._entry_id] = self
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_session_changed(self._entry_id), self._session_changed
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop answering for the panel."""
        if satellites(self.hass).get(self._entry_id) is self:
            del satellites(self.hass)[self._entry_id]
        self._fail_announcements()
        await super().async_will_remove_from_hass()

    @callback
    def _session_changed(self) -> None:
        session = self._session
        self._fail_announcements(None if session is None else session.token)
        self.async_write_ha_state()

    @callback
    def _fail_announcements(self, live_token: str | None = None) -> None:
        for token, future in self._announcements.values():
            if token != live_token and not future.done():
                future.set_result(False)

    @callback
    def async_get_configuration(self) -> AssistSatelliteConfiguration:
        """Return the panel's wake words as it last reported them."""
        voice = self._voice
        if voice is None:
            return AssistSatelliteConfiguration(
                available_wake_words=[], active_wake_words=[], max_active_wake_words=1
            )
        return AssistSatelliteConfiguration(
            available_wake_words=[
                AssistSatelliteWakeWord(
                    id=word.id,
                    wake_word=word.phrase,
                    trained_languages=list(word.languages),
                )
                for word in voice.wake_words
            ],
            active_wake_words=list(voice.active),
            # Zero means no limit, but Core's selector compares against it, so
            # the limit is every wake word the panel has.
            max_active_wake_words=max(1, len(voice.wake_words)),
        )

    async def async_set_configuration(
        self, config: AssistSatelliteConfiguration
    ) -> None:
        """Write the active wake words to the panel's own settings."""
        entry = self.platform.config_entry
        assert entry is not None
        try:
            await entry.runtime_data.coordinator.client.async_set_voice_wake_words(
                list(config.active_wake_words)
            )
        except HaPaneldError as err:
            raise HomeAssistantError("The panel did not accept the wake words") from err

    @callback
    def _resolve_pipeline(self) -> str | None:
        """Return the pipeline the panel named for this turn's wake word.

        With no wake word (an announcement, or a conversation Home Assistant
        started), the first active wake word's pipeline speaks. A pipeline the
        panel names that no longer exists falls back to the preferred one.
        """
        voice = self._voice
        if voice is None:
            return None
        wake_word_id = self._run.wake_word_id if self._run is not None else None
        if wake_word_id is None and voice.active:
            wake_word_id = voice.active[0]
        pipeline_id = voice.pipelines.get(wake_word_id or "", "")
        if not pipeline_id:
            return None
        if any(p.id == pipeline_id for p in async_get_pipelines(self.hass)):
            return pipeline_id
        _LOGGER.warning(
            "Pipeline %s for wake word %s no longer exists; using the preferred one",
            pipeline_id,
            wake_word_id,
        )
        return None

    async def async_run(self, run: VoiceRun) -> None:
        """Run one conversation turn from the panel's audio."""
        voice = self._voice
        self._run = run
        self._continue_conversation = False
        try:
            await self.async_accept_pipeline_from_satellite(
                run.stream(),
                wake_word_phrase=(
                    None
                    if voice is None or run.continued
                    else voice.phrase(run.wake_word_id)
                ),
            )
        finally:
            if self._run is run:
                self._run = None
            run.send({"kind": "end"})

    @callback
    def on_pipeline_event(self, event: PipelineEvent) -> None:
        """Tell the panel when to stop listening and what to play."""
        run = self._run
        if run is None:
            return
        data = event.data or {}
        if event.type in (PipelineEventType.STT_VAD_END, PipelineEventType.STT_END):
            run.send({"kind": "listen_end"})
        elif event.type is PipelineEventType.INTENT_END:
            self._continue_conversation = bool(
                (data.get("intent_output") or {}).get("continue_conversation")
            )
        elif event.type is PipelineEventType.TTS_END:
            if tts_output := data.get("tts_output"):
                run.send(
                    {
                        "kind": "play",
                        "url": panel_url(self.hass, tts_output["url"]),
                        "continue_conversation": self._continue_conversation,
                    }
                )
        elif event.type is PipelineEventType.ERROR:
            run.send({"kind": "error", "code": str(data.get("code", "error"))})

    @callback
    def async_played(self, announce_id: str | None) -> None:
        """The panel finished playing an announcement, or a turn's reply."""
        if announce_id is None:
            self.tts_response_finished()
            return
        pending = self._announcements.get(announce_id)
        if pending is not None and not pending[1].done():
            pending[1].set_result(True)

    async def async_announce(self, announcement: AssistSatelliteAnnouncement) -> None:
        """Play an announcement on the panel."""
        await self._announce(announcement, listen_after=False)

    async def async_start_conversation(
        self, start_announcement: AssistSatelliteAnnouncement
    ) -> None:
        """Play the opening and have the panel listen for the answer."""
        await self._announce(start_announcement, listen_after=True)

    async def _announce(
        self, announcement: AssistSatelliteAnnouncement, *, listen_after: bool
    ) -> None:
        session = self._session
        if session is None:
            raise HomeAssistantError("The panel is not connected")
        announce_id = secrets.token_urlsafe(12)
        future: asyncio.Future[bool] = self.hass.loop.create_future()
        self._announcements[announce_id] = (session.token, future)
        preannounce = announcement.preannounce_media_id
        session.connection.send_message(
            event_message(
                session.subscription_id,
                {
                    "kind": EVENT_VOICE_ANNOUNCE,
                    "announce_id": announce_id,
                    "url": panel_url(self.hass, announcement.media_id),
                    "preannounce_url": (
                        panel_url(self.hass, preannounce) if preannounce else None
                    ),
                    "message": announcement.message,
                    "listen_after": listen_after,
                },
            )
        )
        try:
            async with asyncio.timeout(ANNOUNCE_TIMEOUT):
                played = await future
        except TimeoutError as err:
            raise HomeAssistantError(
                "The panel did not finish the announcement"
            ) from err
        finally:
            self._announcements.pop(announce_id, None)
        if not played:
            raise HomeAssistantError("The panel disconnected during the announcement")

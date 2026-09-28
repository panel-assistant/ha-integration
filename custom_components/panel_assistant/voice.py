"""The panel as an Assist satellite, over its own session.

The panel owns the microphone, the wake word and its wake words' pipelines; it
streams 16 kHz mono PCM16 after a wake word and plays what it is handed. Home
Assistant runs the pipeline through the satellite entity
(``assist_satellite.py``), exactly as it does for every other satellite.

Three requests and one event kind carry it, all on the panel's session:

- ``voice_configuration``: the panel reports its wake words, which are active,
  and the pipeline each one runs. Sent after hello and whenever they change.
- ``voice_run``: a subscription for one conversation turn. Its result names a
  binary handler; each binary frame is that handler's byte then PCM, and a
  frame of only that byte ends the audio. Its events tell the panel to stop
  listening (``listen_end``), what to play (``play``) and that the turn is over
  (``end``).
- ``voice_played``: the panel finished playing a reply or an announcement.
- ``voice_announce`` events on the hello subscription ask the panel to play an
  announcement, and optionally to listen afterwards.
- ``voice_colors`` events on the hello subscription give the colour of each
  wake word's pipeline, which the panel's listening overlay takes. Home
  Assistant hands every pipeline one colour, kept for all panels, so a
  pipeline looks the same wherever it answers.

A binary handler lives for one turn, never a session, because a connection has
only 255 of them.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
import yarl
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import websocket_command
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.network import is_hass_url
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .transport import (
    CAPABILITY_VOICE,
    ERR_SESSION_UNKNOWN,
    _bounded_list,
    _code,
    _plain_string,
    _session_token,
    async_get_sessions,
    signal_session_changed,
)

if TYPE_CHECKING:
    from .assist_satellite import PanelAssistSatellite

_LOGGER = logging.getLogger(__name__)

COMMAND_VOICE_CONFIGURATION: Final = f"{DOMAIN}/voice_configuration"
COMMAND_VOICE_RUN: Final = f"{DOMAIN}/voice_run"
COMMAND_VOICE_PLAYED: Final = f"{DOMAIN}/voice_played"
EVENT_VOICE_ANNOUNCE: Final = "voice_announce"
EVENT_VOICE_COLORS: Final = "voice_colors"

ERR_VOICE_UNAVAILABLE: Final = "voice_unavailable"

MAX_WAKE_WORDS: Final = 32
# A turn whose audio stops arriving without its end frame ends here, so a
# panel that vanished mid-sentence cannot hold the pipeline open.
AUDIO_IDLE_TIMEOUT: Final = 5.0
# At most this much unsent audio is held for a turn, about 20 seconds.
MAX_QUEUED_FRAMES: Final = 2_000

_DATA_SATELLITES: Final = f"{DOMAIN}_voice_satellites"
_DATA_COLORS: Final = f"{DOMAIN}_voice_colors"
_COLORS_STORAGE_KEY: Final = f"{DOMAIN}.voice_colors"
_COLORS_STORAGE_VERSION: Final = 1

# Distinct, saturated hues that read as a tint over a light or a dark
# dashboard. The first is the overlay's colour before any is assigned.
PIPELINE_COLORS: Final = (
    "#00FF88",
    "#3D8BFF",
    "#FFB020",
    "#FF4FD8",
    "#22D3EE",
    "#9B6BFF",
    "#FF6B5B",
    "#B6F03C",
)
_ANNOUNCE_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


@dataclass(frozen=True, slots=True)
class WakeWord:
    """One wake word the panel can listen for."""

    id: str
    phrase: str
    languages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VoiceConfiguration:
    """The panel's wake words, as it reported them."""

    enabled: bool
    wake_words: tuple[WakeWord, ...]
    active: tuple[str, ...]
    # Wake word ID to pipeline ID. Absent or blank means the preferred one.
    pipelines: Mapping[str, str]

    def phrase(self, wake_word_id: str | None) -> str | None:
        """Return the phrase of one wake word."""
        return next((w.phrase for w in self.wake_words if w.id == wake_word_id), None)


_WAKE_WORD_SCHEMA: Final = vol.Schema(
    {
        vol.Required("id"): _code,
        vol.Required("wake_word"): _plain_string(64),
        vol.Optional("trained_languages", default=list): _bounded_list(
            16, _plain_string(16)
        ),
    },
    extra=vol.REMOVE_EXTRA,
)


def _pipelines(value: Any) -> dict[str, str]:
    if type(value) is not dict or len(value) > MAX_WAKE_WORDS:
        raise vol.Invalid("expected a bounded map of wake word to pipeline")
    return {_code(key): _plain_string(64)(item) for key, item in value.items()}


def _configuration_consistent(msg: dict[str, Any]) -> dict[str, Any]:
    ids = [item["id"] for item in msg["wake_words"]]
    if len(set(ids)) != len(ids) or not set(msg["active"]) <= set(ids):
        raise vol.Invalid("active wake words must be among the available ones")
    return msg


VOICE_CONFIGURATION_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("type"): COMMAND_VOICE_CONFIGURATION,
            vol.Required("session"): _session_token,
            vol.Required("enabled"): bool,
            vol.Required("wake_words"): _bounded_list(
                MAX_WAKE_WORDS, _WAKE_WORD_SCHEMA
            ),
            vol.Required("active"): _bounded_list(MAX_WAKE_WORDS, _code),
            vol.Optional("pipelines", default=dict): _pipelines,
        },
        extra=vol.REMOVE_EXTRA,
    ),
    _configuration_consistent,
)

VOICE_RUN_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_VOICE_RUN,
        vol.Required("session"): _session_token,
        # The wake word that started this turn; null when it continues a
        # conversation or follows an announcement.
        vol.Optional("wake_word_id", default=None): vol.Any(None, _code),
        # A later turn of the same conversation: the wake word still names the
        # pipeline, but nobody said it again.
        vol.Optional("continued", default=False): bool,
    },
    extra=vol.REMOVE_EXTRA,
)

VOICE_PLAYED_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_VOICE_PLAYED,
        vol.Required("session"): _session_token,
        # Names an announcement; absent for a turn's reply.
        vol.Optional("announce_id"): vol.Match(_ANNOUNCE_ID_PATTERN),
    },
    extra=vol.REMOVE_EXTRA,
)


def satellites(hass: HomeAssistant) -> dict[str, PanelAssistSatellite]:
    """Return the satellite entity of each entry that has one."""
    known: dict[str, PanelAssistSatellite] = hass.data.setdefault(_DATA_SATELLITES, {})
    return known


class PipelineColors:
    """Hands each pipeline one colour and keeps it, for every panel.

    A pipeline seen for the first time takes the least used colour, so the
    first few are all different; the assignment is stored and never moves.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the assignments, loaded on first use."""
        self._store: Store[dict[str, Any]] = Store(
            hass, _COLORS_STORAGE_VERSION, _COLORS_STORAGE_KEY
        )
        self._assigned: dict[str, int] | None = None
        self._lock = asyncio.Lock()

    async def async_colors(self, pipeline_ids: Iterable[str]) -> dict[str, str]:
        """Return the colour of each pipeline, assigning any new one."""
        async with self._lock:
            if self._assigned is None:
                stored = await self._store.async_load() or {}
                self._assigned = {
                    str(pipeline_id): index
                    for pipeline_id, index in dict(stored.get("pipelines", {})).items()
                    if type(index) is int and 0 <= index < len(PIPELINE_COLORS)
                }
            assigned = self._assigned
            wanted = list(dict.fromkeys(pipeline_ids))
            fresh = [
                pipeline_id for pipeline_id in wanted if pipeline_id not in assigned
            ]
            for pipeline_id in fresh:
                used = Counter(assigned.values())
                assigned[pipeline_id] = min(
                    range(len(PIPELINE_COLORS)), key=lambda index: (used[index], index)
                )
            if fresh:
                self._store.async_delay_save(lambda: {"pipelines": dict(assigned)}, 1.0)
            return {
                pipeline_id: PIPELINE_COLORS[assigned[pipeline_id]]
                for pipeline_id in wanted
            }


def pipeline_colors(hass: HomeAssistant) -> PipelineColors:
    """Return the one colour assignment Home Assistant keeps for all panels."""
    colors: PipelineColors | None = hass.data.get(_DATA_COLORS)
    if colors is None:
        colors = hass.data[_DATA_COLORS] = PipelineColors(hass)
    return colors


def panel_url(hass: HomeAssistant, url: str) -> str:
    """Return a Home Assistant URL relative to Home Assistant.

    The panel resolves it against the address its own session reached, which
    it has just proved; Home Assistant's idea of its own address may be one
    the panel cannot reach.
    """
    parsed = yarl.URL(url)
    if parsed.is_absolute() and is_hass_url(hass, url):
        return str(parsed.relative())
    return url


@dataclass(slots=True)
class VoiceRun:
    """One conversation turn the panel is streaming."""

    connection: ActiveConnection
    msg_id: int
    wake_word_id: str | None
    continued: bool
    audio: asyncio.Queue[bytes | None]

    @callback
    def send(self, event: dict[str, Any]) -> None:
        """Send one event on the turn's subscription."""
        self.connection.send_message(event_message(self.msg_id, event))

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the turn's audio until its end frame, or until it stops."""
        while True:
            try:
                chunk = await asyncio.wait_for(self.audio.get(), AUDIO_IDLE_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("Voice audio stopped arriving; ending the turn")
                return
            if not chunk:
                return
            yield chunk


def _session_for(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> PanelAssistSatellite | None:
    session = async_get_sessions(hass).for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return None
    satellite = satellites(hass).get(session.entry_id)
    if CAPABILITY_VOICE not in session.capabilities or satellite is None:
        connection.send_error(
            msg["id"], ERR_VOICE_UNAVAILABLE, "Voice was not granted to this session."
        )
        return None
    return satellite


@callback
@websocket_command(VOICE_CONFIGURATION_SCHEMA)
def ws_voice_configuration(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Record the wake words the panel reports."""
    session = async_get_sessions(hass).for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    if CAPABILITY_VOICE not in session.capabilities:
        connection.send_error(
            msg["id"], ERR_VOICE_UNAVAILABLE, "Voice was not granted to this session."
        )
        return
    session.voice = VoiceConfiguration(
        enabled=msg["enabled"],
        wake_words=tuple(
            WakeWord(item["id"], item["wake_word"], tuple(item["trained_languages"]))
            for item in msg["wake_words"]
        ),
        active=tuple(msg["active"]),
        pipelines=msg["pipelines"],
    )
    async_get_sessions(hass).mark_changed(session)
    connection.send_result(msg["id"], {})


@callback
@websocket_command(vol.All(VOICE_RUN_SCHEMA))
def ws_voice_run(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run one conversation turn from the panel's audio."""
    if (satellite := _session_for(hass, connection, msg)) is None:
        return
    if (entry := satellite.platform.config_entry) is None:
        connection.send_error(
            msg["id"], ERR_VOICE_UNAVAILABLE, "The satellite has no entry."
        )
        return
    audio: asyncio.Queue[bytes | None] = asyncio.Queue(MAX_QUEUED_FRAMES)

    @callback
    def _on_audio(_hass: HomeAssistant, _conn: ActiveConnection, data: bytes) -> None:
        if not data:
            # The end of the audio is never dropped: without it the turn waits
            # out the idle timeout. Make room by dropping the oldest frame.
            while True:
                try:
                    audio.put_nowait(None)
                except asyncio.QueueFull:
                    audio.get_nowait()
                else:
                    return
        try:
            audio.put_nowait(data)
        except asyncio.QueueFull:
            _LOGGER.debug("Voice audio queue full; dropping a frame")

    handler_id, unregister_handler = connection.async_register_binary_handler(_on_audio)
    registered = True

    @callback
    def unregister() -> None:
        # Core frees the slot by index, and a later turn may already hold it.
        nonlocal registered
        if registered:
            registered = False
            unregister_handler()

    run = VoiceRun(connection, msg["id"], msg["wake_word_id"], msg["continued"], audio)
    task = entry.async_create_background_task(
        hass, satellite.async_run(run), f"{satellite.entity_id}_voice_run"
    )

    @callback
    def _unsubscribe() -> None:
        # The panel ended the turn, or its connection closed.
        unregister()
        task.cancel()

    @callback
    def _done(_task: asyncio.Task[None]) -> None:
        unregister()
        connection.subscriptions.pop(msg["id"], None)

    task.add_done_callback(_done)
    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"], {"handler_id": handler_id})


@callback
@websocket_command(vol.All(VOICE_PLAYED_SCHEMA))
def ws_voice_played(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Note that the panel finished playing a reply or an announcement."""
    if (satellite := _session_for(hass, connection, msg)) is None:
        return
    satellite.async_played(msg.get("announce_id"))
    connection.send_result(msg["id"], {})


@callback
def async_setup_voice(hass: HomeAssistant) -> None:
    """Register the voice requests once for the domain, never per entry."""
    websocket_api.async_register_command(hass, ws_voice_configuration)
    websocket_api.async_register_command(hass, ws_voice_run)
    websocket_api.async_register_command(hass, ws_voice_played)


def satellite_unique_id(entry_id: str) -> str:
    """The unique ID of an entry's satellite entity."""
    return f"{entry_id}_assist_satellite"


def satellite_known(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Whether this panel has been a satellite before, so its entity is restored."""
    return (
        er.async_get(hass).async_get_entity_id(
            Platform.ASSIST_SATELLITE, DOMAIN, satellite_unique_id(entry.entry_id)
        )
        is not None
    )


@callback
def async_follow_voice(
    hass: HomeAssistant, entry: ConfigEntry, platforms: list[Platform]
) -> Callable[[], None]:
    """Load the satellite platform the first time the panel offers voice.

    Loading it pulls in Home Assistant's Assist stack, which a panel that
    never offers voice should not cost anyone.
    """

    @callback
    def _changed() -> None:
        if Platform.ASSIST_SATELLITE in platforms:
            return
        session = async_get_sessions(hass).get(entry.entry_id)
        if session is None or CAPABILITY_VOICE not in session.capabilities:
            return
        platforms.append(Platform.ASSIST_SATELLITE)
        entry.async_create_task(
            hass,
            hass.config_entries.async_forward_entry_setups(
                entry, [Platform.ASSIST_SATELLITE]
            ),
            f"{DOMAIN}_voice_platform",
        )

    return async_dispatcher_connect(
        hass, signal_session_changed(entry.entry_id), _changed
    )

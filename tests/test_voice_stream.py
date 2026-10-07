"""Speech in step across panels: the Sendspin grant and the announce paths.

Every panel here is a real aiosendspin player dialling the server on Home
Assistant's own http app with the key its hello reply carried, so what a panel
receives is what a panel would hear.
"""

import asyncio
import math
from array import array
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any
from unittest.mock import patch

import av
import pytest
from aiohttp import web
from aiosendspin.client import SendspinClient
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, Roles
from aiosendspin.noise import (
    ClientPairingRecord,
    Identity,
    InMemoryClientPairingStore,
    b64url_decode,
    psk_id_for,
)
from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.http import HomeAssistantView
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store

from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.identity import CONF_INSTALL_IDENTITY
from custom_components.panel_assistant.voice_stream import (
    SENDSPIN_PATH,
    STORAGE_KEY,
    async_get_voice_stream,
    decode_clip,
)

from .test_native import panel_patches
from .test_transport import DID, HEALTH, OTHER_DID, WsClientFactory, _receive
from .test_transport_commands import Panel as NativePanel
from .test_transport_commands import (
    _call,
    native,  # noqa: F401  # the fixture
)
from .test_transport_commands import _hello as _native_hello
from .test_transport_contract import _hello_result_conforms
from .test_voice import (
    FakePipeline,
    Panel,
    _hello,
    _run,
    _satellite,
    _turn_events,
    entry,  # noqa: F401  # the fixture
    pipeline,  # noqa: F401  # the fixture
)

# aiosendspin 10.0.0 leaves a player's buffer-reset timer armed after a player
# disconnects (server/roles/player/v1.py, PlayerV1Role.on_disconnect).
LINGERING = pytest.mark.parametrize("expected_lingering_timers", [True])
BYTES_PER_SECOND = 48_000 * 2


def _clip(seconds: float, fmt: str = "wav", rate: int = 22_050) -> bytes:
    """A tone encoded the way TTS engines deliver it."""
    codec = {"mp3": "libmp3lame", "wav": "pcm_s16le"}[fmt]
    buffer = BytesIO()
    with av.open(buffer, "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=rate, layout="mono")
        count = int(seconds * rate)
        samples = array(
            "h",
            (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(count)),
        )
        pts = 0
        for start in range(0, count, 1152):
            part = samples[start : start + 1152]
            frame = av.AudioFrame(format="s16", layout="mono", samples=len(part))
            frame.planes[0].update(part.tobytes())
            frame.sample_rate = rate
            frame.pts = pts
            pts += len(part)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buffer.getvalue()


CLIPS = {
    "chime": _clip(0.2),
    "speech": _clip(0.5),
    "mp3": _clip(0.5, "mp3"),
    "long": _clip(5.0),
}


class _ClipView(HomeAssistantView):
    """Serves the clips Home Assistant's TTS would, and counts each fetch."""

    url = "/test_voice_stream/{name}"
    name = "test:voice_stream"
    requires_auth = False

    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def get(self, request: web.Request, name: str) -> web.Response:
        self.fetched.append(name)
        return web.Response(body=CLIPS[name])


async def _until(condition: Callable[[], bool], seconds: float = 5) -> None:
    """Wait a bounded time for something a real socket delivers."""
    async with asyncio.timeout(seconds):
        while True:
            if condition():
                return
            await asyncio.sleep(0.02)


@dataclass
class Speaker:
    """A panel's Sendspin voice player (sendspin-cpp on the real thing)."""

    identity: Identity = field(default_factory=Identity.generate)
    # One list per stream received: (server timestamp, bytes).
    streams: list[list[tuple[int, int]]] = field(default_factory=list)
    ends: int = 0
    client: SendspinClient | None = None

    @property
    def client_id(self) -> str:
        return self.identity.peer_id

    async def dial(self, base: str, grant: dict[str, str] | None) -> None:
        """Dial with what the hello reply granted, or as a stranger with None."""
        store = InMemoryClientPairingStore()
        server_id = None
        if grant is not None:
            psk = b64url_decode(grant["psk"])
            server_id = grant["server_id"]
            await store.store_record(
                ClientPairingRecord(psk_id_for(psk), psk, server_id)
            )
        self.client = SendspinClient(
            self.identity,
            "panel",
            [Roles.PLAYER],
            pairing_store=store,
            player_support=ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        channels=1,
                        sample_rate=48_000,
                        bit_depth=16,
                    )
                ],
                buffer_capacity=4_000_000,
            ),
            required_lead_time_ms=250,
            min_buffer_ms=250,
        )
        self.client.add_audio_chunk_listener(
            lambda ts, data, _fmt, _ahead: self.streams[-1].append((ts, len(data)))
        )
        self.client.add_stream_start_listener(lambda _msg: self.streams.append([]))
        self.client.add_stream_end_listener(lambda _reason: self._ended())
        path = SENDSPIN_PATH if grant is None else grant["path"]
        await self.client.connect(
            base.replace("http", "ws", 1) + path, expected_server_id=server_id
        )

    def _ended(self) -> None:
        self.ends += 1

    def seconds(self, index: int) -> float:
        return sum(size for _ts, size in self.streams[index]) / BYTES_PER_SECOND

    async def ended(self, count: int) -> None:
        await _until(lambda: self.ends >= count, 10)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()


@pytest.fixture
async def base(hass: HomeAssistant, hass_client_no_auth: Any) -> str:
    """Home Assistant's own http app on a local port, serving the clips too."""
    hass.data[_ClipView.name] = view = _ClipView()
    hass.http.register_view(view)
    client = await hass_client_no_auth()
    return str(client.make_url("/")).rstrip("/")


def _fetched(hass: HomeAssistant) -> list[str]:
    view: _ClipView = hass.data[_ClipView.name]
    return view.fetched


@pytest.fixture
async def speakers() -> AsyncIterator[list[Speaker]]:
    made: list[Speaker] = []
    yield made
    for speaker in made:
        await speaker.close()


async def _ready(hass: HomeAssistant, speaker: Speaker) -> None:
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None
    await _until(lambda: voice_stream.ready(speaker.client_id))


async def _voice_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    speaker: Speaker,
    *,
    did: str = DID,
) -> Panel:
    client = await hass_ws_client(hass, token)
    await client.send_json_auto_id(
        _hello(["state", "events", "voice", "voice_stream"])
        | {"did": did, "voice_stream": {"client_id": speaker.client_id}}
    )
    hello = await _receive(client)
    assert hello["success"], hello
    panel = Panel(client, hello)
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()
    return panel


async def _second_entry(hass: HomeAssistant, user_id: str) -> MockConfigEntry:
    """Load a second bound panel, beside the ``entry`` fixture's."""
    other = MockConfigEntry(
        domain=DOMAIN,
        title="beta",
        unique_id=OTHER_DID,
        data={
            CONF_ADDRESS: "beta.local",
            CONF_TRANSPORT_USER_ID: user_id,
            CONF_INSTALL_IDENTITY: True,
        },
    )
    other.add_to_hass(hass)

    async def health(client: Any) -> Any:
        if "beta" in str(client.address.stored_value):
            return replace(HEALTH, discovery_id=OTHER_DID)
        return HEALTH

    with (
        panel_patches(),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            health,
        ),
    ):
        assert await hass.config_entries.async_setup(other.entry_id)
        await hass.async_block_till_done()
    return other


async def _event(panel: Panel) -> dict[str, Any]:
    message = await panel.receive()
    assert message["id"] == panel.subscription, message
    event: dict[str, Any] = message["event"]
    assert event["kind"] == "voice_announce", event
    return event


async def _played(panel: Panel, announce_id: str | None = None) -> None:
    message: dict[str, Any] = {
        "type": "panel_assistant/voice_played",
        "session": panel.token,
    }
    if announce_id is not None:
        message["announce_id"] = announce_id
    assert (await panel.send(message))["success"]


# ---------------------------------------------------------------------------
# The hello grant.


async def test_hello_grants_the_stream_and_hands_the_same_key_every_time(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    hass_storage: dict[str, Any],
) -> None:
    """A missed reply never locks a panel out: every hello gets the same key."""
    speaker = Speaker()
    first = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, speaker
    )
    second = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, speaker
    )

    assert "voice_stream" in first.result["capabilities"]
    grant = first.result["voice_stream"]
    assert grant["path"] == "/api/panel_assistant/sendspin"
    assert len(grant["server_id"]) == 43
    assert len(b64url_decode(grant["psk"])) == 32
    assert second.result["voice_stream"] == grant
    # Exactly the reply the shared conformance vectors describe.
    _hello_result_conforms(first.result, ["state", "events", "voice", "voice_stream"])
    # The key outlives the process: it is stored, owned by the panel's entry.
    await flush_store(async_get_voice_stream(hass)._pairing._ha_store)
    record = hass_storage[STORAGE_KEY]["data"]["records"][speaker.client_id]
    assert record["psk"] == grant["psk"]
    assert record["owner"] == entry.entry_id


async def test_a_panel_offering_no_key_is_not_granted_the_stream(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """Without its Sendspin key a panel keeps playing by URL."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_hello(["state", "events", "voice_stream"]))
    hello = await _receive(client)
    assert hello["success"], hello
    assert "voice_stream" not in hello["result"]["capabilities"]
    assert "voice_stream" not in hello["result"]


# ---------------------------------------------------------------------------
# Announcements.


@LINGERING
async def test_one_announcement_to_two_panels_is_one_stream_in_step(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
) -> None:
    """Both panels get the same samples at the same server times, fetched once."""
    other = await _second_entry(hass, hass_read_only_user.id)
    a, b = Speaker(), Speaker()
    speakers += [a, b]
    panel_a = await _voice_panel(hass, hass_ws_client, hass_read_only_access_token, a)
    panel_b = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, b, did=OTHER_DID
    )
    await a.dial(base, panel_a.result["voice_stream"])
    await b.dial(base, panel_b.result["voice_stream"])
    await _ready(hass, a)
    await _ready(hass, b)

    call = asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {
                "entity_id": [_satellite(hass, entry), _satellite(hass, other)],
                "media_id": f"{base}/test_voice_stream/mp3",
                "preannounce_media_id": f"{base}/test_voice_stream/chime",
            },
            blocking=True,
        )
    )
    event_a, event_b = await _event(panel_a), await _event(panel_b)
    assert event_a["stream"] is True
    assert event_b["stream"] is True
    # The URL stays, for a panel that cannot take the stream.
    assert event_a["url"] == f"{base}/test_voice_stream/mp3"
    await a.ended(1)
    await b.ended(1)

    assert len(a.streams) == len(b.streams) == 1
    assert a.streams[0]
    assert a.streams[0] == b.streams[0]
    # Chime and speech back to back in the one stream, each fetched once.
    expected = decode_clip(CLIPS["chime"]) + decode_clip(CLIPS["mp3"])
    assert a.seconds(0) == len(expected) / BYTES_PER_SECOND
    assert sorted(_fetched(hass)) == ["chime", "mp3"]

    # Home Assistant still waits for each panel to say it has played.
    await asyncio.sleep(0)
    assert not call.done()
    await _played(panel_a, event_a["announce_id"])
    await _played(panel_b, event_b["announce_id"])
    async with asyncio.timeout(5):
        await call


@LINGERING
@pytest.mark.parametrize("shared", [True, False])
async def test_calls_of_one_context_arriving_apart_still_play_in_step(
    shared: bool,
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
) -> None:
    """A script's parallel branches share a context and arrive tens of ms apart.

    Two unrelated automations saying the same words at once do not merge.
    """
    other = await _second_entry(hass, hass_read_only_user.id)
    a, b = Speaker(), Speaker()
    speakers += [a, b]
    panel_a = await _voice_panel(hass, hass_ws_client, hass_read_only_access_token, a)
    panel_b = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, b, did=OTHER_DID
    )
    await a.dial(base, panel_a.result["voice_stream"])
    await b.dial(base, panel_b.result["voice_stream"])
    await _ready(hass, a)
    await _ready(hass, b)
    shared_context = Context()

    def announce(entity_id: str | None) -> asyncio.Future[Any]:
        return asyncio.ensure_future(
            hass.services.async_call(
                "assist_satellite",
                "announce",
                {
                    "entity_id": entity_id,
                    "media_id": f"{base}/test_voice_stream/speech",
                    "preannounce": False,
                },
                blocking=True,
                context=shared_context if shared else Context(),
            )
        )

    first = announce(_satellite(hass, entry))
    await asyncio.sleep(0.05)
    second = announce(_satellite(hass, other))
    event_a, event_b = await _event(panel_a), await _event(panel_b)
    assert event_a["stream"] is event_b["stream"] is True
    await a.ended(1)
    await b.ended(1)

    assert len(a.streams) == len(b.streams) == 1
    assert a.streams[0]
    assert (a.streams[0] == b.streams[0]) is shared
    await _played(panel_a, event_a["announce_id"])
    await _played(panel_b, event_b["announce_id"])
    async with asyncio.timeout(5):
        await asyncio.gather(first, second)


@LINGERING
async def test_a_panel_whose_player_is_not_ready_gets_the_announcement_by_url(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
) -> None:
    """The ready panel streams; the other gets today's event, unchanged."""
    other = await _second_entry(hass, hass_read_only_user.id)
    a, b = Speaker(), Speaker()
    speakers += [a]
    panel_a = await _voice_panel(hass, hass_ws_client, hass_read_only_access_token, a)
    panel_b = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, b, did=OTHER_DID
    )
    await a.dial(base, panel_a.result["voice_stream"])
    await _ready(hass, a)  # b never dials

    call = asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {
                "entity_id": [_satellite(hass, entry), _satellite(hass, other)],
                "media_id": f"{base}/test_voice_stream/speech",
                "preannounce": False,
            },
            blocking=True,
        )
    )
    event_a, event_b = await _event(panel_a), await _event(panel_b)
    assert event_a["stream"] is True
    assert set(event_b) == {
        "kind",
        "announce_id",
        "url",
        "preannounce_url",
        "message",
        "listen_after",
    }
    assert event_b["url"] == f"{base}/test_voice_stream/speech"
    await a.ended(1)
    await _played(panel_a, event_a["announce_id"])
    await _played(panel_b, event_b["announce_id"])
    async with asyncio.timeout(5):
        await call


@LINGERING
async def test_a_stranger_with_no_key_from_hello_cannot_play(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
) -> None:
    """A player that was never handed a key connects but is never streamed to."""
    stranger = Speaker()
    speakers.append(stranger)
    panel = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, stranger
    )
    # It dials as the panel would, but without the key the hello reply carried.
    await stranger.dial(base, None)
    await asyncio.sleep(0.5)  # long past the 0.2 s a keyed player needs

    call = asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {
                "entity_id": _satellite(hass, entry),
                "media_id": f"{base}/test_voice_stream/speech",
                "preannounce": False,
            },
            blocking=True,
        )
    )
    event = await _event(panel)
    assert "stream" not in event
    await _played(panel, event["announce_id"])
    async with asyncio.timeout(5):
        await call
    assert stranger.streams == []


@LINGERING
async def test_removing_the_entry_revokes_its_panels_key(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
    hass_storage: dict[str, Any],
) -> None:
    """The panel's player is dropped, and its key no longer admits it."""
    speaker = Speaker()
    speakers.append(speaker)
    panel = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, speaker
    )
    grant = panel.result["voice_stream"]
    await speaker.dial(base, grant)
    await _ready(hass, speaker)
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    await _until(lambda: speaker.client is not None and not speaker.client.connected)
    await flush_store(voice_stream._pairing._ha_store)
    assert hass_storage[STORAGE_KEY]["data"]["records"] == {}
    # Dialling again with the old key: connected, never playable.
    await speaker.close()
    await speaker.dial(base, grant)
    await asyncio.sleep(0.5)
    assert not voice_stream.ready(speaker.client_id)


# ---------------------------------------------------------------------------
# Voice replies.


@LINGERING
async def test_a_reply_streams_to_the_answering_panel_only(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    speakers: list[Speaker],
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    """The panel that heard the wake word hears the reply; the other hears nothing."""
    await _second_entry(hass, hass_read_only_user.id)
    a, b = Speaker(), Speaker()
    speakers += [a, b]
    panel_a = await _voice_panel(hass, hass_ws_client, hass_read_only_access_token, a)
    panel_b = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, b, did=OTHER_DID
    )
    await a.dial(base, panel_a.result["voice_stream"])
    await b.dial(base, panel_b.result["voice_stream"])
    await _ready(hass, a)
    await _ready(hass, b)
    pipeline.events[-2] = PipelineEvent(
        PipelineEventType.TTS_END,
        {"tts_output": {"url": f"{base}/test_voice_stream/speech"}},
    )

    run_id, handler = await _run(panel_a, "hey_jarvis")
    await panel_a.client.send_bytes(bytes([handler]))
    events = await _turn_events(panel_a, run_id)

    assert events[-2:] == [
        {
            "kind": "play",
            "url": f"{base}/test_voice_stream/speech",
            "continue_conversation": True,
            "stream": True,
        },
        {"kind": "end"},
    ]
    await a.ended(1)
    assert a.seconds(0) == len(decode_clip(CLIPS["speech"])) / BYTES_PER_SECOND
    assert b.streams == []
    await _played(panel_a)


# ---------------------------------------------------------------------------
# The media player's announcements, and one cutting another off.


@LINGERING
async def test_a_newer_media_announcement_cuts_the_older_stream_off(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    base: str,
    speakers: list[Speaker],
) -> None:
    """The first stream ends at once; the newer one plays whole."""
    speaker = Speaker()
    speakers.append(speaker)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        _native_hello(["state", "events", "commands", "approval", "voice_stream"])
        | {"voice_stream": {"client_id": speaker.client_id}}
    )
    hello = await _receive(client)
    assert hello["success"], hello
    panel = NativePanel(client, hello)
    await panel.sync(hass)
    await speaker.dial(base, hello["result"]["voice_stream"])
    await _ready(hass, speaker)
    from .test_media_player import _player

    async def announce(name: str) -> dict[str, Any]:
        call = _call(
            hass,
            "media_player",
            "play_media",
            {
                "entity_id": _player(hass),
                "media_content_id": f"{base}/test_voice_stream/{name}",
                "media_content_type": "music",
                "announce": True,
            },
        )
        command = await panel.command()
        assert (await panel.answer(command["command_id"], "applied"))["success"]
        async with asyncio.timeout(5):
            await call
        value: dict[str, Any] = command["value"]
        return value

    first = await announce("long")
    assert first == {
        "action": "play",
        "url": f"{base}/test_voice_stream/long",
        "announce": True,
        "stream": True,
    }
    await _until(lambda: bool(speaker.streams and speaker.streams[0]))
    second = await announce("speech")
    assert second["stream"] is True
    await speaker.ended(2)

    assert len(speaker.streams) == 2
    assert speaker.seconds(0) < 4.0  # of 5 s
    assert speaker.seconds(1) == len(decode_clip(CLIPS["speech"])) / BYTES_PER_SECOND

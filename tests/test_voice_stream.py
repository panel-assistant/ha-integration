"""Speech in step across panels: Sendspin inside each panel's session.

Every panel here is a real aiosendspin player whose WebSocket is its Panel
Assistant session: what it sends goes as ``voice_stream_frame`` commands over
``hass_ws_client``, and what it receives is the ``sendspin`` events of its hello
subscription. So what a panel receives, and in what order, is what a panel
would hear.
"""

import asyncio
import base64
import math
from array import array
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Any
from unittest.mock import patch

import av
import pytest
from aiohttp import WSMessage, WSMsgType, web
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.http import HomeAssistantView
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store

from custom_components.panel_assistant import voice_stream as voice_stream_module
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.identity import CONF_INSTALL_IDENTITY
from custom_components.panel_assistant.voice_stream import (
    STORAGE_KEY,
    Listener,
    async_get_voice_stream,
    decode_clips,
)

from .test_native import panel_patches
from .test_transport import DID, HEALTH, OTHER_DID, WsClientFactory, _receive
from .test_transport_commands import Panel as NativePanel
from .test_transport_commands import (
    _call,
    native,  # noqa: F401  # the fixture
)
from .test_transport_commands import _hello as _native_hello
from .test_transport_contract import (
    _hello_result_conforms,
    _voice_stream_event_conforms,
)
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
CAPABILITY = "voice_stream_session"
NATIVE_RATE = 22_050


def _clip(seconds: float, fmt: str = "wav", rate: int = NATIVE_RATE) -> bytes:
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
    "long": _clip(8.0),
    "narrow": _clip(0.5, rate=16_000),
}


def _pcm(*names: str) -> bytes:
    pcm, _rate = decode_clips(CLIPS[name] for name in names)
    return pcm


def _whole(pcm: bytes) -> Any:
    """The seconds a player hears of ``pcm`` played whole, to within 10 ms.

    The server's converter holds back a few samples (under 5 ms in 8 s).
    """
    return pytest.approx(len(pcm) / 2 / NATIVE_RATE, abs=0.01)


class _ClipView(HomeAssistantView):
    """Serves the clips Home Assistant's TTS would, and counts each fetch."""

    url = "/test_voice_stream/{name}"
    name = "test:voice_stream"
    requires_auth = False

    def __init__(self) -> None:
        self.fetched: list[str] = []
        # A clip named here is served only once its event is set.
        self.held: dict[str, asyncio.Event] = {}

    async def get(self, request: web.Request, name: str) -> web.Response:
        self.fetched.append(name)
        if (hold := self.held.get(name)) is not None:
            await hold.wait()
        return web.Response(body=CLIPS[name])


async def _until(condition: Callable[[], bool], seconds: float = 5) -> None:
    """Wait a bounded time for something a real socket delivers."""
    async with asyncio.timeout(seconds):
        while True:
            if condition():
                return
            await asyncio.sleep(0.02)


class Wire:
    """A panel's session connection, read by one pump as the app reads it.

    ``sendspin`` events go to the panel's Sendspin socket and
    ``voice_stream_end`` events to ``ends``; every other message waits for the
    test. ``timeline`` keeps every message in arrival order.
    """

    def __init__(self, client: Any, subscription: int, token: str) -> None:
        self.client = client
        self.subscription = subscription
        self.token = token
        self.timeline: list[dict[str, Any]] = []
        self.ends: list[dict[str, Any]] = []
        self.frame_bytes = 0
        self.socket: PanelSocket | None = None
        self._frames: set[int] = set()
        self._failed: list[dict[str, Any]] = []
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._receive = client.receive_json
        client.receive_json = self._inbox.get
        self.task = asyncio.ensure_future(self._pump())

    async def _pump(self) -> None:
        while True:
            message: dict[str, Any] = await self._receive()
            self.timeline.append(message)
            if message.get("type") == "result" and message["id"] in self._frames:
                self._frames.discard(message["id"])
                # Frames still in flight when the session ends are refused.
                if not message["success"] and (
                    message["error"]["code"] != "session_unknown"
                ):
                    self._failed.append(message)
                continue
            event = message.get("event") or {}
            if message.get("id") == self.subscription and event.get("kind") in (
                "sendspin",
                "voice_stream_end",
            ):
                _voice_stream_event_conforms(event)
                if event["kind"] == "voice_stream_end":
                    self.ends.append(event)
                else:
                    assert self.socket is not None
                    self.frame_bytes += len(event["frame"])
                    self.socket.deliver(event, len(self.timeline) - 1)
                continue
            if event.get("kind") == "session_closed" and self.socket is not None:
                # The panel's Sendspin connection ends with its session.
                await self.socket.close()
            self._inbox.put_nowait(message)

    async def send_frame(self, data: bytes, *, text: bool) -> None:
        message = {
            "type": "panel_assistant/voice_stream_frame",
            "session": self.token,
            "frame": base64.b64encode(data).decode(),
            "text": text,
        }
        sending = self.client.send_json_auto_id(message)
        self._frames.add(message["id"])
        await sending

    def first(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        """The timeline index of the first message ``predicate`` picks."""
        return next(i for i, m in enumerate(self.timeline) if predicate(m))

    def frames(self, stream_id: str | None) -> list[int]:
        """Timeline indices of the frames labelled ``stream_id``."""
        return [
            i
            for i, m in enumerate(self.timeline)
            if (m.get("event") or {}).get("kind") == "sendspin"
            and m["event"]["stream_id"] == stream_id
        ]

    def end_index(self, stream_id: str) -> int:
        return self.first(
            lambda m: (
                (m.get("event") or {}).get("kind") == "voice_stream_end"
                and m["event"]["stream_id"] == stream_id
            )
        )

    async def close(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        assert not self._failed, self._failed


class PanelSocket:
    """The panel's end of its Sendspin connection inside the session."""

    def __init__(self, wire: Wire) -> None:
        self._wire = wire
        self._inbox: asyncio.Queue[tuple[WSMessage, str | None, int]] = asyncio.Queue()
        self._closed = False
        # The label and timeline index of the frame the player is reading.
        self.label: str | None = None
        self.index = -1

    def deliver(self, event: dict[str, Any], index: int) -> None:
        data = base64.b64decode(event["frame"])
        message = (
            WSMessage(WSMsgType.TEXT, data.decode(), "")
            if event["text"]
            else WSMessage(WSMsgType.BINARY, data, "")
        )
        self._inbox.put_nowait((message, event["stream_id"], index))

    async def receive(self) -> WSMessage:
        if self._closed:
            return WSMessage(WSMsgType.CLOSED, None, "")
        message, self.label, self.index = await self._inbox.get()
        return message

    async def send_str(self, data: str) -> None:
        await self._wire.send_frame(data.encode(), text=True)

    async def send_bytes(self, data: bytes) -> None:
        await self._wire.send_frame(data, text=False)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_code(self) -> int | None:
        return 1000 if self._closed else None

    def exception(self) -> BaseException | None:
        return None

    async def close(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        self._inbox.put_nowait((WSMessage(WSMsgType.CLOSED, None, ""), None, -1))
        return True


class _Dialler:
    """The client session a player dials with: it hands over the session socket."""

    closed = False

    def __init__(self, socket: PanelSocket) -> None:
        self._socket = socket

    async def ws_connect(self, _url: str, **_kwargs: Any) -> PanelSocket:
        return self._socket

    async def close(self) -> None:
        return None


@dataclass
class Heard:
    """One stream as a player received it."""

    label: str | None  # the stream_id on the frame that carried stream/start
    index: int  # that frame's place in the session's timeline
    # (server timestamp, bytes, stream_id of the carrying frame) per chunk.
    chunks: list[tuple[int, int, str | None]] = field(default_factory=list)
    # The label and timeline index of the frame that carried stream/end.
    end: tuple[str | None, int] | None = None


@dataclass
class Speaker:
    """A panel's Sendspin voice player (sendspin-cpp on the real thing)."""

    identity: Identity = field(default_factory=Identity.generate)
    rate: int = NATIVE_RATE
    channels: int = 1
    streams: list[Heard] = field(default_factory=list)
    ends: int = 0
    client: SendspinClient | None = None
    socket: PanelSocket | None = None

    @property
    def client_id(self) -> str:
        return self.identity.peer_id

    @property
    def bytes_per_second(self) -> int:
        return self.rate * 2 * self.channels

    async def connect(self, wire: Wire, grant: dict[str, str] | None) -> None:
        """Connect inside the session with what hello granted, or as a stranger."""
        self.socket = socket = wire.socket = PanelSocket(wire)
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
            session=_Dialler(socket),  # type: ignore[arg-type]
            pairing_store=store,
            player_support=ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        channels=self.channels,
                        sample_rate=self.rate,
                        bit_depth=16,
                    )
                ],
                buffer_capacity=4_000_000,
            ),
            required_lead_time_ms=250,
            min_buffer_ms=250,
        )
        self.client.add_audio_chunk_listener(
            lambda ts, data, _fmt, _ahead: self.streams[-1].chunks.append(
                (ts, len(data), socket.label)
            )
        )
        self.client.add_stream_start_listener(
            lambda _msg: self.streams.append(Heard(socket.label, socket.index))
        )
        self.client.add_stream_end_listener(lambda _reason: self._ended(socket))
        await self.client.connect("ws://session", expected_server_id=server_id)

    def _ended(self, socket: PanelSocket) -> None:
        self.ends += 1
        if self.streams and self.streams[-1].end is None:
            self.streams[-1].end = (socket.label, socket.index)

    def start(self, index: int) -> int:
        """The server timestamp of a stream's first chunk."""
        return self.streams[index].chunks[0][0]

    def timing(self, index: int) -> list[tuple[int, int]]:
        return [(ts, size) for ts, size, _label in self.streams[index].chunks]

    def seconds(self, index: int) -> float:
        heard = self.streams[index].chunks
        return sum(size for _ts, size, _label in heard) / self.bytes_per_second

    async def ended(self, count: int) -> None:
        await _until(lambda: self.ends >= count, 10)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()


@pytest.fixture
async def base(hass: HomeAssistant, hass_client_no_auth: Any) -> str:
    """Home Assistant's own http app on a local port, serving the clips."""
    hass.data[_ClipView.name] = view = _ClipView()
    hass.http.register_view(view)
    client = await hass_client_no_auth()
    return str(client.make_url("/")).rstrip("/")


def _fetched(hass: HomeAssistant) -> list[str]:
    view: _ClipView = hass.data[_ClipView.name]
    return view.fetched


@pytest.fixture
async def wires() -> AsyncIterator[list[tuple[Wire, Speaker]]]:
    made: list[tuple[Wire, Speaker]] = []
    yield made
    for wire, speaker in made:
        await speaker.close()
        await wire.close()


async def _ready(hass: HomeAssistant, speaker: Speaker) -> None:
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None
    await _until(lambda: voice_stream.ready(speaker.client_id))


def _voice_hello(speaker: Speaker, did: str = DID) -> dict[str, Any]:
    return _hello(["state", "events", "voice", CAPABILITY]) | {
        "did": did,
        CAPABILITY: {"client_id": speaker.client_id},
    }


async def _voice_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    wires: list[tuple[Wire, Speaker]],
    speaker: Speaker | None = None,
    *,
    did: str = DID,
    hello: dict[str, Any] | None = None,
    connect: bool = True,
) -> tuple[Panel, Wire, Speaker]:
    """A voice panel whose player connects inside its session, keyed by hello."""
    speaker = speaker or Speaker()
    client = await hass_ws_client(hass, token)
    await client.send_json_auto_id(hello or _voice_hello(speaker, did))
    reply = await _receive(client)
    assert reply["success"], reply
    panel = Panel(client, reply)
    wire = Wire(client, panel.subscription, panel.token)
    wires.append((wire, speaker))
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()
    if connect:
        await speaker.connect(wire, panel.result[CAPABILITY])
        await _ready(hass, speaker)
    return panel, wire, speaker


async def _second_entry(
    hass: HomeAssistant, user_id: str, did: str = OTHER_DID, name: str = "beta"
) -> MockConfigEntry:
    """Load another bound panel, beside the ``entry`` fixture's."""
    other = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        unique_id=did,
        data={
            CONF_ADDRESS: f"{name}.local",
            CONF_TRANSPORT_USER_ID: user_id,
            CONF_INSTALL_IDENTITY: True,
        },
    )
    other.add_to_hass(hass)

    async def health(client: Any) -> Any:
        if name in str(client.address.stored_value):
            return replace(HEALTH, discovery_id=did)
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
    _voice_stream_event_conforms(event)
    return event


async def _played(panel: Panel, announce_id: str | None = None) -> None:
    message: dict[str, Any] = {
        "type": "panel_assistant/voice_played",
        "session": panel.token,
    }
    if announce_id is not None:
        message["announce_id"] = announce_id
    assert (await panel.send(message))["success"]


def _announce(
    hass: HomeAssistant, entity_ids: Any, base: str, **fields: Any
) -> asyncio.Future[Any]:
    return asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {"entity_id": entity_ids, **fields},
            blocking=True,
        )
    )


def _is_event(stream_id: str) -> Callable[[dict[str, Any]], bool]:
    """Pick the message that told the panel to play ``stream_id``."""

    def pick(message: dict[str, Any]) -> bool:
        event = message.get("event") or {}
        value = event.get("value") if event.get("kind") == "command" else event
        return (
            isinstance(value, dict)
            and value.get("stream_id") == stream_id
            and (value.get("stream") is True)
        )

    return pick


def _heard_in_order(wire: Wire, speaker: Speaker, index: int, stream_id: str) -> None:
    """The play message, then the stream's frames, then its end, on one session."""
    heard = speaker.streams[index]
    told = wire.first(_is_event(stream_id))
    # The frame that carried stream/start came after the message that said
    # play, and it and every chunk carry the stream's id.
    assert heard.label == stream_id
    assert told < heard.index
    assert {label for _ts, _size, label in heard.chunks} == {stream_id}
    # The stream's own end reached the player before voice_stream_end did.
    assert heard.end is not None
    assert heard.end[0] == stream_id
    assert heard.end[1] < wire.end_index(stream_id)
    assert all(told < i < wire.end_index(stream_id) for i in wire.frames(stream_id))


# ---------------------------------------------------------------------------
# The hello grant.


async def test_hello_grants_the_session_stream_and_hands_the_same_key_every_time(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    hass_storage: dict[str, Any],
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """A missed reply never locks a panel out: every hello gets the same key."""
    speaker = Speaker()
    token = hass_read_only_access_token
    first, _, _ = await _voice_panel(
        hass, hass_ws_client, token, wires, speaker, connect=False
    )
    second, _, _ = await _voice_panel(
        hass, hass_ws_client, token, wires, speaker, connect=False
    )

    assert CAPABILITY in first.result["capabilities"]
    grant = first.result[CAPABILITY]
    assert set(grant) == {"server_id", "psk"}
    assert len(grant["server_id"]) == 43
    assert len(b64url_decode(grant["psk"])) == 32
    assert second.result[CAPABILITY] == grant
    # Exactly the reply the shared conformance vectors describe.
    _hello_result_conforms(first.result, ["state", "events", "voice", CAPABILITY])
    # The key outlives the process: it is stored, owned by the panel's entry.
    await flush_store(async_get_voice_stream(hass)._pairing._ha_store)
    record = hass_storage[STORAGE_KEY]["data"]["records"][speaker.client_id]
    assert record["psk"] == grant["psk"]
    assert record["owner"] == entry.entry_id


@pytest.mark.parametrize(
    "offer",
    [
        pytest.param({"capabilities": [CAPABILITY]}, id="no_key"),
        pytest.param(
            {
                "capabilities": ["voice_stream"],
                "voice_stream": {"client_id": "B" * 43},
            },
            id="the_old_separate_endpoint",
        ),
        pytest.param(
            {
                "capabilities": ["voice_stream"],
                CAPABILITY: {"client_id": Identity.generate().peer_id},
            },
            id="the_old_capability_with_a_key",
        ),
    ],
)
async def test_a_panel_without_a_session_stream_offer_is_not_granted_one(
    offer: dict[str, Any],
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """Without its key, or offering only v1's endpoint, a panel plays by URL."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    hello = _hello(["state", "events", *offer["capabilities"]])
    await client.send_json_auto_id(
        hello | {k: v for k, v in offer.items() if k != "capabilities"}
    )
    reply = await _receive(client)
    assert reply["success"], reply
    assert not {CAPABILITY, "voice_stream"} & set(reply["result"]["capabilities"])
    assert not {CAPABILITY, "voice_stream"} & set(reply["result"])


async def test_a_session_without_the_grant_cannot_send_frames(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """The frame and stop commands answer only a session granted the stream."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_hello(["state", "events", "voice"]))
    reply = await _receive(client)
    token = reply["result"]["session"]
    for message in (
        {"type": "panel_assistant/voice_stream_frame", "frame": "AA==", "text": False},
        {"type": "panel_assistant/voice_stream_stop", "stream_id": "abc"},
    ):
        await client.send_json_auto_id(message | {"session": token})
        answer = await _receive(client)
        assert answer["error"]["code"] == "voice_stream_unavailable", answer


# ---------------------------------------------------------------------------
# Announcements.


@LINGERING
async def test_a_granted_panel_connects_pairs_and_plays_inside_its_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """Told first, then the stream, then its end; finished without voice_played."""
    panel, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    call = _announce(
        hass,
        _satellite(hass, entry),
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce=False,
    )
    event = await _event(panel)
    assert event["stream"] is True
    stream_id = event["stream_id"]
    # Home Assistant's announcement finishes when the stream drains.
    async with asyncio.timeout(10):
        await call
    await speaker.ended(1)

    assert wire.ends == [
        {
            "kind": "voice_stream_end",
            "stream_id": stream_id,
            "outcome": "played",
            "listen_after": False,
        }
    ]
    assert len(speaker.streams) == 1
    _heard_in_order(wire, speaker, 0, stream_id)
    # Speech at its own rate, whole.
    assert speaker.seconds(0) == _whole(_pcm("speech"))


@LINGERING
async def test_one_announcement_to_two_panels_is_one_stream_in_step(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """Both panels get the same samples at the same server times, fetched once."""
    other = await _second_entry(hass, hass_read_only_user.id)
    token = hass_read_only_access_token
    panel_a, wire_a, a = await _voice_panel(hass, hass_ws_client, token, wires)
    panel_b, wire_b, b = await _voice_panel(
        hass, hass_ws_client, token, wires, did=OTHER_DID
    )

    call = _announce(
        hass,
        [_satellite(hass, entry), _satellite(hass, other)],
        base,
        media_id=f"{base}/test_voice_stream/mp3",
        preannounce_media_id=f"{base}/test_voice_stream/chime",
    )
    event_a, event_b = await _event(panel_a), await _event(panel_b)
    assert event_a["stream"] is event_b["stream"] is True
    assert event_a["stream_id"] == event_b["stream_id"]
    # The URL stays beside the stream.
    assert event_a["url"] == f"{base}/test_voice_stream/mp3"
    async with asyncio.timeout(10):
        await call
    await a.ended(1)
    await b.ended(1)

    assert len(a.streams) == len(b.streams) == 1
    assert a.timing(0)
    assert a.timing(0) == b.timing(0)
    _heard_in_order(wire_a, a, 0, event_a["stream_id"])
    _heard_in_order(wire_b, b, 0, event_b["stream_id"])
    # Chime and speech back to back in the one stream, each fetched once.
    assert a.seconds(0) == _whole(_pcm("chime", "mp3"))
    assert sorted(_fetched(hass)) == ["chime", "mp3"]
    assert [end["outcome"] for end in wire_a.ends + wire_b.ends] == ["played"] * 2


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
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """A script's parallel branches share a context and arrive tens of ms apart.

    Two unrelated automations saying the same words at once do not merge.
    """
    other = await _second_entry(hass, hass_read_only_user.id)
    token = hass_read_only_access_token
    panel_a, _, a = await _voice_panel(hass, hass_ws_client, token, wires)
    panel_b, _, b = await _voice_panel(
        hass, hass_ws_client, token, wires, did=OTHER_DID
    )
    shared_context = Context()

    def announce(entity_id: str | None, signature: str) -> asyncio.Future[Any]:
        return asyncio.ensure_future(
            hass.services.async_call(
                "assist_satellite",
                "announce",
                {
                    "entity_id": entity_id,
                    "media_id": f"{base}/test_voice_stream/speech",
                    # Each entity signs the chime's path itself, in its own second.
                    "preannounce_media_id": (
                        f"{base}/test_voice_stream/chime?authSig={signature}"
                    ),
                },
                blocking=True,
                context=shared_context if shared else Context(),
            )
        )

    first = announce(_satellite(hass, entry), "first")
    await asyncio.sleep(0.05)
    second = announce(_satellite(hass, other), "second")
    event_a, event_b = await _event(panel_a), await _event(panel_b)
    assert event_a["stream"] is event_b["stream"] is True
    async with asyncio.timeout(10):
        await asyncio.gather(first, second)
    await a.ended(1)
    await b.ended(1)

    assert len(a.streams) == len(b.streams) == 1
    assert a.timing(0)
    assert (a.timing(0) == b.timing(0)) is shared
    assert (event_a["stream_id"] == event_b["stream_id"]) is shared


@LINGERING
@pytest.mark.parametrize("granted", [True, False])
async def test_a_panel_that_cannot_take_the_stream_gets_the_announcement_by_url(
    granted: bool,
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """The ready panel streams; the other gets today's event and reports played.

    The other is granted the stream but never connects its player, or is not
    granted it at all.
    """
    other = await _second_entry(hass, hass_read_only_user.id)
    token = hass_read_only_access_token
    panel_a, _, a = await _voice_panel(hass, hass_ws_client, token, wires)
    b = Speaker()
    panel_b, wire_b, _ = await _voice_panel(
        hass,
        hass_ws_client,
        token,
        wires,
        b,
        did=OTHER_DID,
        connect=False,
        hello=None
        if granted
        else _hello(["state", "events", "voice"]) | {"did": OTHER_DID},
    )
    assert (CAPABILITY in panel_b.result["capabilities"]) is granted

    call = _announce(
        hass,
        [_satellite(hass, entry), _satellite(hass, other)],
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce=False,
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
    await asyncio.sleep(0)
    # Only the panel that played by URL is still waited for.
    assert not call.done()
    await _played(panel_b, event_b["announce_id"])
    async with asyncio.timeout(5):
        await call
    assert wire_b.ends == []


@LINGERING
async def test_a_stranger_with_no_key_from_hello_cannot_play(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """A player that was never handed the key connects but is never streamed to."""
    panel, wire, stranger = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires, connect=False
    )
    # It connects inside the session, but without the key the reply carried.
    await stranger.connect(wire, None)
    await asyncio.sleep(0.5)  # long past the 0.2 s a keyed player needs

    call = _announce(
        hass,
        _satellite(hass, entry),
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce=False,
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
    wires: list[tuple[Wire, Speaker]],
    hass_storage: dict[str, Any],
) -> None:
    """The panel's player is dropped with its session, and its key forgotten."""
    _, _, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    await _until(lambda: speaker.client is not None and not speaker.client.connected)
    assert not voice_stream.ready(speaker.client_id)
    await flush_store(voice_stream._pairing._ha_store)
    assert hass_storage[STORAGE_KEY]["data"]["records"] == {}


@LINGERING
async def test_a_panels_sendspin_connection_ends_with_its_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """A dropped session leaves no Sendspin player behind to be streamed to."""
    panel, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None

    # The connection drops: no session_closed reaches the panel.
    await panel.client.close()
    assert wire.socket is not None
    await wire.socket.close()

    await _until(lambda: not voice_stream.ready(speaker.client_id))
    client = voice_stream.server.get_client(speaker.client_id)
    assert client is None or not client.is_connected


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
    wires: list[tuple[Wire, Speaker]],
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    """The panel that heard the wake word hears the reply; the other hears nothing.

    The reply is finished at drain, without the panel reporting it played.
    """
    await _second_entry(hass, hass_read_only_user.id)
    token = hass_read_only_access_token
    panel_a, wire_a, a = await _voice_panel(hass, hass_ws_client, token, wires)
    _, wire_b, b = await _voice_panel(hass, hass_ws_client, token, wires, did=OTHER_DID)
    pipeline.events[-2] = PipelineEvent(
        PipelineEventType.TTS_END,
        {"tts_output": {"url": f"{base}/test_voice_stream/speech"}},
    )
    entity_id = _satellite(hass, entry)

    run_id, handler = await _run(panel_a, "hey_jarvis")
    await panel_a.client.send_bytes(bytes([handler]))
    events = await _turn_events(panel_a, run_id)
    stream_id = events[-2]["stream_id"]
    assert events[-2:] == [
        {
            "kind": "play",
            "url": f"{base}/test_voice_stream/speech",
            "continue_conversation": True,
            "stream": True,
            "stream_id": stream_id,
        },
        {"kind": "end"},
    ]
    _voice_stream_event_conforms(events[-2])
    await a.ended(1)
    await _until(lambda: bool(wire_a.ends))

    # The end carries what the play said about listening again.
    assert wire_a.ends == [
        {
            "kind": "voice_stream_end",
            "stream_id": stream_id,
            "outcome": "played",
            "listen_after": True,
        }
    ]
    _heard_in_order(wire_a, a, 0, stream_id)
    assert a.seconds(0) == _whole(_pcm("speech"))
    assert b.streams == []
    assert wire_b.ends == []
    await _until(lambda: hass.states.get(entity_id).state != "responding")


# ---------------------------------------------------------------------------
# The media player's announcements, and one replacing another.


@LINGERING
async def test_a_newer_media_announcement_ends_the_older_stream_first(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """The old stream's end reaches the panel before the new stream's play."""
    speaker = Speaker()
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        _native_hello(["state", "events", "commands", "approval", CAPABILITY])
        | {CAPABILITY: {"client_id": speaker.client_id}}
    )
    hello = await _receive(client)
    assert hello["success"], hello
    panel = NativePanel(client, hello)
    wire = Wire(client, panel.subscription, panel.token)
    wires.append((wire, speaker))
    await panel.sync(hass)
    await speaker.connect(wire, hello["result"][CAPABILITY])
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
        _voice_stream_event_conforms(value)
        return value

    first = await announce("long")
    await _until(lambda: bool(speaker.streams and speaker.streams[0].chunks))
    assert first == {
        "action": "play",
        "url": f"{base}/test_voice_stream/long",
        "announce": True,
        "stream": True,
        "stream_id": first["stream_id"],
    }
    second = await announce("speech")
    await speaker.ended(2)
    await _until(lambda: len(wire.ends) == 2)

    assert [(end["stream_id"], end["outcome"]) for end in wire.ends] == [
        (first["stream_id"], "preempted"),
        (second["stream_id"], "played"),
    ]
    # The old stream ended, on the wire, before the new one was announced.
    assert wire.end_index(first["stream_id"]) < wire.first(
        _is_event(second["stream_id"])
    )
    _heard_in_order(wire, speaker, 0, first["stream_id"])
    _heard_in_order(wire, speaker, 1, second["stream_id"])
    assert speaker.seconds(0) < 6.0  # of 8 s
    assert speaker.seconds(1) == _whole(_pcm("speech"))


# ---------------------------------------------------------------------------
# A panel stopping speech itself.


@LINGERING
async def test_a_panel_that_stops_its_stream_leaves_it_and_the_others_play_on(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """voice_stream_stop ends the stream for that panel alone, as preempted."""
    other = await _second_entry(hass, hass_read_only_user.id)
    token = hass_read_only_access_token
    panel_a, wire_a, a = await _voice_panel(hass, hass_ws_client, token, wires)
    panel_b, wire_b, b = await _voice_panel(
        hass, hass_ws_client, token, wires, did=OTHER_DID
    )
    call = _announce(
        hass,
        [_satellite(hass, entry), _satellite(hass, other)],
        base,
        media_id=f"{base}/test_voice_stream/long",
        preannounce=False,
    )
    event_a, _ = await _event(panel_a), await _event(panel_b)
    stream_id = event_a["stream_id"]
    await _until(lambda: bool(a.streams and a.streams[0].chunks))

    reply = await panel_a.send(
        {
            "type": "panel_assistant/voice_stream_stop",
            "session": panel_a.token,
            "stream_id": stream_id,
        }
    )
    assert reply["success"], reply
    await a.ended(1)
    assert wire_a.ends == [
        {
            "kind": "voice_stream_end",
            "stream_id": stream_id,
            "outcome": "preempted",
            "listen_after": False,
        }
    ]
    assert wire_b.ends == []
    async with asyncio.timeout(15):
        await call
    await b.ended(1)

    assert [end["outcome"] for end in wire_b.ends] == ["played"]
    assert a.seconds(0) < 6.0  # of 8 s
    assert b.seconds(0) == _whole(_pcm("long"))
    # Nothing labelled the stream reached the panel after it ended for it.
    assert not [i for i in wire_a.frames(stream_id) if i > wire_a.end_index(stream_id)]


# ---------------------------------------------------------------------------
# Overlapping announcements: the newest owns each panel.


def _listener(
    speaker: Speaker, told: list[str], outcomes: list[str] | None = None
) -> Listener:
    def play(stream_id: str) -> bool:
        told.append(stream_id)
        return True

    return Listener(
        speaker.client_id,
        play,
        ended=None if outcomes is None else outcomes.append,
    )


@LINGERING
async def test_overlapping_replacements_leave_the_newest_playing_whole(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """Two streams replacing one: the later plays whole, and nothing stops it.

    Starts are driven at the ownership seam itself, because only there can two
    replacements be made to overlap every time.
    """
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None
    _, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    told: list[str] = []
    outcomes: list[str] = []
    rate = NATIVE_RATE

    await voice_stream._async_start(
        [_listener(speaker, told, outcomes)], _pcm("long"), rate
    )
    await _until(lambda: bool(speaker.streams and speaker.streams[0].chunks))
    speech, chime = _pcm("speech"), _pcm("chime")
    await asyncio.gather(
        voice_stream._async_start([_listener(speaker, told, outcomes)], speech, rate),
        voice_stream._async_start([_listener(speaker, told, outcomes)], chime, rate),
    )

    live = [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == f"{DOMAIN} voice stream" and not task.done()
    ]
    tracked = voice_stream._playing[speaker.client_id].task
    await _until(lambda: all(task.done() for task in live), 10)
    await speaker.ended(len(speaker.streams))
    assert speaker.seconds(-1) == _whole(chime)
    assert speaker.seconds(0) < 6.0  # of 8 s, cut off
    assert len(set(told)) == 3
    assert outcomes == ["preempted", "preempted", "played"]
    assert [end["stream_id"] for end in wire.ends] == told
    for index, stream_id in enumerate(told):
        if index < len(speaker.streams):
            assert speaker.streams[index].label == stream_id
    # One stream owned the panel; none ran on untracked.
    assert live == [tracked]


@LINGERING
async def test_a_slow_earlier_batch_leaves_the_next_window_coalescing(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """An announcement still fetching when the next one opens does not split it."""
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None
    token = hass_read_only_access_token
    await _second_entry(hass, hass_read_only_user.id)
    await _second_entry(hass, hass_read_only_user.id, "f" * 64, "gamma")
    speakers = []
    for did in (DID, OTHER_DID, "f" * 64):
        _, _, speaker = await _voice_panel(hass, hass_ws_client, token, wires, did=did)
        speakers.append(speaker)
    a, b, c = speakers
    view: _ClipView = hass.data[_ClipView.name]
    view.held["chime"] = release = asyncio.Event()
    speech = (f"{base}/test_voice_stream/speech",)
    told: list[str] = []

    with patch.object(voice_stream_module, "COALESCE_WINDOW", 0.5):
        earlier = asyncio.ensure_future(
            voice_stream.async_announce(
                "same", _listener(a, told), (f"{base}/test_voice_stream/chime",)
            )
        )
        await asyncio.sleep(0.6)  # its window has closed; its clip is still held
        later_b = asyncio.ensure_future(
            voice_stream.async_announce("same", _listener(b, told), speech)
        )
        await asyncio.sleep(0)
        release.set()
        async with asyncio.timeout(5):
            assert await earlier
        later_c = asyncio.ensure_future(
            voice_stream.async_announce("same", _listener(c, told), speech)
        )
        async with asyncio.timeout(5):
            assert await later_b
            assert await later_c

    await b.ended(1)
    await c.ended(1)
    assert len(b.streams) == len(c.streams) == 1
    assert b.timing(0)
    assert b.timing(0) == c.timing(0)
    assert told[1] == told[2] != told[0]
    assert _fetched(hass).count("speech") == 1


# ---------------------------------------------------------------------------
# Formats.


def test_speech_is_decoded_at_its_own_rate_never_upsampled() -> None:
    """The stream carries a clip's own rate, or the lowest of chime and speech."""
    pcm, rate = decode_clips([CLIPS["speech"]])
    assert rate == NATIVE_RATE
    assert len(pcm) == int(0.5 * NATIVE_RATE) * 2
    pcm, rate = decode_clips([CLIPS["chime"], CLIPS["narrow"]])
    assert rate == 16_000
    assert len(pcm) == (int(0.2 * 16_000) + int(0.5 * 16_000)) * 2


@LINGERING
async def test_every_member_hears_the_same_start_in_any_format(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """A member the server converts for still starts at exactly the same time."""
    voice_stream = async_get_voice_stream(hass)
    assert voice_stream is not None
    token = hass_read_only_access_token
    await _second_entry(hass, hass_read_only_user.id)
    _, _, a = await _voice_panel(hass, hass_ws_client, token, wires)
    _, _, b = await _voice_panel(
        hass,
        hass_ws_client,
        token,
        wires,
        Speaker(rate=44_100, channels=2),
        did=OTHER_DID,
    )
    speech = (f"{base}/test_voice_stream/speech",)
    told: list[str] = []

    assert await asyncio.gather(
        voice_stream.async_announce("one", _listener(a, told), speech),
        voice_stream.async_announce("one", _listener(b, told), speech),
    ) == [True, True]
    await a.ended(1)
    await b.ended(1)

    assert len(a.streams) == len(b.streams) == 1
    assert a.timing(0) != b.timing(0)  # b's audio was converted for it
    assert a.start(0) == b.start(0)
    # The converter keeps back its last few tens of ms when the stream ends.
    assert a.seconds(0) == pytest.approx(b.seconds(0), abs=0.05)


@LINGERING
async def test_a_stream_that_fails_before_its_first_chunk_tells_the_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,
    wires: list[tuple[Wire, Speaker]],
) -> None:
    """The panel told to play hears it failed, the call fails, the next plays."""
    panel, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    satellite = _satellite(hass, entry)

    with patch(
        "aiosendspin.server.group.SendspinGroup.start_stream",
        side_effect=RuntimeError("no stream"),
    ):
        call = _announce(
            hass,
            satellite,
            base,
            media_id=f"{base}/test_voice_stream/speech",
            preannounce=False,
        )
        event = await _event(panel)
        with pytest.raises(HomeAssistantError):
            async with asyncio.timeout(5):
                await call
    assert wire.ends == [
        {
            "kind": "voice_stream_end",
            "stream_id": event["stream_id"],
            "outcome": "failed",
            "listen_after": False,
        }
    ]
    call = _announce(
        hass,
        satellite,
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce=False,
    )
    event = await _event(panel)
    async with asyncio.timeout(10):
        await call
    await speaker.ended(1)
    assert wire.ends[-1]["outcome"] == "played"
    _heard_in_order(wire, speaker, 0, event["stream_id"])

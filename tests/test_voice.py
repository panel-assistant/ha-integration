"""The panel as an Assist satellite, over its own session."""

import asyncio
from collections.abc import AsyncIterable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    flush_store,
)

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.identity import CONF_INSTALL_IDENTITY
from custom_components.panel_assistant.voice import PIPELINE_COLORS, PipelineColors

from .test_native import _setup
from .test_transport import DID, WsClientFactory, _receive, _send

PIPELINES = [SimpleNamespace(id="pipeline_a"), SimpleNamespace(id="pipeline_b")]
WAKE_WORDS = [
    {"id": "okay_nabu", "wake_word": "Okay Nabu", "trained_languages": ["en"]},
    {"id": "hey_jarvis", "wake_word": "Hey Jarvis", "trained_languages": ["en"]},
]


def _hello(capabilities: list[str]) -> dict[str, Any]:
    return {
        "type": "panel_assistant/hello",
        "protocol": {"min": 3, "max": 3},
        "did": DID,
        "app": {"version": "0.9.9-rc1", "version_code": 990},
        "contract_digest": "c" * 64,
        "capabilities": capabilities,
        "channels": [],
    }


def _configuration(token: str, **changes: Any) -> dict[str, Any]:
    return {
        "type": "panel_assistant/voice_configuration",
        "session": token,
        "enabled": True,
        "wake_words": WAKE_WORDS,
        "active": ["okay_nabu", "hey_jarvis"],
        "pipelines": {"hey_jarvis": "pipeline_b", "okay_nabu": "gone"},
    } | changes


class FakePipeline:
    """Stands in for Core's audio pipeline: records what it was given, emits events."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cancelled = False
        self.audio: list[bytes] = []
        self.events: list[PipelineEvent] = [
            PipelineEvent(PipelineEventType.STT_START),
            PipelineEvent(PipelineEventType.STT_VAD_END),
            PipelineEvent(PipelineEventType.STT_END, {"stt_output": {"text": "hi"}}),
            PipelineEvent(PipelineEventType.INTENT_START),
            PipelineEvent(
                PipelineEventType.INTENT_END,
                {"intent_output": {"continue_conversation": True}},
            ),
            PipelineEvent(PipelineEventType.TTS_START, {"tts_input": "hello"}),
            PipelineEvent(
                PipelineEventType.TTS_END,
                {"tts_output": {"url": "/api/tts_proxy/reply.mp3"}},
            ),
            PipelineEvent(PipelineEventType.RUN_END),
        ]

    async def __call__(self, hass: HomeAssistant, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        stream: AsyncIterable[bytes] = kwargs["stt_stream"]
        try:
            async for chunk in stream:
                self.audio.append(chunk)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        callback: Callable[[PipelineEvent], None] = kwargs["event_callback"]
        for event in self.events:
            callback(event)


@pytest.fixture
def pipeline() -> Any:
    fake = FakePipeline()
    with (
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            fake,
        ),
        patch(
            "custom_components.panel_assistant.assist_satellite.async_get_pipelines",
            return_value=PIPELINES,
        ),
    ):
        yield fake


class Panel:
    """One signed-in panel connection with an open session."""

    def __init__(self, client: Any, hello: dict[str, Any]) -> None:
        self.client = client
        self.subscription = hello["id"]
        self.result = hello["result"]
        self.token: str = hello["result"]["session"]
        # Every voice_colors event, in order; the other tests read past them.
        self.colors: list[dict[str, str]] = []

    async def receive(self) -> dict[str, Any]:
        while True:
            message = await _receive(self.client)
            event = message.get("event") or {}
            if message.get("type") != "event" or event.get("kind") != "voice_colors":
                return message
            self.colors.append(event["colors"])

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        await self.client.send_json_auto_id(message)
        return await self.receive()

    async def configure(self, **changes: Any) -> dict[str, Any]:
        return await self.send(_configuration(self.token, **changes))


async def _connect(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    capabilities: list[str] | None = None,
) -> Panel:
    client = await hass_ws_client(hass, token)
    await client.send_json_auto_id(
        _hello(["state", "events", "voice"] if capabilities is None else capabilities)
    )
    hello = await _receive(client)
    assert hello["success"], hello
    return Panel(client, hello)


def _satellite(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "assist_satellite", DOMAIN, f"{entry.entry_id}_assist_satellite"
    )


@pytest.fixture
async def entry(hass: HomeAssistant, hass_read_only_user: Any) -> MockConfigEntry:
    """A bound panel entry on the default (shadow) authority.

    Home Assistant always runs its own core integration, which the Assist
    stack depends on; the test harness does not start it unasked.
    """
    assert await async_setup_component(hass, "homeassistant", {})
    entry = await _setup(hass, hass_read_only_user.id, native=None, described=None)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_INSTALL_IDENTITY: True}
    )
    return entry


async def _ready(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> tuple[Panel, str]:
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()
    entity_id = _satellite(hass, entry)
    assert entity_id is not None
    return panel, entity_id


async def test_voice_is_granted_under_shadow_and_the_satellite_joins_the_panel_device(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_admin_user: Any,
    entry: MockConfigEntry,
) -> None:
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert panel.result["authority"] == "shadow"
    assert "voice" in panel.result["capabilities"]
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()

    entity_id = _satellite(hass, entry)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "idle"
    device = er.async_get(hass).async_get(entity_id).device_id
    devices = {
        item.device_id
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert devices == {device}


async def test_turning_the_voice_assistant_off_makes_the_satellite_unavailable(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert (await panel.configure(enabled=True))["success"]
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "idle"


async def test_a_panel_that_offers_no_voice_has_no_satellite(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> None:
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "events"]
    )
    refused = await panel.configure()
    assert refused["error"]["code"] == "voice_unavailable"
    await hass.async_block_till_done()
    assert _satellite(hass, entry) is None


async def test_the_selector_reads_the_panels_wake_words_and_writes_them_back(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> None:
    _panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    admin = await hass_ws_client(hass)
    configuration = await _send(
        admin, {"type": "assist_satellite/get_configuration", "entity_id": entity_id}
    )
    assert configuration["result"] == {
        "available_wake_words": WAKE_WORDS,
        "active_wake_words": ["okay_nabu", "hey_jarvis"],
        # Every wake word the panel has may be active at once; Core refuses a
        # selection larger than this number, so it is never zero.
        "max_active_wake_words": 2,
        "pipeline_entity_id": None,
        "vad_entity_id": None,
    }
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_set_voice_wake_words",
        AsyncMock(),
    ) as written:
        response = await _send(
            admin,
            {
                "type": "assist_satellite/set_wake_words",
                "entity_id": entity_id,
                "wake_word_ids": ["hey_jarvis"],
            },
        )
    assert response["success"], response
    written.assert_awaited_once_with(["hey_jarvis"])


async def _run(
    panel: Panel, wake_word_id: str | None, **fields: Any
) -> tuple[int, int]:
    response = await panel.send(
        {
            "type": "panel_assistant/voice_run",
            "session": panel.token,
            "wake_word_id": wake_word_id,
            **fields,
        },
    )
    assert response["success"], response
    handler: int = response["result"]["handler_id"]
    return response["id"], handler


async def _turn_events(panel: Panel, run_id: int) -> list[dict[str, Any]]:
    events = []
    while True:
        message = await panel.receive()
        assert message["id"] == run_id, message
        events.append(message["event"])
        if message["event"]["kind"] == "end":
            return events


async def test_a_wake_word_turn_streams_to_the_pipeline_the_panel_named(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    run_id, handler = await _run(panel, "hey_jarvis")
    await panel.client.send_bytes(bytes([handler]) + b"\x01\x02" * 160)
    await panel.client.send_bytes(bytes([handler]) + b"\x03\x04" * 160)
    await panel.client.send_bytes(bytes([handler]))

    events = await _turn_events(panel, run_id)

    call = pipeline.calls[-1]
    assert call["pipeline_id"] == "pipeline_b"
    assert call["wake_word_phrase"] == "Hey Jarvis"
    assert pipeline.audio == [b"\x01\x02" * 160, b"\x03\x04" * 160]
    assert events == [
        {"kind": "listen_end"},
        {"kind": "listen_end"},
        {
            "kind": "play",
            "url": "/api/tts_proxy/reply.mp3",
            "continue_conversation": True,
        },
        {"kind": "end"},
    ]
    # Held responding until the panel says it has played the reply.
    assert hass.states.get(entity_id).state == "responding"
    played = await panel.send(
        {"type": "panel_assistant/voice_played", "session": panel.token}
    )
    assert played["success"]
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "idle"
    # The turn's binary handler was released with the turn.
    assert all(slot is None for slot in _connection_of(hass, entry).binary_handlers)


async def test_a_continued_turn_keeps_the_pipeline_without_the_wake_phrase(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    run_id, handler = await _run(panel, "hey_jarvis", continued=True)
    await panel.client.send_bytes(bytes([handler]))
    await _turn_events(panel, run_id)

    call = pipeline.calls[-1]
    assert call["pipeline_id"] == "pipeline_b"
    assert call["wake_word_phrase"] is None


async def test_a_pipeline_that_no_longer_exists_falls_back_to_the_preferred(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    run_id, handler = await _run(panel, "okay_nabu")
    await panel.client.send_bytes(bytes([handler]))
    await _turn_events(panel, run_id)

    assert pipeline.calls[-1]["pipeline_id"] is None


def _connection_of(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    from custom_components.panel_assistant.transport import async_get_sessions

    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    return session.connection


async def test_an_announcement_plays_on_the_panel_and_waits_for_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    call = asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {
                "entity_id": entity_id,
                "media_id": "/local/doorbell.mp3",
                "preannounce": False,
            },
            blocking=True,
        )
    )
    message = await panel.receive()
    assert message["id"] == panel.subscription
    event = message["event"]
    assert event["kind"] == "voice_announce"
    assert event["listen_after"] is False
    assert event["preannounce_url"] is None
    assert event["url"].startswith("/local/doorbell.mp3")
    await asyncio.sleep(0)
    assert not call.done()
    played = await panel.send(
        {
            "type": "panel_assistant/voice_played",
            "session": panel.token,
            "announce_id": event["announce_id"],
        },
    )
    assert played["success"]
    async with asyncio.timeout(5):
        await call


async def test_an_announcement_fails_when_its_session_ends(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    call = asyncio.ensure_future(
        hass.services.async_call(
            "assist_satellite",
            "announce",
            {"entity_id": entity_id, "media_id": "/local/a.mp3", "preannounce": False},
            blocking=True,
        )
    )
    message = await panel.receive()
    assert message["event"]["kind"] == "voice_announce"
    # The panel reconnects: the first session is superseded, and nothing it was
    # asked to play can finish on the new one.
    await _connect(hass, hass_ws_client, hass_read_only_access_token)
    with pytest.raises(HomeAssistantError):
        async with asyncio.timeout(5):
            await call


async def _colors(panel: Panel) -> dict[str, str]:
    """The next colours the panel was sent, read past or not."""
    if not panel.colors:
        message = await _receive(panel.client)
        assert message["event"]["kind"] == "voice_colors", message
        panel.colors.append(message["event"]["colors"])
    return panel.colors.pop(0)


async def test_each_wake_word_is_sent_the_colour_of_the_pipeline_it_runs(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    await hass.async_block_till_done()
    # okay_nabu names a pipeline that is gone, so it runs, and looks like, the
    # preferred one; hey_jarvis runs pipeline_b.
    colors = await _colors(panel)
    assert set(colors) == {"okay_nabu", "hey_jarvis"}
    assert colors["okay_nabu"] != colors["hey_jarvis"]

    assert (
        await panel.configure(pipelines={"okay_nabu": "pipeline_b", "hey_jarvis": ""})
    )["success"]
    await hass.async_block_till_done()
    assert await _colors(panel) == {
        "okay_nabu": colors["hey_jarvis"],
        "hey_jarvis": colors["okay_nabu"],
    }

    # Nothing changed, so nothing is sent again.
    assert (
        await panel.configure(
            enabled=False, pipelines={"okay_nabu": "pipeline_b", "hey_jarvis": ""}
        )
    )["success"]
    await hass.async_block_till_done()
    assert panel.colors == []


async def test_a_pipeline_keeps_its_colour_for_every_panel_and_across_restarts(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    first = PipelineColors(hass)
    colors = await first.async_colors(["p1", "p2"])
    assert colors == {"p1": PIPELINE_COLORS[0], "p2": PIPELINE_COLORS[1]}
    again = await first.async_colors(["p3", "p2"])
    assert again == {"p3": PIPELINE_COLORS[2], "p2": PIPELINE_COLORS[1]}

    await flush_store(first._store)
    assert hass_storage["panel_assistant.voice_colors"]["data"] == {
        "pipelines": {"p1": 0, "p2": 1, "p3": 2}
    }

    restarted = PipelineColors(hass)
    assert await restarted.async_colors(["p3", "p1"]) == {
        "p3": PIPELINE_COLORS[2],
        "p1": PIPELINE_COLORS[0],
    }
    # Once every colour is taken, a new pipeline shares the least used one.
    more = [f"q{index}" for index in range(len(PIPELINE_COLORS) - 3)]
    await restarted.async_colors(more)
    assert (await restarted.async_colors(["late"]))["late"] == PIPELINE_COLORS[0]


async def test_a_turn_is_refused_to_another_connection_and_to_a_panel_without_voice(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    # The session token is bound to the panel's own connection.
    other = await hass_ws_client(hass, hass_read_only_access_token)
    stolen = await _send(
        other,
        {"type": "panel_assistant/voice_run", "session": panel.token},
    )
    assert stolen["error"]["code"] == "session_unknown"

    # A session that was not granted voice cannot start a turn.
    await panel.client.close()
    await hass.async_block_till_done()
    silent = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "events"]
    )
    refused = await silent.send(
        {"type": "panel_assistant/voice_run", "session": silent.token}
    )
    assert refused["error"]["code"] == "voice_unavailable"
    assert pipeline.calls == []


async def test_leaving_a_turn_cancels_the_pipeline_and_releases_its_handler(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    run_id, handler = await _run(panel, "hey_jarvis")
    await panel.client.send_bytes(bytes([handler]) + b"\x01\x02" * 160)
    await hass.async_block_till_done()
    assert pipeline.calls, "the turn never reached the pipeline"
    left = await panel.send({"type": "unsubscribe_events", "subscription": run_id})
    assert left["success"], left
    await hass.async_block_till_done()
    assert all(slot is None for slot in _connection_of(hass, entry).binary_handlers)
    assert run_id not in _connection_of(hass, entry).subscriptions
    assert pipeline.cancelled


async def test_the_end_of_the_audio_survives_a_full_queue(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    pipeline: FakePipeline,
) -> None:
    panel, _entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    gate = asyncio.Event()
    stream_audio = pipeline.__call__

    async def held(hass: HomeAssistant, **kwargs: Any) -> None:
        await gate.wait()
        await stream_audio(hass, **kwargs)

    with (
        patch("custom_components.panel_assistant.voice.MAX_QUEUED_FRAMES", 2),
        patch(
            "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
            held,
        ),
    ):
        run_id, handler = await _run(panel, "hey_jarvis")
        for frame in (b"\x01", b"\x02", b"\x03", b"\x04"):
            await panel.client.send_bytes(bytes([handler]) + frame * 320)
        await panel.client.send_bytes(bytes([handler]))
        await hass.async_block_till_done()
        gate.set()
        # Well inside the five-second idle timeout that a lost end would cost.
        async with asyncio.timeout(2):
            events = await _turn_events(panel, run_id)
    assert events[-1] == {"kind": "end"}
    assert pipeline.audio == [b"\x02" * 320]

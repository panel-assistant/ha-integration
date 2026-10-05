"""Home Assistant's voice timers, set at a panel and rung on it."""

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.intent.timers import TimersNotSupportedError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_transport import WsClientFactory
from .test_voice import (  # noqa: F401  (fixtures)
    FakePipeline,
    Panel,
    _configuration,
    _ready,
    _run,
    entry,
    pipeline,
)


def _device(hass: HomeAssistant, entity_id: str) -> str:
    device_id = er.async_get(hass).async_get(entity_id).device_id
    assert device_id is not None
    return device_id


async def _timer(
    hass: HomeAssistant, device_id: str, name: str | None = None, seconds: int = 1
) -> None:
    slots: dict[str, Any] = {"seconds": {"value": seconds}}
    if name is not None:
        slots["name"] = {"value": name}
    await intent.async_handle(
        hass, "test", intent.INTENT_START_TIMER, slots, device_id=device_id
    )


async def _announcement(panel: Panel, seconds: float = 5) -> dict[str, Any]:
    async with asyncio.timeout(seconds):
        message = await panel.receive()
    assert message["id"] == panel.subscription, message
    event = message["event"]
    assert event["kind"] == "voice_announce", event
    return event


async def _played(panel: Panel, event: dict[str, Any]) -> None:
    played = await panel.send(
        {
            "type": "panel_assistant/voice_played",
            "session": panel.token,
            "announce_id": event["announce_id"],
        }
    )
    assert played["success"], played


async def test_a_timer_set_at_the_panel_rings_it_when_it_finishes(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    await _timer(hass, _device(hass, entity_id))

    event = await _announcement(panel)
    assert event["url"].startswith("/panel_assistant/usb/timer_finished.flac")
    assert event["preannounce_url"] is None
    assert event["listen_after"] is False
    await _played(panel, event)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "idle"


async def test_a_named_timer_rings_then_says_its_name(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    spoken: list[str] = []
    stream = SimpleNamespace(
        token="t", url="/api/tts_proxy/t.mp3", async_set_message=spoken.append
    )
    tts = "homeassistant.components.assist_satellite.entity.tts"
    with (
        patch(f"{tts}.async_resolve_engine", return_value="tts.test"),
        patch(f"{tts}.async_create_stream", return_value=stream),
        patch(f"{tts}.generate_media_source_id", return_value="media-source://t"),
    ):
        await _timer(hass, _device(hass, entity_id), name="pasta")
        event = await _announcement(panel)
    assert spoken == ["pasta"]
    assert event["message"] == "pasta"
    assert event["preannounce_url"].startswith(
        "/panel_assistant/usb/timer_finished.flac"
    )
    assert event["url"].startswith("/api/tts_proxy/t.mp3")
    await _played(panel, event)


async def test_a_cancelled_timer_does_not_ring(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    device_id = _device(hass, entity_id)
    await _timer(hass, device_id)
    await intent.async_handle(
        hass, "test", intent.INTENT_CANCEL_TIMER, {}, device_id=device_id
    )
    with pytest.raises(TimeoutError):
        await _announcement(panel, seconds=2)


async def test_a_panel_takes_timers_only_while_its_voice_assistant_is_on(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    device_id = _device(hass, entity_id)
    await _timer(hass, device_id, seconds=60)
    # A reconfiguration that leaves voice on keeps the one registration.
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()
    await _timer(hass, device_id, seconds=60)

    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    with pytest.raises(TimersNotSupportedError):
        await _timer(hass, device_id, seconds=60)

    assert (await panel.configure(enabled=True))["success"]
    await hass.async_block_till_done()
    await _timer(hass, device_id, seconds=60)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(TimersNotSupportedError):
        await _timer(hass, device_id, seconds=60)


async def test_a_ring_waiting_on_a_turn_is_dropped_when_voice_turns_off(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    """A ring first cancels the turn in progress; voice can go off meanwhile."""
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_to_cancel(hass: HomeAssistant, **kwargs: Any) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelling.set()
            await release.wait()
            raise

    events: list[dict[str, Any]] = []
    with patch(
        "homeassistant.components.assist_satellite.entity.async_pipeline_from_audio_stream",
        slow_to_cancel,
    ):
        await _run(panel, "hey_jarvis")
        async with asyncio.timeout(5):
            await started.wait()
        await _timer(hass, _device(hass, entity_id))
        async with asyncio.timeout(5):
            await cancelling.wait()
        await panel.client.send_json_auto_id(_configuration(panel.token, enabled=False))
        while (message := await panel.receive())["type"] != "result":
            events.append(message["event"])
        assert message["success"], message
        assert hass.states.get(entity_id).state == "unavailable"
        release.set()
        await hass.async_block_till_done()

    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(1):
            while True:
                events.append((await panel.receive()).get("event") or {})
    assert all(event.get("kind") != "voice_announce" for event in events), events

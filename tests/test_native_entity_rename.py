"""Registry renames preserve the live panel's reports and command destinations."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.stream import Stream
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN

from .test_native import _native_entries, _report, panel_patches
from .test_transport import DID, WsClientFactory, _send
from .test_transport_commands import Panel, _call, _connect
from .test_transport_commands import native as native


@pytest.fixture
async def panel(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> Panel:
    """Keep a real subscribed panel connection open throughout each rename."""
    return await _connect(hass, hass_ws_client, hass_read_only_access_token)


@contextmanager
def _state_reports(hass: HomeAssistant, entity_id: str) -> Iterator[list[Any]]:
    reports: list[Any] = []

    @callback
    def reported(event: Any) -> None:
        reports.append(event)

    @callback
    def matches(data: Any) -> bool:
        return data["entity_id"] == entity_id

    unsub = hass.bus.async_listen(EVENT_STATE_REPORTED, reported, event_filter=matches)
    changed = hass.bus.async_listen(EVENT_STATE_CHANGED, reported, event_filter=matches)
    try:
        yield reports
    finally:
        unsub()
        changed()


async def _rename(
    hass: HomeAssistant,
    entity_id: str,
    iteration: int,
    *,
    pending_command: bool = False,
) -> str:
    new_id = f"{entity_id.split('.')[0]}.renamed_panel_{iteration}"
    before = er.async_get(hass).async_get(entity_id)
    assert before is not None
    changed = asyncio.Event()

    @callback
    def renamed_state(event: Any) -> None:
        if event.data["entity_id"] == new_id and event.data["new_state"] is not None:
            changed.set()

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, renamed_state)
    try:
        renamed = er.async_get(hass).async_update_entity(
            entity_id, new_entity_id=new_id
        )
        if pending_command:
            async with asyncio.timeout(5):
                await changed.wait()
        else:
            await hass.async_block_till_done()
    finally:
        unsub()
    assert renamed.id == before.id
    assert renamed.unique_id == before.unique_id
    assert hass.states.get(entity_id) is None
    assert hass.states.get(new_id) is not None
    return new_id


async def _observe(hass: HomeAssistant, panel: Panel, channel: str, value: Any) -> None:
    response = await _send(
        panel.client,
        _report(
            panel.token,
            "delta",
            [{"channel": channel, "state": "known", "value": value}],
        ),
    )
    assert response["success"], response
    assert response["result"]["rejected"] == []
    await hass.async_block_till_done()


async def _command(
    hass: HomeAssistant,
    panel: Panel,
    entity_id: str,
    service: str,
    channel: str,
    value: Any,
    **data: Any,
) -> None:
    call = _call(
        hass, entity_id.split(".")[0], service, {"entity_id": entity_id, **data}
    )
    command = await panel.command()
    assert (command["channel"], command["value"]) == (channel, value)
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        await call
    await panel.nothing_sent()


@pytest.mark.parametrize(
    ("suffix", "channel", "values", "states"),
    [
        ("temperature", "temperature", (23.5, 24.5), ("23.5", "24.5")),
        ("touch_sound", "touch_sound", (False, True), ("off", "on")),
        (
            "led",
            "led",
            ({"on": False, "brightness": 40}, {"on": True, "brightness": 80}),
            ("off", "on"),
        ),
        (
            "media",
            "media",
            ({"state": "playing", "muted": True}, {"state": "paused", "muted": False}),
            ("playing", "paused"),
        ),
        (
            "ha_companion_update",
            "update_companion",
            tuple(
                {
                    "in_progress": False,
                    "installed_version": version,
                    "latest_version": None,
                    "release_url": None,
                }
                for version in ("2026.2.0", "2026.3.0")
            ),
            ("unknown", "unknown"),
        ),
    ],
)
async def test_native_reports_and_commands_follow_two_registry_renames(
    hass: HomeAssistant,
    native: MockConfigEntry,
    panel: Panel,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    suffix: str,
    channel: str,
    values: tuple[Any, Any],
    states: tuple[str, str],
) -> None:
    """Reports update the new ID once, and services retain the same panel route."""
    unique_id = f"{DID}_{suffix}"
    entity_id = _native_entries(hass, native.entry_id)[unique_id].entity_id
    original_id = entity_id
    for iteration, (value, expected) in enumerate(zip(values, states, strict=True), 1):
        entity_id = await _rename(hass, entity_id, iteration)
        with _state_reports(hass, entity_id) as reports:
            await _observe(hass, panel, channel, value)
        assert len(reports) == 1
        shown = hass.states.get(entity_id)
        assert shown is not None
        assert shown.state == expected
        if suffix == "ha_companion_update":
            assert shown.attributes["installed_version"] == value["installed_version"]
        elif suffix == "touch_sound":
            await _command(hass, panel, entity_id, "turn_on", channel, True)
        elif suffix == "led":
            assert shown.attributes["supported_color_modes"] == ["rgb"]
            await _command(hass, panel, entity_id, "turn_on", channel, {"on": True})
        elif suffix == "media":
            await _observe(hass, panel, "volume", 20 + iteration)
            assert hass.states.get(entity_id).attributes[
                "volume_level"
            ] == pytest.approx((20 + iteration) / 100)
            await _command(
                hass, panel, entity_id, "volume_set", "volume", 42, volume_level=0.42
            )
        assert hass.states.get(original_id) is None

    # A new session after unload must reach only the replacement entity, and
    # the registry customisation must survive the entry lifecycle.
    assert await hass.config_entries.async_unload(native.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert hass.states.get(entity_id).attributes["restored"] is True
    with panel_patches():
        assert await hass.config_entries.async_setup(native.entry_id)
        await hass.async_block_till_done()
    again = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert _native_entries(hass, native.entry_id)[unique_id].entity_id == entity_id
    await _observe(hass, again, channel, values[0])
    assert hass.states.get(entity_id).state == states[0]
    assert hass.states.get(original_id) is None


async def test_counted_events_follow_two_renames_without_duplicate_firing(
    hass: HomeAssistant, native: MockConfigEntry, panel: Panel
) -> None:
    """A button's live event subscription moves with its registry identity."""
    entity_id = _native_entries(hass, native.entry_id)[f"{DID}_button"].entity_id
    for iteration, event_type in enumerate(("keycode_home", "keycode_back"), 1):
        entity_id = await _rename(hass, entity_id, iteration)
        message = {
            "type": "panel_assistant/report_event",
            "session": panel.token,
            "channel": "button",
            "event_id": iteration,
            "event_type": event_type,
        }
        with _state_reports(hass, entity_id) as reports:
            assert (await _send(panel.client, message))["success"]
            await hass.async_block_till_done()
            assert len(reports) == 1
            assert (
                hass.states.get(entity_id).attributes["event_type"]
                == event_type.upper()
            )
            assert (await _send(panel.client, message))["success"]
            await hass.async_block_till_done()
            assert len(reports) == 1


async def test_camera_consumer_and_commands_follow_two_registry_renames(
    hass: HomeAssistant, native: MockConfigEntry, panel: Panel
) -> None:
    """Core camera lookup and a retained consumer still use the renamed camera."""
    entity_id = _native_entries(hass, native.entry_id)[f"{DID}_camera"].entity_id
    camera = hass.data["camera"].get_entity(entity_id)
    assert camera is not None
    await _observe(hass, panel, "camera_enabled", True)
    for iteration in (1, 2):
        stream = await camera.async_create_stream()
        assert stream is not None
        old_id = entity_id
        with patch.object(Stream, "stop", autospec=True) as stop:
            entity_id = await _rename(hass, entity_id, iteration)
        # Old Core removes and re-adds custom entities; the current lifecycle
        # must preserve a live viewer's stream during an in-place rename.
        assert stop.await_count == (
            0 if hasattr(Entity, "async_entity_id_changed") else 1
        )
        assert hass.data["camera"].get_entity(old_id) is None
        assert hass.data["camera"].get_entity(entity_id) is camera
        if hasattr(Entity, "async_entity_id_changed"):
            assert await camera.async_create_stream() is stream
        assert await camera.stream_source() == "rtsp://panel.local:8554/live"
        await _command(hass, panel, entity_id, "turn_on", "camera_enabled", True)
        await _observe(hass, panel, "camera_enabled", True)
    stream = await camera.async_create_stream()
    assert stream is not None
    with patch.object(Stream, "stop", autospec=True) as stop:
        assert await hass.config_entries.async_unload(native.entry_id)
        await hass.async_block_till_done()
    assert [call.args[0] for call in stop.await_args_list] == [stream]
    assert hass.data["camera"].get_entity(entity_id) is None
    assert await camera.stream_source() is None


async def test_panel_update_keeps_coordinator_subscription_after_two_renames(
    hass: HomeAssistant, native: MockConfigEntry, panel: Panel
) -> None:
    """Health polls still update the one software entity, then stop on unload."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "update", DOMAIN, f"{native.entry_id}_update"
    )
    assert entity_id is not None
    coordinator = native.runtime_data.coordinator
    for iteration, version in enumerate(("1.2.8", "1.2.9"), 1):
        entity_id = await _rename(hass, entity_id, iteration)
        coordinator.async_set_updated_data(
            replace(
                coordinator.data,
                health=replace(coordinator.data.health, version=version),
            )
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["installed_version"] == version
    assert await hass.config_entries.async_unload(native.entry_id)
    await hass.async_block_till_done()
    unloaded = hass.states.get(entity_id)
    coordinator.async_set_updated_data(
        replace(
            coordinator.data, health=replace(coordinator.data.health, version="1.2.10")
        )
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) == unloaded


async def test_media_announcement_command_survives_two_registry_renames(
    hass: HomeAssistant, native: MockConfigEntry, panel: Panel
) -> None:
    """A panel can acknowledge an announcement started under the old entity ID."""
    entity_id = _native_entries(hass, native.entry_id)[f"{DID}_media"].entity_id
    player = hass.data["media_player"].get_entity(entity_id)
    call = _call(
        hass,
        "media_player",
        "play_media",
        {
            "entity_id": entity_id,
            "media_content_id": "https://radio.example/chime.mp3",
            "media_content_type": "music",
            "announce": True,
        },
    )
    command = await panel.command()
    assert command["channel"] == "media"
    assert command["value"] == {
        "action": "play",
        "url": "https://radio.example/chime.mp3",
        "announce": True,
    }
    for iteration in (1, 2):
        old_id = entity_id
        entity_id = await _rename(hass, entity_id, iteration, pending_command=True)
        assert not call.done()
        assert hass.data["media_player"].get_entity(old_id) is None
        assert hass.data["media_player"].get_entity(entity_id) is player
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        await call
    await panel.nothing_sent()
    await _observe(hass, panel, "media", {"state": "playing", "muted": False})
    assert hass.states.get(entity_id).state == "playing"

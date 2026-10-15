"""The panel's voice switch and its Assist satellite entity, kept in step."""

import asyncio
import logging
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.panel_assistant.assist_satellite import PanelAssistSatellite
from custom_components.panel_assistant.transport_sessions import async_get_sessions
from custom_components.panel_assistant.voice import satellites

from .test_transport import WsClientFactory
from .test_voice import (  # noqa: F401  (fixtures)
    FakePipeline,
    _connect,
    _ready,
    _run,
    _satellite,
    _turn_events,
    entry,
    pipeline,
)


async def test_turning_voice_off_disables_the_satellite_and_on_enables_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Voice off disables the satellite; voice on brings it back on the live session.

    Turning voice on must not cost Core's config-entry reload, which would end
    the session and write the panel's entities unavailable 30 s later.
    """
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    registry = er.async_get(hass)
    session = async_get_sessions(hass).get(entry.entry_id)
    entity_ids = {
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert any(item.startswith("update.") for item in entity_ids)
    assert any(item.startswith("sensor.") for item in entity_ids)
    unavailable: list[str] = []

    @callback
    def _record(event: Any) -> None:
        state = event.data["new_state"]
        if event.data["entity_id"] in entity_ids and (
            state is None or state.state == STATE_UNAVAILABLE
        ):
            unavailable.append(event.data["entity_id"])

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)

    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is (
        er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(entity_id) is None
    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is (
        er.RegistryEntryDisabler.INTEGRATION
    )
    unavailable.clear()  # The satellite leaving while voice is off is expected.

    assert (await panel.configure(enabled=True))["success"]
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is None
    # Voice answers at once, on the same session.
    run_id, handler = await _run(panel, "hey_jarvis")
    await panel.client.send_bytes(bytes([handler]))
    assert (await _turn_events(panel, run_id))[-1] == {"kind": "end"}
    assert (
        await panel.send(
            {"type": "panel_assistant/voice_played", "session": panel.token}
        )
    )["success"]
    # Core's reload delay passes without ending the session.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert async_get_sessions(hass).get(entry.entry_id) is session
    assert hass.states.get(entity_id).state == "idle"
    assert unavailable == []
    # The satellite left and came back without a fault on its way out.
    assert not [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name.startswith(
            (
                "homeassistant.helpers.dispatcher",
                "homeassistant.helpers.entity",
                "homeassistant.components.assist_satellite",
            )
        )
    ]


async def test_voice_back_on_while_the_satellite_is_still_leaving_brings_it_back(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    """Voice turned on again before the disabled satellite has gone still ends up on."""
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    leave = asyncio.Event()
    leaving = PanelAssistSatellite.async_will_remove_from_hass

    async def _slow_leave(self: PanelAssistSatellite) -> None:
        await leave.wait()
        await leaving(self)

    with patch.object(PanelAssistSatellite, "async_will_remove_from_hass", _slow_leave):
        assert (await panel.configure(enabled=False))["success"]
        assert (await panel.configure(enabled=True))["success"]
        leave.set()
        await hass.async_block_till_done()

    assert er.async_get(hass).async_get(entity_id).disabled_by is None
    assert hass.states.get(entity_id).state == "idle"
    run_id, handler = await _run(panel, "hey_jarvis")
    await panel.client.send_bytes(bytes([handler]))
    assert (await _turn_events(panel, run_id))[-1] == {"kind": "end"}


async def test_an_entry_unloaded_while_voice_comes_back_keeps_no_satellite(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """A satellite waiting to come back is not added to an entry being unloaded."""
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    leave = asyncio.Event()
    leaving = PanelAssistSatellite.async_will_remove_from_hass

    async def _slow_leave(self: PanelAssistSatellite) -> None:
        await leave.wait()
        await leaving(self)

    with patch.object(PanelAssistSatellite, "async_will_remove_from_hass", _slow_leave):
        assert (await panel.configure(enabled=False))["success"]
        assert (await panel.configure(enabled=True))["success"]
        unload = hass.async_create_task(
            hass.config_entries.async_unload(entry.entry_id)
        )
        await asyncio.sleep(0)
        leave.set()
        assert await unload
        await hass.async_block_till_done()

    assert entry.entry_id not in satellites(hass)
    state = hass.states.get(entity_id)
    assert state is None or state.state == "unavailable"


async def test_a_new_satellite_of_a_panel_with_voice_off_starts_disabled(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    entity_id = _satellite(hass, entry)
    assert entity_id is not None
    assert er.async_get(hass).async_get(entity_id).disabled_by is (
        er.RegistryEntryDisabler.INTEGRATION
    )


async def test_a_satellite_disabled_by_the_user_is_left_alone(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    registry = er.async_get(hass)
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    for enabled in (False, True):
        assert (await panel.configure(enabled=enabled))["success"]
        await hass.async_block_till_done()
        assert registry.async_get(entity_id).disabled_by is (
            er.RegistryEntryDisabler.USER
        )


async def test_enabling_the_satellite_turns_voice_on_at_the_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """Enabling the entity Panel Assistant disabled asks the panel to turn voice on."""
    panel = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        ["state", "events", "voice", "management"],
    )
    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    entity_id = _satellite(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(entity_id).disabled_by is (
        er.RegistryEntryDisabler.INTEGRATION
    )

    registry.async_update_entity(entity_id, disabled_by=None)
    message = await panel.receive()
    event = message["event"]
    assert event["kind"] == "manage", event
    assert event["op"] == "settings"
    assert event["settings"] == {"voice_enabled": "true"}
    # The panel answers first and reports its voice configuration after.
    answered = await panel.send(
        {
            "type": "panel_assistant/command_result",
            "session": panel.token,
            "command_id": event["command_id"],
            "outcome": "applied",
        }
    )
    assert answered["success"], answered
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is None
    assert (await panel.configure(enabled=True))["success"]
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is None


async def test_a_refused_voice_on_disables_the_satellite_again(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    """An older panel that cannot take voice_enabled keeps its satellite disabled."""
    panel = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        ["state", "events", "voice", "management"],
    )
    assert (await panel.configure(enabled=False))["success"]
    await hass.async_block_till_done()
    entity_id = _satellite(hass, entry)
    registry = er.async_get(hass)

    registry.async_update_entity(entity_id, disabled_by=None)
    event = (await panel.receive())["event"]
    answered = await panel.send(
        {
            "type": "panel_assistant/command_result",
            "session": panel.token,
            "command_id": event["command_id"],
            "outcome": "refused",
            "code": "not_commandable",
        }
    )
    assert answered["success"], answered
    await hass.async_block_till_done()
    assert registry.async_get(entity_id).disabled_by is (
        er.RegistryEntryDisabler.INTEGRATION
    )

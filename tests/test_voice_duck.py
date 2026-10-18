"""The other players in a panel's area are turned down while it holds a conversation."""

import asyncio
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_config_flow,
    mock_integration,
    mock_platform,
    setup_test_component_platform,
)

from .test_transport import OTHER_DID, WsClientFactory, _receive
from .test_voice import (  # noqa: F401  (fixtures)
    FakePipeline,
    Panel,
    _hello,
    _ready,
    _run,
    _satellite,
    _turn_events,
    entry,
    pipeline,
)
from .test_voice_stream import _second_entry

LEVEL = 0.6
MUTE_ONLY = MediaPlayerEntityFeature.VOLUME_MUTE | MediaPlayerEntityFeature.VOLUME_STEP


class _PlayerFlow(ConfigFlow):
    """The flow Core expects behind the players' config entry."""

    VERSION = 1


@pytest.fixture(autouse=True)
def _player_flow() -> Iterator[None]:
    with mock_config_flow("test", _PlayerFlow):
        yield


class Player(MediaPlayerEntity):
    """Another integration's speaker, whose level snaps to 15 steps like a panel's."""

    _attr_should_poll = False
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.VOLUME_MUTE
    )

    def __init__(
        self,
        name: str,
        state: MediaPlayerState = MediaPlayerState.PLAYING,
        features: MediaPlayerEntityFeature | None = None,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_state = state
        self._attr_volume_level = LEVEL
        self._attr_is_volume_muted = False
        if features is not None:
            self._attr_supported_features = features
        self.changes: list[float | bool] = []

    async def async_set_volume_level(self, volume: float) -> None:
        self.changes.append(volume)
        self._attr_volume_level = round(volume * 15) / 15
        self.async_write_ha_state()

    async def async_mute_volume(self, mute: bool) -> None:
        self.changes.append(mute)
        self._attr_is_volume_muted = mute
        self.async_write_ha_state()

    @property
    def level(self) -> Any:
        return self.hass.states.get(self.entity_id).attributes.get("volume_level")


async def _room(
    hass: HomeAssistant,
    satellite_id: str,
    *players: Player,
    elsewhere: Player | None = None,
) -> None:
    """Put the panel and the players in one area, and one player in another."""

    async def setup_entry(hass: HomeAssistant, test_entry: ConfigEntry) -> bool:
        await hass.config_entries.async_forward_entry_setups(
            test_entry, ["media_player"]
        )
        return True

    everything = [*players, *([elsewhere] if elsewhere else [])]
    mock_integration(hass, MockModule("test", async_setup_entry=setup_entry))
    mock_platform(hass, "test.config_flow", None)
    setup_test_component_platform(
        hass, "media_player", everything, from_config_entry=True
    )
    test_entry = MockConfigEntry(domain="test")
    test_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(test_entry.entry_id)
    await hass.async_block_till_done()

    areas = ar.async_get(hass)
    office, kitchen = areas.async_create("Office"), areas.async_create("Kitchen")
    entities = er.async_get(hass)
    device_id = entities.async_get(satellite_id).device_id
    dr.async_get(hass).async_update_device(device_id, area_id=office.id)
    for player in players:
        entities.async_update_entity(player.entity_id, area_id=office.id)
    if elsewhere is not None:
        entities.async_update_entity(elsewhere.entity_id, area_id=kitchen.id)


def _reply(fake: FakePipeline, *, follow_up: bool) -> None:
    fake.events[4] = PipelineEvent(
        PipelineEventType.INTENT_END,
        {"intent_output": {"continue_conversation": follow_up}},
    )


async def _turn(panel: Panel, *, continued: bool = False) -> None:
    run_id, handler = await _run(panel, "hey_jarvis", continued=continued)
    await panel.client.send_bytes(bytes([handler]) + b"\x01\x02" * 160)
    await panel.client.send_bytes(bytes([handler]))
    await _turn_events(panel, run_id)


async def _played(hass: HomeAssistant, panel: Panel) -> None:
    played = await panel.send(
        {"type": "panel_assistant/voice_played", "session": panel.token}
    )
    assert played["success"], played
    await hass.async_block_till_done()


async def test_the_rooms_player_is_turned_down_for_the_turn_and_back_exactly(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)
    _reply(pipeline, follow_up=False)

    await _turn(panel)
    await hass.async_block_till_done()
    assert hass.states.get(satellite).state == "responding"
    assert speaker.level < LEVEL

    await _played(hass, panel)
    assert hass.states.get(satellite).state == "idle"
    assert speaker.level == LEVEL
    assert len(speaker.changes) == 2


async def test_only_sounding_players_in_the_area_are_touched_and_a_tv_is_muted(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    idle = Player("idle", MediaPlayerState.IDLE)
    tv = Player("tv", MediaPlayerState.ON, MUTE_ONLY)
    own = Player("own")
    kitchen = Player("kitchen")
    await _room(hass, satellite, idle, tv, own, elsewhere=kitchen)
    # The panel pauses its own player for a turn itself.
    er.async_get(hass).async_update_entity(
        own.entity_id, config_entry_id=entry.entry_id
    )
    _reply(pipeline, follow_up=False)

    await _turn(panel)
    await hass.async_block_till_done()
    assert hass.states.get(tv.entity_id).attributes["is_volume_muted"] is True

    await _played(hass, panel)
    assert hass.states.get(tv.entity_id).attributes["is_volume_muted"] is False
    assert tv.changes == [True, False]
    assert idle.changes == own.changes == kitchen.changes == []


async def test_a_level_someone_sets_during_the_conversation_is_kept(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)
    _reply(pipeline, follow_up=False)

    await _turn(panel)
    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": speaker.entity_id, "volume_level": 0.8},
        blocking=True,
        context=Context(),
    )
    await _played(hass, panel)

    assert speaker.level == 0.8


async def test_the_room_stays_down_through_a_follow_up_and_comes_back_once(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)

    _reply(pipeline, follow_up=True)
    await _turn(panel)
    await _played(hass, panel)
    assert speaker.level < LEVEL

    _reply(pipeline, follow_up=False)
    await _turn(panel, continued=True)
    await _played(hass, panel)
    assert speaker.level == LEVEL
    assert len(speaker.changes) == 2


async def test_a_follow_up_that_never_starts_lets_the_room_go(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)
    _reply(pipeline, follow_up=True)

    await _turn(panel)
    await _played(hass, panel)
    assert speaker.level < LEVEL

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    assert speaker.level == LEVEL


async def test_a_reply_the_panel_never_reports_lets_the_room_go_with_a_warning(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)
    _reply(pipeline, follow_up=False)

    await _turn(panel)
    await hass.async_block_till_done()
    assert speaker.level < LEVEL

    with caplog.at_level(logging.WARNING):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
        await hass.async_block_till_done()
    assert speaker.level == LEVEL
    assert f"A voice turn on {satellite} did not end" in caplog.text


class SlowPlayer(Player):
    """A speaker that takes its time over the first change of level."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.gate = asyncio.Event()
        self.asked = 0

    async def async_set_volume_level(self, volume: float) -> None:
        self.asked += 1
        if self.asked == 1:
            await self.gate.wait()
        await super().async_set_volume_level(volume)


async def test_a_turn_over_before_a_slow_player_turned_down_still_gives_it_back(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = SlowPlayer("speaker")
    await _room(hass, satellite, speaker)
    # Nothing was heard: the turn ends without a reply.
    pipeline.events = [
        PipelineEvent(PipelineEventType.STT_START),
        PipelineEvent(PipelineEventType.ERROR, {"code": "stt-no-text-recognized"}),
        PipelineEvent(PipelineEventType.RUN_END),
    ]

    await _turn(panel)
    # The turn is over and the room let go while the player is still busy.
    assert hass.states.get(satellite).state == "idle"
    assert speaker.level == LEVEL

    speaker.gate.set()
    await hass.async_block_till_done()
    assert speaker.level == LEVEL
    assert len(speaker.changes) == 2


async def test_a_turn_the_panel_leaves_lets_the_room_go(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)

    run_id, handler = await _run(panel, "hey_jarvis")
    await panel.client.send_bytes(bytes([handler]) + b"\x01\x02" * 160)
    await hass.async_block_till_done()
    assert speaker.level < LEVEL

    left = await panel.send({"type": "unsubscribe_events", "subscription": run_id})
    assert left["success"], left
    await hass.async_block_till_done()
    assert pipeline.cancelled
    assert speaker.level == LEVEL


async def test_two_panels_in_one_room_give_the_player_back_after_both(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    pipeline: FakePipeline,  # noqa: F811
) -> None:
    panel, satellite = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    other = await _second_entry(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(
        _hello(["state", "events", "voice"]) | {"did": OTHER_DID}
    )
    hello = await _receive(client)
    assert hello["success"], hello
    second = Panel(client, hello)
    assert (await second.configure())["success"]
    await hass.async_block_till_done()
    speaker = Player("speaker")
    await _room(hass, satellite, speaker)
    other_satellite = _satellite(hass, other)
    assert other_satellite is not None
    office = (
        dr.async_get(hass)
        .async_get(er.async_get(hass).async_get(satellite).device_id)
        .area_id
    )
    dr.async_get(hass).async_update_device(
        er.async_get(hass).async_get(other_satellite).device_id, area_id=office
    )
    _reply(pipeline, follow_up=False)

    await _turn(panel)
    await _turn(second)
    await _played(hass, panel)
    assert speaker.level < LEVEL

    await _played(hass, second)
    assert speaker.level == LEVEL
    assert len(speaker.changes) == 2

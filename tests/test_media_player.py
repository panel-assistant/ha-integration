"""The panel's speaker as a media player: what it shows and what it sends."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import transport
from custom_components.panel_assistant.contract import CONTRACT

from .test_native import DESCRIPTORS, _hello, _observations, _report
from .test_transport import DID, WsClientFactory, _send
from .test_transport_commands import (
    ALL_CAPABILITIES,
    Panel,
    _call,
    _connect,
    native,  # noqa: F401  # the fixture
)

# Looked up at use, so a catalogue without the channel fails each test.
MEDIA = {entry["channel"]: entry for entry in CONTRACT["channels"]}.get("media", {})


def _player(hass: HomeAssistant) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        MEDIA_PLAYER_DOMAIN, "panel_assistant", f"{DID}_media"
    )
    assert entity_id is not None
    return entity_id


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


async def _sent(
    hass: HomeAssistant, panel: Panel, service: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Call a media player service and return the command the panel receives."""
    entity_id = _player(hass)
    call = _call(hass, MEDIA_PLAYER_DOMAIN, service, {"entity_id": entity_id, **data})
    command = await panel.command()
    assert command["session"] == panel.token
    ack = await panel.answer(command["command_id"], "applied")
    assert ack["success"], ack
    async with asyncio.timeout(5):
        assert await call is None
    return command


@pytest.fixture
async def panel(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> Panel:
    """A synced native session that describes every catalogue channel."""
    return await _connect(hass, hass_ws_client, hass_read_only_access_token)


# ---------------------------------------------------------------------------
# Observations.


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ({"state": "idle", "muted": False}, True),
        ({"state": "playing", "muted": True}, True),
        ({"state": "paused", "muted": False}, True),
        ({"state": "buffering", "muted": False}, True),
        ({"state": "stopped", "muted": False}, False),
        ({"state": "playing"}, False),
        ({"state": "playing", "muted": "false"}, False),
        ({"state": "playing", "muted": 0}, False),
        ({"muted": False}, False),
        ("playing", False),
        (None, False),
    ],
)
def test_media_observation_values(value: Any, accepted: bool) -> None:
    """Only the four states with a strict mute flag are a media observation."""
    descriptor = transport.DESCRIPTOR_SCHEMA(
        {key: v for key, v in MEDIA.items() if key not in ("value", "attributes")}
    )
    validate = transport._VALUE_VALIDATORS[descriptor["platform"]]
    if accepted:
        assert validate(value | {"extra": 1}, descriptor) == value
    else:
        with pytest.raises(transport.ValueRejected):
            validate(value, descriptor)


async def test_a_rejected_media_report_is_named_in_the_reply(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A well-formed media value is stored; a malformed one is rejected."""
    await _observe(hass, panel, "media", {"state": "playing", "muted": False})
    response = await _send(
        panel.client,
        _report(
            panel.token,
            "delta",
            [{"channel": "media", "state": "known", "value": {"state": "loud"}}],
        ),
    )
    assert response["success"], response
    assert [item["channel"] for item in response["result"]["rejected"]] == ["media"]
    assert hass.states.get(_player(hass)).state == MediaPlayerState.PLAYING


@pytest.mark.parametrize(
    ("reported", "state"),
    [
        ("idle", MediaPlayerState.IDLE),
        ("playing", MediaPlayerState.PLAYING),
        ("paused", MediaPlayerState.PAUSED),
        ("buffering", MediaPlayerState.BUFFERING),
    ],
)
async def test_state_mute_and_volume_render_from_reports(
    hass: HomeAssistant, panel: Panel, reported: str, state: MediaPlayerState
) -> None:
    """The player shows the media report and the volume channel's report."""
    entity_id = _player(hass)
    await _observe(hass, panel, "media", {"state": reported, "muted": True})
    await _observe(hass, panel, "volume", 35)

    shown = hass.states.get(entity_id)
    assert shown is not None
    assert shown.state == state
    assert shown.attributes["is_volume_muted"] is True
    assert shown.attributes["volume_level"] == pytest.approx(0.35)
    assert shown.attributes["supported_features"] == (
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

    await _observe(hass, panel, "media", {"state": reported, "muted": False})
    assert hass.states.get(entity_id).attributes["is_volume_muted"] is False


# ---------------------------------------------------------------------------
# Commands.


@pytest.mark.parametrize(
    ("service", "data", "channel", "value"),
    [
        ("media_pause", {}, "media", {"action": "pause"}),
        ("media_play", {}, "media", {"action": "resume"}),
        ("media_stop", {}, "media", {"action": "stop"}),
        (
            "volume_mute",
            {"is_volume_muted": True},
            "media",
            {"action": "mute", "muted": True},
        ),
        (
            "volume_mute",
            {"is_volume_muted": False},
            "media",
            {"action": "mute", "muted": False},
        ),
        ("volume_set", {"volume_level": 0.42}, "volume", 42),
        (
            "play_media",
            {
                "media_content_id": "https://radio.example/stream.mp3",
                "media_content_type": "music",
            },
            "media",
            {
                "action": "play",
                "url": "https://radio.example/stream.mp3",
                "announce": False,
            },
        ),
        (
            "play_media",
            {
                "media_content_id": "https://radio.example/chime.mp3",
                "media_content_type": "music",
                "announce": True,
            },
            "media",
            {
                "action": "play",
                "url": "https://radio.example/chime.mp3",
                "announce": True,
            },
        ),
    ],
)
async def test_each_service_sends_its_command(
    hass: HomeAssistant,
    panel: Panel,
    service: str,
    data: dict[str, Any],
    channel: str,
    value: Any,
) -> None:
    """Each service reaches the panel as exactly this channel and value."""
    command = await _sent(hass, panel, service, data)
    assert command["channel"] == channel
    assert command["value"] == value
    assert type(command["value"]) is type(value)


async def test_volume_up_steps_through_the_volume_channel(
    hass: HomeAssistant, panel: Panel
) -> None:
    """A volume step sets the volume channel one step above its report."""
    await _observe(hass, panel, "volume", 30)
    command = await _sent(hass, panel, "volume_up", {})
    assert (command["channel"], command["value"]) == ("volume", 40)


async def test_a_home_assistant_url_is_sent_relative(
    hass: HomeAssistant, panel: Panel
) -> None:
    """The panel resolves a Home Assistant URL against the address it reached."""
    await hass.config.async_update(internal_url="http://ha.example:8123")

    command = await _sent(
        hass,
        panel,
        "play_media",
        {
            "media_content_id": "http://ha.example:8123/api/tts_proxy/reply.mp3",
            "media_content_type": "music",
        },
    )
    assert command["value"] == {
        "action": "play",
        "url": "/api/tts_proxy/reply.mp3",
        "announce": False,
    }


async def test_a_media_source_announcement_plays_a_signed_relative_url(
    hass: HomeAssistant, panel: Panel, tmp_path: Path
) -> None:
    """A media source resolves to a signed path the panel resolves itself."""
    (tmp_path / "chime.mp3").write_bytes(b"\xff\xfb")
    hass.config.media_dirs["local"] = str(tmp_path)
    await hass.async_block_till_done()

    command = await _sent(
        hass,
        panel,
        "play_media",
        {
            "media_content_id": "media-source://media_source/local/chime.mp3",
            "media_content_type": "music",
            "announce": True,
        },
    )
    assert command["channel"] == "media"
    value = command["value"]
    assert set(value) == {"action", "url", "announce"}
    assert value["action"] == "play"
    assert value["announce"] is True
    assert value["url"].startswith("/media/local/chime.mp3?authSig=")


async def test_browsing_offers_audio_only(
    hass: HomeAssistant, panel: Panel, tmp_path: Path
) -> None:
    """Browsing a media folder lists its audio and hides its video."""
    (tmp_path / "chime.mp3").write_bytes(b"\xff\xfb")
    (tmp_path / "clip.mp4").write_bytes(b"\x00")
    hass.config.media_dirs["local"] = str(tmp_path)
    entity_id = _player(hass)
    player = hass.data[MEDIA_PLAYER_DOMAIN].get_entity(entity_id)

    browsed: BrowseMedia = await player.async_browse_media(
        "app", "media-source://media_source/local/"
    )
    assert [child.title for child in browsed.children or ()] == ["chime.mp3"]


# ---------------------------------------------------------------------------
# Only a panel that describes the channel has the player.


async def test_no_player_without_the_described_channel(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel that does not describe media has no player; unsupported removes it."""
    unique_id = f"{DID}_media"
    registry = er.async_get(hass)

    async def hello(channels: list[dict[str, Any]], **extra: Any) -> None:
        client = await hass_ws_client(hass, hass_read_only_access_token)
        message = _hello(channels) | {"capabilities": ALL_CAPABILITIES} | extra
        response = await _send(client, message)
        assert response["success"], response
        token = response["result"]["session"]
        for sync, observations in (
            ("full_begin", []),
            ("full_end", _observations(channels)),
        ):
            assert (await _send(client, _report(token, sync, observations)))["success"]
        await hass.async_block_till_done()

    without = [item for item in DESCRIPTORS if item["channel"] != "media"]
    await hello(without)
    assert (
        registry.async_get_entity_id("media_player", "panel_assistant", unique_id)
        is None
    )
    assert not hass.states.async_entity_ids("media_player")

    await hello(DESCRIPTORS)
    entity_id = registry.async_get_entity_id(
        "media_player", "panel_assistant", unique_id
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == MediaPlayerState.IDLE

    await hello(without)
    # Merely left out: the player stays, unavailable.
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    await hello(without, unsupported=["media"])
    assert (
        registry.async_get_entity_id("media_player", "panel_assistant", unique_id)
        is None
    )


@pytest.mark.parametrize("offered", [True, False])
async def test_the_media_capability_is_granted_whenever_offered(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    offered: bool,
) -> None:
    """A panel describes media only once a session grants it."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    capabilities = [*ALL_CAPABILITIES, "media"] if offered else list(ALL_CAPABILITIES)
    response = await _send(client, _hello(DESCRIPTORS) | {"capabilities": capabilities})
    assert response["success"], response
    assert ("media" in response["result"]["capabilities"]) is offered

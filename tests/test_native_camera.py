"""Standard camera behaviour through a real panel transport session."""

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.camera import CameraEntityFeature, async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN

from .test_native import DESCRIPTORS, _hello, _native_entries, _report, _session, _sync
from .test_native import native as native
from .test_transport import DID, WsClientFactory, _send

CAMERA = next(item for item in DESCRIPTORS if item["channel"] == "camera_enabled") | {
    "platform": "camera",
    "translation_key": "camera",
    "unique_suffix": "camera",
    "entity_category": None,
    "enabled_default": True,
}


async def _camera(
    hass: HomeAssistant, entry: MockConfigEntry, client: Any
) -> tuple[Any, str]:
    token = await _session(client, [CAMERA])
    await _sync(hass, client, token, [CAMERA])
    entries = _native_entries(hass, entry.entry_id)
    assert f"{DID}_camera" in entries
    return hass.data["camera"].get_entity(entries[f"{DID}_camera"].entity_id), token


async def _on(hass: HomeAssistant, client: Any, token: str, value: bool) -> None:
    response = await _send(
        client,
        _report(
            token,
            "delta",
            [{"channel": "camera_enabled", "state": "known", "value": value}],
        ),
    )
    assert response["success"]
    assert response["result"]["rejected"] == []
    await hass.async_block_till_done()


async def test_camera_off_is_available_and_refuses_media_then_fetches_fresh_stills(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Off is controllable; on resolves media from the verified panel client."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    camera, token = await _camera(hass, native, client)
    assert camera.available
    assert hass.states.get(camera.entity_id).state == "idle"
    assert not camera.is_on
    assert camera.supported_features & CameraEntityFeature.ON_OFF
    assert await camera.stream_source() is None
    with pytest.raises(HomeAssistantError):
        await async_get_image(hass, camera.entity_id)
    await _on(hass, client, token, True)
    assert await camera.stream_source() == "rtsp://panel.local:8554/live"
    assert camera.supported_features & CameraEntityFeature.STREAM
    jpeg = b"\xff\xd8fresh\xff\xd9"
    with patch.object(
        native.runtime_data.client, "_async_get_bounded", return_value=jpeg
    ) as fetch:
        assert (await async_get_image(hass, camera.entity_id)).content == jpeg
        assert (await async_get_image(hass, camera.entity_id)).content == jpeg
    assert fetch.call_count == 2
    assert (
        str(fetch.call_args.args[0])
        == "http://panel.local:8888/api/v1/camera/snapshot.jpg"
    )


@pytest.mark.parametrize("ending", ["off", "disconnect", "off_on", "unload"])
async def test_camera_discards_inflight_still_and_revokes_old_hls(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    ending: str,
) -> None:
    """A privacy stop or connection loss invalidates old media across re-enable."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    camera, token = await _camera(hass, native, client)
    await _on(hass, client, token, True)
    stream = await camera.async_create_stream()
    stream.add_provider("hls")
    assert stream.endpoint_url("hls")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def jpeg(*args: Any) -> bytes:
        entered.set()
        await finish.wait()
        return b"\xff\xd8old\xff\xd9"

    with patch.object(
        native.runtime_data.client, "_async_get_bounded", side_effect=jpeg
    ):
        pending = asyncio.create_task(camera.async_camera_image())
        await entered.wait()
        if ending == "disconnect":
            await client.close()
            await hass.async_block_till_done()
        elif ending == "unload":
            assert await hass.config_entries.async_unload(native.entry_id)
        else:
            await _on(hass, client, token, False)
            assert camera.available
            assert not camera.is_on
            if ending == "off_on":
                await _on(hass, client, token, True)
        finish.set()
        assert await pending is None
    assert stream.access_token is None
    assert stream.outputs() == {}
    if ending != "off_on":
        assert await camera.async_create_stream() is None
    if ending == "disconnect":
        assert not camera.available


@pytest.mark.parametrize("legacy_owner", ["self", "other"])
@pytest.mark.parametrize("advertised", ["camera", "unsupported"])
async def test_camera_replaces_only_its_own_legacy_native_switch(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    legacy_owner: str,
    advertised: str,
) -> None:
    """Retirement cannot delete an MQTT switch or a different entry's entity."""
    registry = er.async_get(hass)
    other = MockConfigEntry(domain=DOMAIN, title="other")
    other.add_to_hass(hass)
    owned = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{DID}_camera_enabled",
        config_entry=native if legacy_owner == "self" else other,
    )
    mqtt = registry.async_get_or_create(
        "switch", "mqtt", f"{DID}_camera_enabled", config_entry=native
    )
    foreign = registry.async_get_or_create(
        "switch", DOMAIN, "foreign_camera_enabled", config_entry=other
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    if advertised == "camera":
        await _camera(hass, native, client)
    else:
        response = await _send(client, _hello([]) | {"unsupported": ["camera_enabled"]})
        assert response["success"]
        await hass.async_block_till_done()
    assert (registry.async_get(owned.entity_id) is None) == (legacy_owner == "self")
    assert registry.async_get(mqtt.entity_id) is not None
    assert registry.async_get(foreign.entity_id) is not None


async def test_camera_off_during_webrtc_registration_releases_the_provider(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A provider registered after off cannot retain camera sessions or preload."""
    from unittest.mock import AsyncMock, Mock

    from homeassistant.components.camera.webrtc import async_register_webrtc_provider

    client = await hass_ws_client(hass, hass_read_only_access_token)
    camera, token = await _camera(hass, native, client)
    entered, finish = asyncio.Event(), asyncio.Event()
    registered: set[str] = set()

    async def register(entity: Any) -> None:
        entered.set()
        await finish.wait()
        registered.add(entity.entity_id)

    async def unregister(entity: Any) -> None:
        registered.discard(entity.entity_id)

    provider = Mock(
        domain="go2rtc",
        async_is_supported=lambda source: True,
        async_register_camera=register,
        async_unregister_camera=unregister,
        async_handle_async_webrtc_offer=AsyncMock(),
        async_on_webrtc_candidate=AsyncMock(),
    )
    remove = async_register_webrtc_provider(hass, provider)
    await hass.async_block_till_done()
    await _send(
        client,
        _report(
            token,
            "delta",
            [
                {"channel": "camera_enabled", "state": "known", "value": True},
            ],
        ),
    )
    await entered.wait()
    try:
        await _send(
            client,
            _report(
                token,
                "delta",
                [
                    {"channel": "camera_enabled", "state": "known", "value": False},
                ],
            ),
        )
    finally:
        finish.set()
    await hass.async_block_till_done()
    assert camera.webrtc_provider is None
    assert registered == set()
    remove()
    await hass.async_block_till_done()


async def test_camera_rejects_still_and_stream_creation_from_an_old_owner(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A late created stream cannot survive off and a new client address is used."""
    from custom_components.panel_assistant.client import normalize_address

    client = await hass_ws_client(hass, hass_read_only_access_token)
    camera, token = await _camera(hass, native, client)
    await _on(hass, client, token, True)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def settings(*args: Any) -> Any:
        from homeassistant.components.camera import DynamicStreamSettings

        entered.set()
        await finish.wait()
        return DynamicStreamSettings()

    with patch(
        "homeassistant.components.camera.get_dynamic_camera_stream_settings",
        side_effect=settings,
    ):
        pending = asyncio.create_task(camera.async_create_stream())
        await entered.wait()
        try:
            await _on(hass, client, token, False)
        finally:
            finish.set()
        assert await pending is None
    await _on(hass, client, token, True)
    native.runtime_data.client.address = normalize_address("[2001:db8::2]:9999")
    assert await camera.stream_source() == "rtsp://[2001:db8::2]:8554/live"
    with patch.object(
        native.runtime_data.client, "_async_get_bounded", return_value=b"fresh"
    ) as fetch:
        assert (await async_get_image(hass, camera.entity_id)).content == b"fresh"
    assert (
        str(fetch.call_args.args[0])
        == "http://[2001:db8::2]:9999/api/v1/camera/snapshot.jpg"
    )


async def test_legacy_camera_switch_identity_and_customisation_survive_reconnect(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An older panel's switch hello is not proof that its camera replaced it."""
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_camera_enabled", config_entry=native
    )
    legacy = registry.async_update_entity(
        legacy.entity_id,
        new_entity_id="switch.room_camera_privacy",
        name="Room camera privacy",
        icon="mdi:cctv",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    registry.async_update_entity_options(
        legacy.entity_id, DOMAIN, {"custom_choice": "retain"}
    )
    fields = ("entity_id", "unique_id", "name", "icon", "disabled_by", "options")
    expected = tuple(
        getattr(registry.async_get(legacy.entity_id), field) for field in fields
    )
    descriptor = CAMERA | {
        "platform": "switch",
        "translation_key": "camera_enabled",
        "unique_suffix": "camera_enabled",
        "entity_category": "config",
        "enabled_default": False,
    }
    for _connection in range(2):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello([descriptor]))
        assert response["success"]
        assert response["result"]["channels"]["unknown"] == []
        await _sync(hass, client, response["result"]["session"], [descriptor])
        retained = registry.async_get(legacy.entity_id)
        assert retained is not None
        assert tuple(getattr(retained, field) for field in fields) == expected
        assert registry.async_get_entity_id("camera", DOMAIN, f"{DID}_camera") is None
        await client.close()
        await hass.async_block_till_done()
        retained = registry.async_get(legacy.entity_id)
        assert retained is not None
        assert tuple(getattr(retained, field) for field in fields) == expected


async def test_legacy_camera_switch_stays_controllable_until_camera_replaces_it(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """HA-first rollout preserves old switch control until the app offers a camera."""
    from .test_native import _setup
    from .test_transport_commands import Panel, _call

    entry = await _setup(
        hass, hass_read_only_user.id, native=True, options={"authority": "native"}
    )
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_camera_enabled", config_entry=entry
    )
    legacy = registry.async_update_entity(
        legacy.entity_id,
        new_entity_id="switch.room_camera_privacy",
        name="Room camera privacy",
        icon="mdi:cctv",
    )
    descriptor = CAMERA | {
        "platform": "switch",
        "translation_key": "camera_enabled",
        "unique_suffix": "camera_enabled",
        "entity_category": "config",
        "enabled_default": False,
    }
    for _connection in range(2):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(
            client,
            _hello([descriptor])
            | {
                "capabilities": ["state", "events", "commands", "approval"],
            },
        )
        assert response["success"]
        panel = Panel(client, response)
        await _sync(hass, client, panel.token, [descriptor])
        state = hass.states.get(legacy.entity_id)
        assert state is not None
        assert state.state == "off"
        call = _call(hass, "switch", "turn_on", {"entity_id": legacy.entity_id})
        command = await panel.command()
        assert command["channel"] == "camera_enabled"
        assert command["value"] is True
        assert (await panel.answer(command["command_id"], "applied"))["success"]
        async with asyncio.timeout(5):
            await call
        await _on(hass, client, panel.token, True)
        assert hass.states.get(legacy.entity_id).state == "on"
        retained = registry.async_get(legacy.entity_id)
        assert (retained.entity_id, retained.name, retained.icon) == (
            legacy.entity_id,
            "Room camera privacy",
            "mdi:cctv",
        )
        await client.close()
        await hass.async_block_till_done()
    client = await hass_ws_client(hass, hass_read_only_access_token)
    camera, _token = await _camera(hass, entry, client)
    assert camera.available
    assert registry.async_get(legacy.entity_id) is None
    await client.close()
    await hass.async_block_till_done()
    # A rollback to the old app can describe its switch again after retirement.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client, [descriptor])
    await _sync(hass, client, token, [descriptor])
    restored = registry.async_get_entity_id("switch", DOMAIN, f"{DID}_camera_enabled")
    assert restored is not None
    assert hass.states.get(restored).state == "off"

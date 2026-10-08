"""Channels a session leaves out stay; channels a panel cannot serve go.

A channel missing from one session is only unavailable. A hello that lists a
channel as unsupported removes its native entity, except the reboot and reload
actions, which a transient loss of the root helper must not erase.
"""

from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.panel_assistant.const import DOMAIN

from .test_native import DESCRIPTORS, PANEL_HELLO, _hello, _setup, panel_patches
from .test_transport import DID, WsClientFactory, _send
from .test_transport_contract import ANDROID_PRODUCER


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


def _panel_descriptors(*channels: str) -> list[dict[str, Any]]:
    """Return the panel's own descriptors of these channels, from its hello."""
    return [
        descriptor
        for descriptor in PANEL_HELLO["channels"]
        if descriptor["channel"] in channels
    ]


def _producer_hello() -> dict[str, Any]:
    """Return the hello the Android producer sent, byte for byte but its ID."""
    (message,) = (
        item["message"]
        for item in ANDROID_PRODUCER["transportMessages"]
        if item["name"] == "hello"
    )
    return {key: value for key, value in message.items() if key != "id"}


async def _hello_on_new_socket(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    client = await hass_ws_client(hass, token)
    response = await _send(client, message)
    assert response.get("success"), response
    await hass.async_block_till_done()
    result: dict[str, Any] = response["result"]
    return result


async def test_a_session_omitting_a_channel_deletes_nothing(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A channel one session leaves out stays supported, registered and rendered."""
    entry = await _setup(hass, hass_read_only_user.id)
    registry = er.async_get(hass)
    both = _panel_descriptors("relay1", "relay2")
    # A known channel described in a shape the catalogue does not know.
    reshaped = deepcopy(_panel_descriptors("relay64")[0]) | {"translation_key": "x"}

    first = await _hello_on_new_socket(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello([*both, reshaped]),
    )
    assert first["channels"]["unknown"] == ["relay64"]
    relay2_id = registry.async_get_entity_id("switch", DOMAIN, f"{DID}_relay2")
    assert relay2_id is not None
    relay2 = registry.async_get(relay2_id)
    assert relay2 is not None
    assert hass.states.get(relay2_id) is not None

    await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _hello(both[:1])
    )

    assert registry.entities.get_entry(relay2.id) == relay2
    state = hass.states.get(relay2_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # Nor does a later load whose session describes it no more.
    with panel_patches():
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _hello(both[:1])
    )

    assert registry.entities.get_entry(relay2.id) == relay2


async def test_a_native_born_reboot_survives_a_later_unsupported_hello(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Transient helper loss also preserves an action created without MQTT."""
    await _setup(hass, hass_read_only_user.id)
    registry = er.async_get(hass)
    with panel_patches():
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
        entity_id = registry.async_get_entity_id("button", DOMAIN, f"{DID}_reboot")
        assert entity_id is not None
        created = registry.async_get(entity_id)
        assert created is not None
        registry.async_update_entity(entity_id, name="My native reboot")
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([]) | {"unsupported": ["reboot"]},
        )
    kept = registry.entities.get_entry(created.id)
    assert kept is not None
    assert (kept.entity_id, kept.name) == (entity_id, "My native reboot")
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_UNAVAILABLE
    with panel_patches():
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
    recovered = registry.entities.get_entry(created.id)
    assert recovered is not None
    assert (recovered.entity_id, recovered.name) == (entity_id, "My native reboot")


async def test_a_first_unsupported_reboot_creates_no_native_button(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A rootless first hello cannot invent a reboot action."""
    entry = await _setup(hass, hass_read_only_user.id)
    await _hello_on_new_socket(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello([]) | {"unsupported": ["reboot"]},
    )
    assert (
        er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{DID}_reboot")
        is None
    )
    assert entry.state is ConfigEntryState.LOADED

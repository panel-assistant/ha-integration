"""The channels a panel supports: what a cutover may move, and what it keeps.

A native move takes an MQTT entity only once the panel has described its
channel in a hello, and a channel one session leaves out stays supported.
"""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store

from custom_components.panel_assistant import guards
from custom_components.panel_assistant.const import (
    CONF_CUTOVER,
    CONF_SUPPORTED_CHANNELS,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.contract import catalogue_channel_for_suffix
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_cutover import NATIVE, _issue, _mqtt, _record, _reload
from .test_native import PANEL_HELLO, _hello, _setup, panel_patches
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


def _supported(entry: MockConfigEntry) -> list[str]:
    stored: dict[str, Any] = entry.data[CONF_SUPPORTED_CHANNELS]
    assert stored["did"] == DID
    channels: list[str] = stored["channels"]
    return channels


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
    entry = await _setup(hass, hass_read_only_user.id, native=True, described=None)
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
    assert _supported(entry) == ["relay1", "relay2"]
    relay2_id = registry.async_get_entity_id("switch", DOMAIN, f"{DID}_relay2")
    assert relay2_id is not None
    relay2 = registry.async_get(relay2_id)
    assert relay2 is not None
    assert hass.states.get(relay2_id) is not None

    await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _hello(both[:1])
    )

    assert _supported(entry) == ["relay1", "relay2"]
    assert registry.entities.get_entry(relay2.id) == relay2
    state = hass.states.get(relay2_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # Nor does a later load whose session describes it no more.
    await _reload(hass, entry)
    await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _hello(both[:1])
    )

    assert _supported(entry) == ["relay1", "relay2"]
    assert registry.entities.get_entry(relay2.id) == relay2


async def test_a_channel_the_panel_never_described_stays_with_mqtt_until_it_does(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_storage: dict[str, Any],
) -> None:
    """Nothing moves before a hello, and only what the panel described moves."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("switch", "relay2", {})])
    registry = er.async_get(hass)
    relay1 = registry.async_get(mqtt["entity_ids"]["relay1"])
    relay2 = registry.async_get(mqtt["entity_ids"]["relay2"])
    assert relay1 is not None
    assert relay2 is not None

    with (
        panel_patches(),
        patch.object(
            hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
        ) as reload,
    ):
        entry = await _setup(
            hass, hass_read_only_user.id, native=True, options=NATIVE, described=None
        )

        # No hello yet: the panel may support neither channel, so none moves.
        assert CONF_CUTOVER not in entry.data
        assert registry.entities.get_entry(relay1.id) == relay1
        assert registry.entities.get_entry(relay2.id) == relay2

        # The Android producer's own hello describes relay1 alone.
        result = await _hello_on_new_socket(
            hass, hass_ws_client, hass_read_only_access_token, _producer_hello()
        )
        assert result["mqtt_discovery"] == "announce"
        assert reload.call_count == 1
        assert entry.state is ConfigEntryState.LOADED
        assert _supported(entry) == ["relay1"]

        moved = registry.entities.get_entry(relay1.id)
        assert moved is not None
        assert (moved.platform, moved.unique_id) == (DOMAIN, f"{DID}_relay1")
        record = _record(entry)
        assert record["state"] == "complete"
        assert record["unmigrated"] == [
            {
                "registry_id": relay2.id,
                "entity_id": relay2.entity_id,
                "unique_suffix": "relay2",
                "reason": "not_described",
                "customised": False,
                "channel": "relay2",
            }
        ]
        # It stays MQTT's, enabled and never quarantined, and holds the
        # panel's MQTT discovery announced.
        assert registry.entities.get_entry(relay2.id) == relay2
        assert record.get("quarantined", []) == []
        assert (
            _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id)
            is None
        )
        result = await _hello_on_new_socket(
            hass, hass_ws_client, hass_read_only_access_token, _producer_hello()
        )
        assert result["mqtt_discovery"] == "announce"
        assert reload.call_count == 1

        # The supported channels outlive the load, in the stored entry.
        await flush_store(hass.config_entries._store)
        (stored,) = (
            item
            for item in hass_storage["core.config_entries"]["data"]["entries"]
            if item["entry_id"] == entry.entry_id
        )
        assert stored["data"][CONF_SUPPORTED_CHANNELS] == {
            "did": DID,
            "channels": ["relay1"],
        }

        # Once a hello describes relay2, the next pass moves it too.
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello(_panel_descriptors("relay1", "relay2")),
        )
        assert reload.call_count == 2
        assert entry.state is ConfigEntryState.LOADED

    assert _supported(entry) == ["relay1", "relay2"]
    moved = registry.entities.get_entry(relay2.id)
    assert moved is not None
    assert (moved.entity_id, moved.platform, moved.unique_id) == (
        relay2.entity_id,
        DOMAIN,
        f"{DID}_relay2",
    )
    record = _record(entry)
    assert record["state"] == "complete"
    assert record["unmigrated"] == []
    assert set(record["entities"]) == {relay1.id, relay2.id}
    result = await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _producer_hello()
    )
    assert result["mqtt_discovery"] == "withdraw"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"][CONF_SUPPORTED_CHANNELS] == {
        "did": "**REDACTED**",
        "channels": ["relay1", "relay2"],
    }


async def test_a_new_panel_identity_starts_its_own_supported_channels(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Channels another identity described admit nothing for this one."""
    _mqtt(hass, [("switch", "relay1", {})])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: "panel.local",
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
            CONF_SUPPORTED_CHANNELS: {"did": "e" * 64, "channels": ["relay1"]},
        },
        options=NATIVE,
    )
    entry.add_to_hass(hass)
    with panel_patches():
        assert await async_setup_component(
            hass, DOMAIN, {DOMAIN: {"native_entities": True}}
        )
        await hass.async_block_till_done()

    assert CONF_CUTOVER not in entry.data
    assert entry.runtime_data.cutover_reconciliation_pending


async def test_a_leftover_waiting_for_its_channel_is_never_quarantined(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Only a channel the panel never described keeps its MQTT entity live."""
    mqtt = _mqtt(
        hass,
        [("switch", "relay1", {}), ("switch", "relay2", {}), ("switch", "mystery", {})],
    )
    registry = er.async_get(hass)
    relay2 = registry.async_get(mqtt["entity_ids"]["relay2"])
    assert relay2 is not None
    # Every entity counts as loaded at once, so each quarantine decision is
    # made before the test looks, whatever the machine's speed.
    with patch.object(guards, "_async_wait_loaded", AsyncMock()):
        entry = await _setup(
            hass,
            hass_read_only_user.id,
            native=True,
            options=NATIVE,
            described={"relay1"},
        )
        await _settle(hass)

    mystery = registry.async_get(mqtt["entity_ids"]["mystery"])
    assert mystery is not None
    assert _record(entry).get("quarantined") == [mystery.id]
    assert registry.entities.get_entry(relay2.id) == relay2
    assert {
        item["unique_suffix"]: item["reason"] for item in _record(entry)["unmigrated"]
    } == {
        "relay2": "not_described",
        "mystery": "unknown_suffix",
    }


@pytest.mark.parametrize(
    ("platform", "suffix", "channel"),
    [
        ("switch", "relay1", "relay1"),
        ("switch", "relay64", "relay64"),
        ("light", "button_led2", "button_led2"),
        ("switch", "voice_assistant", "voice_enabled"),
        ("sensor", "diag_cpu", "diag_cpu"),
        ("switch", "relay0", None),
        ("switch", "relay65", None),
        ("light", "relay1", None),
        ("switch", "mystery", None),
    ],
)
def test_a_suffix_names_the_channel_a_hello_describes(
    platform: str, suffix: str, channel: str | None
) -> None:
    """A family member's channel carries its index; a suffix need not be a channel."""
    assert catalogue_channel_for_suffix(platform, suffix) == channel

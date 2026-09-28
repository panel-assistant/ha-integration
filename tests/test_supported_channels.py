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
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {}),
            ("switch", "relay2", {}),
            ("sensor", "diag_cpu", {}),
        ],
    )
    registry = er.async_get(hass)
    relay1 = registry.async_get(mqtt["entity_ids"]["relay1"])
    relay2 = registry.async_get(mqtt["entity_ids"]["relay2"])
    diagnostic = registry.async_get(mqtt["entity_ids"]["diag_cpu"])
    assert relay1 is not None
    assert relay2 is not None
    assert diagnostic is not None

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
        assert {
            row["unique_suffix"]: row["reason"] for row in record["unmigrated"]
        } == {
            "relay2": "not_described",
            "diag_cpu": "not_described",
        }
        # The command stays MQTT's but is suspended. The read-only diagnostic
        # stays enabled, and neither is quarantined or deleted.
        kept = registry.entities.get_entry(relay2.id)
        assert kept is not None
        assert (kept.platform, kept.disabled_by) == (
            "mqtt",
            er.RegistryEntryDisabler.INTEGRATION,
        )
        assert registry.entities.get_entry(diagnostic.id) == diagnostic
        assert record.get("quarantined", []) == []
        issue = _issue(hass, "native_controls_unavailable", entry.entry_id)
        assert issue is not None
        assert issue.translation_placeholders == {
            "panel": "alpha",
            "entities": relay2.entity_id,
        }
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
            _hello(_panel_descriptors("relay1", "relay2", "diag_cpu")),
        )
        assert reload.call_count == 2
        assert entry.state is ConfigEntryState.LOADED

    assert _supported(entry) == ["diag_cpu", "relay1", "relay2"]
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
    assert set(record["entities"]) == {relay1.id, relay2.id, diagnostic.id}
    assert _issue(hass, "native_controls_unavailable", entry.entry_id) is None
    result = await _hello_on_new_socket(
        hass, hass_ws_client, hass_read_only_access_token, _producer_hello()
    )
    assert result["mqtt_discovery"] == "withdraw"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"][CONF_SUPPORTED_CHANNELS] == {
        "did": "**REDACTED**",
        "channels": ["diag_cpu", "relay1", "relay2"],
    }


async def test_late_descriptor_keeps_original_user_disable_after_rediscovery(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A user-disabled MQTT control stays user-disabled after its channel arrives."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {}),
            ("button", "reboot", {"disabled_by": er.RegistryEntryDisabler.USER}),
        ],
    )
    registry = er.async_get(hass)
    original = registry.async_get(mqtt["entity_ids"]["reboot"])
    assert original is not None
    entry = await _setup(
        hass, hass_read_only_user.id, native=True, options=NATIVE, described=None
    )
    with panel_patches(), patch.object(guards, "_async_wait_loaded", AsyncMock()):
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello(_panel_descriptors("relay1")),
        )
        assert (
            registry.entities.get_entry(original.id).disabled_by
            is er.RegistryEntryDisabler.USER
        )
        # MQTT discovery may re-enable the same registry entry while it waits.
        registry.async_update_entity(original.entity_id, disabled_by=None)
        await _settle(hass)
        assert (
            registry.entities.get_entry(original.id).disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
    migrated = registry.entities.get_entry(original.id)
    assert migrated is not None
    assert (migrated.platform, migrated.entity_id, migrated.disabled_by) == (
        DOMAIN,
        original.entity_id,
        er.RegistryEntryDisabler.USER,
    )
    assert _record(entry)["entities"][original.id]["disabled_by_before"] == "user"


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


async def test_a_migrated_reboot_survives_a_later_unsupported_hello(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A transient loss of root cannot erase a migrated button's identity."""
    mqtt = _mqtt(hass, [("button", "reboot", {})])
    registry = er.async_get(hass)
    original = registry.async_get(mqtt["entity_ids"]["reboot"])
    assert original is not None
    entry = await _setup(
        hass, hass_read_only_user.id, native=True, options=NATIVE, described=None
    )
    with panel_patches():
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
        # The first hello starts the deferred cutover reload; the next one
        # renders the migrated native entity into its preserved registry ID.
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
        migrated = registry.entities.get_entry(original.id)
        assert migrated is not None
        assert (migrated.platform, migrated.entity_id) == (DOMAIN, original.entity_id)
        registry.async_update_entity(migrated.entity_id, name="My reboot")

        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([]) | {"unsupported": ["reboot"]},
        )

    kept = registry.entities.get_entry(original.id)
    assert kept is not None
    assert (kept.platform, kept.entity_id, kept.name) == (
        DOMAIN,
        original.entity_id,
        "My reboot",
    )
    assert "reboot" not in _supported(entry)
    state = hass.states.get(original.entity_id)
    assert state is not None and state.state == STATE_UNAVAILABLE
    with panel_patches():
        await _hello_on_new_socket(
            hass,
            hass_ws_client,
            hass_read_only_access_token,
            _hello([next(d for d in DESCRIPTORS if d["channel"] == "reboot")]),
        )
    recovered = registry.entities.get_entry(original.id)
    assert recovered is not None
    assert (recovered.platform, recovered.entity_id, recovered.name) == (
        DOMAIN,
        original.entity_id,
        "My reboot",
    )
    assert "reboot" in _supported(entry)
    hass.config_entries.async_update_entry(entry, options={"authority": "mqtt"})
    await _reload(hass, entry)
    restored = registry.entities.get_entry(original.id)
    assert restored is not None
    assert (restored.platform, restored.entity_id, restored.name) == (
        "mqtt",
        original.entity_id,
        "My reboot",
    )


async def test_a_native_born_reboot_survives_a_later_unsupported_hello(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Transient helper loss also preserves an action created without MQTT."""
    await _setup(hass, hass_read_only_user.id, native=True, described=None)
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
    entry = await _setup(
        hass, hass_read_only_user.id, native=True, options=NATIVE, described=None
    )
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


async def test_a_leftover_waiting_for_its_channel_is_never_quarantined(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """An undescribed control stays registered but cannot be used."""
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
    kept = registry.entities.get_entry(relay2.id)
    assert kept is not None
    assert kept.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert kept.id not in _record(entry).get("quarantined", [])
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
        ("update", "ha_companion_update", "update_companion"),
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

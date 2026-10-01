"""Channels a panel states it cannot serve: their native entities go.

A hello may list channels as ``unsupported``. That explicit statement removes
the channel's native entity and drops it from the panel's supported channels;
a channel a hello merely leaves out keeps both.
"""

from dataclasses import replace
from typing import Any

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN, update_unique_id
from custom_components.panel_assistant.contract import catalogue_entry_for_channel

from .test_cutover import NATIVE, _mqtt, _record, _reload
from .test_native import _hello, _observations, _report, _setup
from .test_supported_channels import _panel_descriptors, _producer_hello, _supported
from .test_transport import DID, WsClientFactory, _send

# What the panel describes before it learns which hardware it lacks.
FIRST = (
    "illuminance",
    "temperature",
    "humidity",
    "relay1",
    "relay2",
    "update_companion",
)


def _hello_with(
    described: tuple[str, ...], unsupported: list[str] | None = None
) -> dict[str, Any]:
    message = _hello(_panel_descriptors(*described))
    if unsupported is not None:
        message["unsupported"] = unsupported
    return message


async def _say_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    message: dict[str, Any],
) -> None:
    """Open a session with this hello and report every channel it describes."""
    client = await hass_ws_client(hass, token)
    response = await _send(client, message)
    assert response.get("success"), response
    session = response["result"]["session"]
    for sync, observations in (
        ("full_begin", []),
        ("full_end", _observations(message["channels"])),
    ):
        reply = await _send(client, _report(session, sync, observations))
        assert reply["success"], reply
    await hass.async_block_till_done()


def _entity_id(
    hass: HomeAssistant, platform: str, suffix: str, did: str = DID
) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{did}_{suffix}")


@pytest.fixture
async def described(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> MockConfigEntry:
    """A native entry whose panel has described FIRST and rendered it."""
    entry = await _setup(hass, hass_read_only_user.id, native=True, described=None)
    await _say_hello(
        hass, hass_ws_client, hass_read_only_access_token, _hello_with(FIRST)
    )
    assert _supported(entry) == sorted(FIRST)
    return entry


async def test_an_unsupported_channel_loses_its_entity_and_its_support(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Explicitly unsupported channels go; an omitted one and the rest stay."""
    registry = er.async_get(hass)
    light = _entity_id(hass, "sensor", "illuminance")
    assert light is not None
    registry.async_update_entity(light, name="Brightness", icon="mdi:sun")
    await hass.async_block_till_done()
    kept = registry.async_get(light)
    temperature = _entity_id(hass, "sensor", "temperature")
    relay2 = _entity_id(hass, "switch", "relay2")
    assert None not in (temperature, relay2)
    reading = hass.states.get(light).state
    assert reading != STATE_UNAVAILABLE

    # relay1 is merely left out; the others are stated unsupported.
    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(
            ("illuminance", "humidity", "update_companion"),
            ["temperature", "relay2"],
        ),
    )

    for platform, suffix in (
        ("sensor", "temperature"),
        ("switch", "relay2"),
    ):
        assert _entity_id(hass, platform, suffix) is None, suffix
    for entity_id in (temperature, relay2):
        assert hass.states.get(entity_id) is None
    assert _supported(described) == [
        "humidity",
        "illuminance",
        "relay1",
        "update_companion",
    ]
    # The omitted channel keeps its entity, unavailable.
    relay1 = _entity_id(hass, "switch", "relay1")
    assert relay1 is not None
    assert hass.states.get(relay1).state == STATE_UNAVAILABLE
    # The entities that stay are untouched and still read.
    assert registry.async_get(light) == kept
    assert hass.states.get(light).state == reading
    humidity = _entity_id(hass, "sensor", "humidity")
    assert humidity is not None
    assert hass.states.get(humidity).state != STATE_UNAVAILABLE


async def test_an_unsupported_companion_update_loses_its_native_entity(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel without the Companion app keeps no native Companion update."""
    companion = _entity_id(hass, "update", "ha_companion_update")
    assert companion is not None

    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("illuminance",), ["update_companion"]),
    )

    assert _entity_id(hass, "update", "ha_companion_update") is None
    assert hass.states.get(companion) is None
    assert "update_companion" not in _supported(described)


async def test_current_android_retires_old_auto_update_controls_on_upgrade(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A real new hello removes old updater controls and preserves explicit updates."""
    retired = {
        "self_update",
        "update_channel",
        "companion_auto_update",
        "companion_update_channel",
        "webview_auto_update",
    }
    retained = {"relay1", "update_paneld", "update_companion"}
    entry = await _setup(hass, hass_read_only_user.id, native=True, described=None)
    old_descriptors = _panel_descriptors(*sorted(retired | retained))
    assert {
        descriptor["channel"] for descriptor in old_descriptors
    } == retired | retained
    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello(old_descriptors),
    )
    registry = er.async_get(hass)
    old_entities: dict[str, str] = {}
    for descriptor in old_descriptors:
        if descriptor["channel"] == "update_paneld":
            # PA's receipt-bound update entity owns app updates rather than
            # rendering the older panel's update descriptor a second time.
            continue
        entity_id = registry.async_get_entity_id(
            descriptor["platform"], DOMAIN, f"{DID}_{descriptor['unique_suffix']}"
        )
        assert entity_id is not None, descriptor["channel"]
        old_entities[descriptor["channel"]] = entity_id
    app_update_id = registry.async_get_entity_id(
        "update", DOMAIN, update_unique_id(entry.entry_id)
    )
    assert app_update_id is not None
    app_update = registry.async_get(app_update_id)
    kept_entities = {
        channel: registry.async_get(entity_id)
        for channel, entity_id in old_entities.items()
        if channel in retained
    }
    entry_entities = {
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    message = _producer_hello()
    assert retired <= set(message["unsupported"])
    assert retired.isdisjoint(
        descriptor["channel"] for descriptor in message["channels"]
    )
    await _say_hello(hass, hass_ws_client, hass_read_only_access_token, message)

    for channel in retired:
        entity_id = old_entities[channel]
        assert registry.async_get(entity_id) is None, channel
        assert hass.states.get(entity_id) is None, channel
    assert retired.isdisjoint(_supported(entry))
    assert retained <= set(_supported(entry))
    assert {
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == entry_entities - {old_entities[channel] for channel in retired}
    for channel, item in kept_entities.items():
        assert registry.async_get(old_entities[channel]) == item, channel
    assert registry.async_get(app_update_id) == app_update


async def test_unsupported_without_an_entity_or_catalogue_entry_changes_nothing(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A channel with no entity, or none the catalogue knows, is ignored."""
    registry = er.async_get(hass)
    before = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, described.entry_id)
    }

    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(
            FIRST[:1],
            ["proximity", "mystery", "relay0", "relay65", "relay01", "humidity"],
        ),
    )

    after = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, described.entry_id)
    }
    # Only the one channel with an entity went.
    assert before - after == {f"{DID}_humidity"}
    assert after <= before
    assert _supported(described) == sorted(set(FIRST) - {"humidity"})


async def test_a_channel_both_described_and_unsupported_is_kept(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A described channel is served, whatever the unsupported list says."""
    message = _hello_with(("temperature",), ["temperature", "humidity", "relay1"])
    # Described in a shape the catalogue does not know, but described.
    (relay1,) = _panel_descriptors("relay1")
    message["channels"].append(relay1 | {"translation_key": "x"})
    await _say_hello(hass, hass_ws_client, hass_read_only_access_token, message)

    temperature = _entity_id(hass, "sensor", "temperature")
    assert temperature is not None
    assert hass.states.get(temperature).state != STATE_UNAVAILABLE
    assert _entity_id(hass, "switch", "relay1") is not None
    assert "relay1" in _supported(described)
    assert _entity_id(hass, "sensor", "humidity") is None
    assert "temperature" in _supported(described)
    assert "humidity" not in _supported(described)


async def test_a_removed_channel_described_again_gets_its_entity_back(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel that gains the hardware later gets the entity again."""
    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("illuminance",), ["temperature"]),
    )
    assert _entity_id(hass, "sensor", "temperature") is None

    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("illuminance", "temperature")),
    )

    temperature = _entity_id(hass, "sensor", "temperature")
    assert temperature is not None
    assert hass.states.get(temperature).state != STATE_UNAVAILABLE
    assert "temperature" in _supported(described)


async def test_only_this_identitys_native_entity_is_removed(
    hass: HomeAssistant,
    described: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Neither another identity's entity nor an MQTT one is ever removed."""
    registry = er.async_get(hass)
    reset_did = "f" * 64
    # An MQTT entity carrying the very native unique ID that is removed.
    lookalike = registry.async_get_or_create(
        "sensor",
        "mqtt",
        f"{reset_did}_temperature",
        suggested_object_id="mqtt_temperature",
    )
    old_temperature = _entity_id(hass, "sensor", "temperature")
    assert old_temperature is not None

    # Explicit saved identity for this registry-ownership scenario. A health
    # response alone cannot move an existing installation to another identity.
    hass.config_entries.async_update_entry(described, unique_id=reset_did)
    coordinator = described.runtime_data.coordinator
    coordinator.async_set_updated_data(
        replace(
            coordinator.data,
            health=replace(coordinator.data.health, discovery_id=reset_did),
        )
    )
    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("temperature",)) | {"did": reset_did},
    )
    assert _entity_id(hass, "sensor", "temperature", reset_did) is not None
    # Another entry's entity under this identity's native unique ID.
    other = MockConfigEntry(domain=DOMAIN, title="beta")
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{reset_did}_humidity",
        config_entry=other,
        suggested_object_id="beta_humidity",
    )

    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("illuminance",), ["temperature", "humidity"]) | {"did": reset_did},
    )

    # The new identity's own entity goes; the old identity's stays.
    assert _entity_id(hass, "sensor", "temperature", reset_did) is None
    assert _entity_id(hass, "sensor", "temperature") == old_temperature
    assert _entity_id(hass, "sensor", "humidity") is not None
    assert registry.async_get(lookalike.entity_id) == lookalike
    assert registry.async_get(foreign.entity_id) == foreign


async def test_a_moved_mqtt_entity_goes_when_its_channel_is_unsupported(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A cut-over panel's moved entity is its native entity, so it goes too."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("switch", "relay2", {})])
    registry = er.async_get(hass)
    relay1 = registry.async_get(mqtt["entity_ids"]["relay1"])
    relay2 = registry.async_get(mqtt["entity_ids"]["relay2"])
    assert relay1 is not None
    assert relay2 is not None
    entry = await _setup(
        hass,
        hass_read_only_user.id,
        native=True,
        options=NATIVE,
        described={"relay1", "relay2"},
    )
    moved = registry.entities.get_entry(relay2.id)
    assert moved is not None
    assert (moved.platform, moved.unique_id) == (DOMAIN, f"{DID}_relay2")

    await _say_hello(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        _hello_with(("relay1",), ["relay2"]),
    )

    assert registry.entities.get_entry(relay2.id) is None
    kept = registry.entities.get_entry(relay1.id)
    assert kept is not None
    assert kept.entity_id == relay1.entity_id
    assert _supported(entry) == ["relay1"]
    # The next load carries on from the record without the removed entity.
    await _reload(hass, entry)
    assert _record(entry)["state"] == "complete"
    assert registry.entities.get_entry(relay1.id) is not None


@pytest.mark.parametrize(
    ("channel", "platform", "suffix"),
    [
        ("temperature", "sensor", "temperature"),
        ("voice_enabled", None, None),
        ("update_companion", "update", "ha_companion_update"),
        ("relay1", "switch", "relay1"),
        ("relay64", "switch", "relay64"),
        ("button_led2", "light", "button_led2"),
        ("buttons", "light", "buttons"),
        ("relay0", None, None),
        ("relay65", None, None),
        ("relay01", None, None),
        ("relay", None, None),
        ("voice_assistant", None, None),
        ("mystery", None, None),
    ],
)
def test_a_channel_names_its_catalogue_entry_and_suffix(
    channel: str, platform: str | None, suffix: str | None
) -> None:
    """A family member's ID carries its index; a suffix is not a channel ID."""
    match = catalogue_entry_for_channel(channel)
    if platform is None:
        assert match is None
    else:
        assert match is not None
        assert (match[0]["platform"], match[1]) == (platform, suffix)

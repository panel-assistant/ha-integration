"""Transport shadow mode: retained sessions and the MQTT comparison."""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.transport import (
    DESCRIPTOR_SCHEMA,
    Observation,
    _compare,
    async_get_sessions,
    session_available,
)

from .test_transport import (
    DID,
    WsClientFactory,
    _hello,
    _open,
    _receive,
    _send,
    entry,  # noqa: F401
)

REDACTED = "**REDACTED**"
PANEL_ID = "alpha"


def _described(channel: str, platform: str, **fields: Any) -> dict[str, Any]:
    return {
        "channel": channel,
        "platform": platform,
        "translation_key": channel,
        "unique_suffix": channel,
        **fields,
    }


CHANNELS = [
    _described("relay1", "switch"),
    _described("relay2", "switch"),
    _described("relay3", "switch"),
    _described("zigbee_router", "switch"),
    _described("proximity", "binary_sensor"),
    _described("volume", "number", min=0, max=100),
    _described("update_channel", "select", options=["stable", "prerelease"]),
    _described("screen", "light"),
    _described("button_led", "light"),
    _described("home_dashboard", "text"),
    _described("navigate", "text"),
    _described("diag_cpu", "sensor", unit="%"),
    _described("cpu_governor", "sensor", options=["performance", "auto"]),
    _described("diag_wifi_ssid", "sensor"),
    _described("diag_wifi_rssi", "sensor", unit="dBm"),
    _described("software_update_paneld", "update", unique_suffix="update_paneld"),
    _described("camera_snapshot", "image"),
]

# Platform, unique suffix, state and attributes of each MQTT entity on the device.
MQTT_ENTITIES: list[tuple[str, str, str, dict[str, Any]]] = [
    ("switch", "relay1", "on", {}),
    ("switch", "relay2", "off", {}),
    ("switch", "relay3", "off", {}),
    ("binary_sensor", "proximity", "unavailable", {}),
    ("number", "volume", "40.0", {}),
    ("select", "update_channel", "Pre-release", {}),
    (
        "light",
        "screen",
        "on",
        {
            "brightness": 180,
            "rgb_color": (1, 2, 3),
            "effect": "Rainbow",
            "supported_color_modes": ["rgb"],
        },
    ),
    ("light", "button_led", "on", {"brightness": 100}),
    ("text", "home_dashboard", "lovelace/home", {}),
    ("text", "navigate", "lovelace/a", {}),
    ("sensor", "diag_cpu", "12.5004", {}),
    ("sensor", "cpu_governor", "performance", {}),
    ("sensor", "diag_wifi_ssid", "HomeNet", {}),
    ("sensor", "diag_wifi_rssi", "-60", {}),
    (
        "update",
        "update_paneld",
        "on",
        {"installed_version": "0.9.7", "latest_version": "0.9.8"},
    ),
    ("image", "camera_snapshot", "2026-09-13T10:00:00+00:00", {}),
    ("switch", "watchdog", "on", {}),
]

OBSERVATIONS: list[dict[str, Any]] = [
    {"channel": "relay1", "state": "known", "value": True},
    {"channel": "relay2", "state": "known", "value": True},
    {"channel": "proximity", "state": "unavailable"},
    {"channel": "volume", "state": "known", "value": 40},
    {"channel": "update_channel", "state": "known", "value": "prerelease"},
    {
        "channel": "screen",
        "state": "known",
        "value": {
            "on": True,
            "brightness": 180,
            "color": {"r": 1, "g": 2, "b": 3},
            "effect": "rainbow",
        },
    },
    {
        "channel": "button_led",
        "state": "known",
        "value": {"on": True, "brightness": 90},
    },
    {"channel": "home_dashboard", "state": "known", "value": "lovelace/home"},
    {"channel": "navigate", "state": "known", "value": "lovelace/b"},
    {"channel": "diag_cpu", "state": "known", "value": 12.5},
    {"channel": "cpu_governor", "state": "known", "value": "performance"},
    # A sensor declaring no unit or class carries text, such as an SSID.
    {"channel": "diag_wifi_ssid", "state": "known", "value": "HomeNet"},
    # A sensor declaring a unit measures, so text is rejected.
    {"channel": "diag_wifi_rssi", "state": "known", "value": "strong"},
    {
        "channel": "software_update_paneld",
        "state": "known",
        "value": {"installed_version": "0.9.7", "latest_version": "0.9.8"},
    },
    {
        "channel": "camera_snapshot",
        "state": "known",
        "value": {"url": "http://panel.local:8888/snapshot.jpg"},
    },
]


def _report(
    token: str, sync: str, observations: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "type": "panel_assistant/report_state",
        "session": token,
        "sync": sync,
        "observations": observations,
    }


async def _full_sync(client: Any, token: str) -> dict[str, Any]:
    begin = await _send(client, _report(token, "full_begin", []))
    assert begin["success"], begin
    end = await _send(client, _report(token, "full_end", OBSERVATIONS))
    assert end["success"], end
    return end


@pytest.fixture
def mqtt_device(hass: HomeAssistant) -> dict[str, Any]:
    """Register the panel's MQTT device, its entities and their states."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", f"ha-paneld-{PANEL_ID}"), ("mqtt", "ha-paneld-aid-1")},
    )
    registry = er.async_get(hass)
    entity_ids: dict[str, str] = {}
    for platform, suffix, state, attributes in MQTT_ENTITIES:
        item = registry.async_get_or_create(
            platform,
            "mqtt",
            f"{PANEL_ID}_{suffix}",
            config_entry=mqtt_entry,
            device_id=device.id,
        )
        entity_ids[suffix] = item.entity_id
        hass.states.async_set(item.entity_id, state, attributes)
    # A disabled entity has no state but is still part of the parity gap, and an
    # entity without the panel prefix is not a panel channel at all.
    registry.async_get_or_create(
        "switch",
        "mqtt",
        f"{PANEL_ID}_silence_boot_chime",
        config_entry=mqtt_entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    registry.async_get_or_create(
        "sensor", "mqtt", "unrelated", config_entry=mqtt_entry, device_id=device.id
    )
    return {"entry": mqtt_entry, "device": device, "entity_ids": entity_ids}


# ---------------------------------------------------------------------------
# Authority, retention and the rejection record.


async def test_hello_answers_shadow(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """While no native authority exists, shadow is the only mode served."""
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello())

    assert response["result"]["authority"] == "shadow"
    assert response["result"]["capabilities"] == ["events", "state"]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["transport"]["authority"] == "shadow"
    assert diagnostics["transport"]["closed_at"] is None


async def test_closed_session_is_kept_until_a_new_one_opens(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Diagnostics show the last values while disconnected, never as available."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client, channels=CHANNELS)
    await _full_sync(client, token)
    await client.close()
    await hass.async_block_till_done()

    sessions = async_get_sessions(hass)
    retained = sessions.latest(entry.entry_id)
    assert sessions.get(entry.entry_id) is None
    assert retained is not None and retained.closed_at is not None
    assert retained.observations["relay1"].value is True
    assert not session_available(hass, entry.entry_id)
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["connected"] is False
    assert transport["closed_at"] == retained.closed_at.isoformat()
    assert transport["shadow"]["channels"]["relay1"]["ws"]["value"] is True

    again = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(again)

    current = sessions.latest(entry.entry_id)
    assert current is not None and current is not retained
    # The kept session, and the closed connection it references, is released.
    assert entry.entry_id not in sessions._last_by_entry
    assert current is sessions.get(entry.entry_id)
    assert current.closed_at is None
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert (transport["connected"], transport["observations"]) == (True, 0)


async def test_superseded_session_is_replaced_not_kept(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A newer hello's session is what diagnostics show, then keep after it ends."""
    old = await hass_ws_client(hass, hass_read_only_access_token)
    old_token = await _open(old, channels=CHANNELS)
    await _full_sync(old, old_token)
    new = await hass_ws_client(hass, hass_read_only_access_token)
    new_token = await _open(new)
    closed = await _receive(old)
    assert closed["event"]["reason"] == "superseded"

    sessions = async_get_sessions(hass)
    assert sessions.latest(entry.entry_id) is sessions.get(entry.entry_id)
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert (transport["connected"], transport["channels"]) == (True, 3)

    response = await _send(
        new,
        _report(
            new_token,
            "delta",
            [{"channel": "relay3", "state": "known", "value": False}],
        ),
    )
    assert response["result"] == {"rejected": []}
    await new.close()
    await hass.async_block_till_done()

    retained = sessions.latest(entry.entry_id)
    assert retained is not None and retained.token == new_token
    assert retained.closed_at is not None
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert (transport["connected"], transport["channels"]) == (False, 3)
    assert transport["observations"] == 1


async def test_unload_keeps_and_removal_forgets_the_last_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Only removing the entry drops its last session."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    sessions = async_get_sessions(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert sessions.get(entry.entry_id) is None
    assert sessions.latest(entry.entry_id) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert sessions.latest(entry.entry_id) is None


async def test_rejection_is_recorded_per_channel_and_cleared(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A validator rejection is remembered until that channel is accepted."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None

    await _send(
        client,
        _report(
            token,
            "delta",
            [
                {"channel": "relay3", "state": "known", "value": "ON"},
                {"channel": "volume", "state": "known", "value": 101},
                {"channel": "screen", "state": "known", "value": True},
            ],
        ),
    )
    # Undescribed channels are named by the panel, so they are never remembered.
    assert session.rejections == {"relay3": "invalid_value", "volume": "invalid_value"}

    await _send(
        client,
        _report(
            token, "delta", [{"channel": "relay3", "state": "known", "value": True}]
        ),
    )
    assert session.rejections == {"volume": "invalid_value"}
    await _send(
        client, _report(token, "delta", [{"channel": "volume", "state": "unavailable"}])
    )
    assert session.rejections == {}


# ---------------------------------------------------------------------------
# The comparison, end to end through the diagnostics download.


EXPECTED_COMPARISONS = {
    "relay1": "match",
    "relay2": "differs",
    "relay3": "ws_missing",
    "zigbee_router": "mqtt_missing",
    "proximity": "match",
    "volume": "match",
    "update_channel": "match",
    "screen": "match",
    "button_led": "differs",
    "home_dashboard": "match",
    "navigate": "differs",
    "diag_cpu": "match",
    "cpu_governor": "match",
    "diag_wifi_ssid": "match",
    "diag_wifi_rssi": "ws_rejected",
    "software_update_paneld": "match",
    "camera_snapshot": "not_compared",
}


async def test_diagnostics_compare_each_channel_with_its_mqtt_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    mqtt_device: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Every outcome, normalised per platform, with a parity-gap list."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client, channels=CHANNELS)
    end = await _full_sync(client, token)
    assert end["result"]["rejected"] == [
        {"channel": "diag_wifi_rssi", "code": "invalid_value"}
    ]
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    relay1_state = hass.states.get(mqtt_device["entity_ids"]["relay1"])
    assert relay1_state is not None
    session.observations["relay1"].received_at = relay1_state.last_reported + timedelta(
        seconds=5
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    shadow = diagnostics["transport"]["shadow"]
    channels = shadow["channels"]
    assert {name: item["comparison"] for name, item in channels.items()} == (
        EXPECTED_COMPARISONS
    )
    assert shadow["summary"] == {
        "match": 10,
        "differs": 3,
        "ws_missing": 1,
        "ws_rejected": 1,
        "mqtt_missing": 1,
        "not_compared": 1,
    }
    assert shadow["mqtt_device"] is True
    assert shadow["mqtt_only"] == ["silence_boot_chime", "watchdog"]

    relay1 = channels["relay1"]
    assert (relay1["platform"], relay1["unique_suffix"]) == ("switch", "relay1")
    assert relay1["ws"]["value"] is True
    assert relay1["ws"]["state"] == "known"
    assert relay1["ws"]["refresh"] is False
    assert relay1["ws"]["rejected"] is None
    assert isinstance(relay1["ws"]["age_s"], float)
    assert relay1["mqtt"]["entity_id"] == mqtt_device["entity_ids"]["relay1"]
    assert relay1["mqtt"]["state"] == "on"
    assert relay1["mqtt"]["last_reported"] == relay1_state.last_reported.isoformat()
    assert relay1["mqtt"]["last_changed"] == relay1_state.last_changed.isoformat()
    assert relay1["freshness_delta_s"] == 5.0

    assert channels["relay3"]["ws"] is None
    assert channels["relay3"]["freshness_delta_s"] is None
    assert channels["zigbee_router"]["mqtt"] is None
    assert channels["diag_wifi_rssi"]["ws"]["rejected"] == "invalid_value"
    assert channels["diag_wifi_rssi"]["ws"]["value"] is None
    assert channels["diag_wifi_ssid"]["ws"]["rejected"] is None
    assert channels["diag_wifi_ssid"]["ws"]["value"] == REDACTED
    assert channels["screen"]["mqtt"]["attributes"] == {
        "brightness": 180,
        "rgb_color": (1, 2, 3),
        "effect": "Rainbow",
    }
    assert channels["software_update_paneld"]["unique_suffix"] == "update_paneld"
    assert channels["software_update_paneld"]["mqtt"]["attributes"] == {
        "installed_version": "0.9.7",
        "latest_version": "0.9.8",
    }
    assert channels["proximity"]["ws"]["state"] == "unavailable"
    assert channels["diag_cpu"]["mqtt"]["state"] == "12.5004"

    # User data is compared, but never shown.
    for name in ("home_dashboard", "navigate"):
        assert channels[name]["ws"]["value"] == REDACTED
        assert channels[name]["mqtt"]["state"] == REDACTED
    assert channels["diag_wifi_ssid"]["mqtt"]["state"] == REDACTED
    rendered = repr(diagnostics)
    for secret in (
        "lovelace/home",
        "lovelace/a",
        "lovelace/b",
        "HomeNet",
        token,
        DID,
        hass_read_only_user.id,
    ):
        assert secret not in rendered
    assert "panel_id" not in shadow
    assert diagnostics["health"]["panel_id"] == REDACTED


async def test_diagnostics_without_an_mqtt_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """With no MQTT device every compared channel is an MQTT gap."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)

    shadow = (await async_get_config_entry_diagnostics(hass, entry))["transport"][
        "shadow"
    ]

    assert shadow["mqtt_device"] is False
    assert shadow["mqtt_only"] == []
    assert {name: item["comparison"] for name, item in shadow["channels"].items()} == {
        "button": "not_compared",
        "relay3": "mqtt_missing",
        "volume": "mqtt_missing",
    }


async def test_diagnostics_without_any_session_have_no_comparison(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    mqtt_device: dict[str, Any],
) -> None:
    """Before a panel ever connects there is nothing to compare."""
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["transport"] == {
        "connected": False,
        "native_entities": False,
        "effective_authority": "shadow",
        "cutover": None,
        "active_owner": "mqtt",
        "mqtt_discovery": "announce",
    }


async def test_comparison_failure_does_not_break_the_download(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    mqtt_device: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A fault in the comparison yields an error code, never a failed download."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client, channels=CHANNELS)
    raised: Exception | None = None
    diagnostics: dict[str, Any] = {}

    with patch(
        "custom_components.panel_assistant.transport._shadow_channel",
        side_effect=RuntimeError("boom"),
    ):
        try:
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        except Exception as err:
            raised = err

    assert raised is None, raised
    assert diagnostics["transport"]["shadow"] == {"error": "comparison_failed"}
    assert diagnostics["transport"]["connected"] is True
    assert "health" in diagnostics


def _entity_snapshot(items: list[er.RegistryEntry]) -> list[Any]:
    return sorted((item.id, item) for item in items)


async def test_shadow_mode_never_writes_a_registry(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    mqtt_device: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Hello, a full sync, deltas and a download leave every registry as it was."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    mqtt_entry: MockConfigEntry = mqtt_device["entry"]
    device: dr.DeviceEntry = mqtt_device["device"]

    def snapshot() -> tuple[Any, ...]:
        # Whole registry entries compare every field, including modified_at.
        return (
            _entity_snapshot(
                er.async_entries_for_config_entry(entity_registry, entry.entry_id)
            ),
            _entity_snapshot(
                er.async_entries_for_config_entry(entity_registry, mqtt_entry.entry_id)
            ),
            _entity_snapshot(
                er.async_entries_for_device(
                    entity_registry, device.id, include_disabled_entities=True
                )
            ),
            sorted(
                (item.id, item)
                for config_entry_id in (entry.entry_id, mqtt_entry.entry_id)
                for item in dr.async_entries_for_config_entry(
                    device_registry, config_entry_id
                )
            ),
            dict(entry.data),
            dict(entry.options),
            entry.unique_id,
            dict(mqtt_entry.data),
            dict(mqtt_entry.options),
            len(hass.config_entries.async_entries()),
        )

    before = snapshot()
    panel_entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    assert len(before[1]) == len(MQTT_ENTITIES) + 2
    assert len(before[3]) == 2

    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client, channels=CHANNELS)
    await _full_sync(client, token)
    for value in (False, True):
        response = await _send(
            client,
            _report(
                token,
                "delta",
                [
                    {"channel": "relay1", "state": "known", "value": value},
                    {"channel": "volume", "state": "known", "value": 41},
                ],
            ),
        )
        assert response["result"] == {"rejected": []}
    await async_get_config_entry_diagnostics(hass, entry)
    await hass.async_block_till_done()

    assert snapshot() == before
    assert [
        item
        for item in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if item not in panel_entities
    ] == []


# ---------------------------------------------------------------------------
# Normalisation, one rule at a time.


def _descriptor(platform: str, **fields: Any) -> dict[str, Any]:
    descriptor: dict[str, Any] = DESCRIPTOR_SCHEMA(
        {
            "channel": "leaf",
            "platform": platform,
            "translation_key": "leaf",
            "unique_suffix": "leaf",
            **fields,
        }
    )
    return descriptor


def _known(value: Any) -> Observation:
    return Observation("known", value, {}, False, dt_util.utcnow())


def _state(state: str, **attributes: Any) -> State:
    return State("sensor.leaf", state, attributes)


SELECT = _descriptor("select", options=["stable", "prerelease"])
ENUM = _descriptor("sensor", options=["auto", "performance"])


@pytest.mark.parametrize(
    ("descriptor", "value", "state", "expected"),
    [
        pytest.param(_descriptor("switch"), True, _state("on"), "match", id="on"),
        pytest.param(_descriptor("switch"), False, _state("off"), "match", id="off"),
        pytest.param(_descriptor("switch"), True, _state("off"), "differs", id="sw"),
        pytest.param(
            _descriptor("switch"), False, _state("unknown"), "differs", id="unknown"
        ),
        pytest.param(
            _descriptor("binary_sensor"), True, _state("on"), "match", id="binary"
        ),
        pytest.param(
            _descriptor("binary_sensor"), True, _state("off"), "differs", id="binary_x"
        ),
        pytest.param(_descriptor("number"), 40, _state("40.0005"), "match", id="num"),
        pytest.param(_descriptor("number"), 40, _state("41"), "differs", id="num_x"),
        pytest.param(_descriptor("number"), 40, _state("40.01"), "differs", id="tol"),
        pytest.param(_descriptor("number"), 40, _state("abc"), "differs", id="nan"),
        pytest.param(_descriptor("sensor"), 12.5, _state("12.5"), "match", id="sensor"),
        pytest.param(_descriptor("sensor"), 12.5, _state("13"), "differs", id="sen_x"),
        pytest.param(ENUM, "auto", _state("auto"), "match", id="enum"),
        pytest.param(ENUM, "auto", _state("Auto"), "differs", id="enum_exact"),
        pytest.param(SELECT, "prerelease", _state("Pre-release"), "match", id="slug"),
        pytest.param(SELECT, "stable", _state("Stable"), "match", id="slug_case"),
        pytest.param(SELECT, "prerelease", _state("Stable"), "differs", id="select_x"),
        pytest.param(
            _descriptor("light"),
            {"on": False, "brightness": 10},
            _state("off"),
            "match",
            id="light_off",
        ),
        pytest.param(
            _descriptor("light"), {"on": True}, _state("off"), "differs", id="light_x"
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "brightness": 180},
            _state("on", brightness=180),
            "match",
            id="brightness",
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "brightness": 180},
            _state("on", brightness=179),
            "differs",
            id="brightness_x",
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "color": {"r": 1, "g": 2, "b": 3}},
            _state("on", rgb_color=(1, 2, 3)),
            "match",
            id="color",
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "color": {"r": 1, "g": 2, "b": 3}},
            _state("on", rgb_color=(3, 2, 1)),
            "differs",
            id="color_x",
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "effect": "rainbow"},
            _state("on", effect="Rainbow"),
            "match",
            id="effect",
        ),
        pytest.param(
            _descriptor("light"),
            {"on": True, "effect": "rainbow"},
            _state("on", effect="Strobe"),
            "differs",
            id="effect_x",
        ),
        pytest.param(
            _descriptor("text"), "lovelace/home", _state("lovelace/home"), "match"
        ),
        pytest.param(
            _descriptor("text"),
            "lovelace/home",
            _state("Lovelace/Home"),
            "differs",
            id="text_exact",
        ),
        pytest.param(
            _descriptor("update"),
            {"installed_version": "0.9.7", "latest_version": "0.9.8"},
            _state("on", installed_version="0.9.7", latest_version="0.9.8"),
            "match",
            id="update",
        ),
        pytest.param(
            _descriptor("update"),
            {"installed_version": "0.9.7", "latest_version": "0.9.8"},
            _state("off", installed_version="0.9.7", latest_version="0.9.7"),
            "differs",
            id="update_latest",
        ),
        pytest.param(
            _descriptor("update"),
            {"installed_version": "0.9.8", "latest_version": "0.9.8"},
            _state("off", installed_version="0.9.7", latest_version="0.9.8"),
            "differs",
            id="update_installed",
        ),
        pytest.param(
            _descriptor("image"),
            {"url": "http://panel.local/snap.jpg"},
            _state("2026-09-13T10:00:00+00:00"),
            "not_compared",
            id="image",
        ),
        pytest.param(_descriptor("button"), None, _state("unknown"), "not_compared"),
        pytest.param(
            _descriptor("event", options=["keycode_home"]),
            None,
            _state("unknown"),
            "not_compared",
        ),
    ],
)
def test_normalisation(
    descriptor: dict[str, Any], value: Any, state: State, expected: str
) -> None:
    """A typed wire value and a Home Assistant state string are compared alike."""
    assert _compare(descriptor, _known(value), None, state) == expected


def test_unavailable_matches_only_unavailable() -> None:
    """Unavailable on one side only is a difference, whatever the value."""
    descriptor = _descriptor("switch")
    gone = Observation("unavailable", None, {}, False, dt_util.utcnow())

    assert _compare(descriptor, gone, None, _state("unavailable")) == "match"
    assert _compare(descriptor, gone, None, _state("off")) == "differs"
    assert _compare(descriptor, _known(True), None, _state("unavailable")) == "differs"


def test_outcome_precedence() -> None:
    """Missing MQTT, then a rejection, then nothing received."""
    descriptor = _descriptor("switch")

    assert _compare(descriptor, _known(True), "invalid_value", None) == "mqtt_missing"
    assert (
        _compare(descriptor, _known(True), "invalid_value", _state("on"))
        == "ws_rejected"
    )
    assert _compare(descriptor, None, None, _state("on")) == "ws_missing"
    assert _compare(_descriptor("image"), None, None, None) == "not_compared"

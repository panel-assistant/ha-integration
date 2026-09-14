"""Native entities: dormant by default, rendered from descriptors when turned on."""

import json
import logging
from collections.abc import AsyncGenerator
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.contract import CONTRACT
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.native import NATIVE_ONLY_PLATFORMS

from .test_transport import (
    DID,
    HEALTH,
    STATUS,
    WsClientFactory,
    _registry_digest,
    _send,
)

FIXTURES = Path(__file__).parent / "fixtures"
EVENT_TYPES = ["keycode_home", "keycode_back"]


def _descriptor_for(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the descriptor a panel sends for one catalogue entry."""
    family = entry["family"]
    descriptor = {
        key: entry[key]
        for key in (
            "platform",
            "translation_key",
            "entity_category",
            "enabled_default",
            "device_class",
            "unit",
            "state_class",
            "force_update",
            "options",
            "min",
            "max",
            "step",
        )
    }
    if family is None:
        descriptor |= {
            "channel": entry["channel"],
            "unique_suffix": entry["unique_suffix"],
        }
    else:
        descriptor |= {
            "channel": f"{family}1",
            "family": family,
            "index": 1,
            "unique_suffix": entry["unique_suffix"].format(index=1),
        }
    if entry["platform"] == "event":
        # Event types come from the panel's profile, not the catalogue.
        descriptor["options"] = EVENT_TYPES
    return descriptor


# Every catalogue channel, everything enabled so each renders a state.
DESCRIPTORS = [
    _descriptor_for(entry) | {"enabled_default": True} for entry in CONTRACT["channels"]
]
PANEL_HELLO: dict[str, Any] = json.loads(
    (FIXTURES / "panel_hello.json").read_text(encoding="utf-8")
)
PANEL_REPORT: dict[str, Any] = json.loads(
    (FIXTURES / "panel_report_state.json").read_text(encoding="utf-8")
)
# Values for the catalogue channels the panel does not report yet.
EXTRA_OBSERVATIONS = [
    {"channel": "auto_sleep_activity", "state": "known", "value": False},
    {
        "channel": "camera_snapshot",
        "state": "known",
        "value": {"url": "http://panel.local:8888/api/v1/camera/snapshot?t=1"},
    },
    {"channel": "diag_boot", "state": "known", "value": "2026-09-14T08:00:00+00:00"},
    {"channel": "relay1", "state": "known", "value": True},
]


def _hello(channels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "panel_assistant/hello",
        "protocol": {"min": 1, "max": 1},
        "did": DID,
        "app": {"version": "0.9.8-rc1", "version_code": 790},
        "contract_digest": "c" * 64,
        "capabilities": ["state", "events"],
        "channels": channels,
    }


def _report(token: str, sync: str, observations: list[Any]) -> dict[str, Any]:
    return {
        "type": "panel_assistant/report_state",
        "session": token,
        "sync": sync,
        "observations": observations,
    }


def _observations(
    descriptors: list[dict[str, Any]] = DESCRIPTORS,
) -> list[dict[str, Any]]:
    reported = {item["channel"]: item for item in PANEL_REPORT["observations"]}
    for item in EXTRA_OBSERVATIONS:
        reported[item["channel"]] = item
    described = {descriptor["channel"] for descriptor in descriptors}
    return [item for channel, item in reported.items() if channel in described]


async def _session(client: Any, channels: list[dict[str, Any]] | None = None) -> str:
    response = await _send(
        client, _hello(DESCRIPTORS if channels is None else channels)
    )
    assert response["success"], response
    token: str = response["result"]["session"]
    return token


async def _sync(
    hass: HomeAssistant,
    client: Any,
    token: str,
    descriptors: list[dict[str, Any]] = DESCRIPTORS,
) -> None:
    for sync, observations in (
        ("full_begin", []),
        ("full_end", _observations(descriptors)),
    ):
        response = await _send(client, _report(token, sync, observations))
        assert response["success"], response
        assert response["result"]["rejected"] == []
    await hass.async_block_till_done()


async def _setup(
    hass: HomeAssistant,
    user_id: str,
    native: bool,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: "panel.local", CONF_TRANSPORT_USER_ID: user_id},
        options=options or {},
    )
    config_entry.add_to_hass(hass)
    executor = SimpleNamespace(
        async_acquire_finalizer=AsyncMock(return_value=True),
        async_release_finalizer=AsyncMock(),
    )
    manager = SimpleNamespace(
        async_list=AsyncMock(return_value=()), async_transition=AsyncMock()
    )
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        config = {DOMAIN: {"native_entities": True}} if native else {}
        assert await async_setup_component(hass, DOMAIN, config)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry


@pytest.fixture
async def dormant(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """A bound panel entry with native entities left off, as shipped."""
    yield await _setup(hass, hass_read_only_user.id, native=False)


@pytest.fixture
async def native(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """A bound panel entry with native entities turned on."""
    yield await _setup(hass, hass_read_only_user.id, native=True)


def _native_entries(hass: HomeAssistant, entry_id: str) -> dict[str, er.RegistryEntry]:
    return {
        item.unique_id: item
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if item.unique_id.startswith(f"{DID}_")
    }


def touch_sound(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return _native_entries(hass, entry.entry_id)[f"{DID}_touch_sound"].entity_id


def _expected_unique_ids() -> set[str]:
    return {
        f"{DID}_{descriptor['unique_suffix']}"
        for descriptor in DESCRIPTORS
        if descriptor["channel"] != "update_paneld"
    }


# ---------------------------------------------------------------------------
# Dormant by default.


async def test_flag_off_creates_nothing_through_a_full_session(
    hass: HomeAssistant,
    dormant: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Every type described, synced and fired leaves the registry as it was."""
    registry_before = _registry_digest(hass, dormant.entry_id)
    states_before = sorted(hass.states.async_entity_ids())
    client = await hass_ws_client(hass, hass_read_only_access_token)

    token = await _session(client)
    await _sync(hass, client, token)
    event = await _send(
        client,
        {
            "type": "panel_assistant/report_event",
            "session": token,
            "channel": "button",
            "event_id": 1,
            "event_type": "keycode_home",
        },
    )
    assert event["success"]
    await hass.async_block_till_done()

    assert _registry_digest(hass, dormant.entry_id) == registry_before
    assert sorted(hass.states.async_entity_ids()) == states_before
    assert dormant.runtime_data.platforms == ["sensor", "update"]
    for platform in NATIVE_ONLY_PLATFORMS:
        assert f"{DOMAIN}.{platform}" not in hass.config.components
    transport = (await async_get_config_entry_diagnostics(hass, dormant))["transport"]
    assert transport.get("native_entities") is False


# ---------------------------------------------------------------------------
# Rendered when turned on.


async def test_every_type_renders_under_the_native_unique_id(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Each catalogue channel becomes one entity keyed ``<did>_<unique_suffix>``."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await hass.async_block_till_done()

    entries = _native_entries(hass, native.entry_id)
    assert set(entries) == _expected_unique_ids()
    platforms = {item.domain for item in entries.values()}
    assert platforms == {
        "binary_sensor",
        "button",
        "event",
        "image",
        "light",
        "number",
        "select",
        "sensor",
        "switch",
        "text",
        "update",
    }
    by_suffix = {
        unique_id.removeprefix(f"{DID}_"): item for unique_id, item in entries.items()
    }
    assert by_suffix["relay1"].translation_key == "relay"
    assert by_suffix["diag_cpu"].entity_category == "diagnostic"
    assert by_suffix["voice_assistant"].translation_key == "voice_enabled"
    # Every entity sits on the entry's own device, next to the status sensor.
    status = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{native.entry_id}_status"
        )
        or ""
    )
    assert status is not None
    assert {item.device_id for item in entries.values()} == {status.device_id}

    # Unavailable until the full sync completes.
    screen = by_suffix["screen"].entity_id
    assert hass.states.get(screen).state == STATE_UNAVAILABLE
    # A button reports nothing, so only the unfinished sync holds it back.
    begin = await _send(client, _report(token, "full_begin", _observations()))
    assert begin["success"]
    await hass.async_block_till_done()
    assert hass.states.get(screen).state == STATE_UNAVAILABLE
    assert hass.states.get(by_suffix["reboot"].entity_id).state == STATE_UNAVAILABLE
    await _sync(hass, client, token)

    def state(suffix: str) -> Any:
        found = hass.states.get(by_suffix[suffix].entity_id)
        assert found is not None
        return found

    assert state("relay1").state == "on"
    assert state("touch_sound").state == "on"
    assert state("screen").state == "off"
    led = state("led")
    assert (led.state, led.attributes["brightness"]) == ("on", 90)
    assert led.attributes.get("rgb_color") == (1, 2, 3)
    assert (led.attributes["effect"], led.attributes["color_mode"]) == ("pulse", "rgb")
    assert state("button_led1").attributes["supported_color_modes"] == ["onoff"]
    assert state("cpu_governor").state == "efficiency"
    assert state("cpu_governor").attributes["options"] == [
        "performance",
        "efficiency",
        "auto",
    ]
    assert state("volume").state == "0"
    assert state("navigate").state == "/"
    assert state("proximity").state == "on"
    assert state("temperature").state == "21.5"
    assert state("diag_ip").state == "192.0.2.4"
    assert state("diag_boot").state == "2026-09-14T08:00:00+00:00"
    storage = state("storage_health")
    assert (storage.state, storage.attributes.get("device_class")) == (
        "warning",
        "enum",
    )
    assert storage.attributes["quick_check"] == "ok"
    assert state("diag_cpu").state == STATE_UNAVAILABLE
    companion = state("ha_companion_update")
    assert companion.attributes["installed_version"] == "2026.1.1"
    assert state("reboot").attributes.get("device_class") == "restart"
    assert state("reboot").state == "unknown"
    assert state("button").state == "unknown"
    image = hass.data["image"].get_entity(by_suffix["camera_snapshot"].entity_id)
    assert image.image_url == "http://panel.local:8888/api/v1/camera/snapshot?t=1"
    assert state("camera_snapshot").state == image.image_last_updated.isoformat()

    # Every name resolves from the English catalogue, placeholders filled.
    english = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components"
            / "panel_assistant"
            / "translations"
            / "en.json"
        ).read_text(encoding="utf-8")
    )
    descriptors = {
        f"{DID}_{descriptor['unique_suffix']}": descriptor for descriptor in DESCRIPTORS
    }
    for unique_id, item in entries.items():
        descriptor = descriptors[unique_id]
        name = english["entity"][item.domain][descriptor["translation_key"]]["name"]
        expected = name.format(index=descriptor.get("index"))
        found = hass.states.get(item.entity_id)
        assert found is not None
        assert found.attributes["friendly_name"] == f"alpha {expected}", unique_id


async def test_no_second_paneld_update_and_no_mqtt_entry_is_claimed(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The shipped update entity stays the only one; MQTT entries are untouched."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    mqtt_before = [
        registry.async_get_or_create(
            platform, "mqtt", f"alpha_{suffix}", config_entry=mqtt_entry
        )
        for platform, suffix in (
            ("light", "screen"),
            ("switch", "relay1"),
            ("update", "ha_paneld_update"),
            ("event", "button"),
        )
    ]
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)

    for item in mqtt_before:
        assert registry.async_get(item.entity_id) == item
    updates = [
        item
        for item in er.async_entries_for_config_entry(registry, native.entry_id)
        if item.domain == "update"
    ]
    assert sorted(item.unique_id for item in updates) == sorted(
        [f"{native.entry_id}_update", f"{DID}_ha_companion_update"]
    )


async def test_availability_follows_the_session_and_the_description(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A channel a later session omits goes unavailable and is never removed."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    relay = _native_entries(hass, native.entry_id)[f"{DID}_relay1"].entity_id
    assert hass.states.get(relay).state == "on"

    await client.close()
    await hass.async_block_till_done()
    assert hass.states.get(relay).state == STATE_UNAVAILABLE

    reload = _native_entries(hass, native.entry_id)[f"{DID}_reload"].entity_id
    volume = _native_entries(hass, native.entry_id)[f"{DID}_volume"].entity_id
    again = await hass_ws_client(hass, hass_read_only_access_token)
    # Omit a relay and a button, and describe volume in a shape the catalogue
    # does not know: its reports are still accepted, but it renders nothing.
    later = [
        item | {"translation_key": "loudness"} if item["channel"] == "volume" else item
        for item in DESCRIPTORS
        if item["channel"] not in ("relay1", "reload")
    ]
    token = await _session(again, later)
    await _sync(hass, again, token, later)
    assert hass.states.get(relay).state == STATE_UNAVAILABLE
    assert hass.states.get(reload).state == STATE_UNAVAILABLE
    assert hass.states.get(volume).state == STATE_UNAVAILABLE
    assert hass.states.get(touch_sound(hass, native)).state == "on"
    assert {f"{DID}_relay1", f"{DID}_reload"} <= set(
        _native_entries(hass, native.entry_id)
    )
    # Undescribed channels are rendered as unavailable, not failed writes.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    delta = await _send(
        again,
        _report(token, "delta", [{"channel": "touch_sound", "state": "unavailable"}]),
    )
    assert delta["success"]
    await hass.async_block_till_done()
    touch = _native_entries(hass, native.entry_id)[f"{DID}_touch_sound"].entity_id
    assert hass.states.get(touch).state == STATE_UNAVAILABLE


async def test_unknown_descriptors_are_accepted_and_render_nothing(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A channel, or a known channel in another shape, is listed as unknown."""
    future = {
        "channel": "future_leaf",
        "platform": "switch",
        "translation_key": "future_leaf",
        "unique_suffix": "future_leaf",
    }
    reshaped = {
        "channel": "volume",
        "platform": "number",
        "translation_key": "loudness",
        "unique_suffix": "volume",
    }
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello([future, reshaped, DESCRIPTORS[0]]))
    await hass.async_block_till_done()

    assert response["result"]["channels"] == {
        "accepted": 3,
        "unknown": ["future_leaf", "volume"],
    }
    assert set(_native_entries(hass, native.entry_id)) == {
        f"{DID}_{DESCRIPTORS[0]['unique_suffix']}"
    }
    diagnostics = await async_get_config_entry_diagnostics(hass, native)
    assert diagnostics["transport"]["unknown_channels"] == ["future_leaf", "volume"]
    assert diagnostics["transport"].get("native_entities") is True


async def test_events_fire_once_per_counted_event_id(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A retried or older event ID never fires the entity again."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    button = _native_entries(hass, native.entry_id)[f"{DID}_button"].entity_id

    async def report(event_id: int, event_type: str) -> dict[str, Any]:
        response = await _send(
            client,
            {
                "type": "panel_assistant/report_event",
                "session": token,
                "channel": "button",
                "event_id": event_id,
                "event_type": event_type,
            },
        )
        await hass.async_block_till_done()
        return response

    # Before the full sync an event is refused and not counted.
    early = await report(1, "keycode_home")
    assert early.get("error", {}).get("code") == "invalid_format"
    assert hass.states.get(button).state == STATE_UNAVAILABLE

    await _sync(hass, client, token)
    assert hass.states.get(button).state == "unknown"
    # So the panel's retry of that event, once synced, still fires.
    assert (await report(1, "keycode_home"))["success"]
    assert hass.states.get(button).attributes["event_type"] == "keycode_home"
    assert (await report(2, "keycode_back"))["success"]
    fired = hass.states.get(button)
    assert fired.attributes["event_type"] == "keycode_back"

    assert (await report(2, "keycode_home"))["success"]
    assert (await report(1, "keycode_home"))["success"]
    assert hass.states.get(button) == fired

    assert (await report(3, "keycode_home"))["success"]
    assert hass.states.get(button).attributes["event_type"] == "keycode_home"


@pytest.mark.parametrize(
    ("domain", "suffix", "service", "data"),
    [
        ("switch", "relay1", "turn_on", {}),
        ("switch", "relay1", "turn_off", {}),
        ("light", "screen", "turn_on", {}),
        ("light", "screen", "turn_off", {}),
        ("select", "cpu_governor", "select_option", {"option": "auto"}),
        ("number", "volume", "set_value", {"value": 10}),
        ("text", "navigate", "set_value", {"value": "/x"}),
        ("button", "reboot", "press", {}),
    ],
)
async def test_commands_are_refused_with_a_translated_error(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    domain: str,
    suffix: str,
    service: str,
    data: dict[str, Any],
) -> None:
    """Under the default shadow authority MQTT carries commands; none is sent."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    entity_id = _native_entries(hass, native.entry_id)[f"{DID}_{suffix}"].entity_id

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True
        )

    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "authority_mismatch"
    # The next message answers this request: no command event was queued.
    response = await _send(client, _report(token, "delta", []))
    assert (response["type"], response["success"]) == ("result", True)
    diagnostics = await async_get_config_entry_diagnostics(hass, native)
    assert diagnostics["transport"]["commands"]["counts"] == {}


async def test_the_panels_own_hello_is_fully_known_to_the_catalogue(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The hello a panel built, and its report, need nothing unknown."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    message = deepcopy(PANEL_HELLO) | {"did": DID}

    response = await _send(client, message)
    assert response["success"], response
    assert response["result"]["channels"] == {
        "accepted": len(PANEL_HELLO["channels"]),
        "unknown": [],
    }
    token = response["result"]["session"]
    report = deepcopy(PANEL_REPORT) | {"session": token, "sync": "full_begin"}
    begin = await _send(client, report)
    assert begin["result"]["rejected"] == []
    end = await _send(client, _report(token, "full_end", []))
    assert end["success"]
    await hass.async_block_till_done()

    entries = _native_entries(hass, native.entry_id)
    assert f"{DID}_relay64" in entries
    assert f"{DID}_ha_paneld_update" not in entries
    assert hass.states.get(entries[f"{DID}_screen"].entity_id).state == "off"
    # The panel describes wire-only settings disabled until a user enables them.
    assert (
        entries[f"{DID}_watchdog"].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_a_new_snapshot_report_fetches_the_image_again(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Each camera report is a new snapshot, never the previous cached bytes."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    entity_id = _native_entries(hass, native.entry_id)[
        f"{DID}_camera_snapshot"
    ].entity_id
    image = hass.data["image"].get_entity(entity_id)
    image._cached_image = object()
    before = image.image_last_updated

    url = "http://panel.local:8888/api/v1/camera/snapshot?t=2"
    delta = await _send(
        client,
        _report(
            token,
            "delta",
            [{"channel": "camera_snapshot", "state": "known", "value": {"url": url}}],
        ),
    )
    assert delta["success"]
    await hass.async_block_till_done()

    assert image._cached_image is None
    assert image.image_url == url
    assert image.image_last_updated > before
    assert hass.states.get(entity_id).state == image.image_last_updated.isoformat()

    # An unavailable report is no new snapshot.
    image._cached_image = object()
    gone = await _send(
        client,
        _report(
            token, "delta", [{"channel": "camera_snapshot", "state": "unavailable"}]
        ),
    )
    assert gone["success"]
    await hass.async_block_till_done()
    assert image._cached_image is not None
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_reload_unloads_every_native_platform_and_adds_each_entity_once(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reload tears down all eleven platforms and the next session re-adds."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    before = _registry_digest(hass, native.entry_id)
    assert len(native.runtime_data.platforms) == 11

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
    ):
        assert await hass.config_entries.async_unload(native.entry_id)
        await hass.async_block_till_done()
        relay = _native_entries(hass, native.entry_id)[f"{DID}_relay1"].entity_id
        # Every native platform unloaded: a left-over entity would still be on.
        assert hass.states.get(relay).state == STATE_UNAVAILABLE
        assert hass.states.get(relay).attributes.get("restored") is True
        assert await hass.config_entries.async_setup(native.entry_id)
        await hass.async_block_till_done()
    assert native.state is ConfigEntryState.LOADED

    again = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(again)
    await _sync(hass, again, token)
    await _sync(hass, again, token)

    assert _registry_digest(hass, native.entry_id) == before
    # A second session change must not offer the same entities again.
    assert "already exists" not in caplog.text
    relay = _native_entries(hass, native.entry_id)[f"{DID}_relay1"].entity_id
    assert hass.states.get(relay).state == "on"
    assert len(hass.states.async_entity_ids("switch")) == len(
        [item for item in DESCRIPTORS if item["platform"] == "switch"]
    )


async def test_a_new_panel_identity_never_renders_into_the_old_entities(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A factory-reset panel on the same entry gets its own entities."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    old_relay = _native_entries(hass, native.entry_id)[f"{DID}_relay1"].entity_id
    await client.close()
    await hass.async_block_till_done()

    reset_did = "f" * 64
    health = replace(HEALTH, discovery_id=reset_did)
    coordinator = native.runtime_data.coordinator
    coordinator.async_set_updated_data(replace(coordinator.data, health=health))
    again = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(again, _hello(DESCRIPTORS) | {"did": reset_did})
    assert response["success"], response
    await _sync(hass, again, response["result"]["session"])

    assert hass.states.get(old_relay).state == STATE_UNAVAILABLE
    registry = er.async_get(hass)
    new_relay = registry.async_get_entity_id("switch", DOMAIN, f"{reset_did}_relay1")
    assert new_relay is not None
    assert hass.states.get(new_relay).state == "on"


async def test_only_catalogued_attributes_are_shown(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel attribute can neither overwrite Core's nor appear untranslated."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    led_value = {"on": True, "brightness": 90, "color": {"r": 1, "g": 2, "b": 3}}
    delta = await _send(
        client,
        _report(
            token,
            "delta",
            [
                {
                    "channel": "led",
                    "state": "known",
                    "value": led_value,
                    "attributes": {"color_mode": "xy", "brightness": 255},
                },
                {
                    "channel": "storage_health",
                    "state": "known",
                    "value": "healthy",
                    "attributes": {"quick_check": "ok", "unlisted": 1},
                },
            ],
        ),
    )
    assert delta["success"]
    await hass.async_block_till_done()
    entries = _native_entries(hass, native.entry_id)

    led = hass.states.get(entries[f"{DID}_led"].entity_id)
    assert (led.attributes["color_mode"], led.attributes["brightness"]) == ("rgb", 90)
    storage = hass.states.get(entries[f"{DID}_storage_health"].entity_id)
    assert storage.attributes.get("quick_check") == "ok"
    assert "unlisted" not in storage.attributes


async def test_an_event_channel_without_types_renders_and_fires_nothing(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel whose profile declares no event types keeps its whole session."""
    untyped = [
        item | {"options": None} if item["platform"] == "event" else item
        for item in DESCRIPTORS
    ]
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client, untyped)
    await _sync(hass, client, token, untyped)
    button = _native_entries(hass, native.entry_id)[f"{DID}_button"].entity_id

    response = await _send(
        client,
        {
            "type": "panel_assistant/report_event",
            "session": token,
            "channel": "button",
            "event_id": 1,
            "event_type": "keycode_home",
        },
    )
    await hass.async_block_till_done()

    assert response["success"]
    assert hass.states.get(button).state == "unknown"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_colour_light_stays_rgb_across_a_reload(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Only the learned mode is restored, never the light's value."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    led = _native_entries(hass, native.entry_id)[f"{DID}_led"].entity_id
    assert hass.states.get(led).attributes["supported_color_modes"] == ["rgb"]

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
    ):
        assert await hass.config_entries.async_reload(native.entry_id)
        await hass.async_block_till_done()
    again = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(again)
    screen_off = [item for item in _observations() if item["channel"] != "led"] + [
        {"channel": "led", "state": "known", "value": {"on": False}}
    ]
    for sync, observations in (("full_begin", []), ("full_end", screen_off)):
        assert (await _send(again, _report(token, sync, observations)))["success"]
    await hass.async_block_till_done()

    state = hass.states.get(led)
    assert state.state == "off"
    assert state.attributes["supported_color_modes"] == ["rgb"]

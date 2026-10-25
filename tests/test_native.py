"""Native entities, rendered from the descriptors a panel sends."""

import json
import logging
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.contract import catalogue_entry
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_transport import (
    DID,
    HEALTH,
    STATUS,
    WsClientFactory,
    _registry_digest,
    _send,
)
from .test_transport_contract import CHANNEL_DESCRIPTORS

FIXTURES = Path(__file__).parent / "fixtures"


# Every channel the catalogue knows, as a panel describes it, everything
# enabled so each renders a state.
DESCRIPTORS = [
    descriptor | {"enabled_default": True}
    for descriptor in CHANNEL_DESCRIPTORS
    if descriptor["index"] in (None, 1) and catalogue_entry(descriptor) is not None
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
    {"channel": "dashboard_idle_return", "state": "known", "value": 15},
    {"channel": "dashboard_theme", "state": "known", "value": "ambient"},
    {"channel": "screen_off_timeout", "state": "known", "value": 300},
    {"channel": "diag_boot", "state": "known", "value": "2026-09-14T08:00:00+00:00"},
    {"channel": "media", "state": "known", "value": {"state": "idle", "muted": False}},
    {"channel": "relay1", "state": "known", "value": True},
]


def _hello(channels: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "panel_assistant/hello",
        "protocol": {"min": 5, "max": 5},
        "did": DID,
        # The version health reports, which a test may change.
        "app": {"version": HEALTH.version, "version_code": 790},
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


@contextmanager
def panel_patches() -> Iterator[None]:
    """Answer the panel's health and status, and the install job reads, locally.

    Setting up an entry needs these, so a test that reloads one wraps the
    reload in them too.
    """
    executor = SimpleNamespace(
        async_reconcile_entry=AsyncMock(),
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
    ):
        yield


async def _setup(
    hass: HomeAssistant, user_id: str, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up an entry an administrator bound to the panel's account."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: "panel.local",
            CONF_TRANSPORT_USER_ID: user_id,
            "installation_identity": True,
        },
        options=options or {},
    )
    config_entry.add_to_hass(hass)
    with panel_patches():
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry


@pytest.fixture
async def native(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """A bound panel entry."""
    yield await _setup(hass, hass_read_only_user.id)


def _native_entries(hass: HomeAssistant, entry_id: str) -> dict[str, er.RegistryEntry]:
    return {
        item.unique_id: item
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        if item.unique_id.startswith(f"{DID}_")
    }


def touch_sound(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return _native_entries(hass, entry.entry_id)[f"{DID}_touch_sound"].entity_id


def _expected_unique_ids() -> set[str]:
    # The media player shows the volume, so a panel with one has no number.
    return {
        f"{DID}_{descriptor['unique_suffix']}"
        for descriptor in DESCRIPTORS
        if descriptor["channel"] not in ("update_paneld", "volume")
    }


# ---------------------------------------------------------------------------
# Rendered from the descriptors.


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
        "camera",
        "event",
        "light",
        "media_player",
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
    assert by_suffix["ha_companion_update"].translation_key == "update_companion"
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
    assert state("dashboard_theme").state == "ambient"
    assert state("dashboard_theme").attributes["options"] == [
        "follow",
        "dark",
        "light",
        "ambient",
    ]
    # Minutes are typed, not dragged along a 1,440-step slider.
    idle = state("dashboard_idle_return")
    assert (idle.state, idle.attributes["mode"], idle.attributes["max"]) == (
        "15",
        "box",
        1440,
    )
    assert idle.attributes["unit_of_measurement"] == "min"
    timeout = state("screen_off_timeout")
    assert (timeout.state, timeout.attributes["mode"]) == ("300", "box")
    assert timeout.attributes["unit_of_measurement"] == "s"
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
    navigate = _native_entries(hass, native.entry_id)[f"{DID}_navigate"].entity_id
    again = await hass_ws_client(hass, hass_read_only_access_token)
    # Omit a relay and a button, and describe navigate in a shape the catalogue
    # does not know: its reports are still accepted, but it renders nothing.
    later = [
        item | {"translation_key": "route"} if item["channel"] == "navigate" else item
        for item in DESCRIPTORS
        if item["channel"] not in ("relay1", "reload")
    ]
    token = await _session(again, later)
    await _sync(hass, again, token, later)
    assert hass.states.get(relay).state == STATE_UNAVAILABLE
    assert hass.states.get(reload).state == STATE_UNAVAILABLE
    assert hass.states.get(navigate).state == STATE_UNAVAILABLE
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


async def test_a_withheld_companion_update_is_never_a_pending_update(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel with no Companion app reports the channel unavailable, and the
    native entity then renders unavailable rather than a pending update.

    This is the native half of the phantom the panel side withholds: an entity
    in ``on`` would list under Settings, Updates, count on the badge and be
    swept up by "Update all" on a panel that deliberately has no Companion app.
    The panel decides; the integration only renders what it is told, so this
    holds with no integration code of its own.
    """
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    companion = _native_entries(hass, native.entry_id)[
        f"{DID}_ha_companion_update"
    ].entity_id

    delta = await _send(
        client,
        _report(
            token, "delta", [{"channel": "update_companion", "state": "unavailable"}]
        ),
    )
    assert delta["success"]
    await hass.async_block_till_done()

    withheld = hass.states.get(companion)
    assert withheld.state == STATE_UNAVAILABLE
    assert withheld.attributes.get("installed_version") is None
    assert withheld.attributes.get("latest_version") is None
    # An unrelated channel is untouched by the Companion's absence.
    assert (
        hass.states.get(
            _native_entries(hass, native.entry_id)[f"{DID}_touch_sound"].entity_id
        ).state
        == "on"
    )


USB_DEBUGGING_DESCRIPTOR = {
    "channel": "diag_usb_debugging",
    "platform": "binary_sensor",
    "translation_key": "usb_debugging",
    "unique_suffix": "usb_debugging",
    "entity_category": "diagnostic",
    "enabled_default": True,
}


async def test_usb_debugging_reports_observed_state_and_read_loss(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """USB debugging follows actual reports, with unreadable state unavailable."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client, [USB_DEBUGGING_DESCRIPTOR])
    await hass.async_block_till_done()
    entry = _native_entries(hass, native.entry_id)[f"{DID}_usb_debugging"]
    assert entry.domain == "binary_sensor"
    assert entry.entity_category == "diagnostic"
    assert entry.disabled_by is None
    assert entry.translation_key == "usb_debugging"
    assert hass.states.get(entry.entity_id).state == STATE_UNAVAILABLE

    # Completing a sync without a read must not invent an OFF observation.
    await _sync(hass, client, token, [USB_DEBUGGING_DESCRIPTOR])
    assert hass.states.get(entry.entity_id).state == STATE_UNAVAILABLE
    for observation, expected in (
        ({"state": "known", "value": True}, "on"),
        ({"state": "unavailable"}, STATE_UNAVAILABLE),
        ({"state": "known", "value": False}, "off"),
        ({"state": "known", "value": True}, "on"),
    ):
        response = await _send(
            client,
            _report(token, "delta", [{"channel": "diag_usb_debugging", **observation}]),
        )
        assert response["success"], response
        assert response["result"]["rejected"] == []
        await hass.async_block_till_done()
        assert hass.states.get(entry.entity_id).state == expected

    await client.close()
    await hass.async_block_till_done()
    assert hass.states.get(entry.entity_id).state == STATE_UNAVAILABLE
    assert f"{DID}_usb_debugging" in _native_entries(hass, native.entry_id)


async def test_older_catalogue_ignores_usb_debugging_without_refusing_session(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An integration without this channel still serves the panel's known ones."""
    from custom_components.panel_assistant import contract

    earlier = {
        key: value
        for key, value in contract._BY_CHANNEL.items()
        if key != "diag_usb_debugging"
    }
    known = next(d for d in DESCRIPTORS if d["channel"] == "auto_brightness")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with patch.dict(contract._BY_CHANNEL, earlier, clear=True):
        response = await _send(client, _hello([USB_DEBUGGING_DESCRIPTOR, known]))
        assert response["success"], response
        assert response["result"]["channels"] == {
            "accepted": 2,
            "unknown": ["diag_usb_debugging"],
        }
        token = response["result"]["session"]
        await _sync(hass, client, token, [USB_DEBUGGING_DESCRIPTOR, known])
        assert set(_native_entries(hass, native.entry_id)) == {
            f"{DID}_{known['unique_suffix']}"
        }
        known_entry = next(iter(_native_entries(hass, native.entry_id).values()))
        assert hass.states.get(known_entry.entity_id).state == "off"


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
    # The Android key names the MQTT button entity used, so automations match.
    assert hass.states.get(button).attributes["event_type"] == "KEYCODE_HOME"
    assert "KEYCODE_BACK" in hass.states.get(button).attributes["event_types"]
    assert (await report(2, "keycode_back"))["success"]
    fired = hass.states.get(button)
    assert fired.attributes["event_type"] == "KEYCODE_BACK"

    assert (await report(2, "keycode_home"))["success"]
    assert (await report(1, "keycode_home"))["success"]
    assert hass.states.get(button) == fired

    assert (await report(3, "keycode_home"))["success"]
    assert hass.states.get(button).attributes["event_type"] == "KEYCODE_HOME"


async def test_the_panels_own_hello_is_fully_known_to_the_catalogue(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Captured panel channels with the current producer envelope stay known."""
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


async def test_reload_unloads_every_native_platform_and_adds_each_entity_once(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A reload tears down all twelve platforms and the next session re-adds."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    before = _registry_digest(hass, native.entry_id)
    assert len(native.runtime_data.platforms) == 12

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
    """An unconfirmed replacement cannot create or reuse this entry's entities."""
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
    assert response["error"]["code"] == "unknown_panel"
    assert hass.states.get(old_relay).state == STATE_UNAVAILABLE
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, f"{reset_did}_relay1") is None


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
                {
                    "channel": "auto_sleep_activity",
                    "state": "known",
                    "value": True,
                    "attributes": {
                        "reason": "presence",
                        "learned_delay": "90s",
                        "source_count": 2,
                        "phase": "holding",
                        "manual_suppression": False,
                        "unlisted": 1,
                    },
                },
                {
                    "channel": "zigbee_gateway_health",
                    "state": "known",
                    "value": "runaway",
                    "attributes": {"gateway_cpu_percent": 97, "unlisted": 1},
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
    # The attributes the MQTT auto-sleep activity entity carried.
    activity = hass.states.get(entries[f"{DID}_auto_sleep_activity"].entity_id)
    assert activity.state == "on"
    assert {
        key: activity.attributes.get(key)
        for key in (
            "reason",
            "learned_delay",
            "source_count",
            "phase",
            "manual_suppression",
        )
    } == {
        "reason": "presence",
        "learned_delay": "90s",
        "source_count": 2,
        "phase": "holding",
        "manual_suppression": False,
    }
    assert "unlisted" not in activity.attributes
    zigbee = hass.states.get(entries[f"{DID}_zigbee_gateway_health"].entity_id)
    assert zigbee.state == "runaway"
    assert zigbee.attributes.get("gateway_cpu_percent") == 97
    assert "unlisted" not in zigbee.attributes


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


async def test_the_session_names_the_build_on_the_device_card(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The version and build the panel declared in its hello reach the card.

    They arrive after the entities registered it, and whether or not the
    stored address answers, so the card is corrected when the session opens.
    """
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier(
        (DOMAIN, native.entry_id), native.entry_id
    )
    assert device is not None
    assert device.sw_version == HEALTH.version

    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _session(client)
    await hass.async_block_till_done()

    device = registry.async_get_device_by_identifier(
        (DOMAIN, native.entry_id), native.entry_id
    )
    assert device is not None
    assert device.sw_version == "0.9.8-rc1 (790)"

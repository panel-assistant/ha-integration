"""The per-panel cutover: MQTT entities move to this integration, and back."""

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import RELOAD_AFTER_UPDATE_DELAY, ConfigEntryState
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import entity_sources
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntity,
    MockEntityPlatform,
    async_fire_time_changed,
)

from custom_components.panel_assistant import cutover, transport
from custom_components.panel_assistant.const import (
    CONF_CUTOVER,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.contract import catalogue_entry_for_suffix
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_native import _session, _setup, _sync, panel_patches
from .test_transport import DID, HEALTH, WsClientFactory, _send

PANEL_ID = HEALTH.panel_id
MQTT_AREA = "living_room"


def _mqtt_device_identifiers() -> set[tuple[str, str]]:
    return {("mqtt", f"ha-paneld-{PANEL_ID}"), ("mqtt", "ha-paneld-aid-1")}


def _mqtt(
    hass: HomeAssistant,
    entities: list[tuple[str, str, dict[str, Any]]],
    *,
    device: bool = True,
    loaded: bool = True,
) -> dict[str, Any]:
    """Register the panel's MQTT config entry, device and entities.

    Each entity is a platform, a unique suffix, and registry keywords.
    """
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    if loaded:
        mqtt_entry.mock_state(hass, ConfigEntryState.LOADED)
    device_entry = None
    if device:
        device_entry = dr.async_get(hass).async_get_or_create(
            config_entry_id=mqtt_entry.entry_id,
            identifiers=_mqtt_device_identifiers(),
        )
        dr.async_get(hass).async_update_device(device_entry.id, area_id=MQTT_AREA)
    registry = er.async_get(hass)
    entity_ids: dict[str, str] = {}
    for platform, suffix, keywords in entities:
        item = registry.async_get_or_create(
            platform,
            "mqtt",
            f"{PANEL_ID}_{suffix}",
            config_entry=mqtt_entry,
            device_id=None if device_entry is None else device_entry.id,
            **keywords,
        )
        entity_ids[suffix] = item.entity_id
    return {"entry": mqtt_entry, "device": device_entry, "entity_ids": entity_ids}


def _record(entry: MockConfigEntry) -> dict[str, Any]:
    record: dict[str, Any] = entry.data[CONF_CUTOVER]
    return record


def _issue(hass: HomeAssistant, issue: str, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{issue}_{entry_id}")


def _snapshot(hass: HomeAssistant, *entry_ids: str) -> list[tuple[Any, ...]]:
    """Return everything the cutover may touch on these entries' entities."""
    registry = er.async_get(hass)
    return sorted(
        (
            item.entity_id,
            item.platform,
            item.unique_id,
            item.config_entry_id,
            item.device_id,
            item.disabled_by,
            item.hidden_by,
            item.name,
            item.icon,
            item.area_id,
            item.entity_category,
        )
        for entry_id in entry_ids
        for item in er.async_entries_for_config_entry(registry, entry_id)
    )


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with panel_patches():
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


_HELLO_TAIL: dict[str, Any] = {
    "protocol": {"min": 1, "max": 1},
    "did": DID,
    "app": {"version": "0.9.8-rc1", "version_code": 790},
    "contract_digest": "c" * 64,
    "capabilities": ["state", "events", "commands", "approval"],
    "channels": [],
}


async def _hello_result(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, token: str
) -> dict[str, Any]:
    client = await hass_ws_client(hass, token)
    response = await _send(client, {"type": "panel_assistant/hello"} | _HELLO_TAIL)
    assert response["success"], response
    result: dict[str, Any] = response["result"]
    return result


NATIVE = {"authority": "native"}


# ---------------------------------------------------------------------------
# Forward.


async def test_forward_cutover_keeps_each_entity_and_its_customisations(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Every known MQTT entity moves with its ID, settings and room intact."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {"original_name": "Relay 1"}),
            ("light", "button_led2", {}),
            ("sensor", "diag_cpu", {"entity_category": EntityCategory.DIAGNOSTIC}),
            ("text", "home_dashboard", {}),
            ("switch", "voice_assistant", {}),
        ],
    )
    registry = er.async_get(hass)
    relay = mqtt["entity_ids"]["relay1"]
    registry.async_update_entity(
        relay,
        name="Porch lamp",
        icon="mdi:lamp",
        area_id="porch",
        entity_category=EntityCategory.CONFIG,
    )
    registry.async_update_entity(
        mqtt["entity_ids"]["home_dashboard"], hidden_by=er.RegistryEntryHider.USER
    )
    before = {
        suffix: registry.async_get(entity_id)
        for suffix, entity_id in mqtt["entity_ids"].items()
    }

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.area_id == MQTT_AREA
    for suffix, old in before.items():
        assert old is not None
        item = registry.async_get(old.entity_id)
        assert item is not None, suffix
        assert item.platform == DOMAIN
        assert item.unique_id == f"{DID}_{suffix}"
        assert item.config_entry_id == entry.entry_id
        assert item.device_id == device.id
        assert item.disabled_by is None
        assert item.id == old.id
        assert (item.name, item.icon, item.area_id, item.hidden_by) == (
            old.name,
            old.icon,
            old.area_id,
            old.hidden_by,
        )
        assert item.entity_category == old.entity_category
    moved_relay = registry.async_get(relay)
    assert moved_relay is not None
    assert moved_relay.name == "Porch lamp"
    assert moved_relay.icon == "mdi:lamp"
    assert moved_relay.area_id == "porch"
    assert moved_relay.entity_category is EntityCategory.CONFIG
    assert registry.async_get_entity_id("switch", "mqtt", f"{PANEL_ID}_relay1") is None

    record = _record(entry)
    assert record["state"] == "complete"
    assert record["did"] == DID
    assert record["panel_id"] == PANEL_ID
    assert record["unmigrated"] == []
    assert record["removed"] == []
    assert "error" not in record
    assert {info["entity_id"] for info in record["entities"].values()} == set(
        mqtt["entity_ids"].values()
    )
    assert {info["state"] for info in record["entities"].values()} == {"done"}
    assert set(record["entities"]) == {
        old.id for old in before.values() if old is not None
    }
    relay_info = record["entities"][moved_relay.id]
    assert relay_info["mqtt_unique_id"] == f"{PANEL_ID}_relay1"
    assert relay_info["mqtt_config_entry_id"] == mqtt["entry"].entry_id
    assert relay_info["mqtt_device_id"] == mqtt["device"].id
    assert relay_info["disabled_by_before"] is None
    assert _issue(hass, "cutover_incomplete", entry.entry_id) is None
    assert (
        _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id) is None
    )

    # The native entities render into the moved registry entries: same IDs.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    state = hass.states.get(relay)
    assert state is not None
    assert state.state == "on"
    assert state.name == "Porch lamp"
    assert hass.states.get(mqtt["entity_ids"]["diag_cpu"]) is not None

    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["active_owner"] == "native"
    assert transport["mqtt_discovery"] == "withdraw"
    assert transport["mqtt_discovery_granted"] == "withdraw"
    assert transport["cutover"]["did"] == "**REDACTED**"
    assert transport["cutover"]["panel_id"] == "**REDACTED**"
    shown = transport["cutover"]["entities"][moved_relay.id]
    assert shown["mqtt_unique_id"] == "**REDACTED**"
    assert shown["entity_id"] == relay
    assert transport["cutover"]["state"] == "complete"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"][CONF_CUTOVER] == "**REDACTED**"


async def test_a_person_disabled_mqtt_entity_stays_disabled_and_moves(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A disable of the person's own is not this integration's to clear."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {"disabled_by": er.RegistryEntryDisabler.USER}),
            (
                "switch",
                "watchdog",
                {"disabled_by": er.RegistryEntryDisabler.INTEGRATION},
            ),
        ],
    )

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    registry = er.async_get(hass)
    relay = registry.async_get(mqtt["entity_ids"]["relay1"])
    watchdog = registry.async_get(mqtt["entity_ids"]["watchdog"])
    assert relay is not None and watchdog is not None
    assert relay.platform == DOMAIN
    assert relay.unique_id == f"{DID}_relay1"
    assert relay.disabled_by is er.RegistryEntryDisabler.USER
    assert watchdog.platform == DOMAIN
    assert watchdog.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    record = _record(entry)
    assert record["entities"][relay.id]["disabled_by_before"] == "user"
    assert record["entities"][watchdog.id]["disabled_by_before"] == "integration"
    assert record["state"] == "complete"


async def test_a_native_entry_on_the_target_id_is_removed_first(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Native entities rendered while MQTT still owned the panel give way."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _session(client)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    native_relay = registry.async_get_entity_id("switch", DOMAIN, f"{DID}_relay1")
    native_volume = registry.async_get_entity_id("number", DOMAIN, f"{DID}_volume")
    assert native_relay is not None and native_volume is not None
    assert native_relay != mqtt["entity_ids"]["relay1"]
    native_count = len(er.async_entries_for_config_entry(registry, entry.entry_id))

    hass.config_entries.async_update_entry(entry, options=NATIVE)
    await _reload(hass, entry)

    assert registry.async_get(native_relay) is None
    assert registry.async_get(native_volume) is None
    moved = registry.async_get(mqtt["entity_ids"]["relay1"])
    assert moved is not None
    assert moved.platform == DOMAIN
    assert moved.unique_id == f"{DID}_relay1"
    record = _record(entry)
    assert sorted(record["removed"]) == sorted([native_relay, native_volume])
    assert record["state"] == "complete"
    # Two native entries gave way to two moved ones; nothing else changed.
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == (
        native_count
    )


async def test_unknown_suffixes_and_the_paneld_update_stay_with_mqtt(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Only channels a native entity renders move; the rest are listed."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {}),
            ("switch", "mystery", {}),
            ("switch", "relay0", {}),
            ("update", "ha_paneld_update", {}),
        ],
    )
    registry = er.async_get(hass)
    # Prefixed, but on another device: another integration's entity.
    other_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt["entry"].entry_id, identifiers={("mqtt", "other")}
    )
    stranger = registry.async_get_or_create(
        "switch",
        "mqtt",
        f"{PANEL_ID}_relay2",
        config_entry=mqtt["entry"],
        device_id=other_device.id,
    )
    unrelated = registry.async_get_or_create(
        "sensor", "mqtt", "unrelated", config_entry=mqtt["entry"]
    )

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    record = _record(entry)
    assert [
        (item["entity_id"], item["unique_suffix"], item["reason"], item["customised"])
        for item in record["unmigrated"]
    ] == [
        (mqtt["entity_ids"]["mystery"], "mystery", "unknown_suffix", False),
        (mqtt["entity_ids"]["relay0"], "relay0", "unknown_suffix", False),
        (
            mqtt["entity_ids"]["ha_paneld_update"],
            "ha_paneld_update",
            "not_rendered",
            False,
        ),
    ]
    assert {item["registry_id"] for item in record["unmigrated"]} == {
        registry.async_get(mqtt["entity_ids"][s]).id  # type: ignore[union-attr]
        for s in ("mystery", "relay0", "ha_paneld_update")
    }
    for suffix in ("mystery", "relay0", "ha_paneld_update"):
        item = registry.async_get(mqtt["entity_ids"][suffix])
        assert item is not None
        assert item.platform == "mqtt"
        assert item.unique_id == f"{PANEL_ID}_{suffix}"
    for untouched in (stranger, unrelated):
        item = registry.async_get(untouched.entity_id)
        assert item is not None
        assert item.platform == "mqtt"
        assert item.unique_id == untouched.unique_id
    assert registry.async_get(mqtt["entity_ids"]["relay1"]).platform == DOMAIN  # type: ignore[union-attr]
    assert list(record["entities"]) == [
        registry.async_get(mqtt["entity_ids"]["relay1"]).id  # type: ignore[union-attr]
    ]
    assert record["state"] == "complete"
    assert (
        _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id) is None
    )
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "withdraw"


@pytest.mark.parametrize(
    "customise",
    [
        {"name": "Mine"},
        {"icon": "mdi:star"},
        {"area_id": "porch"},
        {"hidden_by": er.RegistryEntryHider.USER},
        {"disabled_by": er.RegistryEntryDisabler.USER},
    ],
    ids=lambda customise: next(iter(customise)),
)
async def test_a_customised_unmigrated_entity_withholds_the_claim(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    customise: dict[str, Any],
) -> None:
    """The panel keeps announcing until the person deletes or clears it."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {}),
            ("switch", "mystery", {}),
            ("update", "ha_paneld_update", {}),
        ],
    )
    registry = er.async_get(hass)
    mystery = mqtt["entity_ids"]["mystery"]
    registry.async_update_entity(mystery, **customise)
    # A disable that is not the person's own blocks nothing.
    registry.async_update_entity(
        mqtt["entity_ids"]["ha_paneld_update"],
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    record = _record(entry)
    assert record["state"] == "complete"
    assert {
        item["unique_suffix"]: item["customised"] for item in record["unmigrated"]
    } == {
        "mystery": True,
        "ha_paneld_update": False,
    }
    issue = _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {"panel": "alpha", "entities": mystery}
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "announce"
    assert result["authority"] == "native"
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["active_owner"] == "native"
    assert transport["mqtt_discovery"] == "announce"
    assert transport["mqtt_discovery_granted"] == "announce"

    # Deleting the entity releases the claim without a reload.
    registry.async_remove(mystery)
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "withdraw"
    assert (
        _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id) is None
    )
    assert _record(entry) == record


@pytest.mark.parametrize(
    ("flag", "options", "mqtt_discovery"),
    [
        (True, {}, "announce"),
        (True, {"authority": "shadow"}, "announce"),
        (True, {"authority": "mqtt"}, "announce"),
        (False, {"authority": "native"}, "announce"),
        (True, {"authority": "native"}, "withdraw"),
    ],
)
async def test_hello_claims_mqtt_discovery_only_under_a_completed_native_cutover(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    flag: bool,
    options: dict[str, str],
    mqtt_discovery: str,
) -> None:
    """Shadow, MQTT and a dark option announce; only native withdraws."""
    _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=flag, options=options)

    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)

    assert "mqtt_discovery" in result
    assert result["mqtt_discovery"] == mqtt_discovery
    assert (CONF_CUTOVER in entry.data) is (mqtt_discovery == "withdraw")
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["mqtt_discovery"] == mqtt_discovery
    assert transport["active_owner"] == (
        "native" if mqtt_discovery == "withdraw" else "mqtt"
    )


async def test_a_loaded_mqtt_entity_is_unloaded_before_it_moves(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Core migrates only unloaded entities, so a loaded one is disabled first."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    relay = mqtt["entity_ids"]["relay1"]
    platform = MockEntityPlatform(hass, domain="switch", platform_name="mqtt")
    platform.config_entry = mqtt["entry"]
    await platform.async_add_entities([MockEntity(unique_id=f"{PANEL_ID}_relay1")])
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert relay in entity_sources(hass)
    assert hass.states.get(relay) is not None
    with pytest.raises(ValueError):
        registry.async_update_entity_platform(
            relay,
            DOMAIN,
            new_config_entry_id="x",
            new_unique_id=f"{DID}_relay1",
        )

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    assert relay not in entity_sources(hass)
    assert hass.states.get(relay) is None
    item = registry.async_get(relay)
    assert item is not None
    assert item.platform == DOMAIN
    assert item.unique_id == f"{DID}_relay1"
    assert item.disabled_by is None
    record = _record(entry)
    assert record["state"] == "complete"
    assert record["entities"][item.id]["state"] == "done"
    assert record["entities"][item.id]["disabled_by_before"] is None

    # The native switch renders under the very same entity ID.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    state = hass.states.get(relay)
    assert state is not None
    assert state.state == "on"


class _SlowToUnload(MockEntity):
    """An entity whose removal suspends before it leaves the loaded entities."""

    async def async_internal_will_remove_from_hass(self) -> None:
        await asyncio.sleep(0.2)
        await super().async_internal_will_remove_from_hass()


async def test_the_move_waits_for_an_entity_that_is_slow_to_unload(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """The disable takes effect later; the move waits for it rather than fail."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    relay = mqtt["entity_ids"]["relay1"]
    platform = MockEntityPlatform(hass, domain="switch", platform_name="mqtt")
    platform.config_entry = mqtt["entry"]
    await platform.async_add_entities([_SlowToUnload(unique_id=f"{PANEL_ID}_relay1")])
    await hass.async_block_till_done()
    assert relay in entity_sources(hass)

    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    assert relay not in entity_sources(hass)
    item = er.async_get(hass).async_get(relay)
    assert item is not None
    assert item.platform == DOMAIN
    assert item.disabled_by is None
    record = _record(entry)
    assert record["state"] == "complete"
    assert "error" not in record


# ---------------------------------------------------------------------------
# Failure at each step, and the retry.


def _fail_device(hass: HomeAssistant) -> Any:
    return patch.object(cutover, "_own_device", side_effect=RuntimeError("device"))


def _fail_wait(hass: HomeAssistant) -> Any:
    return patch.object(
        cutover, "_async_wait_unloaded", side_effect=TimeoutError("still loaded")
    )


def _fail_migrate(hass: HomeAssistant) -> Any:
    return patch.object(
        er.EntityRegistry, "async_update_entity_platform", side_effect=ValueError("no")
    )


def _fail_enable(hass: HomeAssistant) -> Any:
    registry = er.async_get(hass)
    original = er.EntityRegistry.async_update_entity

    def update(entity_id: str, **changes: Any) -> Any:
        if "disabled_by" in changes and changes["disabled_by"] is None:
            raise RuntimeError("enable")
        return original(registry, entity_id, **changes)

    return patch.object(er.EntityRegistry, "async_update_entity", side_effect=update)


def _fail_record(hass: HomeAssistant) -> Any:
    original = hass.config_entries.async_update_entry
    failed = False

    def update(entry: Any, **changes: Any) -> Any:
        nonlocal failed
        if CONF_CUTOVER in changes.get("data", {}) and not failed:
            failed = True
            raise OSError("disk")
        return original(entry, **changes)

    return patch.object(hass.config_entries, "async_update_entry", side_effect=update)


@pytest.mark.parametrize(
    ("step", "failure", "error", "entity_state", "entity_platform", "disabled"),
    [
        ("device", _fail_device, "RuntimeError", None, "mqtt", None),
        ("disable", _fail_wait, "TimeoutError", "disabled", "mqtt", "integration"),
        ("migrate", _fail_migrate, "ValueError", "disabled", "mqtt", "integration"),
        ("enable", _fail_enable, "RuntimeError", "migrated", DOMAIN, "integration"),
        ("record", _fail_record, "OSError", None, "mqtt", None),
    ],
)
async def test_a_failed_step_leaves_a_partial_record_and_the_retry_finishes(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
    step: str,
    failure: Callable[..., Any],
    error: str,
    entity_state: str | None,
    entity_platform: str,
    disabled: str | None,
) -> None:
    """Setup still succeeds, record and issue say where, the next setup finishes."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    relay = mqtt["entity_ids"]["relay1"]

    with failure(hass):
        entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)

    assert entry.state is ConfigEntryState.LOADED
    record = _record(entry)
    assert record["state"] == "in_progress"
    assert record["error"]["step"] == step
    assert record["error"]["exception"] == error
    assert record["error"]["entity_id"] == (None if entity_state is None else relay)
    item = er.async_get(hass).async_get(relay)
    assert item is not None
    assert item.platform == entity_platform
    assert (None if item.disabled_by is None else item.disabled_by.value) == disabled
    if entity_state is None:
        assert record.get("entities", {}) == {}
    else:
        assert record["entities"][item.id]["state"] == entity_state
        assert record["entities"][item.id]["disabled_by_before"] is None
    issue = _issue(hass, "cutover_incomplete", entry.entry_id)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "panel": "alpha",
        "step": step,
        "error": error,
    }
    assert any(
        r.levelno == logging.WARNING and "stopped at the" in r.getMessage()
        for r in caplog.records
    )
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "announce"
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["active_owner"] == "mqtt"
    assert transport["cutover"]["error"]["step"] == step

    await _reload(hass, entry)

    record = _record(entry)
    assert record["state"] == "complete"
    assert "error" not in record
    item = er.async_get(hass).async_get(relay)
    assert item is not None
    assert item.platform == DOMAIN
    assert item.unique_id == f"{DID}_relay1"
    assert item.disabled_by is None
    assert record["entities"][item.id]["state"] == "done"
    assert _issue(hass, "cutover_incomplete", entry.entry_id) is None
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "withdraw"


async def test_the_delayed_reload_after_enabling_changes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Core reloads an entry after its entity was enabled; the record stands."""
    _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    record = _record(entry)
    assert record["state"] == "complete"
    snapshot = _snapshot(hass, entry.entry_id)
    writes: list[dict[str, Any]] = []
    original = hass.config_entries.async_update_entry

    def update(target: Any, **changes: Any) -> Any:
        writes.append(changes)
        return original(target, **changes)

    with (
        panel_patches(),
        patch.object(hass.config_entries, "async_update_entry", side_effect=update),
        patch.object(
            hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
        ) as reload,
    ):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=RELOAD_AFTER_UPDATE_DELAY + 1)
        )
        await hass.async_block_till_done()

    assert reload.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert writes == []
    assert _record(entry) == record
    assert _snapshot(hass, entry.entry_id) == snapshot


async def test_a_second_setup_with_a_complete_record_changes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Idempotent by record and by inspection: nothing is written again."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("light", "screen", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    record = _record(entry)
    snapshot = _snapshot(hass, entry.entry_id, mqtt["entry"].entry_id)
    writes: list[dict[str, Any]] = []
    original = hass.config_entries.async_update_entry

    def update(target: Any, **changes: Any) -> Any:
        writes.append(changes)
        return original(target, **changes)

    with patch.object(hass.config_entries, "async_update_entry", side_effect=update):
        await _reload(hass, entry)

    assert writes == []
    assert _record(entry) == record
    assert _snapshot(hass, entry.entry_id, mqtt["entry"].entry_id) == snapshot

    # By inspection too: a lost record finds nothing left to move.
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_CUTOVER}
    )
    await _reload(hass, entry)

    fresh = _record(entry)
    assert fresh["state"] == "complete"
    assert fresh["entities"] == {}
    assert fresh["unmigrated"] == []
    assert _snapshot(hass, entry.entry_id, mqtt["entry"].entry_id) == snapshot


async def test_two_loaded_entries_with_one_identity_refuse_to_move_anything(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """An identity two entries report keys nothing; the move waits with an issue."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    first = await _setup(hass, hass_read_only_user.id, native=True)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="beta",
        data={
            CONF_ADDRESS: "panel-2.local",
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
        },
        options=NATIVE,
    )
    second.add_to_hass(hass)

    with panel_patches():
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED
    record = _record(second)
    assert record["state"] == "in_progress"
    assert record["error"] == {
        "step": "identity",
        "exception": "ValueError",
        "entity_id": None,
    }
    issue = _issue(hass, "cutover_incomplete", second.entry_id)
    assert issue is not None
    assert issue.translation_placeholders["step"] == "identity"
    item = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None
    assert item.platform == "mqtt"
    assert CONF_CUTOVER not in first.data


# ---------------------------------------------------------------------------
# Reversal.


async def test_reversal_hands_every_entity_back_to_mqtt(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Shadow again restores platform, unique ID, config entry and device."""
    mqtt = _mqtt(
        hass,
        [
            ("switch", "relay1", {}),
            ("switch", "relay2", {"disabled_by": er.RegistryEntryDisabler.USER}),
            ("switch", "mystery", {}),
        ],
    )
    registry = er.async_get(hass)
    registry.async_update_entity(mqtt["entity_ids"]["relay1"], name="Porch lamp")
    before = _snapshot(hass, mqtt["entry"].entry_id)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert _snapshot(hass, mqtt["entry"].entry_id) != before

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    await _reload(hass, entry)

    assert _snapshot(hass, mqtt["entry"].entry_id) == before
    assert CONF_CUTOVER not in entry.data
    assert _issue(hass, "cutover_incomplete", entry.entry_id) is None
    assert (
        _issue(hass, "cutover_blocked_by_customised_entities", entry.entry_id) is None
    )
    result = await _hello_result(hass, hass_ws_client, hass_read_only_access_token)
    assert result["mqtt_discovery"] == "announce"
    assert result["authority"] == "shadow"
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["cutover"] is None
    assert transport["active_owner"] == "mqtt"


async def test_turning_the_flag_off_reverses_the_next_setup(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Without native entities the effective authority is shadow, so it reverses."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    before = _snapshot(hass, mqtt["entry"].entry_id)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert CONF_CUTOVER in entry.data

    hass.data[DOMAIN]["native_entities"] = False
    await _reload(hass, entry)

    assert _snapshot(hass, mqtt["entry"].entry_id) == before
    assert CONF_CUTOVER not in entry.data


async def test_reversal_without_the_mqtt_device_links_no_device(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A device removed meanwhile is not invented again."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    dr.async_get(hass).async_remove_device(mqtt["device"].id)

    hass.config_entries.async_update_entry(entry, options={"authority": "mqtt"})
    await _reload(hass, entry)

    item = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None
    assert item.platform == "mqtt"
    assert item.unique_id == f"{PANEL_ID}_relay1"
    assert item.config_entry_id == mqtt["entry"].entry_id
    assert item.device_id is None
    assert CONF_CUTOVER not in entry.data


async def test_reversal_skips_an_entity_a_person_deleted(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A deleted entity is recorded as such and blocks nothing."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    registry = er.async_get(hass)
    registry.async_remove(mqtt["entity_ids"]["relay1"])
    writes: list[dict[str, Any]] = []
    original = hass.config_entries.async_update_entry

    def update(target: Any, **changes: Any) -> Any:
        writes.append(changes)
        return original(target, **changes)

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    with patch.object(hass.config_entries, "async_update_entry", side_effect=update):
        await _reload(hass, entry)

    assert registry.async_get(mqtt["entity_ids"]["relay1"]) is None
    volume = registry.async_get(mqtt["entity_ids"]["volume"])
    assert volume is not None
    assert volume.platform == "mqtt"
    assert CONF_CUTOVER not in entry.data
    states = [
        info["state"]
        for write in writes
        if CONF_CUTOVER in write.get("data", {})
        for info in write["data"][CONF_CUTOVER]["entities"].values()
    ]
    assert "deleted" in states


async def test_reversal_falls_back_to_the_one_loaded_mqtt_entry(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A recorded MQTT entry that is gone is replaced by the one that is loaded."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})], loaded=False)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    await hass.config_entries.async_remove(mqtt["entry"].entry_id)
    await hass.async_block_till_done()
    replacement = MockConfigEntry(domain="mqtt")
    replacement.add_to_hass(hass)
    replacement.mock_state(hass, ConfigEntryState.LOADED)
    # Removing the MQTT entry took its entities with it; ours survived the move.
    relay = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert relay is not None and relay.platform == DOMAIN

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    await _reload(hass, entry)

    item = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None
    assert item.platform == "mqtt"
    assert item.config_entry_id == replacement.entry_id
    assert CONF_CUTOVER not in entry.data


async def test_reversal_without_any_mqtt_entry_waits_with_an_issue(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Nothing to hand the entities to: the record stays and the issue says so."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})], loaded=False)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    await hass.config_entries.async_remove(mqtt["entry"].entry_id)
    await hass.async_block_till_done()

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    await _reload(hass, entry)

    assert CONF_CUTOVER in entry.data
    record = _record(entry)
    assert record["state"] == "reversing"
    assert record["error"]["step"] == "mqtt_entry"
    item = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None
    assert item.platform == DOMAIN
    issue = _issue(hass, "cutover_incomplete", entry.entry_id)
    assert issue is not None
    assert issue.translation_placeholders["step"] == "mqtt_entry"


async def test_reversal_waits_for_an_mqtt_entry_discovered_again(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """An MQTT entry on the old unique ID is MQTT's; the moved one waits."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    registry = er.async_get(hass)
    duplicate = registry.async_get_or_create(
        "switch", "mqtt", f"{PANEL_ID}_relay1", config_entry=mqtt["entry"]
    )

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    await _reload(hass, entry)

    assert CONF_CUTOVER in entry.data
    record = _record(entry)
    assert record["state"] == "reversing"
    assert record["error"] == {
        "step": "target",
        "exception": "ValueError",
        "entity_id": mqtt["entity_ids"]["relay1"],
    }
    assert registry.async_get(duplicate.entity_id) is not None
    item = registry.async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None and item.platform == DOMAIN

    # Once the duplicate is gone, the next setup finishes the reversal.
    registry.async_remove(duplicate.entity_id)
    await _reload(hass, entry)

    item = registry.async_get(mqtt["entity_ids"]["relay1"])
    assert item is not None
    assert item.platform == "mqtt"
    assert CONF_CUTOVER not in entry.data


async def test_the_claim_needs_the_native_authority_as_well_as_the_record(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A complete record under any other effective authority owns nothing."""
    _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert transport.mqtt_discovery_claim(hass, entry) == "withdraw"
    assert transport.entity_owner(hass, entry) == "native"

    # The flag off makes the effective authority shadow, record or not.
    hass.data[DOMAIN]["native_entities"] = False

    assert _record(entry)["state"] == "complete"
    assert transport.mqtt_discovery_claim(hass, entry) == "announce"
    assert transport.entity_owner(hass, entry) == "mqtt"


async def test_a_shadow_setup_without_a_record_writes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Under shadow the MQTT entities are not touched and no record is written."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    before = _snapshot(hass, mqtt["entry"].entry_id)
    writes: list[dict[str, Any]] = []
    original = hass.config_entries.async_update_entry

    def update(target: Any, **changes: Any) -> Any:
        writes.append(changes)
        return original(target, **changes)

    with patch.object(hass.config_entries, "async_update_entry", side_effect=update):
        entry = await _setup(hass, hass_read_only_user.id, native=True)

    assert writes == []
    assert CONF_CUTOVER not in entry.data
    assert _snapshot(hass, mqtt["entry"].entry_id) == before


# ---------------------------------------------------------------------------
# Reloads.


async def test_saving_the_option_reloads_exactly_once_and_data_writes_never(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Only a change of the effective authority reloads the entry."""
    _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True)

    with (
        panel_patches(),
        patch.object(
            hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
        ) as reload,
    ):
        # Data writes, as binding and the record make.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: "panel-2.local"}
        )
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CUTOVER: {"state": "complete"}}
        )
        await hass.async_block_till_done()
        assert reload.call_count == 0
        # The same authority again.
        form = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            form["flow_id"], {"authority": "shadow"}
        )
        await hass.async_block_till_done()
        assert reload.call_count == 0

        form = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            form["flow_id"], {"authority": "native"}
        )
        await hass.async_block_till_done()

    assert reload.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.authority == "native"
    assert _record(entry)["state"] == "complete"


# ---------------------------------------------------------------------------
# The suffix lookup the cutover matches MQTT entities with.


@pytest.mark.parametrize(
    ("platform", "suffix", "translation_key"),
    [
        ("switch", "relay1", "relay"),
        ("switch", "relay64", "relay"),
        ("light", "button_led7", "button_led"),
        ("switch", "voice_assistant", "voice_enabled"),
        ("update", "ha_paneld_update", "update_paneld"),
        ("sensor", "diag_cpu", "diag_cpu"),
        ("switch", "relay0", None),
        ("switch", "relay65", None),
        ("switch", "relay01", None),
        ("switch", "relay", None),
        ("light", "relay1", None),
        ("switch", "mystery", None),
    ],
)
def test_suffix_lookup_matches_the_catalogue(
    platform: str, suffix: str, translation_key: str | None
) -> None:
    """Plain suffixes, indexed families within the bound, and the platform agree."""
    entry = catalogue_entry_for_suffix(platform, suffix)
    if translation_key is None:
        assert entry is None
        return
    assert entry is not None
    assert entry["translation_key"] == translation_key
    assert entry["platform"] == platform

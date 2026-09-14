"""Version guards: duplicates MQTT creates for an owned panel, and removed panels."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.mqtt.util import async_cleanup_device_registry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import entity_sources
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntity,
    MockEntityPlatform,
)

from custom_components.panel_assistant import guards
from custom_components.panel_assistant.const import (
    CONF_CUTOVER,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_cutover import (
    _HELLO_TAIL,
    NATIVE,
    PANEL_ID,
    _issue,
    _mqtt,
    _mqtt_device_identifiers,
    _record,
    _reload,
)
from .test_native import DESCRIPTORS, _hello, _setup, _sync, panel_patches
from .test_transport import DID, HEALTH, WsClientFactory, _send

VECTORS = json.loads(
    (
        Path(__file__).parent / "fixtures" / "panel_assistant_transport_v1_vectors.json"
    ).read_text(encoding="utf-8")
)
ISSUE = "panel_update_required"
CAPABLE = ["state", "events", "commands", "approval", "mqtt_withdraw"]
# What MQTT rediscovers of a moved panel: platform, suffix.
MOVED = [("switch", "relay1"), ("number", "volume"), ("sensor", "diag_cpu")]


@pytest.fixture(autouse=True)
def _short_load_wait() -> Iterator[None]:
    """An entity created in the registry alone never loads; do not wait long."""
    with patch.object(guards, "LOAD_TIMEOUT", 0.05):
        yield


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return this integration's warnings, and Core's report of a remove failure."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and (
            record.name.startswith("custom_components.panel_assistant")
            or "remove callback" in record.getMessage()
        )
    ]


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


def _quarantined(entry: MockConfigEntry) -> list[str]:
    quarantined: list[str] = _record(entry).get("quarantined", [])
    return quarantined


async def _load_mqtt_entity(
    hass: HomeAssistant,
    mqtt_entry: MockConfigEntry,
    domain: str,
    unique_id: str,
    identifiers: set[tuple[str, str]] | None = None,
) -> MockEntity:
    """Add an entity through the MQTT platform, as MQTT's discovery does.

    The platform creates or updates the registry entry, links the device and
    loads the entity, in that order.
    """
    platform = MockEntityPlatform(hass, domain=domain, platform_name="mqtt")
    platform.config_entry = mqtt_entry
    entity = MockEntity(
        unique_id=unique_id,
        device_info={
            "identifiers": identifiers
            if identifiers is not None
            else _mqtt_device_identifiers()
        },
    )
    await platform.async_add_entities([entity])
    return entity


def _create_mqtt(
    hass: HomeAssistant, mqtt: dict[str, Any], domain: str, suffix: str
) -> er.RegistryEntry:
    """Register an MQTT entity on the panel's MQTT device, loading nothing."""
    return er.async_get(hass).async_get_or_create(
        domain,
        "mqtt",
        f"{PANEL_ID}_{suffix}",
        config_entry=mqtt["entry"],
        device_id=mqtt["device"].id,
    )


def _loaded(hass: HomeAssistant, entity_id: str) -> bool:
    return entity_id in entity_sources(hass) or hass.states.get(entity_id) is not None


async def _capable_sync(
    hass: HomeAssistant, client: Any, capabilities: list[str]
) -> dict[str, Any]:
    """Open a session offering these capabilities and complete its full sync."""
    response = await _send(client, _hello(DESCRIPTORS) | {"capabilities": capabilities})
    assert response["success"], response
    result: dict[str, Any] = response["result"]
    await _sync(hass, client, result["session"])
    await _settle(hass)
    return result


@contextmanager
def _panel_id(panel_id: str) -> Iterator[None]:
    with patch("tests.test_native.HEALTH", replace(HEALTH, panel_id=panel_id)):
        yield


# ---------------------------------------------------------------------------
# Devices are not merged; MQTT's own cleanup cannot touch the entry's device.


async def test_mqtt_device_cleanup_never_removes_the_entry_device_or_moved_entities(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """MQTT removes only its own device, and only MQTT's entities go with it."""
    mqtt = _mqtt(
        hass,
        [("switch", "relay1", {}), ("number", "volume", {}), ("switch", "mystery", {})],
    )
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    before = {
        suffix: registry.async_get(entity_id)
        for suffix, entity_id in mqtt["entity_ids"].items()
        if suffix != "mystery"
    }
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    own = device_registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert own is not None
    # MQTT's cleanup reads its triggers and tags only for its own device.
    hass.data["mqtt"] = SimpleNamespace(device_triggers={}, tags={})

    await async_cleanup_device_registry(hass, own.id, mqtt["entry"].entry_id)
    await async_cleanup_device_registry(hass, mqtt["device"].id, mqtt["entry"].entry_id)
    # The unmigrated entity kept MQTT's device; removing it takes that entity.
    assert device_registry.async_get(mqtt["device"].id) is not None
    device_registry.async_remove_device(mqtt["device"].id)
    await hass.async_block_till_done()

    assert device_registry.async_get(mqtt["device"].id) is None
    assert registry.async_get(mqtt["entity_ids"]["mystery"]) is None
    assert device_registry.async_get(own.id) is not None
    for suffix, old in before.items():
        assert old is not None
        item = registry.async_get(old.entity_id)
        assert item is not None, suffix
        assert (item.id, item.platform, item.device_id, item.unique_id) == (
            old.id,
            DOMAIN,
            own.id,
            f"{DID}_{suffix}",
        )


# ---------------------------------------------------------------------------
# The mqtt_withdraw capability and the update issue.


@pytest.mark.parametrize(
    ("options", "granted"),
    [
        ({"authority": "mqtt"}, []),
        ({}, ["events", "state"]),
        (NATIVE, ["approval", "commands", "events", "state"]),
    ],
    ids=["mqtt", "shadow", "native"],
)
@pytest.mark.parametrize("offered", [True, False])
async def test_mqtt_withdraw_is_granted_whenever_it_is_offered(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    options: dict[str, str],
    granted: list[str],
    offered: bool,
) -> None:
    """Under every authority, and the answer is always there."""
    _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=options)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    capabilities = CAPABLE if offered else CAPABLE[:-1]

    response = await _send(
        client,
        {"type": "panel_assistant/hello"}
        | _HELLO_TAIL
        | {"capabilities": capabilities},
    )

    assert response["success"], response
    result = response["result"]
    assert result["capabilities"] == sorted(
        [*granted, "mqtt_withdraw"] if offered else granted
    )
    assert result["mqtt_discovery"] in ("withdraw", "announce")
    transport = (await async_get_config_entry_diagnostics(hass, entry))["transport"]
    assert transport["mqtt_withdraw_offered"] is offered


@pytest.mark.parametrize(
    ("options", "raised"),
    [({"authority": "mqtt"}, False), ({}, False), (NATIVE, True)],
    ids=["mqtt", "shadow", "native"],
)
async def test_a_session_without_mqtt_withdraw_asks_for_the_panel_update(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    options: dict[str, str],
    raised: bool,
) -> None:
    """Only while this integration owns the panel's entities."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=options)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    capable = await _send(
        client,
        {"type": "panel_assistant/hello"} | _HELLO_TAIL | {"capabilities": CAPABLE},
    )
    assert capable["success"], capable
    assert _issue(hass, ISSUE, entry.entry_id) is None

    client = await hass_ws_client(hass, hass_read_only_access_token)
    older = await _send(client, {"type": "panel_assistant/hello"} | _HELLO_TAIL)
    assert older["success"], older

    issue = _issue(hass, ISSUE, entry.entry_id)
    if raised:
        assert issue is not None
        assert issue.is_fixable is False
        assert issue.is_persistent is False
        assert issue.translation_key == ISSUE
        assert issue.translation_placeholders == {
            "panel": "alpha",
            "required_version": "0.9.8-rc1",
        }
    else:
        assert issue is None

    # What MQTT creates for the panel is a duplicate only of what is owned.
    duplicate = _create_mqtt(hass, mqtt, "number", "volume")
    await _settle(hass)

    item = er.async_get(hass).async_get(duplicate.entity_id)
    assert item is not None
    if raised:
        assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    else:
        assert _issue(hass, ISSUE, entry.entry_id) is None
        assert item.disabled_by is None
        assert "quarantined" not in entry.data.get(CONF_CUTOVER, {})


# ---------------------------------------------------------------------------
# Quarantine.


async def test_the_quarantine_waits_for_an_entity_that_loads_after_it_is_created(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A disable in the create listener would leave the duplicate loaded."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    moved = mqtt["entity_ids"]["relay1"]
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert _issue(hass, ISSUE, entry.entry_id) is None

    entity = await _load_mqtt_entity(
        hass, mqtt["entry"], "switch", f"{PANEL_ID}_relay1"
    )
    await _settle(hass)

    registry = er.async_get(hass)
    duplicate = registry.async_get_entity_id("switch", "mqtt", f"{PANEL_ID}_relay1")
    assert duplicate is not None and duplicate == entity.entity_id
    item = registry.async_get(duplicate)
    assert item is not None
    assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert duplicate not in entity_sources(hass)
    assert hass.states.get(duplicate) is None
    assert _quarantined(entry) == [item.id]
    assert _issue(hass, ISSUE, entry.entry_id) is not None
    original = registry.async_get(moved)
    assert original is not None
    assert (original.platform, original.disabled_by) == (DOMAIN, None)


async def test_an_entity_created_disabled_and_enabled_again_is_still_quarantined(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Created disabled on the panel's device, then enabled and loaded."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    registry = er.async_get(hass)

    created = registry.async_get_or_create(
        "switch",
        "mqtt",
        f"{PANEL_ID}_relay1",
        config_entry=mqtt["entry"],
        device_id=mqtt["device"].id,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    registry.async_update_entity(created.entity_id, disabled_by=None)
    await _load_mqtt_entity(hass, mqtt["entry"], "switch", f"{PANEL_ID}_relay1")
    await _settle(hass)

    item = registry.async_get(created.entity_id)
    assert item is not None
    assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert not _loaded(hass, created.entity_id)
    assert _quarantined(entry) == [created.id]


async def test_a_downgraded_panel_never_duplicates_and_an_upgrade_cleans_up(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Rediscovery, cleanup and a second rediscovery of every moved entity."""
    mqtt = _mqtt(hass, [(domain, suffix, {}) for domain, suffix in MOVED])
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    moved = {
        suffix: registry.async_get(mqtt["entity_ids"][suffix]) for _, suffix in MOVED
    }
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    record = _record(entry)
    assert record["state"] == "complete"

    # An older panel announces again: MQTT creates every moved entity anew on
    # the panel's device, and loads one of them.
    loaded = await _load_mqtt_entity(
        hass, mqtt["entry"], "switch", f"{PANEL_ID}_relay1"
    )
    for domain, suffix in MOVED[1:]:
        _create_mqtt(hass, mqtt, domain, suffix)
    await _settle(hass)

    duplicates = {
        suffix: registry.async_get_entity_id(domain, "mqtt", f"{PANEL_ID}_{suffix}")
        for domain, suffix in MOVED
    }
    assert loaded.entity_id == duplicates["relay1"]
    ids = {}
    for suffix, entity_id in duplicates.items():
        assert entity_id is not None, suffix
        item = registry.async_get(entity_id)
        assert item is not None
        assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION, suffix
        assert not _loaded(hass, entity_id), suffix
        ids[suffix] = item.id
    assert sorted(_quarantined(entry)) == sorted(ids.values())
    assert _issue(hass, ISSUE, entry.entry_id) is not None

    # A session that does not follow the withdrawal cleans nothing up.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    older = await _capable_sync(hass, client, CAPABLE[:-1])
    assert older["mqtt_discovery"] == "withdraw"
    assert sorted(_quarantined(entry)) == sorted(ids.values())
    assert all(registry.entities.get_entry(i) is not None for i in ids.values())
    assert _issue(hass, ISSUE, entry.entry_id) is not None

    # The updated panel completes its full sync under withdraw.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    capable = await _capable_sync(hass, client, CAPABLE)
    assert capable["mqtt_discovery"] == "withdraw"

    assert all(registry.entities.get_entry(i) is None for i in ids.values())
    assert _quarantined(entry) == []
    assert device_registry.async_get(mqtt["device"].id) is None
    assert _issue(hass, ISSUE, entry.entry_id) is None
    for suffix, old in moved.items():
        assert old is not None
        item = registry.async_get(old.entity_id)
        assert item is not None, suffix
        assert (item.id, item.platform) == (old.id, DOMAIN)

    # Downgraded again: MQTT restores each removed entry, disabled, enables it
    # again and loads it through its platform, which links a new device.
    for domain, suffix in MOVED:
        restored = registry.async_get_or_create(domain, "mqtt", f"{PANEL_ID}_{suffix}")
        assert restored.id == ids[suffix]
        assert restored.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        registry.async_update_entity(restored.entity_id, disabled_by=None)
        await _load_mqtt_entity(hass, mqtt["entry"], domain, f"{PANEL_ID}_{suffix}")
    await _settle(hass)

    for suffix, registry_id in ids.items():
        item = registry.entities.get_entry(registry_id)
        assert item is not None, suffix
        assert item.device_id is not None
        assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION, suffix
        assert not _loaded(hass, item.entity_id), suffix
    assert sorted(_quarantined(entry)) == sorted(ids.values())
    assert _issue(hass, ISSUE, entry.entry_id) is not None


async def test_cleanup_keeps_a_quarantined_duplicate_a_person_changed(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Named or disabled by a person: kept, listed, and so is MQTT's device."""
    mqtt = _mqtt(hass, [(domain, suffix, {}) for domain, suffix in MOVED])
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    duplicates = {
        suffix: _create_mqtt(hass, mqtt, domain, suffix) for domain, suffix in MOVED
    }
    await _settle(hass)
    assert sorted(_quarantined(entry)) == sorted(i.id for i in duplicates.values())
    registry.async_update_entity(duplicates["relay1"].entity_id, name="Mine")
    registry.async_update_entity(
        duplicates["volume"].entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )

    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _capable_sync(hass, client, CAPABLE)

    assert registry.entities.get_entry(duplicates["diag_cpu"].id) is None
    kept = [duplicates["relay1"].id, duplicates["volume"].id]
    assert all(registry.entities.get_entry(i) is not None for i in kept)
    assert sorted(_quarantined(entry)) == sorted(kept)
    assert device_registry.async_get(mqtt["device"].id) is not None
    assert _issue(hass, ISSUE, entry.entry_id) is None


async def test_a_renamed_panel_is_guarded_under_its_new_id(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """MQTT announces the panel under its current ID after a rename."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    renamed = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt["entry"].entry_id,
        identifiers={("mqtt", "ha-paneld-beta")},
    )

    with _panel_id("beta"):
        await _reload(hass, entry)
    duplicate = er.async_get(hass).async_get_or_create(
        "switch",
        "mqtt",
        "beta_relay1",
        config_entry=mqtt["entry"],
        device_id=renamed.id,
    )
    await _settle(hass)

    assert _record(entry)["panel_id"] == PANEL_ID
    assert _quarantined(entry) == [duplicate.id]


async def test_a_panel_whose_id_prefixes_another_keeps_its_hands_off(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Matched by device identifier: ``office`` never takes ``office_dash``."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    mqtt_entry.mock_state(hass, ConfigEntryState.LOADED)
    device_registry = dr.async_get(hass)
    registry = er.async_get(hass)
    devices = {
        panel: device_registry.async_get_or_create(
            config_entry_id=mqtt_entry.entry_id,
            identifiers={("mqtt", f"ha-paneld-{panel}")},
        )
        for panel in ("office", "office_dash")
    }
    registry.async_get_or_create(
        "switch",
        "mqtt",
        "office_relay1",
        config_entry=mqtt_entry,
        device_id=devices["office"].id,
    )
    with _panel_id("office"):
        entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert _record(entry)["state"] == "complete"

    dash = [
        registry.async_get_or_create(
            "switch",
            "mqtt",
            f"office_dash_{suffix}",
            config_entry=mqtt_entry,
            device_id=devices["office_dash"].id,
        )
        for suffix in ("relay1", "relay2")
    ]
    loaded_dash = await _load_mqtt_entity(
        hass,
        mqtt_entry,
        "number",
        "office_dash_volume",
        identifiers={("mqtt", "ha-paneld-office_dash")},
    )
    ours = registry.async_get_or_create(
        "number",
        "mqtt",
        "office_volume",
        config_entry=mqtt_entry,
        device_id=devices["office"].id,
    )
    await _settle(hass)

    for item in dash:
        current = registry.async_get(item.entity_id)
        assert current is not None
        assert current.disabled_by is None
    assert loaded_dash.entity_id in entity_sources(hass)
    assert _quarantined(entry) == [ours.id]


async def test_setup_quarantines_rediscovered_duplicates_beside_a_customised_one(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Moved entities rediscovered while unloaded; the customised one stays."""
    mqtt = _mqtt(
        hass,
        [("switch", "relay1", {}), ("number", "volume", {}), ("switch", "mystery", {})],
    )
    registry = er.async_get(hass)
    mystery = mqtt["entity_ids"]["mystery"]
    registry.async_update_entity(mystery, name="Mine")
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    # The panel holds the claim at announce, so MQTT discovers everything
    # again while the entry is not loaded.
    duplicates = [
        _create_mqtt(hass, mqtt, "switch", "relay1"),
        _create_mqtt(hass, mqtt, "number", "volume"),
    ]
    with panel_patches():
        assert await hass.config_entries.async_setup(entry.entry_id)
        await _settle(hass)

    assert sorted(_quarantined(entry)) == sorted(item.id for item in duplicates)
    for item in duplicates:
        current = registry.async_get(item.entity_id)
        assert current is not None
        assert current.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    kept = registry.async_get(mystery)
    assert kept is not None
    assert (kept.platform, kept.disabled_by, kept.name) == ("mqtt", None, "Mine")
    assert _issue(hass, ISSUE, entry.entry_id) is not None

    # The claim is still announce, so a capable panel's sync removes nothing.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    result = await _capable_sync(hass, client, CAPABLE)
    assert result["mqtt_discovery"] == "announce"
    assert all(registry.async_get(item.entity_id) is not None for item in duplicates)

    # A restart forgets the issue; setup raises it again, and writes nothing.
    hass.config_entries.async_update_entry(entry, data=dict(entry.data))
    guards.async_delete_panel_update_required(hass, entry.entry_id)
    record = deepcopy(_record(entry))
    writes: list[dict[str, Any]] = []
    original = hass.config_entries.async_update_entry

    def update(target: Any, **changes: Any) -> Any:
        writes.append(changes)
        return original(target, **changes)

    with patch.object(hass.config_entries, "async_update_entry", side_effect=update):
        await _reload(hass, entry)
        await _settle(hass)

    assert _issue(hass, ISSUE, entry.entry_id) is not None
    assert writes == []
    assert _record(entry) == record


async def test_reversal_removes_quarantined_duplicates_and_restores_entity_ids(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A quarantined duplicate never holds back the release."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    registry = er.async_get(hass)
    relay = registry.async_update_entity(
        mqtt["entity_ids"]["relay1"], new_entity_id="switch.porch_lamp"
    )
    volume = registry.async_get(mqtt["entity_ids"]["volume"])
    assert volume is not None
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    duplicates = [
        _create_mqtt(hass, mqtt, "switch", "relay1"),
        _create_mqtt(hass, mqtt, "number", "volume"),
    ]
    await _settle(hass)
    assert sorted(_quarantined(entry)) == sorted(item.id for item in duplicates)

    hass.config_entries.async_update_entry(entry, options={"authority": "shadow"})
    await _reload(hass, entry)
    await _settle(hass)

    assert CONF_CUTOVER not in entry.data
    for item in duplicates:
        assert registry.entities.get_entry(item.id) is None
    for original, suffix in ((relay, "relay1"), (volume, "volume")):
        back = registry.entities.get_entry(original.id)
        assert back is not None
        assert (
            back.entity_id,
            back.platform,
            back.unique_id,
            back.config_entry_id,
            back.device_id,
        ) == (
            original.entity_id,
            "mqtt",
            f"{PANEL_ID}_{suffix}",
            mqtt["entry"].entry_id,
            mqtt["device"].id,
        )
        # MQTT would read a removed duplicate's record under this unique ID as
        # its own removed entity, and enable and unhide the original again.
        assert (back.domain, "mqtt", back.unique_id) not in registry.deleted_entities
    assert _issue(hass, "cutover_incomplete", entry.entry_id) is None


# ---------------------------------------------------------------------------
# Entry removal.


async def _hello_error(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, token: str
) -> str | None:
    client = await hass_ws_client(hass, token)
    response = await _send(client, {"type": "panel_assistant/hello"} | _HELLO_TAIL)
    if response["success"]:
        return None
    code: str = response["error"]["code"]
    return code


async def _reloaded_store(hass: HomeAssistant) -> guards.RemovedPanels:
    """Read the removed panels back from storage, as after a restart."""
    removed = guards.RemovedPanels(hass)
    await removed.async_load()
    return removed


async def test_removing_a_cut_over_entry_hands_its_entities_back_and_tells_the_panel(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Back on MQTT before Core clears the entry; the next hello says removed."""
    mqtt = _mqtt(hass, [("switch", "relay1", {}), ("number", "volume", {})])
    registry = er.async_get(hass)
    relay = registry.async_update_entity(
        mqtt["entity_ids"]["relay1"], new_entity_id="switch.porch_lamp", name="Porch"
    )
    volume = registry.async_get(mqtt["entity_ids"]["volume"])
    assert volume is not None
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    duplicate = _create_mqtt(hass, mqtt, "number", "volume")
    await _settle(hass)
    assert _quarantined(entry) == [duplicate.id]
    assert _issue(hass, ISSUE, entry.entry_id) is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await _settle(hass)

    assert registry.entities.get_entry(duplicate.id) is None
    for original, suffix in ((relay, "relay1"), (volume, "volume")):
        back = registry.entities.get_entry(original.id)
        assert back is not None, suffix
        assert (
            back.entity_id,
            back.platform,
            back.unique_id,
            back.config_entry_id,
        ) == (
            original.entity_id,
            "mqtt",
            f"{PANEL_ID}_{suffix}",
            mqtt["entry"].entry_id,
        )
    assert registry.entities.get_entry(relay.id).name == "Porch"  # type: ignore[union-attr]
    assert _issue(hass, ISSUE, entry.entry_id) is None
    assert _warnings(caplog) == []

    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "entry_removed"
    )
    # Across a restart.
    reloaded = await _reloaded_store(hass)
    assert DID in reloaded
    hass.data[DOMAIN]["removed_panels"] = reloaded
    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "entry_removed"
    )

    # Adding the panel again forgets the removal.
    again = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: "panel.local",
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
        },
    )
    again.add_to_hass(hass)
    with panel_patches():
        assert await hass.config_entries.async_setup(again.entry_id)
        await hass.async_block_till_done()
    assert DID not in reloaded
    assert DID not in await _reloaded_store(hass)
    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) is None
    assert await hass.config_entries.async_unload(again.entry_id)
    await hass.async_block_till_done()
    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "unknown_panel"
    )


async def test_removing_an_entry_without_a_record_stores_nothing_and_raises_nothing(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A shadow entry's panel was never claimed; it stays an unknown panel."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    before = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    entry = await _setup(hass, hass_read_only_user.id, native=True)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _warnings(caplog) == []
    assert er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"]) == before
    assert guards.REMOVED_PANELS_STORAGE_KEY not in hass_storage
    assert DID not in await _reloaded_store(hass)
    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "unknown_panel"
    )


async def test_removal_that_cannot_hand_entities_back_still_tells_the_panel(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No MQTT entry to hand them to: logged, never raised, and still remembered."""
    mqtt = _mqtt(hass, [("switch", "relay1", {})], loaded=False)
    entry = await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    await hass.config_entries.async_remove(mqtt["entry"].entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    messages = _warnings(caplog)
    assert any("mqtt_entry step" in message for message in messages), messages
    assert not any("remove callback" in message for message in messages)
    assert DID in await _reloaded_store(hass)
    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "entry_removed"
    )


async def test_a_removed_identity_two_loaded_entries_report_is_not_called_removed(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An ambiguous identity is unknown, whatever was removed before."""
    await _setup(hass, hass_read_only_user.id, native=True)
    twin = MockConfigEntry(
        domain=DOMAIN, title="twin", data={CONF_ADDRESS: "twin.local"}, unique_id=DID
    )
    twin.add_to_hass(hass)
    twin.mock_state(hass, ConfigEntryState.LOADED)
    await hass.data[DOMAIN]["removed_panels"].async_add(DID)

    assert await _hello_error(hass, hass_ws_client, hass_read_only_access_token) == (
        "unknown_panel"
    )


async def test_the_removed_panels_keep_only_the_newest(hass: HomeAssistant) -> None:
    """Bounded, oldest dropped first, and malformed stored items ignored."""
    removed = guards.RemovedPanels(hass)
    dids = [f"{index:064x}" for index in range(guards.MAX_REMOVED_PANELS + 1)]
    for did in dids:
        await removed.async_add(did)
    await removed.async_add(dids[1])

    reloaded = await _reloaded_store(hass)

    assert dids[0] not in reloaded
    assert all(did in reloaded for did in dids[1:])
    await reloaded.async_add("f" * 64)
    assert dids[2] not in await _reloaded_store(hass)
    assert dids[1] in await _reloaded_store(hass)


# ---------------------------------------------------------------------------
# The hello result a capable panel reads.


async def test_the_live_hello_result_matches_its_conformance_vector(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The vector is what this integration answers under a completed cutover."""
    vector = next(
        v
        for v in VECTORS["results"]
        if v["name"] == "hello_result_native_withdraw_granted"
    )
    _mqtt(hass, [("switch", "relay1", {})])
    await _setup(hass, hass_read_only_user.id, native=True, options=NATIVE)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, deepcopy(vector["request"]) | {"did": DID})

    assert response["success"], response
    result = response["result"]
    expected = vector["result"]
    assert set(result) == set(expected)
    for key in ("protocol", "authority", "mqtt_discovery", "capabilities", "channels"):
        assert result[key] == expected[key], key
    assert isinstance(result["session"], str) and result["session"]
    assert set(result["integration"]) == set(expected["integration"])

"""The one device card every platform shares, and the identity it must not adopt."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.device import (
    async_refresh_panel_device,
    panel_device_info,
    panel_reported_name,
)
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.status import (
    PanelDevice,
    PanelStatus,
    parse_status_response,
)

HEALTH = PanelHealth(
    version="0.9.7-rc4",
    panel_id="alpha_panel",
    build="1788979164237",
    config_hash="7ac44093",
    protocol=(5, 5),
)


def _snapshot(
    device: PanelDevice | None,
    *,
    with_status: bool = True,
    health: PanelHealth = HEALTH,
) -> PanelSnapshot:
    status = (
        PanelStatus(warning_count=0, capability_count=0, panel_assistant_device=device)
        if with_status
        else None
    )
    return PanelSnapshot(health=health, status=status, status_error=None)


def test_reported_facts_fill_every_card_field() -> None:
    """A panel that states its hardware should leave no blank on the device page."""
    info = panel_device_info(
        "entry-1",
        _snapshot(
            PanelDevice(
                name="Alpha panel",
                manufacturer="Acme",
                model="AP-1",
                hw_version="Android 14 · TQ3A",
                area="Study",
            )
        ),
    )

    assert info["name"] == "Alpha panel"
    assert info["manufacturer"] == "Acme"
    assert info["model"] == "AP-1"
    assert info["suggested_area"] == "Study"
    assert info["sw_version"] == "0.9.7-rc4"
    assert info["configuration_url"] == "homeassistant://panel-assistant/entry-1"


def test_the_card_keeps_its_own_identifier_and_claims_no_unstated_hardware() -> None:
    """The identifier is this entry's alone; a MAC only once the panel states it."""
    info = panel_device_info(
        "entry-1",
        _snapshot(PanelDevice(name="Alpha panel", manufacturer="Acme")),
    )

    assert info["identifiers"] == {(DOMAIN, "entry-1")}
    assert "connections" not in info
    assert "serial_number" not in info


def test_a_silent_panel_still_produces_a_usable_card() -> None:
    """Panels below the release that added the projection must not blank the page."""
    for snapshot in (_snapshot(None), _snapshot(None, with_status=False)):
        info = panel_device_info("entry-1", snapshot)

        assert info["name"] == "alpha_panel"
        assert info["sw_version"] == "0.9.7-rc4"
        # Unstated, so left out: the registry keeps what the panel last said
        # rather than being told the product is this integration's app.
        assert "model" not in info
        assert "manufacturer" not in info
        assert "suggested_area" not in info


def test_a_partial_report_fills_only_what_the_panel_stated() -> None:
    """A field the panel dropped stays absent instead of arriving empty."""
    info = panel_device_info(
        "entry-1", _snapshot(PanelDevice(manufacturer="Acme", area="Study"))
    )

    assert info["manufacturer"] == "Acme"
    assert info["suggested_area"] == "Study"
    assert info["name"] == "alpha_panel"
    assert "model" not in info


def test_the_card_never_shows_an_older_apps_android_first_hardware_line() -> None:
    """Older apps sent `Android <release> · <build>` inside the device projection.

    The card's hardware line comes only from the panel's hardware facts, so that
    line is never shown and a card without facts keeps what it had.
    """
    for snapshot in (
        _snapshot(
            PanelDevice(model="Wall Display X2i", hw_version="Android 11 · RD2A")
        ),
        _snapshot(None),
        None,
    ):
        info = panel_device_info("entry-1", snapshot, "alpha")

        assert "hw_version" not in info
        assert "serial_number" not in info


@pytest.mark.parametrize(
    ("hardware", "line"),
    [
        ({"firmware": "1.5.6", "android_release": "8.1.0"}, "1.5.6 · Android 8.1.0"),
        ({"firmware": "1.5.6"}, "1.5.6"),
        ({"android_release": "14"}, "Android 14"),
    ],
)
def test_the_hardware_line_leads_with_the_vendor_firmware(
    hardware: dict[str, str], line: str
) -> None:
    """Firmware first, because most panels never change their Android release."""
    status = PanelStatus(
        warning_count=0, capability_count=0, panel_assistant_hardware=hardware
    )
    info = panel_device_info(
        "entry-1", PanelSnapshot(health=HEALTH, status=status, status_error=None)
    )

    assert info["hw_version"] == line
    assert "serial_number" not in info


def test_the_profile_model_the_panel_reports_is_the_card_model() -> None:
    """The panel names the product from its profile; the card shows exactly that."""
    info = panel_device_info(
        "entry-1",
        _snapshot(PanelDevice(manufacturer="Shelly", model="Wall Display X2i")),
    )

    assert info["manufacturer"] == "Shelly"
    assert info["model"] == "Wall Display X2i"


def test_the_firmware_line_preserves_the_exact_version_and_build() -> None:
    """A release candidate is rebuilt many times, so the version alone is ambiguous."""
    info = panel_device_info(
        "entry-1", _snapshot(None, health=replace(HEALTH, version_code=812))
    )

    assert info["sw_version"] == "0.9.7-rc4 (812)"


def test_the_session_names_the_build_even_when_the_address_is_silent() -> None:
    """A connected panel declared its version and build; the card uses them."""
    for snapshot in (None, _snapshot(None, health=replace(HEALTH, version_code=812))):
        info = panel_device_info(
            "entry-1", snapshot, "alpha", app_build=("0.9.8-rc2", 904)
        )

        assert info["sw_version"] == "0.9.8-rc2 (904)"


def test_an_unknown_version_leaves_the_registered_one_alone() -> None:
    """Nobody has answered yet, so there is nothing to replace the last version with."""
    info = panel_device_info("entry-1", None, "alpha")

    assert "sw_version" not in info
    assert info["name"] == "alpha"


async def test_a_registered_card_is_brought_up_to_date(hass: HomeAssistant) -> None:
    """The card a panel registered earlier learns the session's build and the
    panel's hardware facts, while the product it named is kept."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Shelly",
        model="Wall Display X2i",
        sw_version="0.9.8-rc1",
        hw_version="Android 11 · RD2A.211001.002 release-keys",
        configuration_url="http://192.168.1.23:8888/",
    )

    async_refresh_panel_device(
        hass,
        entry.entry_id,
        panel_device_info(
            entry.entry_id,
            PanelSnapshot(
                health=HEALTH,
                status=parse_status_response(
                    '{"warnings":[],"capabilities":[],"panel_assistant_hardware":'
                    '{"firmware":"2.6.8","android_release":"11",'
                    '"serial_number":"90000000000000002"}}'
                ),
                status_error=None,
            ),
            "alpha",
            ("0.9.8-rc2", 904),
        ),
    )

    device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert (
        device.configuration_url == f"homeassistant://panel-assistant/{entry.entry_id}"
    )
    assert device.sw_version == "0.9.8-rc2 (904)"
    assert device.hw_version == "2.6.8 · Android 11"
    assert device.serial_number == "90000000000000002"
    assert device.manufacturer == "Shelly"
    assert device.model == "Wall Display X2i"


async def test_refreshing_never_creates_a_card(hass: HomeAssistant) -> None:
    """Only the entities register the device; a refresh before them is a no-op."""
    async_refresh_panel_device(
        hass, "entry-1", panel_device_info("entry-1", None, "alpha", ("0.9.8", 1))
    )

    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, "entry-1"), "entry-1"
        )
        is None
    )


@pytest.mark.parametrize("reported_area", ["null", " NuLl "])
async def test_literal_null_report_does_not_create_an_area(
    hass: HomeAssistant, reported_area: str
) -> None:
    """A stored panel value of `null` cannot seed a new HA area."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    status = parse_status_response(
        json.dumps(
            {
                "warnings": [],
                "capabilities": [],
                "panel_assistant_device": {"model": "NSPanel", "area": reported_area},
            }
        )
    )
    info = panel_device_info(entry.entry_id, _snapshot(status.panel_assistant_device))

    assert "suggested_area" not in info
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **info
    )
    assert device.area_id is None
    assert not ar.async_get(hass).areas


@pytest.mark.parametrize("area_name", ["null", " NuLl "])
async def test_existing_literal_null_assignment_is_cleared_once(
    hass: HomeAssistant, area_name: str
) -> None:
    """Repair our device assignment while preserving all area records and real moves."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    accidental = areas.async_get_or_create(area_name)
    study = areas.async_get_or_create("Study")
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    registry.async_update_device(device.id, area_id=accidental.id)
    info = panel_device_info(entry.entry_id, _snapshot(PanelDevice(area="null")))

    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id is None
    assert areas.async_get_area(accidental.id) == accidental
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id is None

    registry.async_update_device(device.id, area_id=study.id)
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id == study.id

    registry.async_update_device(device.id, area_id="unavailable_area")
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id == "unavailable_area"


def _loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    return entry


def _status_body(hardware: str | None) -> PanelStatus:
    """Parse a status body as the client does, with or without hardware facts."""
    extra = "" if hardware is None else f',"panel_assistant_hardware":{hardware}'
    return parse_status_response(
        '{"warnings":[],"capabilities":[],"panel_assistant_device":'
        '{"name":"Alpha panel","manufacturer":"Sonoff","model":"NSPanel Pro"}'
        f"{extra}}}"
    )


async def _card_after_setup(
    hass: HomeAssistant, entry: MockConfigEntry, status: PanelStatus
) -> dr.DeviceEntry:
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=replace(HEALTH, version="0.9.0", version_code=1144)),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=status),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    return device


async def test_the_card_shows_the_panels_firmware_android_release_and_serial(
    hass: HomeAssistant,
) -> None:
    """Product and app build lead; firmware, Android release and serial follow.

    A fact the integration does not know yet is discarded, never a reason to
    refuse the panel's whole status.
    """
    entry = _loaded_entry(hass)
    device = await _card_after_setup(
        hass,
        entry,
        _status_body(
            '{"firmware":"1.11.0","android_release":"8.1.0",'
            '"serial_number":"G000000000000000001","bootloader":"a later fact"}'
        ),
    )

    assert device.model == "NSPanel Pro"
    assert device.manufacturer == "Sonoff"
    assert device.sw_version == "0.9.0 (1144)"
    assert device.hw_version == "1.11.0 · Android 8.1.0"
    assert device.serial_number == "G000000000000000001"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    hardware = diagnostics["status"]["panel_assistant_hardware"]
    assert hardware["firmware"] == "1.11.0"
    assert hardware["serial_number"] != "G000000000000000001"


async def test_a_panel_without_hardware_facts_keeps_the_card_it_had(
    hass: HomeAssistant,
) -> None:
    """An app that predates the hardware facts neither blanks nor invents them."""
    entry = _loaded_entry(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        hw_version="1.11.0 · Android 8.1.0",
        serial_number="G000000000000000001",
    )

    device = await _card_after_setup(hass, entry, _status_body(None))

    assert device.model == "NSPanel Pro"
    assert device.hw_version == "1.11.0 · Android 8.1.0"
    assert device.serial_number == "G000000000000000001"


async def test_the_card_follows_the_name_the_panel_reports_but_not_a_user_rename(
    hass: HomeAssistant,
) -> None:
    """A panel renamed on its own page renames its card; an HA rename still wins."""
    entry = MockConfigEntry(domain=DOMAIN, title="jenna_6c08")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Jenna",
    )

    def refresh(snapshot: PanelSnapshot | None) -> None:
        async_refresh_panel_device(
            hass,
            entry.entry_id,
            panel_device_info(entry.entry_id, snapshot, entry.title),
            panel_reported_name(snapshot),
        )

    refresh(_snapshot(PanelDevice(name="Shelly HA Panel")))
    assert registry.async_get(device.id).name == "Shelly HA Panel"

    # Before the panel answers, the fallback never replaces what it reported.
    refresh(None)
    assert registry.async_get(device.id).name == "Shelly HA Panel"

    registry.async_update_device(device.id, name_by_user="Office wall")
    refresh(_snapshot(PanelDevice(name="Renamed on the panel")))
    current = registry.async_get(device.id)
    assert current.name == "Renamed on the panel"
    assert current.name_by_user == "Office wall"


SHELLY_MAC = "00:1a:2b:3c:4d:5e"


def _shelly_wall_display(hass: HomeAssistant) -> dr.DeviceEntry:
    """The Shelly integration's own device for a Shelly Wall Display."""
    shelly = MockConfigEntry(domain="shelly", unique_id="001A2B3C4D5E")
    shelly.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=shelly.entry_id,
        identifiers={("shelly", "001A2B3C4D5E")},
        connections={(dr.CONNECTION_NETWORK_MAC, "001A2B3C4D5E")},
        name="ShellyWallDisplay-001A2B3C4D5E",
        manufacturer="Shelly",
        model="Shelly Wall Display X2i",
    )


async def _linked_devices(hass_ws_client, device_id: str) -> set[str]:
    client = await hass_ws_client()
    await client.send_json_auto_id(
        {"type": "config/device_registry/list_linked_devices", "device_id": device_id}
    )
    response = await client.receive_json()
    assert response["success"], response
    return set(response["result"]["linked_devices"])


async def test_the_card_lists_the_shelly_integrations_device_as_linked(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """The panel's reported Wi-Fi MAC links its card to Shelly's, and back.

    Neither device absorbs the other: Shelly's keeps its own entry, name and
    model, and the panel's card keeps its own.
    """
    assert await async_setup_component(hass, "config", {})
    shelly = _shelly_wall_display(hass)
    entry = _loaded_entry(hass)

    card = await _card_after_setup(
        hass,
        entry,
        _status_body(
            f'{{"serial_number":"0123456789abcdef","network_macs":["{SHELLY_MAC}"]}}'
        ),
    )

    assert card.id != shelly.id
    assert card.connections == {(dr.CONNECTION_NETWORK_MAC, SHELLY_MAC)}
    assert await _linked_devices(hass_ws_client, card.id) == {shelly.id}
    assert await _linked_devices(hass_ws_client, shelly.id) == {card.id}
    shelly_now = dr.async_get(hass).async_get(shelly.id)
    assert shelly_now.config_entry_id == shelly.config_entry_id
    assert shelly_now.name == "ShellyWallDisplay-001A2B3C4D5E"
    assert card.config_entry_id == entry.entry_id
    assert card.name == "Alpha panel"
    # Redacted like the serial: diagnostics are shared in public issues.
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert SHELLY_MAC not in json.dumps(diagnostics)


async def test_a_panel_without_a_reported_mac_links_to_nothing(
    hass: HomeAssistant, hass_ws_client
) -> None:
    """Older apps send no MAC; the card must not link on anything else it knows."""
    assert await async_setup_component(hass, "config", {})
    _shelly_wall_display(hass)
    entry = _loaded_entry(hass)

    card = await _card_after_setup(
        hass, entry, _status_body('{"serial_number":"0123456789abcdef"}')
    )

    assert card.connections == set()
    assert await _linked_devices(hass_ws_client, card.id) == set()


async def test_a_later_report_replaces_the_addresses_and_silence_keeps_them(
    hass: HomeAssistant,
) -> None:
    """A changed adapter stops linking; a status that omits MACs changes nothing."""
    entry = _loaded_entry(hass)
    card = await _card_after_setup(
        hass, entry, _status_body(f'{{"network_macs":["{SHELLY_MAC}"]}}')
    )
    registry = dr.async_get(hass)

    def refresh(hardware: str) -> set[tuple[str, str]]:
        snapshot = PanelSnapshot(
            health=HEALTH, status=_status_body(hardware), status_error=None
        )
        async_refresh_panel_device(
            hass, entry.entry_id, panel_device_info(entry.entry_id, snapshot)
        )
        return registry.async_get(card.id).connections

    assert refresh('{"network_macs":["00:1a:2b:3c:4d:60"]}') == {
        (dr.CONNECTION_NETWORK_MAC, "00:1a:2b:3c:4d:60")
    }
    assert refresh('{"firmware":"1.11.0"}') == {
        (dr.CONNECTION_NETWORK_MAC, "00:1a:2b:3c:4d:60")
    }


@pytest.mark.parametrize(
    ("macs", "kept"),
    [
        ('"00:1a:2b:3c:4d:5e"', []),
        ('["00:1A:2B:3C:4D:5E","001a2b3c4d5e",1,"00:1a:2b:3c:4d:5e"]', [SHELLY_MAC]),
        (
            '["00:00:00:00:00:01","00:00:00:00:00:02","00:00:00:00:00:03",'
            '"00:00:00:00:00:04","00:00:00:00:00:05"]',
            [f"00:00:00:00:00:0{n}" for n in range(1, 5)],
        ),
    ],
)
def test_an_address_outside_the_apps_grammar_costs_only_that_link(
    macs: str, kept: list[str]
) -> None:
    """A later app's different spelling must never refuse the panel's whole status."""
    status = _status_body(f'{{"firmware":"1.11.0","network_macs":{macs}}}')

    assert status.panel_assistant_hardware == {
        "firmware": "1.11.0",
        "network_macs": kept,
    }


async def test_diagnostics_name_the_model_while_the_status_read_fails(
    hass: HomeAssistant,
) -> None:
    """A report from a panel whose status read is failing still says what it is."""
    entry = _loaded_entry(hass)
    await _card_after_setup(hass, entry, _status_body('{"firmware":"1.11.0"}'))
    entry.runtime_data.coordinator.async_set_updated_data(
        _snapshot(None, with_status=False)
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["status"] is None
    assert diagnostics["device"]["manufacturer"] == "Sonoff"
    assert diagnostics["device"]["model"] == "NSPanel Pro"

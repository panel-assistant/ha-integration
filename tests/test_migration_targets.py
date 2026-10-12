"""Panels other integrations already know: offered in the add-panel form and
raised under Discovered with no Panel Assistant entry."""

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import SelectSelector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelHealth,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.migration_targets import VENDOR_SERVICE_TYPES
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)

HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="screen",
    build="1000",
    config_hash="1a2b3c4d",
    protocol=(5, 5),
)


@pytest.fixture
def adb_open() -> Iterator[SimpleNamespace]:
    """Stand in for the LAN: only hosts in the set accept a connection on 5555."""
    hosts: set[str] = set()
    asked: list[str] = []
    real = asyncio.open_connection
    lan = SimpleNamespace(open=hosts, asked=asked, gate=None)

    async def connect(host: str, port: int, *args, **kwargs):
        if port != 5555:
            return await real(host, port, *args, **kwargs)
        asked.append(host)
        if lan.gate is not None:
            # Hold every probe so discoveries overlap, as on a slow network.
            await lan.gate.wait()
        if host not in hosts:
            raise ConnectionRefusedError
        writer = MagicMock()
        writer.wait_closed = AsyncMock()
        return MagicMock(), writer

    with patch("asyncio.open_connection", side_effect=connect):
        yield lan


def _host_entry(hass: HomeAssistant, domain: str, title: str, host: str) -> None:
    MockConfigEntry(domain=domain, title=title, data={CONF_HOST: host}).add_to_hass(
        hass
    )


def _companion(
    hass: HomeAssistant, name: str, ip: str | None, key: str, *, phone: bool = False
) -> None:
    """A Companion app registration, with its Wi-Fi IP sensor when ip is set.

    On a phone the app also registers its telephony sensors, disabled by default.
    """
    entry = MockConfigEntry(domain="mobile_app", title=name, data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", key)}, name=name
    )
    if phone:
        er.async_get(hass).async_get_or_create(
            "sensor",
            "mobile_app",
            f"webhook{key}_phone_state",
            config_entry=entry,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION,
        )
    if ip is None:
        return
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        "mobile_app",
        f"webhook{key}_wifi_ip_address",
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(entity.entity_id, ip)


def _esphome(hass: HomeAssistant, title: str, host: str, model: str) -> None:
    """An ESPHome entry whose device reports the given model."""
    entry = MockConfigEntry(domain="esphome", title=title, data={CONF_HOST: host})
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, f"02:00:00:00:00:{host[-2:]}")},
        model=model,
    )


async def _add_panel_form(hass: HomeAssistant) -> dict:
    menu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": "add_panel"}
    )


def _offered(form: dict) -> list[tuple[str, str]] | None:
    """The (value, label) pairs the address field offers, or None for plain text."""
    selector = form["data_schema"].schema[CONF_ADDRESS]
    if not isinstance(selector, SelectSelector):
        return None
    assert selector.config["custom_value"] is True
    return [(o["value"], o["label"]) for o in selector.config["options"]]


async def test_form_offers_known_panels_adb_open_first(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """ADB-open devices from any source; closed ones only from panel-only apps."""
    _host_entry(hass, "fully_kiosk", "Entry tablet", "192.168.1.10")
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")
    _esphome(hass, "Kiosk", "192.168.1.20", "Android 14")
    _esphome(hass, "Voice screen", "192.168.1.23", "Android 8.1.0")
    _esphome(hass, "Boiler relay", "192.168.1.21", "Athom Plug V3")
    _host_entry(hass, "shelly", "Porch plug", "192.168.1.22")
    _host_entry(hass, "fully_kiosk", "Public", "8.8.8.8")
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    _companion(hass, "Phone", "192.168.1.31", "b", phone=True)
    _companion(hass, "Old phone", None, "c")
    _companion(hass, "Offline phone", "unavailable", "e")
    _companion(hass, "Stairs tablet", "192.168.1.11", "d")
    _host_entry(hass, "fully_kiosk", "Den tablet", "192.168.1.40")
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "192.168.1.40"}).add_to_hass(
        hass
    )
    adb_open.open.update(
        {
            "192.168.1.10",
            "192.168.1.20",
            "192.168.1.21",
            "192.168.1.30",
            "192.168.1.31",
            "8.8.8.8",
        }
    )

    form = await _add_panel_form(hass)

    assert form["step_id"] == "add_panel"
    assert _offered(form) == [
        ("192.168.1.10", "✓ Entry tablet · Fully Kiosk · 192.168.1.10"),
        ("192.168.1.20", "✓ Kiosk · ESPHome · 192.168.1.20"),
        ("192.168.1.30", "✓ Wall screen · Home Assistant Companion · 192.168.1.30"),
        ("192.168.1.11", "Stairs tablet · Fully Kiosk · 192.168.1.11"),
        ("192.168.1.23", "Voice screen · ESPHome · 192.168.1.23"),
    ]
    assert "unavailable" not in adb_open.asked
    # A phone is never offered, even with network ADB on.
    assert "192.168.1.31" not in adb_open.asked
    # ESP32 boards are never probed, even one that would answer.
    assert "192.168.1.21" not in adb_open.asked


async def test_form_is_plain_address_entry_when_nothing_is_known(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Without Companion, Fully Kiosk or another source the form is unchanged."""
    form = await _add_panel_form(hass)

    assert form["step_id"] == "add_panel"
    assert _offered(form) is None


async def test_picking_a_known_panel_adds_it_without_typing_an_address(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The picked value takes the same path a typed address takes."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")
    form = await _add_panel_form(hass)
    [(value, _label)] = _offered(form)

    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        # The flow is under test, not the new entry's first poll.
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: value}
        )
        assert found["step_id"] == "found_panel"
        result = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "connect_found"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.30"}


async def _network_device_seen(hass: HomeAssistant) -> dict:
    """Core's dhcp watcher reporting some device on the LAN, not a panel."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(
            ip="192.168.1.1", hostname="router", macaddress="020000000001"
        ),
    )
    await hass.async_block_till_done()
    return result


def _discovered(hass: HomeAssistant) -> dict[str, dict]:
    """Panels waiting under Discovered, by card name."""
    return {
        flow["context"]["title_placeholders"]["name"]: flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == config_entries.SOURCE_INTEGRATION_DISCOVERY
    }


async def test_known_panels_are_discovered_with_no_entry(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Any device on the network raises the known panels; phones never appear."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    _companion(hass, "Phone", "192.168.1.31", "b", phone=True)
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")
    _host_entry(hass, "shelly", "Porch plug", "192.168.1.22")
    adb_open.open.update({"192.168.1.30", "192.168.1.31"})
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await _network_device_seen(hass)

    assert result["type"] is FlowResultType.ABORT
    discovered = _discovered(hass)
    assert set(discovered) == {"Wall screen", "Stairs tablet"}
    assert {flow["step_id"] for flow in discovered.values()} == {"confirm_migration"}
    assert "192.168.1.31" not in adb_open.asked


async def test_discovered_panel_carries_its_address_into_the_install(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Confirming the card takes the typed-address path with that address."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")
    await _network_device_seen(hass)
    card = _discovered(hass)["Wall screen"]

    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        found = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        assert found["step_id"] == "found_panel"
        result = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "connect_found"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.30"}


async def test_ignored_panel_stays_ignored(
    hass: HomeAssistant, adb_open: SimpleNamespace, freezer
) -> None:
    """Ignore holds across later searches, and the ignored panel is not probed."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")
    await _network_device_seen(hass)
    card = _discovered(hass)["Wall screen"]
    hass.config_entries.flow.async_abort(card["flow_id"])
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": card["context"]["unique_id"], "title": "Wall screen"},
    )
    adb_open.asked.clear()

    freezer.tick(3601)
    await _network_device_seen(hass)

    assert _discovered(hass) == {}
    assert adb_open.asked == []


async def test_added_panel_is_not_offered_again(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A panel Panel Assistant already has is neither offered nor probed."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "192.168.1.30"}).add_to_hass(
        hass
    )

    await _network_device_seen(hass)

    assert _discovered(hass) == {}
    assert adb_open.asked == []


async def test_waiting_panel_is_probed_once_however_often_the_network_wakes(
    hass: HomeAssistant, adb_open: SimpleNamespace, freezer
) -> None:
    """Every device seen at start searches again; the waiting card is kept."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")

    for _ in range(3):
        await _network_device_seen(hass)
    # A renamed device is still the card already waiting, not a second one.
    (device,) = dr.async_get(hass).async_get_devices(identifiers={("mobile_app", "a")})
    dr.async_get(hass).async_update_device(device.id, name_by_user="Hall screen")
    freezer.tick(3601)
    await _network_device_seen(hass)

    assert adb_open.asked == ["192.168.1.30"]
    assert set(_discovered(hass)) == {"Wall screen"}


async def test_a_device_that_is_not_a_panel_is_probed_once_an_hour(
    hass: HomeAssistant, adb_open: SimpleNamespace, freezer
) -> None:
    """Rejected candidates are not reprobed on every wake, and can qualify later."""
    _host_entry(hass, "shelly", "Porch plug", "192.168.1.22")
    _companion(hass, "Wall tablet", "192.168.1.30", "a")

    for _ in range(3):
        await _network_device_seen(hass)

    assert sorted(adb_open.asked) == ["192.168.1.22", "192.168.1.30"]
    assert _discovered(hass) == {}

    # Network ADB turned on later: the next search after an hour offers it.
    adb_open.open.add("192.168.1.30")
    freezer.tick(3601)
    await _network_device_seen(hass)

    assert sorted(adb_open.asked) == [
        "192.168.1.22",
        "192.168.1.22",
        "192.168.1.30",
        "192.168.1.30",
    ]
    assert set(_discovered(hass)) == {"Wall tablet"}


async def test_a_rejected_candidate_at_a_new_address_is_probed_at_once(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A new address is a new chance; the hourly wait applies per address."""
    _companion(hass, "Wall tablet", "192.168.1.30", "a")
    await _network_device_seen(hass)
    [entity] = [
        e
        for e in er.async_get(hass).entities.values()
        if e.unique_id.endswith("_wifi_ip_address")
    ]
    hass.states.async_set(entity.entity_id, "192.168.1.35")
    adb_open.open.add("192.168.1.35")

    await _network_device_seen(hass)

    assert adb_open.asked == ["192.168.1.30", "192.168.1.35"]
    assert set(_discovered(hass)) == {"Wall tablet"}


async def test_known_panels_are_audited_as_panel_assistant_starts(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An install or update restarts Core; the audit runs then, with no dhcp event."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert set(_discovered(hass)) == {"Wall screen"}


async def test_opening_add_integration_rechecks_known_panels_at_once(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Someone at work in Home Assistant does not wait out the hourly recheck."""
    _companion(hass, "Wall tablet", "192.168.1.30", "a")
    await _network_device_seen(hass)
    assert _discovered(hass) == {}
    adb_open.open.add("192.168.1.30")

    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.async_block_till_done()

    assert set(_discovered(hass)) == {"Wall tablet"}


HEALTH_CHECK = (
    "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health"
)


async def _adb_advertised(
    hass: HomeAssistant, host: str, serial: str = "1234567890123", port: int = 5555
) -> dict:
    """Android's adbd announcing network ADB, with no ha-paneld answering there."""
    address = ip_address(host)
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=address,
                ip_addresses=[address],
                port=port,
                hostname="Android.local.",
                type="_adb._tcp.local.",
                name=f"adb-{serial}._adb._tcp.local.",
                properties={},
            ),
        )
        await hass.async_block_till_done()
    return result


def _cards(hass: HomeAssistant) -> dict[str, dict]:
    """Every Panel Assistant discovery waiting for a decision, by card name."""
    return {
        flow["context"]["title_placeholders"]["name"]: flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["step_id"] == "confirm_migration"
    }


async def test_network_adb_device_is_discovered_and_installs_at_its_address(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """No entry and no other integration: network ADB alone raises the card."""
    adb_open.open.add("192.168.1.50")

    await _adb_advertised(hass, "192.168.1.50")

    card = _cards(hass)["Android 1234567890123"]
    with (
        patch(HEALTH_CHECK, AsyncMock(return_value=HEALTH)),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        found = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        assert found["step_id"] == "found_panel"
        result = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "connect_found"}
        )
    assert result["data"] == {CONF_ADDRESS: "192.168.1.50"}


async def test_network_adb_device_another_app_knows_is_one_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The registry card and the ADB advertisement are the same panel."""
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")
    adb_open.open.add("192.168.1.11")
    await _network_device_seen(hass)

    await _adb_advertised(hass, "192.168.1.11")

    assert set(_cards(hass)) == {"Stairs tablet"}


async def test_network_adb_device_already_added_or_running_the_app_is_not_offered(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An added panel, a panel whose app answers, or a non-standard port: no card."""
    adb_open.open.update({"192.168.1.50", "192.168.1.51", "192.168.1.52"})
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "192.168.1.50"}).add_to_hass(
        hass
    )

    await _adb_advertised(hass, "192.168.1.50", serial="added")
    with patch(HEALTH_CHECK, AsyncMock(return_value=HEALTH)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address("192.168.1.51"),
                ip_addresses=[ip_address("192.168.1.51")],
                port=5555,
                hostname="Android.local.",
                type="_adb._tcp.local.",
                name="adb-running._adb._tcp.local.",
                properties={},
            ),
        )
    await _adb_advertised(hass, "192.168.1.52", serial="odd", port=5556)

    assert _cards(hass) == {}
    assert adb_open.asked == []


async def test_ignored_network_adb_device_stays_ignored_at_a_new_address(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Ignore follows the device's ADB serial, not its address."""
    adb_open.open.update({"192.168.1.50", "192.168.1.60"})
    await _adb_advertised(hass, "192.168.1.50")
    card = _cards(hass)["Android 1234567890123"]
    hass.config_entries.flow.async_abort(card["flow_id"])
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": card["context"]["unique_id"], "title": "Android"},
    )

    await _adb_advertised(hass, "192.168.1.60")

    assert _cards(hass) == {}


async def _ignore_card(hass: HomeAssistant, card: dict) -> None:
    """Ignore as the frontend does: the card is still open when Ignore runs."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": card["context"]["unique_id"], "title": "Ignored"},
    )
    await hass.async_block_till_done()


async def test_network_adb_first_then_another_app_learns_the_device_is_one_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The reverse arrival order also leaves a single card."""
    adb_open.open.add("192.168.1.11")
    await _adb_advertised(hass, "192.168.1.11")
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")

    await _network_device_seen(hass)

    assert set(_cards(hass)) == {"Android 1234567890123"}


async def test_device_ignored_by_its_adb_card_is_not_offered_when_an_app_learns_it(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Ignore before Fully Kiosk knows the device still holds afterwards."""
    adb_open.open.add("192.168.1.11")
    await _adb_advertised(hass, "192.168.1.11")
    await _ignore_card(hass, _cards(hass)["Android 1234567890123"])
    adb_open.asked.clear()
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")

    await _network_device_seen(hass)

    assert _cards(hass) == {}
    assert adb_open.asked == []


async def test_device_ignored_by_its_app_card_is_not_offered_when_adb_announces(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Ignore of the registry card holds against the ADB announcement too."""
    adb_open.open.add("192.168.1.11")
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")
    await _network_device_seen(hass)
    await _ignore_card(hass, _cards(hass)["Stairs tablet"])
    # Fully Kiosk now names the tablet by hostname, so only the announcement
    # carries its address, under an identity of its own.
    [entry] = hass.config_entries.async_entries("fully_kiosk")
    hass.config_entries.async_update_entry(entry, data={CONF_HOST: "stairs.lan"})

    await _adb_advertised(hass, "192.168.1.11")

    assert _cards(hass) == {}


async def test_network_adb_card_uses_the_name_another_app_gives_the_device(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An announcement arriving before any search still shows the panel's name."""
    _host_entry(hass, "fully_kiosk", "Stairs tablet", "192.168.1.11")
    adb_open.open.add("192.168.1.11")

    await _adb_advertised(hass, "192.168.1.11")

    assert set(_cards(hass)) == {"Stairs tablet"}


async def _probes_reach(adb_open: SimpleNamespace, count: int) -> None:
    for _ in range(500):
        if adb_open.asked.count("192.168.1.11") == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"probes: {adb_open.asked}")


async def _overlapping(
    hass: HomeAssistant, adb_open: SimpleNamespace, fully_kiosk_host: str
) -> None:
    """The announcement and a registry search both probing the device at once."""
    adb_open.gate = asyncio.Event()
    announced = asyncio.create_task(_adb_advertised(hass, "192.168.1.11"))
    await _probes_reach(adb_open, 1)
    _host_entry(hass, "fully_kiosk", "Stairs tablet", fully_kiosk_host)
    woke = asyncio.create_task(_network_device_seen(hass))
    await _probes_reach(adb_open, 2)
    adb_open.gate.set()
    await announced
    await woke
    await hass.async_block_till_done()


async def test_overlapping_discoveries_of_one_device_leave_one_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Whichever probe answers first shows the card; the other stands down."""
    adb_open.open.add("192.168.1.11")

    await _overlapping(hass, adb_open, "192.168.1.11")

    assert len(_cards(hass)) == 1


async def test_overlapping_discovery_whose_probe_fails_leaves_the_other_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A stale announcement that fails its probe does not hide the panel."""
    await _overlapping(hass, adb_open, "192.168.1.11")

    assert set(_cards(hass)) == {"Stairs tablet"}


def _shelly_wall_display(host: str) -> ZeroconfServiceInfo:
    """The bench X2i's Stargate announcement, as browsed on the LAN (2026-10-11)."""
    address = ip_address(host)
    return ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=80,
        hostname="ShellyWallDisplay-00A90B9D900A.local.",
        type="_shelly._tcp.local.",
        name="ShellyWallDisplay-00A90B9D900A._shelly._tcp.local.",
        properties={"app": "WallDisplay", "gen": "2", "ver": "2.5.4"},
    )


async def test_vendor_announced_panel_without_the_app_is_offered_with_adb_closed(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Shelly's own announcement raises a card even where adbd announces nothing."""
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_shelly_wall_display("192.168.1.70"),
        )
        await hass.async_block_till_done()

    card = _cards(hass)["ShellyWallDisplay-00A90B9D900A"]
    assert card["context"]["title_placeholders"]["address"] == "192.168.1.70"
    assert card["context"]["unique_id"] == "mdns:shellywalldisplay-00a90b9d900a"


async def test_vendor_announcement_proves_a_panel_the_companion_app_already_knows(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Companion's card is kept, and Shelly's name makes it a panel with ADB closed."""
    _companion(hass, "Wall screen", "192.168.1.70", "a")
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_shelly_wall_display("192.168.1.70"),
        )
        await hass.async_block_till_done()

    assert set(_cards(hass)) == {"Wall screen"}


async def test_vendor_announcement_of_an_added_panel_raises_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An added panel's address is never offered for installation again."""
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "192.168.1.70"}).add_to_hass(
        hass
    )
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_shelly_wall_display("192.168.1.70"),
        )
        await hass.async_block_till_done()

    assert _cards(hass) == {}


def test_manifest_browses_each_vendor_service_by_its_profile_name() -> None:
    """Home Assistant starts the vendor flow only for a matcher in the manifest."""
    manifest = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components/panel_assistant/manifest.json"
        ).read_text(encoding="utf-8")
    )
    matchers = [m for m in manifest["zeroconf"] if isinstance(m, dict)]
    assert {m["type"] for m in matchers} == set(VENDOR_SERVICE_TYPES)
    # Lower-cased, as Core compares the instance name; the app profile reads
    # platform.vendor_mdns: ShellyWallDisplay-*._shelly._tcp.
    assert {"type": "_shelly._tcp.local.", "name": "shellywalldisplay-*"} in matchers


# One card per panel: the bench X2i was offered twice under Discovered, by the
# Shelly registry card and by the app's own announcement (maintainer, 2026-10-11).
PANEL = "192.168.1.70"
APP_HEALTH = replace(HEALTH, panel_id="shelly_ha_panel", discovery_id="b" * 64)


async def _app_announced(hass: HomeAssistant, host: str = PANEL) -> None:
    """The app's own announcement, its health answering as the panel there."""
    address = ip_address(host)
    with patch(HEALTH_CHECK, AsyncMock(return_value=APP_HEALTH)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=address,
                ip_addresses=[address],
                port=8888,
                hostname="shelly.local.",
                type="_ha-paneld._tcp.local.",
                name="shelly._ha-paneld._tcp.local.",
                properties={"did": APP_HEALTH.discovery_id, "name": "Shelly HA Panel"},
            ),
        )
        await hass.async_block_till_done()


async def _by_vendor(hass: HomeAssistant, adb_open: SimpleNamespace) -> None:
    """Stargate's announcement while the app is not answering."""
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_shelly_wall_display(PANEL),
        )
        await hass.async_block_till_done()


async def _by_adb(hass: HomeAssistant, adb_open: SimpleNamespace) -> None:
    """Android's network ADB announcement while the app is not answering."""
    adb_open.open.add(PANEL)
    await _adb_advertised(hass, PANEL)


async def _by_registry(hass: HomeAssistant, adb_open: SimpleNamespace) -> None:
    """The Shelly integration knowing the panel, raised by any dhcp event."""
    if not hass.config_entries.async_entries("shelly"):
        _host_entry(hass, "shelly", "ShellyWallDisplay-00A90B9D900A", PANEL)
    adb_open.open.add(PANEL)
    await _network_device_seen(hass)


INSTALL_CHANNELS = pytest.mark.parametrize(
    "offer", [_by_vendor, _by_adb, _by_registry], ids=["vendor", "adb", "registry"]
)


def _offers(hass: HomeAssistant) -> list[dict]:
    """Every Panel Assistant card under Discovered, whatever step it shows."""
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] != config_entries.SOURCE_USER
    ]


@INSTALL_CHANNELS
async def test_app_announcing_a_panel_offered_for_installation_replaces_its_card(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """The app's verified announcement is the best channel and takes over."""
    await offer(hass, adb_open)
    assert len(_offers(hass)) == 1

    await _app_announced(hass)

    [card] = _offers(hass)
    assert card["context"]["unique_id"] == APP_HEALTH.discovery_id


@INSTALL_CHANNELS
async def test_install_announcement_of_a_panel_the_app_offers_adds_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """A weaker channel arriving later, the app briefly silent, stands down."""
    await _app_announced(hass)

    await offer(hass, adb_open)

    [card] = _offers(hass)
    assert card["context"]["unique_id"] == APP_HEALTH.discovery_id


async def _added_by_hand(hass: HomeAssistant) -> None:
    """Someone typing the panel's address under Add panel, its app answering."""
    form = await _add_panel_form(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(return_value=APP_HEALTH)),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: PANEL}
        )
        result = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "connect_found"}
        )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@INSTALL_CHANNELS
async def test_adding_an_offered_panel_by_hand_closes_its_card(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """A card never stays beside the entry for its panel."""
    await offer(hass, adb_open)

    await _added_by_hand(hass)

    assert _offers(hass) == []


@INSTALL_CHANNELS
async def test_adding_a_panel_by_hand_closes_a_card_pressed_and_left(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """A press that came back to a form, its dialog shut, does not stay listed."""
    await offer(hass, adb_open)
    [card] = _offers(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(side_effect=RuntimeError),
        ),
    ):
        form = await hass.config_entries.flow.async_configure(card["flow_id"], {})
    assert (form["flow_id"], form["step_id"]) == (card["flow_id"], "add_panel")

    await _added_by_hand(hass)

    assert _offers(hass) == []


@INSTALL_CHANNELS
async def test_press_still_running_when_the_panel_is_added_says_so(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """The person's press ends as already added, never as an unknown flow."""
    await offer(hass, adb_open)
    [card] = _offers(hass)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pin(*_args):
        entered.set()
        await release.wait()
        raise RuntimeError

    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            side_effect=pin,
        ),
    ):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"], {})
        )
        await entered.wait()
    await _added_by_hand(hass)
    release.set()

    result = await pressed

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert _offers(hass) == []


@INSTALL_CHANNELS
async def test_moved_panel_announcing_where_it_was_offered_closes_that_card(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """An added panel's new address is its own; the install card there goes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=APP_HEALTH.discovery_id,
        # Only an identity the installation protocol set follows an announcement.
        data={CONF_ADDRESS: "192.168.1.99", "installation_identity": True},
    )
    entry.add_to_hass(hass)
    await offer(hass, adb_open)

    await _app_announced(hass)

    assert entry.data[CONF_ADDRESS] == PANEL
    assert _offers(hass) == []


@INSTALL_CHANNELS
async def test_ignoring_the_install_card_covers_the_app_announcement(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """An Ignore on one card covers the panel, whichever channel shows next."""
    await offer(hass, adb_open)
    [card] = _offers(hass)
    await _ignore_card(hass, card)

    await _app_announced(hass)

    assert _offers(hass) == []


@INSTALL_CHANNELS
async def test_ignoring_the_app_card_covers_every_install_announcement(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """The reverse: an Ignore on the app's card holds against install channels."""
    await _app_announced(hass)
    [card] = _offers(hass)
    await _ignore_card(hass, card)

    await offer(hass, adb_open)

    assert _offers(hass) == []


async def test_app_card_retitled_for_a_name_clash_still_covers_its_panel(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A second panel announcing the same name qualifies both titles, nothing else."""
    await _app_announced(hass)
    other = ip_address("192.168.1.71")
    with patch(
        HEALTH_CHECK,
        AsyncMock(
            return_value=replace(APP_HEALTH, panel_id="other", discovery_id="c" * 64)
        ),
    ):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=other,
                ip_addresses=[other],
                port=8888,
                hostname="other.local.",
                type="_ha-paneld._tcp.local.",
                name="other._ha-paneld._tcp.local.",
                properties={"did": "c" * 64, "name": "Shelly HA Panel"},
            ),
        )
        await hass.async_block_till_done()

    await _by_vendor(hass, adb_open)

    assert sorted(
        flow["context"]["title_placeholders"]["name"] for flow in _offers(hass)
    ) == ["Shelly HA Panel (other)", "Shelly HA Panel (shelly_ha_panel)"]


@INSTALL_CHANNELS
async def test_install_started_from_a_card_survives_the_app_it_installs(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """Only a card still waiting is closed; a person who pressed it keeps it.

    The app announces twice: while the press is still being handled, when Core
    still reports the card's step, and again once the install form is back.
    """
    await offer(hass, adb_open)
    [card] = _offers(hass)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pin(*_args):
        entered.set()
        await release.wait()
        raise RuntimeError

    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            side_effect=pin,
        ),
    ):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"], {})
        )
        await entered.wait()
        await _app_announced(hass)
        assert [flow["flow_id"] for flow in _offers(hass)] == [card["flow_id"]]
        release.set()
        form = await pressed

    # Held on the install form inside the card's own flow.
    assert form["flow_id"] == card["flow_id"]
    assert form["step_id"] == "add_panel"
    await _app_announced(hass)
    assert [flow["flow_id"] for flow in _offers(hass)] == [card["flow_id"]]


# The registry card's probing flow does not outlive opening the add form here,
# so only the two announcements reproduce the race; all three share the check.
@pytest.mark.parametrize("offer", [_by_vendor, _by_adb], ids=["vendor", "adb"])
async def test_card_whose_probe_outlasts_a_manual_add_is_not_shown(
    hass: HomeAssistant, adb_open: SimpleNamespace, offer
) -> None:
    """A card still probing when the panel is added by hand stands down after."""
    adb_open.gate = asyncio.Event()
    offered = asyncio.create_task(offer(hass, adb_open))
    for _ in range(500):
        if PANEL in adb_open.asked:
            break
        await asyncio.sleep(0.01)
    assert PANEL in adb_open.asked
    form = await _add_panel_form(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(return_value=APP_HEALTH)),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: PANEL}
        )
        result = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "connect_found"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    adb_open.gate.set()
    await offered
    await hass.async_block_till_done()

    assert _offers(hass) == []


async def test_pressed_app_card_survives_a_reinstalled_app_announcing_there(
    hass: HomeAssistant,
) -> None:
    """The person's press ends in the card's own answer, not a vanished flow."""
    await _app_announced(hass)
    [card] = _offers(hass)
    entered = asyncio.Event()
    release = asyncio.Event()
    reinstalled = replace(APP_HEALTH, discovery_id="c" * 64)

    async def recheck(*_args):
        entered.set()
        await release.wait()
        return reinstalled

    with patch(HEALTH_CHECK, side_effect=recheck):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"], {})
        )
        await entered.wait()
    address = ip_address(PANEL)
    with patch(HEALTH_CHECK, AsyncMock(return_value=reinstalled)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=address,
                ip_addresses=[address],
                port=8888,
                hostname="shelly.local.",
                type="_ha-paneld._tcp.local.",
                name="shelly._ha-paneld._tcp.local.",
                properties={"did": "c" * 64, "name": "Shelly HA Panel"},
            ),
        )
        await hass.async_block_till_done()
    assert [flow["flow_id"] for flow in _offers(hass)] == [card["flow_id"]]
    release.set()

    result = await pressed

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"


async def test_press_finding_the_app_after_the_panel_was_added_says_so(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A press whose health answer lands after the add ends as already added."""
    await _by_vendor(hass, adb_open)
    [card] = _offers(hass)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def health(*_args):
        entered.set()
        await release.wait()
        return APP_HEALTH

    with patch(HEALTH_CHECK, side_effect=health):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"], {})
        )
        await entered.wait()
    await _added_by_hand(hass)
    release.set()

    result = await pressed

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_wizard_opened_from_a_card_when_the_panel_is_added_says_so(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Any step a pressed card is running ends as already added, not vanished."""
    await _by_vendor(hass, adb_open)
    [card] = _offers(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(return_value=APP_HEALTH)),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_setup_complete",
            AsyncMock(return_value=False),
        ),
    ):
        menu = await hass.config_entries.flow.async_configure(card["flow_id"], {})
    assert (menu["flow_id"], menu["step_id"]) == (card["flow_id"], "found_unconfigured")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def offer_url(*_args):
        entered.set()
        await release.wait()

    with patch(
        "custom_components.panel_assistant.config_flow.async_offer_ha_url",
        side_effect=offer_url,
    ):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(
                card["flow_id"], {"next_step_id": "panel_setup"}
            )
        )
        await entered.wait()
        await _added_by_hand(hass)
        release.set()
        result = await pressed

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert _offers(hass) == []


async def test_press_moving_on_to_another_step_when_the_panel_is_added_says_so(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A press still probing, which then opens the next step, ends as added."""
    await _by_vendor(hass, adb_open)
    [card] = _offers(hass)
    address = normalize_address(PANEL)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def probe(*_args):
        entered.set()
        await release.wait()
        return InstallTargetProbe(state=InstallTargetState("adb_unauthorized"))

    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(return_value=PinnedPanelTarget(original=address, pinned=address)),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            side_effect=probe,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_list_install_choices",
            AsyncMock(return_value=[{"tag": "v1.0.0", "prerelease": False}]),
        ),
    ):
        pressed = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"], {})
        )
        await entered.wait()
        await _added_by_hand(hass)
        release.set()
        result = await pressed

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert _offers(hass) == []


async def test_overlapping_runs_of_a_pressed_card_both_end_when_the_panel_is_added(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Its dialog opened twice: the run still waiting ends as added, not vanished."""
    await _by_vendor(hass, adb_open)
    [card] = _offers(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(side_effect=RuntimeError),
        ),
    ):
        form = await hass.config_entries.flow.async_configure(card["flow_id"], {})
    assert form["step_id"] == "add_panel"
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    runs: list[int] = []

    async def find(*_args):
        run = len(runs)
        runs.append(run)
        if run < 2:  # the hand add's own form asks too
            entered[run].set()
            await release[run].wait()
        return []

    with patch(
        "custom_components.panel_assistant.config_flow.async_find_migration_targets",
        side_effect=find,
    ):
        # Opening the dialog shows its step again, so two runs overlap.
        first = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"])
        )
        await entered[0].wait()
        second = asyncio.create_task(
            hass.config_entries.flow.async_configure(card["flow_id"])
        )
        await entered[1].wait()
        release[0].set()
        assert (await first)["step_id"] == "add_panel"
        await _added_by_hand(hass)
        release[1].set()
        result = await second

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert _offers(hass) == []


async def test_card_retargeted_to_another_address_follows_that_address(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A card whose form now names another panel answers for that one only."""
    await _by_vendor(hass, adb_open)
    [card] = _offers(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(side_effect=RuntimeError),
        ),
    ):
        await hass.config_entries.flow.async_configure(card["flow_id"], {})
        form = await hass.config_entries.flow.async_configure(
            card["flow_id"], {CONF_ADDRESS: "192.168.1.71"}
        )
    assert (form["flow_id"], form["step_id"]) == (card["flow_id"], "add_panel")

    await _added_by_hand(hass)

    # Adding the panel the card first named leaves the card for the other one.
    assert [flow["flow_id"] for flow in _offers(hass)] == [card["flow_id"]]

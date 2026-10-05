"""Panels other integrations already know: offered in the add-panel form and
raised under Discovered with no Panel Assistant entry."""

import asyncio
from collections.abc import Iterator
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
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import DOMAIN

HEALTH = PanelHealth(
    version="0.9.0", panel_id="screen", build="1000", config_hash="1a2b3c4d"
)


@pytest.fixture
def adb_open() -> Iterator[SimpleNamespace]:
    """Stand in for the LAN: only hosts in the set accept a connection on 5555."""
    hosts: set[str] = set()
    asked: list[str] = []
    real = asyncio.open_connection

    async def connect(host: str, port: int, *args, **kwargs):
        if port != 5555:
            return await real(host, port, *args, **kwargs)
        asked.append(host)
        if host not in hosts:
            raise ConnectionRefusedError
        writer = MagicMock()
        writer.wait_closed = AsyncMock()
        return MagicMock(), writer

    with patch("asyncio.open_connection", side_effect=connect):
        yield SimpleNamespace(open=hosts, asked=asked)


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
    hass: HomeAssistant, adb_open: SimpleNamespace
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
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Every device seen at start searches again; the waiting card is kept."""
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    adb_open.open.add("192.168.1.30")

    for _ in range(3):
        await _network_device_seen(hass)
    # A renamed device is still the card already waiting, not a second one.
    device = dr.async_get(hass).async_get_device(identifiers={("mobile_app", "a")})
    assert device is not None
    dr.async_get(hass).async_update_device(device.id, name_by_user="Hall screen")
    await _network_device_seen(hass)

    assert adb_open.asked == ["192.168.1.30"]
    assert set(_discovered(hass)) == {"Wall screen"}

"""Panels other integrations already know: offered in the add-panel form and
raised under Discovered with no Panel Assistant entry."""

import asyncio
from collections.abc import Iterator
from ipaddress import ip_address
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

from custom_components.panel_assistant.client import CannotConnectError, PanelHealth
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

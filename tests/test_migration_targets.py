"""The add-panel form offers panels other integrations already know."""

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


def _companion(hass: HomeAssistant, name: str, ip: str | None, key: str) -> None:
    """A Companion app registration, with its Wi-Fi IP sensor when ip is set."""
    entry = MockConfigEntry(domain="mobile_app", title=name, data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", key)}, name=name
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
    _host_entry(hass, "esphome", "Kiosk", "192.168.1.20")
    _host_entry(hass, "esphome", "Boiler relay", "192.168.1.21")
    _host_entry(hass, "shelly", "Porch plug", "192.168.1.22")
    _host_entry(hass, "fully_kiosk", "Public", "8.8.8.8")
    _companion(hass, "Wall screen", "192.168.1.30", "a")
    _companion(hass, "Phone", "192.168.1.31", "b")
    _companion(hass, "Old phone", None, "c")
    _companion(hass, "Offline phone", "unavailable", "e")
    _companion(hass, "Stairs tablet", "192.168.1.11", "d")
    _host_entry(hass, "fully_kiosk", "Den tablet", "192.168.1.40")
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "192.168.1.40"}).add_to_hass(
        hass
    )
    adb_open.open.update({"192.168.1.10", "192.168.1.20", "192.168.1.30", "8.8.8.8"})

    form = await _add_panel_form(hass)

    assert form["step_id"] == "add_panel"
    assert _offered(form) == [
        ("192.168.1.10", "✓ Entry tablet · Fully Kiosk · 192.168.1.10"),
        ("192.168.1.20", "✓ Kiosk · ESPHome · 192.168.1.20"),
        ("192.168.1.30", "✓ Wall screen · Home Assistant Companion · 192.168.1.30"),
        ("192.168.1.11", "Stairs tablet · Fully Kiosk · 192.168.1.11"),
    ]
    assert "unavailable" not in adb_open.asked


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

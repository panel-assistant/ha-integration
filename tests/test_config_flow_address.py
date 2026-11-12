"""Manual panel addresses survive normalization through entry creation."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.panel_assistant.client import PanelAddress
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import PinnedPanelTarget

from . import config_flow_helpers
from .config_flow_helpers import _connect_found, _start_step

install_network_pin = config_flow_helpers.install_network_pin


@pytest.mark.parametrize(
    ("typed", "stored", "port"),
    [
        ("fd77:2::20", "[fd77:2::20]", 8888),
        ("  FD77:0002:0:0:0:0:0:20  ", "[fd77:2::20]", 8888),
        ("fd77:2::20:9999", "[fd77:2::20:9999]", 8888),
        (" PANEL.local:9999 ", "panel.local:9999", 9999),
        ("192.168.1.20:9999", "192.168.1.20:9999", 9999),
        ("[fd77:2::20]:9999", "[fd77:2::20]:9999", 9999),
        ("[fd77:2::20]:8888", "[fd77:2::20]", 8888),
    ],
    ids=[
        "bare-ipv6",
        "expanded-ipv6",
        "ipv6-ending-in-digits",
        "hostname-port",
        "ipv4-port",
        "ipv6-port",
        "ipv6-default-port",
    ],
)
async def test_add_panel_stores_address_and_connects_to_selected_port(
    hass: HomeAssistant,
    aioclient_mock,
    monkeypatch: pytest.MonkeyPatch,
    install_network_pin,
    typed: str,
    stored: str,
    port: int,
) -> None:
    """Bare IPv6 uses the default port; explicit host:port keeps its port."""
    monkeypatch.setattr(AiohttpClientMockResponse, "connection", None, raising=False)
    # DNS gets a fixed peer; IP literals retain the address the owner typed.
    install_network_pin.pin.side_effect = lambda _hass, address: PinnedPanelTarget(
        original=address,
        pinned=PanelAddress(
            host="192.168.1.23" if address.host == "panel.local" else address.host,
            port=address.port,
        ),
    )
    endpoint = "192.168.1.23:9999" if stored == "panel.local:9999" else stored
    if port == 8888:
        endpoint = f"{endpoint}:8888"
    health_url = f"http://{endpoint}/api/v1/health"
    aioclient_mock.get(
        health_url,
        text=(
            "ha-paneld 0.9.0 panel=alpha build=1000 cfg=1a2b3c4d "
            "pkg=io.panelassistant.android proto=5-5\n"
        ),
    )
    with patch(
        "custom_components.panel_assistant.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: typed}
        )
        assert found["type"] is FlowResultType.MENU
        assert found["step_id"] == "found_panel"
        assert found["description_placeholders"]["address"] == stored
        assert not hass.config_entries.async_entries(DOMAIN)
        result = await _connect_found(hass, found)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: stored}
    assert [
        entry.data[CONF_ADDRESS] for entry in hass.config_entries.async_entries(DOMAIN)
    ] == [stored]
    assert [(method, str(url)) for method, url, *_ in aioclient_mock.mock_calls] == [
        ("GET", health_url),
        ("GET", health_url),
    ]

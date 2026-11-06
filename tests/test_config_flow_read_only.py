"""Existing-panel connectivity keeps installer mutation restrictions separate."""

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientConnectionError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_jobs import async_get_install_job_manager

from .config_flow_helpers import _connect_found, _start_step

NON_LAN_HOSTS = ("100.100.1.2", "[2001:4860::123]")
HEALTH = (
    "ha-paneld 0.9.0 panel=alpha build=1000 cfg=1a2b3c4d "
    "pkg=io.panelassistant.android proto=5-5\n"
)


@pytest.fixture
async def no_installation(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch):
    """Observe mutation boundaries while leaving pinning and receipts real."""
    # The HTTP fixture has no socket; match aiohttp's released-response shape.
    monkeypatch.setattr(AiohttpClientMockResponse, "connection", None, raising=False)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            new_callable=AsyncMock,
        ) as adb,
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            new_callable=AsyncMock,
        ) as authorize,
    ):
        yield
    adb.assert_not_awaited()
    authorize.assert_not_awaited()
    manager = await async_get_install_job_manager(hass)
    assert await manager.async_list() == ()


@pytest.mark.parametrize("host", NON_LAN_HOSTS, ids=["tailnet", "routed_ipv6"])
async def test_healthy_non_lan_custom_endpoint_connects_after_confirmation(
    hass: HomeAssistant, aioclient_mock, no_installation, host: str
) -> None:
    """An existing tailnet or IPv6 panel needs no installer permission."""
    address = f"{host}:9999"
    health_url = f"http://{address}/api/v1/health"
    aioclient_mock.get(health_url, text=HEALTH)
    with patch(
        "custom_components.panel_assistant.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: address}
        )

        assert found["type"] is FlowResultType.MENU
        assert found["step_id"] == "found_panel"
        assert found["description_placeholders"]["address"] == address
        assert not hass.config_entries.async_entries(DOMAIN)
        result = await _connect_found(hass, found)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: address}
    assert result["title"] == "alpha"
    assert [
        entry.data[CONF_ADDRESS] for entry in hass.config_entries.async_entries(DOMAIN)
    ] == [address]
    assert [(method, str(url)) for method, url, *_ in aioclient_mock.mock_calls] == [
        ("GET", health_url),
        ("GET", health_url),
    ]


@pytest.mark.parametrize("host", NON_LAN_HOSTS, ids=["tailnet", "routed_ipv6"])
@pytest.mark.parametrize("unreachable", [False, True], ids=["invalid", "unreachable"])
async def test_unhealthy_non_lan_custom_endpoint_cannot_offer_installation(
    hass: HomeAssistant, aioclient_mock, no_installation, host: str, unreachable: bool
) -> None:
    """A failed read-only check cannot grant installation or authorization."""
    address = f"{host}:9999"
    health_url = f"http://{address}/api/v1/health"
    if unreachable:
        aioclient_mock.get(health_url, exc=ClientConnectionError)
    else:
        aioclient_mock.get(health_url, text="not panel health")
    form = await _start_step(hass, "add_panel")
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ADDRESS: address}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "invalid_install_address"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert [(method, str(url)) for method, url, *_ in aioclient_mock.mock_calls] == [
        ("GET", health_url)
    ]


@pytest.mark.parametrize("host", NON_LAN_HOSTS, ids=["tailnet", "routed_ipv6"])
async def test_default_non_lan_endpoint_retains_install_refusal_before_http(
    hass: HomeAssistant, aioclient_mock, no_installation, host: str
) -> None:
    """Host-only first contact still obeys the real installer LAN restriction."""
    aioclient_mock.get(f"http://{host}:8888/api/v1/health", text=HEALTH)
    form = await _start_step(hass, "add_panel")
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ADDRESS: host}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "unsafe_install_target"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert aioclient_mock.mock_calls == []

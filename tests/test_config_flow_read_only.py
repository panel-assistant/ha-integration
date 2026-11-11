"""Existing-panel connectivity keeps installer mutation restrictions separate."""

import logging
import socket
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientConnectionError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
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


# The three forms an owner types for a running panel the installer may not
# touch: a carrier-grade NAT address (the range Tailscale uses), the same with
# the panel's own port typed, and a global IPv6 address (segmented-network
# proof, cells c15).
OUTSIDE_INSTALL_RANGE = (
    ("100.127.77.20", "100.127.77.20:8888"),
    ("100.127.77.20:8888", "100.127.77.20:8888"),
    ("[2001:db8:77::20]", "[2001:db8:77::20]:8888"),
)
OUTSIDE_IDS = ["cgnat", "cgnat_port_8888", "global_ipv6"]


@pytest.mark.parametrize(("typed", "endpoint"), OUTSIDE_INSTALL_RANGE, ids=OUTSIDE_IDS)
async def test_running_panel_outside_install_range_is_added_without_adb(
    hass: HomeAssistant, aioclient_mock, no_installation, typed: str, endpoint: str
) -> None:
    """A panel answering on 8888 is added wherever it is; nothing probes ADB."""
    health_url = f"http://{endpoint}/api/v1/health"
    aioclient_mock.get(health_url, text=HEALTH)
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
        result = await _connect_found(hass, found)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "alpha"
    assert [
        entry.data[CONF_ADDRESS] for entry in hass.config_entries.async_entries(DOMAIN)
    ] == [endpoint.removesuffix(":8888")]
    assert [(method, str(url)) for method, url, *_ in aioclient_mock.mock_calls] == [
        ("GET", health_url),
        ("GET", health_url),
    ]


async def test_running_panel_named_by_a_tailnet_name_is_added_without_adb(
    hass: HomeAssistant, aioclient_mock, no_installation
) -> None:
    """A name resolving outside the install range is read under that name."""
    health_url = "http://panel.tailnet.example:8888/api/v1/health"
    aioclient_mock.get(health_url, text=HEALTH)
    resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.127.77.20", 8888))]
    with (
        patch(
            "custom_components.panel_assistant.install_network.socket.getaddrinfo",
            return_value=resolved,
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.tailnet.example"}
        )
        assert found["step_id"] == "found_panel"
        result = await _connect_found(hass, found)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [
        entry.data[CONF_ADDRESS] for entry in hass.config_entries.async_entries(DOMAIN)
    ] == ["panel.tailnet.example"]


@pytest.mark.parametrize(("typed", "endpoint"), OUTSIDE_INSTALL_RANGE, ids=OUTSIDE_IDS)
@pytest.mark.parametrize("unreachable", [True, False], ids=["silent", "not_a_panel"])
async def test_silent_address_outside_install_range_names_the_install_rule(
    hass: HomeAssistant,
    aioclient_mock,
    no_installation,
    caplog: pytest.LogCaptureFixture,
    typed: str,
    endpoint: str,
    unreachable: bool,
) -> None:
    """Nothing on 8888: the owner is told why no install is offered there."""
    health_url = f"http://{endpoint}/api/v1/health"
    if unreachable:
        aioclient_mock.get(health_url, exc=ClientConnectionError)
    else:
        aioclient_mock.get(health_url, text="not panel health")
    form = await _start_step(hass, "add_panel")
    with caplog.at_level(logging.WARNING):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: typed}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "not_answering_outside_install_range"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert [(method, str(url)) for method, url, *_ in aioclient_mock.mock_calls] == [
        ("GET", health_url)
    ]
    refusals = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "Panel Assistant setup refused" in record.getMessage()
    ]
    assert refusals == [
        "Panel Assistant setup refused: step=add_panel "
        "error=not_answering_outside_install_range "
        f"panel={endpoint.removesuffix(':8888')} "
        "cause=unsafe_target; no panel answered on port 8888"
    ]


async def test_configured_panel_outside_install_range_is_never_reinstalled(
    hass: HomeAssistant, aioclient_mock, no_installation
) -> None:
    """Its entry exists but the panel is silent: still no ADB, only the rule."""
    MockConfigEntry(
        domain=DOMAIN, data={CONF_ADDRESS: "100.127.77.20"}, unique_id="a" * 64
    ).add_to_hass(hass)
    health_url = "http://100.127.77.20:8888/api/v1/health"
    aioclient_mock.get(health_url, exc=ClientConnectionError)
    form = await _start_step(hass, "add_panel")
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ADDRESS: "100.127.77.20"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_answering_outside_install_range"}

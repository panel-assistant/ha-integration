"""Adding a panel clears the install-failure Repair of that same panel.

An install that ended without proof (the app installed, but port 8888 not
reached) raises a Repair asking for verification. When the owner then adds the
panel through "found panel", its app has answered: the Repair is no longer
true. The panel is matched by the ADB serial its app reports, never by address.
"""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    panel_failure_issue_id,
    record_install_failure,
)

from . import config_flow_helpers
from .config_flow_helpers import _connect_found, _start_step
from .test_install_jobs import durable_receipt, target

install_network_pin = config_flow_helpers.install_network_pin

SERIAL = "SERIAL-1"
HEALTH = (
    "ha-paneld 0.9.0 panel=alpha build=1000 cfg=1a2b3c4d "
    "pkg=io.panelassistant.android proto=5-5"
)


async def _failed_install(
    hass: HomeAssistant,
    serial: str = SERIAL,
    address: str = "192.168.1.23",
    pinned: str = "192.168.1.23",
) -> str:
    """Record an install failure for the panel with ``serial``; its Repair id."""
    receipt = durable_receipt()
    failed = target(address=address, serial=serial, pinned_address=pinned)
    record_install_failure(hass, replace(receipt, target=failed))
    await hass.async_block_till_done()
    issue_id = panel_failure_issue_id(f"install:{serial}")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    return issue_id


async def _add_found_panel(
    hass: HomeAssistant, aioclient_mock, typed: str, health_line: str
) -> dict:
    # The test pin sends every typed address to this one panel address.
    host = "192.168.1.23:9999" if typed.endswith(":9999") else "192.168.1.23:8888"
    aioclient_mock.get(f"http://{host}/api/v1/health", text=health_line + "\n")
    with patch(
        "custom_components.panel_assistant.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: typed}
        )
        assert found["step_id"] == "found_panel"
        result = await _connect_found(hass, found)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result


@pytest.fixture(autouse=True)
def _no_connection_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AiohttpClientMockResponse, "connection", None, raising=False)


@pytest.mark.parametrize(
    ("address", "pinned"),
    [
        ("192.168.1.23", "192.168.1.23"),
        ("[fd77:2::20]", "[fd77:2::20]"),
        ("panel.local", "192.168.1.40"),
    ],
    ids=["same-address", "installed-over-ipv6", "installed-at-another-address"],
)
async def test_adding_the_panel_clears_its_install_failure_repair(
    hass: HomeAssistant, aioclient_mock, install_network_pin, address, pinned
) -> None:
    """The panel whose install failed answers as itself and is added.

    Wherever the install reached it, the app's serial names the same panel.
    """
    issue_id = await _failed_install(hass, address=address, pinned=pinned)

    await _add_found_panel(
        hass, aioclient_mock, "192.168.1.23", f"{HEALTH} adb={SERIAL}"
    )

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize(
    ("typed", "health_line"),
    [
        ("192.168.1.23", f"{HEALTH} adb=SERIAL-2"),
        ("192.168.1.23", HEALTH),
        ("192.168.1.23:9999", f"{HEALTH} adb=SERIAL-2"),
    ],
    ids=[
        "another-panel-same-address",
        "app-reports-no-serial",
        "another-port",
    ],
)
async def test_adding_another_panel_keeps_the_repair(
    hass: HomeAssistant, aioclient_mock, install_network_pin, typed, health_line
) -> None:
    """A different panel, or one that cannot say which it is, clears nothing."""
    issue_id = await _failed_install(hass)

    await _add_found_panel(hass, aioclient_mock, typed, health_line)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

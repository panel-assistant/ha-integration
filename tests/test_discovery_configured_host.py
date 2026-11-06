"""Discovery and the add flow recognize configured hosts across listener ports."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import discovery
from custom_components.panel_assistant.client import (
    CannotConnectError,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import PinnedPanelTarget

from .config_flow_helpers import _probe, _start_step
from .test_migration_targets import (
    HEALTH_CHECK,
    _adb_advertised,
    _cards,
    _companion,
    _host_entry,
    _shelly_wall_display,
    adb_open,
)

# Expose the shared LAN boundary fixture to this module.
adb_open = adb_open


@pytest.mark.parametrize("source", ["adb", "vendor", "registry"])
@pytest.mark.parametrize(
    "stored,host,offered",
    [
        ("192.168.1.50:8889", "192.168.1.50", False),
        ("192.168.1.50", "192.168.1.50", False),
        ("192.168.1.50:8888", "192.168.1.50", False),
        ("192.168.1.51:8889", "192.168.1.50", True),
        ("[fd00::50]:8889", "fd00::50", False),
        ("[fd00::51]:8889", "fd00::50", True),
    ],
)
async def test_configured_host_is_not_discovered_again(
    hass: HomeAssistant,
    adb_open: SimpleNamespace,
    source: str,
    stored: str,
    host: str,
    offered: bool,
) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="configured-panel", data={CONF_ADDRESS: stored}
    ).add_to_hass(hass)
    adb_open.open.add(host)
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.discovery.async_probe_install_target",
            AsyncMock(return_value=_probe("installed")),
        ),
    ):
        if source == "adb":
            await _adb_advertised(hass, host)
        elif source == "vendor":
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_shelly_wall_display(host),
            )
        else:
            if ":" in host:
                _host_entry(hass, "fully_kiosk", "Known tablet", f"[{host}]")
            else:
                _companion(hass, "Known tablet", host, "companion")
            discovery.async_offer_migration_targets(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert bool(_cards(hass)) is offered


@pytest.mark.parametrize(
    "stored,typed,configured",
    [
        ("192.168.1.50:8889", "192.168.1.50", True),
        ("192.168.1.50", "192.168.1.50", True),
        ("192.168.1.50:8888", "192.168.1.50", True),
        ("Panel.LOCAL:8889", "panel.local", True),
        ("192.168.1.51:8889", "192.168.1.50", False),
        ("[fd00::50]:8889", "[fd00::50]", True),
        ("[fd00::51]:8889", "[fd00::50]", False),
    ],
)
async def test_add_panel_does_not_offer_installed_configured_host(
    hass: HomeAssistant,
    stored: str,
    typed: str,
    configured: bool,
) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="configured-panel", data={CONF_ADDRESS: stored}
    ).add_to_hass(hass)
    address = normalize_address(typed)
    pinned = normalize_address("192.168.1.50") if typed == "panel.local" else address
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(return_value=PinnedPanelTarget(address, pinned)),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(
                return_value=_probe(
                    "installed",
                    model="Test Panel",
                    serial="SERIAL",
                    primary_abi="arm64-v8a",
                    android_sdk=34,
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_list_install_choices",
            AsyncMock(return_value=[{"tag": "v1.0.0", "prerelease": False}]),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: typed}
        )
    if configured:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
    else:
        assert result["step_id"] == "choose_version"


async def test_entry_added_during_adb_probe_blocks_the_card(
    hass: HomeAssistant,
    adb_open: SimpleNamespace,
) -> None:
    adb_open.open.add("192.168.1.50")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_entry() -> None:
        entered.set()
        await release.wait()

    adb_open.gate = SimpleNamespace(wait=wait_for_entry)
    task = asyncio.create_task(_adb_advertised(hass, "192.168.1.50"))
    await entered.wait()
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="added-during-probe",
        data={CONF_ADDRESS: "192.168.1.50:8889"},
    ).add_to_hass(hass)
    release.set()
    await task
    assert _cards(hass) == {}


@pytest.mark.parametrize("state", ["install_candidate", "adb_unauthorized"])
async def test_configured_host_can_still_offer_proven_reinstallation(
    hass: HomeAssistant,
    adb_open: SimpleNamespace,
    state: str,
) -> None:
    adb_open.open.add("192.168.1.50")
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="configured-panel",
        data={CONF_ADDRESS: "192.168.1.50:8889"},
    ).add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.discovery.async_probe_install_target",
        AsyncMock(return_value=_probe(state)),
    ):
        await _adb_advertised(hass, "192.168.1.50")
    # The existing clean/unauthorized classifier exception remains available;
    # an unavailable app alone neither removes its entry nor grants install.
    assert len(_cards(hass)) == 1


async def test_unrelated_invalid_entry_addresses_do_not_hide_a_new_host(
    hass: HomeAssistant,
    adb_open: SimpleNamespace,
) -> None:
    for value in (None, 3, "http://bad"):
        MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: value}).add_to_hass(hass)
    adb_open.open.add("192.168.1.50")
    await _adb_advertised(hass, "192.168.1.50")
    assert len(_cards(hass)) == 1

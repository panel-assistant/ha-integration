"""Repairs and forms name a panel as the owner renamed it in Home Assistant.

A panel is often added under its discovery hostname and renamed afterwards in
Settings > Devices. Every notice that names the panel must use that rename.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, PanelHealth
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.foreign_ha import (
    async_raise_foreign_ha_issue,
    foreign_ha_issue_id,
)
from custom_components.panel_assistant.identity import ISSUE_IDENTITY, accept_health
from custom_components.panel_assistant.repairs import async_create_fix_flow
from custom_components.panel_assistant.transport import (
    async_raise_merged_identity_issue,
    merged_identity_issue_id,
)

from . import test_availability as availability
from . import test_reinstalled_panel as reinstalled
from . import test_upgrade_repairs as upgrade
from .test_installation_identity import _health as _install_health
from .test_reinstalled_panel import network  # noqa: F401 - fixture by parameter name
from .test_transport import WsClientFactory, _hello, _send, _start_fix_flow

RENAMED = "Living room screen"


def _rename(hass: HomeAssistant, entry: ConfigEntry, name: str = RENAMED) -> None:
    """Rename the panel's device the way Settings > Devices does."""
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    registry.async_update_device(device.id, name_by_user=name)


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    return issue


async def test_another_home_assistant_repair_uses_the_rename(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    entry = await availability._load(hass, hass_read_only_user.id)
    _rename(hass, entry)

    assert async_raise_foreign_ha_issue(hass, entry)

    issue = _issue(hass, foreign_ha_issue_id(entry.entry_id))
    assert issue.translation_placeholders == {"panel": RENAMED}


async def test_other_account_repair_and_its_rebind_form_use_the_rename(
    hass: HomeAssistant,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_user: Any,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await availability._load(hass, hass_read_only_user.id)
    _rename(hass, entry)
    other = await hass_ws_client(hass, hass_access_token)

    refused = await _send(other, _hello())

    assert refused["error"]["code"] == "panel_user_mismatch"
    issue_id = f"panel_user_mismatch_{entry.entry_id}"
    assert _issue(hass, issue_id).translation_placeholders == {"panel": RENAMED}
    _, form = await _start_fix_flow(await hass_client(), issue_id)
    assert form["step_id"] == "confirm_rebind"
    assert form["description_placeholders"]["panel"] == RENAMED


async def test_merged_identity_repair_uses_the_rename(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    entry = await availability._load(hass, hass_read_only_user.id)
    _rename(hass, entry)

    async_raise_merged_identity_issue(hass, "did-1", [entry])

    issue = _issue(hass, merged_identity_issue_id("did-1"))
    assert issue.translation_placeholders == {"panels": RENAMED}


async def test_unverified_address_repair_uses_the_rename(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    entry = await availability._load(hass, hass_read_only_user.id)
    _rename(hass, entry)
    await availability._connect(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )

    await availability._poll(
        hass,
        entry,
        {
            availability.STORED: CannotConnectError(),
            availability.MOVED: CannotConnectError(),
        },
    )

    issue = availability._issue(hass, entry)
    assert issue is not None
    assert (issue.translation_placeholders or {})["panel"] == RENAMED


async def test_identity_mismatch_repair_uses_the_rename(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    network: reinstalled.Network,  # noqa: F811
) -> None:
    entry = await reinstalled._load(hass, network, hass_read_only_user.id, link=None)
    _rename(hass, entry)
    network.answers[reinstalled.STORED] = reinstalled._health(reinstalled.NEW)

    await reinstalled._poll(hass, entry)

    issue = _issue(hass, f"panel_identity_mismatch_{entry.entry_id}")
    assert (issue.translation_placeholders or {})["panel"] == RENAMED


async def test_shared_link_repair_names_the_other_panel_by_its_rename(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    network: reinstalled.Network,  # noqa: F811
) -> None:
    entry = await reinstalled._load(hass, network, hass_read_only_user.id)
    beta = await reinstalled._load(
        hass,
        network,
        hass_read_only_user.id,
        reinstalled.OTHER,
        reinstalled.OTHER_ADDRESS,
        title="beta",
    )
    _rename(hass, beta)
    network.answers[reinstalled.STORED] = reinstalled._health(reinstalled.NEW)

    await reinstalled._poll(hass, entry)

    issue = _issue(hass, f"panel_identity_mismatch_{entry.entry_id}")
    assert issue.translation_key == "panel_link_shared"
    assert (issue.translation_placeholders or {})["others"] == RENAMED


async def test_identity_confirmation_repair_and_its_flow_use_the_rename(
    hass: HomeAssistant,
) -> None:
    legacy, target = "1" * 64, "a" * 64
    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", unique_id=legacy, data={"address": "192.0.2.1"}
    )
    entry.add_to_hass(hass)
    _rename(hass, entry)

    assert not accept_health(hass, entry, _install_health(target, legacy))

    issue = _issue(hass, f"{ISSUE_IDENTITY}_{entry.entry_id}")
    assert (issue.translation_placeholders or {})["panel"] == RENAMED
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass = hass
    shown = await flow.async_step_init()
    assert RENAMED in shown["description_placeholders"]["panels"]


async def test_panel_never_moved_off_mqtt_is_refused_under_its_rename(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """The setup error now matches the Repair, which already used the rename."""
    upgrade._mqtt_device(hass)
    entry = upgrade._entry(hass, hass_read_only_user.id, options={"authority": "mqtt"})
    _rename(hass, entry)

    await upgrade._set_up(hass, entry, upgrade.CURRENT)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    issue = _issue(hass, f"mqtt_panel_needs_090_{entry.entry_id}")
    assert issue.translation_placeholders == {"panel": RENAMED}
    assert entry.error_reason_translation_placeholders == {"panel": RENAMED}


async def test_authorize_adb_form_uses_the_rename(hass: HomeAssistant) -> None:
    health = PanelHealth(
        version="0.9.9",
        panel_id="alpha",
        build="998",
        config_hash="abcd1234",
        protocol=(5, 5),
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=health))
    )
    _rename(hass, entry)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        AsyncMock(return_value=health),
    ):
        opened = await hass.config_entries.options.async_init(entry.entry_id)
        assert opened["type"] is FlowResultType.MENU
        consent = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "authorize_adb"}
        )

    assert consent["step_id"] == "authorize_adb"
    assert consent["description_placeholders"]["panel"] == RENAMED

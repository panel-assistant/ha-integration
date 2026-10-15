"""Panel Assistant 1.0 refuses a panel still on the old app id.

Only Panel Assistant 0.9.0 and earlier can move a panel from the old app to the
new one. This release says so at setup, in a Repair and on an update, and
installs nothing on the panel.
"""

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.app_identity import LEGACY_PACKAGE_ID
from custom_components.panel_assistant.const import DOMAIN, SOURCE_USB_INSTALL

from . import config_flow_helpers
from .config_flow_helpers import DISCOVERY_HEALTH, DISCOVERY_ID, _start_step
from .config_flow_helpers import HEALTH as FLOW_HEALTH
from .test_upgrade_repairs import CURRENT, _entry, _panel, _set_up

# The config-flow tests' network pin, so adding a panel never resolves DNS.
install_network_pin = config_flow_helpers.install_network_pin

# Builds older than the app-id move report no package; both are the old app.
OLD_APP_HEALTHS = pytest.mark.parametrize(
    "package", [LEGACY_PACKAGE_ID, None], ids=["old_id", "no_package"]
)


def _issues(hass: HomeAssistant, entry_id: str) -> set[str]:
    return {
        issue_id
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.endswith(f"_{entry_id}")
    }


@OLD_APP_HEALTHS
@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_USB_INSTALL])
async def test_setup_refuses_an_answering_old_app(
    hass: HomeAssistant, package: str | None, source: str
) -> None:
    """Adding a panel whose old app answers ends in the refusal, with no entry."""
    probe = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=replace(FLOW_HEALTH, package=package)),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe,
        ),
    ):
        if source == SOURCE_USER:
            form = await _start_step(hass, "add_panel")
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            )
        else:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_USB_INSTALL},
                data={CONF_ADDRESS: "panel.local"},
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "old_app_installed"
    assert hass.config_entries.async_entries(DOMAIN) == []
    probe.assert_not_awaited()


@OLD_APP_HEALTHS
async def test_a_configured_old_app_panel_gets_the_refusal_not_the_move(
    hass: HomeAssistant, hass_read_only_user: Any, package: str | None
) -> None:
    """Its Repair says to move it with 0.9.0, and goes once the new app answers."""
    entry = _entry(hass, hass_read_only_user.id)

    await _set_up(hass, entry, replace(CURRENT, package=package))

    refusal = ir.async_get(hass).async_get_issue(
        DOMAIN, f"old_app_needs_090_{entry.entry_id}"
    )
    assert refusal is not None
    assert not refusal.is_fixable
    assert refusal.translation_key == "old_app_needs_090"
    assert _issues(hass, entry.entry_id) == {f"old_app_needs_090_{entry.entry_id}"}

    with _panel(CURRENT):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    assert _issues(hass, entry.entry_id) == set()


async def test_a_move_already_started_is_still_finished(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A panel caught half-way through a move keeps its move, not the refusal."""
    entry = _entry(
        hass, hass_read_only_user.id, data={"successor_move": {"panel_id": "alpha"}}
    )

    await _set_up(hass, entry, replace(CURRENT, package=LEGACY_PACKAGE_ID))

    assert _issues(hass, entry.entry_id) == {f"move_to_new_app_{entry.entry_id}"}


async def test_updating_an_old_app_panel_installs_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """The update entity refuses before it looks for any way onto the panel."""
    entry = _entry(hass, hass_read_only_user.id)
    old = replace(CURRENT, package=LEGACY_PACKAGE_ID)
    await _set_up(hass, entry, old)
    registry = er.async_get(hass)
    entity_id = next(
        item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "update"
    )
    entity = hass.data["update"].get_entity(entity_id)

    route = AsyncMock(side_effect=AssertionError("no install route is looked for"))
    with (
        _panel(old),
        patch.object(type(entity), "_async_install_route", route),
        pytest.raises(HomeAssistantError) as raised,
    ):
        await entity.async_install(None, False)

    assert raised.value.translation_key == "old_app_needs_090"
    route.assert_not_awaited()


async def test_adding_a_moving_panel_again_still_repairs_its_address(
    hass: HomeAssistant,
) -> None:
    """A panel whose move already started is the owner's, not a new old app."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=DISCOVERY_ID,
        data={
            CONF_ADDRESS: "192.168.1.50",
            "installation_identity": True,
            "successor_move": {"panel_id": "alpha"},
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        AsyncMock(return_value=replace(DISCOVERY_HEALTH, package=LEGACY_PACKAGE_ID)),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_ADDRESS] == "panel.local"

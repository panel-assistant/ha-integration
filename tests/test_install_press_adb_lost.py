"""The Install press names ADB when ADB is what changed after the preview.

A firewall change or a panel reboot can close the panel's ADB port, or make it
forget this Home Assistant, between the install preview and the Install press.
The panel is the same panel; the press says what happened to ADB.
"""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.install_jobs import InstallPhase
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)

from . import config_flow_helpers
from .config_flow_helpers import RELEASE, _choose_version, _start_step
from .test_config_flow import CANDIDATE, CREDENTIAL, _manager_for, _receipt

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
install_network_pin = config_flow_helpers.install_network_pin


@pytest.mark.parametrize(
    ("state", "error"),
    [
        (InstallTargetState.ADB_UNREACHABLE, "adb_unreachable"),
        (InstallTargetState.ADB_UNAUTHORIZED, "adb_still_unauthorized"),
    ],
    ids=["adb_closed", "adb_unauthorized"],
)
async def test_install_press_names_adb_when_adb_is_what_changed(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    state: InstallTargetState,
    error: str,
) -> None:
    """ADB lost after the preview is reported as ADB, not as a changed panel."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    probe_mock = AsyncMock(side_effect=[CANDIDATE, InstallTargetProbe(state)])
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_install_candidate"
    assert result["errors"] == {"base": error}
    manager.async_create_or_join.assert_not_awaited()
    # The log keeps what the probe saw before and at the press.
    assert f"previewed=install_candidate now={state.value}" in caplog.text

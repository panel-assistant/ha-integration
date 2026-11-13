"""The add-panel flow tells the owner about device security changes first."""

from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.install_receipt import InstallPhase

from . import config_flow_helpers
from .config_flow_helpers import RELEASE, _start_step
from .test_config_flow import CANDIDATE, _english, _manager_for, _receipt

install_release_catalog = config_flow_helpers.install_release_catalog
install_network_pin = config_flow_helpers.install_network_pin


_VERIFIER_DISCLOSURE = (
    "On a Meta Portal, Panel Assistant turns off Android's package verifier"
)


async def test_both_install_routes_say_a_portal_loses_its_package_verifier(
    hass: HomeAssistant, install_release_catalog: AsyncMock
) -> None:
    """Turning off the verifier is a security change the owner hears about first."""
    install_release_catalog.return_value = [{"tag": "v0.9.7", "prerelease": False}]
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=_manager_for(_receipt(InstallPhase.APPROVED))),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        question = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
    usb = await _start_step(hass, "install_usb")

    for result in (question, usb):
        text = _english(
            "config", result["step_id"], result["description_placeholders"] or {}
        )
        assert _VERIFIER_DISCLOSURE in text, result["step_id"]
        assert "stops checking that new apps were signed by Meta" in text
    # The release candidate and rejoin questions say it too.
    for step_id in (
        "confirm_install_rc",
        "confirm_reinstall_candidate",
        "confirm_reinstall_rc",
    ):
        assert _VERIFIER_DISCLOSURE in _english(
            "config",
            step_id,
            {**question["description_placeholders"], "entry_title": "Panel"},
        ), step_id

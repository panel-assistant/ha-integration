"""The add-panel flow tells the owner about device security changes first."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
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


@pytest.mark.parametrize(
    ("model", "told"), [("Portal", True), ("Portal Mini", True), ("WF1589T", False)]
)
async def test_only_a_portal_is_told_it_loses_its_package_verifier(
    hass: HomeAssistant, install_release_catalog: AsyncMock, model: str, told: bool
) -> None:
    """Turning off the verifier is a Portal's security change, heard about first.

    Every other panel keeps its verifier, so its owner is not shown the
    paragraph at all.
    """
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
            AsyncMock(return_value=replace(CANDIDATE, model=model)),
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

    assert question["step_id"] == "confirm_install_candidate"
    placeholders = question["description_placeholders"] or {}
    text = _english("config", question["step_id"], placeholders)
    assert (_VERIFIER_DISCLOSURE in text) is told
    assert ("stops checking that new apps were signed by Meta" in text) is told
    assert "other apps." in text
    # The release candidate and rejoin questions carry the same note.
    for step_id in (
        "confirm_install_rc",
        "confirm_reinstall_candidate",
        "confirm_reinstall_rc",
    ):
        assert (
            _VERIFIER_DISCLOSURE
            in _english("config", step_id, {**placeholders, "entry_title": "Panel"})
        ) is told, step_id


async def test_the_usb_route_says_a_portal_loses_its_package_verifier(
    hass: HomeAssistant,
) -> None:
    """USB asks before the panel is known, so it keeps the Portal paragraph."""
    usb = await _start_step(hass, "install_usb")
    text = _english("config", usb["step_id"], usb["description_placeholders"] or {})
    assert _VERIFIER_DISCLOSURE in text

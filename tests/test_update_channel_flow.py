"""Channel recommendation and durable consent through the real install flow."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.config_flow import HaPaneldConfigFlow
from custom_components.panel_assistant.install_jobs import InstallPhase
from tests.test_config_flow import (
    ARTIFACT,
    CANDIDATE,
    CREDENTIAL,
    HEALTH,
    RELEASE,
    TARGET,
    _direct_result_flow,
    _final_proof,
    _finalizing_executor,
    _manager_for,
    _rc_release,
    _receipt,
    _start_step,
)
from tests.test_config_flow import install_network_pin as install_network_pin
from tests.test_config_flow import install_release_catalog as install_release_catalog


async def test_default_install_recommendation_follows_pa_catalogue_first_row(
    hass: HomeAssistant, install_release_catalog: AsyncMock
) -> None:
    """An automatic PA-channel prerelease is the default without a per-panel opt-in."""
    selected = _rc_release("v0.9.8-rc1")
    install_release_catalog.return_value = [
        {"tag": selected.tag, "prerelease": True},
        {"tag": RELEASE.tag, "prerelease": False},
    ]
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    with (
        patch(
            "custom_components.panel_assistant.update_policy.INTEGRATION_VERSION",
            "1.0.0-rc1",
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
            AsyncMock(return_value=selected),
        ) as resolver,
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch.object(
            HaPaneldConfigFlow,
            "_async_show_install_progress",
            AsyncMock(
                return_value={"type": FlowResultType.ABORT, "reason": "proof-complete"}
            ),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        assert choose["data_schema"]({}) == {"release_candidate": "stable"}
        selector = next(iter(choose["data_schema"].schema.values()))
        assert selector.config["options"][0] == {
            "value": "stable",
            "label": f"{selected.tag} (recommended)",
        }
        preview = await hass.config_entries.flow.async_configure(choose["flow_id"], {})
        assert preview["step_id"] == "confirm_install_candidate"
        assert preview["description_placeholders"]["tag"] == selected.tag
        resolver.assert_awaited_once_with(hass, None)
        await hass.config_entries.flow.async_configure(preview["flow_id"], {})
    manager.async_create_or_join.assert_awaited_once()
    planned = manager.async_create_or_join.await_args.args[1]
    assert planned.release_tag == selected.tag
    assert planned.prerelease_opt_in is False


@pytest.mark.parametrize("opt_in", [False, True])
async def test_completed_install_preserves_its_per_panel_prerelease_choice(
    hass: HomeAssistant, opt_in: bool
) -> None:
    """A fresh entry carries its durable receipt's explicit channel consent."""
    version = "0.9.7-rc3" if opt_in else "0.9.7"
    receipt = replace(
        _receipt(InstallPhase.HEALTHY_UNCLAIMED),
        artifact=replace(
            ARTIFACT,
            prerelease_opt_in=opt_in,
            release_tag=f"v{version}",
            version_name=version,
            apk_name=f"ha-paneld-v{version}-manual-setup-required.apk",
        ),
    )
    manager = _manager_for(receipt)
    flow = _direct_result_flow(hass, receipt, _finalizing_executor(hass, manager))
    with (
        patch(
            "custom_components.panel_assistant.update_policy.INTEGRATION_VERSION",
            "1.0.0",
        ),
        _final_proof(
            manager, health=AsyncMock(return_value=replace(HEALTH, version=version))
        ),
    ):
        result = await flow.async_step_install_result()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: TARGET.address}
    assert result["options"] == {
        "authority": "native",
        **({"prerelease_panel_builds": True} if opt_in else {}),
    }

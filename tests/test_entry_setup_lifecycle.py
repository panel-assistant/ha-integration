"""A healthy installation becomes a device only after its first setup finishes."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelSetupState,
    normalize_address,
)
from custom_components.panel_assistant.config_flow import HaPaneldConfigFlow
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
)

from .config_flow_helpers import HEALTH
from .install_executor_harness import (  # noqa: F401 - durable store fixture
    emulate_home_assistant_store_file,
)
from .test_install_executor_finalizer import FinalProof, seed_healthy


@pytest.mark.parametrize("complete", [False, True])
async def test_new_usb_panel_waits_for_completed_setup(
    hass: HomeAssistant, complete: bool
) -> None:
    """The actual USB handover entry point cannot add an unfinished device."""
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
            AsyncMock(
                return_value=PanelSetupState(complete=complete, needs_user=False)
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "usb_install"},
            data={CONF_ADDRESS: "panel.local"},
        )

    assert result["type"] is (
        FlowResultType.CREATE_ENTRY if complete else FlowResultType.EXTERNAL_STEP
    )
    assert len(hass.config_entries.async_entries(DOMAIN)) == int(complete)


async def test_interrupted_first_setup_keeps_receipt_retryable_after_restart(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing setup loses no install; a fresh executor finalizes once complete."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch)
    setup = AsyncMock(return_value=PanelSetupState(complete=False))
    monkeypatch.setattr(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_setup_state",
        setup,
    )
    first = HaPaneldConfigFlow()
    first.hass = hass
    first.flow_id = "before_restart"
    first._pending_job_id = receipt.job_id
    first._install_executor = InstallExecutor(hass, manager)
    result = await first._async_finalize_healthy_install(receipt)

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert not first._install_executor.is_finalizer_active(receipt.job_id)
    assert not hass.config_entries.async_entries(DOMAIN)
    continued = await first.async_step_panel_setup({})
    assert continued["step_id"] == "install_result"
    first.async_remove()
    restarted = InstallJobManager(hass)
    saved = await restarted.async_get(receipt.job_id)
    assert saved.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert saved.consumed_entry_id is None
    setup.return_value = PanelSetupState(complete=True)
    second = HaPaneldConfigFlow()
    second.hass = hass
    second.flow_id = "after_restart"
    second._pending_job_id = receipt.job_id
    second._install_executor = InstallExecutor(hass, restarted)
    resumed = await second._async_finalize_healthy_install(saved)
    assert resumed["type"] is FlowResultType.CREATE_ENTRY
    assert resumed["data"][CONF_ADDRESS] == receipt.target.address
    second.async_remove()


async def test_cancelled_setup_read_releases_the_verified_install(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interruption after health proof leaves the receipt for the next flow."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch)
    monkeypatch.setattr(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_setup_state",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = "cancelled_setup_read"
    flow._pending_job_id = receipt.job_id
    flow._install_executor = InstallExecutor(hass, manager)
    with pytest.raises(asyncio.CancelledError):
        await flow._async_finalize_healthy_install(receipt)
    assert not flow._install_executor.is_finalizer_active(receipt.job_id)
    assert (await manager.async_get(receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_existing_identity_reconnects_during_interrupted_setup(
    hass: HomeAssistant,
) -> None:
    """An existing configured panel keeps its entry and stored options."""
    health = replace(HEALTH, discovery_id="a" * 64)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=health.discovery_id,
        data={CONF_ADDRESS: "panel.local"},
        options={"keep": "saved"},
    )
    entry.add_to_hass(hass)
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = "reconnect"
    flow._pending_address = normalize_address("panel.local")
    flow._pending_health = health
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
        AsyncMock(return_value=PanelSetupState(complete=False)),
    ):
        result = await flow._async_show_found_panel()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.options == {"keep": "saved"}

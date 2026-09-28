"""A Repairs retry authorizes a new job only for the same proven panel and bytes."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant.client import normalize_address
from custom_components.panel_assistant.failure_repair import (
    RetrySafetyHold,
    async_retry_install_job,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.install_plan import install_plan_sha256
from tests import test_install_jobs as job_fixtures
from tests.test_config_flow import ARTIFACT, CANDIDATE, CREDENTIAL, RELEASE, TARGET
from tests.test_install_jobs import Clock

emulate_home_assistant_store_file = job_fixtures.emulate_home_assistant_store_file


async def _failed_receipt(manager: InstallJobManager):
    receipt, _ = await manager.async_create_or_join(
        TARGET,
        ARTIFACT,
        install_plan_sha256(TARGET, ARTIFACT, CREDENTIAL.generation_id),
        CREDENTIAL.generation_id,
    )
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    receipt = await manager.async_transition(
        receipt.job_id, receipt.revision, InstallPhase.AUTHORIZING
    )
    return await manager.async_transition(
        receipt.job_id,
        receipt.revision,
        InstallPhase.FAILED,
        result_code=InstallResultCode.AUTHORIZATION_FAILED,
        result_subcode="adb:authorization_failed",
    )


async def test_retry_creates_new_job_for_exact_physical_target_and_signed_release(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    old = await _failed_receipt(manager)
    pin = PinnedPanelTarget(
        original=normalize_address(TARGET.address),
        pinned=normalize_address(TARGET.pinned_address),
    )
    with (
        patch(
            "custom_components.panel_assistant.install_network.async_pin_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.install_network.async_revalidate_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.adb_credentials.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.provisioning.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_install_choice",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.install_jobs.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        fresh = await async_retry_install_job(hass, old)

    assert fresh.job_id != old.job_id
    assert fresh.phase is InstallPhase.APPROVED
    assert fresh.target == old.target
    assert fresh.artifact == old.artifact


async def test_retry_holds_before_mutation_when_dns_points_at_another_panel(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    old = await _failed_receipt(manager)
    changed_pin = PinnedPanelTarget(
        original=normalize_address(TARGET.address),
        pinned=normalize_address("192.168.1.24"),
    )
    probe = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.install_network.async_pin_install_target",
            AsyncMock(return_value=changed_pin),
        ),
        patch(
            "custom_components.panel_assistant.provisioning.async_probe_install_target",
            probe,
        ),
        pytest.raises(RetrySafetyHold, match="retry_target_changed"),
    ):
        await async_retry_install_job(hass, old)

    probe.assert_not_awaited()
    assert [receipt.job_id for receipt in await manager.async_list()] == [old.job_id]


async def test_retry_holds_if_adb_credential_changed(hass: HomeAssistant) -> None:
    manager = InstallJobManager(hass, now=Clock())
    old = await _failed_receipt(manager)
    pin = PinnedPanelTarget(
        original=normalize_address(TARGET.address),
        pinned=normalize_address(TARGET.pinned_address),
    )
    probe = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.install_network.async_pin_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.install_network.async_revalidate_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.adb_credentials.async_get_durable_adb_credential",
            AsyncMock(return_value=replace(CREDENTIAL, generation_id="c" * 64)),
        ),
        patch(
            "custom_components.panel_assistant.provisioning.async_probe_install_target",
            probe,
        ),
        pytest.raises(RetrySafetyHold, match="retry_credential_changed"),
    ):
        await async_retry_install_job(hass, old)

    probe.assert_not_awaited()
    assert [receipt.job_id for receipt in await manager.async_list()] == [old.job_id]

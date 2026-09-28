"""A Repairs retry authorizes a new job only for the same proven panel and bytes."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from yarl import URL

from custom_components.panel_assistant.build_feed import (
    FeedBuild,
    feed_release_artifact,
)
from custom_components.panel_assistant.client import normalize_address
from custom_components.panel_assistant.failure_repair import (
    RetrySafetyHold,
    async_retry_install_job,
)
from custom_components.panel_assistant.install_jobs import (
    InstallArtifact,
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.install_plan import (
    _build_artifact,
    install_plan_sha256,
)
from tests import test_install_jobs as job_fixtures
from tests.test_config_flow import ARTIFACT, CANDIDATE, CREDENTIAL, RELEASE, TARGET
from tests.test_install_jobs import Clock

emulate_home_assistant_store_file = job_fixtures.emulate_home_assistant_store_file


async def _failed_receipt(
    manager: InstallJobManager, artifact: InstallArtifact = ARTIFACT
):
    receipt, _ = await manager.async_create_or_join(
        TARGET,
        artifact,
        install_plan_sha256(TARGET, artifact, CREDENTIAL.generation_id),
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


async def test_retry_keeps_the_exact_signed_feed_build(hass: HomeAssistant) -> None:
    build = FeedBuild(
        version_code=444,
        version_name="0.9.7-rc4",
        apk_url=URL("https://feed.example/apk"),
        apk_sha256="a" * 64,
        apk_size=1234,
        commit="0" * 40,
        database_compatibility="hapaneld-db:v1:ha-paneld.db:1:1",
        min_sdk=26,
        published="2026-09-28T00:00:00Z",
        package_id="io.github.maxlyth.hapaneld",
    )
    release = feed_release_artifact(build)
    artifact = _build_artifact(release, release.tag)
    manager = InstallJobManager(hass, now=Clock())
    old = await _failed_receipt(manager, artifact)
    pin = PinnedPanelTarget(
        original=normalize_address(TARGET.address),
        pinned=normalize_address(TARGET.pinned_address),
    )
    resolver = AsyncMock(return_value=release)
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
            resolver,
        ),
        patch(
            "custom_components.panel_assistant.install_jobs.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        fresh = await async_retry_install_job(hass, old)

    resolver.assert_awaited_once_with(hass, release.tag)
    assert fresh.job_id != old.job_id
    assert fresh.artifact == old.artifact

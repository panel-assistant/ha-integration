"""Durable panel install admission and channel consent across retries and restarts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    install_executor,
    install_jobs,
    update_policy,
)
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobConflictError,
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
    install_plan_sha256,
)
from tests.test_install_executor import Harness, create_job
from tests.test_install_executor import artifact as executor_artifact
from tests.test_install_executor import (
    emulate_home_assistant_store_file as emulate_home_assistant_store_file,
)
from tests.test_install_jobs import CREDENTIAL_ID, Clock, create, target
from tests.test_install_jobs import artifact as stored_artifact


async def test_only_the_first_preflight_may_report_a_satisfied_target(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revalidation before a mutation still demands a clean panel."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.preflight_admissions == [True, False]
    assert "installed_size" not in harness.events


@pytest.mark.parametrize("minimum,maximum", [(None, None), (4, 5)])
async def test_durable_install_refuses_unknown_or_incompatible_range_before_download(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    minimum: int | None,
    maximum: int | None,
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(
        manager,
        replace(executor_artifact(), protocol_min=minimum, protocol_max=maximum),
    )
    harness = Harness(monkeypatch)
    completed = await InstallExecutor(hass, InstallJobManager(hass)).async_wait(
        receipt.job_id
    )
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.ARTIFACT_REJECTED
    assert completed.result_subcode == "artifact:artifact_contract_invalid"
    assert harness.events.count("preflight") == 1
    assert "download" not in harness.events
    assert "stage" not in harness.events
    assert "install" not in harness.events


async def test_historical_missing_range_receipt_finishes_verified_installed_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(
        manager, replace(executor_artifact(), protocol_min=None, protocol_max=None)
    )
    harness = Harness(monkeypatch)
    harness.target_installed = True
    harness.installed_bytes = receipt.artifact.apk_size
    completed = await InstallExecutor(hass, InstallJobManager(hass)).async_wait(
        receipt.job_id
    )
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("installed_size") == 1
    assert harness.events.count("launch") == 1
    assert harness.events.count("health") == 1
    assert "download" not in harness.events
    assert "stage" not in harness.events
    assert "install" not in harness.events


@pytest.mark.parametrize("barrier", [InstallPhase.STAGING, InstallPhase.INSTALLING])
@pytest.mark.parametrize("explicit_opt_in", [False, True])
async def test_running_pa_channel_is_rechecked_at_each_apk_mutation_barrier(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    barrier: InstallPhase,
    explicit_opt_in: bool,
) -> None:
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0-rc2")
    manager = InstallJobManager(hass)
    selected = replace(
        executor_artifact(),
        release_tag="v0.9.7-rc3",
        version_name="0.9.7-rc3",
        apk_name="ha-paneld-v0.9.7-rc3-manual-setup-required.apk",
        prerelease_opt_in=explicit_opt_in,
    )
    receipt = await create_job(manager, selected)
    harness = Harness(monkeypatch, expected_version=selected.version_name)
    channel_changed = False

    async def credential_after_channel_change(
        selected_hass: HomeAssistant,
    ) -> AdbCredential:
        nonlocal channel_changed
        current = await manager.async_get(receipt.job_id)
        if current.phase is barrier:
            monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0")
            channel_changed = True
        return await harness.async_credential(selected_hass)

    monkeypatch.setattr(
        install_executor,
        "async_get_durable_adb_credential",
        credential_after_channel_change,
    )
    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    assert channel_changed
    assert len(harness.download_arguments) == 1
    assert harness.download_arguments[0][0].protocol_min == 3
    assert harness.download_arguments[0][0].protocol_max == 3
    if explicit_opt_in:
        assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
        assert harness.events.count("stage") == 1
        assert harness.events.count("install") == 1
    else:
        assert completed.phase is InstallPhase.FAILED
        assert "install" not in harness.events
        if barrier is InstallPhase.STAGING:
            assert "stage" not in harness.events
            assert completed.result_code is InstallResultCode.ARTIFACT_REJECTED
        else:
            assert harness.events.count("stage") == 1
            assert harness.events.count("remote_cleanup:install_refused") == 1
            assert completed.result_code is InstallResultCode.INSTALL_FAILED


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.9.7-rc3"])
async def test_stable_and_rc_receipts_restart_without_changing_frozen_plan(
    hass: HomeAssistant, tag: str
) -> None:
    """Legacy stable shape and approved RC identity both survive durable reload."""
    selected = replace(
        stored_artifact(),
        release_tag=tag,
        version_name=tag[1:],
        apk_name=f"ha-paneld-{tag}-manual-setup-required.apk",
    )
    manager = InstallJobManager(hass, now=Clock())
    receipt, created = await create(manager, install_artifact=selected)
    assert created is True
    persisted = install_jobs._serialize_receipt(receipt)
    assert persisted["artifact"] == {
        **{
            key: value
            for key, value in asdict(selected).items()
            if key not in {"protocol_min", "protocol_max", "prerelease_opt_in"}
        },
        "supported_abis": list(selected.supported_abis),
    }
    restarted = InstallJobManager(hass, now=Clock())
    loaded = await restarted.async_get(receipt.job_id)
    assert loaded == receipt
    joined, created_again = await create(restarted, install_artifact=selected)
    assert created_again is False
    assert joined == receipt
    different = replace(
        selected,
        release_tag="v0.9.7-rc4",
        version_name="0.9.7-rc4",
        apk_name="ha-paneld-v0.9.7-rc4-manual-setup-required.apk",
    )
    with pytest.raises(InstallJobConflictError):
        await create(restarted, install_artifact=different)
    assert await restarted.async_get(receipt.job_id) == receipt


async def test_protocol_proof_and_explicit_consent_survive_restart_and_bind_plan(
    hass: HomeAssistant,
) -> None:
    selected = replace(
        stored_artifact(), protocol_min=2, protocol_max=3, prerelease_opt_in=True
    )
    manager = InstallJobManager(hass, now=Clock())
    receipt, created = await create(manager, install_artifact=selected)
    assert created
    persisted = install_jobs._serialize_receipt(receipt)["artifact"]
    assert persisted["protocol_min"] == 2
    assert persisted["protocol_max"] == 3
    assert persisted["prerelease_opt_in"] is True
    restarted = InstallJobManager(hass, now=Clock())
    assert (await restarted.async_get(receipt.job_id)).artifact == selected
    for changed in (
        replace(selected, protocol_min=1),
        replace(selected, protocol_max=4),
        replace(selected, prerelease_opt_in=False),
    ):
        assert (
            install_plan_sha256(target(), changed, CREDENTIAL_ID) != receipt.plan_sha256
        )
        with pytest.raises(InstallJobConflictError):
            await create(restarted, install_artifact=changed)


def test_historical_plan_hash_remains_byte_identical_without_protocol_proof() -> None:
    legacy_artifact = {
        key: value
        for key, value in asdict(stored_artifact()).items()
        if key not in {"protocol_min", "protocol_max", "prerelease_opt_in"}
    }
    canonical = (
        json.dumps(
            {
                "schema": "io.github.maxlyth.hapaneld.install-plan.v1",
                "target": asdict(target()),
                "artifact": legacy_artifact,
                "adb_credential_id": CREDENTIAL_ID,
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    assert (
        install_plan_sha256(target(), stored_artifact(), CREDENTIAL_ID)
        == hashlib.sha256(canonical.encode("ascii")).hexdigest()
    )

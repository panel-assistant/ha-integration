"""Adversarial tests for the process-wide clean-install executor."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    install_adb,
    install_executor,
    install_jobs,
)
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelAddress,
)
from custom_components.panel_assistant.install_adb import (
    DefiniteCleanupReason,
    StagedApk,
)
from custom_components.panel_assistant.install_artifacts import (
    ArtifactCustodyError,
    ArtifactErrorCode,
)
from custom_components.panel_assistant.install_artifacts import (
    InstallArtifact as CustodiedArtifact,
)
from custom_components.panel_assistant.install_executor import (
    InstallExecutor,
    async_get_install_executor,
)
from custom_components.panel_assistant.install_jobs import (
    InstallArtifact as InstallJobArtifact,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallJobReceipt,
    InstallJobRevisionError,
    InstallJobStoreError,
    InstallJobTransitionError,
    InstallPhase,
    InstallResultCode,
    InstallTarget,
    install_plan_sha256,
)
from custom_components.panel_assistant.install_network import (
    InstallNetworkErrorCode,
    PinnedPanelTarget,
)
from custom_components.panel_assistant.release import InstallDescriptor, ReleaseArtifact

from .install_adb_fakes import FakePanel
from .install_executor_harness import (  # noqa: F401 - the store fixture is autouse
    APK_SHA256,
    CREDENTIAL_ID,
    Harness,
    artifact,
    create_job,
    emulate_home_assistant_store_file,
    seed_phase,
    target,
)


def seed_crash_partial(path: Path) -> None:
    """Create one production-shaped private partial outside the event loop."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(b"interrupted download")
    path.chmod(0o600)


async def seed_attempt_ceiling(
    hass: HomeAssistant, phase: InstallPhase
) -> tuple[InstallJobReceipt, InstallJobManager]:
    """Persist a safe receipt at the real durable claim-attempt ceiling."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    receipt = await manager.async_transition(
        receipt.job_id, receipt.revision, InstallPhase.AUTHORIZING
    )
    for _ in range(31):
        manager = InstallJobManager(hass)
        receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    assert receipt.attempt == receipt.executor_generation == 32
    for next_phase in (
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
    ):
        transition_fields: dict[str, object] = {}
        if next_phase is InstallPhase.DOWNLOADING:
            transition_fields["preflight_root_mode"] = AdbRootMode.ROOTLESS.value
        if next_phase is InstallPhase.ARTIFACT_READY:
            transition_fields["actual_apk_bytes"] = artifact().apk_size
        receipt = await manager.async_transition(
            receipt.job_id,
            receipt.revision,
            next_phase,
            **transition_fields,
        )
        if next_phase is phase:
            return receipt, InstallJobManager(hass)
    raise AssertionError


async def test_happy_path_stops_unclaimed_and_binds_every_operation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One worker performs the bounded phase path but never creates an entry."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    add_entry = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_add", add_entry)
    verify_mutation = manager.async_verify_mutation_barrier
    verify_cleanup = manager.async_verify_cleanup_barrier

    async def mutation_barrier(
        job_id: str, revision: int, phase: InstallPhase
    ) -> InstallJobReceipt:
        harness.events.append(f"mutation_barrier:{phase.value}")
        harness.mutation_barrier_arguments.append((job_id, revision, phase))
        return await verify_mutation(job_id, revision, phase)

    async def cleanup_barrier(
        job_id: str, revision: int, phase: InstallPhase
    ) -> InstallJobReceipt:
        harness.events.append(f"cleanup_barrier:{phase.value}")
        harness.cleanup_barrier_arguments.append((job_id, revision, phase))
        return await verify_cleanup(job_id, revision, phase)

    monkeypatch.setattr(manager, "async_verify_mutation_barrier", mutation_barrier)
    monkeypatch.setattr(manager, "async_verify_cleanup_barrier", cleanup_barrier)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    execution_digest = sha256()
    execution_digest.update(b"ha-paneld-install-execution-v1\0")
    execution_digest.update(bytes.fromhex(receipt.job_id))
    execution_digest.update(bytes.fromhex(receipt.plan_sha256))
    expected_execution_id = execution_digest.hexdigest()[:32]
    expected_staging_slot_id = sha256(
        b"ha-paneld-device-local-staging-slot-v1\0"
    ).hexdigest()[:32]
    expected_pinned = PinnedPanelTarget(
        original=PanelAddress(host="panel-one.local", port=8888),
        pinned=PanelAddress(host="192.168.250.23", port=8888),
    )
    expected_adb_target = AdbInstallTarget(
        address=PanelAddress(host="192.168.250.23", port=8888),
        serial="SERIAL-1",
        model="Test Panel",
        primary_abi="arm64-v8a",
        android_sdk=34,
    )
    expected_descriptor = InstallDescriptor(
        schema=receipt.artifact.descriptor_schema,
        release_tag=receipt.artifact.release_tag,
        version_name=receipt.artifact.version_name,
        version_code=receipt.artifact.version_code,
        apk_name=receipt.artifact.apk_name,
        apk_size=receipt.artifact.apk_size,
        apk_sha256=receipt.artifact.apk_sha256,
        package_id=receipt.artifact.package_id,
        signer_certificate_sha256=receipt.artifact.signer_certificate_sha256,
        min_sdk=receipt.artifact.min_sdk,
        supported_abis=receipt.artifact.supported_abis,
        database_compatibility=receipt.artifact.database_compatibility,
        launch_component=receipt.artifact.launch_component,
    )
    expected_release = ReleaseArtifact(
        tag=receipt.artifact.release_tag,
        version=receipt.artifact.version_name,
        apk_name=receipt.artifact.apk_name,
        apk_url=(
            "https://github.com/panel-assistant/android/releases/download/"
            "v0.1.0/panel-assistant-v0.1.0-manual-setup-required.apk"
        ),
        sha256=APK_SHA256,
        descriptor=expected_descriptor,
        protocol_min=5,
        protocol_max=5,
    )
    expected_staged = StagedApk(
        job_id=expected_staging_slot_id,
        remote_path=(
            f"/data/local/tmp/ha-paneld-install-{expected_staging_slot_id}.apk"
        ),
        apk_size=12_345,
        apk_sha256=APK_SHA256,
    )

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.health_checked_at is not None
    assert completed.result_code is None
    assert completed.consumed_entry_id is None
    assert harness.events.count("stage") == 1
    assert harness.events.count("install") == 1
    assert harness.events.count("launch") == 1
    assert harness.events.count("remote_cleanup:install_succeeded") == 1
    assert harness.events == [
        "credential",
        "pin",
        "credential",
        "preflight",
        "download",
        "pin",
        "credential",
        "preflight",
        "pin",
        "mutation_barrier:staging",
        "credential",
        "stage",
        "pin",
        "mutation_barrier:installing",
        "credential",
        "install",
        "local_cleanup",
        "pin",
        "cleanup_barrier:launching",
        "credential",
        "remote_cleanup:install_succeeded",
        "pin",
        "mutation_barrier:launching",
        "credential",
        "launch",
        "health_client",
        "pin",
        "health",
        # Proven healthy, the install asks the panel about its setup so it can
        # hand over Home Assistant's address, over the same client. This harness
        # reports setup finished, so it asks and then says nothing further:
        # a set-up panel keeps its display size, so no sizing is read either.
        "setup_state",
        "pin",
        "mutation_barrier:health_check",
        "credential",
        "display_settlement",
    ]
    assert harness.pin_arguments == [expected_pinned] * 8
    assert all(pin.original.host == "panel-one.local" for pin in harness.pin_arguments)
    assert all(pin.pinned.host == "192.168.250.23" for pin in harness.pin_arguments)
    assert harness.preflight_arguments == [
        (expected_adb_target, harness.credentials[1].signer, expected_descriptor),
        (expected_adb_target, harness.credentials[2].signer, expected_descriptor),
    ]
    assert harness.download_arguments == [(expected_release, expected_execution_id)]
    downloaded_release = harness.download_arguments[0][0]
    assert downloaded_release.tag == receipt.artifact.release_tag
    assert downloaded_release.version == receipt.artifact.version_name
    assert downloaded_release.apk_name == receipt.artifact.apk_name
    assert downloaded_release.apk_url == (
        "https://github.com/panel-assistant/android/releases/download/"
        f"{receipt.artifact.release_tag}/{receipt.artifact.apk_name}"
    )
    assert downloaded_release.sha256 == receipt.artifact.apk_sha256
    assert downloaded_release.descriptor == expected_descriptor
    assert harness.stage_arguments == [
        (
            expected_adb_target,
            harness.credentials[3].signer,
            expected_descriptor,
            expected_staging_slot_id,
            Path(f"/private/{expected_execution_id}.apk"),
            AdbRootMode.ROOTLESS,
        )
    ]
    assert harness.install_arguments == [
        (
            expected_adb_target,
            harness.credentials[4].signer,
            expected_descriptor,
            expected_staging_slot_id,
            AdbRootMode.ROOTLESS,
        )
    ]
    assert harness.local_cleanup_job_ids == [expected_execution_id]
    assert harness.remote_cleanup_arguments == [
        (
            expected_adb_target,
            harness.credentials[5].signer,
            expected_staged,
            DefiniteCleanupReason.INSTALL_SUCCEEDED,
            AdbRootMode.ROOTLESS,
        )
    ]
    assert harness.launch_arguments == [
        (
            expected_adb_target,
            harness.credentials[6].signer,
            expected_descriptor,
            AdbRootMode.ROOTLESS,
        )
    ]
    assert harness.health_client_addresses == [expected_pinned.pinned]
    assert harness.mutation_barrier_arguments == [
        (receipt.job_id, 7, InstallPhase.STAGING),
        (receipt.job_id, 8, InstallPhase.INSTALLING),
        (receipt.job_id, 10, InstallPhase.LAUNCHING),
        (receipt.job_id, 11, InstallPhase.HEALTH_CHECK),
    ]
    assert harness.cleanup_barrier_arguments == [
        (receipt.job_id, 10, InstallPhase.LAUNCHING)
    ]
    add_entry.assert_not_awaited()


async def test_original_hostname_is_never_given_to_an_adb_operation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DNS is revalidated separately while ADB receives only the frozen IP."""
    manager = InstallJobManager(hass)
    selected_target = InstallTarget(
        address="panel-one.local",
        pinned_address="192.168.250.23",
        adb_serial="SERIAL-1",
        model="Test Panel",
        primary_abi="arm64-v8a",
        android_sdk=34,
    )
    selected_artifact = artifact()
    receipt, created = await manager.async_create_or_join(
        selected_target,
        selected_artifact,
        install_plan_sha256(selected_target, selected_artifact, CREDENTIAL_ID),
        CREDENTIAL_ID,
    )
    assert created
    harness = Harness(monkeypatch)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    adb_targets = [arguments[0] for arguments in harness.preflight_arguments]
    adb_targets.extend(arguments[0] for arguments in harness.stage_arguments)
    adb_targets.extend(arguments[0] for arguments in harness.install_arguments)
    adb_targets.extend(arguments[0] for arguments in harness.remote_cleanup_arguments)
    adb_targets.extend(arguments[0] for arguments in harness.launch_arguments)
    assert adb_targets
    assert all(
        adb_target.address.host == "192.168.250.23" for adb_target in adb_targets
    )
    assert all(
        pinned.original.host == "panel-one.local"
        and pinned.pinned.host == "192.168.250.23"
        for pinned in harness.pin_arguments
    )
    assert harness.health_client_addresses[0].host == "192.168.250.23"
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_concurrent_ensure_owns_exactly_one_worker_and_actuator_call(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent config flows cannot duplicate the staged or installed mutation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    executor = InstallExecutor(hass, manager)

    tasks = await asyncio.gather(
        *(executor.async_ensure_job(receipt.job_id) for _index in range(40))
    )
    assert tasks[0] is not None
    assert all(task is tasks[0] for task in tasks)
    await harness.stage_entered.wait()
    assert harness.events.count("stage") == 1

    harness.stage_release.set()
    await tasks[0]
    assert harness.events.count("stage") == 1
    assert harness.events.count("install") == 1
    assert harness.events.count("launch") == 1


@pytest.mark.parametrize(
    ("rejected_phase", "stage_calls", "install_calls"),
    [
        (InstallPhase.STAGING, 0, 0),
        (InstallPhase.INSTALLING, 1, 0),
        (InstallPhase.LAUNCHING, 1, 1),
    ],
)
async def test_rejected_mutation_barrier_precedes_and_blocks_exact_actuator(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    rejected_phase: InstallPhase,
    stage_calls: int,
    install_calls: int,
) -> None:
    """A durable barrier rejection occurs before its one external mutation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    verify = manager.async_verify_mutation_barrier

    async def rejecting_barrier(
        job_id: str, revision: int, phase: InstallPhase
    ) -> InstallJobReceipt:
        harness.events.append(f"mutation_barrier:{phase.value}")
        if phase is rejected_phase:
            raise InstallJobTransitionError
        return await verify(job_id, revision, phase)

    monkeypatch.setattr(manager, "async_verify_mutation_barrier", rejecting_barrier)
    executor = InstallExecutor(hass, manager)

    paused = await executor.async_wait(receipt.job_id)

    assert paused.phase is rejected_phase
    assert harness.events.count("stage") == stage_calls
    assert harness.events.count("install") == install_calls
    assert "launch" not in harness.events
    assert (
        harness.events.index(f"mutation_barrier:{rejected_phase.value}")
        == len(harness.events) - 1
    )
    assert await executor.async_ensure_job(receipt.job_id) is None


async def test_rejected_cleanup_barrier_blocks_remote_rm_and_launch(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cleanup side effect occurs unless its fresh narrow barrier succeeds."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)

    async def rejecting_cleanup(
        _job_id: str, _revision: int, phase: InstallPhase
    ) -> InstallJobReceipt:
        harness.events.append(f"cleanup_barrier:{phase.value}")
        raise InstallJobTransitionError

    monkeypatch.setattr(manager, "async_verify_cleanup_barrier", rejecting_cleanup)
    executor = InstallExecutor(hass, manager)

    paused = await executor.async_wait(receipt.job_id)

    assert paused.phase is InstallPhase.LAUNCHING
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert "launch" not in harness.events
    assert harness.events[-1] == "cleanup_barrier:launching"
    assert await executor.async_ensure_job(receipt.job_id) is None


@pytest.mark.parametrize(
    ("operation", "durable_phase", "event"),
    [
        ("stage", InstallPhase.STAGING, "stage"),
        ("install", InstallPhase.INSTALLING, "install"),
        (
            "cleanup",
            InstallPhase.LAUNCHING,
            "remote_cleanup:install_succeeded",
        ),
        ("launch", InstallPhase.LAUNCHING, "launch"),
    ],
)
async def test_unexpected_post_actuator_fault_cannot_replay_in_same_process(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    durable_phase: InstallPhase,
    event: str,
) -> None:
    """Untyped faults preserve ambiguity and permanently stop this worker owner."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    fault = OSError("post-actuator close failed")
    if operation == "stage":
        harness.stage_error = fault
    elif operation == "install":
        harness.install_error = fault
    elif operation == "cleanup":
        harness.cleanup_error = fault
    else:
        harness.launch_error = fault
    executor = InstallExecutor(hass, manager)

    with pytest.raises(OSError, match="post-actuator close failed"):
        await executor.async_wait(receipt.job_id)

    persisted = await manager.async_get(receipt.job_id)
    assert persisted.phase is durable_phase
    assert persisted.result_code is None
    assert harness.events.count(event) == 1
    assert await executor.async_ensure_job(receipt.job_id) is None
    assert harness.events.count(event) == 1

    restarted = InstallJobManager(hass)
    cleanup_count = harness.events.count("local_cleanup")
    quarantined = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)
    assert harness.events.count("local_cleanup") == cleanup_count + 1
    assert harness.events.count(event) == 1
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_cancelled_waiter_does_not_cancel_detached_worker(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing config flow progress leaves its process-wide worker alive."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    executor = InstallExecutor(hass, manager)
    waiter = asyncio.create_task(executor.async_wait(receipt.job_id))
    await harness.stage_entered.wait()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    running = await executor.async_ensure_job(receipt.job_id)
    assert running is not None and not running.cancelled()

    harness.stage_release.set()
    await running
    current = await manager.async_get(receipt.job_id)
    assert current.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.APPROVED,
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.INSTALLED,
        InstallPhase.HEALTH_CHECK,
    ],
)
async def test_new_process_resumes_every_safe_phase(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """Every replay-safe durable phase reaches the unclaimed health boundary."""
    receipt, restarted = await seed_phase(hass, phase)
    Harness(monkeypatch)

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.mark.parametrize(
    "phase", [InstallPhase.STAGING, InstallPhase.INSTALLING, InstallPhase.LAUNCHING]
)
async def test_new_process_quarantines_every_ambiguous_phase_without_adb(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """A crash boundary is never replayed by the new process worker."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert completed.failure_stage is phase
    assert completed.result_subcode == "job:interrupted_mutation"
    assert harness.events == ["local_cleanup"]
    assert "stage" not in harness.events
    assert "install" not in harness.events
    assert "launch" not in harness.events


async def test_worker_cancellation_leaves_durable_phase_for_new_process_claim(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HA shutdown is not persisted as a definite failure or cancellation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    executor = InstallExecutor(hass, manager)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.stage_entered.wait()

    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    persisted = await manager.async_get(receipt.job_id)
    assert persisted.phase is InstallPhase.STAGING
    assert persisted.result_code is None
    assert await executor.async_ensure_job(receipt.job_id) is None

    restarted = InstallJobManager(hass)
    quarantined = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)
    assert harness.events.count("local_cleanup") == 1
    assert harness.events.count("stage") == 1
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED


async def test_restart_removes_real_local_ready_before_ambiguous_quarantine(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash-held APK is deleted on disk before the receipt becomes terminal."""
    receipt, restarted = await seed_phase(hass, InstallPhase.INSTALLING)
    execution_id = install_executor._execution_id(receipt)
    ready = Path(
        hass.config.path(
            ".storage", "panel_assistant.install_artifacts", f"{execution_id}.apk"
        )
    )
    await hass.async_add_executor_job(seed_crash_partial, ready)
    real_cleanup = install_executor.async_cleanup_install_artifact
    harness = Harness(monkeypatch)
    monkeypatch.setattr(
        install_executor, "async_cleanup_install_artifact", real_cleanup
    )

    quarantined = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert not await hass.async_add_executor_job(ready.exists)
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert "stage" not in harness.events
    assert "install" not in harness.events
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)


@pytest.mark.parametrize(
    ("phase", "suffix"),
    [
        (InstallPhase.DOWNLOADING, ".apk.part"),
        (InstallPhase.ARTIFACT_READY, ".apk"),
    ],
)
async def test_exhausted_safe_claim_cleans_real_custody_before_quarantine(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
    suffix: str,
) -> None:
    """Attempt exhaustion cannot terminalize while reproducible bytes remain."""
    receipt, _ = await seed_attempt_ceiling(hass, phase)
    execution_id = install_executor._execution_id(receipt)
    custody = Path(
        hass.config.path(
            ".storage", "panel_assistant.install_artifacts", f"{execution_id}{suffix}"
        )
    )
    await hass.async_add_executor_job(seed_crash_partial, custody)
    real_cleanup = install_executor.async_cleanup_install_artifact
    harness = Harness(monkeypatch)
    monkeypatch.setattr(
        install_executor, "async_cleanup_install_artifact", real_cleanup
    )

    executor = await async_get_install_executor(hass)
    quarantined = await executor.async_wait(receipt.job_id)

    assert not await hass.async_add_executor_job(custody.exists)
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert quarantined.failure_stage is phase
    assert quarantined.result_subcode == "job:attempts_exhausted"
    assert quarantined.attempt == receipt.attempt
    assert harness.events == []


async def test_exhausted_claim_cleanup_failure_stays_active_and_cannot_replay(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed exact cleanup preserves durable truth and stops this process."""
    receipt, _ = await seed_attempt_ceiling(hass, InstallPhase.ARTIFACT_READY)
    execution_id = install_executor._execution_id(receipt)
    custody = Path(
        hass.config.path(
            ".storage", "panel_assistant.install_artifacts", f"{execution_id}.apk"
        )
    )
    await hass.async_add_executor_job(seed_crash_partial, custody)
    harness = Harness(monkeypatch)
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    executor = await async_get_install_executor(hass)

    paused = await executor.async_wait(receipt.job_id)

    assert await hass.async_add_executor_job(custody.exists)
    assert paused == receipt
    assert harness.events == ["local_cleanup"]
    assert await executor.async_ensure_job(receipt.job_id) is None


async def test_exhausted_claim_cleanup_cancellation_stays_active_and_cannot_replay(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HA shutdown during cleanup neither terminalizes nor restarts the worker."""
    receipt, _ = await seed_attempt_ceiling(hass, InstallPhase.ARTIFACT_READY)
    execution_id = install_executor._execution_id(receipt)
    custody = Path(
        hass.config.path(
            ".storage", "panel_assistant.install_artifacts", f"{execution_id}.apk"
        )
    )
    await hass.async_add_executor_job(seed_crash_partial, custody)
    harness = Harness(monkeypatch)
    harness.local_cleanup_entered = asyncio.Event()
    harness.local_cleanup_release = asyncio.Event()
    executor = await async_get_install_executor(hass)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.local_cleanup_entered.wait()

    worker.cancel()
    harness.local_cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker

    assert await hass.async_add_executor_job(custody.exists)
    assert await InstallJobManager(hass).async_get(receipt.job_id) == receipt
    assert harness.events == ["local_cleanup"]
    assert await executor.async_ensure_job(receipt.job_id) is None


async def test_exhausted_claim_revision_drift_requires_fresh_cleanup_confirmation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed receipt revision invalidates the first cleanup confirmation."""
    receipt, restarted = await seed_attempt_ceiling(hass, InstallPhase.ARTIFACT_READY)
    execution_id = install_executor._execution_id(receipt)
    custody = Path(
        hass.config.path(
            ".storage", "panel_assistant.install_artifacts", f"{execution_id}.apk"
        )
    )
    await hass.async_add_executor_job(seed_crash_partial, custody)
    real_cleanup = install_executor.async_cleanup_install_artifact
    harness = Harness(monkeypatch)
    cleanup_revisions: list[int] = []

    async def cleanup_then_drift(selected_hass: HomeAssistant, job_id: str) -> None:
        current = await restarted.async_get(receipt.job_id)
        cleanup_revisions.append(current.revision)
        await real_cleanup(selected_hass, job_id)
        if len(cleanup_revisions) == 1:
            await restarted.async_request_cancel(current.job_id, current.revision)

    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=restarted),
    )
    monkeypatch.setattr(
        install_executor, "async_cleanup_install_artifact", cleanup_then_drift
    )

    executor = await async_get_install_executor(hass)
    quarantined = await executor.async_wait(receipt.job_id)

    assert cleanup_revisions == [receipt.revision, receipt.revision + 1]
    assert not await hass.async_add_executor_job(custody.exists)
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert quarantined.cancel_requested
    assert harness.events == []


@pytest.mark.parametrize("failure", ["missing", "generation"])
async def test_authorizing_requires_the_exact_durable_credential(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """No network or ADB work starts before durable authorization succeeds."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    if failure == "missing":
        harness.credential_error_at = 1
    else:
        harness.credential_wrong_at = 1

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events == ["credential"]
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.AUTHORIZATION_FAILED


@pytest.mark.parametrize(
    ("credential_call", "blocked_event", "prior_count", "result"),
    [
        (2, "preflight", 0, InstallResultCode.TRANSPORT_FAILED),
        (3, "preflight", 1, InstallResultCode.TRANSPORT_FAILED),
        (4, "stage", 0, InstallResultCode.TRANSPORT_FAILED),
        (5, "install", 0, InstallResultCode.VERIFICATION_REQUIRED),
        (
            6,
            "remote_cleanup:install_succeeded",
            0,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
        (7, "launch", 0, InstallResultCode.LAUNCH_FAILED),
    ],
)
async def test_credential_generation_is_reloaded_before_every_adb_action(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    credential_call: int,
    blocked_event: str,
    prior_count: int,
    result: InstallResultCode,
) -> None:
    """A rotated durable key closes the next ADB boundary before the operation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.credential_wrong_at = credential_call

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count(blocked_event) == prior_count
    assert completed.result_code is result
    if result in {
        InstallResultCode.AMBIGUOUS_MUTATION,
        InstallResultCode.VERIFICATION_REQUIRED,
    }:
        assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    else:
        assert completed.phase is InstallPhase.FAILED


@pytest.mark.parametrize(
    ("credential_call", "blocked_event", "prior_count", "phase", "result"),
    [
        (
            2,
            "preflight",
            0,
            InstallPhase.FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        ),
        (
            3,
            "preflight",
            1,
            InstallPhase.FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        ),
        (
            4,
            "stage",
            0,
            InstallPhase.FAILED,
            InstallResultCode.TRANSPORT_FAILED,
        ),
        (
            5,
            "install",
            0,
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
        (
            6,
            "remote_cleanup:install_succeeded",
            0,
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
        (
            7,
            "launch",
            0,
            InstallPhase.FAILED,
            InstallResultCode.LAUNCH_FAILED,
        ),
    ],
)
async def test_missing_or_nonprivate_credential_never_reaches_next_adb_action(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    credential_call: int,
    blocked_event: str,
    prior_count: int,
    phase: InstallPhase,
    result: InstallResultCode,
) -> None:
    """Deletion and permission failures share the fail-closed durable authority."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.credential_error_at = credential_call

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count(blocked_event) == prior_count
    assert completed.phase is phase
    assert completed.result_code is result


async def test_pin_drift_before_stage_prevents_the_mutation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Original-name rebinding is caught again immediately before staging."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.pin_error_at = 3

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.TRANSPORT_FAILED


async def test_changed_pin_value_before_stage_prevents_the_mutation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolver returning any pin other than the frozen target fails closed."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.pin_wrong_at = 3

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.TRANSPORT_FAILED


async def test_pin_drift_during_health_check_never_reports_healthy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin that moves after launch stops health from believing the reply."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    resolve = harness.async_pin

    async def _moved_after_launch(
        hass: HomeAssistant, pinned: PinnedPanelTarget
    ) -> PinnedPanelTarget:
        if "launch" in harness.events:
            harness.pin_wrong_at = harness.pin_calls + 1
        return await resolve(hass, pinned)

    monkeypatch.setattr(
        install_executor, "async_revalidate_install_target", _moved_after_launch
    )

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    # Whatever answers at the moved address is never asked or told anything.
    assert "health" not in harness.events
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert completed.result_subcode == "network:pinned_target_removed"


@pytest.mark.parametrize(
    ("initial", "changed"),
    [
        (AdbRootMode.ROOTLESS, AdbRootMode.ROOT_ADBD),
        (AdbRootMode.ROOT_SU, AdbRootMode.ROOTLESS),
        (AdbRootMode.ROOTLESS, AdbRootMode.ROOT_SU),
    ],
)
async def test_root_posture_drift_fails_before_staging(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    initial: AdbRootMode,
    changed: AdbRootMode,
) -> None:
    """Fresh serial and root posture must match the earlier clean admission."""
    manager = InstallJobManager(hass)
    first = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.preflight_root_modes = [initial, changed]

    completed = await InstallExecutor(hass, manager).async_wait(first.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.PREFLIGHT_REJECTED


@pytest.mark.parametrize("root_mode", list(AdbRootMode))
async def test_admitted_root_posture_is_bound_to_every_mutation_primitive(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, root_mode: AdbRootMode
) -> None:
    """The same exact root admission is passed into every later ADB connection."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.preflight_root_modes = [
        root_mode,
        root_mode,
    ]

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.stage_arguments[0][-1] is root_mode
    assert harness.install_arguments[0][-1] is root_mode
    assert harness.remote_cleanup_arguments[0][-1] is root_mode
    assert harness.launch_arguments[0][-1] is root_mode
    assert completed.preflight_root_mode == root_mode.value
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.mark.parametrize(
    ("operation", "phase", "result"),
    [
        ("stage", InstallPhase.FAILED, InstallResultCode.TRANSPORT_FAILED),
        (
            "install",
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
        (
            "cleanup",
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
        (
            "launch",
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
    ],
)
async def test_same_connection_root_drift_fails_closed_before_mutation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    phase: InstallPhase,
    result: InstallResultCode,
) -> None:
    """A mutation primitive's own fresh root mismatch is never ignored."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    error = InstallAdbError(InstallAdbErrorCode.ROOT_MODE_CHANGED)
    if operation == "stage":
        harness.stage_error = error
    elif operation == "install":
        harness.install_error = error
    elif operation == "cleanup":
        harness.cleanup_error = error
    else:
        harness.launch_error = error

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is phase
    assert completed.result_code is result
    if operation in {"stage", "install"}:
        assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    if operation != "launch":
        assert "launch" not in harness.events


async def test_same_connection_target_change_at_launch_requires_recovery(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed app whose device identity changed at launch is not a clean
    failure."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.launch_error = InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert completed.result_subcode == "adb:target_changed"


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
    ],
)
async def test_safe_resume_compares_fresh_root_posture_with_durable_admission(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """A restart cannot forget the root posture admitted before download."""
    receipt, restarted = await seed_phase(hass, phase)
    assert receipt.preflight_root_mode == AdbRootMode.ROOTLESS.value
    harness = Harness(monkeypatch)
    harness.preflight_root_modes = [AdbRootMode.ROOT_ADBD]

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.PREFLIGHT_REJECTED


async def test_new_process_cleans_one_stale_partial_then_resumes_download(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process-death partial is exact-owned cleanup, not a terminal BUSY."""
    receipt, restarted = await seed_phase(hass, InstallPhase.DOWNLOADING)
    execution_digest = sha256()
    execution_digest.update(b"ha-paneld-install-execution-v1\0")
    execution_digest.update(bytes.fromhex(receipt.job_id))
    execution_digest.update(bytes.fromhex(receipt.plan_sha256))
    execution_id = execution_digest.hexdigest()[:32]
    artifact_directory = Path(
        hass.config.path(".storage", "panel_assistant.install_artifacts")
    )
    partial = artifact_directory / f"{execution_id}.apk.part"
    await hass.async_add_executor_job(seed_crash_partial, partial)
    real_download = install_executor.async_download_install_artifact
    real_cleanup = install_executor.async_cleanup_install_artifact
    harness = Harness(monkeypatch)
    download_calls = 0
    cleanup_observations: list[bool] = []

    async def crash_shaped_download(
        selected_hass: HomeAssistant,
        session: object,
        release: ReleaseArtifact,
        job_id: str,
    ) -> CustodiedArtifact:
        nonlocal download_calls
        download_calls += 1
        if download_calls == 1:
            return await real_download(selected_hass, session, release, job_id)
        return await harness.async_download(selected_hass, session, release, job_id)

    async def exact_cleanup(selected_hass: HomeAssistant, job_id: str) -> None:
        cleanup_observations.append(
            await selected_hass.async_add_executor_job(partial.exists)
        )
        await real_cleanup(selected_hass, job_id)

    monkeypatch.setattr(
        install_executor, "async_download_install_artifact", crash_shaped_download
    )
    monkeypatch.setattr(
        install_executor, "async_cleanup_install_artifact", exact_cleanup
    )

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert download_calls == 2
    assert cleanup_observations == [True, False]
    assert not await hass.async_add_executor_job(partial.exists)
    assert harness.events.count("stage") == 1
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
    ],
)
async def test_one_stale_busy_is_cleaned_and_resumed_from_every_safe_phase(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """A crash partial is recoverable even after artifact-ready persistence."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)
    attempts = 0

    async def busy_once(
        selected_hass: HomeAssistant,
        session: object,
        release: ReleaseArtifact,
        job_id: str,
    ) -> CustodiedArtifact:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ArtifactCustodyError(ArtifactErrorCode.BUSY)
        return await harness.async_download(selected_hass, session, release, job_id)

    monkeypatch.setattr(install_executor, "async_download_install_artifact", busy_once)

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert attempts == 2
    assert harness.events.count("local_cleanup") == 2
    assert harness.events.count("stage") == 1
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
    ],
)
async def test_repeated_download_busy_is_bounded_and_paused(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """One worker removes at most one exact stale partial before stopping."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)
    harness.download_error = ArtifactCustodyError(ArtifactErrorCode.BUSY)
    executor = InstallExecutor(hass, restarted)

    paused = await executor.async_wait(receipt.job_id)

    assert harness.events.count("download") == 2
    assert harness.events.count("local_cleanup") == 1
    assert paused.phase is phase
    assert paused.result_code is None
    assert await executor.async_ensure_job(receipt.job_id) is None


@pytest.mark.parametrize("preflight_call", [1, 2])
async def test_identity_drift_at_either_clean_preflight_fails_closed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    preflight_call: int,
) -> None:
    """A changed serial is rejected both initially and before the first mutation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.preflight_wrong_at = preflight_call

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == (preflight_call == 2)
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.PREFLIGHT_REJECTED


@pytest.mark.parametrize("field", ["model", "primary_abi", "android_sdk"])
async def test_each_target_fact_drift_at_revalidation_fails_closed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """Model, primary ABI, and SDK are all exact pre-mutation bindings."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.preflight_wrong_at = 2
    harness.preflight_wrong_field = field

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.PREFLIGHT_REJECTED


@pytest.mark.parametrize(
    "mutation", ["job_id", "size", "sha256", "path_type", "path_empty"]
)
async def test_artifact_mismatch_is_rejected_without_staging(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """The executor cross-checks custody output against frozen signed facts."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.download_mutation = mutation

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "stage" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.ARTIFACT_REJECTED


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
    ],
)
@pytest.mark.parametrize(
    ("error_code", "result"),
    [
        (ArtifactErrorCode.DIGEST_MISMATCH, InstallResultCode.ARTIFACT_REJECTED),
        (ArtifactErrorCode.TIMEOUT, InstallResultCode.TRANSPORT_FAILED),
    ],
)
async def test_artifact_error_taxonomy_is_stable_across_safe_resume_phases(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
    error_code: ArtifactErrorCode,
    result: InstallResultCode,
) -> None:
    """A custody failure cannot change meaning merely because HA restarted."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)
    harness.download_error = ArtifactCustodyError(error_code)

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert harness.events.count("local_cleanup") == 1
    assert "stage" not in harness.events
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is result


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
    ],
)
@pytest.mark.parametrize(
    "error_code",
    [code for code in ArtifactErrorCode if code is not ArtifactErrorCode.BUSY],
)
async def test_resumed_artifact_failure_keeps_its_internal_code_and_source_stage(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    phase: InstallPhase,
    error_code: ArtifactErrorCode,
) -> None:
    """Every terminal custody code survives a safe-phase resume."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)
    harness.download_error = ArtifactCustodyError(error_code)

    with caplog.at_level(logging.WARNING, logger=install_jobs.__name__):
        completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)
    loaded = await InstallJobManager(hass).async_get(receipt.job_id)

    assert harness.events.count("download") == 1
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is loaded.phase is InstallPhase.FAILED
    expected_result = (
        InstallResultCode.TRANSPORT_FAILED
        if error_code in {ArtifactErrorCode.TIMEOUT, ArtifactErrorCode.DOWNLOAD_FAILED}
        else InstallResultCode.ARTIFACT_REJECTED
    )
    assert loaded.result_code is expected_result
    assert asdict(loaded).get("result_subcode") == f"artifact:{error_code.value}"
    assert asdict(loaded).get("failure_stage") is phase
    warnings = [
        record
        for record in caplog.records
        if record.name in {install_jobs.__name__, install_executor.__name__}
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].name == install_jobs.__name__
    for detail in ("panel-one.local", phase.value, f"artifact:{error_code.value}"):
        assert detail in warnings[0].getMessage()


@pytest.mark.parametrize(
    ("operation", "error_code", "terminal_phase", "result_code", "source_phase"),
    [
        (
            "stage",
            InstallAdbErrorCode.STAGING_PATH_OCCUPIED,
            InstallPhase.FAILED,
            InstallResultCode.ARTIFACT_REJECTED,
            InstallPhase.STAGING,
        ),
        (
            "stage",
            InstallAdbErrorCode.STAGE_INTERRUPTED,
            InstallPhase.FAILED,
            InstallResultCode.TRANSPORT_FAILED,
            InstallPhase.STAGING,
        ),
        (
            "install",
            InstallAdbErrorCode.INSTALL_AMBIGUOUS,
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.AMBIGUOUS_MUTATION,
            InstallPhase.INSTALLING,
        ),
        (
            "cleanup",
            InstallAdbErrorCode.CLEANUP_AMBIGUOUS,
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.AMBIGUOUS_MUTATION,
            InstallPhase.LAUNCHING,
        ),
        (
            "launch",
            InstallAdbErrorCode.LAUNCH_AMBIGUOUS,
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.AMBIGUOUS_MUTATION,
            InstallPhase.LAUNCHING,
        ),
    ],
)
async def test_adb_terminal_receipt_logs_one_coded_failure_at_its_actual_stage(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    error_code: InstallAdbErrorCode,
    terminal_phase: InstallPhase,
    result_code: InstallResultCode,
    source_phase: InstallPhase,
) -> None:
    """The worker keeps the coded refusal or ambiguity without replaying an actuator."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    setattr(harness, f"{operation}_error", InstallAdbError(error_code))

    with caplog.at_level(logging.WARNING, logger=install_jobs.__name__):
        completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    loaded = await InstallJobManager(hass).async_get(receipt.job_id)

    event = operation if operation != "cleanup" else "remote_cleanup:install_succeeded"
    assert harness.events.count(event) == 1
    assert loaded.phase is completed.phase is terminal_phase
    assert loaded.result_code is result_code
    assert asdict(loaded).get("result_subcode") == f"adb:{error_code.value}"
    assert asdict(loaded).get("failure_stage") is source_phase
    warnings = [
        record
        for record in caplog.records
        if record.name in {install_jobs.__name__, install_executor.__name__}
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].name == install_jobs.__name__
    for detail in (
        "panel-one.local",
        source_phase.value,
        f"adb:{error_code.value}",
    ):
        assert detail in warnings[0].getMessage()


@pytest.mark.parametrize("error_code", list(InstallAdbErrorCode))
async def test_every_adb_failure_code_survives_the_receipt_boundary(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error_code: InstallAdbErrorCode,
) -> None:
    """Every coded ADB failure remains identifiable after persistence."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.preflight_error = InstallAdbError(error_code)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    loaded = await InstallJobManager(hass).async_get(receipt.job_id)

    assert harness.events.count("preflight") == 1
    assert "download" not in harness.events
    assert completed.phase is loaded.phase is InstallPhase.FAILED
    assert loaded.failure_stage is InstallPhase.PREFLIGHT
    assert loaded.result_subcode == f"adb:{error_code.value}"


@pytest.mark.parametrize("error_code", list(InstallNetworkErrorCode))
async def test_pinned_target_failure_retains_its_network_subcode(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_code: InstallNetworkErrorCode,
) -> None:
    """A lost network pin is diagnosable after the failed receipt is reloaded."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.pin_error_at = 1
    harness.pin_error_code = error_code

    with caplog.at_level(logging.WARNING, logger=install_jobs.__name__):
        completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    loaded = await InstallJobManager(hass).async_get(receipt.job_id)

    assert harness.events.count("pin") == 1
    assert "stage" not in harness.events
    assert completed.phase is loaded.phase is InstallPhase.FAILED
    assert loaded.result_code is InstallResultCode.TRANSPORT_FAILED
    assert asdict(loaded).get("result_subcode") == (f"network:{error_code.value}")
    assert asdict(loaded).get("failure_stage") is InstallPhase.PREFLIGHT
    warnings = [
        record
        for record in caplog.records
        if record.name in {install_jobs.__name__, install_executor.__name__}
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].name == install_jobs.__name__
    for detail in (
        "panel-one.local",
        InstallPhase.PREFLIGHT.value,
        f"network:{error_code.value}",
    ):
        assert detail in warnings[0].getMessage()


@pytest.mark.parametrize(
    "failure",
    [InstallJobRevisionError(), InstallJobStoreError(), InstallJobTransitionError()],
)
async def test_worker_logs_lost_receipt_authority_once_without_replaying(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    """A swallowed durable-authority error leaves a warning and no invented outcome."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    executor = InstallExecutor(hass, manager)

    with (
        patch.object(manager, "async_transition", side_effect=failure),
        caplog.at_level(logging.WARNING, logger=install_executor.__name__),
    ):
        stopped = await executor.async_wait(receipt.job_id)

    assert stopped.phase is InstallPhase.APPROVED
    assert stopped.result_code is None
    assert harness.events == []
    assert await executor.async_ensure_job(receipt.job_id) is None
    warnings = [
        record
        for record in caplog.records
        if record.name == install_executor.__name__
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert receipt.job_id in warnings[0].getMessage()


@pytest.mark.parametrize("failure", ["pin", "credential", "identity"])
async def test_revalidation_failure_removes_the_authenticated_local_artifact(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Every definite rejection after download cleans only this job's file."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    if failure == "pin":
        harness.pin_error_at = 2
    elif failure == "credential":
        harness.credential_error_at = 3
    else:
        harness.preflight_wrong_at = 2

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    frozen = install_executor._frozen_execution(receipt)

    assert harness.local_cleanup_job_ids == [frozen.execution_id]
    assert "stage" not in harness.events
    assert completed.phase is InstallPhase.FAILED


async def test_failed_local_cleanup_pauses_safe_phase_for_later_retry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local residue is neither hidden by failure nor made crash-ambiguous."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.download_mutation = "sha256"
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    executor = InstallExecutor(hass, manager)

    paused = await executor.async_wait(receipt.job_id)

    assert paused.phase is InstallPhase.DOWNLOADING
    assert paused.result_code is None
    assert "stage" not in harness.events
    await asyncio.sleep(0)
    harness.download_mutation = None
    harness.local_cleanup_error = None

    completed = await executor.async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("stage") == 1


async def test_definite_install_refusal_cleans_exact_stage_then_fails(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A definite package-manager refusal permits only exact job-owned cleanup."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.install_outcome = InstallOutcome.REFUSED

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    frozen = install_executor._frozen_execution(receipt)
    staged = install_executor._staged(receipt.artifact)

    assert harness.events.count("remote_cleanup:install_refused") == 1
    assert harness.remote_cleanup_arguments == [
        (
            frozen.adb_target,
            harness.credentials[5].signer,
            staged,
            DefiniteCleanupReason.INSTALL_REFUSED,
            AdbRootMode.ROOTLESS,
        )
    ]
    assert harness.local_cleanup_job_ids == [frozen.execution_id]
    assert "launch" not in harness.events
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.INSTALL_FAILED


@pytest.mark.parametrize(
    ("cleanup_error", "result"),
    [
        (
            InstallAdbError(InstallAdbErrorCode.CLEANUP_AMBIGUOUS),
            InstallResultCode.AMBIGUOUS_MUTATION,
        ),
        (
            InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE),
            InstallResultCode.VERIFICATION_REQUIRED,
        ),
    ],
)
async def test_incomplete_remote_cleanup_quarantines_instead_of_launching(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_error: InstallAdbError,
    result: InstallResultCode,
) -> None:
    """Unknown or retained remote staging state is never hidden by a launch."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.cleanup_error = cleanup_error

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "launch" not in harness.events
    assert harness.events.count("local_cleanup") == 2
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is result


async def test_unverified_stage_is_quarantined_without_remote_cleanup(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete copy with the wrong bytes is not retried by the executor."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_error = InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "install" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.AMBIGUOUS_MUTATION


async def test_definite_pre_push_stage_refusal_cleans_local_and_fails(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A definite occupied staging path is not mislabeled as an ambiguous push."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_error = InstallAdbError(InstallAdbErrorCode.STAGING_PATH_OCCUPIED)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("local_cleanup") == 1
    assert "install" not in harness.events
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.ARTIFACT_REJECTED


def _stage_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Harness, FakePanel, InstallJobArtifact, bytes]:
    """Run the real ADB staging, install and cleanup against one fake panel."""
    body = bytes(range(256)) * 48
    selected = replace(
        artifact(), apk_sha256=sha256(body).hexdigest(), apk_size=len(body)
    )
    harness = Harness(monkeypatch)
    harness.local_apk = (tmp_path, body)
    for name in (
        "async_stage_apk",
        "async_install_staged_apk",
        "async_cleanup_staged_apk",
    ):
        monkeypatch.setattr(install_executor, name, getattr(install_adb, name))
    panel = FakePanel()
    panel.install(monkeypatch)
    return harness, panel, selected, body


_SLOT = (
    f"/data/local/tmp/ha-paneld-install-{install_executor._REMOTE_STAGING_SLOT_ID}.apk"
)


async def test_a_copy_that_stalls_does_not_lock_the_panel_out_of_a_retry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A copy that stops part-way is a retryable failure, and the retry installs.

    The link stays down after the stall, so the partial copy is still on the
    panel when the owner tries again with the same address.
    """
    _harness, panel, selected, body = _stage_on(monkeypatch, tmp_path)
    manager = InstallJobManager(hass)
    panel.stall_next_push = True

    first_receipt = await create_job(manager, selected)
    first = await InstallExecutor(hass, manager).async_wait(first_receipt.job_id)

    assert first.phase is InstallPhase.FAILED
    assert first.result_code is InstallResultCode.TRANSPORT_FAILED
    assert panel.installed is None
    assert 0 < len(panel.files[_SLOT]) < len(body)

    panel.reachable = True
    second_receipt = await create_job(manager, selected)
    second = await InstallExecutor(hass, manager).async_wait(second_receipt.job_id)

    assert second.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert panel.installed == body
    assert panel.files == {}


async def test_a_stalled_copy_is_removed_when_the_panel_still_answers(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a stall the panel is asked once more, and its partial copy goes."""
    _harness, panel, _selected, _body = _stage_on(monkeypatch, tmp_path)
    manager = InstallJobManager(hass)
    panel.stall_next_push = True
    panel.drop_link_on_stall = False

    receipt = await create_job(manager, _selected)
    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.TRANSPORT_FAILED
    assert panel.installed is None
    assert panel.files == {}


async def test_mismatched_staged_receipt_purges_local_but_never_guesses_remote(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected stage result cannot authorize install or guessed cleanup."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_mismatch = True

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("stage") == 1
    assert "install" not in harness.events
    assert harness.events.count("local_cleanup") == 1
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.AMBIGUOUS_MUTATION


async def test_ambiguous_install_purges_local_but_retains_remote_stage(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown package outcome retains the bounded remote stage, not local bytes."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.install_error = InstallAdbError(InstallAdbErrorCode.INSTALL_AMBIGUOUS)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.stage_arguments[0][3] == install_executor._REMOTE_STAGING_SLOT_ID
    assert harness.install_arguments[0][3] == install_executor._REMOTE_STAGING_SLOT_ID
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert harness.events.count("local_cleanup") == 1
    assert "launch" not in harness.events
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.AMBIGUOUS_MUTATION


async def test_failed_recovery_cleanup_preserves_phase_and_cannot_replay(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local cleanup must succeed before quarantine, without retrying installation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.install_error = InstallAdbError(InstallAdbErrorCode.INSTALL_AMBIGUOUS)
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    executor = InstallExecutor(hass, manager)

    paused = await executor.async_wait(receipt.job_id)

    assert paused.phase is InstallPhase.INSTALLING
    assert paused.result_code is None
    assert harness.events.count("install") == 1
    assert harness.events.count("local_cleanup") == 1
    assert await executor.async_ensure_job(receipt.job_id) is None

    harness.local_cleanup_error = None
    restarted = InstallJobManager(hass)
    quarantined = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert harness.events.count("install") == 1
    assert harness.events.count("local_cleanup") == 2
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_worker_cancellation_during_recovery_cleanup_preserves_truth(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown during cleanup cannot invent quarantine or replay installation."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.install_error = InstallAdbError(InstallAdbErrorCode.INSTALL_AMBIGUOUS)
    harness.local_cleanup_entered = asyncio.Event()
    harness.local_cleanup_release = asyncio.Event()
    executor = InstallExecutor(hass, manager)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.local_cleanup_entered.wait()

    worker.cancel()
    harness.local_cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await worker

    persisted = await manager.async_get(receipt.job_id)
    assert persisted.phase is InstallPhase.INSTALLING
    assert persisted.result_code is None
    assert harness.events.count("install") == 1
    assert harness.events.count("local_cleanup") == 1
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert await executor.async_ensure_job(receipt.job_id) is None


@pytest.mark.parametrize("failure", ["credential", "adb"])
async def test_install_pre_mutation_failure_retains_only_bounded_remote_stage(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Recovery removes local custody but never guesses at remote disposition."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    if failure == "credential":
        harness.credential_wrong_at = 5
    else:
        harness.install_error = InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("stage") == 1
    assert not any(event.startswith("remote_cleanup:") for event in harness.events)
    assert harness.events.count("local_cleanup") == 1
    assert "launch" not in harness.events
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_ambiguous_launch_is_quarantined_after_exact_cleanup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown activity-manager result remains explicitly recoverable."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.launch_error = InstallAdbError(InstallAdbErrorCode.LAUNCH_AMBIGUOUS)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("remote_cleanup:install_succeeded") == 1
    assert harness.events.count("launch") == 1
    assert harness.events.count("local_cleanup") == 2
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.AMBIGUOUS_MUTATION


async def test_definite_launch_refusal_fails_after_exact_cleanup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical activity-manager refusal is a definite launch failure."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.launch_outcome = LaunchOutcome.REFUSED

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("remote_cleanup:install_succeeded") == 1
    assert harness.events.count("launch") == 1
    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.LAUNCH_FAILED


async def test_local_custody_cleanup_failure_after_install_requires_recovery(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A definitely installed package does not hide a failed private-file cleanup."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    harness.local_cleanup_error_at = 1

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert "launch" not in harness.events
    assert harness.events.count("local_cleanup") == 2
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_health_poll_is_bounded_and_never_claims_success_on_transport_failure(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated absent health stops after the fixed attempt budget."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.health_error = CannotConnectError()
    sleep = AsyncMock()
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.health_calls == install_executor._HEALTH_ATTEMPTS
    assert sleep.await_count == install_executor._HEALTH_ATTEMPTS - 1
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED


@pytest.mark.parametrize("home_state", ["blocked", "unknown", None])
async def test_healthy_new_install_requires_home_proof(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, home_state: str | None
) -> None:
    """The installed app answering HTTP is insufficient without HOME proof."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.home_ui_state = home_state
    monkeypatch.setattr(install_executor, "_HEALTH_ATTEMPTS", 2)
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.home_proof_calls == 2
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_version_mismatch_never_becomes_healthy_or_creates_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health from any other version remains a verification-required install."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    Harness(monkeypatch, expected_version="9.9.9")
    # Another version is waited out for the whole health budget.
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)
    add_entry = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_add", add_entry)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    add_entry.assert_not_awaited()


async def test_receipt_reconstruction_always_supplies_authenticated_descriptor(
    hass: HomeAssistant,
) -> None:
    """The executor accepts only an already authenticated durable receipt."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)

    frozen = install_executor._frozen_execution(receipt)

    assert frozen.release.descriptor is not None


async def test_durable_cancel_after_stage_uses_cleanup_barrier_then_cancels(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent cancel cleans a definitely staged path under fresh authority."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)

    async def request_cancel_after_push() -> None:
        current = await manager.async_get(receipt.job_id)
        await manager.async_request_cancel(current.job_id, current.revision)

    harness.stage_callback = request_cancel_after_push
    cleanup_barrier = AsyncMock(wraps=manager.async_verify_cleanup_barrier)
    monkeypatch.setattr(manager, "async_verify_cleanup_barrier", cleanup_barrier)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("stage") == 1
    assert harness.events.count("remote_cleanup:cancelled") == 1
    assert harness.stage_arguments[0][3] == install_executor._REMOTE_STAGING_SLOT_ID
    assert harness.remote_cleanup_arguments[0][2] == install_executor._staged(
        receipt.artifact
    )
    assert harness.events.count("local_cleanup") == 1
    assert "install" not in harness.events
    assert cleanup_barrier.await_count == 1
    assert completed.phase is InstallPhase.CANCELLED
    assert completed.result_code is InstallResultCode.CANCELLED_AFTER_STAGING_CLEANUP


async def test_ambiguous_cancel_cleanup_quarantines_and_purges_local_only(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uncertain exact rm preserves recovery truth without leaking local bytes."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)

    async def request_cancel_after_push() -> None:
        current = await manager.async_get(receipt.job_id)
        await manager.async_request_cancel(current.job_id, current.revision)

    harness.stage_callback = request_cancel_after_push
    harness.cleanup_error = InstallAdbError(InstallAdbErrorCode.CLEANUP_AMBIGUOUS)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.events.count("stage") == 1
    assert harness.events.count("remote_cleanup:cancelled") == 1
    assert harness.events.count("local_cleanup") == 1
    assert "install" not in harness.events
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.AMBIGUOUS_MUTATION


async def test_cancelled_downloading_receipt_cleans_possible_partial_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash-era partial is not orphaned when cancellation wins the restart."""
    receipt, restarted = await seed_phase(hass, InstallPhase.DOWNLOADING)
    receipt = await restarted.async_request_cancel(receipt.job_id, receipt.revision)
    harness = Harness(monkeypatch)

    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert harness.events == ["local_cleanup"]
    assert completed.phase is InstallPhase.CANCELLED
    assert completed.result_code is InstallResultCode.CANCELLED_BY_USER


async def test_cancel_cleanup_failure_keeps_safe_request_retryable(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed local cleanup cannot turn a requested cancel into definite failure."""
    receipt, restarted = await seed_phase(hass, InstallPhase.DOWNLOADING)
    receipt = await restarted.async_request_cancel(receipt.job_id, receipt.revision)
    harness = Harness(monkeypatch)
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    executor = InstallExecutor(hass, restarted)

    paused = await executor.async_wait(receipt.job_id)

    assert paused.phase is InstallPhase.DOWNLOADING
    assert paused.cancel_requested
    assert paused.result_code is None
    await asyncio.sleep(0)
    harness.local_cleanup_error = None

    completed = await executor.async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.CANCELLED
    assert completed.result_code is InstallResultCode.CANCELLED_BY_USER


async def test_resume_hook_is_explicit_and_getter_is_process_wide(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an importing loaded integration can invoke process-wide safe resume."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    manager_getter = AsyncMock()
    reconcile = AsyncMock()

    async def delayed_manager(_hass: HomeAssistant) -> InstallJobManager:
        await asyncio.sleep(0)
        return manager

    manager_getter.side_effect = delayed_manager
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        manager_getter,
    )
    monkeypatch.setattr(
        install_executor, "async_reconcile_install_artifacts", reconcile
    )

    first, second = await asyncio.gather(
        async_get_install_executor(hass), async_get_install_executor(hass)
    )
    assert first is second
    assert manager_getter.await_count == 1
    reconcile.assert_awaited_once_with(
        hass, frozenset({install_executor._execution_id(receipt)})
    )
    assert not first._tasks

    resumed = await first.async_resume_loaded_jobs()
    assert resumed == (receipt.job_id,)
    worker = await first.async_ensure_job(receipt.job_id)
    assert worker is not None
    await worker
    assert harness.events.count("stage") == 1


async def test_concurrent_loaded_entry_resume_does_not_clean_active_staging_worker(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated entry setup cannot unlink a singleton worker's live artifact."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    executor = await async_get_install_executor(hass)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.stage_entered.wait()

    resumed = await asyncio.gather(
        install_executor.async_resume_loaded_install_jobs(hass),
        install_executor.async_resume_loaded_install_jobs(hass),
    )

    assert resumed == [(), ()]
    assert harness.events.count("stage") == 1
    assert harness.events.count("local_cleanup") == 0
    assert await executor.async_ensure_job(receipt.job_id) is worker

    harness.stage_release.set()
    await worker
    completed = await manager.async_get(receipt.job_id)
    assert harness.events.count("stage") == 1
    assert harness.events.count("install") == 1
    assert harness.events.count("local_cleanup") == 1
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_concurrent_loaded_entry_resume_quarantines_restart_only_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent loaded entries idempotently reconcile one crashed mutation."""
    receipt, restarted = await seed_phase(hass, InstallPhase.INSTALLING)
    harness = Harness(monkeypatch)
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=restarted),
    )

    resumed = await asyncio.gather(
        install_executor.async_resume_loaded_install_jobs(hass),
        install_executor.async_resume_loaded_install_jobs(hass),
    )
    quarantined = await restarted.async_get(receipt.job_id)

    assert resumed == [(), ()]
    assert harness.events == ["local_cleanup"]
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED


async def test_failed_artifact_reconciliation_aborts_singleton_publication(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe orphan state cannot be hidden behind a partially published owner."""
    manager = InstallJobManager(hass)
    await create_job(manager)
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=manager),
    )
    reconcile = AsyncMock(
        side_effect=ArtifactCustodyError(ArtifactErrorCode.PATH_INVALID)
    )
    monkeypatch.setattr(
        install_executor, "async_reconcile_install_artifacts", reconcile
    )

    with pytest.raises(ArtifactCustodyError) as raised:
        await async_get_install_executor(hass)

    assert raised.value.code is ArtifactErrorCode.PATH_INVALID
    assert install_executor._EXECUTOR_DATA_KEY not in hass.data
    reconcile.side_effect = None
    executor = await async_get_install_executor(hass)
    assert isinstance(executor, InstallExecutor)
    assert reconcile.await_count == 2


async def test_cancelled_artifact_reconciliation_aborts_singleton_publication(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HA shutdown cannot publish an owner before its one-time GC completes."""
    manager = InstallJobManager(hass)
    await create_job(manager)
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=manager),
    )
    reconcile = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(
        install_executor, "async_reconcile_install_artifacts", reconcile
    )

    with pytest.raises(asyncio.CancelledError):
        await async_get_install_executor(hass)

    assert install_executor._EXECUTOR_DATA_KEY not in hass.data


async def test_artifact_reconciliation_retains_healthy_unclaimed_custody_id(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GC policy is receipt-generic and cannot silently invent a phase exception."""
    receipt, manager = await seed_phase(hass, InstallPhase.HEALTH_CHECK)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    receipt = await manager.async_transition(
        receipt.job_id,
        receipt.revision,
        InstallPhase.HEALTHY_UNCLAIMED,
        health_checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=manager),
    )
    reconcile = AsyncMock()
    monkeypatch.setattr(
        install_executor, "async_reconcile_install_artifacts", reconcile
    )

    await async_get_install_executor(hass)

    reconcile.assert_awaited_once_with(
        hass, frozenset({install_executor._execution_id(receipt)})
    )


async def test_artifact_reconciliation_does_not_retain_terminal_custody_id(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminal receipt retention cannot turn into local APK retention."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    receipt = await manager.async_transition(
        receipt.job_id, receipt.revision, InstallPhase.AUTHORIZING
    )
    receipt = await manager.async_transition(
        receipt.job_id,
        receipt.revision,
        InstallPhase.FAILED,
        result_code=InstallResultCode.AUTHORIZATION_FAILED,
    )
    monkeypatch.setattr(
        install_executor,
        "async_get_install_job_manager",
        AsyncMock(return_value=manager),
    )
    reconcile = AsyncMock()
    monkeypatch.setattr(
        install_executor, "async_reconcile_install_artifacts", reconcile
    )

    await async_get_install_executor(hass)

    reconcile.assert_awaited_once_with(hass, frozenset())


@pytest.mark.parametrize(
    "phase", [InstallPhase.STAGING, InstallPhase.INSTALLING, InstallPhase.LAUNCHING]
)
async def test_resume_hook_quarantines_but_does_not_resume_ambiguous_phase(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: InstallPhase,
) -> None:
    """Loaded-domain recovery never reports crash-ambiguous work as resumed."""
    receipt, restarted = await seed_phase(hass, phase)
    harness = Harness(monkeypatch)
    executor = InstallExecutor(hass, restarted)

    resumed = await executor.async_resume_loaded_jobs()
    quarantined = await restarted.async_get(receipt.job_id)

    assert resumed == ()
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert harness.events == ["local_cleanup"]
    assert "stage" not in harness.events
    assert "install" not in harness.events
    assert "launch" not in harness.events

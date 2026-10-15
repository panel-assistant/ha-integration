"""Finalizer leases and verified receipts for the clean-install executor.

Covers what happens once an install has proved itself healthy: the finalizer
lease has exactly one owner and is released once, a verified receipt is
consumed once, concurrent, cancelled or interrupted finalizers never leak or
double-release the lease, the final proof applies the installed-app rule, a
restart settles a healthy receipt when its entry loads, the handover to the
successor app, and the first-setup steps a freshly installed panel receives.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import install_executor, install_jobs
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelHealth,
    PanelSetupState,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_executor import (
    FinalizationOutcome,
    InstallExecutor,
    health_is_installed_app,
)
from custom_components.panel_assistant.install_jobs import (
    InstallArtifact,
    InstallJobManager,
    InstallJobReceipt,
    InstallPhase,
    InstallResultCode,
    install_plan_sha256,
)
from custom_components.panel_assistant.status import PanelStatus

from .install_executor_harness import (  # noqa: F401 - the store fixture is autouse
    CREDENTIAL_ID,
    Harness,
    artifact,
    create_job,
    emulate_home_assistant_store_file,
    seed_phase,
    target,
)


async def seed_healthy(
    hass: HomeAssistant,
) -> tuple[InstallJobReceipt, InstallJobManager]:
    """Persist one HEALTHY_UNCLAIMED receipt, as a finished worker leaves it."""
    receipt, manager = await seed_phase(hass, InstallPhase.HEALTH_CHECK)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    receipt = await manager.async_transition(
        receipt.job_id,
        receipt.revision,
        InstallPhase.HEALTHY_UNCLAIMED,
        health_checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    return receipt, manager


class _CountingLeases(dict[str, object]):
    """The executor's lease map, counting every lease actually released."""

    def __init__(self) -> None:
        super().__init__()
        self.released: list[str] = []

    def __delitem__(self, job_id: str) -> None:
        self.released.append(job_id)
        super().__delitem__(job_id)

    def pop(self, *args: object) -> object:  # type: ignore[override]
        raise AssertionError("leases are released through release_finalizer only")


def counting_executor(
    hass: HomeAssistant, manager: InstallJobManager
) -> tuple[InstallExecutor, _CountingLeases]:
    """Return an executor whose releases the test can count."""
    executor = InstallExecutor(hass, manager)
    leases = _CountingLeases()
    executor._finalizers = leases  # type: ignore[assignment]
    return executor, leases


@dataclass
class FinalProof:
    """The read-only final proof, with the health read optionally held open."""

    monkeypatch: pytest.MonkeyPatch
    package: str | None = None
    version: str | None = None
    hold_health: bool = False
    home_ui_state: str = "setup"

    def __post_init__(self) -> None:
        self.health_started = asyncio.Event()
        self.release_health = asyncio.Event()
        self.health_reads = 0
        proof = self

        class FakeClient:
            def __init__(self, _session: object, _address: object) -> None:
                pass

            async def async_get_health(self) -> PanelHealth:
                proof.health_reads += 1
                proof.health_started.set()
                if proof.hold_health:
                    await proof.release_health.wait()
                return PanelHealth(
                    version=proof.version or artifact().version_name,
                    panel_id="alpha",
                    build="1",
                    config_hash="1a2b3c4d",
                    package=proof.package,
                )

            async def async_get_status(
                self, *, home_proof: bool = False
            ) -> PanelStatus:
                assert home_proof
                return PanelStatus(
                    warning_count=0,
                    capability_count=0,
                    home_ui={
                        "state": proof.home_ui_state,
                        "reason": "activity",
                        "evidence": "foreground",
                    },
                )

        self.monkeypatch.setattr(
            install_executor, "async_revalidate_install_target", AsyncMock()
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_get_durable_adb_credential",
            AsyncMock(
                return_value=AdbCredential(signer=object(), generation_id=CREDENTIAL_ID)
            ),
        )
        self.monkeypatch.setattr(
            install_executor, "async_verify_installed_target", AsyncMock()
        )
        self.monkeypatch.setattr(install_executor, "HaPaneldClient", FakeClient)


ENTRY_ID = "01M1J723MDQ69QDQVCRXKYZBJV"


async def test_finalizer_lease_grants_exactly_one_owner_and_releases_by_owner(
    hass: HomeAssistant,
) -> None:
    """Two completed config flows cannot both create an entry for one job."""
    receipt, manager = await seed_healthy(hass)
    executor, leases = counting_executor(hass, manager)

    granted = await asyncio.gather(
        *(
            executor._async_acquire_finalizer(receipt.job_id, f"flow_{index}")
            for index in range(40)
        )
    )
    winners = [index for index, lease in enumerate(granted) if lease is not None]
    assert len(winners) == 1
    owner = f"flow_{winners[0]}"
    assert executor.is_finalizer_active(receipt.job_id)
    assert await executor._async_acquire_finalizer(receipt.job_id, owner) is not None

    executor.release_finalizer(receipt.job_id, "flow_loser")
    assert executor.is_finalizer_active(receipt.job_id)
    executor.release_finalizer(receipt.job_id, owner)
    executor.release_finalizer(receipt.job_id, owner)
    assert not executor.is_finalizer_active(receipt.job_id)
    assert leases.released == [receipt.job_id]
    assert await executor._async_acquire_finalizer(receipt.job_id, "flow_next")


async def test_finalizer_lease_is_unavailable_before_health_or_after_consumption(
    hass: HomeAssistant,
) -> None:
    """The in-memory lease never substitutes for the durable receipt phase."""
    manager = InstallJobManager(hass)
    approved = await create_job(manager)
    executor = InstallExecutor(hass, manager)
    assert await executor._async_acquire_finalizer(approved.job_id, "flow_one") is None
    verdict = await executor.async_verify_finalization(approved.job_id, "flow_one")
    assert verdict.outcome is FinalizationOutcome.BUSY
    assert not executor.is_finalizer_active(approved.job_id)
    approved = await manager.async_claim(approved.job_id, approved.revision)
    approved = await manager.async_request_cancel(approved.job_id, approved.revision)
    await manager.async_transition(
        approved.job_id,
        approved.revision,
        InstallPhase.CANCELLED,
        result_code=InstallResultCode.CANCELLED_BY_USER,
    )

    healthy, manager = await seed_healthy(hass)
    executor = InstallExecutor(hass, manager)
    assert await executor._async_acquire_finalizer(healthy.job_id, "flow_one")
    await manager.async_transition(
        healthy.job_id,
        healthy.revision,
        InstallPhase.CONSUMED,
        result_code=InstallResultCode.ENTRY_CREATED,
        consumed_entry_id=ENTRY_ID,
    )
    assert await executor._async_acquire_finalizer(healthy.job_id, "flow_two") is None
    assert executor.is_finalizer_active(healthy.job_id)
    executor.release_finalizer(healthy.job_id, "flow_one")
    assert not executor.is_finalizer_active(healthy.job_id)


async def test_verified_receipt_is_consumed_once_and_releases_its_lease_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease spans verification and the entry's addition, then goes once."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch)
    executor, leases = counting_executor(hass, manager)

    verdict = await executor.async_verify_finalization(receipt.job_id, "flow_one")
    assert verdict.outcome is FinalizationOutcome.VERIFIED
    assert verdict.health is not None
    # Held while Home Assistant adds the entry: setup reconciliation of that
    # very entry, and any other flow, must leave the receipt to this owner.
    assert executor.is_finalizer_active(receipt.job_id)
    await executor.async_reconcile_entry(
        ENTRY_ID, receipt.target.address, verdict.health
    )
    other = await executor.async_verify_finalization(receipt.job_id, "flow_two")
    assert other.outcome is FinalizationOutcome.BUSY
    assert (await manager.async_get(receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )

    await executor.async_consume_finalization(receipt.job_id, "flow_one", ENTRY_ID)
    consumed = await manager.async_get(receipt.job_id)

    assert consumed.phase is InstallPhase.CONSUMED
    assert consumed.consumed_entry_id == ENTRY_ID
    assert leases.released == [receipt.job_id]


async def test_concurrent_finalizers_have_one_owner_and_one_release(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two flows and a loading entry race; one owner finalizes and releases."""
    receipt, manager = await seed_healthy(hass)
    proof = FinalProof(monkeypatch, hold_health=True)
    executor, leases = counting_executor(hass, manager)

    first = hass.async_create_task(
        executor.async_verify_finalization(receipt.job_id, "flow_one")
    )
    await proof.health_started.wait()
    racers = await asyncio.wait_for(
        asyncio.gather(
            executor.async_verify_finalization(receipt.job_id, "flow_two"),
            executor.async_verify_finalization(receipt.job_id, "flow_one"),
            executor.async_reconcile_entry(
                ENTRY_ID,
                receipt.target.address,
                PanelHealth(
                    version=receipt.artifact.version_name,
                    panel_id="alpha",
                    build="1",
                    config_hash="1a2b3c4d",
                ),
            ),
        ),
        timeout=5,
    )
    proof.release_health.set()
    verdict = await first
    await executor.async_consume_finalization(receipt.job_id, "flow_one", ENTRY_ID)

    assert [racer.outcome for racer in racers[:2]] == [
        FinalizationOutcome.BUSY,
        FinalizationOutcome.BUSY,
    ]
    assert verdict.outcome is FinalizationOutcome.VERIFIED
    assert proof.health_reads == 1
    assert (await manager.async_get(receipt.job_id)).consumed_entry_id == ENTRY_ID
    assert leases.released == [receipt.job_id]


async def test_same_owner_resubmit_during_verification_is_busy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner submitting again mid-proof never starts a second panel read."""
    receipt, manager = await seed_healthy(hass)
    proof = FinalProof(monkeypatch, hold_health=True)
    executor, _ = counting_executor(hass, manager)

    first = hass.async_create_task(
        executor.async_verify_finalization(receipt.job_id, "flow_one")
    )
    await proof.health_started.wait()
    again = hass.async_create_task(
        executor.async_verify_finalization(receipt.job_id, "flow_one")
    )
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        assert again.done()
        assert again.result().outcome is FinalizationOutcome.BUSY
        assert proof.health_reads == 1
    finally:
        proof.release_health.set()
        await asyncio.wait_for(asyncio.gather(first, again), timeout=5)


async def test_cancelled_verification_releases_its_lease_exactly_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation mid-proof hands the lease back and leaves the receipt intact."""
    receipt, manager = await seed_healthy(hass)
    proof = FinalProof(monkeypatch, hold_health=True)
    executor, leases = counting_executor(hass, manager)

    task = hass.async_create_task(
        executor.async_verify_finalization(receipt.job_id, "flow_one")
    )
    await proof.health_started.wait()
    # A dialog closed at the same moment asks for the same release.
    executor.release_finalizer(receipt.job_id, "flow_one")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert leases.released == [receipt.job_id]
    assert (await manager.async_get(receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )
    proof.hold_health = False
    retried = await executor.async_verify_finalization(receipt.job_id, "flow_two")
    assert retried.outcome is FinalizationOutcome.VERIFIED


async def test_removal_mid_verification_releases_only_when_verification_exits(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second finalizer never verifies beside a removed one still reading."""
    receipt, manager = await seed_healthy(hass)
    proof = FinalProof(monkeypatch, hold_health=True)
    executor, leases = counting_executor(hass, manager)

    task = hass.async_create_task(
        executor.async_verify_finalization(receipt.job_id, "flow_removed")
    )
    await proof.health_started.wait()
    executor.release_finalizer(receipt.job_id, "flow_removed")
    assert executor.is_finalizer_active(receipt.job_id)
    busy = await executor.async_verify_finalization(receipt.job_id, "flow_other")
    assert busy.outcome is FinalizationOutcome.BUSY

    proof.release_health.set()
    await task

    assert leases.released == [receipt.job_id]
    assert (await manager.async_get(receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )


@pytest.mark.parametrize(
    ("package", "version", "expected"),
    [
        (None, None, FinalizationOutcome.VERIFIED),
        (None, "9.9.9", FinalizationOutcome.RECOVERY_REQUIRED),
        ("io.panelassistant.android", None, FinalizationOutcome.RECOVERY_REQUIRED),
    ],
    ids=["installed-app", "other-version", "other-package"],
)
async def test_final_proof_applies_the_installed_app_rule(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    package: str | None,
    version: str | None,
    expected: FinalizationOutcome,
) -> None:
    """Drift found by the final proof is made durable and releases the lease."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch, package=package, version=version)
    executor, leases = counting_executor(hass, manager)

    verdict = await executor.async_verify_finalization(receipt.job_id, "flow_one")

    assert verdict.outcome is expected
    stored = await manager.async_get(receipt.job_id)
    if expected is FinalizationOutcome.VERIFIED:
        assert stored.phase is InstallPhase.HEALTHY_UNCLAIMED
        assert leases.released == []
    else:
        assert stored.phase is InstallPhase.RECOVERY_REQUIRED
        assert stored.result_code is InstallResultCode.VERIFICATION_REQUIRED
        assert leases.released == [receipt.job_id]


async def test_final_proof_rejects_home_chooser_after_matching_health(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finalizer cannot create an entry over Android's launcher chooser."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch, home_ui_state="blocked")
    executor, _ = counting_executor(hass, manager)

    verdict = await executor.async_verify_finalization(receipt.job_id, "flow_one")

    assert verdict.outcome is FinalizationOutcome.RECOVERY_REQUIRED
    stored = await manager.async_get(receipt.job_id)
    assert stored.phase is InstallPhase.RECOVERY_REQUIRED


@pytest.mark.parametrize(
    ("package", "expected_phase"),
    [
        (None, InstallPhase.CONSUMED),
        ("io.panelassistant.android", InstallPhase.RECOVERY_REQUIRED),
    ],
    ids=["installed-app", "other-package"],
)
async def test_restart_settles_a_healthy_receipt_when_its_entry_loads(
    hass: HomeAssistant, package: str | None, expected_phase: InstallPhase
) -> None:
    """A restart between entry creation and consumption loses no receipt.

    The process that verified the receipt is gone, and with it every lease; a
    fresh manager and executor read only what was persisted.
    """
    receipt, _before_restart = await seed_healthy(hass)
    restarted = InstallJobManager(hass)
    executor, leases = counting_executor(hass, restarted)
    health = PanelHealth(
        version=receipt.artifact.version_name,
        panel_id="alpha",
        build="1",
        config_hash="1a2b3c4d",
        package=package,
    )

    await executor.async_reconcile_entry(ENTRY_ID, receipt.target.address, health)
    await executor.async_reconcile_entry(ENTRY_ID, receipt.target.address, health)
    settled = await restarted.async_get(receipt.job_id)

    assert settled.phase is expected_phase
    if expected_phase is InstallPhase.CONSUMED:
        assert settled.consumed_entry_id == ENTRY_ID
    assert leases.released == [receipt.job_id]


async def test_an_entry_for_another_panel_never_settles_this_receipt(
    hass: HomeAssistant,
) -> None:
    """A loading entry settles only the healthy receipt at its own address."""
    receipt, _before_restart = await seed_healthy(hass)
    restarted = InstallJobManager(hass)
    executor, _ = counting_executor(hass, restarted)
    health = PanelHealth(
        version=receipt.artifact.version_name,
        panel_id="alpha",
        build="1",
        config_hash="1a2b3c4d",
    )

    await executor.async_reconcile_entry(ENTRY_ID, "other-panel.local", health)
    stored = await restarted.async_get(receipt.job_id)

    assert stored.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert stored.consumed_entry_id is None


async def test_cancelled_entry_reconciliation_releases_its_lease_once(
    hass: HomeAssistant,
) -> None:
    """Cancelling a loading entry mid-transition strands no lease."""
    receipt, manager = await seed_healthy(hass)
    executor, leases = counting_executor(hass, manager)
    entered = asyncio.Event()

    async def _blocked_transition(*_args: object, **_kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    health = PanelHealth(
        version=receipt.artifact.version_name,
        panel_id="alpha",
        build="1",
        config_hash="1a2b3c4d",
    )
    with patch.object(manager, "async_transition", side_effect=_blocked_transition):
        task = hass.async_create_task(
            executor.async_reconcile_entry(ENTRY_ID, receipt.target.address, health)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert leases.released == [receipt.job_id]
    assert (await manager.async_get(receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )
    assert await executor._async_acquire_finalizer(receipt.job_id, "flow_next")


@pytest.mark.parametrize(
    "failure",
    [install_jobs.InstallJobStoreError(), RuntimeError("private-receipt-detail")],
)
async def test_consumption_failure_never_raises_and_still_releases(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    """The entry exists by now, so persistence failure is logged, never raised."""
    receipt, manager = await seed_healthy(hass)
    FinalProof(monkeypatch)
    executor, leases = counting_executor(hass, manager)
    verdict = await executor.async_verify_finalization(receipt.job_id, "flow_one")
    assert verdict.outcome is FinalizationOutcome.VERIFIED

    with patch.object(manager, "async_transition", side_effect=failure):
        await executor.async_consume_finalization(receipt.job_id, "flow_one", ENTRY_ID)

    assert leases.released == [receipt.job_id]
    assert "Unable to consume a completed ha-paneld install receipt" in caplog.text
    assert "private-receipt-detail" not in caplog.text


SUCCESSOR_PACKAGE_ID = "io.panelassistant.android"
SUCCESSOR_LAUNCH_COMPONENT = (
    "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
)


def successor_artifact() -> InstallArtifact:
    """The same release under the new application id.

    The launch component is fully qualified because the classes stay in the
    legacy namespace, which does not move with the application id.
    """
    return replace(
        artifact(),
        package_id=SUCCESSOR_PACKAGE_ID,
        launch_component=SUCCESSOR_LAUNCH_COMPONENT,
    )


async def create_successor_job(manager: InstallJobManager) -> InstallJobReceipt:
    """Create one durable approved receipt for the successor package."""
    selected_target = target()
    selected_artifact = successor_artifact()
    receipt, created = await manager.async_create_or_join(
        selected_target,
        selected_artifact,
        install_plan_sha256(selected_target, selected_artifact, CREDENTIAL_ID),
        CREDENTIAL_ID,
    )
    assert created
    return receipt


async def test_the_handover_wait_ends_on_the_successor_not_the_first_reply(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both apps are built from one tree, so the version alone proves nothing.

    Mid-handover the old app is still answering on 8888, and it answers with the
    very version being installed. Only its reported package tells the two apart.
    """
    manager = InstallJobManager(hass)
    receipt = await create_successor_job(manager)
    harness = Harness(monkeypatch)
    sleep = AsyncMock()
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)
    # Four answers from the old app, then nothing at all while the port changes
    # hands, then the successor.
    replies: list[Exception | str] = [
        *["io.github.maxlyth.hapaneld"] * 4,
        CannotConnectError(),
        CannotConnectError(),
        SUCCESSOR_PACKAGE_ID,
    ]
    harness.health_packages = replies

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.result_code is None
    assert harness.health_calls == len(replies)
    assert sleep.await_count == len(replies) - 1
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_migration_incomplete_{receipt.job_id}"
        )
        is None
    )


async def test_a_handover_that_never_finishes_is_reported_and_not_healthy(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old app answering for the whole budget is never a successful install."""
    manager = InstallJobManager(hass)
    receipt = await create_successor_job(manager)
    harness = Harness(monkeypatch)
    sleep = AsyncMock()
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)
    harness.health_packages = ["io.github.maxlyth.hapaneld"]

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert harness.health_calls == install_executor._HANDOVER_HEALTH_ATTEMPTS
    # The panel carries on by itself, so this is reported rather than retried.
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"panel_migration_incomplete_{receipt.job_id}"
    )
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "address": "192.168.250.23",
        "version": "0.1.0",
    }


async def test_a_legacy_install_keeps_the_budget_and_failure_it_had(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing hands over, so another app's reply is wrong, not 'not yet'."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    sleep = AsyncMock()
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)
    harness.expected_version = "9.9.9"

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert harness.health_calls == 1
    assert sleep.await_count == 0
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_migration_incomplete_{receipt.job_id}"
        )
        is None
    )


async def test_the_successor_is_launched_by_its_own_exact_component(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The descriptor's own component reaches the panel, never a rebuilt one."""
    manager = InstallJobManager(hass)
    receipt = await create_successor_job(manager)
    harness = Harness(monkeypatch)
    harness.health_packages = [SUCCESSOR_PACKAGE_ID]

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    _target, _signer, descriptor, _root = harness.launch_arguments[-1]
    assert descriptor.package_id == SUCCESSOR_PACKAGE_ID
    assert descriptor.launch_component == SUCCESSOR_LAUNCH_COMPONENT
    for _target, _signer, preflighted in harness.preflight_arguments:
        assert preflighted.package_id == SUCCESSOR_PACKAGE_ID


async def test_a_reply_without_a_package_is_never_taken_for_the_successor(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The successor always reports its own id, so silence is the other app."""
    manager = InstallJobManager(hass)
    receipt = await create_successor_job(manager)
    harness = Harness(monkeypatch)
    sleep = AsyncMock()
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)
    # A healthy panel at the right version naming no package: a build older
    # than the migration, which cannot be the app just installed.
    harness.health_packages = [None]

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert harness.health_calls == install_executor._HANDOVER_HEALTH_ATTEMPTS


async def test_a_legacy_install_still_accepts_a_build_that_names_no_package(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every build older than the migration reports none; they still install."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.health_packages = [None]

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.health_calls == 1


async def test_a_panel_that_answered_nothing_is_not_told_it_is_part_migrated(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handover is reported only when one was actually seen in progress.

    A panel that fell off the network answers nothing at all. Telling its owner
    it has not finished moving to its new app sends them looking for a handover
    that never started.
    """
    manager = InstallJobManager(hass)
    receipt = await create_successor_job(manager)
    harness = Harness(monkeypatch)
    harness.health_error = CannotConnectError()
    monkeypatch.setattr(install_executor.asyncio, "sleep", AsyncMock())

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_migration_incomplete_{receipt.job_id}"
        )
        is None
    )


async def test_a_freshly_installed_panel_is_told_where_home_assistant_is(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install site's positive path.

    Every other test here runs with the panel reporting setup finished, so they
    prove only that nothing is handed over. This one proves the wiring: an
    install that reaches a healthy panel mid-setup hands it the address.
    """
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=True)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.handed_over_urls == ["http://192.0.2.5:8123"]
    # Asked before told, over the one client the health check already opened.
    assert harness.events.count("setup_state") == 1
    assert harness.events.count("health_client") == 1


async def test_an_older_panel_is_not_sent_a_key_it_would_refuse(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version gate at the install site.

    A panel that does not advertise handover support would refuse the unknown
    key, and its config admission is atomic, so the whole request would be
    rejected rather than the one field ignored.
    """
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=False)

    await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert harness.handed_over_urls == []
    assert harness.events.count("setup_state") == 1


async def test_a_panel_already_at_the_target_converges_instead_of_refusing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry against a satisfied panel finishes; it is not told it is dirty.

    Nothing is downloaded, copied or installed, because the panel already runs
    exactly these bytes. Launch, health and the setup handover still run, so
    the job ends in the same place a fresh install does.
    """
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.target_installed = True
    harness.installed_bytes = 12_345

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.result_code is None
    assert completed.actual_apk_bytes == 12_345
    assert completed.preflight_root_mode == AdbRootMode.ROOTLESS.value
    for never in ("download", "stage", "install"):
        assert never not in harness.events, harness.events
    assert harness.events.count("installed_size") == 1
    assert harness.events.count("launch") == 1
    assert harness.events.count("health") == 1
    # One preflight, and it is the one allowed to report a satisfied target.
    assert harness.preflight_admissions == [True]
    # The observation is bound to the same target, artifact and root posture.
    target, descriptor, root_mode = harness.installed_size_arguments[0]
    assert target.serial == "SERIAL-1"
    assert descriptor.apk_sha256 == receipt.artifact.apk_sha256
    assert root_mode is AdbRootMode.ROOTLESS


async def test_a_panel_holding_the_app_at_other_bytes_is_still_refused(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean install never replaces an app that is already there."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.target_installed = True
    harness.installed_bytes = None

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.FAILED
    assert completed.result_code is InstallResultCode.PREFLIGHT_REJECTED
    assert harness.events.count("installed_size") == 1
    for never in ("download", "stage", "install", "launch"):
        assert never not in harness.events, harness.events


@pytest.mark.parametrize(
    ("installed_package", "reported_package", "expected"),
    [
        (SUCCESSOR_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, True),
        (SUCCESSOR_PACKAGE_ID, "io.github.maxlyth.hapaneld", False),
        (SUCCESSOR_PACKAGE_ID, None, False),
        ("io.github.maxlyth.hapaneld", None, True),
        ("io.github.maxlyth.hapaneld", "io.github.maxlyth.hapaneld", True),
        ("io.github.maxlyth.hapaneld", SUCCESSOR_PACKAGE_ID, False),
    ],
)
def test_one_installed_app_rule_takes_the_whole_health_line(
    installed_package: str, reported_package: str | None, expected: bool
) -> None:
    """The shared receipt rule reads the package, never the version alone."""
    installed = replace(artifact(), package_id=installed_package)
    health = PanelHealth(
        version=installed.version_name,
        panel_id="alpha",
        build="1",
        config_hash="1a2b3c4d",
        package=reported_package,
    )

    assert health_is_installed_app(health, installed) is expected
    assert not health_is_installed_app(replace(health, version="9.9.9"), installed)

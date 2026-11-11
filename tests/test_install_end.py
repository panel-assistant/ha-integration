"""Ending an unfinished install: what the receipt says, and who may end it."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_artifacts import (
    ArtifactCustodyError,
    ArtifactErrorCode,
)
from custom_components.panel_assistant.install_executor import (
    FinalizationOutcome,
    async_get_install_executor,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallJobReceipt,
    InstallJobRevisionError,
    InstallJobTransitionError,
    InstallPhase,
    InstallResultCode,
    async_get_install_job_manager,
)
from custom_components.panel_assistant.install_receipt import InstallEndReason

from . import install_executor_harness
from .install_executor_harness import Harness, seed_phase

# Bind the shared autouse fixture here so pytest applies it to this module.
emulate_home_assistant_store_file = (
    install_executor_harness.emulate_home_assistant_store_file
)
EXECUTOR_LOG = "custom_components.panel_assistant.install_executor"


@pytest.fixture(autouse=True)
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The panel, ADB and download stand-ins behind the real executor."""
    return Harness(monkeypatch)


async def _stored(hass: HomeAssistant, job_id: str) -> InstallJobReceipt:
    return await (await async_get_install_job_manager(hass)).async_get(job_id)


def _logged(caplog: pytest.LogCaptureFixture, logger: str, *parts: str) -> bool:
    return any(
        record.name == logger and all(part in record.getMessage() for part in parts)
        for record in caplog.records
    )


ACTIVE_PHASES = [
    InstallPhase.APPROVED,
    InstallPhase.AUTHORIZING,
    InstallPhase.PREFLIGHT,
    InstallPhase.DOWNLOADING,
    InstallPhase.ARTIFACT_READY,
    InstallPhase.REVALIDATING,
    InstallPhase.STAGING,
    InstallPhase.INSTALLING,
    InstallPhase.INSTALLED,
    InstallPhase.LAUNCHING,
    InstallPhase.HEALTH_CHECK,
    InstallPhase.HEALTHY_UNCLAIMED,
]
PANEL_UNTOUCHED = ACTIVE_PHASES[:6]


@pytest.mark.parametrize("phase", ACTIVE_PHASES)
@pytest.mark.parametrize("reason", list(InstallEndReason))
async def test_ending_a_job_says_what_the_panel_may_hold(
    hass: HomeAssistant, phase: InstallPhase, reason: InstallEndReason
) -> None:
    """Before anything was sent it failed or was cancelled; later it needs checking."""
    receipt, manager = await seed_phase(hass, phase)

    ended = await manager.async_end(
        receipt.job_id,
        receipt.revision,
        reason,
        cleanup_confirmed_revision=receipt.revision,
    )

    assert ended.is_terminal
    assert ended.revision == receipt.revision + 1
    # A manager that remembers nothing reads the same receipt back.
    assert await InstallJobManager(hass).async_get(receipt.job_id) == ended
    if reason is InstallEndReason.OWNER_CANCELLED and phase in PANEL_UNTOUCHED:
        assert ended.phase is InstallPhase.CANCELLED
        assert ended.result_code is InstallResultCode.CANCELLED_BY_USER
        assert ended.cancel_requested
        assert ended.failure_stage is None
        assert ended.result_subcode is None
    elif phase is InstallPhase.APPROVED:
        assert ended.phase is InstallPhase.FAILED
        assert ended.result_code is InstallResultCode.TRANSPORT_FAILED
        assert ended.failure_stage is None
        assert ended.result_subcode is None
    elif phase in [*PANEL_UNTOUCHED, InstallPhase.STAGING]:
        assert ended.phase is InstallPhase.FAILED
        assert ended.result_code is InstallResultCode.TRANSPORT_FAILED
        assert ended.failure_stage is phase
        assert ended.result_subcode == f"job:{reason.value}"
    else:
        assert ended.phase is InstallPhase.RECOVERY_REQUIRED
        assert ended.result_code is InstallResultCode.VERIFICATION_REQUIRED
        assert ended.failure_stage is phase
        assert ended.result_subcode == f"job:{reason.value}"


async def test_ending_needs_the_cleanup_proved_for_the_exact_revision(
    hass: HomeAssistant,
) -> None:
    """A stale or absent cleanup proof, or an ended job, changes nothing."""
    receipt, manager = await seed_phase(hass, InstallPhase.DOWNLOADING)

    with pytest.raises(InstallJobRevisionError):
        await manager.async_end(
            receipt.job_id,
            receipt.revision,
            InstallEndReason.STALLED,
            cleanup_confirmed_revision=receipt.revision - 1,
        )
    with pytest.raises(InstallJobRevisionError):
        await manager.async_end(
            receipt.job_id,
            receipt.revision - 1,
            InstallEndReason.STALLED,
            cleanup_confirmed_revision=receipt.revision - 1,
        )
    assert await manager.async_get(receipt.job_id) == receipt

    ended = await manager.async_end(
        receipt.job_id,
        receipt.revision,
        InstallEndReason.STALLED,
        cleanup_confirmed_revision=receipt.revision,
    )
    with pytest.raises(InstallJobTransitionError):
        await manager.async_end(
            ended.job_id,
            ended.revision,
            InstallEndReason.STALLED,
            cleanup_confirmed_revision=ended.revision,
        )
    assert await manager.async_get(receipt.job_id) == ended


async def test_a_job_whose_download_cannot_be_removed_stays_and_is_tried_again(
    hass: HomeAssistant,
    harness: Harness,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The custody rule holds: no receipt ends while its private file may remain."""
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.local_cleanup_error = ArtifactCustodyError(ArtifactErrorCode.IO_FAILED)
    executor = await async_get_install_executor(hass)
    freezer.tick(timedelta(minutes=16))

    with caplog.at_level(logging.WARNING):
        assert await executor.async_end_stalled_jobs() == ()

    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.DOWNLOADING
    assert _logged(
        caplog,
        EXECUTOR_LOG,
        "could not end an unfinished install",
        "reason=stalled",
        "cause=artifact:artifact_io_failed",
    )

    harness.local_cleanup_error = None
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.FAILED


async def test_a_live_install_is_never_ended_under_its_worker(
    hass: HomeAssistant, harness: Harness, freezer: FrozenDateTimeFactory
) -> None:
    """Only a job nobody is advancing is stalled, however old its receipt."""
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    executor = await async_get_install_executor(hass)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.stage_entered.wait()
    freezer.tick(timedelta(hours=2))

    assert await executor.async_end_stalled_jobs(stopped_now=True) == ()
    live = await _stored(hass, receipt.job_id)
    assert live.phase is InstallPhase.STAGING
    assert executor.can_cancel(live)

    harness.stage_release.set()
    await worker
    await hass.async_block_till_done()
    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_an_install_another_dialog_is_completing_is_left_to_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """While a dialog holds the finishing lease, nothing else ends the install."""
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    executor = await async_get_install_executor(hass)
    assert await executor._async_acquire_finalizer(receipt.job_id, "dialog_one")
    freezer.tick(timedelta(hours=25))

    assert await executor.async_end_stalled_jobs(stopped_now=True) == ()
    held = await _stored(hass, receipt.job_id)
    assert held.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert not executor.can_cancel(held)
    assert await executor.async_cancel_job(receipt.job_id) is None

    # The dialog lets go without adding the panel: the next look ends it.
    executor.release_finalizer(receipt.job_id, "dialog_one")
    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    ended = await _stored(hass, receipt.job_id)
    assert ended.phase is InstallPhase.RECOVERY_REQUIRED
    assert ended.result_subcode == "job:unclaimed_expired"


async def test_cancelling_an_install_that_had_changed_the_panel_still_raises_its_repair(
    hass: HomeAssistant,
) -> None:
    """Only a healthy install is let go quietly; a half-made one needs checking."""
    receipt, _manager = await seed_phase(hass, InstallPhase.INSTALLING)
    executor = await async_get_install_executor(hass)

    ended = await executor.async_cancel_job(receipt.job_id)
    await hass.async_block_till_done()

    assert ended is not None
    assert ended.phase is InstallPhase.RECOVERY_REQUIRED
    assert ended.failure_stage is InstallPhase.INSTALLING
    assert ended.result_subcode == "job:cancelled_by_owner"
    issues = [i for (domain, i) in ir.async_get(hass).issues if domain == DOMAIN]
    assert len(issues) == 1


@pytest.mark.parametrize("ending", ["owner_cancel", "expiry"])
async def test_no_dialog_can_take_an_install_while_it_is_being_ended(
    hass: HomeAssistant,
    harness: Harness,
    freezer: FrozenDateTimeFactory,
    ending: str,
) -> None:
    """Ending waits on the download's removal; finishing is refused in that gap."""
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    executor = await async_get_install_executor(hass)
    harness.local_cleanup_entered = asyncio.Event()
    harness.local_cleanup_release = asyncio.Event()
    if ending == "expiry":
        freezer.tick(timedelta(hours=25))
        ender = hass.async_create_task(executor.async_end_stalled_jobs())
    else:
        ender = hass.async_create_task(executor.async_cancel_job(receipt.job_id))
    await harness.local_cleanup_entered.wait()
    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.HEALTHY_UNCLAIMED

    taken = await executor.async_verify_finalization(receipt.job_id, "dialog_one")

    assert taken.outcome is FinalizationOutcome.BUSY
    assert not executor.is_finalizer_active(receipt.job_id)
    assert "pin" not in harness.events, "the panel is not verified under an ending job"
    harness.local_cleanup_release.set()
    await ender
    await hass.async_block_till_done()
    ended = await _stored(hass, receipt.job_id)
    assert ended.phase is InstallPhase.RECOVERY_REQUIRED
    assert ended.failure_stage is InstallPhase.HEALTHY_UNCLAIMED
    # Once it has ended, a dialog that asks again is told so, not kept waiting.
    late = await executor.async_verify_finalization(receipt.job_id, "dialog_one")
    assert late.outcome is FinalizationOutcome.BUSY
    assert not executor.is_finalizer_active(receipt.job_id)

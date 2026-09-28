"""Installer receipts surface one actionable Core repair per panel."""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import failure_repair
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.failure_repair import async_failure_events
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallJobReceipt,
    InstallPhase,
    InstallResultCode,
)
from tests import test_install_jobs as job_fixtures
from tests.test_install_jobs import (
    CURRENT_ENTRY_ID,
    Clock,
    create,
    target,
    transition_to_staging,
)

emulate_home_assistant_store_file = job_fixtures.emulate_home_assistant_store_file


def _install_issues(hass: HomeAssistant) -> tuple[ir.IssueEntry, ...]:
    """Observe issues through the same Core registry used by Repairs."""
    return tuple(
        issue
        for (domain, _issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    )


async def _authorization_failure(
    manager: InstallJobManager, *, address: str, serial: str, pinned: str
) -> InstallJobReceipt:
    receipt, created = await create(
        manager, install_target=target(address, serial, pinned)
    )
    assert created
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


async def test_failed_attempts_keep_one_issue_per_panel_with_latest_diagnosis(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    first = await _authorization_failure(
        manager, address="first.local", serial="SERIAL-1", pinned="192.168.1.23"
    )
    loaded = await InstallJobManager(hass, now=Clock()).async_get(first.job_id)
    assert loaded.failure_stage is InstallPhase.AUTHORIZING
    assert loaded.result_subcode == "adb:authorization_failed"
    await hass.async_block_till_done()
    issues = _install_issues(hass)
    assert len(issues) == 1
    first_issue = issues[0]
    assert first_issue.is_fixable
    assert first_issue.severity is ir.IssueSeverity.ERROR
    assert first_issue.translation_key == "installer_failure_authorization_failed"
    assert first_issue.data == {"key": first_issue.issue_id}
    await hass.async_block_till_done()
    events = await async_failure_events(hass, first_issue.issue_id)
    assert events[-1]["receipt"]["failure_stage"] == InstallPhase.AUTHORIZING
    assert events[-1]["receipt"]["result_subcode"] == "adb:authorization_failed"

    repeated = await _authorization_failure(
        manager, address="first.local", serial="SERIAL-1", pinned="192.168.1.23"
    )
    assert repeated.job_id != first.job_id
    await hass.async_block_till_done()
    issues = _install_issues(hass)
    assert len(issues) == 1
    assert issues[0].issue_id == first_issue.issue_id
    await hass.async_block_till_done()
    events = await async_failure_events(hass, first_issue.issue_id)
    assert events[-1]["job_id"] == repeated.job_id

    alias = await _authorization_failure(
        manager,
        address="192.168.1.23",
        serial="SERIAL-1",
        pinned="192.168.1.23",
    )
    assert alias.job_id != repeated.job_id
    await hass.async_block_till_done()
    assert tuple(issue.issue_id for issue in _install_issues(hass)) == (
        first_issue.issue_id,
    )
    await hass.async_block_till_done()
    events = await async_failure_events(hass, first_issue.issue_id)
    assert events[-1]["job_id"] == alias.job_id

    await _authorization_failure(
        manager, address="second.local", serial="SERIAL-2", pinned="192.168.1.24"
    )
    await hass.async_block_till_done()
    issues = _install_issues(hass)
    assert len(issues) == 2
    assert len({issue.issue_id for issue in issues}) == 2


async def test_repair_keeps_a_report_while_its_private_copy_is_pending(
    hass: HomeAssistant, monkeypatch,
) -> None:
    saved = asyncio.Event()
    allow_save = asyncio.Event()
    store = failure_repair._failure_store(hass).store
    original = store.async_save

    async def delayed_save(data):
        saved.set()
        await allow_save.wait()
        return await original(data)

    monkeypatch.setattr(store, "async_save", delayed_save)
    manager = InstallJobManager(hass, now=Clock())
    await _authorization_failure(
        manager, address="first.local", serial="SERIAL-1", pinned="192.168.1.23"
    )
    await asyncio.wait_for(saved.wait(), 5)
    (issue,) = _install_issues(hass)
    allow_save.set()
    await hass.async_block_till_done()
    assert (await async_failure_events(hass, issue.issue_id))[-1]["reason"] == (
        "authorization_failed"
    )


async def test_report_recovers_from_committed_receipt_when_private_copy_is_missing(
    hass: HomeAssistant, monkeypatch,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    receipt = await _authorization_failure(
        manager, address="first.local", serial="SERIAL-1", pinned="192.168.1.23"
    )
    await hass.async_block_till_done()
    (issue,) = _install_issues(hass)
    hass.data.pop(f"{DOMAIN}.failure_repair_store")
    store = failure_repair._failure_store(hass).store

    async def missing_copy():
        return None

    monkeypatch.setattr(store, "async_load", missing_copy)
    events = await async_failure_events(hass, issue.issue_id)
    assert [event["job_id"] for event in events] == [receipt.job_id]
    assert events[0]["receipt"]["result_subcode"] == "adb:authorization_failed"


async def test_recovery_required_raises_issue_and_later_consumed_receipt_clears_it(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    receipt, _ = await create(manager)
    staging = await transition_to_staging(manager, receipt.job_id)
    recovery = await manager.async_transition(
        staging.job_id,
        staging.revision,
        InstallPhase.RECOVERY_REQUIRED,
        result_code=InstallResultCode.AMBIGUOUS_MUTATION,
        result_subcode="adb:stage_ambiguous",
    )
    assert recovery.failure_stage is InstallPhase.STAGING
    assert recovery.result_subcode == "adb:stage_ambiguous"
    await hass.async_block_till_done()
    issues = _install_issues(hass)
    assert len(issues) == 1
    await hass.async_block_till_done()
    events = await async_failure_events(hass, issues[0].issue_id)
    assert events[-1]["receipt"]["failure_stage"] == InstallPhase.STAGING
    assert events[-1]["receipt"]["result_subcode"] == "adb:stage_ambiguous"

    successful, _ = await create(manager)
    successful = await manager.async_claim(successful.job_id, successful.revision)
    for phase in (InstallPhase.AUTHORIZING, InstallPhase.PREFLIGHT):
        successful = await manager.async_transition(
            successful.job_id, successful.revision, phase
        )
    successful = await manager.async_transition(
        successful.job_id,
        successful.revision,
        InstallPhase.INSTALLED,
        preflight_root_mode="root_adbd",
        actual_apk_bytes=successful.artifact.apk_size,
    )
    for phase in (InstallPhase.LAUNCHING, InstallPhase.HEALTH_CHECK):
        successful = await manager.async_transition(
            successful.job_id, successful.revision, phase
        )
    successful = await manager.async_transition(
        successful.job_id,
        successful.revision,
        InstallPhase.HEALTHY_UNCLAIMED,
        health_checked_at=Clock()(),
    )
    consumed = await manager.async_transition(
        successful.job_id,
        successful.revision,
        InstallPhase.CONSUMED,
        result_code=InstallResultCode.ENTRY_CREATED,
        consumed_entry_id=CURRENT_ENTRY_ID,
    )
    assert consumed.phase is InstallPhase.CONSUMED
    await hass.async_block_till_done()
    assert _install_issues(hass) == ()
    await hass.async_block_till_done()
    assert await async_failure_events(hass, issues[0].issue_id) == []


async def test_cancellation_does_not_clear_an_earlier_failure(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    await _authorization_failure(
        manager, address="first.local", serial="SERIAL-1", pinned="192.168.1.23"
    )
    await hass.async_block_till_done()
    (issue,) = _install_issues(hass)

    receipt, _ = await create(
        manager,
        install_target=target("first.local", "SERIAL-1", "192.168.1.23"),
    )
    cancelled = await manager.async_request_cancel(receipt.job_id, receipt.revision)
    cancelled = await manager.async_claim(cancelled.job_id, cancelled.revision)
    cancelled = await manager.async_transition(
        cancelled.job_id,
        cancelled.revision,
        InstallPhase.CANCELLED,
        result_code=InstallResultCode.CANCELLED_BY_USER,
    )
    assert cancelled.phase is InstallPhase.CANCELLED
    assert tuple(entry.issue_id for entry in _install_issues(hass)) == (issue.issue_id,)

    other, _ = await create(
        manager,
        install_target=target("second.local", "SERIAL-2", "192.168.1.24"),
    )
    other = await manager.async_request_cancel(other.job_id, other.revision)
    other = await manager.async_claim(other.job_id, other.revision)
    await manager.async_transition(
        other.job_id,
        other.revision,
        InstallPhase.CANCELLED,
        result_code=InstallResultCode.CANCELLED_BY_USER,
    )
    assert tuple(entry.issue_id for entry in _install_issues(hass)) == (issue.issue_id,)


async def test_restart_quarantine_raises_repair_from_claim_boundary(
    hass: HomeAssistant,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    receipt, _ = await create(manager)
    staging = await transition_to_staging(manager, receipt.job_id)

    restarted = InstallJobManager(hass, now=Clock())
    quarantined = await restarted.async_claim(
        staging.job_id,
        staging.revision,
        cleanup_confirmed_revision=staging.revision,
    )
    assert quarantined.phase is InstallPhase.RECOVERY_REQUIRED
    assert quarantined.failure_stage is InstallPhase.STAGING
    assert quarantined.result_subcode == "job:interrupted_mutation"
    await hass.async_block_till_done()
    issues = _install_issues(hass)
    assert len(issues) == 1
    await hass.async_block_till_done()
    events = await async_failure_events(hass, issues[0].issue_id)
    assert events[-1]["receipt"]["failure_stage"] == InstallPhase.STAGING
    assert events[-1]["receipt"]["result_subcode"] == "job:interrupted_mutation"

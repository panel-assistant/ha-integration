"""Installer failures remain actionable in Home Assistant Repairs."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import repairs as panel_repairs
from custom_components.panel_assistant.build_feed import feed_release_artifact
from custom_components.panel_assistant.const import DOMAIN, update_unique_id
from custom_components.panel_assistant.failure_repair import (
    RetrySafetyHold,
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    async_refresh_update_failure_name,
)
from custom_components.panel_assistant.install_jobs import InstallJobManager
from tests import test_install_jobs as job_fixtures
from tests.test_feed_update import _build
from tests.test_install_failure_repairs import _authorization_failure
from tests.test_install_jobs import Clock

emulate_home_assistant_store_file = job_fixtures.emulate_home_assistant_store_file


async def test_update_repair_names_the_home_assistant_panel_even_after_rename(
    hass: HomeAssistant, repairs_ready: None
) -> None:
    """A code name in the entry title does not label a current Repair."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="BoardCode", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "0.9.10", RuntimeError("update failed")
    )
    issue_id = next(
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    )
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="BoardCode",
    )
    registry.async_update_device(device.id, name_by_user="Kitchen display")

    async_refresh_update_failure_name(hass, entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"panel": "Kitchen display"}

    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "0.9.11", RuntimeError("update failed")
    )
    events = await async_failure_events(hass, issue_id)
    assert events[-1]["panel"] == "Kitchen display"


@pytest.fixture
async def repairs_ready(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_failed_install_opens_one_repair_with_retry_report_and_clear(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    """The owner can act on a panel failure after its setup dialog is gone."""
    issue_id = "installer_failure_test_panel"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="installer_failure",
        translation_placeholders={"panel": "Kitchen", "reason": "Install failed"},
        data={"address": "192.168.250.23", "kind": "install", "job_id": "0" * 32},
    )
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )

    assert response.status == 200
    flow = await response.json()
    assert flow["type"] == "menu"
    assert flow["menu_options"] == ["retry", "support_report", "clear_error"]

    response = await admin.post(
        f"/api/repairs/issues/fix/{flow['flow_id']}",
        json={"next_step_id": "clear_error"},
    )
    assert response.status == 200
    assert (await response.json())["step_id"] == "clear_error"
    response = await admin.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_support_report_keeps_unredacted_receipt_details_in_admin_flow(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    await _authorization_failure(
        manager,
        address="kitchen.local",
        serial="KITCHEN-123",
        pinned="192.168.250.23",
    )
    await hass.async_block_till_done()
    issue = next(
        item
        for (domain, _), item in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    )
    assert "KITCHEN-123" in await panel_repairs.async_support_report(
        hass, issue.issue_id
    )
    await hass.async_block_till_done()
    assert issue.data == {"key": issue.issue_id}
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": issue.issue_id},
    )
    menu = await response.json()
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}",
        json={"next_step_id": "support_report"},
    )
    assert response.status == 200
    form = await response.json()
    assert form["step_id"] == "support_report"
    report = form["description_placeholders"]["report"]
    assert report.startswith("## Panel Assistant support report\n")
    assert "### Failure 1 — install" in report
    assert "**Panel:** kitchen.local" in report
    assert "**Reason:** authorization_failed" in report
    assert "```text\n" in report
    assert "KITCHEN-123" in report
    assert "192.168.250.23" in report
    assert "receipt.failure_stage: authorizing" in report
    assert "receipt.result_subcode: adb:authorization_failed" in report
    assert "Panel Assistant" in report
    hass.data.pop(f"{DOMAIN}.failure_repair_store")
    assert "KITCHEN-123" in await panel_repairs.async_support_report(
        hass, issue.issue_id
    )


async def test_retry_safety_hold_stays_in_repairs_and_updates_report(
    hass: HomeAssistant,
    repairs_ready: None,
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = InstallJobManager(hass, now=Clock())
    await _authorization_failure(
        manager,
        address="kitchen.local",
        serial="KITCHEN-123",
        pinned="192.168.250.23",
    )
    await hass.async_block_till_done()
    issue = next(
        item
        for (domain, _), item in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    )
    retry = AsyncMock(side_effect=RetrySafetyHold("retry_target_changed"))
    monkeypatch.setattr(panel_repairs, "async_retry_install_job", retry)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": issue.issue_id},
    )
    menu = await response.json()
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}",
        json={"next_step_id": "retry"},
    )
    assert response.status == 200
    retry_view = await response.json()
    if retry_view["type"] == "progress":
        await hass.async_block_till_done()
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        retry_view = await response.json()
    result = retry_view
    assert result["step_id"] == "retry_error"
    assert result["errors"] == {"base": "retry_target_changed"}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue.issue_id) is not None
    retry.assert_awaited_once()
    report = await panel_repairs.async_support_report(hass, issue.issue_id)
    assert "retry_target_changed" in report


async def test_support_report_keeps_multiline_update_details_inside_a_code_fence(
    hass: HomeAssistant, repairs_ready: None
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: "kitchen.local"}
    )
    entry.add_to_hass(hass)
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10",
        RuntimeError("download failed\n### misleading heading"),
        artifact={"note": "three ticks ``` and a newline\nremain"},
    )
    issue_id = next(
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    )
    report = await panel_repairs.async_support_report(hass, issue_id)
    assert "**Reason:** download failed ### misleading heading" in report
    assert "\n### misleading heading\n" not in report
    assert "````text\n" in report
    assert "artifact.note:\n  three ticks ``` and a newline\n  remain" in report
    assert "target_version: 0.9.10" in report


async def test_update_repair_retries_via_home_assistant_and_clears_on_success(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test panel",
        data={CONF_ADDRESS: "kitchen.local"},
    )
    entry.add_to_hass(hass)
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "0.9.10", RuntimeError("download timed out")
    )
    issue = next(
        item
        for (domain, _), item in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    )
    assert issue.translation_key == "installer_failure_update"
    assert issue.data == {"key": issue.issue_id}
    assert "download timed out" in await panel_repairs.async_support_report(
        hass, issue.issue_id
    )
    entity = er.async_get(hass).async_get_or_create(
        "update",
        DOMAIN,
        update_unique_id(entry.entry_id),
        suggested_object_id="kitchen",
    )
    calls = []

    async def installed(service) -> None:
        calls.append(service.data)
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, "0.9.10", None
        )

    hass.services.async_register("update", "install", installed)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": issue.issue_id},
    )
    menu = await response.json()
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}",
        json={"next_step_id": "retry"},
    )
    result = await response.json()
    if result["type"] == "progress":
        await hass.async_block_till_done()
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        result = await response.json()
    assert result["type"] == "create_entry"
    assert calls == [{"entity_id": entity.entity_id, "version": "0.9.10"}]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue.issue_id) is None


async def test_update_retry_holds_when_signed_feed_artifact_changed(
    hass: HomeAssistant,
    repairs_ready: None,
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    original = feed_release_artifact(_build(102))
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.7-rc4 build 102",
        RuntimeError("staging failed"),
        artifact=asdict(original),
    )
    issue = next(
        item
        for (domain, _), item in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    )
    er.async_get(hass).async_get_or_create(
        "update", DOMAIN, update_unique_id(entry.entry_id)
    )
    calls = []

    async def installed(service) -> None:
        calls.append(service.data)

    hass.services.async_register("update", "install", installed)
    resolver = AsyncMock(return_value=replace(original, sha256="b" * 64))
    monkeypatch.setattr(panel_repairs, "async_resolve_install_choice", resolver)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": issue.issue_id},
    )
    menu = await response.json()
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}",
        json={"next_step_id": "retry"},
    )
    result = await response.json()
    if result["type"] == "progress":
        await hass.async_block_till_done()
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        result = await response.json()
    assert result["step_id"] == "retry_error"
    assert result["errors"] == {"base": "retry_release_changed"}
    assert calls == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue.issue_id) is not None
    resolver.assert_awaited_once_with(hass, original.tag)


@pytest.mark.parametrize("new_failure_after_retry", [False, True])
async def test_superseded_update_repair_retries_current_offer(
    hass: HomeAssistant,
    repairs_ready: None,
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    new_failure_after_retry: bool,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test panel", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    old = feed_release_artifact(_build(102))
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.7-rc4 build 102",
        RuntimeError("staging failed"),
        artifact=asdict(old),
    )
    issue_id = next(
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    )
    entity = er.async_get(hass).async_get_or_create(
        "update", DOMAIN, update_unique_id(entry.entry_id)
    )
    hass.states.async_set(
        entity.entity_id,
        "0.9.7-rc4",
        {
            "installed_version": "0.9.7-rc4 build 101",
            "latest_version": "0.9.7-rc4 build 103",
        },
    )
    calls = []

    async def installed(service) -> None:
        calls.append(service.data)
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, "0.9.7-rc4", 103
        )
        if new_failure_after_retry:
            await async_record_update_failure(
                hass,
                entry.entry_id,
                entry.title,
                "0.9.7-rc4 build 104",
                RuntimeError("next update failed"),
            )

    hass.services.async_register("update", "install", installed)
    resolver = AsyncMock(side_effect=AssertionError("old release must not be retried"))
    monkeypatch.setattr(panel_repairs, "async_resolve_install_choice", resolver)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    menu = await response.json()
    assert menu["menu_options"] == ["retry", "support_report", "clear_error"]
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}", json={"next_step_id": "retry"}
    )
    result = await response.json()
    if result["type"] == "progress":
        await hass.async_block_till_done()
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        result = await response.json()
    assert result["type"] == ("menu" if new_failure_after_retry else "create_entry")
    assert calls == [{"entity_id": entity.entity_id}]
    assert resolver.await_count == 0
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ) is new_failure_after_retry
    if new_failure_after_retry:
        assert (await async_failure_events(hass, issue_id))[-1][
            "reason"
        ] == "next update failed"


@pytest.mark.parametrize(
    "language", ["en", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"]
)
async def test_every_localized_failure_opens_a_labeled_repair_flow(
    hass: HomeAssistant, repairs_ready: None, language: str
) -> None:
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    for cause in (
        "authorization_failed",
        "preflight_rejected",
        "artifact_rejected",
        "transport_failed",
        "install_failed",
        "launch_failed",
        "health_check_failed",
        "ambiguous_mutation",
        "verification_required",
        "update",
        "retry_hold",
    ):
        prefix = f"component.{DOMAIN}.issues.installer_failure_{cause}"
        assert "{panel}" in strings[f"{prefix}.title"]
        assert strings[f"{prefix}.fix_flow.step.init.menu_options.retry"]
        assert strings[f"{prefix}.fix_flow.step.init.menu_options.clear_error"]
        description = strings[f"{prefix}.fix_flow.step.support_report.description"]
        assert description.startswith("### [")
        assert "](https://panel-assistant.io/go/support-report)" in description
        assert "](https://panel-assistant.io/go/report-issue)" in description
        assert "\n\n{report}" in description
        assert "```\n{report}\n```" not in description
        assert strings[f"{prefix}.fix_flow.error.retry_target_changed"]

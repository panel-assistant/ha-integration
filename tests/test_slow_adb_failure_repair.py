"""Failed ADB copies retain useful diagnostics and an actionable native Repair."""

from __future__ import annotations

import json
from typing import Any

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN, update_unique_id
from custom_components.panel_assistant.install_adb import (
    InstallAdbErrorCode,
    InstallAdbTransferError,
)
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    async_refresh_update_failure_name,
    async_restore_update_failure,
    panel_failure_issue_id,
)


@pytest.mark.parametrize("slow", [True, False], ids=["slow-copy", "normal-rate-copy"])
async def test_transfer_failure_survives_restart_and_opens_repair_with_safe_retry(
    hass: HomeAssistant,
    hass_client: Any,
    hass_storage: dict[str, Any],
    slow: bool,
) -> None:
    """Only measured slow failures get restart advice; both retain retry custody."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    sent = 3_500_000 if slow else 35_000_000
    transfer = InstallAdbTransferError(
        InstallAdbErrorCode.STAGE_AMBIGUOUS,
        reason="no_progress",
        bytes_sent=sent,
        elapsed_seconds=100.0,
        total_bytes=40_000_000,
    )
    raw_cause = TimeoutError("private transport detail: panel-secret.local")
    transfer.__cause__ = raw_cause
    error = RuntimeError("The panel update did not complete")
    error.__cause__ = transfer
    await async_record_update_failure(hass, entry.entry_id, entry.title, "1.0.0", error)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    expected_transfer = {
        "reason": "no_progress",
        "bytes_sent": sent,
        "elapsed_seconds": 100.0,
        "total_bytes": 40_000_000,
        "bytes_per_second": sent / 100.0,
        "slow": slow,
    }
    events = await async_failure_events(hass, issue_id)
    assert events[-1]["transfer"] == expected_transfer
    assert "private transport detail: panel-secret.local" in events[-1]["exception"]

    # Restore from the JSON-backed saved event, as on Home Assistant startup.
    await hass.async_block_till_done()
    store_key = f"{DOMAIN}.failure_repairs"
    hass_storage[store_key] = json.loads(json.dumps(hass_storage[store_key]))
    hass.data.pop(f"{DOMAIN}.failure_repair_store")
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    await async_restore_update_failure(hass, entry.entry_id)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Kitchen",
    )
    registry.async_update_device(device.id, name_by_user="Kitchen display")
    async_refresh_update_failure_name(hass, entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == (
        "installer_failure_update_slow_transfer" if slow else "installer_failure_update"
    )
    assert issue.data == {"key": issue_id}
    assert "panel-secret.local" not in json.dumps(issue.translation_placeholders)
    assert issue.translation_placeholders["panel"] == "Kitchen display"
    assert issue.learn_more_url.endswith("issue=installer_failure_update")

    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    menu = await response.json()
    assert response.status == 200
    assert menu["type"] == "menu"
    assert menu["menu_options"] == ["retry", "support_report", "clear_error"]
    translations = await async_get_translations(hass, "en", "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.{issue.translation_key}"
    description = translations[f"{prefix}.fix_flow.step.init.description"].format(
        **issue.translation_placeholders
    )
    assert "panel-secret.local" not in description
    if slow:
        assert "about 35 KB/s" in description
        assert "Restart the panel" in description
    else:
        assert "KB/s" not in description

    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}",
        json={"next_step_id": "support_report"},
    )
    form = await response.json()
    assert response.status == 200
    report = form["description_placeholders"]["report"]
    assert "events[0].transfer.reason: no_progress" in report
    assert f"events[0].transfer.bytes_sent: {sent}" in report
    assert "private transport detail: panel-secret.local" in report
    await admin.post(f"/api/repairs/issues/fix/{menu['flow_id']}", json={})

    entity = er.async_get(hass).async_get_or_create(
        "update", DOMAIN, update_unique_id(entry.entry_id)
    )
    calls = []

    async def installed(service: Any) -> None:
        calls.append(service.data)
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, "1.0.0", None
        )

    hass.services.async_register("update", "install", installed)
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
    assert result["type"] == "create_entry"
    assert calls == [{"entity_id": entity.entity_id, "version": "1.0.0"}]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_failure_recorder_handles_cyclic_exception_context(
    hass: HomeAssistant,
) -> None:
    """A malformed exception chain cannot prevent the ordinary failure Repair."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    error = RuntimeError("The update did not complete")
    context = TimeoutError()
    error.__context__ = context
    context.__cause__ = error
    await async_record_update_failure(hass, entry.entry_id, entry.title, "1.0.0", error)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    events = await async_failure_events(hass, issue_id)
    assert events[-1]["reason"] == "The update did not complete"
    assert "transfer" not in events[-1]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize(
    "language",
    ["en", "cs", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "uk", "zh-Hans"],
)
async def test_slow_transfer_repair_loads_rate_and_recovery_copy_in_every_locale(
    hass: HomeAssistant, language: str
) -> None:
    """Core must supply the measured-rate copy and all existing recovery actions."""
    assert await async_setup_component(hass, DOMAIN, {})
    translations = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.installer_failure_update_slow_transfer"
    assert "{panel}" in translations[f"{prefix}.title"]
    description = translations[f"{prefix}.fix_flow.step.init.description"].format(
        transfer_rate="35", repair_url="/recovery", learn_more_url="/more"
    )
    assert "35 KB/s" in description
    assert "](/recovery)" in description
    for option in ("retry", "support_report", "clear_error"):
        assert translations[f"{prefix}.fix_flow.step.init.menu_options.{option}"]
    assert (
        "{report}" in translations[f"{prefix}.fix_flow.step.support_report.description"]
    )

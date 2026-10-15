"""Every Repair can be acted on, and none outlives the panel it is about."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import repairs_failure
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_clear_adb_authorization,
    async_failure_events,
    async_record_update_failure,
    async_request_adb_authorization,
    panel_failure_issue_id,
)

GONE = "01M4E3HDSS02FM7P8HNEXHKHRE"


def _entry(hass: HomeAssistant, title: str = "Kitchen") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=title, data={CONF_ADDRESS: f"{title.lower()}.local"}
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def _raise(hass: HomeAssistant, issue_id: str, key: str, **kwargs: Any) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=kwargs.pop("is_fixable", False),
        is_persistent=kwargs.pop("is_persistent", False),
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders={"panel": "Alpha panel", "address": "a.local"},
        **kwargs,
    )


async def _open(admin: Any, issue_id: str) -> dict[str, Any]:
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    return await response.json()


async def _choose(
    hass: HomeAssistant, admin: Any, flow_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    response = await admin.post(f"/api/repairs/issues/fix/{flow_id}", json=body)
    assert response.status == 200
    result = await response.json()
    if result["type"] == "progress":
        await hass.async_block_till_done()
        response = await admin.post(f"/api/repairs/issues/fix/{flow_id}", json={})
        result = await response.json()
    return result


@pytest.fixture
async def repairs_ready(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def _held_update_repair(
    hass: HomeAssistant, admin: Any, entry: MockConfigEntry
) -> str:
    """An update failure whose Retry was held, as on production."""
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "1.0.0", RuntimeError("download failed")
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    menu = await _open(admin, issue_id)
    # No update entity exists, so the retry cannot be proven safe.
    await _choose(hass, admin, menu["flow_id"], {"next_step_id": "retry"})
    issue = _issue(hass, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_retry_hold"
    return issue_id


async def test_every_option_of_a_held_retry_answers(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    """Retry says why it is held, the report shows, and Clear removes it."""
    admin = await hass_client()
    issue_id = await _held_update_repair(hass, admin, _entry(hass))

    menu = await _open(admin, issue_id)
    assert menu["menu_options"] == ["retry", "support_report", "clear_error"]
    retry = await _choose(hass, admin, menu["flow_id"], {"next_step_id": "retry"})
    assert retry["step_id"] == "retry_error"
    assert retry["errors"] == {"base": "retry_update_unavailable"}

    menu = await _open(admin, issue_id)
    report = await _choose(
        hass, admin, menu["flow_id"], {"next_step_id": "support_report"}
    )
    assert report["step_id"] == "support_report"
    assert "kind: retry_hold" in report["description_placeholders"]["report"]
    assert (
        "reason: retry_update_unavailable"
        in (report["description_placeholders"]["report"])
    )

    menu = await _open(admin, issue_id)
    clear = await _choose(hass, admin, menu["flow_id"], {"next_step_id": "clear_error"})
    assert clear["step_id"] == "clear_error"
    done = await _choose(hass, admin, menu["flow_id"], {})
    assert done["type"] == "create_entry"
    assert _issue(hass, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []

    strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.installer_failure_retry_hold.fix_flow"
    for option in menu["menu_options"]:
        assert strings[f"{prefix}.step.init.menu_options.{option}"]
    for step in ("support_report", "clear_error", "retry_error"):
        assert strings[f"{prefix}.step.{step}.title"]
    assert strings[f"{prefix}.error.retry_update_unavailable"]


async def test_removing_a_panel_removes_every_repair_about_it(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    """Including the ones production kept: ADB approval, identity, address."""
    admin = await hass_client()
    gone, kept = _entry(hass, "Office"), _entry(hass, "Hall")
    held = await _held_update_repair(hass, admin, gone)
    for entry in (gone, kept):
        async_request_adb_authorization(hass, entry.entry_id)
        _raise(
            hass, f"panel_identity_mismatch_{entry.entry_id}", "panel_identity_mismatch"
        )
        _raise(hass, f"panel_address_{entry.entry_id}", "panel_address_unreachable")
    install = panel_failure_issue_id("install:SERIAL-1")
    _raise(hass, install, "installer_failure", is_fixable=True, data={"key": install})

    assert await hass.config_entries.async_remove(gone.entry_id)
    await hass.async_block_till_done()

    remaining = {
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    }
    assert remaining == {
        f"adb_update_authorization_{kept.entry_id}",
        f"panel_identity_mismatch_{kept.entry_id}",
        f"panel_address_{kept.entry_id}",
        install,
    }
    assert await async_failure_events(hass, held) == []


async def test_repairs_of_panels_removed_earlier_are_cleared_at_start(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A Repair left by an older release goes; a live panel's and an install's stay."""
    live = _entry(hass, "Hall")
    stale_failure = panel_failure_issue_id(f"update:{GONE}")
    hass_storage[f"{DOMAIN}.failure_repairs"] = {
        "version": 1,
        "key": f"{DOMAIN}.failure_repairs",
        "data": {
            stale_failure: {
                "events": [{"kind": "update", "entry_id": GONE, "reason": "x"}]
            }
        },
    }
    install = panel_failure_issue_id("install:SERIAL-1")
    _raise(
        hass,
        f"adb_update_authorization_{GONE}",
        "adb_update_authorization",
        is_fixable=True,
        is_persistent=True,
        data={"entry_id": GONE},
    )
    _raise(
        hass,
        stale_failure,
        "installer_failure_retry_hold",
        is_fixable=True,
        is_persistent=True,
        data={"key": stale_failure},
    )
    _raise(
        hass,
        f"adb_update_authorization_{live.entry_id}",
        "adb_update_authorization",
        is_fixable=True,
        is_persistent=True,
        data={"entry_id": live.entry_id},
    )
    _raise(hass, install, "installer_failure", is_fixable=True, data={"key": install})

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    remaining = {
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    }
    assert remaining >= {f"adb_update_authorization_{live.entry_id}", install}
    assert f"adb_update_authorization_{GONE}" not in remaining
    assert stale_failure not in remaining
    assert await async_failure_events(hass, stale_failure) == []


@pytest.mark.parametrize(
    ("key", "issue_id", "data"),
    [
        (
            "adb_update_authorization",
            f"adb_update_authorization_{GONE}",
            {"entry_id": GONE},
        ),
        (
            "installer_failure_retry_hold",
            panel_failure_issue_id(f"update:{GONE}"),
            {"key": panel_failure_issue_id(f"update:{GONE}")},
        ),
    ],
)
async def test_a_repair_opened_after_its_panel_went_clears_itself(
    hass: HomeAssistant,
    repairs_ready: None,
    hass_client: Any,
    key: str,
    issue_id: str,
    data: dict[str, str],
) -> None:
    """The owner is told the panel was removed, and the Repair is gone."""
    await repairs_failure._failure_store(hass).append(
        issue_id, {"kind": "update", "entry_id": GONE, "reason": "x"}
    )
    _raise(hass, issue_id, key, is_fixable=True, is_persistent=True, data=data)

    result = await _open(await hass_client(), issue_id)

    assert result["type"] == "abort"
    assert result["reason"] == "entry_removed"
    assert _issue(hass, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []
    strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
    assert strings[f"component.{DOMAIN}.issues.{key}.fix_flow.abort.entry_removed"]


async def test_allowing_updates_elsewhere_keeps_a_live_panels_repair(
    hass: HomeAssistant, repairs_ready: None, hass_client: Any
) -> None:
    """Approval granted while the menu is open is not the panel being removed."""
    admin = await hass_client()
    entry = _entry(hass)
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "1.0.0", RuntimeError("download failed")
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    async_request_adb_authorization(hass, entry.entry_id)
    menu = await _open(admin, issue_id)
    assert "authorize_adb" in menu["menu_options"]
    async_clear_adb_authorization(hass, entry.entry_id)

    result = await _choose(
        hass, admin, menu["flow_id"], {"next_step_id": "authorize_adb"}
    )

    assert result["type"] == "menu"
    assert result["menu_options"] == ["retry", "support_report", "clear_error"]
    assert _issue(hass, issue_id) is not None
    assert await async_failure_events(hass, issue_id)

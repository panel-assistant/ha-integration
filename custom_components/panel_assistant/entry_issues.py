"""Every Repair about one panel entry goes when the entry goes.

A Repair about a removed panel can never be acted on, and Home Assistant gives
the owner no way to delete one, so it would stay forever.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .repairs_failure import (
    async_delete_issue_and_report,
    async_update_failure_entry_ids,
    panel_failure_issue_id,
)


async def async_delete_entry_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete every Repair this integration raised about one entry."""
    registry = ir.async_get(hass)
    for (domain, issue_id), issue in list(registry.issues.items()):
        data = issue.data if isinstance(issue.data, dict) else {}
        if domain == DOMAIN and (
            issue_id.endswith(f"_{entry_id}") or data.get("entry_id") == entry_id
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    # An update failure's ID is a hash, so its saved report names the entry.
    await async_delete_issue_and_report(
        hass, panel_failure_issue_id(f"update:{entry_id}")
    )


async def async_reconcile_issues(hass: HomeAssistant) -> None:
    """Clear orphan Repairs and refresh links on retained persistent Repairs."""
    named = await async_update_failure_entry_ids(hass)
    for (domain, _), issue in ir.async_get(hass).issues.items():
        if domain == DOMAIN and isinstance(issue.data, dict):
            entry_id = issue.data.get("entry_id")
            if isinstance(entry_id, str):
                named.add(entry_id)
    for entry_id in named:
        if hass.config_entries.async_get_entry(entry_id) is None:
            await async_delete_entry_issues(hass, entry_id)
    # These issues can outlive the observation that raised them (for example,
    # the retired volume entity no longer exists). An upgrade must refresh
    # their presentation without retrying an operation or changing its record.
    for (domain, issue_id), issue in list(ir.async_get(hass).issues.items()):
        key = issue.translation_key
        if (
            domain != DOMAIN
            or not issue.is_persistent
            or key is None
            or issue.is_fixable is None
            or issue.severity is None
        ):
            continue
        url = help_url("repair", issue=key)
        if key == "installer_failure" or key.startswith("installer_failure_"):
            links = {
                "repair_url": url,
                "guide_url": help_url("support-report"),
                "approval_url": help_url("repair", issue="adb_update_authorization"),
            }
        elif key == "volume_number_removed":
            links = {
                "automations_url": "/config/automation/dashboard",
                "scripts_url": "/config/script/dashboard",
            }
        elif key == "panel_permissions":
            links = {"permissions_url": url}
        elif key == "adb_update_authorization":
            links = {"approval_url": url}
        elif key == "no_update_route":
            links = {"recovery_url": url}
        else:
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            breaks_in_ha_version=issue.breaks_in_ha_version,
            data=issue.data,
            is_fixable=issue.is_fixable,
            is_persistent=True,
            issue_domain=issue.issue_domain,
            learn_more_url=url,
            severity=issue.severity,
            translation_key=key,
            translation_placeholders={
                **(issue.translation_placeholders or {}),
                **links,
                "learn_more_url": url,
            },
        )

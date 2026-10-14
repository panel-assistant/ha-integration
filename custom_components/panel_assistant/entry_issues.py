"""Every Repair about one panel entry goes when the entry goes.

A Repair about a removed panel can never be acted on, and Home Assistant gives
the owner no way to delete one, so it would stay forever.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
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


async def async_delete_orphan_issues(hass: HomeAssistant) -> None:
    """Clear the Repairs of entries removed before removal cleared them."""
    named = await async_update_failure_entry_ids(hass)
    for (domain, _), issue in ir.async_get(hass).issues.items():
        if domain == DOMAIN and isinstance(issue.data, dict):
            entry_id = issue.data.get("entry_id")
            if isinstance(entry_id, str):
                named.add(entry_id)
    for entry_id in named:
        if hass.config_entries.async_get_entry(entry_id) is None:
            await async_delete_entry_issues(hass, entry_id)

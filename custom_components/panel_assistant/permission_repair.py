"""Keep missing supported grants separate from successful APK installation."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .status import PanelStatus, permissions_held

ISSUE_PANEL_PERMISSIONS = "panel_permissions"


def permission_issue_id(entry_id: str) -> str:
    """Keep commissioning attached to the configured panel across address changes."""
    return f"{ISSUE_PANEL_PERMISSIONS}_{entry_id}"


def async_reconcile_permission_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Use accepted, fresh grant observations; unknown readings retain the issue."""
    issue_id = permission_issue_id(entry.entry_id)
    if status.permissions is not None and "missing" in status.permissions.values():
        from .device import panel_display_name

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PANEL_PERMISSIONS,
            translation_placeholders={"panel": panel_display_name(hass, entry)},
            data={"entry_id": entry.entry_id},
        )
    elif permissions_held(status):
        ir.async_delete_issue(hass, DOMAIN, issue_id)

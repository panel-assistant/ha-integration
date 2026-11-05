"""Tell the owner when the panel app was stopped part way through a restore.

Android kills the app whenever an app-op or permission changes, which a
restore can trigger. The app then reports the restore as interrupted until
the next destructive operation. Only the owner can restore the backup again,
from the panel's own page, so this Repair says so and clears itself once the
panel stops reporting it.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .update_coordinator import PanelUpdateSnapshot

ISSUE_RESTORE_INTERRUPTED = "panel_restore_interrupted"


def restore_issue_id(entry_id: str) -> str:
    """One issue per configured panel."""
    return f"{ISSUE_RESTORE_INTERRUPTED}_{entry_id}"


def async_reconcile_restore_issue(
    hass: HomeAssistant, entry: ConfigEntry, snapshot: PanelUpdateSnapshot | None
) -> None:
    """Raise or clear the issue from a successful read; a failed read keeps it."""
    if snapshot is None or snapshot.error is not None or snapshot.operation is None:
        return
    issue_id = restore_issue_id(entry.entry_id)
    if not snapshot.operation.restore_interrupted:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    from .device import panel_display_name, sidebar_url

    url = help_url("repair", issue=ISSUE_RESTORE_INTERRUPTED)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RESTORE_INTERRUPTED,
        learn_more_url=url,
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "install_url": sidebar_url(entry.entry_id, "install"),
            "learn_more_url": url,
        },
        data={"entry_id": entry.entry_id},
    )

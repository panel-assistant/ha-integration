"""Say why a panel is showing its dashboard without entity filtering.

A panel learns which entities its dashboard uses and asks Home Assistant for
only those. Until it can, it holds the dashboard behind a screen that says
what it is waiting for, and once that hold has stood for its time limit it
opens the dashboard unfiltered rather than leave the panel without one. It
keeps working on the cause and returns to the filtered dashboard on its own.
While it runs unfiltered, this Repair says so and where to look, and it
clears itself when the panel reports the hold has ended.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .status import PanelStatus

ISSUE_DASHBOARD_UNFILTERED = "dashboard_unfiltered"


def dashboard_unfiltered_issue_id(entry_id: str) -> str:
    """One issue per configured panel, whatever the hold was."""
    return f"{ISSUE_DASHBOARD_UNFILTERED}_{entry_id}"


def dashboard_released_unfiltered(status: PanelStatus | None) -> bool:
    """Whether the panel reports its dashboard released from an entity hold."""
    renderer = None if status is None else status.renderer
    return renderer is not None and renderer.get("entity_hold_state") == "released"


def async_reconcile_entity_hold_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Raise the issue while the dashboard runs unfiltered; clear it after."""
    issue_id = dashboard_unfiltered_issue_id(entry.entry_id)
    if not dashboard_released_unfiltered(status):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    from .device import panel_display_name, sidebar_url

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DASHBOARD_UNFILTERED,
        learn_more_url=help_url("repair", issue=ISSUE_DASHBOARD_UNFILTERED),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "entities_url": sidebar_url(entry.entry_id, "entities"),
        },
        data={"entry_id": entry.entry_id},
    )

"""Say why a panel is showing its dashboard without entity filtering.

A panel learns which entities its dashboard uses and asks Home Assistant for
only those. Until it can, it holds the dashboard behind a screen that says
what it is waiting for, and once that hold has stood for its time limit it
opens the dashboard unfiltered rather than leave the panel without one. It
keeps working on the cause and returns to the filtered dashboard on its own.

That usually takes minutes (a panel can report a short release as its app
starts), so a release is a log line. Only a dashboard still unfiltered after
``UNFILTERED_REPAIR_AFTER`` raises this Repair, which says where to look; it
clears itself when the panel reports the hold has ended.
"""

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN, help_url
from .status import PanelStatus

_LOGGER = logging.getLogger(__name__)

ISSUE_DASHBOARD_UNFILTERED = "dashboard_unfiltered"
UNFILTERED_REPAIR_AFTER = timedelta(minutes=30)
# When each panel was first seen unfiltered in its current release, by entry id.
DATA_UNFILTERED_SINCE = "dashboard_unfiltered_since"


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
    """Log a release, raise the issue if it lasts; clear it when it ends."""
    if hass.config_entries.async_get_entry(entry.entry_id) is not entry:
        return
    issue_id = dashboard_unfiltered_issue_id(entry.entry_id)
    since: dict[str, datetime] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_UNFILTERED_SINCE, {}
    )
    if not dashboard_released_unfiltered(status):
        since.pop(entry.entry_id, None)
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    from .device import panel_display_name, sidebar_url

    now = dt_util.utcnow()
    if entry.entry_id not in since:
        since[entry.entry_id] = now
        _LOGGER.warning(
            "%s is showing its dashboard without entity filtering until it can "
            "finish preparing its entity filter",
            panel_display_name(hass, entry),
        )
    if now - since[entry.entry_id] < UNFILTERED_REPAIR_AFTER:
        return
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

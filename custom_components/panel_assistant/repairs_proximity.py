"""Lead the owner into proximity setup when the panel says it needs it.

A panel whose proximity sensor has never been calibrated cannot wake on a
wave, and on panels whose screen switches fully off (the Shelly X2i) a wave
is one of the ways back. Calibration needs a person in front of the panel,
so it cannot be done for them; it can be started from the panel's own page,
which Panel Assistant's sidebar opens. This Repair is the setup step that
says so, and it clears itself once the panel reports a saved calibration.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .status import PanelStatus

ISSUE_PROXIMITY_SETUP = "panel_proximity_setup"


def proximity_issue_id(entry_id: str) -> str:
    """One issue per configured panel, kept across address changes."""
    return f"{ISSUE_PROXIMITY_SETUP}_{entry_id}"


def proximity_state(status: PanelStatus | None) -> str | None:
    """The panel's calibration state, or None when it reports no sensor."""
    if status is None or status.proximity is None:
        return None
    state = status.proximity.get("state")
    return state if isinstance(state, str) else None


def async_reconcile_proximity_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Raise the issue on ``calibration_required``; clear it once it is moot.

    A calibration in progress leaves the issue as it is, so a person part way
    through setup is neither nagged nor told it is done.
    """
    issue_id = proximity_issue_id(entry.entry_id)
    state = proximity_state(status)
    if state == "calibration_required":
        from .device import panel_display_name

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PROXIMITY_SETUP,
            learn_more_url=help_url("repair", issue=ISSUE_PROXIMITY_SETUP),
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "learn_more_url": help_url("repair", issue=ISSUE_PROXIMITY_SETUP),
            },
            data={"entry_id": entry.entry_id},
        )
    elif state != "calibrating":
        ir.async_delete_issue(hass, DOMAIN, issue_id)

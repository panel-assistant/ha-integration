"""Tell the owner how to set up proximity when the panel says it needs it.

A panel whose proximity sensor has never been calibrated cannot wake on a
wave. Proximity is optional: touch always wakes the panel, and a panel asks
for setup only while its Wake on wave is on. Calibration needs a person in
front of the panel, so it cannot be done for them; it starts from the panel's
own Presence & wake card, which Panel Assistant's sidebar opens.

The Repair has no fix flow, so Home Assistant offers Ignore on it, and it
clears itself once the panel reports a saved calibration or stops asking.
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
    """The panel's calibration state, or None when it reports none.

    A panel reports none when it has no proximity sensor, or when nothing it
    is set to do needs a calibration.
    """
    if status is None or status.proximity is None:
        return None
    state = status.proximity.get("state")
    return state if isinstance(state, str) else None


def async_reconcile_proximity_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Raise the issue on ``calibration_required``; clear it once it is moot.

    A calibration in progress, or a sensor that is briefly unreadable, leaves
    the issue as it is: a person part way through setup is neither nagged nor
    told it is done, and an owner who ignored the issue is not shown it afresh
    when the sensor reads again. Home Assistant forgets an ignore when the
    issue is deleted.
    """
    issue_id = proximity_issue_id(entry.entry_id)
    state = proximity_state(status)
    if state == "calibration_required":
        # The device module imports the coordinator, which imports this one.
        from .device import PROXIMITY_SETUP_ANCHOR, panel_display_name, sidebar_url

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PROXIMITY_SETUP,
            learn_more_url=help_url("repair", issue=ISSUE_PROXIMITY_SETUP),
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "configure_url": sidebar_url(
                    entry.entry_id, "configure", PROXIMITY_SETUP_ANCHOR
                ),
                "learn_more_url": help_url("repair", issue=ISSUE_PROXIMITY_SETUP),
            },
            data={"entry_id": entry.entry_id},
        )
    elif state not in ("calibrating", "source_unavailable"):
        ir.async_delete_issue(hass, DOMAIN, issue_id)

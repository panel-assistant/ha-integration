"""Report a panel whose move to the new app did not finish, and let it finish.

Installing the successor beside the legacy package starts a handover the panel
carries out itself: it pulls its own state across, releases port 8888, and only
then removes the old app. The handover is re-entrant, so a panel that is still
part-way through is not broken and nothing here mutates it. What this reports is
that Home Assistant stopped watching before the successor answered, which is the
one thing a person has to be told rather than left to notice.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_PANEL_MIGRATION_INCOMPLETE: Final = "panel_migration_incomplete"

ISSUE_DATA_ADDRESS: Final = "address"
ISSUE_DATA_VERSION: Final = "version"


def panel_migration_issue_id(job_id: str) -> str:
    """Return the Repairs issue ID for one installation job's handover."""
    return f"{ISSUE_PANEL_MIGRATION_INCOMPLETE}_{job_id}"


@callback
def async_raise_panel_migration_incomplete(
    hass: HomeAssistant, job_id: str, address: str, version: str
) -> None:
    """Report that the successor had not taken over before the wait ran out."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        panel_migration_issue_id(job_id),
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PANEL_MIGRATION_INCOMPLETE,
        translation_placeholders={"address": address, "version": version},
        data={
            ISSUE_DATA_ADDRESS: address,
            ISSUE_DATA_VERSION: version,
        },
    )


@callback
def async_delete_panel_migration_incomplete(hass: HomeAssistant, job_id: str) -> None:
    """Withdraw the report once the successor has answered for itself."""
    ir.async_delete_issue(hass, DOMAIN, panel_migration_issue_id(job_id))

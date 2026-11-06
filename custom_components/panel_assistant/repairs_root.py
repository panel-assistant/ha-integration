"""Report root that stopped answering in this boot, with a guarded restart."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .panel_operations import claim_panel_operation, release_panel_operation
from .status import PanelStatus
from .transport_protocol import CAPABILITY_COMMANDS
from .transport_sessions import async_get_sessions, async_send_command

ISSUE_PANEL_ROOT_LOST = "panel_root_lost"


def root_issue_id(entry_id: str) -> str:
    """Keep one root-loss warning per panel."""
    return f"{ISSUE_PANEL_ROOT_LOST}_{entry_id}"


@callback
def async_reconcile_root_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Only an explicit observation raises or clears the warning."""
    from .device import panel_display_name

    issue_id = root_issue_id(entry.entry_id)
    if status.root_lost_since_boot is True:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PANEL_ROOT_LOST,
            learn_more_url=help_url("repair", issue=ISSUE_PANEL_ROOT_LOST),
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "learn_more_url": help_url("repair", issue=ISSUE_PANEL_ROOT_LOST),
            },
            data={"entry_id": entry.entry_id},
        )
    elif status.root_lost_since_boot is False:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


class PanelRootLostFlow(RepairsFlow):
    """Restart only with fresh loss and explicit absence of a screen lock."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        # Core supplies issue metadata, never restart consent, on init.
        return await self.async_step_confirm_restart()

    async def async_step_confirm_restart(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        from .device import panel_display_name

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="entry_removed")
        if not claim_panel_operation(self.hass, self._entry_id):
            return self.async_abort(reason="panel_busy")
        try:
            from .coordinator import HaPaneldDataUpdateCoordinator

            coordinator = getattr(
                getattr(entry, "runtime_data", None), "coordinator", None
            )
            if not isinstance(coordinator, HaPaneldDataUpdateCoordinator):
                return self.async_abort(reason="root_unreadable")
            session = async_get_sessions(self.hass).get(self._entry_id)
            previous = coordinator.data
            await coordinator.async_refresh()
            snapshot = coordinator.data
            if (
                self.hass.config_entries.async_get_entry(self._entry_id) is not entry
                or not coordinator.last_update_success
                or snapshot is None
                or snapshot is previous
                or snapshot.status is None
                or snapshot.status.root_lost_since_boot is None
            ):
                return self.async_abort(reason="root_unreadable")
            if snapshot.status.root_lost_since_boot is False:
                return self.async_create_entry(data={})
            if (
                snapshot.status.screen_lock_secure is not False
                or session is None
                or async_get_sessions(self.hass).get(self._entry_id) is not session
                or not session.full_sync_complete
                or CAPABILITY_COMMANDS not in session.capabilities
            ):
                return self.async_abort(reason="restart_unavailable")
            if user_input is not None:
                try:
                    await async_send_command(self.hass, session, "reboot", None)
                except HomeAssistantError:
                    return self.async_abort(reason="restart_failed")
                # Completing a Repair deletes its issue; only fresh status can
                # prove restoration, so close the dialog without completing it.
                return self.async_abort(reason="restart_started")
            return self.async_show_form(
                step_id="confirm_restart",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "panel": panel_display_name(self.hass, entry)
                },
                last_step=True,
            )
        finally:
            release_panel_operation(self.hass, self._entry_id)

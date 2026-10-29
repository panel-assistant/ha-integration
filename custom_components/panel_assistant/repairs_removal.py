"""Owner-requested panel removal, confirmed through Repairs before any change."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN, help_url
from .device import panel_display_name
from .removal import (
    REASON_ADB_AUTHORIZATION,
    RemovalError,
    async_read_firmware,
    async_remove_app,
)
from .repairs_update_route import help_parameters
from .status import PanelStatus

ISSUE_REMOVE_FROM_PANEL = "remove_from_panel"


def removal_issue_id(entry_id: str) -> str:
    """Keep one owner-started removal request attached to its panel."""
    return f"{ISSUE_REMOVE_FROM_PANEL}_{entry_id}"


def removal_notification_id(entry_id: str) -> str:
    """Name the notice that points the owner at the removal Repair."""
    return f"{DOMAIN}_{ISSUE_REMOVE_FROM_PANEL}_{entry_id}"


@callback
def async_dismiss_removal_notification(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the notice once its Repair has ended."""
    persistent_notification.async_dismiss(hass, removal_notification_id(entry_id))


async def async_request_removal(
    hass: HomeAssistant, entry: ConfigEntry, *, notify: bool = True
) -> None:
    """Create the confirmation without contacting or changing the panel.

    A notification answers the press visibly; the Repair itself is the only
    route to removal.
    """
    panel = panel_display_name(hass, entry)
    ir.async_create_issue(
        hass,
        DOMAIN,
        removal_issue_id(entry.entry_id),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_REMOVE_FROM_PANEL,
        learn_more_url=help_url("removal-risks"),
        translation_placeholders={
            "panel": panel,
            "learn_more_url": help_url("removal-risks"),
        },
        data={"entry_id": entry.entry_id},
    )
    if not notify:
        return
    prefix = f"component.{DOMAIN}.common.remove_from_panel_notification"
    strings = await async_get_translations(
        hass, hass.config.language, "common", [DOMAIN]
    )
    title = strings.get(f"{prefix}_title", "Remove Panel Assistant from {panel}")
    message = strings.get(
        f"{prefix}_message",
        "To remove Panel Assistant from {panel}, open [Repairs]({repairs_url}) "
        "and confirm there. Nothing has changed on the panel yet.",
    )
    persistent_notification.async_create(
        hass,
        message.format(panel=panel, repairs_url="/config/repairs"),
        title=title.format(panel=panel),
        notification_id=removal_notification_id(entry.entry_id),
    )


class RemoveFromPanelFlow(RepairsFlow):
    """Keep an interrupted removal retryable until neither app remains."""

    def __init__(self, entry_id: str) -> None:
        from .config_flow import AdbAuthorization

        self._entry_id = entry_id
        self._adb_authorization = AdbAuthorization()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Let the owner remove the app, or withdraw the request and keep it."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return await self.async_step_confirm()
        return self.async_show_menu(
            step_id="init",
            menu_options=["confirm", "keep_app"],
            description_placeholders={"panel": panel_display_name(self.hass, entry)},
        )

    async def async_step_keep_app(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Withdraw the request; the panel and its entry are left untouched."""
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        async_dismiss_removal_notification(self.hass, self._entry_id)
        return self.async_create_entry(data={})

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            async_dismiss_removal_notification(self.hass, self._entry_id)
            return self.async_create_entry(data={})
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_remove_app(self.hass, entry)
            except RemovalError as err:
                if err.reason == REASON_ADB_AUTHORIZATION:
                    # Ask the panel to allow Home Assistant here, then come back.
                    return await self.async_step_authorize_adb()
                errors["base"] = f"removal_{err.reason}"
            else:
                # Keep the persistent request until entry removal has completed.
                # A restart before then leaves the same Repair available to retry.
                await self.hass.config_entries.async_remove(entry.entry_id)
                if self.hass.config_entries.async_get_entry(entry.entry_id) is None:
                    async_dismiss_removal_notification(self.hass, entry.entry_id)
                    return self.async_create_entry(data={})
                errors["base"] = "removal_remove_failed"
        coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
        status = getattr(getattr(coordinator, "data", None), "status", None)
        parameters = help_parameters(
            status if isinstance(status, PanelStatus) else None
        )
        firmware = await async_read_firmware(self.hass, entry)
        if firmware is not None:
            parameters["fw"] = firmware
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "panel": panel_display_name(self.hass, entry),
                "risks_url": help_url("removal-risks", **parameters),
                "learn_more_url": help_url("removal-risks", **parameters),
            },
            errors=errors,
        )

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Let the panel allow Home Assistant, then return to the removal."""
        return await self._async_authorize_adb(user_input, done=False)

    async def async_step_authorize_adb_done(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Show the wait's outcome, ignoring progress input."""
        return await self._async_authorize_adb(None, done=True)

    async def _async_authorize_adb(
        self, user_input: dict[str, Any] | None, *, done: bool
    ) -> RepairsFlowResult:
        from .config_flow import async_authorize_adb_form

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return await self.async_step_confirm()
        form = await async_authorize_adb_form(
            self,
            entry,
            panel_display_name(self.hass, entry),
            user_input,
            self._adb_authorization,
            done=done,
        )
        if form is not None:
            return form
        return await self.async_step_confirm()

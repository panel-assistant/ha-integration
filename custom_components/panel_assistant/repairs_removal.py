"""Owner-requested panel removal, confirmed through Repairs before any change."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .device import panel_display_name
from .removal import RemovalError, async_read_firmware, async_remove_app
from .repairs_update_route import help_parameters
from .status import PanelStatus

ISSUE_REMOVE_FROM_PANEL = "remove_from_panel"


def removal_issue_id(entry_id: str) -> str:
    """Keep one owner-started removal request attached to its panel."""
    return f"{ISSUE_REMOVE_FROM_PANEL}_{entry_id}"


@callback
def async_request_removal(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the confirmation without contacting or changing the panel."""
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
            "panel": panel_display_name(hass, entry),
            "learn_more_url": help_url("removal-risks"),
        },
        data={"entry_id": entry.entry_id},
    )


class RemoveFromPanelFlow(RepairsFlow):
    """Keep an interrupted removal retryable until neither app remains."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_create_entry(data={})
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_remove_app(self.hass, entry)
            except RemovalError as err:
                errors["base"] = f"removal_{err.reason}"
            else:
                # Keep the persistent request until entry removal has completed.
                # A restart before then leaves the same Repair available to retry.
                await self.hass.config_entries.async_remove(entry.entry_id)
                if self.hass.config_entries.async_get_entry(entry.entry_id) is None:
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

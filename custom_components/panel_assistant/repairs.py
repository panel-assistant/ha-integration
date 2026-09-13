"""Repairs for Panel Assistant: an administrator binds a panel to its user."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownStep

from .const import CONF_TRANSPORT_USER_ID
from .transport import (
    ISSUE_DATA_ENTRY_ID,
    ISSUE_DATA_USER_ID,
    ISSUE_PANEL_USER_MISMATCH,
    async_bind_user,
    async_delete_binding_issue,
)

ABORT_ENTRY_REMOVED = "entry_removed"
ABORT_USER_UNAVAILABLE = "user_unavailable"


class PanelUserBindingFlow(RepairsFlow):
    """Confirm the user a panel asked to connect as, then bind the panel to it.

    The user is the one the issue named when this flow started. A later
    ``hello`` from someone else changes the issue, never this flow, so the
    administrator binds exactly the user they were shown.
    """

    def __init__(self, entry_id: str, user_id: str) -> None:
        """Remember the entry and the user this flow was started for."""
        self._entry_id = entry_id
        self._user_id = user_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Choose the confirmation that fits the entry's current binding."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.data.get(CONF_TRANSPORT_USER_ID) in (
            None,
            self._user_id,
        ):
            return await self.async_step_confirm_bind()
        return await self.async_step_confirm_rebind()

    async def async_step_confirm_bind(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm binding a panel that has no user yet."""
        return await self._async_confirm("confirm_bind", user_input)

    async def async_step_confirm_rebind(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Confirm moving a panel from its current user to another."""
        return await self._async_confirm("confirm_rebind", user_input)

    async def _async_confirm(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> RepairsFlowResult:
        # Everything is checked again on submit: the user or the entry may have
        # gone while the form was open.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            async_delete_binding_issue(self.hass, self._entry_id)
            return self.async_abort(reason=ABORT_ENTRY_REMOVED)
        user = await self.hass.auth.async_get_user(self._user_id)
        if user is None or not user.is_active or user.system_generated:
            # Core keeps an issue whose flow aborts, but a request that can
            # never be confirmed is only noise. A newer request stays.
            async_delete_binding_issue(self.hass, self._entry_id, self._user_id)
            return self.async_abort(reason=ABORT_USER_UNAVAILABLE)
        if user_input is not None:
            async_bind_user(self.hass, entry, user.id)
            return self.async_create_entry(data={})

        placeholders = {"panel": entry.title, "user": user.name or ""}
        if step_id == "confirm_rebind":
            bound_id = entry.data.get(CONF_TRANSPORT_USER_ID)
            bound = await self.hass.auth.async_get_user(bound_id) if bound_id else None
            bound_name = bound.name if bound is not None else None
            placeholders["bound_user"] = bound_name or ""
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Panel Assistant issue."""
    entry_id = (data or {}).get(ISSUE_DATA_ENTRY_ID)
    user_id = (data or {}).get(ISSUE_DATA_USER_ID)
    if (
        not issue_id.startswith(f"{ISSUE_PANEL_USER_MISMATCH}_")
        or not isinstance(entry_id, str)
        or not isinstance(user_id, str)
    ):
        raise UnknownStep
    return PanelUserBindingFlow(entry_id, user_id)

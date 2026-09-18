"""Repairs for Panel Assistant: an administrator binds a panel to its user."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownStep
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .app_identity import SUCCESSOR_PACKAGE_ID
from .client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidAddressError,
    InvalidResponseError,
    normalize_address,
)
from .const import CONF_TRANSPORT_USER_ID
from .migration_repair import (
    ISSUE_DATA_ADDRESS,
    ISSUE_DATA_VERSION,
    ISSUE_PANEL_MIGRATION_INCOMPLETE,
)
from .transport import (
    ISSUE_DATA_ENTRY_ID,
    ISSUE_DATA_USER_ID,
    ISSUE_PANEL_USER_MISMATCH,
    async_bind_user,
    async_delete_binding_issue,
)

ABORT_ENTRY_REMOVED = "entry_removed"
ABORT_USER_UNAVAILABLE = "user_unavailable"
ABORT_MIGRATION_UNFINISHED = "migration_unfinished"
_NOT_SHOWN = object()


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
        # The binding the shown form described. Submitting it binds only if the
        # entry is still bound that way.
        self._shown_binding: object = _NOT_SHOWN

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
        bound_id = entry.data.get(CONF_TRANSPORT_USER_ID)
        if user_input is not None:
            if bound_id != self._shown_binding:
                # Someone changed the binding while this form was open, so the
                # administrator confirmed something that is no longer true.
                return await self.async_step_init()
            async_bind_user(self.hass, entry, user.id)
            return self.async_create_entry(data={})

        self._shown_binding = bound_id
        placeholders = {"panel": entry.title, "user": user.name or ""}
        if step_id == "confirm_rebind":
            bound = await self.hass.auth.async_get_user(bound_id) if bound_id else None
            bound_name = bound.name if bound is not None else None
            placeholders["bound_user"] = bound_name or ""
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
        )


class PanelMigrationFlow(RepairsFlow):
    """Ask the panel, once, whether its new app has taken over yet.

    This flow reads and never writes. The handover is the panel's own and is
    re-entrant, so the useful thing a person can do here is check again: either
    the new app answers for itself, and the issue goes, or it does not yet, and
    they are told that rather than being offered a button that changes nothing.
    """

    def __init__(self, address: str, version: str) -> None:
        """Remember the panel address and the version this job installed."""
        self._address = address
        self._version = version

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Show one confirmation that re-reads the panel when submitted.

        The manager starts this step with the issue's own data, never with an
        administrator's submission, so the form is always shown first.
        """
        return await self.async_step_confirm_migration()

    async def async_step_confirm_migration(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Re-read the panel and finish only when the new app answers."""
        if user_input is not None:
            if await self._async_successor_answered():
                return self.async_create_entry(data={})
            return self.async_abort(reason=ABORT_MIGRATION_UNFINISHED)
        return self.async_show_form(
            step_id="confirm_migration",
            data_schema=vol.Schema({}),
            description_placeholders={
                "address": self._address,
                "version": self._version,
            },
        )

    async def _async_successor_answered(self) -> bool:
        try:
            address = normalize_address(self._address)
        except InvalidAddressError:
            return False
        client = HaPaneldClient(async_get_clientsession(self.hass), address)
        try:
            health = await client.async_get_health()
        except CannotConnectError, InvalidResponseError:
            return False
        return (
            health.package == SUCCESSOR_PACKAGE_ID and health.version == self._version
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Panel Assistant issue."""
    values = data or {}
    if issue_id.startswith(f"{ISSUE_PANEL_MIGRATION_INCOMPLETE}_"):
        address = values.get(ISSUE_DATA_ADDRESS)
        version = values.get(ISSUE_DATA_VERSION)
        if not isinstance(address, str) or not isinstance(version, str):
            raise UnknownStep
        return PanelMigrationFlow(address, version)
    entry_id = values.get(ISSUE_DATA_ENTRY_ID)
    user_id = values.get(ISSUE_DATA_USER_ID)
    if (
        not issue_id.startswith(f"{ISSUE_PANEL_USER_MISMATCH}_")
        or not isinstance(entry_id, str)
        or not isinstance(user_id, str)
    ):
        raise UnknownStep
    return PanelUserBindingFlow(entry_id, user_id)

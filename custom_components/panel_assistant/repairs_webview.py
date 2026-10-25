"""Owner-started WebView updates on the panel's authenticated native session."""

from __future__ import annotations

import asyncio
from secrets import token_hex
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .client import CannotConnectError, InvalidResponseError
from .const import CONF_TRANSPORT_USER_ID, DOMAIN, help_url
from .status import PanelStatus
from .transport_sessions import (
    PanelSession,
    async_get_sessions,
    async_manage,
    session_manages,
)
from .webview import ACTIVE_STATES, WebViewStatus, parse_webview

ISSUE_WEBVIEW = "panel_webview"


def webview_issue_id(entry_id: str) -> str:
    """One persistent issue, independent of address or provider attempts."""
    return f"{ISSUE_WEBVIEW}_{entry_id}"


def async_reconcile_webview_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Read-only reconciliation; pairing always precedes this advisory Repair."""
    if CONF_TRANSPORT_USER_ID not in entry.data:
        ir.async_delete_issue(hass, DOMAIN, webview_issue_id(entry.entry_id))
        return
    observation = status.webview
    if observation is None:
        return  # Unknown/older status cannot prove that a persistent issue is fixed.
    _reconcile(hass, entry, observation)


def _reconcile(hass: HomeAssistant, entry: ConfigEntry, status: WebViewStatus) -> None:
    issue_id = webview_issue_id(entry.entry_id)
    if status.needs_attention or status.transaction_state in ACTIVE_STATES:
        from .device import panel_display_name

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_WEBVIEW,
            learn_more_url=help_url("repair", issue=ISSUE_WEBVIEW),
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "learn_more_url": help_url("repair", issue=ISSUE_WEBVIEW),
            },
            data={"entry_id": entry.entry_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


class WebViewRepairFlow(RepairsFlow):
    """One explicit start, then bounded read-only progress owned by this flow."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        self._task: asyncio.Task[str | None] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_update_webview()

    async def async_step_update_webview(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            if self._task is None:
                self._task = self.hass.async_create_task(
                    self._async_update(), f"WebView repair {self._entry_id}"
                )
            return await self.async_step_webview_progress()
        return self._show_form()

    def _show_form(self, error: str | None = None) -> RepairsFlowResult:
        from .device import panel_display_name

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            ir.async_delete_issue(self.hass, DOMAIN, webview_issue_id(self._entry_id))
            return self.async_abort(reason="entry_removed")
        return self.async_show_form(
            step_id="update_webview",
            data_schema=vol.Schema({}),
            errors={"base": error} if error else {},
            description_placeholders={
                "panel": panel_display_name(self.hass, entry),
                "learn_more_url": help_url("repair", issue=ISSUE_WEBVIEW),
            },
        )

    async def async_step_webview_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if self._task is None:
            return self._show_form()
        if self._task.done():
            return self.async_show_progress_done(next_step_id="webview_result")
        return self.async_show_progress(
            step_id="webview_progress",
            progress_action="updating_webview",
            progress_task=self._task,
        )

    async def async_step_webview_result(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if self._task is None:
            return self._show_form()
        try:
            reason = self._task.result()
        except Exception:
            reason = "webview_unavailable"
        self._task = None
        if self.hass.config_entries.async_get_entry(self._entry_id) is None:
            return self._show_form()
        if reason is None:
            return self.async_create_entry(data={})
        return self._show_form(reason)

    def _session(
        self, entry: ConfigEntry, user_id: object, did: str | None
    ) -> PanelSession | None:
        """Recheck the same entry, binding and authenticated identity each time."""
        session = async_get_sessions(self.hass).get(entry.entry_id)
        if (
            self.hass.config_entries.async_get_entry(entry.entry_id) is not entry
            or not user_id
            or entry.data.get(CONF_TRANSPORT_USER_ID) != user_id
            or entry.unique_id != did
            or (
                session is not None
                and (session.did != did or session.user_id != user_id)
            )
        ):
            raise ValueError("panel_unavailable")
        # A provider rebind may temporarily remove its native session. Missing
        # transport is different from the entry, account or identity retiring.
        return session if session_manages(session) else None

    async def _async_update(self) -> str | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return "entry_removed"
        user_id, did = entry.data.get(CONF_TRANSPORT_USER_ID), entry.unique_id

        async def observe() -> WebViewStatus:
            session = self._session(entry, user_id, did)
            if session is None:
                raise ConnectionError("panel_reconnecting")
            status = parse_webview(
                await async_manage(self.hass, session, "webview.observe")
            )
            self._session(entry, user_id, did)
            return status

        try:
            async with asyncio.timeout(120):
                status = await observe()
                if (
                    status.transaction_state not in ACTIVE_STATES
                    and status.supported
                    and status.eligible
                    and status.candidate_sha256
                ):
                    session = self._session(entry, user_id, did)
                    if session is None:
                        raise ConnectionError("panel_reconnecting")
                    status = parse_webview(
                        await async_manage(
                            self.hass,
                            session,
                            "webview.install",
                            request_id=f"pa-repair-{token_hex(16)}",
                            artifact_sha256=status.candidate_sha256,
                        )
                    )
                    self._session(entry, user_id, did)
                while status.transaction_state in ACTIVE_STATES:
                    await asyncio.sleep(1)
                    try:
                        status = await observe()
                    except (
                        HomeAssistantError,
                        CannotConnectError,
                        InvalidResponseError,
                        ConnectionError,
                    ):
                        # The accepted update may restart its process. Reacquire
                        # only the same bound identity; retirement still aborts.
                        self._session(entry, user_id, did)
                        continue
                _reconcile(self.hass, entry, status)
                return "webview_deferred" if status.needs_attention else None
        except Exception:
            # The app owns a possibly committed operation. Progress never posts
            # again; another explicit press always starts with fresh observation.
            return "webview_unavailable"

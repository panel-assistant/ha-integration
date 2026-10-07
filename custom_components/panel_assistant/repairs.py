"""Repairs for Panel Assistant: an administrator binds a panel to its user."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, Platform
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownStep
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .app_identity import SUCCESSOR_PACKAGE_ID
from .client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidAddressError,
    InvalidResponseError,
    is_valid_discovery_id,
    normalize_address,
)
from .config_flow import async_authorize_adb_form
from .const import CONF_TRANSPORT_USER_ID, DOMAIN, update_unique_id
from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
from .device import panel_display_name
from .failure_repair import (
    ISSUE_ADB_AUTHORIZATION,
    ISSUE_INSTALLER_FAILURE,
    RetrySafetyHold,
    adb_authorization_issue_id,
    async_clear_failure,
    async_failure_events,
    async_record_retry_hold,
    async_retry_install_job,
    async_support_report,
)
from .identity import (
    ISSUE_IDENTITY,
    can_confirm_identity,
    confirm_identity,
    is_installation,
)
from .install_executor import async_get_install_executor
from .install_jobs import InstallPhase, async_get_install_job_manager
from .migration_repair import (
    ISSUE_DATA_ADDRESS,
    ISSUE_DATA_VERSION,
    ISSUE_PANEL_MIGRATION_INCOMPLETE,
)
from .panel_move import ISSUE_DATA_ENTRY_ID as SUCCESSOR_MOVE_ENTRY_ID
from .panel_move import (
    ISSUE_MOVE_TO_NEW_APP,
    SuccessorMoveFlow,
    move_issue_id,
)
from .permission_repair import (
    ISSUE_PANEL_PERMISSIONS,
    permission_issue_id,
    permission_observations,
    permissions_held,
)
from .release import ReleaseResolutionError
from .release_catalog import async_resolve_install_choice
from .restart_repair import ISSUE_RESTART_REQUIRED, RestartRequiredFlow
from .transport import (
    BINDING_ISSUES,
    ISSUE_DATA_ENTRY_ID,
    ISSUE_DATA_USER_ID,
    async_bind_user,
    async_confirmable_user,
    async_delete_binding_issue,
)

ABORT_ENTRY_REMOVED = "entry_removed"
ABORT_USER_UNAVAILABLE = "user_unavailable"
ABORT_MIGRATION_UNFINISHED = "migration_unfinished"
_NOT_SHOWN = object()


SUPPORT_REPORT_GUIDE_URL = "https://panel-assistant.io/go/support-report"
REPORT_ISSUE_URL = "https://panel-assistant.io/go/report-issue"


class PanelIdentityFlow(RepairsFlow):
    """Confirm exactly the pending panel setups shown when this flow opened."""

    def __init__(self, values: dict[str, str | int | float | None]) -> None:
        self._values = dict(values)
        self._candidates: list[dict[str, Any]] | None = None
        self._registered: set[str] = set()
        self._panels = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        # Core passes issue metadata here; only the next step accepts consent.
        return await self.async_step_confirm_identity()

    def _unchanged(self, values: dict[str, Any]) -> bool:
        entry = self.hass.config_entries.async_get_entry(values["entry_id"])
        if (
            entry is None
            or is_installation(entry)
            or entry.unique_id != values["legacy_did"]
            or entry.data.get("address") != values["address"]
        ):
            return False
        try:
            normalize_address(values["address"])
        except InvalidAddressError:
            return False
        if values["entry_id"] in self._registered:
            issue = ir.async_get(self.hass).async_get_issue(
                DOMAIN, f"{ISSUE_IDENTITY}_{entry.entry_id}"
            )
            if issue is None or issue.data != values:
                return False
        return True

    async def async_step_confirm_identity(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if self._candidates is None:
            registry = ir.async_get(self.hass)
            snapshot_values: list[dict[str, Any]] = [self._values]
            for (domain, issue_id), issue in registry.issues.items():
                if (
                    domain == DOMAIN
                    and issue.translation_key == ISSUE_IDENTITY
                    and issue.is_fixable
                    and isinstance(issue.data, dict)
                    and isinstance(issue.data.get("entry_id"), str)
                    and issue_id == f"{ISSUE_IDENTITY}_{issue.data.get('entry_id')}"
                ):
                    entry_id = issue.data["entry_id"]
                    assert isinstance(entry_id, str)
                    self._registered.add(entry_id)
                    if issue.data.get("entry_id") != self._values.get("entry_id"):
                        snapshot_values.append(dict(issue.data))
            self._candidates = []
            names = []
            for candidate in snapshot_values:
                if (
                    not all(
                        isinstance(candidate.get(key), str)
                        for key in ("entry_id", "did", "legacy_did", "address")
                    )
                    or not is_valid_discovery_id(candidate["did"])
                    or not is_valid_discovery_id(candidate["legacy_did"])
                    or candidate["did"] == candidate["legacy_did"]
                    or not self._unchanged(candidate)
                ):
                    if candidate is self._values:
                        return self.async_abort(reason="identity_changed")
                    continue
                entry = self.hass.config_entries.async_get_entry(candidate["entry_id"])
                assert entry is not None
                self._candidates.append(candidate)
                names.append(f"- {entry.title} ({candidate['address']})")
            self._panels = "\n".join(names)
        if not self._candidates or not all(
            self._unchanged(values) for values in self._candidates
        ):
            return self.async_abort(reason="identity_changed")
        errors = {}
        if user_input is not None:
            healths = []
            try:
                for values in self._candidates:
                    healths.append(
                        await HaPaneldClient(
                            async_get_clientsession(self.hass),
                            normalize_address(values["address"]),
                        ).async_get_health()
                    )
            except CannotConnectError, InvalidResponseError, InvalidAddressError:
                errors["base"] = "cannot_connect"
            else:
                # All reads finish before checking the entire frozen batch. No
                # await divides these checks from the registry mutations below.
                targets = [values["did"] for values in self._candidates]
                if len(set(targets)) != len(targets):
                    return self.async_abort(reason="identity_changed")
                entries = []
                for values, health in zip(self._candidates, healths, strict=True):
                    if not self._unchanged(values):
                        return self.async_abort(reason="identity_changed")
                    entry = self.hass.config_entries.async_get_entry(values["entry_id"])
                    assert entry is not None
                    if (
                        health.discovery_id != values["did"]
                        or health.legacy_discovery_id != values["legacy_did"]
                        or not can_confirm_identity(self.hass, entry, health)
                    ):
                        return self.async_abort(reason="identity_changed")
                    entries.append(entry)
                for entry, health in zip(entries, healths, strict=True):
                    if not confirm_identity(self.hass, entry, health):
                        return self.async_abort(reason="identity_changed")
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm_identity",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"panels": self._panels},
        )


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
        user = await async_confirmable_user(self.hass, self._user_id)
        if user is None:
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


class InstallerFailureFlow(RepairsFlow):
    """Keep a failed panel's retry, report and clear action together."""

    def __init__(self) -> None:
        self._retry_task: asyncio.Task[str | None] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        options = ["retry", "support_report", "clear_error"]
        entry = self._adb_entry()
        if entry is not None:
            options.insert(1, "authorize_adb")
        return self.async_show_menu(
            step_id="init",
            menu_options=options,
            description_placeholders=(
                {"panel": panel_display_name(self.hass, entry)} if entry else None
            ),
        )

    def _adb_entry(self) -> ConfigEntry | None:
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        data = issue.data if issue is not None else None
        entry_id = data.get("entry_id") if isinstance(data, dict) else None
        entry = (
            self.hass.config_entries.async_get_entry(entry_id)
            if isinstance(entry_id, str)
            else None
        )
        return entry if entry is not None and entry.domain == DOMAIN else None

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Restore ADB trust, then leave the failed update report available."""
        entry = self._adb_entry()
        if entry is None:
            return self.async_abort(reason=ABORT_ENTRY_REMOVED)
        panel = panel_display_name(self.hass, entry)
        form = await async_authorize_adb_form(self, entry, panel, user_input)
        if form is not None:
            return form
        return await self.async_step_init()

    async def async_step_support_report(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="support_report",
            data_schema=vol.Schema({}),
            description_placeholders={
                "report": await async_support_report(self.hass, self.issue_id),
                "guide_url": SUPPORT_REPORT_GUIDE_URL,
                "issue_url": REPORT_ISSUE_URL,
            },
        )

    async def async_step_clear_error(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="clear_error", data_schema=vol.Schema({})
            )
        await async_clear_failure(self.hass, self.issue_id)
        return self.async_create_entry(data={})

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Retry in this repair, keeping the underlying installer process-owned."""
        if self._retry_task is None:
            self._retry_task = self.hass.async_create_task(
                self._async_retry(), f"retry panel failure {self.issue_id}"
            )
        return await self.async_step_retry_progress()

    async def async_step_retry_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        task = self._retry_task
        if task is None:
            return await self.async_step_init()
        if not task.done():
            return self.async_show_progress(
                step_id="retry_progress",
                progress_action="retrying",
                progress_task=task,
            )
        return self.async_show_progress_done(next_step_id="retry_result")

    async def async_step_retry_result(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        task = self._retry_task
        if task is None:
            return await self.async_step_init()
        try:
            reason = task.result()
        except Exception:
            reason = "retry_failed"
        self._retry_task = None
        if reason == "_update_completed":
            if (
                ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
                is not None
            ):
                return await self.async_step_init()
            return self.async_create_entry(data={})
        if reason is None:
            await async_clear_failure(self.hass, self.issue_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="retry_error",
            data_schema=vol.Schema({}),
            errors={"base": reason},
        )

    async def async_step_retry_error(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_init()

    async def _async_retry(self) -> str | None:
        events = await async_failure_events(self.hass, self.issue_id)
        previous = next(
            (
                event
                for event in reversed(events)
                if event.get("kind") in {"install", "update"}
            ),
            None,
        )
        if previous is None:
            return "retry_cannot_prove_safe"
        try:
            if previous["kind"] == "install":
                manager = await async_get_install_job_manager(self.hass)
                receipt = await manager.async_get(previous["job_id"])
                fresh = await async_retry_install_job(self.hass, receipt)
                executor = await async_get_install_executor(self.hass)
                finished = await executor.async_wait(fresh.job_id)
                if finished.phase is not InstallPhase.HEALTHY_UNCLAIMED:
                    return "retry_failed"
                result = await self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "repair_finalize"},
                    data={"job_id": fresh.job_id},
                )
                if result["type"] is FlowResultType.CREATE_ENTRY:
                    return None
                return "retry_finalization"
            entry_id = previous["entry_id"]
            entity_id = er.async_get(self.hass).async_get_entity_id(
                Platform.UPDATE, DOMAIN, update_unique_id(entry_id)
            )
            if entity_id is None:
                raise RetrySafetyHold("retry_update_unavailable")
            state = self.hass.states.get(entity_id)
            offered = state.attributes.get("latest_version") if state else None
            installed = (
                state.attributes.get("installed_version", state.state)
                if state
                else None
            )
            if (
                state is not None
                and state.state == STATE_OFF
                and isinstance(installed, str)
                and offered == installed
            ):
                # The panel already runs the newest build on offer: the update
                # that failed to report back has since landed, or nothing newer
                # exists. There is nothing to install, so the failure is over;
                # asking Home Assistant to install would only be refused.
                return None
            current_retry = (
                isinstance(offered, str)
                and offered != installed
                and offered != previous.get("target_version")
            )
            saved_artifact = previous.get("artifact")
            if isinstance(saved_artifact, dict) and not current_retry:
                tag = saved_artifact.get("tag")
                if not isinstance(tag, str):
                    raise RetrySafetyHold("retry_release_changed")
                try:
                    current = await async_resolve_install_choice(self.hass, tag)
                except ReleaseResolutionError as err:
                    raise RetrySafetyHold("retry_release_changed") from err
                if asdict(current) != saved_artifact:
                    raise RetrySafetyHold("retry_release_changed")
            service_data = {"entity_id": entity_id}
            if not current_retry and isinstance(previous.get("target_version"), str):
                service_data["version"] = previous["target_version"]
            await self.hass.services.async_call(
                Platform.UPDATE,
                "install",
                service_data,
                blocking=True,
            )
            return "_update_completed"
        except RetrySafetyHold as err:
            await async_record_retry_hold(self.hass, self.issue_id, previous, err)
            return err.reason
        except Exception as err:
            await async_record_retry_hold(self.hass, self.issue_id, previous, err)
            return "retry_failed"


class PanelPermissionFlow(RepairsFlow):
    """Explain manual commissioning and recheck what the named panel now holds."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_commission_permissions()

    async def async_step_commission_permissions(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason=ABORT_ENTRY_REMOVED)
        errors = {}
        if user_input is not None:
            coordinator = getattr(
                getattr(entry, "runtime_data", None), "coordinator", None
            )
            if isinstance(coordinator, HaPaneldDataUpdateCoordinator):
                previous = coordinator.data
                # async_refresh waits for a fresh read; async_request_refresh
                # may merely schedule a debounced poll and retain old data.
                await coordinator.async_refresh()
                if (
                    self.hass.config_entries.async_get_entry(self._entry_id)
                    is not entry
                ):
                    return self.async_abort(reason=ABORT_ENTRY_REMOVED)
                snapshot: PanelSnapshot | None = coordinator.data
                if (
                    not coordinator.last_update_success
                    or snapshot is previous
                    or getattr(
                        getattr(entry, "runtime_data", None), "coordinator", None
                    )
                    is not coordinator
                ):
                    snapshot = None
                status = snapshot.status if snapshot is not None else None
                permissions = (
                    permission_observations(self.hass, entry, status)
                    if status is not None
                    else None
                )
                if permissions_held(permissions):
                    return self.async_create_entry(data={})
                if permissions is not None and "missing" in permissions.values():
                    errors["base"] = "permissions_missing"
            if not errors:
                errors["base"] = "permissions_unreadable"
        return self.async_show_form(
            step_id="commission_permissions",
            data_schema=vol.Schema({}),
            description_placeholders={"panel": panel_display_name(self.hass, entry)},
            errors=errors,
        )


class AdbAuthorizationFlow(RepairsFlow):
    """Offer the configured panel the same authorization as its Options flow."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_authorize_adb()

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason=ABORT_ENTRY_REMOVED)
        panel = panel_display_name(self.hass, entry)
        form = await async_authorize_adb_form(self, entry, panel, user_input)
        if form is not None:
            return form
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Panel Assistant issue."""
    values = data or {}
    if issue_id == ISSUE_RESTART_REQUIRED:
        return RestartRequiredFlow()
    if issue_id.startswith(f"{ISSUE_PANEL_PERMISSIONS}_"):
        entry_id = values.get("entry_id")
        if not isinstance(entry_id, str) or issue_id != permission_issue_id(entry_id):
            raise UnknownStep
        return PanelPermissionFlow(entry_id)
    if issue_id.startswith(f"{ISSUE_ADB_AUTHORIZATION}_"):
        entry_id = values.get("entry_id")
        if not isinstance(entry_id, str) or issue_id != adb_authorization_issue_id(
            entry_id
        ):
            raise UnknownStep
        return AdbAuthorizationFlow(entry_id)
    if issue_id.startswith(f"{ISSUE_IDENTITY}_"):
        return PanelIdentityFlow(values)
    if issue_id.startswith(f"{ISSUE_INSTALLER_FAILURE}_"):
        return InstallerFailureFlow()
    if issue_id.startswith(f"{ISSUE_MOVE_TO_NEW_APP}_"):
        entry_id = values.get(SUCCESSOR_MOVE_ENTRY_ID)
        if not isinstance(entry_id, str) or issue_id != move_issue_id(entry_id):
            raise UnknownStep
        return SuccessorMoveFlow(entry_id)
    if issue_id.startswith(f"{ISSUE_PANEL_MIGRATION_INCOMPLETE}_"):
        address = values.get(ISSUE_DATA_ADDRESS)
        version = values.get(ISSUE_DATA_VERSION)
        if not isinstance(address, str) or not isinstance(version, str):
            raise UnknownStep
        return PanelMigrationFlow(address, version)
    entry_id = values.get(ISSUE_DATA_ENTRY_ID)
    user_id = values.get(ISSUE_DATA_USER_ID)
    # Both binding issues ask one question and are answered by one flow, which
    # reads the entry itself to decide which confirmation to show.
    if (
        not any(issue_id.startswith(f"{asked}_") for asked in BINDING_ISSUES)
        or not isinstance(entry_id, str)
        or not isinstance(user_id, str)
    ):
        raise UnknownStep
    return PanelUserBindingFlow(entry_id, user_id)

"""Config flow for ha-paneld."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Self

import voluptuous as vol
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from aiohttp import ClientPayloadError, ClientResponseError, ServerDisconnectedError
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    FlowType,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import (
    AbortFlow,
    FlowHandler,
    FlowResult,
    FlowResultType,
    UnknownFlow,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import current_request
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import local_adb
from .adb_credentials import (
    AdbCredentialError,
    async_get_adb_credential,
    async_get_adb_signer,
    async_get_durable_adb_credential,
)
from .adb_protocol import InstallAdbError, same_physical_target
from .announced_addresses import hand_over_addresses
from .browser_delivery import async_register_browser_delivery
from .browser_panel import PANEL_PATH, async_register_browser_panel
from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidAddressError,
    InvalidResponseError,
    NoAnswerError,
    PanelAddress,
    PanelHealth,
    RequestRefusedError,
    is_valid_discovery_id,
    normalize_address,
)
from .const import (
    CONF_PRERELEASE_PANEL_BUILDS,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    INSTALL_RECOVERY_PLACEHOLDERS,
    SOURCE_USB_INSTALL,
    help_url,
)
from .device import panel_display_name
from .discovery import (
    CONFIRMED,
    MigrationTarget,
    _apply_discovery_title,
    _entry_holds_its_address,
    address_schema,
    async_confirm_discovery,
    async_confirm_migration,
    async_find_migration_targets,
    async_ignore,
    async_integration_discovery,
    async_offer_migration_targets,
    async_zeroconf,
    close_offers,
    closes_when_done,
    configured_entries_for_host,
    markdown_literal,
)
from .foreign_ha import async_connected_to_other_ha, async_raise_foreign_ha_issue
from .ha_url import async_offer_ha_url
from .identity import CONF_INSTALL_IDENTITY, accept_health, is_installation, reinstalled
from .install_adb import async_installed_artifact_size, async_preflight_install
from .install_executor import (
    FinalizationOutcome,
    InstallExecutor,
    async_get_install_executor,
)
from .install_jobs import async_get_install_job_manager
from .install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
    PinnedPanelTarget,
    async_pin_entry_target,
    async_pin_install_target,
    async_revalidate_install_target,
)
from .install_plan import InstallPlanError, build_install_plan
from .install_receipt import (
    HEALTH_UNREACHABLE_SUBCODE,
    InstallJobConflictError,
    InstallJobError,
    InstallJobReceipt,
    InstallPhase,
    InstallResultCode,
)
from .owner_report import owner_report_issue_url
from .provisioning import (
    InstallTargetProbe,
    InstallTargetState,
    async_probe_install_target,
)
from .refused_hellos import async_refused_hello_at
from .release import (
    ReleaseArtifact,
    ReleaseResolutionError,
)
from .release_catalog import async_list_install_choices, async_resolve_install_choice
from .removal import (
    REASON_ADB_AUTHORIZATION,
    RemovalError,
    async_read_firmware,
    async_remove_app,
)
from .repairs_failure import async_clear_adb_authorization
from .repairs_removal import async_request_removal
from .repairs_update_route import help_parameters
from .repairs_upgrade import ISSUE_OLD_APP_NEEDS_090, runs_old_app
from .status import PanelStatus
from .transport import _panel_did
from .transport_binding import (
    async_binding_request,
    async_confirmable_user,
    async_discard_binding_request,
)
from .update_policy import prereleases_allowed

# The old_app_installed text links to the same steps as its Repair.
_OLD_APP_PLACEHOLDERS = {
    "old_app_help_url": help_url("repair", issue=ISSUE_OLD_APP_NEEDS_090)
}
_LOGGER = logging.getLogger(__name__)

_CANCELLED_ABORT_REASONS = {
    InstallResultCode.CANCELLED_BY_USER: "install_cancelled",
    InstallResultCode.CANCELLED_AFTER_STAGING_CLEANUP: (
        "install_cancelled_after_staging_cleanup"
    ),
}
_FAILED_ABORT_REASONS = {
    InstallResultCode.AUTHORIZATION_FAILED: "install_authorization_failed",
    InstallResultCode.PREFLIGHT_REJECTED: "install_preflight_rejected",
    InstallResultCode.ARTIFACT_REJECTED: "install_artifact_rejected",
    InstallResultCode.TRANSPORT_FAILED: "install_transport_failed",
    InstallResultCode.INSTALL_FAILED: "install_package_failed",
    InstallResultCode.LAUNCH_FAILED: "install_launch_failed",
    InstallResultCode.HEALTH_CHECK_FAILED: "install_health_check_failed",
}
_RECOVERY_ABORT_REASONS = {
    InstallResultCode.AMBIGUOUS_MUTATION: "install_ambiguous_mutation",
    InstallResultCode.VERIFICATION_REQUIRED: "install_verification_required",
}

# Endings that are not refusals: the panel is already there, another card has
# it, the work is done, or an announcement was not a panel to begin with.
# Announcements arrive all day, so these are logged for debugging only.
_QUIET_ABORTS = frozenset(
    {
        "already_configured",
        "already_in_progress",
        "invalid_discovery",
        "not_panel",
        "reinstall_successful",
        "usb_installed",
    }
)

_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_ADDRESS): TextSelector(TextSelectorConfig())}
)
_CONF_RELEASE_CANDIDATE = "release_candidate"
# The recommended stable release's option value. The frontend treats an empty
# value as unfilled, so a required selector offering "" can never be submitted.
_STABLE_CHOICE = "stable"
_SETUP_POLL_SECONDS = 3
# Long enough to walk to the panel and answer Android's Allow prompt.
_ADB_APPROVAL_SECONDS = 120.0
_ADB_ALLOW_POLL_SECONDS = 3.0
_HOST_PROBE_SECONDS = 3
_SETUP_WATCH_SECONDS = 60 * 60


class HaPaneldConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an ha-paneld config flow."""

    VERSION = 1
    # 2: the authority option is gone (see ``async_migrate_entry``).
    # 3: the device setup record (``CONF_DEVICE_SETUP``) is kept.
    MINOR_VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the flow that chooses who owns a panel's commands."""
        return HaPaneldOptionsFlow()

    _pending_reinstall_entry: ConfigEntry | None = None
    _pending_address: PanelAddress | None = None
    _pending_health: PanelHealth | None = None
    _pending_discovery_id: str | None = None
    _pending_friendly_name: str | None = None
    # An announced panel's addresses, best first, each with the addresses its
    # announcement carried, and whether its health is still being read at
    # them (a later announcement of the same panel then adds to them).
    _discovery_addresses: dict[PanelAddress, frozenset[str]] | None = None
    _discovery_probing: bool = False
    _pending_probe: InstallTargetProbe | None = None
    _pending_probe_state: str | None = None
    _pending_release: ReleaseArtifact | None = None
    _pending_rc_tag: str | None = None
    _pending_install_target: PinnedPanelTarget | None = None
    _pending_job_id: str | None = None
    # The unfinished install that owns the panel being added, and what was
    # typed, so the person can carry on with it or cancel it and start again.
    _unfinished_job_id: str | None = None
    _unfinished_input: dict[str, Any] | None = None
    # What the next refusal shown should give as its cause in the log.
    _refusal_cause: object = None
    # The panel's refusal of the last setup read, shown on the setup form.
    _setup_refusal: RequestRefusedError | None = None
    # The address as typed, for the log when nothing was pinned from it.
    _typed_address: str | None = None
    _progress_waiter: asyncio.Task[InstallJobReceipt] | None = None
    _progress_action = "installing"
    _authorization_task: asyncio.Task[InstallTargetProbe] | None = None
    _authorization_errors: dict[str, str] | None = None
    _install_executor: InstallExecutor | None = None
    _finalizer_job_id: str | None = None
    _flow_removed = False
    _steps_running = 0
    _install_releases: list[dict[str, Any]] | None = None
    _setup_watch: asyncio.Task[None] | None = None
    _release_catalog_error: str | None = None
    _migration_targets: list[MigrationTarget] | None = None
    _migration_target: MigrationTarget | None = None
    _usb_installer_url = ""

    def is_matching(self, other_flow: Self) -> bool:
        """Enrich the pending card when the app follows its vendor announcement."""
        if (
            self._pending_discovery_id is None
            or self._pending_discovery_id != other_flow.unique_id
        ):
            return False
        if other_flow._discovery_probing:
            # The same panel, still being reached: this announcement's
            # addresses, perhaps its other family, are tried there too.
            hand_over_addresses(other_flow, self)
            return True
        if other_flow._pending_health is None:
            return False
        if (
            self._pending_friendly_name is not None
            and CONFIRMED not in other_flow.context.get("title_placeholders", {})
        ):
            other_flow._pending_friendly_name = self._pending_friendly_name
            _apply_discovery_title(other_flow)
        return True

    def _log_refusal(self, step: str | None, error: str) -> None:
        """Say in the log what this flow refused, for which panel, and why.

        The dialog shows only a translated sentence. Someone helping from a log
        needs the refusal itself, and the cause when one is known beyond it.
        """
        cause, self._refusal_cause = self._refusal_cause, None
        address = (
            self._pending_address.stored_value
            if self._pending_address is not None
            else self._typed_address
            or self.context.get("title_placeholders", {}).get(
                "address",
                next(iter(self._discovery_addresses)).stored_value
                if self._discovery_addresses
                else "unknown",
            )
        )
        if isinstance(cause, InstallNetworkError):
            detail = cause.code.value
        elif isinstance(cause, RequestRefusedError):
            detail = f"{type(cause).__name__}: {cause}"
        elif isinstance(cause, BaseException):
            detail = type(cause).__name__
            if cause.__cause__ is not None:
                detail = f"{detail} from {cause.__cause__!r}"
        else:
            detail = str(cause) if cause is not None else error
        _LOGGER.log(
            logging.DEBUG if error in _QUIET_ABORTS else logging.WARNING,
            "Panel Assistant setup refused: step=%s error=%s panel=%s cause=%s",
            step or "unknown",
            error,
            address,
            detail,
            # A store refusal has many causes; its traceback names the one.
            exc_info=cause if isinstance(cause, InstallJobError) else None,
        )

    def async_show_form(
        self,
        *,
        step_id: str | None = None,
        errors: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> ConfigFlowResult:
        """Show a form; one that refuses also says so in the log."""
        if errors:
            self._log_refusal(step_id, ",".join(sorted(set(errors.values()))))
        return super().async_show_form(step_id=step_id, errors=errors, **kwargs)

    def async_abort(self, *, reason: str, **kwargs: Any) -> ConfigFlowResult:
        """End the flow; an ending that refuses also says so in the log."""
        self._log_refusal(self.cur_step["step_id"] if self.cur_step else None, reason)
        return super().async_abort(reason=reason, **kwargs)

    async def _async_unblocked[ResultT](
        self, attempt: Callable[[], Awaitable[ResultT]]
    ) -> ResultT:
        """Ask the receipt store; an install nobody is advancing never refuses.

        When an unfinished install of the same panel stands in the way, the
        ones whose time is up, or whose worker cannot run again, are ended and
        the question is asked once more. One still being advanced is raised to
        the caller, which offers to carry on with it or cancel it.
        """
        try:
            return await attempt()
        except InstallJobConflictError:
            executor = await async_get_install_executor(self.hass)
            if not await executor.async_end_stalled_jobs(stopped_now=True):
                raise
            return await attempt()

    async def _async_show_unfinished_install(
        self,
        conflict: InstallJobConflictError | InstallJobReceipt,
        typed: dict[str, Any],
    ) -> ConfigFlowResult:
        """Say that an unfinished install owns this panel, and offer the ways on.

        The same panel reached by another name (its host name, its other
        address family, a second discovery card) meets the receipt of the
        install already started. That is not a storage fault: the person can
        carry on with that install, or cancel it and start again here.
        """
        receipt = (
            conflict.receipt
            if isinstance(conflict, InstallJobConflictError)
            else conflict
        )
        if receipt is None:
            self._refusal_cause = "unfinished install unknown"
            return self._show_add_panel_form(typed, {"base": "install_receipt_error"})
        self._unfinished_job_id = receipt.job_id
        self._unfinished_input = typed
        try:
            executor = await async_get_install_executor(self.hass)
            can_cancel = executor.can_cancel(receipt)
        except InstallJobError as err:
            self._refusal_cause = err
            return self._show_add_panel_form(typed, {"base": "install_receipt_error"})
        _LOGGER.warning(
            "Panel Assistant setup found an unfinished install of this panel: "
            "requested=%s existing=%s pinned=%s stage=%s cancellable=%s job=%s",
            typed.get(CONF_ADDRESS, "unknown"),
            receipt.target.address,
            receipt.target.pinned_address,
            receipt.phase.value,
            can_cancel,
            receipt.job_id,
        )
        return self.async_show_menu(
            step_id="install_unfinished" if can_cancel else "install_unfinished_busy",
            menu_options=(
                ["resume_install", "cancel_install"]
                if can_cancel
                else ["resume_install"]
            ),
            description_placeholders={
                "address": typed.get(CONF_ADDRESS, ""),
                "existing_address": receipt.target.address,
                "version": receipt.artifact.version_name,
            },
        )

    async def _async_unclaimed_install_of(
        self, health: PanelHealth
    ) -> InstallJobReceipt | None:
        """A healthy install of this panel waiting under another of its addresses.

        A receipt keeps the address its install used, so the panel reached by
        another (its other address family, a card) misses it in the address
        lookup. Asked at the receipt's own address, the app there names itself;
        the same name is the same panel, which then meets its install rather
        than being added beside it.
        """
        if health.discovery_id is None or self.source == SOURCE_USB_INSTALL:
            # The USB installer's flow runs with nobody at a dialog; it adds its
            # panel, and finishing the install later finds that entry instead.
            return None
        try:
            receipts = await (
                await async_get_install_job_manager(self.hass)
            ).async_list()
        except InstallJobError:
            return None
        for receipt in receipts:
            if receipt.phase is not InstallPhase.HEALTHY_UNCLAIMED:
                continue
            try:
                there = await HaPaneldClient(
                    async_get_clientsession(self.hass),
                    normalize_address(receipt.target.pinned_address),
                ).async_get_health()
            except HaPaneldError, InvalidAddressError:
                continue
            if there.discovery_id == health.discovery_id:
                return receipt
        return None

    async def _async_unfinished_install(self) -> InstallJobReceipt | None:
        """The unfinished install this flow met, as it stands now."""
        if self._unfinished_job_id is None:
            return None
        manager = await async_get_install_job_manager(self.hass)
        return await manager.async_get(self._unfinished_job_id)

    async def async_step_install_unfinished(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the unfinished install again, as it stands now."""
        try:
            receipt = await self._async_unfinished_install()
        except InstallJobError as err:
            self._refusal_cause = err
            return self.async_abort(reason="install_receipt_error")
        if receipt is None or self._unfinished_input is None:
            return self.async_abort(reason="unknown")
        if receipt.is_terminal:
            # It ended while the menu was open; nothing owns the panel now.
            return await self.async_step_add_panel(self._unfinished_input)
        return await self._async_show_unfinished_install(
            receipt, self._unfinished_input
        )

    async def async_step_install_unfinished_busy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The same menu while the install cannot be cancelled."""
        return await self.async_step_install_unfinished(user_input)

    async def async_step_resume_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Carry on with the install already started, under its own address."""
        try:
            receipt = await self._async_unfinished_install()
        except InstallJobError as err:
            self._refusal_cause = err
            return self.async_abort(reason="install_receipt_error")
        if receipt is None or self._unfinished_input is None:
            return self.async_abort(reason="unknown")
        if receipt.is_terminal:
            return await self.async_step_add_panel(self._unfinished_input)
        self._reset_pending_panel()
        self._pending_address = normalize_address(receipt.target.address)
        self._pending_install_target = PinnedPanelTarget(
            original=self._pending_address,
            pinned=normalize_address(receipt.target.pinned_address),
        )
        self._pending_job_id = receipt.job_id
        if receipt.phase is InstallPhase.HEALTHY_UNCLAIMED:
            return await self.async_step_install_result()
        return await self._async_show_install_progress(receipt)

    async def async_step_cancel_install(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """End the install already started, then look at the panel afresh."""
        if self._unfinished_job_id is None or self._unfinished_input is None:
            return self.async_abort(reason="unknown")
        try:
            executor = await async_get_install_executor(self.hass)
            await executor.async_cancel_job(self._unfinished_job_id)
        except InstallJobError as err:
            self._refusal_cause = err
            return self.async_abort(reason="install_receipt_error")
        # Ended: the panel is looked at afresh. Still changing the panel, or
        # not stopped yet: the menu is shown again as it stands now.
        return await self.async_step_install_unfinished()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user add a panel by its address or install one over USB."""
        # HA loads dependencies before the first flow, but domain async_setup
        # need not run until an entry exists. USB delivery must be ready now.
        async_register_browser_delivery(self.hass)
        await async_register_browser_panel(self.hass)
        # Someone adding a panel by hand is the moment to show every known one.
        async_offer_migration_targets(self.hass)
        return self.async_show_menu(
            step_id="user",
            menu_options=["add_panel", "install_usb"],
        )

    async def async_step_repair_finalize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let a Repairs retry use the ordinary verified entry-creation path."""
        job_id = user_input.get("job_id") if user_input else None
        if not isinstance(job_id, str):
            return self.async_abort(reason="install_receipt_error")
        self._pending_job_id = job_id
        return await self.async_step_install_result()

    async def async_step_install_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open browser-owned USB installation without creating an entry."""
        if user_input is None:
            return self.async_show_form(
                step_id="install_usb",
                data_schema=vol.Schema({}),
                description_placeholders={"panel_access_url": help_url("panel-access")},
            )
        # The dialog opens the installer window itself, and only for an absolute
        # URL, so the address is the one this browser used to reach Home Assistant.
        request = current_request.get()
        origin = str(request.url.origin()) if request is not None else ""
        self._usb_installer_url = f"{origin}/{PANEL_PATH}"
        return await self.async_step_usb_installer()

    async def async_step_usb_installer(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait on the installer window until the USB install adds its panel."""
        if user_input is not None:
            return self.async_external_step_done(next_step_id="usb_installed")
        return self.async_external_step(
            step_id="usb_installer", url=self._usb_installer_url
        )

    async def async_step_usb_installed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="usb_installed")

    async def async_step_usb_install(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Add the panel the browser USB installer has just installed.

        The administrator pressed Install, the same consent a network install
        asks for, and that path adds its panel when the app answers; so does
        this one. A panel already added is left as it is, its address updated
        when it has moved, exactly as when someone adds it by hand.
        """
        try:
            address = normalize_address(discovery_info[CONF_ADDRESS])
            health = await HaPaneldClient(
                async_get_clientsession(self.hass), address
            ).async_get_health()
        except InvalidAddressError, CannotConnectError, InvalidResponseError:
            return self.async_abort(reason="not_panel")
        try:
            result = await self._async_create_panel_entry(address, health)
        except AbortFlow as abort:
            if abort.reason == "already_configured":
                await self._async_finish_usb_installers()
            raise
        if result["type"] is FlowResultType.CREATE_ENTRY or (
            result.get("reason") == "already_configured"
        ):
            await self._async_finish_usb_installers()
        return result

    async def _async_finish_usb_installers(self) -> None:
        """End every Add dialog still waiting on the USB installer window."""
        for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            if flow["step_id"] == "usb_installer":
                with contextlib.suppress(UnknownFlow):
                    await self.hass.config_entries.flow.async_configure(
                        flow["flow_id"], {}
                    )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        return await async_zeroconf(self, discovery_info)

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        return await async_integration_discovery(self, discovery_info)

    async def async_step_ignore(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        return await async_ignore(self, user_input)

    async def async_step_confirm_migration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await async_confirm_migration(self, user_input)

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await async_confirm_discovery(self, user_input)

    def _show_add_panel_form(
        self,
        user_input: dict[str, Any] | None,
        errors: dict[str, str],
        *,
        cause: object = None,
    ) -> ConfigFlowResult:
        """Ask only for the panel's address; what happens next depends on the panel.

        Panels Home Assistant knows through other apps are offered in a picker,
        which has its own step so that its text describes it only when shown.
        """
        if cause is not None:
            self._refusal_cause = cause
        picker = address_schema(self._migration_targets)
        return self.async_show_form(
            step_id="add_panel" if picker is None else "add_known_panel",
            data_schema=self.add_suggested_values_to_schema(
                picker or _DATA_SCHEMA, user_input
            ),
            errors=errors,
            description_placeholders={
                "panel_access_url": help_url("panel-access"),
                "panel_help_url": help_url("panel-unreachable"),
                **INSTALL_RECOVERY_PLACEHOLDERS,
                **_OLD_APP_PLACEHOLDERS,
            },
        )

    def _version_options(self) -> list[SelectOptionDict]:
        """The newest stable release first, then published test releases."""
        releases = self._install_releases or []
        return [
            SelectOptionDict(
                value=(
                    _STABLE_CHOICE
                    if index == 0
                    and (not release["prerelease"] or prereleases_allowed())
                    else str(release["tag"])
                ),
                label=str(release.get("name", release["tag"])),
            )
            for index, release in enumerate(releases)
        ]

    def _show_choose_version(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Offer the releases to choose from."""
        options = self._version_options()
        # With nothing to choose, an empty form still submits, which reloads.
        schema = (
            vol.Schema(
                {
                    vol.Required(
                        _CONF_RELEASE_CANDIDATE, default=options[0]["value"]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            )
            if options
            else vol.Schema({})
        )
        if self._release_catalog_error and not errors:
            errors = {"base": self._release_catalog_error}
        return self.async_show_form(
            step_id="choose_version",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "address": self._pending_address.stored_value
                if self._pending_address is not None
                else ""
            },
        )

    def _found_client(self) -> HaPaneldClient:
        """Talk to the found panel at its pinned address when there is one."""
        assert self._pending_address is not None
        address = (
            self._pending_install_target.pinned
            if self._pending_install_target is not None
            else self._pending_address
        )
        return HaPaneldClient(async_get_clientsession(self.hass), address)

    async def _async_show_found_panel(self) -> ConfigFlowResult:
        """Say the app is installed but not connected, and whether its setup is done.

        A new panel must finish its own setup wizard before it gets an entry.
        Existing entries reconnect through their accepted identity above.
        """
        assert self._pending_address is not None
        assert self._pending_health is not None
        known = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, self._pending_health.discovery_id
            )
            if self._pending_health.discovery_id is not None
            else None
        )
        if self._pending_job_id is None and (
            (known is not None and known.source != SOURCE_IGNORE)
            or any(
                reinstalled(self.hass, entry, self._pending_health)
                and not _entry_holds_its_address(self, entry)
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            )
        ):
            return await self._async_create_panel_entry(
                self._pending_address, self._pending_health
            )
        if unclaimed := await self._async_unclaimed_install_of(self._pending_health):
            return await self._async_show_unfinished_install(
                unclaimed, {CONF_ADDRESS: self._pending_address.stored_value}
            )
        if runs_old_app(self._pending_health):
            return self.async_abort(
                reason="old_app_installed",
                description_placeholders=_OLD_APP_PLACEHOLDERS,
            )
        try:
            state = await self._found_client().async_get_setup_state()
            needs_user = not state.complete
        except HaPaneldError:
            needs_user = True
        return self.async_show_menu(
            step_id="found_unconfigured" if needs_user else "found_panel",
            menu_options=(
                ["connect_found", "add_panel"]
                if not needs_user
                else ["panel_setup", "add_panel"]
            ),
            description_placeholders={
                "address": self._pending_address.stored_value,
                "version": self._pending_health.version,
            },
        )

    async def async_step_found_unconfigured(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the found panel again, re-reading its setup state."""
        return await self.async_step_found_panel()

    async def async_step_panel_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the panel's setup wizard, then connect it once setup is done."""
        if self._pending_address is None:
            return self.async_abort(reason="unknown")
        if self.source != SOURCE_USB_INSTALL:
            return await self._async_panel_setup_form(user_input)
        if user_input is not None:
            return self.async_external_step_done(
                next_step_id=(
                    "install_result"
                    if self._pending_job_id is not None
                    and self._pending_reinstall_entry is None
                    else "connect_found"
                )
            )
        # USB's handover endpoint has already offered Home Assistant's address
        # before starting this background flow; it is never sent a second time.
        if self._setup_watch is None or self._setup_watch.done():
            self._setup_watch = self.hass.async_create_background_task(
                self._async_watch_setup(),
                f"{DOMAIN} watch panel setup {self.flow_id}",
            )
        return self.async_external_step(
            step_id="panel_setup", url=self._found_client().setup_url
        )

    async def _async_panel_setup_form(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Link the panel's setup page in a titled form; Submit checks it is done.

        Home Assistant's dialog shows an external step with no title or text,
        and a form never moves on by itself, so the owner says when they are
        done, as in the onboarding step.
        """
        client = self._found_client()
        errors: dict[str, str] = {}
        placeholders = {"setup_url": client.setup_url}
        refusal, self._setup_refusal = self._setup_refusal, None
        if user_input is None:
            await async_offer_ha_url(self.hass, client)
        else:
            try:
                complete = await client.async_get_setup_complete()
            except RequestRefusedError as err:
                refusal, complete = err, False
            except HaPaneldError:
                complete = False
            if complete:
                if (
                    self._pending_job_id is not None
                    and self._pending_reinstall_entry is None
                ):
                    return await self.async_step_install_result()
                return await self.async_step_connect_found()
            errors["base"] = "panel_setup_not_finished"
        if refusal is not None:
            # A refusal is not "not finished yet": Submit can never get past it
            # unless the person and the log are told what the panel answered.
            self._refusal_cause = refusal
            errors["base"] = "panel_setup_refused"
            placeholders["refusal"] = str(refusal)
        return self.async_show_form(
            step_id="panel_setup",
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def _async_watch_setup(self) -> None:
        """Move the flow on by itself as soon as the panel reports setup done."""
        client = self._found_client()
        deadline = asyncio.get_running_loop().time() + _SETUP_WATCH_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_SETUP_POLL_SECONDS)
            try:
                complete = await client.async_get_setup_complete()
            except HaPaneldError:
                continue
            if complete:
                # USB may finish its background flow inside this callback.
                # Removing that completed flow must not cancel its own watcher.
                self._setup_watch = None
                with contextlib.suppress(UnknownFlow):
                    result = await self.hass.config_entries.flow.async_configure(
                        flow_id=self.flow_id, user_input={}
                    )
                    # USB registration has no foreground config-flow dialog
                    # to submit the continuation after external_done.
                    if (
                        self.source == SOURCE_USB_INSTALL
                        and result["type"] is FlowResultType.EXTERNAL_STEP_DONE
                    ):
                        result = await self.hass.config_entries.flow.async_configure(
                            flow_id=self.flow_id, user_input={}
                        )
                        # Core skips async_on_create_entry when replacing Ignore.
                        # Settle the waiting USB dialog after either add path.
                        if result["type"] is FlowResultType.CREATE_ENTRY or (
                            result.get("reason") == "already_configured"
                        ):
                            await self._async_finish_usb_installers()
                return

    async def _async_load_install_releases(self) -> None:
        """Cache the bounded published catalogue for this setup flow."""
        try:
            self._install_releases = await async_list_install_choices(self.hass)
        except ReleaseResolutionError:
            self._install_releases = []
            self._release_catalog_error = "release_catalog_unavailable"
        else:
            self._release_catalog_error = (
                None if self._install_releases else "release_catalog_empty"
            )

    def _reset_pending_panel(self) -> None:
        """Forget everything learned about a previously entered address."""
        self._pending_reinstall_entry = None
        self._pending_address = None
        self._pending_health = None
        self._pending_probe = None
        self._pending_probe_state = None
        self._pending_release = None
        self._pending_rc_tag = None
        self._pending_install_target = None
        self._pending_job_id = None
        self._authorization_errors = None
        self._typed_address = None

    async def async_step_add_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Look at the panel first, then connect, install, or say what is missing."""
        errors: dict[str, str] = {}

        if user_input is None:
            previous = self._pending_address
            self._reset_pending_panel()
            self._migration_targets = await async_find_migration_targets(self.hass)
            return self._show_add_panel_form(
                {CONF_ADDRESS: previous.stored_value} if previous else None, errors
            )

        self._reset_pending_panel()
        self._typed_address = "".join(
            char if char.isprintable() else "?"
            for char in str(user_input[CONF_ADDRESS]).strip()[:200]
        )
        try:
            address = normalize_address(user_input[CONF_ADDRESS])
            self._pending_reinstall_entry = next(
                (
                    entry
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                    if entry.data.get(CONF_ADDRESS) == address.stored_value
                ),
                None,
            )
        except InvalidAddressError:
            return self._show_add_panel_form(user_input, {"base": "invalid_address"})
        placeholders = self.context.get("title_placeholders", {})
        if "address" in placeholders:
            # A card's form can name another panel; the card then answers for
            # that address, so the panel it first named is not closed through it.
            self.context["title_placeholders"] = {
                **placeholders,
                "address": address.stored_value,
            }

        host = f"[{address.host}]" if ":" in address.host else address.host
        custom_port = address.port != normalize_address(host).port
        target: PinnedPanelTarget | None = None
        # Set when the address is one the installer refuses: a running panel
        # there is still added, but nothing is ever installed over ADB to it.
        install_refusal: InstallNetworkError | None = None
        try:
            target = await async_pin_install_target(self.hass, address)
        except InstallNetworkError as err:
            if (
                not custom_port
                and err.code is not InstallNetworkErrorCode.UNSAFE_TARGET
            ):
                return self._show_add_panel_form(
                    user_input, {"base": _install_network_error(err)}, cause=err
                )
            # Existing panels can be reached beyond the installer's LAN ranges.
            # A read-only health check grants no installation authority.
            install_refusal = err
        except Exception:
            _LOGGER.exception("Unexpected exception while pinning install target")
            return self._show_add_panel_form(user_input, {"base": "unknown"})

        self._pending_address = address
        self._pending_install_target = target
        if target is not None:
            try:
                manager = await async_get_install_job_manager(self.hass)
                active = await self._async_unblocked(
                    partial(
                        manager.async_find_active,
                        address.stored_value,
                        target.pinned.stored_value,
                    )
                )
            except InstallJobConflictError as err:
                return await self._async_show_unfinished_install(
                    err, {CONF_ADDRESS: address.stored_value}
                )
            except InstallJobError as err:
                return self._show_add_panel_form(
                    user_input,
                    {"base": "install_receipt_error"},
                    cause=err,
                )
            except Exception:
                _LOGGER.exception(
                    "Unexpected exception while loading an install receipt"
                )
                return self._show_add_panel_form(user_input, {"base": "unknown"})
            if active is not None:
                # An install already started here resumes with its original release.
                self._pending_job_id = active.job_id
                if active.phase is InstallPhase.HEALTHY_UNCLAIMED:
                    return await self.async_step_install_result()
                return await self._async_show_install_progress(active)

        client = HaPaneldClient(
            async_get_clientsession(self.hass),
            target.pinned if target is not None else address,
        )
        # What the app's port did, kept for the text and the log: the ADB probe
        # below says nothing about it.
        app_port = "answered"
        try:
            health = await client.async_get_health()
        except (CannotConnectError, InvalidResponseError) as err:
            app_port = _app_port_outcome(err)
            # Only when nothing answered may the way back be blocked.
            unreached = err if isinstance(err, NoAnswerError) else None
        except Exception:
            _LOGGER.exception(
                "Unexpected exception while checking for an existing ha-paneld"
            )
            return self._show_add_panel_form(user_input, {"base": "unknown"})
        else:
            if self._address_is_configured(address.stored_value):
                return await self._async_create_panel_entry(address, health)
            self._pending_health = health
            return await self._async_show_found_panel()

        # A panel at this address has just opened a session, so it runs the app
        # and reaches Home Assistant; only the way back to it is blocked.
        if (
            unreached is not None
            and not custom_port
            and (
                sighting := async_refused_hello_at(
                    self.hass,
                    address.host,
                    *((target.pinned.host,) if target is not None else ()),
                )
            )
        ):
            return self._show_add_panel_form(
                user_input,
                {"base": "panel_connected_unreachable"},
                cause=(
                    f"panel {sighting.did} opened a session from "
                    f"{sighting.peer or 'unknown'} reporting "
                    f"{','.join(sighting.addresses) or 'no addresses'}; "
                    f"port {address.port}: {type(unreached).__name__}"
                    + (
                        f" from {unreached.__cause__!r}"
                        if unreached.__cause__ is not None
                        else ""
                    )
                ),
            )

        # A fresh install chooses its listener; typing a custom port does not
        # configure it. Require health proof before using that endpoint, or a
        # host-only first contact before offering installation.
        if install_refusal is not None and not custom_port:
            return self._show_add_panel_form(
                user_input,
                {"base": "not_answering_outside_install_range"},
                cause=(
                    f"{install_refusal.code.value}; "
                    f"no panel answered on port {address.port} ({app_port})"
                ),
            )
        if custom_port or target is None:
            return self._show_add_panel_form(
                user_input,
                {"base": "invalid_install_address"},
                cause=f"port_{address.port}={app_port}",
            )

        try:
            # The first probe deliberately has no key. Discovering an authorization
            # requirement must remain read-only; only the explicit authorization
            # step may create and offer HA's durable key.
            probe = await async_probe_install_target(target.pinned)
        except Exception:
            _LOGGER.exception("Unexpected exception while classifying install target")
            return self._show_add_panel_form(user_input, {"base": "unknown"})

        state = probe.state.value
        if self._address_is_configured(address.stored_value) and state not in (
            "install_candidate",
            "adb_unauthorized",
        ):
            return self.async_abort(reason="already_configured")
        if state == "migration_candidate":
            # The old app is installed but not answering. This release cannot
            # move it, and the new app is never installed beside it.
            errors["base"] = "old_app_installed"
            return self._show_add_panel_form(
                user_input,
                errors,
                cause=f"port_{address.port}={app_port} adb_state={state}",
            )
        if state in ("install_candidate", "installed") and (
            _install_candidate_placeholders(probe) is None
        ):
            state = "retained_or_ambiguous"
        if state in ("adb_unauthorized", "install_candidate", "installed"):
            self._pending_probe = probe if state != "adb_unauthorized" else None
            self._pending_probe_state = state
            return await self.async_step_choose_version()
        if state == "adb_unreachable":
            # Neither port gave an answer; say which failed and how.
            errors["base"] = await _no_port_answered(app_port, target.pinned)
        else:
            errors["base"] = {
                "retained_or_ambiguous": "retained_or_ambiguous",
                "incompatible": "incompatible",
            }.get(state, "unknown")
        return self._show_add_panel_form(
            user_input,
            errors,
            cause=(
                f"port_{address.port}={app_port} adb_state={probe.state.value} "
                f"pinned={target.pinned.stored_value}"
            ),
        )

    async def async_step_add_known_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The address step, shown with a picker of panels known to other apps."""
        return await self.async_step_add_panel(user_input)

    async def async_step_found_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the found panel again, or start over if nothing was found."""
        if self._pending_address is None or self._pending_health is None:
            return await self.async_step_add_panel()
        return await self._async_show_found_panel()

    async def async_step_connect_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Connect the panel that was found, checking it is still the same one."""
        if self._pending_address is None:
            return self.async_abort(reason="unknown")
        back = {CONF_ADDRESS: self._pending_address.stored_value}
        try:
            pinned = self._pending_address
            if self._pending_install_target is not None:
                target = await async_revalidate_install_target(
                    self.hass, self._pending_install_target
                )
                pinned = target.pinned
            health = await HaPaneldClient(
                async_get_clientsession(self.hass), pinned
            ).async_get_health()
        except InstallNetworkError as err:
            return self._show_add_panel_form(
                back, {"base": _install_network_error(err)}
            )
        except CannotConnectError, InvalidResponseError:
            return self._show_add_panel_form(back, {"base": "cannot_connect"})
        except Exception:
            _LOGGER.exception(
                "Unexpected exception while confirming existing ha-paneld"
            )
            return self._show_add_panel_form(back, {"base": "unknown"})
        return await self._async_create_panel_entry(self._pending_address, health)

    async def async_step_choose_version(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the release for a clean panel, then authorize or preview it."""
        if (
            self._pending_address is None
            or self._pending_install_target is None
            or self._pending_probe_state is None
        ):
            return self.async_abort(reason="unknown")
        if user_input is None or self._release_catalog_error:
            # A submit after an error is "try again": load the list afresh.
            if self._install_releases is None or self._release_catalog_error:
                await self._async_load_install_releases()
            options = self._version_options()
            # One release on offer is nothing to choose; a test version still
            # gets its own warning on the install question.
            if len(options) != 1:
                return self._show_choose_version()
            user_input = {_CONF_RELEASE_CANDIDATE: options[0]["value"]}

        tag = user_input.get(_CONF_RELEASE_CANDIDATE, "")
        if tag == _STABLE_CHOICE:
            tag = ""
        releases = self._install_releases or []
        offered = {r["tag"] for r in releases}
        if tag != "" and tag not in offered:
            return self._show_choose_version(
                {_CONF_RELEASE_CANDIDATE: "invalid_release_candidate"}
            )
        if tag == "" and not releases:
            return self._show_choose_version({"base": "release_selection_required"})
        self._pending_rc_tag = tag or None
        try:
            self._pending_release = await async_resolve_install_choice(
                self.hass, self._pending_rc_tag
            )
        except ReleaseResolutionError:
            return self._show_choose_version({"base": "cannot_resolve_release"})
        except Exception:
            _LOGGER.exception("Unexpected exception while resolving ha-paneld release")
            return self._show_choose_version({"base": "unknown"})
        if self._pending_release.descriptor is None:
            return self._show_release_preview_only()
        if self._pending_probe_state == "adb_unauthorized":
            return await self._async_authorize_adb()
        return self._show_install_candidate_preview()

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the panel's Allow, or ask it again after a wait that failed."""
        if (
            self._pending_address is None
            or self._pending_install_target is None
            or self._pending_release is None
            or self._pending_release.descriptor is None
        ):
            return self.async_abort(reason="unknown")
        task = self._authorization_task
        if task is not None:
            if not task.done():
                return self._show_authorization_progress(task)
            self._authorization_task = None
            try:
                self._authorization_errors = self._accept_authorized_probe(
                    task.result()
                )
            except InstallNetworkError as err:
                self._authorization_errors = {"base": _install_network_error(err)}
            except Exception:
                _LOGGER.exception("Unexpected exception while waiting for ADB Allow")
                self._authorization_errors = {"base": "unknown"}
            return self.async_show_progress_done(next_step_id="authorize_adb_done")
        if user_input is None:
            return self._show_authorize_adb(self._authorization_errors)
        return await self._async_authorize_adb()

    async def async_step_authorize_adb_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show where the wait ended; input carried here is never a press.

        Core passes the input that ended a progress step on to its next step,
        so neither the Install press nor another Allow may be taken from it.
        """
        if (
            self._pending_address is None
            or self._pending_release is None
            or self._pending_release.descriptor is None
        ):
            return self.async_abort(reason="unknown")
        if self._pending_probe is None:
            return self._show_authorize_adb(self._authorization_errors)
        return self._show_install_candidate_preview()

    async def _async_authorize_adb(self) -> ConfigFlowResult:
        """Use a key the panel already trusts, or offer it and wait for Allow."""
        assert self._pending_install_target is not None
        self._authorization_errors = None
        try:
            target = await async_revalidate_install_target(
                self.hass, self._pending_install_target
            )
            signer = await async_get_adb_signer(self.hass)
            probe = await async_probe_install_target(target.pinned, signer)
        except InstallNetworkError as err:
            return self._show_authorize_adb({"base": _install_network_error(err)})
        except AdbCredentialError:
            return self._show_authorize_adb({"base": "adb_credential_error"})
        except Exception:
            _LOGGER.exception("Unexpected exception while authorizing ADB")
            return self._show_authorize_adb({"base": "unknown"})
        if probe.state is not InstallTargetState.ADB_UNAUTHORIZED:
            if errors := self._accept_authorized_probe(probe):
                return self._show_authorize_adb(errors)
            return self._show_install_candidate_preview()

        task = self.hass.async_create_task(
            async_wait_for_adb_allow(target.pinned, signer),
            "wait for the panel to allow Home Assistant",
        )
        self._authorization_task = task
        return self._show_authorization_progress(task)

    def _accept_authorized_probe(
        self, probe: InstallTargetProbe
    ) -> dict[str, str] | None:
        """Keep a classified installable panel, or name what stops it."""
        state = probe.state.value
        if state in ("install_candidate", "installed") and (
            _install_candidate_placeholders(probe) is not None
        ):
            self._pending_probe = probe
            self._pending_probe_state = state
            return None
        return {
            "base": {
                "adb_unauthorized": "adb_still_unauthorized",
                "migration_candidate": "old_app_installed",
                "adb_unreachable": "adb_unreachable",
                "incompatible": "incompatible",
            }.get(state, "retained_or_ambiguous")
        }

    def _show_authorization_progress(
        self, task: asyncio.Task[InstallTargetProbe]
    ) -> ConfigFlowResult:
        """Show the wait for Allow on the panel."""
        assert self._pending_address is not None
        return self.async_show_progress(
            step_id="authorize_adb",
            progress_action="authorize_adb",
            description_placeholders={"address": self._pending_address.stored_value},
            progress_task=task,
        )

    def _show_authorize_adb(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show the physical ADB trust checkpoint."""
        return self.async_show_form(
            step_id="authorize_adb",
            data_schema=vol.Schema({}),
            description_placeholders={
                "address": self._pending_address.stored_value
                if self._pending_address is not None
                else "",
                **INSTALL_RECOVERY_PLACEHOLDERS,
            },
            errors=errors,
        )

    async def async_step_confirm_install_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-prove and durably authorize one exact clean installation."""
        if (
            self._pending_address is None
            or self._pending_probe is None
            or self._pending_release is None
            or self._pending_install_target is None
        ):
            return self.async_abort(reason="unknown")
        if self._pending_release.descriptor is None:
            return self._show_release_preview_only()
        if user_input is None:
            return self._show_install_candidate_preview()

        # A kept entry may reinstall only after the clean-package probe.
        if self._address_is_configured(self._pending_address.stored_value) and (
            self._pending_reinstall_entry is None
            or self.hass.config_entries.async_get_entry(
                self._pending_reinstall_entry.entry_id
            )
            is not self._pending_reinstall_entry
            or self._pending_probe.state is not InstallTargetState.INSTALL_CANDIDATE
        ):
            return self.async_abort(reason="already_configured")
        try:
            target = await async_revalidate_install_target(
                self.hass, self._pending_install_target
            )
            created_credential = await async_get_adb_credential(self.hass)
            credential = await async_get_durable_adb_credential(self.hass)
            if credential.generation_id != created_credential.generation_id:
                raise AdbCredentialError
            probe = await async_probe_install_target(target.pinned, credential.signer)
        except InstallNetworkError as err:
            self._refusal_cause = err
            return self._show_install_candidate_preview(
                {"base": _install_network_error(err)}
            )
        except AdbCredentialError as err:
            self._refusal_cause = err
            return self._show_install_candidate_preview(
                {"base": "adb_credential_error"}
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while confirming installation")
            return self._show_install_candidate_preview({"base": "unknown"})

        if not _same_install_target(self._pending_probe, probe):
            self._refusal_cause = (
                f"previewed={self._pending_probe.state.value} "
                f"now={probe.state.value} "
                f"same_device={same_physical_target(probe, self._pending_probe)}"
            )
            return self._show_install_candidate_preview(
                {"base": "install_candidate_changed"}
            )

        if probe.state is InstallTargetState.INSTALLED:
            adb_target = probe.adb_target(target.pinned)
            assert adb_target is not None
            try:
                observed = await async_preflight_install(
                    adb_target,
                    credential.signer,
                    self._pending_release.descriptor,
                    admit_installed_target=True,
                )
                if not observed.target_installed:
                    return self._show_install_candidate_preview(
                        {"base": "installed_without_health"}
                    )
                installed_bytes = await async_installed_artifact_size(
                    adb_target,
                    credential.signer,
                    self._pending_release.descriptor,
                    expected_root_mode=observed.root_mode,
                )
            except InstallAdbError:
                return self._show_install_candidate_preview(
                    {"base": "installed_without_health"}
                )
            if installed_bytes is None:
                return self._show_install_candidate_preview(
                    {"base": "installed_artifact_mismatch"}
                )
            probe = replace(probe, installed_artifact_size=installed_bytes)

        try:
            plan = build_install_plan(
                target,
                probe,
                self._pending_release,
                credential.generation_id,
                expected_rc_tag=self._pending_rc_tag,
            )
            manager = await async_get_install_job_manager(self.hass)
            receipt, _created = await self._async_unblocked(
                partial(
                    manager.async_create_or_join,
                    plan.target,
                    plan.artifact,
                    plan.plan_sha256,
                    plan.adb_credential_id,
                )
            )
        except InstallJobConflictError as err:
            # The panel's serial is known only now, so this is where a second
            # card, or another address of the same panel, meets its install.
            return await self._async_show_unfinished_install(
                err, {CONF_ADDRESS: self._pending_address.stored_value}
            )
        except InstallPlanError as err:
            self._refusal_cause = err
            return self._show_install_candidate_preview(
                {"base": "install_plan_rejected"}
            )
        except InstallJobError as err:
            self._refusal_cause = err
            return self._show_install_candidate_preview(
                {"base": "install_receipt_error"}
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while creating an install job")
            return self._show_install_candidate_preview({"base": "unknown"})

        self._pending_job_id = receipt.job_id
        return await self._async_show_install_progress(receipt)

    def _show_install_candidate_preview(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Render the complete retained target and authenticated release plan."""
        assert self._pending_address is not None
        assert self._pending_probe is not None
        assert self._pending_release is not None
        placeholders = _install_candidate_placeholders(self._pending_probe)
        assert placeholders is not None
        placeholders.update(
            {
                "address": self._pending_address.stored_value,
                "version": self._pending_release.version,
                "tag": self._pending_release.tag,
                "sha256": self._pending_release.sha256,
                **INSTALL_RECOVERY_PLACEHOLDERS,
            }
        )
        step_id = (
            "confirm_install_rc"
            if self._pending_rc_tag is not None
            and self._pending_release is not None
            and "-" in self._pending_release.version
            else "confirm_install_candidate"
        )
        if self._pending_reinstall_entry is not None:
            # Say which entry the panel rejoins; a fresh address keeps its text.
            step_id = step_id.replace("confirm_install_", "confirm_reinstall_")
            placeholders["entry_title"] = self._pending_reinstall_entry.title
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_confirm_install_rc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use the same frozen consent path with an explicitly translated RC warning."""
        return await self.async_step_confirm_install_candidate(user_input)

    async def async_step_confirm_reinstall_candidate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The install offer for a panel that rejoins its existing entry."""
        return await self.async_step_confirm_install_candidate(user_input)

    async def async_step_confirm_reinstall_rc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The release-candidate offer for a panel that rejoins its entry."""
        return await self.async_step_confirm_install_candidate(user_input)

    async def async_step_release_preview_only(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain why an authenticated legacy release cannot be installed."""
        if self._pending_address is None or self._pending_release is None:
            return self.async_abort(reason="unknown")
        if user_input is not None:
            # This release can't be installed here; let the person pick another.
            self._pending_release = None
            return self._show_choose_version()
        return self._show_release_preview_only()

    def _show_release_preview_only(self) -> ConfigFlowResult:
        """Present an authenticated release that lacks the signed install contract."""
        assert self._pending_address is not None
        assert self._pending_release is not None
        return self.async_show_form(
            step_id="release_preview_only",
            data_schema=vol.Schema({}),
            description_placeholders={
                "address": self._pending_address.stored_value,
                "version": self._pending_release.version,
                "tag": self._pending_release.tag,
                "sha256": self._pending_release.sha256,
            },
        )

    async def _async_show_install_progress(
        self, receipt: InstallJobReceipt
    ) -> ConfigFlowResult:
        """Attach a flow-owned waiter without transferring worker ownership."""
        self._pending_job_id = receipt.job_id
        try:
            executor = await async_get_install_executor(self.hass)
            self._install_executor = executor
            worker = await executor.async_ensure_job(receipt.job_id)
        except InstallJobError:
            return self.async_abort(reason="install_receipt_error")
        except Exception:
            _LOGGER.exception("Unexpected exception while starting installation")
            return self.async_abort(
                reason="install_failed",
                description_placeholders=INSTALL_RECOVERY_PLACEHOLDERS,
            )

        if worker is None:
            try:
                manager = await async_get_install_job_manager(self.hass)
                refreshed = await manager.async_get(receipt.job_id)
            except InstallJobError:
                return self.async_abort(reason="install_receipt_error")
            except Exception:
                _LOGGER.exception("Unexpected exception while refreshing install job")
                return self.async_abort(
                    reason="install_failed",
                    description_placeholders=INSTALL_RECOVERY_PLACEHOLDERS,
                )
            if (
                refreshed.phase is InstallPhase.HEALTHY_UNCLAIMED
                or refreshed.is_terminal
            ):
                return await self.async_step_install_result()
            # The executor deliberately refuses to replay a worker that stopped
            # in this process, and registering an immediately completed waiter
            # here would loop. A job it will never advance is ended instead,
            # so its result is shown and the panel is free for a new attempt.
            try:
                ended = await executor.async_end_stalled_jobs(stopped_now=True)
            except InstallJobError as err:
                self._refusal_cause = err
                ended = ()
            if any(job.job_id == receipt.job_id for job in ended):
                return await self.async_step_install_result()
            return self.async_abort(reason="install_worker_stopped")

        self._progress_action = "installing"
        self._progress_waiter = self.hass.async_create_task(
            executor.async_wait(receipt.job_id, until_webview=True),
            f"wait for ha-paneld install {receipt.job_id}",
        )
        return self.async_show_progress(
            step_id="install_progress",
            progress_action="installing",
            description_placeholders={"address": receipt.target.address},
            progress_task=self._progress_waiter,
        )

    async def async_step_install_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete only the flow waiter; the detached worker remains process-owned."""
        if self._pending_job_id is None:
            return self.async_show_progress_done(next_step_id="install_result")
        waiter = self._progress_waiter
        executor = self._install_executor
        if (
            waiter is not None
            and waiter.done()
            and executor is not None
            and self._progress_action == "installing"
            and executor.is_updating_webview(self._pending_job_id)
        ):
            # The worker is updating the panel's browser engine; say so, then
            # wait for the rest of the job.
            self._progress_action = "updating_webview"
            waiter = self._progress_waiter = self.hass.async_create_task(
                executor.async_wait(self._pending_job_id),
                f"wait for ha-paneld install {self._pending_job_id}",
            )
        if waiter is not None and not waiter.done():
            return self.async_show_progress(
                step_id="install_progress",
                progress_action=self._progress_action,
                description_placeholders={
                    "address": (
                        self._pending_address.stored_value
                        if self._pending_address is not None
                        else ""
                    )
                },
                progress_task=waiter,
            )
        if waiter is not None:
            try:
                waiter.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.exception("Install progress waiter failed")
            self._progress_waiter = None
        return self.async_show_progress_done(next_step_id="install_result")

    async def async_step_install_result(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Display a terminal result or finalize one healthy install exactly once."""
        if self._pending_job_id is None:
            return self.async_abort(reason="unknown")
        try:
            manager = await async_get_install_job_manager(self.hass)
            receipt = await manager.async_get(self._pending_job_id)
        except InstallJobError:
            return self.async_abort(reason="install_receipt_error")
        except Exception:
            _LOGGER.exception("Unexpected exception while loading install result")
            return self.async_abort(
                reason="install_failed",
                description_placeholders=INSTALL_RECOVERY_PLACEHOLDERS,
            )

        if receipt.phase in {
            InstallPhase.CANCELLED,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }:
            return self.async_abort(
                reason=_install_terminal_abort_reason(receipt),
                description_placeholders=INSTALL_RECOVERY_PLACEHOLDERS,
            )
        if receipt.phase is InstallPhase.CONSUMED:
            return self.async_abort(reason="already_configured")
        if receipt.phase is not InstallPhase.HEALTHY_UNCLAIMED:
            return await self._async_show_install_progress(receipt)
        return await self._async_finalize_healthy_install(receipt)

    async def _async_finalize_healthy_install(
        self, receipt: InstallJobReceipt
    ) -> ConfigFlowResult:
        """Create the entry a healthy receipt proves, under the executor's lease.

        The executor re-proves the panel and owns the lease. This flow owns only
        what Home Assistant asks of it: the duplicate checks, the entry, and what
        the person is shown.
        """
        if self._flow_removed:
            return self.async_abort(reason="install_worker_stopped")
        retained = next(
            (
                entry
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.data.get(CONF_ADDRESS) == receipt.target.address
            ),
            None,
        )
        executor = self._install_executor
        if executor is None:
            try:
                executor = await async_get_install_executor(self.hass)
            except Exception:
                _LOGGER.exception("Unable to load install finalizer")
                return self._show_install_result_retry(
                    receipt, "install_finalization_retry"
                )
            self._install_executor = executor

        # Recorded before the first await, so removal mid-verification can hand
        # the lease back; the executor defers that until verification exits.
        self._finalizer_job_id = receipt.job_id
        result = await executor.async_verify_finalization(receipt.job_id, self.flow_id)
        if self._flow_removed:
            # async_remove may run during the verification awaits.
            executor.release_finalizer(receipt.job_id, self.flow_id)  # type: ignore[unreachable]
            return self.async_abort(reason="install_worker_stopped")
        outcome = result.outcome
        if outcome is FinalizationOutcome.BUSY:
            return self._show_install_result_retry(receipt, "install_finalization_busy")
        if outcome is FinalizationOutcome.RETRY:
            return self._show_install_result_retry(
                receipt, "install_finalization_retry"
            )
        if outcome is FinalizationOutcome.RECOVERY_REQUIRED:
            return self.async_abort(
                reason="install_recovery_required",
                description_placeholders=INSTALL_RECOVERY_PLACEHOLDERS,
            )
        if outcome is not FinalizationOutcome.VERIFIED or result.health is None:
            return self.async_abort(reason="install_receipt_error")
        if self._address_is_configured(receipt.target.address):
            # Reuse only the entry that existed before verification, and only
            # when the ordinary identity authority accepts the installed panel.
            if (
                retained is not None
                and self.hass.config_entries.async_get_entry(retained.entry_id)
                is retained
                and retained.data.get(CONF_ADDRESS) == receipt.target.address
                and accept_health(self.hass, retained, result.health)
            ):
                await executor.async_consume_finalization(
                    receipt.job_id, self.flow_id, retained.entry_id
                )
                close_offers(
                    self.hass, receipt.target.address, self.flow_id, pressed=True
                )
                self._finalizer_job_id = None
                self._pending_reinstall_entry = retained
                self._pending_address = normalize_address(receipt.target.address)
                self._pending_health = result.health
                return await self.async_step_panel_setup()
            executor.release_finalizer(receipt.job_id, self.flow_id)
            self._finalizer_job_id = None
            return self.async_abort(
                reason="reinstall_identity_mismatch"
                if retained is not None
                else "already_configured"
            )

        # Health proves the installation, not the owner's completed setup.
        # Keep the durable receipt unconsumed while its wizard is open: closing
        # this flow or restarting HA must leave the same install retryable.
        self._pending_address = normalize_address(receipt.target.address)
        self._pending_health = result.health
        # Read setup where the install reached the panel. A typed name the
        # panel does not answer to (not its own host name, nor an allowed
        # host) is refused there, though setup is done.
        self._pending_install_target = PinnedPanelTarget(
            original=self._pending_address,
            pinned=normalize_address(receipt.target.pinned_address),
        )
        try:
            complete = await self._async_new_panel_setup_complete(
                self._pending_install_target.pinned
            )
        except asyncio.CancelledError:
            executor.release_finalizer(receipt.job_id, self.flow_id)
            self._finalizer_job_id = None
            raise
        if self._flow_removed or not complete:
            executor.release_finalizer(receipt.job_id, self.flow_id)
            self._finalizer_job_id = None
            if self._flow_removed:
                return self.async_abort(reason="install_worker_stopped")  # type: ignore[unreachable]
            return await self.async_step_panel_setup()

        known = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, result.health.discovery_id
            )
            if result.health.discovery_id is not None
            else None
        )
        if known is not None and known.source != SOURCE_IGNORE:
            # The panel was added meanwhile under another of its addresses.
            # That entry is what this install became; a second is never made.
            await executor.async_consume_finalization(
                receipt.job_id, self.flow_id, known.entry_id
            )
            self._finalizer_job_id = None
            close_offers(self.hass, receipt.target.address, self.flow_id, pressed=True)
            return self.async_abort(reason="already_configured")

        # The lease stays held through ConfigEntries' actual add.
        # async_on_create_entry hands HA's generated entry id to the executor,
        # which records it and releases the lease.
        return self.async_create_entry(
            title=result.health.panel_id,
            data={CONF_ADDRESS: receipt.target.address},
            options=(
                {CONF_PRERELEASE_PANEL_BUILDS: True}
                if receipt.artifact.prerelease_opt_in
                else {}
            ),
        )

    def _show_install_result_retry(
        self, receipt: InstallJobReceipt, error: str
    ) -> ConfigFlowResult:
        """Keep a healthy receipt retryable without exposing target internals."""
        return self.async_show_form(
            step_id="install_result",
            data_schema=vol.Schema({}),
            description_placeholders={
                "address": receipt.target.address,
                "version": receipt.artifact.version_name,
            },
            errors={"base": error},
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Close the panel's other cards; hand the executor HA's entry id."""
        job_id = self._finalizer_job_id
        executor = self._install_executor
        entry = result.get("result")
        if isinstance(entry, ConfigEntry):
            # Added by hand, over USB or from one card: a card another channel
            # raised for this address must not stay beside the entry.
            address = entry.data.get(CONF_ADDRESS)
            if isinstance(address, str):
                close_offers(self.hass, address, self.flow_id, pressed=True)
        if job_id is not None and executor is not None:
            if isinstance(entry, ConfigEntry):
                # Never raises: the entry exists, and a receipt that cannot be
                # recorded must not make HA remove it or report it failed.
                await executor.async_consume_finalization(
                    job_id, self.flow_id, entry.entry_id
                )
            else:
                executor.release_finalizer(job_id, self.flow_id)
            self._finalizer_job_id = None
        result = await super().async_on_create_entry(result)
        if isinstance(entry, ConfigEntry) and isinstance(
            address := entry.data.get(CONF_ADDRESS), str
        ):
            client = HaPaneldClient(
                async_get_clientsession(self.hass), normalize_address(address)
            )
            if await async_connected_to_other_ha(
                self.hass, client
            ) and async_raise_foreign_ha_issue(self.hass, entry):
                result["description"] = "another_home_assistant"
        if job_id is not None and isinstance(entry, ConfigEntry):
            # Human sign-in must never retain the install finalizer lease. Core
            # supports continuing into options only after adding the entry.
            try:
                continuation = await self.hass.config_entries.options.async_init(
                    entry.entry_id, context={"source": "onboarding"}
                )
            except Exception:
                _LOGGER.exception(
                    "Could not open panel onboarding after entry creation"
                )
            else:
                result["next_flow"] = (FlowType.OPTIONS_FLOW, continuation["flow_id"])
        return result

    def async_remove(self) -> None:
        """Detach this UI flow without cancelling the process-owned worker."""
        self._flow_removed = True
        if self._setup_watch is not None and not self._setup_watch.done():
            self._setup_watch.cancel()
        if self._progress_waiter is not None and not self._progress_waiter.done():
            self._progress_waiter.cancel()
        self._progress_waiter = None
        if self._finalizer_job_id is not None and self._install_executor is not None:
            self._install_executor.release_finalizer(
                self._finalizer_job_id, self.flow_id
            )
        super().async_remove()

    def _address_is_configured(self, address: str) -> bool:
        """Check the configured host without contacting the panel."""
        return bool(configured_entries_for_host(self.hass, address))

    async def _async_create_panel_entry(
        self,
        address: PanelAddress,
        health: PanelHealth,
    ) -> ConfigFlowResult:
        """Create an entry, bound to the account the panel already asked as.

        A panel that has not asked yet is bound when it does. A new panel on
        the old app id is refused: this release cannot move it.
        """
        for existing in self.hass.config_entries.async_entries(DOMAIN):
            if (
                reinstalled(self.hass, existing, health)
                and not (
                    self._pending_job_id is not None
                    and existing is self._pending_reinstall_entry
                )
                and not _entry_holds_its_address(self, existing)
            ):
                if accept_health(self.hass, existing, health):
                    self.hass.config_entries.async_update_entry(
                        existing,
                        data={**existing.data, CONF_ADDRESS: address.stored_value},
                    )
                # A busy operation owns adoption; it must never create a duplicate.
                return self.async_abort(reason="already_configured")
            if existing.data.get(CONF_ADDRESS) == address.stored_value:
                accepted = accept_health(self.hass, existing, health)
                if (
                    existing is not self._pending_reinstall_entry
                    or self._pending_job_id is None
                ):
                    return self.async_abort(reason="already_configured")
                if not accepted:
                    return self.async_abort(reason="reinstall_identity_mismatch")
                return self._async_finish_panel_entry(address, health)
        # A panel that reports its identity is known by it. Adding a known panel
        # again, at the address it has moved to, repairs its entry's stored
        # address rather than creating a second entry for one panel: health at
        # the new address has just answered as it, and an administrator asked.
        if health.discovery_id is not None:
            # A discovery card for this panel may be pending; it must not
            # refuse the person who is adding the panel by hand, and Core
            # closes that card itself when the entry is created. A person who
            # pressed another channel's card for the panel asked as plainly.
            known = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, health.discovery_id
            )
            replace_ignore = (
                known is not None
                and known.source == SOURCE_IGNORE
                and (
                    self.source in {SOURCE_USER, SOURCE_USB_INSTALL}
                    or CONFIRMED in self.context.get("title_placeholders", {})
                )
            )
            if known is not None and not is_installation(known) and not replace_ignore:
                return self.async_abort(reason="already_configured")
            if (known is None or replace_ignore) and (
                unclaimed := await self._async_unclaimed_install_of(health)
            ):
                return await self._async_show_unfinished_install(
                    unclaimed, {CONF_ADDRESS: address.stored_value}
                )
            await self.async_set_unique_id(health.discovery_id, raise_on_progress=False)
            if not replace_ignore:
                self._abort_if_unique_id_configured(
                    updates={CONF_ADDRESS: address.stored_value}, reload_on_update=False
                )
        if runs_old_app(health):
            return self.async_abort(
                reason="old_app_installed",
                description_placeholders=_OLD_APP_PLACEHOLDERS,
            )
        if not await self._async_new_panel_setup_complete(address):
            self._pending_address = address
            self._pending_health = health
            return await self.async_step_panel_setup()
        return self._async_finish_panel_entry(
            address, health, await self._async_asking_user_id(health)
        )

    async def _async_new_panel_setup_complete(self, address: PanelAddress) -> bool:
        """Require the panel's own durable completion before adding its device."""
        try:
            state = await HaPaneldClient(
                async_get_clientsession(self.hass), address
            ).async_get_setup_state()
        except RequestRefusedError as err:
            # The setup form that follows shows it and logs it.
            self._setup_refusal = err
            return False
        except HaPaneldError:
            return False
        return state.complete

    async def _async_asking_user_id(self, health: PanelHealth) -> str | None:
        """Return the account this panel asked to connect as, if it may be bound.

        The account comes from the panel's own ``hello``, refused because no entry
        existed yet. An account that has since gone, been deactivated or turned
        out to be Home Assistant's own is not bound.
        """
        user = await async_confirmable_user(
            self.hass, async_binding_request(self.hass, health.discovery_id)
        )
        return None if user is None else user.id

    def _async_finish_panel_entry(
        self,
        address: PanelAddress,
        health: PanelHealth,
        user_id: str | None = None,
    ) -> ConfigFlowResult:
        """Create the entry, bound to the panel's account or to none yet."""
        async_discard_binding_request(self.hass, health.discovery_id)
        retained = self._pending_reinstall_entry
        if self._pending_job_id is not None and retained is not None:
            if (
                self.hass.config_entries.async_get_entry(retained.entry_id)
                is not retained
                or retained.data.get(CONF_ADDRESS) != address.stored_value
                or not accept_health(self.hass, retained, health)
            ):
                return self.async_abort(reason="reinstall_identity_mismatch")
            return self.async_abort(reason="reinstall_successful")
        data: dict[str, Any] = {CONF_ADDRESS: address.stored_value}
        if health.installation_identity:
            data[CONF_INSTALL_IDENTITY] = True
        if user_id is not None:
            data[CONF_TRANSPORT_USER_ID] = user_id
        known = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, self.unique_id
            )
            if self.unique_id is not None
            else None
        )
        if known is not None and known.source == SOURCE_IGNORE:
            # Core skips async_on_create_entry when it replaces Ignore. The
            # explicit flow is committing now, so its other cards can close.
            close_offers(self.hass, address.stored_value, self.flow_id, pressed=True)
        # A discovered panel keeps the name its card promised. A manually added one
        # has no advertisement to read, so it stays on the panel id.
        return self.async_create_entry(
            title=self.context.get("title_placeholders", {}).get("name")
            or health.panel_id,
            data=data,
        )


for _name, _step in list(vars(HaPaneldConfigFlow).items()):
    if _name.startswith("async_step_"):
        setattr(HaPaneldConfigFlow, _name, closes_when_done(_step))


_FRIENDLY_NAME_MAX_LENGTH = 64


def _install_candidate_placeholders(
    probe: InstallTargetProbe,
) -> dict[str, str] | None:
    """Return complete physical facts for a clean or installed target."""
    if (
        probe.model is None
        or probe.serial is None
        or probe.primary_abi is None
        or probe.android_sdk is None
    ):
        return None
    return {
        "model": markdown_literal(probe.model),
        "serial": markdown_literal(probe.serial),
        "abi": markdown_literal(probe.primary_abi),
        "sdk": str(probe.android_sdk),
    }


def _same_install_target(
    expected: InstallTargetProbe, observed: InstallTargetProbe
) -> bool:
    """Require physical identity and a safe clean-or-adopt state to remain exact."""
    return (
        observed.state
        in {
            InstallTargetState.INSTALL_CANDIDATE,
            InstallTargetState.INSTALLED,
        }
        and not (
            expected.state is InstallTargetState.INSTALLED
            and observed.state is InstallTargetState.INSTALL_CANDIDATE
        )
        and _install_candidate_placeholders(observed) is not None
        and same_physical_target(observed, expected)
    )


def _app_port_outcome(error: HaPaneldError) -> str:
    """Name what the panel app's port did when its health was read."""
    if isinstance(error, InvalidResponseError):
        return "invalid_response"
    cause = error.__cause__
    if cause is None:
        # It answered over HTTP, with a status other than the app's.
        return "not_panel_app"
    if isinstance(cause, TimeoutError):
        return "timed_out"
    if isinstance(cause, ClientResponseError | ClientPayloadError):
        # Bytes came back, but not as HTTP the app would send.
        return "malformed_response"
    if isinstance(cause, ServerDisconnectedError | ConnectionResetError):
        return "closed"
    os_error = getattr(cause, "os_error", cause)
    if isinstance(os_error, ConnectionRefusedError) or (
        getattr(os_error, "errno", None) == errno.ECONNREFUSED
    ):
        return "refused"
    return "connection_failed"


async def _no_port_answered(app_port: str, pinned: PanelAddress) -> str:
    """The add-panel error when neither the app's port nor ADB answered."""
    if app_port == "refused":
        return "app_port_refused"
    if app_port in ("invalid_response", "not_panel_app", "malformed_response"):
        return "app_port_not_panel"
    if await _async_host_answers(pinned):
        # Something is there, but the health read got no usable answer: it
        # ran out of time, was closed or broke off. Which one is in the log.
        return "app_port_no_answer"
    return "panel_unreachable"


async def _async_host_answers(address: PanelAddress) -> bool:
    """Whether anything at all answers at that address.

    After the app's health read timed out, it tells apart "the port took the
    connection and gave no answer" from "nothing is at that address", so each
    gets the advice that fits. A refused connection is an answer: something is
    listening on the host.
    """
    try:
        async with asyncio.timeout(_HOST_PROBE_SECONDS):
            _reader, writer = await asyncio.open_connection(address.host, address.port)
    except ConnectionRefusedError:
        return True
    except OSError, TimeoutError:
        return False
    writer.close()
    with contextlib.suppress(OSError, TimeoutError, asyncio.CancelledError):
        await writer.wait_closed()
    return True


def _install_network_error(error: InstallNetworkError) -> str:
    """Map a privacy-safe network refusal to a translated flow error."""
    return {
        InstallNetworkErrorCode.INVALID_HOST: "invalid_install_address",
        InstallNetworkErrorCode.RESOLUTION_FAILED: "install_resolution_failed",
        InstallNetworkErrorCode.RESOLUTION_TIMEOUT: "install_resolution_timeout",
        InstallNetworkErrorCode.TOO_MANY_RESULTS: "install_too_many_addresses",
        InstallNetworkErrorCode.UNSAFE_TARGET: "unsafe_install_target",
        InstallNetworkErrorCode.PINNED_TARGET_REMOVED: "install_target_changed",
    }[error.code]


def _install_terminal_abort_reason(receipt: InstallJobReceipt) -> str:
    """Expose one privacy-safe and actionable durable installer outcome."""
    result_code = receipt.result_code
    if receipt.phase is InstallPhase.CANCELLED:
        if result_code is None:
            return "install_cancelled"
        return _CANCELLED_ABORT_REASONS.get(result_code, "install_cancelled")
    if receipt.phase is InstallPhase.FAILED:
        if result_code is None:
            return "install_failed"
        return _FAILED_ABORT_REASONS.get(result_code, "install_failed")
    if receipt.phase is InstallPhase.RECOVERY_REQUIRED:
        if result_code is None:
            return "install_recovery_required"
        if receipt.result_subcode == HEALTH_UNREACHABLE_SUBCODE:
            return "install_health_unreachable"
        return _RECOVERY_ABORT_REASONS.get(result_code, "install_recovery_required")
    return "install_failed"


async def async_wait_for_adb_allow(
    address: PanelAddress, signer: PythonRSASigner
) -> InstallTargetProbe:
    """Hold one key offer until a fresh connection is trusted.

    Android may deliver a remembered Allow to another transport, leaving the
    offering connection unopened after the owner allowed. While the offer is
    pending, fresh connections that never offer the key are probed too, and
    the first one the panel trusts ends the offer early.
    """

    async def watch_fresh_connections() -> InstallTargetProbe:
        try:
            while True:
                await asyncio.sleep(_ADB_ALLOW_POLL_SECONDS)
                fresh = await async_probe_install_target(address, signer)
                if fresh.state not in (
                    InstallTargetState.ADB_UNAUTHORIZED,
                    InstallTargetState.ADB_UNREACHABLE,
                ):
                    return fresh
        finally:
            # A trusted connection or a failure ends the offer; a cancellation
            # means the offer already ended.
            task = asyncio.current_task()
            if task is not None and not task.cancelling():
                ends_offer.reschedule(asyncio.get_running_loop().time())

    watcher: asyncio.Task[InstallTargetProbe] | None = None
    try:
        # The offer stays awaited here, so closing the dialog cancels it at once.
        async with asyncio.timeout(None) as ends_offer:
            watcher = asyncio.create_task(watch_fresh_connections())
            offered = await async_probe_install_target(
                address, signer, authorize=True, approval_seconds=_ADB_APPROVAL_SECONDS
            )
    except TimeoutError:
        if watcher is None or not watcher.done():
            raise
        return watcher.result()
    finally:
        if watcher is not None and not watcher.done():
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            # Closing the dialog during the watcher's connection close must
            # still end this wait, not be absorbed with the watcher's own.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
    if watcher.done() and not watcher.cancelled():
        # A trusted connection that ended in the offer's own loop step wins.
        return watcher.result()
    if offered.state in (
        InstallTargetState.ADB_UNAUTHORIZED,
        InstallTargetState.ADB_UNREACHABLE,
    ):
        return offered
    # Android allows the offering connection; every later operation opens its
    # own, which works only if the panel remembered the key.
    return await async_probe_install_target(address, signer)


@dataclass
class AdbAuthorization:
    """One existing-panel wait, retained until its completion screen is shown."""

    task: asyncio.Task[str | None] | None = None
    #: Values the finished wait's error text names.
    placeholders: dict[str, str] = field(default_factory=dict)


async def async_authorize_adb_form[ResultT: FlowResult[Any, Any]](
    flow: FlowHandler[Any, ResultT, Any],
    entry: ConfigEntry,
    panel: str,
    user_input: dict[str, Any] | None,
    authorization: AdbAuthorization,
    *,
    done: bool = False,
) -> ResultT | None:
    """Share the Options/Repairs wait; only the done step may finish it.

    Core forwards progress input to the next step. That input must neither
    offer the key again nor select an installer-failure menu action.
    """
    error = None
    task = authorization.task
    if done:
        if task is not None and task.done():
            authorization.task = None
            error = task.result()
            if error is None:
                async_clear_adb_authorization(flow.hass, entry.entry_id)
                return None
    else:
        if task is not None and task.done():
            return flow.async_show_progress_done(next_step_id="authorize_adb_done")
        if task is None and user_input is not None:
            task = authorization.task = flow.hass.async_create_task(
                async_authorize_existing_panel_adb(
                    flow.hass, entry, authorization.placeholders
                ),
                "wait for the configured panel to allow Home Assistant",
            )
        if task is not None:
            return flow.async_show_progress(
                step_id="authorize_adb",
                progress_action="authorize_adb",
                description_placeholders={"panel": panel},
                progress_task=task,
            )
    return flow.async_show_form(
        step_id="authorize_adb",
        data_schema=vol.Schema({}),
        description_placeholders={
            "panel": panel,
            **INSTALL_RECOVERY_PLACEHOLDERS,
            **authorization.placeholders,
        },
        errors={"base": error} if error else None,
    )


async def async_authorize_existing_panel_adb(
    hass: HomeAssistant,
    entry: ConfigEntry,
    placeholders: dict[str, str] | None = None,
) -> str | None:
    """Authorize the loaded entry's pinned panel through the existing ADB seam."""
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    expected = getattr(getattr(coordinator, "data", None), "health", None)
    if not isinstance(expected, PanelHealth):
        return "cannot_connect"
    # The entry identity survives an address change even if a recent
    # coordinator read followed that address to a different panel.
    entry_did = entry.unique_id
    expected_did = (
        entry_did
        if entry_did is not None and is_valid_discovery_id(entry_did)
        else _panel_did(entry)
    )
    try:
        address = normalize_address(entry.data[CONF_ADDRESS])
        target = await async_pin_entry_target(hass, address, expected, expected_did)
        if target is None:
            return "panel_identity_changed"
        await async_revalidate_install_target(hass, target)
        signer = await async_get_adb_signer(hass)
        probe = await async_wait_for_adb_allow(target.pinned, signer)
        await async_revalidate_install_target(hass, target)
    except InstallNetworkError as err:
        return _install_network_error(err)
    except CannotConnectError, InvalidResponseError:
        return "cannot_connect"
    except AdbCredentialError:
        return "adb_credential_error"
    except Exception:
        _LOGGER.exception("Unexpected exception authorizing ADB for configured panel")
        return "unknown"

    if probe.state is InstallTargetState.ADB_UNAUTHORIZED:
        return "adb_still_unauthorized"
    if probe.state in {
        InstallTargetState.INSTALLED,
        InstallTargetState.MIGRATION_CANDIDATE,
    }:
        if await hass.config_entries.async_reload(entry.entry_id):
            return None
        return "unknown"
    if probe.state is InstallTargetState.ADB_UNREACHABLE and (
        lease := await local_adb.async_held_lease(
            hass, entry, target.pinned, "Allowing updates"
        )
    ):
        error, found = lease.reason()
        if placeholders is not None:
            placeholders.update(found)
        return error
    return {
        InstallTargetState.ADB_UNREACHABLE: "adb_unreachable",
        InstallTargetState.RETAINED_OR_AMBIGUOUS: "retained_or_ambiguous",
    }.get(probe.state, "panel_identity_changed")


class HaPaneldOptionsFlow(OptionsFlow):
    """Complete setup, configure panel updates, authorise ADB or remove the app."""

    _removal_risks_url: str | None = None

    def __init__(self) -> None:
        self._adb_authorization = AdbAuthorization()

    def _current_panel_health(self) -> PanelHealth | None:
        """Use the loaded entry's panel identity for ADB consent."""
        coordinator = getattr(
            getattr(self.config_entry, "runtime_data", None), "coordinator", None
        )
        health = getattr(getattr(coordinator, "data", None), "health", None)
        return health if isinstance(health, PanelHealth) else None

    def _onboarding_client(self) -> HaPaneldClient:
        return HaPaneldClient(
            async_get_clientsession(self.hass),
            normalize_address(self.config_entry.data[CONF_ADDRESS]),
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer this panel's setup, updates and available ADB consent."""
        if self.context.get("source") == "onboarding":
            return await self.async_step_onboarding()
        options = ["updates", "panel_logs", "owner_report"]
        if self._current_panel_health() is not None:
            options.append("authorize_adb")
        if CONF_TRANSPORT_USER_ID not in self.config_entry.data:
            options.insert(0, "onboarding")
        # Offered whether or not the app answers: a run cut short may already
        # have removed it, and the retry finishes over ADB.
        options.append("remove_app")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_owner_report(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the owner review a draft without changing or contacting the panel."""
        if user_input is not None:
            return self.async_create_entry(data=dict(self.config_entry.options))
        coordinator = getattr(
            getattr(self.config_entry, "runtime_data", None), "coordinator", None
        )
        return self.async_show_form(
            step_id="owner_report",
            data_schema=vol.Schema({}),
            description_placeholders={
                "issue_url": owner_report_issue_url(getattr(coordinator, "data", None))
            },
        )

    async def async_step_panel_logs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the one log destination and source policy for every panel."""
        from .panel_logs import async_get_log_router

        router = async_get_log_router(self.hass)
        policy = router.policy
        destination = policy.get("destination") or {}
        saved = router.saved_sinks() if not destination else {}
        errors: dict[str, str] = {}
        if user_input is not None:
            choice = user_input["destination"]
            sink = None
            if choice.startswith("saved:"):
                sink = saved.get(choice.removeprefix("saved:"))
                if sink is None:
                    errors["destination"] = "log_sink_unavailable"
            elif choice != "none":
                sink = {"protocol": choice}
                if choice != "home_assistant":
                    host = user_input["host"].strip()
                    if not host:
                        errors["host"] = "log_host_required"
                    sink.update(host=host, port=user_input["port"])
            if not errors:
                sources = {"app": user_input["app_level"]}
                if user_input["system_level"]:
                    sources["system"] = user_input["system_level"]
                updated_policy: dict[str, Any] = {
                    "destination": sink,
                    "sources": sources,
                }
                if (
                    sink is not None
                    and sink["protocol"] in {"udp", "tcp", "http"}
                    and user_input.get("also_home_assistant", False)
                ):
                    updated_policy["also_home_assistant"] = True
                try:
                    await router.async_configure(updated_policy)
                except vol.Invalid:
                    errors["base"] = "log_destination_invalid"
                else:
                    return self.async_create_entry(data=dict(self.config_entry.options))
        defaults = {
            "destination": destination.get("protocol", "none"),
            "host": destination.get("host", ""),
            "port": destination.get("port", 514),
            "app_level": policy.get("sources", {}).get("app", 5),
            "system_level": policy.get("sources", {}).get("system", 0),
            "also_home_assistant": policy.get("also_home_assistant", False),
            **(user_input or {}),
        }
        choices: list[SelectOptionDict] = [
            {"value": "none", "label": "Off"},
            {"value": "home_assistant", "label": "Home Assistant"},
            {"value": "udp", "label": "Syslog (UDP)"},
            {"value": "tcp", "label": "Syslog (TCP)"},
            {"value": "http", "label": "HTTP"},
        ]
        choices.extend(
            {
                "value": f"saved:{did}",
                "label": f"{sink['host']}:{sink['port']} ({sink['protocol']})",
            }
            for did, sink in saved.items()
        )
        levels = {
            2: "Verbose",
            3: "Debug",
            4: "Info",
            5: "Warning",
            6: "Error",
            7: "Fatal",
        }
        return self.async_show_form(
            step_id="panel_logs",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "destination", default=defaults["destination"]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=choices,
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="destination",
                        )
                    ),
                    vol.Optional("host", default=defaults["host"]): str,
                    vol.Required("port", default=defaults["port"]): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=65535)
                    ),
                    vol.Required("app_level", default=defaults["app_level"]): vol.In(
                        levels
                    ),
                    vol.Required(
                        "system_level", default=defaults["system_level"]
                    ): vol.In({0: "Off", **levels}),
                    vol.Optional(
                        "also_home_assistant", default=defaults["also_home_assistant"]
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_updates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow prerelease panel builds for this panel, retaining other options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_PRERELEASE_PANEL_BUILDS: user_input[
                        CONF_PRERELEASE_PANEL_BUILDS
                    ],
                }
            )
        return self.async_show_form(
            step_id="updates",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PRERELEASE_PANEL_BUILDS,
                        default=self.config_entry.options.get(
                            CONF_PRERELEASE_PANEL_BUILDS, False
                        ),
                    ): BooleanSelector()
                }
            ),
        )

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer HA's durable ADB key only after explicit existing-panel consent."""
        entry = self.config_entry
        form = await async_authorize_adb_form(
            self,
            entry,
            panel_display_name(self.hass, entry),
            user_input,
            self._adb_authorization,
        )
        if form is not None:
            return form
        return self.async_create_entry(data=dict(entry.options))

    async def async_step_authorize_adb_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show where the wait ended; forwarded input is never a press."""
        entry = self.config_entry
        form = await async_authorize_adb_form(
            self,
            entry,
            panel_display_name(self.hass, entry),
            None,
            self._adb_authorization,
            done=True,
        )
        if form is not None:
            return form
        return self.async_create_entry(data=dict(entry.options))

    def _onboarded(self) -> bool:
        """Return whether the panel has signed in, which binds its account."""
        return CONF_TRANSPORT_USER_ID in self.config_entry.data

    async def async_step_onboarding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the owner to the panel's own setup page, then confirm it connected.

        A form, not an external step: Home Assistant's options dialog shows an
        external step with no title or text at all. A form never moves on by
        itself, so the owner says when they are done.
        """
        client = self._onboarding_client()
        errors: dict[str, str] = {}
        if user_input is None:
            await async_offer_ha_url(self.hass, client)
        elif self._onboarded():
            return self.async_abort(reason="panel_connected")
        else:
            errors["base"] = "panel_not_connected"
        return self.async_show_form(
            step_id="onboarding",
            data_schema=vol.Schema({}),
            description_placeholders={"setup_url": client.setup_url},
            errors=errors,
        )

    async def async_step_remove_app(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove the app on the one Submit under the risks link."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            # Options flows close with their entry; keep the owner's request in
            # Repairs until the scheduled entry deletion has actually completed.
            await async_request_removal(self.hass, self.config_entry, notify=False)
            try:
                await async_remove_app(self.hass, self.config_entry)
            except RemovalError as err:
                errors["base"] = f"removal_{err.reason}"
                placeholders = err.placeholders
                if (
                    err.reason == REASON_ADB_AUTHORIZATION
                    and self._current_panel_health() is None
                ):
                    # Allow updates is offered only while the panel answers.
                    errors["base"] = "removal_adb_authorization_unavailable"
            else:
                # Forget the panel once this flow, which belongs to it, has closed.
                self.hass.async_create_task(
                    self.hass.config_entries.async_remove(self.config_entry.entry_id)
                )
                return self.async_abort(reason="removed_entry_deleted")
        if self._removal_risks_url is None:
            coordinator = getattr(
                getattr(self.config_entry, "runtime_data", None), "coordinator", None
            )
            status = getattr(getattr(coordinator, "data", None), "status", None)
            parameters = help_parameters(
                status if isinstance(status, PanelStatus) else None
            )
            firmware = await async_read_firmware(self.hass, self.config_entry)
            if firmware is not None:
                parameters["fw"] = firmware
            self._removal_risks_url = help_url("removal-risks", **parameters)
        return self.async_show_form(
            step_id="remove_app",
            data_schema=vol.Schema({}),
            description_placeholders={
                "panel": panel_display_name(self.hass, self.config_entry),
                "risks_url": self._removal_risks_url,
                **placeholders,
            },
            errors=errors,
        )

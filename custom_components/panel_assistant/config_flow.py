"""Config flow for ha-paneld."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import unicodedata
from dataclasses import replace
from typing import Any

import voluptuous as vol
from homeassistant.auth.models import User
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    FlowType,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowHandler, FlowResult, UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .adb_credentials import (
    AdbCredentialError,
    async_get_adb_credential,
    async_get_adb_signer,
    async_get_durable_adb_credential,
)
from .browser_delivery import async_register_browser_delivery
from .browser_panel import PANEL_PATH, async_register_browser_panel
from .client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidAddressError,
    InvalidResponseError,
    PanelAddress,
    PanelHealth,
    is_valid_discovery_id,
    normalize_address,
)
from .const import (
    CONF_AUTHORITY,
    CONF_PRERELEASE_PANEL_BUILDS,
    CONF_TRANSPORT_USER_ID,
    DEFAULT_PORT,
    DOMAIN,
    help_url,
)
from .failure_repair import async_clear_adb_authorization
from .ha_url import async_offer_ha_url
from .identity import CONF_INSTALL_IDENTITY, accept_health, is_installation
from .install_adb import (
    ADB_PORT,
    InstallAdbError,
    async_installed_artifact_size,
    async_preflight_install,
    same_physical_target,
)
from .install_executor import (
    FinalizationOutcome,
    InstallExecutor,
    async_get_install_executor,
)
from .install_jobs import (
    InstallJobConflictError,
    InstallJobError,
    InstallJobReceipt,
    InstallPhase,
    InstallResultCode,
    async_get_install_job_manager,
)
from .install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
    PinnedPanelTarget,
    async_pin_entry_target,
    async_pin_install_target,
    async_revalidate_install_target,
)
from .install_plan import InstallPlanError, build_install_plan
from .migration_targets import (
    ADB_SERVICE_TYPE,
    MigrationTarget,
    adb_candidate,
    address_schema,
    async_check_migration_target,
    async_find_migration_targets,
    async_offer_migration_targets,
    ignored_card_data,
    offer_blocked,
)
from .provisioning import (
    InstallTargetProbe,
    InstallTargetState,
    async_probe_install_target,
)
from .release import (
    ReleaseArtifact,
    ReleaseResolutionError,
)
from .release_catalog import async_list_install_choices, async_resolve_install_choice
from .removal import RemovalError, async_read_firmware, async_remove_app
from .status import PanelStatus
from .transport import (
    AUTHORITIES,
    AUTHORITY_NATIVE,
    _panel_did,
    async_bind_user,
    async_binding_request,
    async_confirmable_user,
    async_discard_binding_request,
    authority_options,
    effective_authority,
    native_entities_turned_off,
)
from .update_policy import prereleases_allowed
from .update_route_repair import help_parameters

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

_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_ADDRESS): TextSelector(TextSelectorConfig())}
)
_CONF_RELEASE_CANDIDATE = "release_candidate"
# The recommended stable release's option value. The frontend treats an empty
# value as unfilled, so a required selector offering "" can never be submitted.
_STABLE_CHOICE = "stable"
_DATA_DISCOVERY_NAMES = "discovery_names"
_SETUP_POLL_SECONDS = 3
_HOST_PROBE_SECONDS = 3
_SETUP_WATCH_SECONDS = 60 * 60


class HaPaneldConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an ha-paneld config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the flow that chooses who owns a panel's commands."""
        return HaPaneldOptionsFlow()

    _pending_address: PanelAddress | None = None
    _pending_health: PanelHealth | None = None
    _pending_discovery_id: str | None = None
    _pending_friendly_name: str | None = None
    _discovery_title: str | None = None
    _pending_probe: InstallTargetProbe | None = None
    _pending_probe_state: str | None = None
    _pending_release: ReleaseArtifact | None = None
    _pending_rc_tag: str | None = None
    _pending_install_target: PinnedPanelTarget | None = None
    _pending_job_id: str | None = None
    _progress_waiter: asyncio.Task[InstallJobReceipt] | None = None
    _install_executor: InstallExecutor | None = None
    _finalizer_job_id: str | None = None
    _flow_removed = False
    _install_releases: list[dict[str, Any]] | None = None
    _setup_watch: asyncio.Task[None] | None = None
    _release_catalog_error: str | None = None
    _pending_bind_user_id: str | None = None
    _migration_targets: list[MigrationTarget] | None = None
    _migration_target: MigrationTarget | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user add a panel by its address or install one over USB."""
        # HA loads dependencies before the first flow, but domain async_setup
        # need not run until an entry exists. USB delivery must be ready now.
        async_register_browser_delivery(self.hass)
        await async_register_browser_panel(self.hass)
        # Someone adding a panel by hand is the moment to show every known one.
        async_offer_migration_targets(self.hass, recheck=True)
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
        """Link to browser-owned USB installation without creating an entry."""
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(
            step_id="install_usb",
            data_schema=vol.Schema({}),
            description_placeholders={"usb_install_url": f"/{PANEL_PATH}"},
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Offer a verified local mDNS discovery for explicit confirmation."""
        if discovery_info.type == ADB_SERVICE_TYPE:
            return await self._async_discovered_adb(discovery_info)
        discovery_id = discovery_info.properties.get("did")
        if (
            discovery_info.port != DEFAULT_PORT
            or not isinstance(discovery_id, str)
            or not is_valid_discovery_id(discovery_id)
        ):
            return self.async_abort(reason="invalid_discovery")
        # An IPv6 literal must be bracketed before a port can follow it; bare, its
        # colons read as a port and every IPv6-only panel was refused here.
        advertised = discovery_info.ip_address
        host = f"[{advertised}]" if advertised.version == 6 else str(advertised)
        try:
            address = normalize_address(host)
        except InvalidAddressError:
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(discovery_id)
        for existing in self.hass.config_entries.async_entries(DOMAIN):
            if (
                existing.data.get(CONF_ADDRESS) == address.stored_value
                and existing.unique_id == discovery_id
            ):
                return self.async_abort(reason="already_configured")

        try:
            health = await HaPaneldClient(
                async_get_clientsession(self.hass), address
            ).async_get_health()
        except CannotConnectError, InvalidResponseError:
            return self.async_abort(reason="invalid_discovery")
        except Exception:
            _LOGGER.exception(
                "Unexpected exception while verifying discovered ha-paneld"
            )
            return self.async_abort(reason="unknown")
        if health.discovery_id != discovery_id:
            return self.async_abort(reason="invalid_discovery")
        for existing in self.hass.config_entries.async_entries(DOMAIN):
            if existing.data.get(CONF_ADDRESS) == address.stored_value:
                accept_health(self.hass, existing, health)
                return self.async_abort(reason="already_configured")
        known = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, discovery_id
        )
        if known is not None and not is_installation(known):
            return self.async_abort(reason="already_configured")
        # A known panel advertising from a new address has moved, when its
        # stored address has stopped answering and it is not connected. Its
        # health has just answered as it at the new address, so the stored one
        # is replaced; the running entry polls the new address without a
        # reload, which would end the panel's session for nothing. The identity
        # and the health line are both public on the LAN, so an advertisement
        # never redirects a panel whose stored address still answers, nor one
        # holding a session: that session is the credentialed authority on
        # where the panel is, repairs the address itself, and reports when it
        # cannot. One failed poll is then never enough to move a live panel.
        if self._entry_holds_its_address():
            self._abort_if_unique_id_configured()
        self._abort_if_unique_id_configured(
            updates={CONF_ADDRESS: address.stored_value}, reload_on_update=False
        )

        self._pending_address = address
        self._pending_health = health
        self._pending_discovery_id = discovery_id
        self._pending_friendly_name = _presentation_safe_name(
            discovery_info.properties.get("name")
        )
        # Without a title placeholder the card for every discovered panel falls back
        # to the bare integration name, so a fleet is a list of indistinguishable
        # duplicates. Prefer the human name the panel advertises, and fall back to the
        # panel id only when there is no usable name or the name is already taken.
        self._apply_discovery_title()
        return self._show_discovery_confirmation()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Any device on the network is the cue to offer panels other apps know.

        Without an entry nothing else runs this integration's code, and Core
        matches dhcp hostnames by their first character, hence the manifest's
        one matcher per character.
        """
        async_offer_migration_targets(self.hass)
        return self.async_abort(reason="not_panel")

    async def _async_discovered_adb(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Offer an Android device whose network ADB Android itself advertises."""
        advertised = discovery_info.ip_address
        host = f"[{advertised}]" if advertised.version == 6 else str(advertised)
        serial = discovery_info.name.removesuffix(f".{ADB_SERVICE_TYPE}")
        candidate = (
            adb_candidate(self.hass, host, serial.removeprefix("adb-"))
            if discovery_info.port == ADB_PORT
            else None
        )
        if candidate is None:
            return self.async_abort(reason="not_panel")
        try:
            await HaPaneldClient(
                async_get_clientsession(self.hass), normalize_address(host)
            ).async_get_health()
        except CannotConnectError, InvalidResponseError:
            return await self.async_step_integration_discovery(candidate)
        # The app already answers here; its own advertisement offers it.
        return self.async_abort(reason="not_panel")

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        """Offer a panel another integration knows, unless ignored or added."""
        await self.async_set_unique_id(discovery_info["key"])
        self._abort_if_unique_id_configured()
        # The registries and the ADB announcement can name one device twice.
        if offer_blocked(self.hass, discovery_info["address"], self.flow_id):
            return self.async_abort(reason="already_configured")
        target = await async_check_migration_target(discovery_info)
        if target is None:
            return self.async_abort(reason="not_panel")
        # Again after the probe: the other route may have shown its card while
        # this one waited. Nothing awaits between here and publishing ours.
        if offer_blocked(self.hass, discovery_info["address"], self.flow_id):
            return self.async_abort(reason="already_configured")
        self._migration_target = target
        self.context["title_placeholders"] = {
            "name": target.name,
            "address": target.address,
        }
        return await self.async_step_confirm_migration()

    async def async_step_ignore(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Ignore as Core does, keeping the card's address for the other route."""
        data = ignored_card_data(self.hass, user_input["unique_id"])
        await self.async_set_unique_id(user_input["unique_id"], raise_on_progress=False)
        return self.async_create_entry(title=user_input["title"], data=data)

    async def async_step_confirm_migration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the discovered address into the ordinary add-panel path."""
        target = self._migration_target
        assert target is not None
        if user_input is not None:
            return await self.async_step_add_panel({CONF_ADDRESS: target.address})
        return self.async_show_form(
            step_id="confirm_migration",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": _markdown_literal(target.name),
                "source": target.source,
                "address": target.address,
            },
        )

    async def async_step_confirm_discovery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-verify the discovered panel before creating its first entry."""
        if self._pending_address is None or self._pending_discovery_id is None:
            return self.async_abort(reason="unknown")
        if user_input is not None:
            try:
                health = await HaPaneldClient(
                    async_get_clientsession(self.hass), self._pending_address
                ).async_get_health()
            except CannotConnectError, InvalidResponseError:
                return self._show_discovery_confirmation({"base": "cannot_connect"})
            except Exception:
                _LOGGER.exception(
                    "Unexpected exception while re-verifying discovered ha-paneld"
                )
                return self._show_discovery_confirmation({"base": "unknown"})
            if health.discovery_id != self._pending_discovery_id:
                return self.async_abort(reason="invalid_discovery")
            # Adoption counts as Home Assistant starting this panel just as much
            # as installing it does, so a panel adopted mid-setup is handed the
            # address too rather than being asked for one Home Assistant knows.
            await async_offer_ha_url(
                self.hass,
                HaPaneldClient(
                    async_get_clientsession(self.hass), self._pending_address
                ),
            )
            return await self._async_create_panel_entry(self._pending_address, health)

        return self._show_discovery_confirmation()

    def _apply_discovery_title(self) -> None:
        """Title this flow, disambiguating both sides of a friendly-name clash."""
        assert self._pending_health is not None
        panel_id = self._pending_health.panel_id
        friendly = self._pending_friendly_name
        announced = self._announced_discovery_names()
        announced[self.flow_id] = (friendly, panel_id)

        if friendly is None:
            self._set_discovery_title(panel_id)
            return

        clashing = {
            flow_id: other_panel_id
            for flow_id, (other_friendly, other_panel_id) in announced.items()
            if flow_id != self.flow_id and other_friendly == friendly
        }
        taken = bool(clashing) or any(
            entry.title == friendly for entry in self._async_current_entries()
        )
        # A clash is symmetric. Qualifying only the newcomer would leave two cards
        # that still cannot be told apart, so qualify the ones already showing it.
        for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            other_panel_id = clashing.get(flow["flow_id"])
            if other_panel_id is not None:
                flow["context"]["title_placeholders"] = {
                    "name": _qualified_name(friendly, other_panel_id)
                }
        self._set_discovery_title(
            _qualified_name(friendly, panel_id) if taken else friendly
        )

    def _set_discovery_title(self, display: str) -> None:
        """Record the rendered title for the card, the form and the created entry."""
        self.context["title_placeholders"] = {"name": display}
        self._discovery_title = display

    def _announced_discovery_names(self) -> dict[str, tuple[str | None, str]]:
        """Names claimed by live discovery flows, pruned of any that finished.

        `ConfigFlowContext` is a closed TypedDict, so this cannot ride along in the
        flow context. Keying on flow id makes the store self-pruning.
        """
        store: dict[str, tuple[str | None, str]] = self.hass.data.setdefault(
            DOMAIN, {}
        ).setdefault(_DATA_DISCOVERY_NAMES, {})
        live = {
            flow["flow_id"]
            for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        }
        for flow_id in [f for f in store if f not in live and f != self.flow_id]:
            del store[flow_id]
        return store

    def _show_discovery_confirmation(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Show only verified, presentation-safe discovery details."""
        assert self._pending_address is not None
        assert self._pending_health is not None
        return self.async_show_form(
            step_id="confirm_discovery",
            data_schema=vol.Schema({}),
            description_placeholders={
                "address": self._pending_address.stored_value,
                "panel_name": _markdown_literal(
                    self._discovery_title or self._pending_health.panel_id
                ),
            },
            errors=errors,
        )

    def _show_add_panel_form(
        self,
        user_input: dict[str, Any] | None,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        """Ask only for the panel's address; what happens next depends on the panel."""
        schema = address_schema(self._migration_targets) or _DATA_SCHEMA
        return self.async_show_form(
            step_id="add_panel",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={
                "panel_access_url": help_url("panel-access"),
                "panel_help_url": help_url("panel-unreachable"),
            },
        )

    def _show_choose_version(
        self, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Offer the newest stable release first, then published test releases."""
        releases = self._install_releases or []
        options: list[SelectOptionDict] = [
            SelectOptionDict(
                value=(
                    _STABLE_CHOICE
                    if index == 0
                    and (not release["prerelease"] or prereleases_allowed())
                    else str(release["tag"])
                ),
                label=(
                    f"{release.get('name', release['tag'])} (recommended)"
                    if index == 0
                    and (not release["prerelease"] or prereleases_allowed())
                    else str(release.get("name", release["tag"]))
                ),
            )
            for index, release in enumerate(releases)
        ]
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

        A panel whose own setup wizard is unfinished is offered that wizard first;
        connecting anyway is the explicit way to skip it. A panel too old to
        report its setup state is treated as set up, as before.
        """
        assert self._pending_address is not None
        assert self._pending_health is not None
        try:
            complete = await self._found_client().async_get_setup_complete()
        except HaPaneldError:
            complete = True
        return self.async_show_menu(
            step_id="found_panel" if complete else "found_unconfigured",
            menu_options=(
                ["connect_found", "add_panel"]
                if complete
                else ["panel_setup", "connect_found", "add_panel"]
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
        if user_input is not None:
            return self.async_external_step_done(next_step_id="connect_found")
        # Before the browser goes to the wizard, tell the panel where Home
        # Assistant is, so the wizard has one less question to ask by the time it
        # renders. The panel verifies the address itself and shows the step as a
        # correction if it does not answer.
        await async_offer_ha_url(self.hass, self._found_client())
        if self._setup_watch is None or self._setup_watch.done():
            self._setup_watch = self.hass.async_create_background_task(
                self._async_watch_setup(),
                f"{DOMAIN} watch panel setup {self.flow_id}",
            )
        return self.async_external_step(
            step_id="panel_setup", url=self._found_client().setup_url
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
                with contextlib.suppress(UnknownFlow):
                    await self.hass.config_entries.flow.async_configure(
                        flow_id=self.flow_id, user_input={}
                    )
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
        self._pending_address = None
        self._pending_health = None
        self._pending_probe = None
        self._pending_probe_state = None
        self._pending_release = None
        self._pending_rc_tag = None
        self._pending_install_target = None
        self._pending_job_id = None

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
        try:
            address = normalize_address(user_input[CONF_ADDRESS])
            self._async_abort_entries_match({CONF_ADDRESS: address.stored_value})
        except InvalidAddressError:
            return self._show_add_panel_form(user_input, {"base": "invalid_address"})

        if address.port != DEFAULT_PORT:
            # Installation only uses the standard port, so another port can only
            # be a panel that already runs ha-paneld.
            return await self._async_check_running_panel(address, user_input)

        try:
            target = await async_pin_install_target(self.hass, address)
        except InstallNetworkError as err:
            return self._show_add_panel_form(
                user_input, {"base": _install_network_error(err)}
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while pinning install target")
            return self._show_add_panel_form(user_input, {"base": "unknown"})

        self._pending_address = address
        self._pending_install_target = target
        try:
            manager = await async_get_install_job_manager(self.hass)
            active = await manager.async_find_active(
                address.stored_value, target.pinned.stored_value
            )
        except InstallJobError:
            return self._show_add_panel_form(
                user_input, {"base": "install_receipt_error"}
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while loading an install receipt")
            return self._show_add_panel_form(user_input, {"base": "unknown"})
        if active is not None:
            # An install already started here resumes with its original release.
            self._pending_job_id = active.job_id
            if active.phase is InstallPhase.HEALTHY_UNCLAIMED:
                return await self.async_step_install_result()
            return await self._async_show_install_progress(active)

        client = HaPaneldClient(async_get_clientsession(self.hass), target.pinned)
        try:
            health = await client.async_get_health()
        except CannotConnectError, InvalidResponseError:
            pass
        except Exception:
            _LOGGER.exception(
                "Unexpected exception while checking for an existing ha-paneld"
            )
            return self._show_add_panel_form(user_input, {"base": "unknown"})
        else:
            self._pending_health = health
            return await self._async_show_found_panel()

        try:
            # The first probe deliberately has no key. Discovering an authorization
            # requirement must remain read-only; only the explicit authorization
            # step may create and offer HA's durable key.
            probe = await async_probe_install_target(target.pinned)
        except Exception:
            _LOGGER.exception("Unexpected exception while classifying install target")
            return self._show_add_panel_form(user_input, {"base": "unknown"})

        state = probe.state.value
        if state == "migration_candidate":
            # The old app is installed but not answering. Installing the new
            # app beside it would start the panel's own move, which Panel
            # Assistant's Repair replaces: the old app has to answer first.
            errors["base"] = "old_app_installed"
            return self._show_add_panel_form(user_input, errors)
        if state in ("install_candidate", "installed") and (
            _install_candidate_placeholders(probe) is None
        ):
            state = "retained_or_ambiguous"
        if state in (
            "adb_unauthorized",
            "install_candidate",
            "installed",
            "migration_candidate",
        ):
            self._pending_probe = probe if state != "adb_unauthorized" else None
            self._pending_probe_state = state
            return await self.async_step_choose_version()
        if state == "adb_unreachable":
            errors["base"] = (
                "adb_unreachable"
                if await _async_host_answers(target.pinned)
                else "panel_unreachable"
            )
        else:
            errors["base"] = {
                "retained_or_ambiguous": "retained_or_ambiguous",
                "incompatible": "incompatible",
            }.get(state, "unknown")
        return self._show_add_panel_form(user_input, errors)

    async def _async_check_running_panel(
        self, address: PanelAddress, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Find ha-paneld on a non-standard port, where nothing can be installed."""
        try:
            health = await HaPaneldClient(
                async_get_clientsession(self.hass), address
            ).async_get_health()
        except CannotConnectError, InvalidResponseError:
            return self._show_add_panel_form(user_input, {"base": "cannot_connect"})
        except Exception:
            _LOGGER.exception("Unexpected exception while validating ha-paneld")
            return self._show_add_panel_form(user_input, {"base": "unknown"})
        self._pending_address = address
        self._pending_health = health
        return await self._async_show_found_panel()

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
        if user_input is None:
            if self._install_releases is None or self._release_catalog_error:
                await self._async_load_install_releases()
            return self._show_choose_version()
        if self._release_catalog_error:
            # The person saw no versions, so this submit is "try again": load the
            # list afresh and let them choose from what is now offered.
            await self._async_load_install_releases()
            return self._show_choose_version()

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
            return self._show_authorize_adb()
        return self._show_install_candidate_preview()

    async def async_step_authorize_adb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for explicit approval of Home Assistant's ADB key on the panel."""
        if (
            self._pending_address is None
            or self._pending_install_target is None
            or self._pending_release is None
            or self._pending_release.descriptor is None
        ):
            return self.async_abort(reason="unknown")
        if user_input is None:
            return self._show_authorize_adb()

        try:
            target = await async_revalidate_install_target(
                self.hass, self._pending_install_target
            )
            signer = await async_get_adb_signer(self.hass)
            probe = await async_probe_install_target(
                target.pinned, signer, authorize=True
            )
        except InstallNetworkError as err:
            return self._show_authorize_adb({"base": _install_network_error(err)})
        except AdbCredentialError:
            return self._show_authorize_adb({"base": "adb_credential_error"})
        except Exception:
            _LOGGER.exception("Unexpected exception while retrying ADB authorization")
            return self._show_authorize_adb({"base": "unknown"})

        state = probe.state.value
        if state == "adb_unauthorized":
            return self._show_authorize_adb({"base": "adb_still_unauthorized"})
        if state == "migration_candidate":
            return self._show_authorize_adb({"base": "old_app_installed"})
        if state in ("install_candidate", "installed"):
            placeholders = _install_candidate_placeholders(probe)
            if placeholders is None:
                return self.async_abort(reason="unknown")
            self._pending_probe = probe
            self._pending_probe_state = state
            return self._show_install_candidate_preview()

        return self._show_authorize_adb(
            {
                "base": {
                    "adb_unreachable": "adb_unreachable",
                    "retained_or_ambiguous": "retained_or_ambiguous",
                    "incompatible": "incompatible",
                }.get(state, "unknown")
            }
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
                else ""
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

        # Entry identity is the normalized endpoint. This guard deliberately runs
        # before DNS, credentials, ADB, release, or durable job work.
        self._async_abort_entries_match(
            {CONF_ADDRESS: self._pending_address.stored_value}
        )
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
            return self._show_install_candidate_preview(
                {"base": _install_network_error(err)}
            )
        except AdbCredentialError:
            return self._show_install_candidate_preview(
                {"base": "adb_credential_error"}
            )
        except Exception:
            _LOGGER.exception("Unexpected exception while confirming installation")
            return self._show_install_candidate_preview({"base": "unknown"})

        if not _same_install_target(self._pending_probe, probe):
            return self._show_install_candidate_preview(
                {"base": "install_candidate_changed"}
            )

        if probe.state in {
            InstallTargetState.INSTALLED,
            InstallTargetState.MIGRATION_CANDIDATE,
        }:
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
            receipt, _created = await manager.async_create_or_join(
                plan.target,
                plan.artifact,
                plan.plan_sha256,
                plan.adb_credential_id,
            )
        except InstallJobConflictError:
            return self._show_install_candidate_preview(
                {"base": "install_job_conflict"}
            )
        except InstallPlanError:
            return self._show_install_candidate_preview(
                {"base": "install_plan_rejected"}
            )
        except InstallJobError:
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
            }
        )
        return self.async_show_form(
            step_id=(
                "confirm_install_rc"
                if self._pending_rc_tag is not None
                and self._pending_release is not None
                and "-" in self._pending_release.version
                else "confirm_install_candidate"
            ),
            data_schema=vol.Schema({}),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_confirm_install_rc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use the same frozen consent path with an explicitly translated RC warning."""
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
            return self.async_abort(reason="install_failed")

        if worker is None:
            try:
                manager = await async_get_install_job_manager(self.hass)
                refreshed = await manager.async_get(receipt.job_id)
            except InstallJobError:
                return self.async_abort(reason="install_receipt_error")
            except Exception:
                _LOGGER.exception("Unexpected exception while refreshing install job")
                return self.async_abort(reason="install_failed")
            if (
                refreshed.phase is InstallPhase.HEALTHY_UNCLAIMED
                or refreshed.is_terminal
            ):
                return await self.async_step_install_result()
            # The executor deliberately refuses to replay a worker cancelled in
            # this process. Registering an immediately completed waiter here would
            # create an unbounded progress callback loop.
            return self.async_abort(reason="install_worker_stopped")

        self._progress_waiter = self.hass.async_create_task(
            executor.async_wait(receipt.job_id),
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
        if waiter is not None and not waiter.done():
            return self.async_show_progress(
                step_id="install_progress",
                progress_action="installing",
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
            return self.async_abort(reason="install_failed")

        if receipt.phase in {
            InstallPhase.CANCELLED,
            InstallPhase.FAILED,
            InstallPhase.RECOVERY_REQUIRED,
        }:
            return self.async_abort(reason=_install_terminal_abort_reason(receipt))
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
        if self._address_is_configured(receipt.target.address):
            return self.async_abort(reason="already_configured")
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
            return self.async_abort(reason="install_recovery_required")
        if outcome is not FinalizationOutcome.VERIFIED or result.health is None:
            return self.async_abort(reason="install_receipt_error")
        if self._address_is_configured(receipt.target.address):
            executor.release_finalizer(receipt.job_id, self.flow_id)
            return self.async_abort(reason="already_configured")

        # The lease stays held through ConfigEntries' actual add.
        # async_on_create_entry hands HA's generated entry id to the executor,
        # which records it and releases the lease.
        return self.async_create_entry(
            title=result.health.panel_id,
            data={CONF_ADDRESS: receipt.target.address},
            options={
                **_NEW_PANEL_OPTIONS,
                **(
                    {CONF_PRERELEASE_PANEL_BUILDS: True}
                    if receipt.artifact.prerelease_opt_in
                    else {}
                ),
            },
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
        """Hand the executor HA's actual entry id for the healthy receipt."""
        job_id = self._finalizer_job_id
        executor = self._install_executor
        entry = result.get("result")
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

    def _entry_holds_its_address(self) -> bool:
        """Return whether an advertisement must leave this panel's address alone.

        It must while the stored address still answers, or while the panel is
        connected, whose own session then moves the address if it has moved.
        """
        if self.unique_id is None:
            return False
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, self.unique_id
        )
        coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
        return coordinator is not None and (
            bool(coordinator.reachable) or coordinator.connected
        )

    def _address_is_configured(self, address: str) -> bool:
        """Check the existing endpoint identity without contacting the panel."""
        return any(
            entry.data.get(CONF_ADDRESS) == address
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )

    async def _async_create_panel_entry(
        self, address: PanelAddress, health: PanelHealth
    ) -> ConfigFlowResult:
        """Create an entry, confirming the panel's account first when one asked."""
        for existing in self.hass.config_entries.async_entries(DOMAIN):
            if existing.data.get(CONF_ADDRESS) == address.stored_value:
                accept_health(self.hass, existing, health)
                return self.async_abort(reason="already_configured")
        # A panel that reports its identity is known by it. Adding a known panel
        # again, at the address it has moved to, repairs its entry's stored
        # address rather than creating a second entry for one panel: health at
        # the new address has just answered as it, and an administrator asked.
        if health.discovery_id is not None:
            # A discovery card for this panel may be pending; it must not
            # refuse the person who is adding the panel by hand, and Core
            # closes that card itself when the entry is created.
            known = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, health.discovery_id
            )
            if known is not None and not is_installation(known):
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(health.discovery_id, raise_on_progress=False)
            self._abort_if_unique_id_configured(
                updates={CONF_ADDRESS: address.stored_value}, reload_on_update=False
            )
        self._pending_address = address
        self._pending_health = health
        user = await self._async_asking_user()
        if user is None:
            # Nobody has asked to connect as this panel yet, which is the whole
            # of what could be confirmed here. The panel raises its own Repairs
            # issue when it asks, as it always has.
            return self._async_finish_panel_entry(address, health)
        self._pending_bind_user_id = user.id
        return await self.async_step_confirm_user()

    async def _async_asking_user(self) -> User | None:
        """Return the account this panel asked to connect as, if one may be shown.

        The account comes from the panel's own ``hello``, refused because no entry
        existed yet, so this is the same request the Repairs issue carries and the
        same administrator decision. An account that has since gone, been
        deactivated or turned out to be Home Assistant's own is not offered.
        """
        if self._pending_health is None:
            return None
        return await async_confirmable_user(
            self.hass,
            async_binding_request(self.hass, self._pending_health.discovery_id),
        )

    async def async_step_confirm_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the panel's own account for confirmation, and take either answer."""
        if self._pending_address is None or self._pending_health is None:
            return self.async_abort(reason="unknown")
        user = await self._async_asking_user()
        if user is None or user.id != self._pending_bind_user_id:
            # The account changed or stopped being one an administrator may
            # confirm while this form was open. Add the panel rather than bind
            # something the administrator was never shown.
            return self._async_finish_panel_entry(
                self._pending_address, self._pending_health
            )
        return self.async_show_menu(
            step_id="confirm_user",
            menu_options=["bind_user", "skip_binding"],
            description_placeholders={
                "panel": self._discovery_title or self._pending_health.panel_id,
                "user": user.name or "",
            },
        )

    async def async_step_bind_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the panel already confirmed for the account that asked."""
        if self._pending_address is None or self._pending_health is None:
            return self.async_abort(reason="unknown")
        # Checked again here: the account may have gone while the menu was open,
        # and this is the submission that binds it.
        user = await self._async_asking_user()
        if user is None or user.id != self._pending_bind_user_id:
            return await self.async_step_confirm_user()
        return self._async_finish_panel_entry(
            self._pending_address, self._pending_health, user.id
        )

    async def async_step_skip_binding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the panel without confirming the account that asked."""
        if self._pending_address is None or self._pending_health is None:
            return self.async_abort(reason="unknown")
        return self._async_finish_panel_entry(
            self._pending_address, self._pending_health
        )

    def _async_finish_panel_entry(
        self,
        address: PanelAddress,
        health: PanelHealth,
        user_id: str | None = None,
    ) -> ConfigFlowResult:
        """Create the entry, bound to a confirmed account or to none."""
        async_discard_binding_request(self.hass, health.discovery_id)
        data: dict[str, Any] = {CONF_ADDRESS: address.stored_value}
        if health.installation_identity:
            data[CONF_INSTALL_IDENTITY] = True
        if user_id is not None:
            data[CONF_TRANSPORT_USER_ID] = user_id
        # A discovered panel keeps the name its card promised. A manually added one
        # has no advertisement to read, so it stays on the panel id.
        return self.async_create_entry(
            title=self._discovery_title or health.panel_id,
            data=data,
            options=_NEW_PANEL_OPTIONS,
        )


_FRIENDLY_NAME_MAX_LENGTH = 64

# A panel added from now on talks to Home Assistant natively. Existing entries
# keep whatever they stored, so nobody is switched off MQTT unasked.
_NEW_PANEL_OPTIONS: dict[str, Any] = {CONF_AUTHORITY: AUTHORITY_NATIVE}


def _qualified_name(friendly: str, panel_id: str) -> str:
    """Show the panel id only when the friendly name alone is ambiguous."""
    return f"{friendly} ({panel_id})"


def _presentation_safe_name(value: object) -> str | None:
    """Accept an mDNS-advertised name only as bounded, printable, single-line text.

    The TXT record is writable by anything on the LAN, so it is presentation input
    and never an identity. Identity stays with the verified discovery id.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _FRIENDLY_NAME_MAX_LENGTH:
        return None
    if any(unicodedata.category(character).startswith("C") for character in name):
        return None
    if any(character.isspace() and character != " " for character in name):
        return None
    return name


def _markdown_literal(value: str) -> str:
    """Keep interpolated device text literal in HA's Markdown descriptions."""
    return "".join(
        f"&#{ord(character)};" if character in "\\`*_{}[]()<>!&:./@" else character
        for character in value
    )


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
        "model": _markdown_literal(probe.model),
        "serial": _markdown_literal(probe.serial),
        "abi": _markdown_literal(probe.primary_abi),
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
            InstallTargetState.MIGRATION_CANDIDATE,
        }
        and not (
            expected.state
            in {InstallTargetState.INSTALLED, InstallTargetState.MIGRATION_CANDIDATE}
            and observed.state is InstallTargetState.INSTALL_CANDIDATE
        )
        and _install_candidate_placeholders(observed) is not None
        and same_physical_target(observed, expected)
    )


async def _async_host_answers(address: PanelAddress) -> bool:
    """Whether anything at all answers at that address.

    It tells apart "the panel is there but its Android Debug Bridge is not" from
    "nothing is at that address", so each gets the advice that fits. A refused
    connection is an answer: something is listening on the host.
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
        return _RECOVERY_ABORT_REASONS.get(result_code, "install_recovery_required")
    return "install_failed"


ABORT_NATIVE_ENTITIES_DISABLED = "native_entities_disabled"


async def async_authorize_adb_form[ResultT: FlowResult[Any, Any]](
    flow: FlowHandler[Any, ResultT, Any],
    entry: ConfigEntry,
    panel: str,
    user_input: dict[str, Any] | None,
) -> ResultT | None:
    """Show the authorize_adb step, or authorize the panel and return None.

    The Options flow and both Repairs share this step; each finishes its own way.
    """
    error = None
    if user_input is not None:
        error = await async_authorize_existing_panel_adb(flow.hass, entry)
        if error is None:
            async_clear_adb_authorization(flow.hass, entry.entry_id)
            return None
    return flow.async_show_form(
        step_id="authorize_adb",
        data_schema=vol.Schema({}),
        description_placeholders={"panel": panel},
        errors={"base": error} if error else None,
    )


async def async_authorize_existing_panel_adb(
    hass: HomeAssistant, entry: ConfigEntry
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
        probe = await async_probe_install_target(target.pinned, signer, authorize=True)
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
    return {
        InstallTargetState.ADB_UNREACHABLE: "adb_unreachable",
        InstallTargetState.RETAINED_OR_AMBIGUOUS: "retained_or_ambiguous",
    }.get(probe.state, "panel_identity_changed")


#: The removal form's one field: the owner has read the risks and confirms.
CONF_REMOVAL_CONFIRMED = "confirmed"


class HaPaneldOptionsFlow(OptionsFlow):
    """Complete setup, choose transport authority, or configure panel updates.

    The authority choice exists only while native entities are turned on. Saving a
    change ends a live panel session and reloads the entry, whose setup moves
    the panel's MQTT entities to this integration under native and back under
    the others, so the panel is granted the new authority when it says hello
    again.
    """

    _setup_watch: asyncio.Task[None] | None = None
    _pending_bind_user_id: str | None = None
    _removal_risks_url: str | None = None

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
        """Offer this panel's setup, transport, updates and available ADB consent."""
        if self.context.get("source") == "onboarding":
            return await self.async_step_onboarding()
        options = ["transport", "updates"]
        if self._current_panel_health() is not None:
            options.append("authorize_adb")
        if CONF_TRANSPORT_USER_ID not in self.config_entry.data:
            options.insert(0, "onboarding")
        # Offered whether or not the app answers: a run cut short may already
        # have removed it, and the retry finishes over ADB.
        options.append("remove_app")
        return self.async_show_menu(step_id="init", menu_options=options)

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
        form = await async_authorize_adb_form(self, entry, entry.title, user_input)
        if form is not None:
            return form
        return self.async_create_entry(data=dict(entry.options))

    async def async_step_onboarding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the panel's own browser wizard obtain its own HA credentials."""
        if user_input is not None:
            return self.async_external_step_done(next_step_id="onboarding_confirm")
        client = self._onboarding_client()
        await async_offer_ha_url(self.hass, client)
        if self._setup_watch is None or self._setup_watch.done():
            self._setup_watch = self.hass.async_create_background_task(
                self._async_watch_onboarding(),
                f"{DOMAIN} watch install onboarding {self.flow_id}",
            )
        return self.async_external_step(step_id="onboarding", url=client.setup_url)

    async def _async_watch_onboarding(self) -> None:
        """Return to Add once setup and a verified panel request exist."""
        client = self._onboarding_client()
        deadline = asyncio.get_running_loop().time() + _SETUP_WATCH_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_SETUP_POLL_SECONDS)
            try:
                if not await client.async_get_setup_complete():
                    continue
                health = await client.async_get_health()
            except HaPaneldError:
                continue
            did = health.discovery_id
            if did is None or did != _panel_did(self.config_entry):
                continue
            if async_binding_request(self.hass, did) is None:
                continue
            with contextlib.suppress(UnknownFlow):
                await self.hass.config_entries.options.async_configure(
                    flow_id=self.flow_id, user_input={}
                )
            return

    async def _onboarding_user(self) -> User | None:
        """Resolve only a live request for this entry's verified panel."""
        try:
            if not await self._onboarding_client().async_get_setup_complete():
                return None
            health = await self._onboarding_client().async_get_health()
        except HaPaneldError:
            return None
        did = health.discovery_id
        if did is None or did != _panel_did(self.config_entry):
            return None
        return await async_confirmable_user(
            self.hass, async_binding_request(self.hass, did)
        )

    async def async_step_onboarding_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the requesting account before administrator confirmation."""
        user = await self._onboarding_user()
        if user is None:
            return self.async_show_form(
                step_id="onboarding_wait", data_schema=vol.Schema({})
            )
        self._pending_bind_user_id = user.id
        return self.async_show_menu(
            step_id="onboarding_confirm",
            menu_options=["onboarding_bind", "onboarding_skip"],
            description_placeholders={
                "panel": self.config_entry.title,
                "user": user.name or "",
            },
        )

    async def async_step_onboarding_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_onboarding_confirm()

    async def async_step_onboarding_bind(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind only the active account the administrator just saw."""
        user = await self._onboarding_user()
        if user is None or user.id != self._pending_bind_user_id:
            return await self.async_step_onboarding_confirm()
        async_bind_user(self.hass, self.config_entry, user.id)
        async_discard_binding_request(self.hass, _panel_did(self.config_entry))
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_onboarding_skip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Leave the entry unbound; Repairs remains available later."""
        return self.async_create_entry(data=dict(self.config_entry.options))

    def async_remove(self) -> None:
        if self._setup_watch is not None and not self._setup_watch.done():
            self._setup_watch.cancel()
        super().async_remove()

    async def async_step_remove_app(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove the app once the owner has seen the risks and confirmed."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_REMOVAL_CONFIRMED):
                errors["base"] = "removal_unconfirmed"
            else:
                try:
                    await async_remove_app(self.hass, self.config_entry)
                except RemovalError as err:
                    errors["base"] = f"removal_{err.reason}"
                else:
                    return await self.async_step_remove_app_done()
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
            data_schema=vol.Schema(
                {vol.Required(CONF_REMOVAL_CONFIRMED, default=False): BooleanSelector()}
            ),
            description_placeholders={
                "panel": self.config_entry.title,
                "risks_url": self._removal_risks_url,
            },
            errors=errors,
        )

    async def async_step_remove_app_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to forget the panel now that its app is gone."""
        return self.async_show_menu(
            step_id="remove_app_done",
            menu_options=["remove_app_delete_entry", "remove_app_keep_entry"],
            description_placeholders={"panel": self.config_entry.title},
        )

    async def async_step_remove_app_delete_entry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the panel, whose app is gone, once this flow has closed."""
        self.hass.async_create_task(
            self.hass.config_entries.async_remove(self.config_entry.entry_id)
        )
        return self.async_abort(reason="removed_entry_deleted")

    async def async_step_remove_app_keep_entry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="removed_entry_kept")

    async def async_step_transport(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show or save the authority."""
        if native_entities_turned_off(self.hass):
            return self.async_abort(reason=ABORT_NATIVE_ENTITIES_DISABLED)
        if user_input is not None:
            return self.async_create_entry(
                data=authority_options(self.config_entry, user_input[CONF_AUTHORITY])
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_AUTHORITY,
                    default=effective_authority(self.hass, self.config_entry),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(AUTHORITIES),
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_AUTHORITY,
                    )
                )
            }
        )
        return self.async_show_form(step_id="transport", data_schema=schema)

"""Panel-owned ha-paneld software update entity."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    AdbCredentialMissingError,
    async_get_adb_credential,
    async_get_durable_adb_credential,
)
from .adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
)
from .app_identity import LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, reports_package
from .build_feed import (
    BuildDownloadError,
    BuildFeed,
    BuildFeedError,
    FeedBuild,
    async_download_build,
    build_label,
    feed_release_artifact,
    parse_build_request,
)
from .client import (
    CannotConnectError,
    HaPaneldError,
    InvalidResponseError,
    NotABridgeError,
    StagingUnavailableError,
    UpdateApprovalRequiredError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
    _version_key,
    is_newer_stable_version,
    is_valid_discovery_id,
)
from .const import DOMAIN, update_unique_id
from .coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelCoordinatorEntity,
    PanelSnapshot,
)
from .device import panel_display_name
from .failure_repair import (
    async_clear_adb_authorization,
    async_clear_update_failure_if_installed,
    async_record_update_failure,
    async_refresh_update_failure_name,
)
from .feed_coordinator import (
    BuildFeedCoordinator,
    StableReleaseCoordinator,
    async_get_feed_coordinator,
    async_get_stable_release_coordinator,
    newest_feed_build,
)
from .install_adb import (
    async_launch_installed_app,
    async_preflight_install,
    async_update_installed_apk,
)
from .install_network import (
    InstallNetworkError,
    PinnedPanelTarget,
    async_pin_entry_target,
    async_revalidate_install_target,
)
from .native import NativeEntity, async_setup_native_platform
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup
from .panel_move import (
    async_evaluate_successor_move,
    claim_panel_operation,
    release_panel_operation,
)
from .provisioning import InstallTargetState, async_probe_install_target
from .release import (
    _RELEASE_SIGNER_CERTIFICATE_SHA256,
    ReleaseArtifact,
    is_feed_build_tag,
)
from .status import PanelCachedUpdate
from .update_coordinator import PanelUpdateCoordinator
from .update_errors import (
    _bridge_not_ready,
    _update_error,
    _UpdateRefusalError,
    _verification_error,
    adb_authorization_error,
    refusal_error,
    route_unavailable_reason,
)
from .update_panel_install import (
    _retry_unstarted_install,
    async_repair_update_permissions,
    async_request_panel_download,
)
from .update_policy import (
    build_allowed,
    feed_build_newer,
    prereleases_allowed,
    version_allowed,
)
from .update_progress import UpdateProgress
from .update_route_repair import async_reconcile_update_route_issue
from .upgrade_repairs import speaks_this_protocol

# A panel's refusal of a build its own order ranks lower (panelUpdateRefusal).
_DOWNGRADE_REFUSAL = "panel-app downgrades are not permitted"
_AWAITING_CONFIRMATION_SUMMARY = "Tap Install on the panel to finish the update."
AWAITING_CONFIRMATION_ATTRIBUTE = "awaiting_confirmation"
# The first ha-paneld release that can be backed up and sent an app over the LAN.
# Older panels cannot use this route to receive an authenticated replacement.
_FIRST_LAN_UPDATE_VERSION = "0.8.6"
# Which way the last update reached the panel, shown on the entity.
ROUTE_ATTRIBUTE = "update_route"
ROUTE_STAGED = "staged_by_home_assistant"
ROUTE_PANEL = "downloaded_by_panel"
ROUTE_ADB = "installed_by_home_assistant_adb"
ROUTE_UNAVAILABLE_ATTRIBUTE = "update_unavailable_reason"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the one panel-owned firmware update entity."""
    async_add_entities(
        [
            HaPaneldUpdateEntity(
                entry.entry_id,
                entry.runtime_data.coordinator,
                entry.runtime_data.update_coordinator,
                async_get_feed_coordinator(hass),
                title=entry.title,
                release=async_get_stable_release_coordinator(hass),
            )
        ]
    )
    # The ha-paneld update keeps its one entity above; native entities add only
    # other components' updates, read-only until commands travel natively.
    async_setup_native_platform(
        hass, entry, Platform.UPDATE, async_add_entities, NativeUpdate
    )


class HaPaneldUpdateEntity(PanelCoordinatorEntity, UpdateEntity):
    """Project a policy-admitted update through the panel's own transaction."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_has_entity_name = True
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_translation_key = "paneld_update"
    _attr_title = "Panel Assistant"

    def __init__(
        self,
        entry_id: str,
        coordinator: HaPaneldDataUpdateCoordinator,
        update_coordinator: PanelUpdateCoordinator,
        feed: BuildFeedCoordinator | None = None,
        title: str | None = None,
        release: StableReleaseCoordinator | None = None,
    ) -> None:
        """Bind update state to the existing config-entry and health authority."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._title = title
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})
        self._update_coordinator = update_coordinator
        # A signed build feed, when configured. Internal builds
        # share one version name, so they are told apart by build number.
        self._feed = feed
        # The compatible release as Home Assistant itself authenticated it,
        # so a panel without internet access is offered and sent it too.
        self._release = release
        self._installed_code: int | None = None
        self._code_key: tuple[str, str] | None = None
        self._code_task: asyncio.Task[None] | None = None
        if feed is not None:
            self._attr_supported_features |= UpdateEntityFeature.SPECIFIC_VERSION
        self._attr_unique_id = update_unique_id(entry_id)
        self._progress = UpdateProgress(
            entry_id,
            coordinator,
            update_coordinator,
            lambda: self.async_write_ha_state(),
            lambda: self._installed_code,
        )
        self._observer_task: asyncio.Task[None] | None = None
        self._recovery_started = False
        self._attr_extra_state_attributes = {}
        self._adb_ready_key: tuple[object, ...] | None = None
        self._legacy_api_ready_key: tuple[object, ...] | None = None
        self._route_checked_key: tuple[object, ...] | None = None
        self._route_task: asyncio.Task[None] | None = None

    async def async_added_to_hass(self) -> None:
        """Refresh presentation when the panel's local operation state changes."""
        await super().async_added_to_hass()
        async_refresh_update_failure_name(self.hass, self._entry_id)
        self.async_on_remove(
            self._update_coordinator.async_add_listener(
                self._handle_update_coordinator_update
            )
        )
        self.async_on_remove(self._cancel_observer)
        if self._feed is not None:
            self.async_on_remove(
                self._feed.async_add_listener(self._handle_offer_refresh)
            )
            self._refresh_installed_code()
        if self._release is not None:
            self.async_on_remove(
                self._release.async_add_listener(self._handle_offer_refresh)
            )
        self._resume_running_operation()
        self._schedule_route_refresh()
        self.async_on_remove(self._cancel_route_refresh)

    def _handle_coordinator_update(self) -> None:
        """Re-read the build number whenever the app on the panel changes."""
        self._progress.hold_through_restart(self.hass)
        self._refresh_installed_code()
        self._schedule_route_refresh()
        super()._handle_coordinator_update()

    def _handle_offer_refresh(self) -> None:
        self._route_checked_key = None
        self._schedule_route_refresh()
        self.async_write_ha_state()

    def _panel_name(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return (
            panel_display_name(self.hass, entry)
            if entry is not None
            else self._title or "This panel"
        )

    def _entry_discovery_id(self) -> str | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        unique_id = entry.unique_id if entry is not None else None
        return unique_id if unique_id and is_valid_discovery_id(unique_id) else None

    def _route_key(self) -> tuple[object, ...] | None:
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        artifact = self._adb_artifact()
        return (
            snapshot.health.panel_id,
            snapshot.health.package,
            snapshot.health.build,
            snapshot.health.discovery_id,
            self._entry_discovery_id(),
            getattr(self.coordinator.client, "address", None),
            self._api_capability(),
            artifact,
        )

    def _api_capability(self) -> str | None:
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is not None and not speaks_this_protocol(snapshot.health):
            # An app on an older protocol has no session here, and installs an
            # update for itself only with the policy a session hands it, so only
            # the authorized ADB route can update it.
            return "none"
        return (
            snapshot.status.install_capability if snapshot and snapshot.status else None
        )

    def _has_install_route(self) -> bool:
        if not self.coordinator.reachable:
            return False
        if self._api_capability() == "api":
            return (
                not self._bridge_handover_due()
                or self._legacy_api_ready_key == self._route_key()
            )
        if (
            self._api_capability() is None
            and self._legacy_api_ready_key is not None
            and self._legacy_api_ready_key == self._route_key()
        ):
            return True
        return (
            self._adb_ready_key is not None
            and self._adb_ready_key == self._route_key()
            and self._adb_artifact() is not None
        )

    def _adb_artifact(self) -> ReleaseArtifact | None:
        artifact = self._host_release()
        return artifact if artifact is not None and artifact.descriptor else None

    def _schedule_route_refresh(self) -> None:
        artifact = self._host_release()
        build = self._selected_feed_build(artifact)
        needs_verification = (
            build is not None
            and self._feed is not None
            and self._feed.verified_apk(build) is None
        )
        if (
            self._api_capability() == "api"
            and not self._bridge_handover_due()
            and not needs_verification
        ):
            self._adb_ready_key = None
            self._legacy_api_ready_key = None
            self._route_checked_key = None
            # A panel that can install for itself needs no route resolved, so this
            # returns before the refresh runs. Withdraw the issue here too, or a
            # panel that regains the privileged route keeps a repair saying it
            # cannot be updated.
            if self._route_observed():
                self._async_reconcile_route_issue(has_route=True)
            return
        # There is deliberately no "already checked this key" skip here. An
        # authorized ADB route is lost by something done on the panel -- a firmware
        # update or a reset switching developer options off -- and none of that
        # moves the key, which is identity, build, address and artifact. Trusting
        # the previous answer made losing the route undetectable until the panel's
        # build or address changed, which is the one case this check exists to
        # catch. Re-resolving costs an admission probe against a panel the
        # coordinator is already polling; the guard below still prevents overlap.
        if self._route_task is not None and not self._route_task.done():
            return
        self._route_task = self.hass.async_create_task(
            self._async_refresh_route(),
            f"check ha-paneld update route {self._entry_id}",
        )
        self._route_task.add_done_callback(self._route_refresh_finished)

    def _route_observed(self) -> bool:
        """Whether the last answer came from the panel rather than from its absence."""
        return self.coordinator.reachable and self.coordinator.data is not None

    def _async_reconcile_route_issue(self, *, has_route: bool) -> None:
        """Keep the repair in step with the route from every path that decides one."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        snapshot: PanelSnapshot | None = self.coordinator.data
        async_reconcile_update_route_issue(
            self.hass,
            entry,
            has_route=has_route,
            status=snapshot.status if snapshot else None,
        )

    def _route_refresh_finished(self, task: asyncio.Task[None]) -> None:
        if self._route_task is task:
            self._route_task = None
            if task.cancelled() or task.exception() is not None:
                # The key stays unchecked, so checking again at once would fail
                # the same way without end; the next poll checks again.
                return
            if self._route_checked_key != self._route_key():
                self._schedule_route_refresh()

    def _cancel_route_refresh(self) -> None:
        task = self._route_task
        self._route_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _async_refresh_route(self) -> None:
        key = self._route_key()
        error: Exception | None = None
        try:
            build = self._selected_feed_build(self._host_release())
            if build is not None and self._feed is not None:
                await self._feed.async_verify_build(build)
            route, _target, _credential = await self._async_install_route()
        except Exception as err:
            _LOGGER.debug(
                "Could not check update route for %s: %s", self._entry_id, err
            )
            route = None
            error = err
        # Admission can fail because no newer signed build is available. That
        # says nothing about the panel's physical route: observe its current app
        # passively, without generating a key or requesting new authorization.
        has_route: bool | None = route is not None
        if not has_route and self._route_observed():
            has_route = await self._async_observe_update_route()
        if key == self._route_key():
            self._route_checked_key = key
            self._adb_ready_key = key if route == ROUTE_ADB else None
            self._legacy_api_ready_key = key if route == ROUTE_PANEL else None
            attributes = dict(self._attr_extra_state_attributes)
            # A bridge that cannot hand over yet has nothing newer to install and
            # loses nothing, so it reads as up to date rather than as a problem.
            if not self._has_install_route() and not _bridge_not_ready(error):
                attributes[ROUTE_UNAVAILABLE_ATTRIBUTE] = route_unavailable_reason(
                    error, has_adb_artifact=self._adb_artifact() is not None
                )
            else:
                attributes.pop(ROUTE_UNAVAILABLE_ATTRIBUTE, None)
            if self._route_observed() and has_route is not None:
                self._async_reconcile_route_issue(has_route=has_route)
            self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    async def _async_install_route(
        self,
    ) -> tuple[str | None, AdbInstallTarget | None, AdbCredential | None]:
        """Choose from the panel's live install route, then authorized ADB."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None or not self.coordinator.reachable:
            return None, None, None
        if self._bridge_handover_due():
            bridge = self._host_release()
            successor = (
                self._release.artifact_for(
                    SUCCESSOR_PACKAGE_ID,
                    allow_prerelease=self._allow_prerelease(),
                    tag=bridge.tag,
                )
                if self._release and bridge is not None
                else None
            )
            if (
                bridge is None
                or successor is None
                or successor.tag != bridge.tag
                or successor.descriptor is None
            ):
                raise _update_error(
                    "release_pair_unavailable",
                    "The matching new-app release is unavailable",
                )
            await self._async_check_successor(successor, snapshot)
        capability = self._api_capability()
        if capability is None:
            try:
                # Older panels exported this exact privileged-route observation
                # as the dashboard's `shot` bit before the typed status field.
                if await self.coordinator.client.async_get_legacy_install_capability():
                    return ROUTE_PANEL, None, None
            except HaPaneldError:
                pass
        elif capability == "api":
            return ROUTE_PANEL, None, None
        adb = await self._async_adb_route()
        if adb is None:
            return None, None, None
        return ROUTE_ADB, *adb

    async def _async_adb_route(
        self,
    ) -> tuple[AdbInstallTarget, AdbCredential] | None:
        """Admit the authorized ADB route, whatever route the panel offers itself."""
        artifact = self._adb_artifact()
        if artifact is None or artifact.descriptor is None:
            return None
        try:
            try:
                credential = await async_get_durable_adb_credential(self.hass)
            except AdbCredentialMissingError:
                credential = None
            admitted = await self._async_admit_adb_target(artifact, credential)
            if admitted is None:
                return None
            target, credential, _root_mode = admitted
        except InstallAdbError as err:
            if err.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED:
                raise adb_authorization_error(
                    self.hass, self._entry_id, self._panel_name()
                ) from err
            return None
        except (
            AdbCredentialError,
            InstallNetworkError,
            HaPaneldError,
            OSError,
        ):
            return None
        async_clear_adb_authorization(self.hass, self._entry_id)
        return target, credential

    async def _async_pin_adb_target(self) -> PinnedPanelTarget | None:
        """Bind passive ADB evidence to the configured panel's pinned HTTP identity."""
        snapshot = self.coordinator.data
        if snapshot is None or not self.coordinator.reachable:
            return None
        return await async_pin_entry_target(
            self.hass,
            self.coordinator.client.address,
            snapshot.health,
            self._entry_discovery_id(),
        )

    async def _async_probe_adb_target(
        self, pinned: PinnedPanelTarget, credential: AdbCredential | None
    ) -> AdbInstallTarget | None:
        """Observe the current installed app without offering an authorization key."""
        probe = (
            await async_probe_install_target(pinned.pinned)
            if credential is None
            else await async_probe_install_target(pinned.pinned, credential.signer)
        )
        if probe.state is InstallTargetState.ADB_UNREACHABLE:
            raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
        if probe.state is InstallTargetState.ADB_UNAUTHORIZED:
            raise InstallAdbError(InstallAdbErrorCode.AUTHORIZATION_REQUIRED)
        if probe.state not in {
            InstallTargetState.INSTALLED,
            InstallTargetState.MIGRATION_CANDIDATE,
        }:
            return None
        return probe.adb_target(pinned.pinned)

    async def _async_observe_update_route(self) -> bool | None:
        """Check route recovery independently of a pending signed update."""
        try:
            capability = self._api_capability()
            if capability == "api":
                return True
            if capability is None:
                try:
                    client = self.coordinator.client
                    if await client.async_get_legacy_install_capability():
                        return True
                except HaPaneldError:
                    pass
            try:
                credential = await async_get_durable_adb_credential(self.hass)
            except AdbCredentialMissingError:
                credential = None
            pinned = await self._async_pin_adb_target()
            if pinned is None:
                return None
            target = await self._async_probe_adb_target(pinned, credential)
            if target is None:
                return None
            await async_revalidate_install_target(self.hass, pinned)
            return True
        except InstallAdbError:
            return False
        except (
            AdbCredentialError,
            InstallNetworkError,
            HaPaneldError,
            OSError,
        ):
            return None

    async def _async_admit_adb_target(
        self, artifact: ReleaseArtifact, credential: AdbCredential | None
    ) -> tuple[AdbInstallTarget, AdbCredential, AdbRootMode] | None:
        """Bind an ADB target to the live HTTP identity and exact signed artifact."""
        snapshot = self.coordinator.data
        if (
            snapshot is None
            or not self.coordinator.reachable
            or artifact.descriptor is None
        ):
            return None
        pinned = await self._async_pin_adb_target()
        if pinned is None:
            _LOGGER.debug(
                "ADB for %s refused: the panel at its address is not this entry's",
                self._panel_name(),
            )
            return None
        if credential is None:
            # Only an already-open peer permits install admission to create a key.
            if await self._async_probe_adb_target(pinned, None) is None:
                return None
            await async_get_adb_credential(self.hass)
            credential = await async_get_durable_adb_credential(self.hass)
        target = await self._async_probe_adb_target(pinned, credential)
        if target is None:
            _LOGGER.debug(
                "ADB for %s refused: no installed app found", self._panel_name()
            )
            return None
        admitted = await async_preflight_install(
            target,
            credential.signer,
            artifact.descriptor,
            admit_installed_target=True,
        )
        if not admitted.target_installed:
            return None
        await async_revalidate_install_target(self.hass, pinned)
        return target, credential, admitted.root_mode

    def _refresh_installed_code(self) -> None:
        """Read the running build number once per install, never per poll."""
        if not self.coordinator.reachable:
            return
        health = self.coordinator.data.health
        if health.version_code is not None:
            self._installed_code = health.version_code
            return
        if self._feed is None:
            return
        key = (health.version, health.build)
        if key == self._code_key or (
            self._code_task is not None and not self._code_task.done()
        ):
            return
        self._code_task = self.hass.async_create_task(
            self._async_read_installed_code(key),
            f"read ha-paneld build {self._entry_id}",
        )

    async def _async_read_installed_code(self, key: tuple[str, str]) -> None:
        try:
            name, code = await self.coordinator.client.async_get_version_code()
        except HaPaneldError:
            return
        # Record the key either way: a disagreeing name is retried only when the
        # panel's app changes again, never on every poll.
        self._code_key = key
        self._installed_code = code if name == key[0] else None
        self._schedule_route_refresh()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """A manual refresh also re-reads the build feed, so a build just
        published can be installed straight away."""
        await super().async_update()
        await self._async_refresh_route()
        if self._feed is not None:
            await self._feed.async_refresh()
        if self._release is not None:
            await self._release.async_refresh()

    def _feed_mode(self) -> BuildFeed | None:
        """Use feed delivery only when the chosen artifact comes from the feed."""
        artifact = self._host_release()
        return (
            self._feed.data
            if self._feed is not None
            and artifact is not None
            and is_feed_build_tag(artifact.tag)
            else None
        )

    def _selected_feed_build(
        self, artifact: ReleaseArtifact | None
    ) -> FeedBuild | None:
        """Find the exact signed feed record chosen for this panel."""
        if (
            artifact is None
            or not is_feed_build_tag(artifact.tag)
            or self._feed is None
            or self._feed.data is None
        ):
            return None
        return next(
            (
                build
                for build in self._feed.data.builds
                if feed_release_artifact(build) == artifact
            ),
            None,
        )

    def _feed_package(self) -> str | None:
        """Choose the package the panel actually runs, including old reports."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.health.package or LEGACY_PACKAGE_ID

    def _awaiting_confirmation(self) -> bool:
        """The panel's own installer waits for a tap on Install."""
        snapshot = self._update_coordinator.data
        operation = snapshot.operation if snapshot is not None else None
        # Either route can wait: the panel's own download runs as "ha-paneld",
        # an update Home Assistant sent runs as the uploaded "APK".
        return (
            operation is not None
            and operation.running
            and operation.awaiting_confirmation
        )

    @property
    def release_summary(self) -> str | None:
        """Tell the owner to finish an update waiting on the panel's prompt."""
        if self._awaiting_confirmation():
            return _AWAITING_CONFIRMATION_SUMMARY
        return super().release_summary

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Route facts, plus the panel's wait for a tap when there is one."""
        attributes = super().extra_state_attributes
        if not self._awaiting_confirmation():
            return None if attributes is None else dict(attributes)
        return {**(attributes or {}), AWAITING_CONFIRMATION_ATTRIBUTE: True}

    def _handle_update_coordinator_update(self) -> None:
        """Keep a recovered operation latched across transient status samples."""
        self._resume_running_operation()
        self.async_write_ha_state()

    def _resume_running_operation(self) -> None:
        """Resume one bounded observer for panel-owned work found after reload."""
        operation = self._update_coordinator.data.operation
        if (
            self._recovery_started
            or (self._observer_task is not None and not self._observer_task.done())
            or operation is None
            or not operation.running
            or operation.component != "ha-paneld"
        ):
            return
        self._recovery_started = True
        self._start_observer(None, recovered=True)
        # Found running after a reload: the panel already took the update.
        self._progress.enter("installing")

    def _start_observer(
        self, expected_version: str | None, *, recovered: bool = False
    ) -> asyncio.Task[None]:
        """Start or reuse the entity's sole bounded health observer."""
        if self._observer_task is not None and not self._observer_task.done():
            return self._observer_task
        self._progress.begin(self.hass, self.installed_version, self.latest_version)
        task = self.hass.async_create_task(
            self._run_observer(expected_version, recovered=recovered),
            f"observe ha-paneld update {self._entry_id}",
        )
        self._observer_task = task
        task.add_done_callback(self._observer_finished)
        return task

    async def _run_observer(
        self,
        expected_version: str | None,
        *,
        recovered: bool,
    ) -> None:
        """Observe one target and release its latch before awaiters resume."""
        starting_health = (
            self.coordinator.data.health
            if recovered and self.coordinator.data
            else None
        )
        try:
            verified = await self._progress.async_wait_for_installed_version(
                self.hass, expected_version
            )
        except Exception as err:
            if recovered:
                await async_record_update_failure(
                    self.hass,
                    self._entry_id,
                    self._panel_name(),
                    expected_version,
                    err,
                    observed_before=(
                        starting_health.version,
                        starting_health.version_code,
                    )
                    if starting_health is not None
                    else None,
                )
            raise
        else:
            if recovered and verified:
                health = self.coordinator.data.health
                await async_clear_update_failure_if_installed(
                    self.hass,
                    self._entry_id,
                    health.version,
                    health.version_code,
                    verified_success=True,
                )
        finally:
            self._progress.end()
            self._recovery_started = False
            if asyncio.current_task() is self._observer_task:
                self._observer_task = None
            self.async_write_ha_state()

    def _observer_finished(self, task: asyncio.Task[None]) -> None:
        """Release the latch only when its observer exits or is cancelled."""
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.warning("ha-paneld update observer stopped: %s", error)

    def _cancel_observer(self) -> None:
        """Stop polling when Home Assistant unloads the entity."""
        task = self._observer_task
        self._observer_task = None
        self._progress.end()
        self._recovery_started = False
        if task is not None and not task.done():
            task.cancel()

    @property
    def available(self) -> bool:
        """Stay available while the panel restarts for its own update."""
        return super().available or self._progress.restarting

    @property
    def update_percentage(self) -> int | None:
        """Report how far the current update has got, by time alone."""
        return self._progress.percentage

    @property
    def in_progress(self) -> bool:
        """Retain local work and recover panel-owned work after an HA restart."""
        operation = self._update_coordinator.data.operation
        return (
            (self._observer_task is not None and not self._observer_task.done())
            or self._progress.attempt is not None
            or (
                operation is not None
                and operation.running
                and operation.component == "ha-paneld"
            )
        )

    @property
    def installed_version(self) -> str | None:
        """Return the version shown: the starting one until an update ends."""
        if (attempt := self._progress.attempt) is not None:
            return attempt.installed
        return self._running_version()

    def _running_version(self) -> str | None:
        """Return the current version from the health authority, once read."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        version = snapshot.health.version
        artifact = self._host_release()
        release = artifact
        if (
            release is not None
            and release.descriptor is None
            and release.version == version
        ):
            # Core hides equal versions before asking version_is_newer. Name
            # the installed bridge so its unfinished handover stays visible.
            return f"{version} (bridge)"
        code = snapshot.health.version_code or self._installed_code
        if code is not None and (
            (
                self._feed is not None
                and self._feed.last_update_success
                and self._feed.data is not None
            )
            or (
                artifact is not None
                and artifact.descriptor is not None
                and artifact.version == version
            )
        ):
            return build_label(version, code)
        return version

    def _allow_prerelease(self) -> bool:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return prereleases_allowed(entry)

    def _artifact_allowed(self, artifact: ReleaseArtifact) -> bool:
        """Recheck policy and prevent a post-1.0 downgrade before delivery."""
        if not build_allowed(
            artifact.version,
            artifact.protocol_min,
            artifact.protocol_max,
            allow_prerelease=self._allow_prerelease(),
        ):
            return False
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return False
        return version_allowed(
            artifact.version,
            artifact.descriptor.version_code
            if artifact.descriptor is not None
            else None,
            snapshot.health.version,
            snapshot.health.version_code
            if snapshot.health.version_code is not None
            else self._installed_code,
            feed=is_feed_build_tag(artifact.tag),
        )

    async def _async_admit_artifact(self, artifact: ReleaseArtifact) -> None:
        """Check current consent for this exact candidate before a fresh command."""
        if not self._artifact_allowed(artifact):
            raise _update_error(
                "update_unavailable", "The requested update is unavailable"
            )

    def _host_release(self) -> ReleaseArtifact | None:
        """Choose the newest eligible artifact across the authenticated sources."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None or is_newer_stable_version(
            _FIRST_LAN_UPDATE_VERSION, snapshot.health.version
        ):
            return None
        package_id = snapshot.health.package or LEGACY_PACKAGE_ID
        installed_key = _version_key(snapshot.health.version)
        if installed_key is None:
            return None
        installed_code = snapshot.health.version_code or self._installed_code
        eligible = []
        if (
            self._feed is not None
            and self._feed.last_update_success
            and self._feed.data
        ):
            # The feed's own order: versionCode first, whatever the name.
            feed_offer = newest_feed_build(
                build
                for build in self._feed.data.builds
                if build.package_id == package_id
                and feed_build_newer(
                    build.version_name,
                    build.version_code,
                    snapshot.health.version,
                    installed_code,
                )
                and self._artifact_allowed(feed_release_artifact(build))
            )
            if feed_offer is not None:
                eligible.append(feed_release_artifact(feed_offer))
        candidates = (
            self._release.candidates_for(package_id)
            if self._release is not None
            else ()
        )
        for artifact in candidates:
            if not self._artifact_allowed(artifact):
                continue
            code = artifact.descriptor.version_code if artifact.descriptor else None
            candidate_key = _version_key(artifact.version)
            if candidate_key is None:
                continue
            if (
                candidate_key > installed_key
                or (
                    artifact.version == snapshot.health.version
                    and code is not None
                    and installed_code is not None
                    and code > installed_code
                )
                # An installed bridge still owes its one-way handover.
                or (
                    artifact.descriptor is None
                    and artifact.version == snapshot.health.version
                )
            ):
                eligible.append(artifact)
        return max(
            eligible,
            key=lambda artifact: (
                _version_key(artifact.version),
                artifact.descriptor.version_code if artifact.descriptor else 0,
            ),
            default=None,
        )

    def _offer_label(self, artifact: ReleaseArtifact) -> str:
        """Keep same-version rebuilds distinguishable in Home Assistant."""
        if artifact.descriptor is not None and (
            is_feed_build_tag(artifact.tag)
            or artifact.version == self.coordinator.data.health.version
        ):
            return build_label(artifact.version, artifact.descriptor.version_code)
        return artifact.version

    def _bridge_handover_due(self) -> bool:
        snapshot: PanelSnapshot | None = self.coordinator.data
        release = self._host_release()
        return bool(
            snapshot is not None
            and self._feed_mode() is None
            and release is not None
            and release.descriptor is None
            and snapshot.health.version == release.version
            and snapshot.health.installation_identity
            and reports_package(snapshot.health.package, LEGACY_PACKAGE_ID)
        )

    async def _async_check_successor(
        self, artifact: ReleaseArtifact, snapshot: PanelSnapshot
    ) -> int | None:
        """Use the panel's handover answer for both offer and install admission."""
        descriptor = artifact.descriptor
        if descriptor is None or descriptor.package_id != SUCCESSOR_PACKAGE_ID:
            raise _update_error(
                "release_pair_unavailable",
                "The matching new-app release is unavailable",
            )
        if (
            not snapshot.health.installation_identity
            or snapshot.health.version != artifact.version
        ):
            raise _update_error(
                "bridge_details_changed", "The panel's handover details changed"
            )
        try:
            capability = await self.coordinator.client.async_get_successor_capability()
        except NotABridgeError as err:
            raise _update_error(
                "bridge_not_ready",
                "The panel cannot move to the new app yet; "
                "it is offered again once it can",
            ) from err
        except HaPaneldError as err:
            raise _update_error(
                "bridge_capability_unavailable",
                "The panel's handover capability could not be checked",
            ) from err
        if capability[3]:
            raise _update_error(
                "successor_untrusted",
                "The installed new app is not trusted for handover",
            )
        if capability[:2] != (descriptor.package_id, artifact.version):
            raise _update_error(
                "bridge_details_changed", "The panel's handover details changed"
            )
        return capability[2]

    def _stable_target(self) -> PanelCachedUpdate | None:
        """Project only the host's authenticated compatible release."""
        release = self._host_release()
        if release is None:
            return None
        return PanelCachedUpdate(
            self.coordinator.data.health.version,
            self._offer_label(release),
            release.tag,
        )

    @property
    def latest_version(self) -> str | None:
        """Report the installed version when no newer admitted target exists."""
        if (attempt := self._progress.attempt) is not None:
            return attempt.latest
        if not self._has_install_route():
            return self.installed_version
        artifact = self._host_release()
        if artifact is None:
            return self.installed_version
        build = self._selected_feed_build(artifact)
        if (
            build is not None
            and self._feed is not None
            and self._feed.verified_apk(build) is None
        ):
            return self.installed_version
        return self._offer_label(artifact)

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare semantic version first and build number only as a tie-break.

        A signed feed build offer compares in the feed's own order instead,
        versionCode first.
        """
        release = self._host_release()
        if (
            release is not None
            and release.descriptor is None
            and installed_version == f"{release.version} (bridge)"
        ):
            return latest_version == release.version
        latest_name = latest_version.partition(" build ")[0]
        installed_name = installed_version.partition(" build ")[0]
        latest_key = _version_key(latest_name)
        installed_key = _version_key(installed_name)
        if latest_key is None or installed_key is None:
            return False
        if (
            release is not None
            and is_feed_build_tag(release.tag)
            and latest_version == self._offer_label(release)
        ):
            return feed_build_newer(
                latest_name,
                parse_build_request(latest_version),
                installed_name,
                parse_build_request(installed_version),
            )
        if latest_key != installed_key:
            return latest_key > installed_key
        latest_code = parse_build_request(latest_version)
        installed_code = parse_build_request(installed_version)
        return (
            latest_code is not None
            and installed_code is not None
            and latest_code > installed_code
        )

    async def async_install(
        self, version: str | None, backup: bool, **_kwargs: object
    ) -> None:
        """Install while no move to the new app runs on the same panel."""
        if not claim_panel_operation(self.hass, self._entry_id):
            raise _update_error(
                "update_busy", "The panel is busy with another operation"
            )
        try:
            await self._async_install_claimed(version, backup)
        finally:
            release_panel_operation(self.hass, self._entry_id)

    async def _async_install_claimed(self, version: str | None, backup: bool) -> None:
        """Start one exact admitted offer, then follow the expected panel restart."""
        if self.coordinator.identity_mismatch:
            raise _update_error("update_unavailable", "The panel identity has changed")
        try:
            route, adb_target, adb_credential = await self._async_install_route()
        except HomeAssistantError as route_error:
            if route_error.translation_key == "adb_authorization_required":
                artifact = self._adb_artifact()
                failed_target = version
                if failed_target is None and artifact is not None:
                    failed_target = (
                        build_label(artifact.version, artifact.descriptor.version_code)
                        if self._feed_mode() is not None and artifact.descriptor
                        else artifact.version
                    )
                await async_record_update_failure(
                    self.hass,
                    self._entry_id,
                    self._panel_name(),
                    failed_target,
                    route_error,
                )
            raise
        if route is None:
            error = _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
            if self.latest_version != self.installed_version:
                await async_record_update_failure(
                    self.hass,
                    self._entry_id,
                    self._panel_name(),
                    self.latest_version,
                    error,
                )
            raise error
        chosen_artifact = self._host_release()
        feed = self._feed_mode()
        if (
            version is not None
            and parse_build_request(version) is not None
            and self._feed is not None
            and (
                chosen_artifact is None or version != self._offer_label(chosen_artifact)
            )
        ):
            feed = self._feed.data
        failure_artifact: dict[str, object] | None = None
        if feed is not None:
            build = await self._async_select_feed_build(feed, version, backup)
            target_version = build.label
            selected_artifact = feed_release_artifact(build)
            repair_artifact: ReleaseArtifact | None = selected_artifact
            verified_apk = self._feed.verified_apk(build) if self._feed else None
            failure_artifact = asdict(selected_artifact)
            if route == ROUTE_ADB and selected_artifact.descriptor is None:
                raise _update_error(
                    "update_unavailable",
                    "The requested Panel Assistant update is unavailable",
                )
        else:
            offer = self._stable_target()
            if route == ROUTE_ADB:
                release = self._adb_artifact()
                if release is not None:
                    offer = PanelCachedUpdate(
                        self.coordinator.data.health.version,
                        self._offer_label(release),
                        release.tag,
                    )
            if (
                self.in_progress
                or backup
                or offer is None
                or version not in (None, offer.target_version)
            ):
                raise _update_error(
                    "update_unavailable",
                    "The requested Panel Assistant update is unavailable",
                )
            target_version = offer.target_version
            release = self._host_release()
            repair_artifact = release
        self._progress.begin(self.hass, self.installed_version, self.latest_version)
        self.async_write_ha_state()
        try:
            if feed is not None:
                if route == ROUTE_ADB:
                    assert adb_target is not None and adb_credential is not None
                    await self._async_deliver_adb(
                        selected_artifact, adb_target, adb_credential, apk=verified_apk
                    )
                else:
                    await self._async_deliver_build(selected_artifact, apk=verified_apk)
            else:
                assert offer is not None
                if route == ROUTE_ADB:
                    assert release is not None
                    assert adb_target is not None and adb_credential is not None
                    await self._async_deliver_adb(release, adb_target, adb_credential)
                    await async_clear_update_failure_if_installed(
                        self.hass,
                        self._entry_id,
                        target_version,
                        None,
                        verified_success=True,
                    )
                    return
                # Home Assistant sends the release over the LAN whenever it could
                # authenticate it. Only a panel that cannot take an upload at all
                # downloads the release itself, and verifies it itself.
                if release is not None and release.tag == offer.tag:
                    if release.descriptor is None:
                        # Keep the signed pair selected for this transaction even
                        # if the shared release coordinator refreshes mid-install.
                        successor = (
                            self._release.artifact_for(
                                SUCCESSOR_PACKAGE_ID,
                                allow_prerelease=self._allow_prerelease(),
                                tag=release.tag,
                            )
                            if self._release
                            else None
                        )
                        if (
                            successor is None
                            or successor.tag != release.tag
                            or successor.descriptor is None
                            or successor.descriptor.package_id != SUCCESSOR_PACKAGE_ID
                        ):
                            raise _update_error(
                                "release_pair_unavailable",
                                "The matching new-app release is unavailable",
                            )
                        repair_artifact = successor
                        bridge_delivered = False
                        if (
                            self.coordinator.data.health.version != release.version
                            or not self.coordinator.data.health.installation_identity
                        ):
                            await self._async_deliver_build(release)
                            bridge_delivered = True
                            self._progress.next_delivery()
                        if reports_package(
                            self.coordinator.data.health.package, SUCCESSOR_PACKAGE_ID
                        ):
                            # A connected bridge may already have completed its
                            # own handover. Prove that result without reinstalling.
                            self._progress.enter("installing")
                            await self._async_wait_for_build(
                                successor, ("", LEGACY_PACKAGE_ID)
                            )
                        else:
                            # Panel Assistant moves the panel to the new app
                            # itself, through its Repair; the panel's own
                            # handover is never started, so it cannot race it.
                            entry = self.hass.config_entries.async_get_entry(
                                self._entry_id
                            )
                            if entry is not None:
                                async_evaluate_successor_move(self.hass, entry)
                            if not bridge_delivered:
                                raise _update_error(
                                    "move_with_repair",
                                    "Move this panel to the new app with its "
                                    "Repair in Settings",
                                )
                    elif not await self._async_deliver_build(release, fallback=True):
                        await self._async_start_panel_download(offer, release)
                else:
                    await self._async_start_panel_download(offer, release)
        except UpdateRejectedError as refusal:
            error = refusal_error(self._panel_name(), refusal)
            await async_record_update_failure(
                self.hass,
                self._entry_id,
                self._panel_name(),
                target_version,
                error,
                artifact=failure_artifact,
            )
            raise error from None
        except Exception as err:
            if not _bridge_not_ready(err):
                await async_record_update_failure(
                    self.hass,
                    self._entry_id,
                    self._panel_name(),
                    target_version,
                    err,
                    artifact=failure_artifact,
                )
            raise
        else:
            owner = self.hass.config_entries.async_get_entry(self._entry_id)
            if (
                route != ROUTE_ADB
                and repair_artifact is not None
                and owner is not None
                # Only a loaded entry that still runs this entity's coordinator
                # may own the task. An unload marks the entry unloading before
                # it awaits anything and drops its runtime data at the end; a
                # reload brings another coordinator. This check and the
                # scheduling below share one event-loop step, so a task created
                # here is always among those the next unload cancels.
                and owner.state is ConfigEntryState.LOADED
                and getattr(getattr(owner, "runtime_data", None), "coordinator", None)
                is self.coordinator
            ):
                # Not awaited: the panel already runs the update, and its ADB
                # may take minutes to answer again. The entry owns the task,
                # so unloading or removing the panel ends the repair.
                owner.async_create_background_task(
                    self.hass,
                    async_repair_update_permissions(
                        self.hass,
                        self.coordinator,
                        repair_artifact,
                        admit_adb_target=self._async_admit_adb_target,
                        panel_name=self._panel_name,
                    ),
                    f"permission repair after updating {owner.title}",
                )
            await async_clear_update_failure_if_installed(
                self.hass,
                self._entry_id,
                selected_artifact.version
                if feed is not None
                else release.version
                if release
                else target_version,
                build.version_code if feed is not None else None,
                verified_success=True,
            )
        finally:
            self._progress.end()
            self.async_write_ha_state()

    async def _async_start_panel_download(
        self, offer: PanelCachedUpdate, artifact: ReleaseArtifact | None
    ) -> None:
        """Ask the panel to fetch only the host-admitted release, then follow it."""
        await async_request_panel_download(
            self.coordinator.client, offer, artifact, self._async_admit_artifact
        )
        self._record_route(ROUTE_PANEL, offer.tag)
        self._progress.enter("installing")
        observer = self._start_observer(offer.target_version)
        self.async_write_ha_state()
        await observer

    def _record_route(self, route: str, tag: str) -> None:
        """Say which way this update reached the panel."""
        self._attr_extra_state_attributes = {ROUTE_ATTRIBUTE: route}
        _LOGGER.info("Updating %s to %s: %s", self.entity_id, tag, route)

    async def _async_select_feed_build(
        self, feed: BuildFeed, version: str | None, backup: bool
    ) -> FeedBuild:
        """Resolve a valid signed-feed target before starting an update attempt."""
        package_id = self._feed_package()
        newest = self._selected_feed_build(self._host_release())
        code = (
            parse_build_request(version)
            if version is not None
            else (newest.version_code if newest is not None else None)
        )
        if self.in_progress or backup:
            raise _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
        build = feed.find(code, package_id) if code is not None else None
        if build is None and code is not None and self._feed is not None:
            # The build may have been published since the last scheduled read.
            await self._feed.async_refresh()
            if self._feed.last_update_success and self._feed.data is not None:
                build = self._feed.data.find(code, package_id)
        if (
            self.in_progress
            or backup
            or build is None
            or (
                build.version_name == self.coordinator.data.health.version
                and build.version_code
                == (self.coordinator.data.health.version_code or self._installed_code)
            )
            or (
                self.coordinator.data.health.version_code is None
                and self._installed_code is None
            )
            or (
                version is None
                and (self._feed is None or self._feed.verified_apk(build) is None)
            )
            or not self._artifact_allowed(feed_release_artifact(build))
        ):
            raise _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
        return build

    async def _async_deliver_build(
        self,
        artifact: ReleaseArtifact,
        *,
        apk: bytes | None = None,
        fallback: bool = False,
        migration: bool = False,
    ) -> bool:
        """Back up, verify, upload and commit one signed build, then prove it.

        With ``fallback``, a download that fetched nothing, or a panel that
        cannot take an upload at all, returns False before anything is
        installed, so the caller can use the panel's own route.
        """
        if self.coordinator.identity_mismatch:
            raise _update_error("update_unavailable", "The panel identity has changed")
        if not self._artifact_allowed(artifact):
            raise _update_error(
                "update_unavailable", "The requested update is unavailable"
            )
        client = self.coordinator.client
        snapshot: PanelSnapshot | None = self.coordinator.data
        descriptor = artifact.descriptor
        package_id = descriptor.package_id if descriptor else LEGACY_PACKAGE_ID
        if snapshot is None:
            # Nothing has been read from the panel, so there is no build to
            # replace and none to prove the replacement against.
            raise _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
        running_package = LEGACY_PACKAGE_ID if migration else package_id
        if not reports_package(snapshot.health.package, running_package):
            raise _update_error(
                "panel_changed_during_update",
                "The panel changed while preparing the update",
            )
        before = (snapshot.health.build, snapshot.health.package)
        installed_successor_code: int | None = None
        if migration:
            installed_successor_code = await self._async_check_successor(
                artifact, snapshot
            )
        await self._async_backup_panel()
        if (
            migration
            and descriptor is not None
            and installed_successor_code is not None
            and installed_successor_code >= descriptor.version_code
        ):
            try:
                await client.async_offer_installed_successor()
            except UpdateApprovalRequiredError as err:
                raise _update_error(
                    "update_approval_required",
                    "Approve this update on the panel, then try again",
                ) from err
            except UpdateBusyError as err:
                raise _update_error(
                    "update_busy", "The panel is busy with another operation"
                ) from err
            except HaPaneldError as err:
                raise _update_error(
                    "bridge_handover_refused", "The panel refused the app handover"
                ) from err
            self._record_route(ROUTE_STAGED, artifact.tag)
            self._progress.enter("installing")
            await self._async_wait_for_build(artifact, before, minimum_code=True)
            return True
        if apk is None:
            try:
                apk = await async_download_build(
                    async_get_clientsession(self.hass), artifact
                )
            except BuildDownloadError as err:
                if fallback:
                    # Nothing was fetched, so nothing failed a check: the panel
                    # can still fetch and verify the release itself.
                    _LOGGER.warning("Could not download %s here: %s", artifact.tag, err)
                    return False
                raise _update_error(
                    "release_download_failed", "The app release could not be downloaded"
                ) from err
            except BuildFeedError as err:
                raise _verification_error(artifact) from err
        try:
            try:
                staged = (
                    await client.async_stage_apk(apk, migration_sha256=artifact.sha256)
                    if migration
                    else await client.async_stage_apk(apk)
                )
            except StagingUnavailableError:
                if fallback:
                    return False
                raise
            health = self.coordinator.data.health if self.coordinator.data else None
            panel_changed = (
                health is None
                or not reports_package(health.package, running_package)
                or (migration and health.version != artifact.version)
            )
            staged_mismatch = (
                staged.package != package_id
                or staged.signer != _RELEASE_SIGNER_CERTIFICATE_SHA256
                or staged.version != artifact.version
            )
            if panel_changed or staged_mismatch:
                with contextlib.suppress(HaPaneldError):
                    await client.async_discard_apk(staged.token)
                if panel_changed:
                    raise _update_error(
                        "panel_changed_during_update",
                        "The panel changed while preparing the update",
                    )
                raise _update_error(
                    "staged_app_mismatch",
                    "The staged app does not match the signed release details",
                )

            async def commit() -> None:
                try:
                    await self._async_admit_artifact(artifact)
                except HomeAssistantError:
                    with contextlib.suppress(HaPaneldError):
                        await client.async_discard_apk(staged.token)
                    raise
                await client.async_commit_apk(staged.token)

            await _retry_unstarted_install(commit)
        except UploadDisabledError as err:
            raise _update_error(
                "upload_disabled", "The panel does not accept app uploads"
            ) from err
        except UpdateBusyError as err:
            raise _update_error(
                "update_busy", "The panel is busy with another operation"
            ) from err
        except UpdateApprovalRequiredError as err:
            raise _update_error(
                "update_approval_required",
                "Approve this update on the panel, then try again",
            ) from err
        except (CannotConnectError, InvalidResponseError) as err:
            raise _update_error(
                "update_not_accepted", "The panel did not accept the update request"
            ) from err
        self._record_route(ROUTE_STAGED, artifact.tag)
        self._progress.enter("installing")
        try:
            await self._async_wait_for_build(artifact, before, watch_refusal=True)
        except UpdateRejectedError as refusal:
            # A panel whose own rule refuses a higher build (its version-name
            # order predates build-number order) can only be crossed by
            # Android's installer, which orders by build number alone.
            adb = (
                await self._async_adb_route()
                if _DOWNGRADE_REFUSAL in str(refusal)
                and descriptor is not None
                and (installed := snapshot.health.version_code or self._installed_code)
                and descriptor.version_code > installed
                else None
            )
            if adb is None:
                raise
            _LOGGER.info(
                "%s refused %s (%s); installing it over ADB",
                self._panel_name(),
                artifact.tag,
                refusal,
            )
            await self._async_deliver_adb(artifact, *adb, apk=apk)
        return True

    async def _async_backup_panel(self) -> None:
        """Keep the existing verified backup gate for both install routes."""
        try:
            await async_store_panel_backup(
                self.hass,
                self._entry_id,
                self._installed_code,
                await self.coordinator.client.async_backup_panel(),
            )
        except UpdateApprovalRequiredError as err:
            raise _update_error(
                "update_approval_required",
                "Approve this update on the panel, then try again",
            ) from err
        except PanelBackupInvalidError as err:
            # The archive was unreadable, so nothing here is a backup. Refuse
            # the upgrade rather than replace the app that still holds the
            # only copy of this panel's settings.
            raise _update_error(
                "panel_backup_failed", "The panel could not be backed up first"
            ) from err
        except (HaPaneldError, OSError) as err:
            raise _update_error(
                "panel_backup_failed", "The panel could not be backed up first"
            ) from err

    async def _async_deliver_adb(
        self,
        artifact: ReleaseArtifact,
        target: AdbInstallTarget,
        credential: AdbCredential,
        *,
        apk: bytes | None = None,
    ) -> None:
        """Send one signed replacement through the already-authorized ADB peer."""
        descriptor = artifact.descriptor
        snapshot: PanelSnapshot | None = self.coordinator.data
        if not self._artifact_allowed(artifact):
            raise _update_error(
                "update_unavailable", "The requested update is unavailable"
            )
        if (
            descriptor is None
            or snapshot is None
            or not reports_package(snapshot.health.package, descriptor.package_id)
        ):
            raise _update_error(
                "panel_changed_during_update",
                "The panel changed while preparing the update",
            )
        before = (snapshot.health.build, snapshot.health.package)
        await self._async_backup_panel()
        if apk is None:
            try:
                apk = await async_download_build(
                    async_get_clientsession(self.hass), artifact
                )
            except BuildDownloadError as err:
                raise _update_error(
                    "release_download_failed", "The app release could not be downloaded"
                ) from err
            except BuildFeedError as err:
                raise _verification_error(artifact) from err
        # A download or backup may take minutes. Re-prove the same panel and
        # credential immediately before replacing its installed package.
        fresh = await self._async_adb_route()
        fresh_target, fresh_credential = fresh if fresh else (None, None)
        if (
            not self._artifact_allowed(artifact)
            or fresh_target != target
            or fresh_credential is None
            or fresh_credential.generation_id != credential.generation_id
        ):
            raise _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
        try:
            with NamedTemporaryFile(
                prefix="panel-assistant-update-", suffix=".apk"
            ) as file:
                await self.hass.async_add_executor_job(file.write, apk)
                await self.hass.async_add_executor_job(file.flush)
                outcome = await async_update_installed_apk(
                    target,
                    credential.signer,
                    descriptor,
                    Path(file.name),
                    before_install=lambda: self._async_admit_artifact(artifact),
                )
        except InstallAdbError as err:
            raise _update_error(
                "update_not_complete", "The panel update did not complete"
            ) from err
        if outcome is InstallOutcome.REFUSED:
            raise _UpdateRefusalError(self._panel_name())
        self._progress.enter("installing")
        try:
            installed = await async_preflight_install(
                target,
                credential.signer,
                descriptor,
                admit_installed_target=True,
            )
            if not installed.target_installed or (
                await async_launch_installed_app(
                    target,
                    credential.signer,
                    descriptor,
                    expected_root_mode=installed.root_mode,
                )
                is not LaunchOutcome.STARTED
            ):
                raise _update_error(
                    "update_not_complete", "The panel update did not complete"
                )
        except InstallAdbError as err:
            raise _update_error(
                "update_not_complete", "The panel update did not complete"
            ) from err
        self._record_route(ROUTE_ADB, artifact.tag)
        await self._async_wait_for_build(artifact, before)

    async def _async_wait_for_build(
        self,
        artifact: ReleaseArtifact,
        before: tuple[str, str | None],
        *,
        minimum_code: bool = False,
        watch_refusal: bool = False,
    ) -> None:
        """Wait for exactly the committed build, then remember its number."""
        proven = await self._progress.async_wait_for_build(
            self.hass,
            artifact,
            before,
            minimum_code=minimum_code,
            watch_refusal=watch_refusal,
        )
        if proven is not None:
            self._installed_code, self._code_key = proven


class NativeUpdate(NativeEntity, UpdateEntity):
    """Another component's update as the panel reports it, such as the Companion app."""

    @property
    def installed_version(self) -> str | None:
        """Return the reported installed version."""
        value = self.reported_value
        return None if value is None else value["installed_version"]

    @property
    def latest_version(self) -> str | None:
        """Return the reported latest version."""
        value = self.reported_value
        return None if value is None else value["latest_version"]

    @property
    def release_url(self) -> str | None:
        """Return the reported release link."""
        value = self.reported_value
        return None if value is None else value["release_url"]

    @property
    def in_progress(self) -> bool:
        """Return whether the panel reports an installation in progress."""
        value = self.reported_value
        return value is not None and bool(value["in_progress"])

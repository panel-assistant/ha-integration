"""Panel-owned ha-paneld software update entity."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from aiohttp import ClientConnectorError
from aiohttp.web import HTTPBadRequest
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    async_get_adb_credential,
    async_get_durable_adb_credential,
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
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    StagingUnavailableError,
    UpdateApprovalRequiredError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
    is_newer_stable_version,
    is_valid_discovery_id,
)
from .const import DOMAIN, update_unique_id
from .coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelCoordinatorEntity,
    PanelSnapshot,
)
from .device import panel_device_info, panel_display_name
from .failure_repair import (
    async_clear_update_failure_if_installed,
    async_record_update_failure,
    async_refresh_update_failure_name,
)
from .feed_coordinator import (
    BuildFeedCoordinator,
    StableReleaseCoordinator,
    async_get_feed_coordinator,
    async_get_stable_release_coordinator,
)
from .install_adb import (
    AdbInstallTarget,
    InstallAdbError,
    InstallOutcome,
    LaunchOutcome,
    async_launch_installed_app,
    async_preflight_install,
    async_update_installed_apk,
)
from .install_network import (
    InstallNetworkError,
    async_pin_install_target,
    async_revalidate_install_target,
)
from .native import NativeEntity, async_setup_native_platform
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup
from .provisioning import InstallTargetState, async_probe_install_target
from .release import (
    _RELEASE_SIGNER_CERTIFICATE_SHA256,
    ReleaseArtifact,
    is_feed_build_tag,
)
from .status import PanelCachedUpdate, home_ui_allows
from .transport import async_get_sessions
from .update_coordinator import PanelUpdateCoordinator

_ANDROID_DOWNLOAD_MAX_SECONDS = 10 * 60
_ANDROID_PACKAGE_INSTALL_MAX_SECONDS = 3 * 60
_RESTART_HEALTH_GRACE_SECONDS = 60
# Android bounds the signed release download to ten minutes and package-manager
# installation to three minutes. Retain one more minute for service replacement
# and fresh health before declaring the panel unreachable.
_UPDATE_TIMEOUT_SECONDS = (
    _ANDROID_DOWNLOAD_MAX_SECONDS
    + _ANDROID_PACKAGE_INSTALL_MAX_SECONDS
    + _RESTART_HEALTH_GRACE_SECONDS
)
_UPDATE_RECHECK_SECONDS = 2
_TERMINAL_STATUS_GRACE_SECONDS = 60
# The first ha-paneld release that can be backed up and sent an app over the LAN.
# An older panel is offered only what it finds itself, which it installs itself.
_FIRST_LAN_UPDATE_VERSION = "0.8.6"
# Which way the last update reached the panel, shown on the entity.
ROUTE_ATTRIBUTE = "update_route"
ROUTE_STAGED = "staged_by_home_assistant"
ROUTE_PANEL = "downloaded_by_panel"
ROUTE_ADB = "installed_by_home_assistant_adb"
ROUTE_UNAVAILABLE_ATTRIBUTE = "update_unavailable_reason"

_LOGGER = logging.getLogger(__name__)


class _UpdateRefusalError(ServiceValidationError, HTTPBadRequest):
    """Report an expected refusal cleanly through both HA service transports."""

    def __init__(self, panel: str) -> None:
        message = (
            f"{panel} refused the update request. "
            "Check its Install tab, then try again."
        )
        HTTPBadRequest.__init__(self, text=message)
        self._message = message
        self.translation_domain = DOMAIN
        self.translation_key = "update_rejected"
        self.translation_placeholders = {"panel": panel}


def _update_error(translation_key: str, fallback: str) -> HomeAssistantError:
    """Return a localized update-action error with a useful log fallback."""
    return HomeAssistantError(
        fallback,
        translation_domain=DOMAIN,
        translation_key=translation_key,
    )


def _verification_error(artifact: ReleaseArtifact) -> HomeAssistantError:
    """Name what failed to verify: a feed build, or a GitHub release."""
    if is_feed_build_tag(artifact.tag):
        return _update_error(
            "build_verification_failed",
            "The build did not match the signed build feed",
        )
    return _update_error(
        "release_verification_failed", "The release did not match its signature"
    )


async def _retry_unstarted_install(start: Callable[[], Awaitable[None]]) -> None:
    """Retry once only when no connection was made to the panel."""
    try:
        await start()
    except CannotConnectError as err:
        if not isinstance(err.__cause__, ClientConnectorError):
            raise
        await asyncio.sleep(1)
        await start()


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
    """Project a selected stable update through the panel's own transaction."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_has_entity_name = True
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_translation_key = "paneld_update"
    _attr_title = "ha-paneld"

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
        self._update_coordinator = update_coordinator
        # A signed build feed, when configured. Internal builds
        # share one version name, so they are told apart by build number.
        self._feed = feed
        # The latest stable release as Home Assistant itself authenticated it,
        # so a panel without internet access is offered and sent it too.
        self._release = release
        self._installed_code: int | None = None
        self._code_key: tuple[str, str] | None = None
        self._code_task: asyncio.Task[None] | None = None
        if feed is not None:
            self._attr_supported_features |= UpdateEntityFeature.SPECIFIC_VERSION
        self._attr_unique_id = update_unique_id(entry_id)
        self._attr_in_progress = False
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
        self._refresh_installed_code()
        self._schedule_route_refresh()
        super()._handle_coordinator_update()

    def _handle_offer_refresh(self) -> None:
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
            snapshot.health.build,
            snapshot.health.discovery_id,
            self._entry_discovery_id(),
            getattr(self.coordinator.client, "address", None),
            self._api_capability(),
            artifact.sha256 if artifact else None,
        )

    def _api_capability(self) -> str | None:
        snapshot: PanelSnapshot | None = self.coordinator.data
        return (
            snapshot.status.install_capability if snapshot and snapshot.status else None
        )

    def _has_install_route(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if self._api_capability() == "api":
            return True
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
        feed = self._feed_mode()
        if feed is not None:
            newest = (
                self._feed.verified_newest(self._feed_package()) if self._feed else None
            )
            return feed_release_artifact(newest) if newest is not None else None
        release = self._host_release()
        if release is not None and release.descriptor is not None:
            return release
        return None

    def _schedule_route_refresh(self) -> None:
        if self._api_capability() == "api":
            self._adb_ready_key = None
            self._legacy_api_ready_key = None
            self._route_checked_key = None
            return
        if self._adb_artifact() is None and self._stable_target() is None:
            return
        if self._route_checked_key == self._route_key():
            return
        if self._route_task is not None and not self._route_task.done():
            return
        self._route_task = self.hass.async_create_task(
            self._async_refresh_route(),
            f"check ha-paneld update route {self._entry_id}",
        )
        self._route_task.add_done_callback(self._route_refresh_finished)

    def _route_refresh_finished(self, task: asyncio.Task[None]) -> None:
        if self._route_task is task:
            self._route_task = None
            self._schedule_route_refresh()

    def _cancel_route_refresh(self) -> None:
        task = self._route_task
        self._route_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _async_refresh_route(self) -> None:
        key = self._route_key()
        try:
            route, _target, _credential = await self._async_install_route()
        except Exception as err:
            _LOGGER.debug(
                "Could not check update route for %s: %s", self._entry_id, err
            )
            route = None
        if key == self._route_key():
            self._route_checked_key = key
            self._adb_ready_key = key if route == ROUTE_ADB else None
            self._legacy_api_ready_key = (
                key if route == ROUTE_PANEL and self._api_capability() is None else None
            )
            attributes = dict(self._attr_extra_state_attributes)
            if not self._has_install_route():
                attributes[ROUTE_UNAVAILABLE_ATTRIBUTE] = (
                    "Panel Assistant has no verified signed build for this ADB update."
                    if self._adb_artifact() is None
                    else "The panel cannot install this update itself, and Panel "
                    "Assistant has no usable authorized ADB route."
                )
            else:
                attributes.pop(ROUTE_UNAVAILABLE_ATTRIBUTE, None)
            self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

    async def _async_install_route(
        self,
    ) -> tuple[str | None, AdbInstallTarget | None, AdbCredential | None]:
        """Choose from the panel's live install route, then authorized ADB."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None or not self.coordinator.last_update_success:
            return None, None, None
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
        artifact = self._adb_artifact()
        if artifact is None or artifact.descriptor is None:
            return None, None, None
        try:
            try:
                credential = await async_get_durable_adb_credential(self.hass)
            except AdbCredentialError:
                credential = None
            pinned = await async_pin_install_target(
                self.hass, self.coordinator.client.address
            )
            # Bind the ADB peer to the same pinned HTTP panel, even when the
            # stored address is a DNS name that can resolve more than once.
            pinned_health = await HaPaneldClient(
                async_get_clientsession(self.hass), pinned.pinned
            ).async_get_health()
            if (
                pinned_health.panel_id != snapshot.health.panel_id
                or pinned_health.package != snapshot.health.package
                or (
                    snapshot.health.discovery_id is not None
                    and pinned_health.discovery_id != snapshot.health.discovery_id
                )
                or (
                    (entry_did := self._entry_discovery_id()) is not None
                    and pinned_health.discovery_id != entry_did
                )
            ):
                return None, None, None
            if credential is None:
                # Running panels can have a config entry without an HA ADB key.
                # Only an already-open ADB peer can establish this route without
                # prompting the panel owner for authorization.
                open_probe = await async_probe_install_target(pinned.pinned)
                if open_probe.state not in {
                    InstallTargetState.INSTALLED,
                    InstallTargetState.MIGRATION_CANDIDATE,
                } or None in (
                    open_probe.serial,
                    open_probe.model,
                    open_probe.primary_abi,
                    open_probe.android_sdk,
                ):
                    return None, None, None
                await async_get_adb_credential(self.hass)
                credential = await async_get_durable_adb_credential(self.hass)
            probe = await async_probe_install_target(pinned.pinned, credential.signer)
            if probe.state not in {
                InstallTargetState.INSTALLED,
                InstallTargetState.MIGRATION_CANDIDATE,
            } or None in (
                probe.serial,
                probe.model,
                probe.primary_abi,
                probe.android_sdk,
            ):
                return None, None, None
            assert probe.serial is not None
            assert probe.model is not None
            assert probe.primary_abi is not None
            assert probe.android_sdk is not None
            target = AdbInstallTarget(
                address=pinned.pinned,
                serial=probe.serial,
                model=probe.model,
                primary_abi=probe.primary_abi,
                android_sdk=probe.android_sdk,
            )
            admitted = await async_preflight_install(
                target,
                credential.signer,
                artifact.descriptor,
                admit_installed_target=True,
            )
            if not admitted.target_installed:
                return None, None, None
            await async_revalidate_install_target(self.hass, pinned)
        except (
            AdbCredentialError,
            InstallAdbError,
            InstallNetworkError,
            HaPaneldError,
            OSError,
        ):
            return None, None, None
        return ROUTE_ADB, target, credential

    def _refresh_installed_code(self) -> None:
        """Read the running build number once per install, never per poll."""
        if self._feed is None or not self.coordinator.last_update_success:
            return
        health = self.coordinator.data.health
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
        """Return the feed only when it and the panel's build number are known."""
        if (
            self._feed is None
            or not self._feed.last_update_success
            or self._feed.data is None
            or self._installed_code is None
        ):
            return None
        return self._feed.data

    def _feed_package(self) -> str | None:
        """Choose the package the panel actually runs, including old reports."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.health.package or LEGACY_PACKAGE_ID

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

    def _start_observer(
        self, expected_version: str | None, *, recovered: bool = False
    ) -> asyncio.Task[None]:
        """Start or reuse the entity's sole bounded health observer."""
        if self._observer_task is not None and not self._observer_task.done():
            return self._observer_task
        self._attr_in_progress = True
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
            verified = await self._async_wait_for_installed_version(expected_version)
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
            self._attr_in_progress = False
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
        self._attr_in_progress = False
        self._recovery_started = False
        if task is not None and not task.done():
            task.cancel()

    @property
    def in_progress(self) -> bool:
        """Retain local work and recover panel-owned work after an HA restart."""
        operation = self._update_coordinator.data.operation
        return (
            (self._observer_task is not None and not self._observer_task.done())
            or self._attr_in_progress
            or (
                operation is not None
                and operation.running
                and operation.component == "ha-paneld"
            )
        )

    @property
    def installed_version(self) -> str | None:
        """Return the current version from the health authority, once read."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        version = snapshot.health.version
        if self._feed_mode() is not None and self._installed_code is not None:
            return build_label(version, self._installed_code)
        release = self._host_release()
        if (
            release is not None
            and release.descriptor is None
            and release.version == version
        ):
            # Core hides equal versions before asking version_is_newer. Name
            # the installed bridge so its unfinished handover stays visible.
            return f"{version} (bridge)"
        return version

    def _offered_update(self) -> PanelCachedUpdate | None:
        """Return only a fresh cached stable target matching installed health."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        status = snapshot.status
        offer = status.panel_assistant_update if status is not None else None
        if (
            offer is None
            or offer.current_version != snapshot.health.version
            or not is_newer_stable_version(
                offer.target_version, snapshot.health.version
            )
        ):
            return None
        return offer

    def _host_release(self) -> ReleaseArtifact | None:
        """Return the release authenticated here, when newer than the panel's."""
        snapshot: PanelSnapshot | None = self.coordinator.data
        release = (
            self._release.artifact_for(snapshot.health.package or LEGACY_PACKAGE_ID)
            if self._release is not None and snapshot is not None
            else None
        )
        if (
            snapshot is None
            or release is None
            or not (
                is_newer_stable_version(release.version, snapshot.health.version)
                # A bridge at the release version still owes its handover.
                or (
                    release.descriptor is None
                    and release.version == snapshot.health.version
                )
            )
            or is_newer_stable_version(
                _FIRST_LAN_UPDATE_VERSION, snapshot.health.version
            )
        ):
            return None
        return release

    def _stable_target(self) -> PanelCachedUpdate | None:
        """Return the newer of the panel's own offer and the release found here."""
        offer = self._offered_update()
        release = self._host_release()
        if release is None or (
            offer is not None
            and is_newer_stable_version(offer.target_version, release.version)
        ):
            return offer
        return PanelCachedUpdate(
            self.coordinator.data.health.version, release.version, release.tag
        )

    @property
    def latest_version(self) -> str | None:
        """Report installed version if no newer panel-approved stable target exists."""
        if not self._has_install_route():
            return self.installed_version
        feed = self._feed_mode()
        if feed is not None:
            newest = (
                self._feed.verified_newest(self._feed_package()) if self._feed else None
            )
            if (
                newest is not None
                and self._installed_code is not None
                and newest.version_code > self._installed_code
            ):
                return newest.label
            return self.installed_version
        offer = self._stable_target()
        if self._adb_ready_key == self._route_key() and self._api_capability() != "api":
            release = self._adb_artifact()
            if release is not None:
                return release.version
        return offer.target_version if offer is not None else self.installed_version

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Use the same stable-versus-RC comparison as the bounded client parser."""
        if self._feed_mode() is not None:
            latest = parse_build_request(latest_version)
            installed = parse_build_request(installed_version)
            if latest is not None and installed is not None:
                return latest > installed
        release = self._host_release()
        if (
            release is not None
            and release.descriptor is None
            and installed_version == f"{release.version} (bridge)"
        ):
            if latest_version == release.version:
                return True
            installed_version = release.version
        return is_newer_stable_version(latest_version, installed_version)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the existing config-entry device without a second identity."""
        return panel_device_info(
            self._entry_id,
            self.coordinator.data,
            self.coordinator.client.configuration_url,
            self._title,
            self.coordinator.app_build,
        )

    async def async_install(
        self, version: str | None, backup: bool, **_kwargs: object
    ) -> None:
        """Start one exact stable offer, then follow the expected panel restart."""
        if self.coordinator.identity_mismatch:
            raise _update_error("update_unavailable", "The panel identity has changed")
        route, adb_target, adb_credential = await self._async_install_route()
        if route is None:
            error = _update_error(
                "update_unavailable", "The requested ha-paneld update is unavailable"
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
        feed = self._feed_mode()
        failure_artifact: dict[str, object] | None = None
        if feed is not None:
            build = await self._async_select_feed_build(feed, version, backup)
            target_version = build.label
            selected_artifact = feed_release_artifact(build)
            verified_apk = self._feed.verified_apk(build) if self._feed else None
            failure_artifact = asdict(selected_artifact)
            if route == ROUTE_ADB and selected_artifact.descriptor is None:
                raise _update_error(
                    "update_unavailable",
                    "The requested ha-paneld update is unavailable",
                )
        else:
            offer = self._stable_target()
            if route == ROUTE_ADB:
                release = self._adb_artifact()
                if release is not None:
                    offer = PanelCachedUpdate(
                        self.coordinator.data.health.version,
                        release.version,
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
                    "The requested ha-paneld update is unavailable",
                )
            target_version = offer.target_version
        self._attr_in_progress = True
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
                release = self._host_release()
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
                        successor = self._release.data if self._release else None
                        if (
                            successor is None
                            or successor.tag != release.tag
                            or successor.descriptor is None
                            or successor.descriptor.package_id != SUCCESSOR_PACKAGE_ID
                        ):
                            raise _verification_error(release)
                        if (
                            self.coordinator.data.health.version != release.version
                            or not self.coordinator.data.health.installation_identity
                        ):
                            await self._async_deliver_build(release)
                        if reports_package(
                            self.coordinator.data.health.package, SUCCESSOR_PACKAGE_ID
                        ):
                            # A connected bridge may already have completed its
                            # own handover. Prove that result without reinstalling.
                            await self._async_wait_for_build(
                                successor, ("", LEGACY_PACKAGE_ID)
                            )
                        else:
                            await self._async_deliver_build(successor, migration=True)
                    elif not await self._async_deliver_build(release, fallback=True):
                        await self._async_start_panel_download(offer)
                else:
                    await self._async_start_panel_download(offer)
        except UpdateRejectedError:
            error = _UpdateRefusalError(self._panel_name())
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
            await async_clear_update_failure_if_installed(
                self.hass,
                self._entry_id,
                selected_artifact.version if feed is not None else target_version,
                build.version_code if feed is not None else None,
                verified_success=True,
            )
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()

    async def _async_start_panel_download(self, offer: PanelCachedUpdate) -> None:
        """Ask the panel to fetch, verify and install the release itself."""
        try:
            await _retry_unstarted_install(
                lambda: self.coordinator.client.async_start_panel_update(offer.tag)
            )
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
        self._record_route(ROUTE_PANEL, offer.tag)
        observer = self._start_observer(offer.target_version)
        self.async_write_ha_state()
        await observer

    def _record_route(self, route: str, tag: str) -> None:
        """Say which way this update reached the panel."""
        self._attr_extra_state_attributes = {ROUTE_ATTRIBUTE: route}
        _LOGGER.info("Updating %s to %s: %s", self.entity_id, tag, route)

    def _show_accepted_restart(self, projected: bool) -> bool:
        """Show an accepted update's absent app as restarting only once."""
        if projected or self.coordinator.available:
            return projected
        sessions = async_get_sessions(self.hass)
        if sessions.restart_notice(self._entry_id) is None:
            sessions.set_restart_notice(
                self._entry_id,
                "app",
                "update",
                1000
                * (
                    _ANDROID_PACKAGE_INSTALL_MAX_SECONDS + _RESTART_HEALTH_GRACE_SECONDS
                ),
            )
        return True

    async def _async_wait_for_installed_version(
        self, expected_version: str | None
    ) -> bool:
        """Poll through restart; return false when recovered outcome is unknown."""
        starting_health = (
            self.coordinator.data.health
            if expected_version is None and self.coordinator.data is not None
            else None
        )
        deadline = asyncio.get_running_loop().time() + _UPDATE_TIMEOUT_SECONDS
        terminal_status_deadline: float | None = None
        restart_projected = False
        running_seen = False
        while asyncio.get_running_loop().time() < deadline:
            await self.coordinator.async_request_refresh()
            restart_projected = self._show_accepted_restart(restart_projected)
            snapshot = self.coordinator.data
            verified = (
                self.installed_version == expected_version
                if expected_version is not None
                else snapshot is not None
                and starting_health is not None
                and (
                    is_newer_stable_version(
                        snapshot.health.version, starting_health.version
                    )
                    or (
                        snapshot.health.version == starting_health.version
                        and starting_health.version_code is not None
                        and snapshot.health.version_code is not None
                        and snapshot.health.version_code > starting_health.version_code
                    )
                )
            )
            if self.coordinator.last_update_success and verified:
                await self._update_coordinator.async_request_refresh()
                return True
            try:
                await self._update_coordinator.async_request_refresh()
                status = self._update_coordinator.data.operation
            except CannotConnectError, InvalidResponseError:
                status = None
            if (
                status is not None
                and status.component == "ha-paneld"
                and status.running
            ):
                running_seen = True
            if (
                status is not None
                and not status.running
                and status.component == "ha-paneld"
            ):
                # Android finishes its progress slot just before its process
                # replacement is observable. Keep polling health long enough
                # to distinguish that successful hand-off from a real failure.
                if terminal_status_deadline is None:
                    terminal_status_deadline = min(
                        deadline,
                        asyncio.get_running_loop().time()
                        + _TERMINAL_STATUS_GRACE_SECONDS,
                    )
                if asyncio.get_running_loop().time() >= terminal_status_deadline:
                    if expected_version is None:
                        # A stable or feed update may have finished before our
                        # first health sample. Without its lost target, terminal
                        # status alone is not proof that it failed.
                        return False
                    raise _update_error(
                        "update_not_complete", "The panel update did not complete"
                    )
            await asyncio.sleep(_UPDATE_RECHECK_SECONDS)
        if expected_version is None and not running_seen:
            return False
        raise _update_error(
            "update_did_not_return", "The panel did not return after the update"
        )

    async def _async_select_feed_build(
        self, feed: BuildFeed, version: str | None, backup: bool
    ) -> FeedBuild:
        """Resolve a valid signed-feed target before starting an update attempt."""
        package_id = self._feed_package()
        newest = self._feed.verified_newest(package_id) if self._feed else None
        code = (
            parse_build_request(version)
            if version is not None
            else (newest.version_code if newest is not None else None)
        )
        if self.in_progress or backup:
            raise _update_error(
                "update_unavailable",
                "The requested ha-paneld update is unavailable",
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
            or build.version_code == self._installed_code
        ):
            raise _update_error(
                "update_unavailable",
                "The requested ha-paneld update is unavailable",
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
        client = self.coordinator.client
        snapshot: PanelSnapshot | None = self.coordinator.data
        descriptor = artifact.descriptor
        package_id = descriptor.package_id if descriptor else LEGACY_PACKAGE_ID
        if snapshot is None:
            # Nothing has been read from the panel, so there is no build to
            # replace and none to prove the replacement against.
            raise _update_error(
                "update_unavailable",
                "The requested ha-paneld update is unavailable",
            )
        running_package = LEGACY_PACKAGE_ID if migration else package_id
        if not reports_package(snapshot.health.package, running_package):
            raise _verification_error(artifact)
        before = (snapshot.health.build, snapshot.health.package)
        installed_successor_code: int | None = None
        if migration:
            try:
                capability = await client.async_get_successor_capability()
            except HaPaneldError as err:
                raise _verification_error(artifact) from err
            if (
                descriptor is None
                or not snapshot.health.installation_identity
                or package_id != SUCCESSOR_PACKAGE_ID
                or snapshot.health.version != artifact.version
                or capability[:2] != (package_id, artifact.version)
                or capability[3]
            ):
                raise _verification_error(artifact)
            installed_successor_code = capability[2]
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
            except HaPaneldError as err:
                raise _verification_error(artifact) from err
            self._record_route(ROUTE_STAGED, artifact.tag)
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
                raise _verification_error(artifact) from err
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
            if (
                staged.package != package_id
                # Ordinary updates stay in-place. Only an explicitly capable
                # bridge may stage its exact signed successor for handover.
                or health is None
                or not reports_package(health.package, running_package)
                or (migration and health.version != artifact.version)
                or staged.signer != _RELEASE_SIGNER_CERTIFICATE_SHA256
                or staged.version != artifact.version
            ):
                with contextlib.suppress(HaPaneldError):
                    await client.async_discard_apk(staged.token)
                raise _verification_error(artifact)
            await _retry_unstarted_install(
                lambda: client.async_commit_apk(staged.token)
            )
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
        await self._async_wait_for_build(artifact, before)
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
        if (
            descriptor is None
            or snapshot is None
            or not reports_package(snapshot.health.package, descriptor.package_id)
        ):
            raise _verification_error(artifact)
        before = (snapshot.health.build, snapshot.health.package)
        await self._async_backup_panel()
        if apk is None:
            try:
                apk = await async_download_build(
                    async_get_clientsession(self.hass), artifact
                )
            except (BuildDownloadError, BuildFeedError) as err:
                raise _verification_error(artifact) from err
        # A download or backup may take minutes. Re-prove the same panel and
        # credential immediately before replacing its installed package.
        route, fresh_target, fresh_credential = await self._async_install_route()
        if (
            route != ROUTE_ADB
            or fresh_target != target
            or fresh_credential is None
            or fresh_credential.generation_id != credential.generation_id
        ):
            raise _update_error(
                "update_unavailable", "The requested ha-paneld update is unavailable"
            )
        try:
            with NamedTemporaryFile(
                prefix="panel-assistant-update-", suffix=".apk"
            ) as file:
                await self.hass.async_add_executor_job(file.write, apk)
                await self.hass.async_add_executor_job(file.flush)
                outcome = await async_update_installed_apk(
                    target, credential.signer, descriptor, Path(file.name)
                )
        except InstallAdbError as err:
            raise _update_error(
                "update_not_complete", "The panel update did not complete"
            ) from err
        if outcome is InstallOutcome.REFUSED:
            raise _UpdateRefusalError(self._panel_name())
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
    ) -> None:
        """Wait for the panel to restart into exactly the committed build."""
        build = artifact.descriptor
        package_id = build.package_id if build else LEGACY_PACKAGE_ID
        loop = asyncio.get_running_loop()
        deadline = (
            loop.time()
            + _ANDROID_PACKAGE_INSTALL_MAX_SECONDS
            + _RESTART_HEALTH_GRACE_SECONDS
        )
        restart_projected = False
        build_seen_without_home = False
        while loop.time() < deadline:
            await self.coordinator.async_request_refresh()
            restart_projected = self._show_accepted_restart(restart_projected)
            health = self.coordinator.data.health if self.coordinator.data else None
            if (
                self.coordinator.last_update_success
                and health is not None
                and (health.build, health.package) != before
                and (minimum_code or health.version == artifact.version)
                and (
                    reports_package(health.package, package_id)
                    or (build is None and health.package == SUCCESSOR_PACKAGE_ID)
                )
            ):
                code = None
                if build is not None:
                    try:
                        (
                            _name,
                            code,
                        ) = await self.coordinator.client.async_get_version_code()
                    except HaPaneldError:
                        code = None
                    if code is not None and not (
                        code >= build.version_code
                        if minimum_code
                        else code == build.version_code
                    ):
                        raise _update_error(
                            "update_not_complete", "The panel update did not complete"
                        )
                if build is None or code is not None:
                    build_seen_without_home = True
                    try:
                        status = await self.coordinator.client.async_get_status(
                            home_proof=True
                        )
                    except HaPaneldError:
                        status = None
                    if status is not None and home_ui_allows(status):
                        if code is not None:
                            self._installed_code = code
                            self._code_key = (health.version, health.build)
                        return
            await asyncio.sleep(_UPDATE_RECHECK_SECONDS)
        raise _update_error(
            "update_not_complete"
            if build_seen_without_home
            else "update_did_not_return",
            "The panel update did not complete"
            if build_seen_without_home
            else "The panel did not return after the update",
        )


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

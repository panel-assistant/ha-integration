"""Panel-owned ha-paneld software update entity."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from functools import partial
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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HaPaneldConfigEntry
from .adb_credentials import AdbCredential
from .adb_protocol import AdbInstallTarget
from .build_feed import (
    BuildFeed,
    FeedBuild,
    build_label,
    feed_release_artifact,
    parse_build_request,
)
from .client import HaPaneldError, UpdateRejectedError, _version_key
from .const import DOMAIN, update_unique_id
from .coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelCoordinatorEntity,
    PanelSnapshot,
)
from .device import panel_display_name
from .feed_coordinator import (
    BuildFeedCoordinator,
    StableReleaseCoordinator,
    async_get_feed_coordinator,
    async_get_stable_release_coordinator,
)
from .native import NativeEntity, async_setup_native_platform
from .panel_operations import claim_panel_operation, release_panel_operation
from .release import ReleaseArtifact, is_feed_build_tag
from .repairs_failure import (
    async_clear_update_failure_if_installed,
    async_record_update_failure,
    async_refresh_update_failure_name,
)
from .repairs_update_route import async_reconcile_update_route_issue
from .repairs_upgrade import runs_old_app
from .status import PanelCachedUpdate
from .update_coordinator import PanelUpdateCoordinator
from .update_errors import (
    _update_error,
    refusal_error,
    route_unavailable_reason,
)
from .update_offer import UpdateOffer
from .update_panel_install import (
    async_repair_update_permissions,
    async_request_panel_download,
)
from .update_policy import feed_build_newer, prereleases_allowed
from .update_progress import UpdateProgress
from .update_route import (
    ROUTE_ADB,
    ROUTE_ATTRIBUTE,
    ROUTE_PANEL,
    ROUTE_SESSION,
    ROUTE_UNAVAILABLE_ATTRIBUTE,
    api_capability,
    async_admit_adb_target,
    async_install_route,
    async_observe_update_route,
    entry_discovery_id,
    session_route,
)
from .update_staging import BuildDelivery

_AWAITING_CONFIRMATION_SUMMARY = "Tap Install on the panel to finish the update."
AWAITING_CONFIRMATION_ATTRIBUTE = "awaiting_confirmation"

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
        self._offer = UpdateOffer(
            coordinator,
            feed,
            release,
            lambda: self._installed_code,
            self._allow_prerelease,
        )
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
        self._delivery = BuildDelivery(
            entry_id,
            coordinator,
            self._offer,
            self._progress,
            installed_code=lambda: self._installed_code,
            record_code=self._remember_code,
            record_route=self._record_route,
            panel_name=self._panel_name,
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
        if self._offer.feed is not None:
            self.async_on_remove(
                self._offer.feed.async_add_listener(self._handle_offer_refresh)
            )
            self._refresh_installed_code()
        if self._offer.release is not None:
            self.async_on_remove(
                self._offer.release.async_add_listener(self._handle_offer_refresh)
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

    def _route_key(self) -> tuple[object, ...] | None:
        snapshot: PanelSnapshot | None = self.coordinator.data
        if snapshot is None:
            return None
        artifact = self._offer.adb_artifact()
        return (
            snapshot.health.panel_id,
            snapshot.health.package,
            snapshot.health.build,
            snapshot.health.discovery_id,
            entry_discovery_id(self.hass, self._entry_id),
            getattr(self.coordinator.client, "address", None),
            api_capability(self.coordinator.data),
            artifact,
        )

    def _session_route(self) -> bool:
        return session_route(self.hass, self._entry_id, self.coordinator)

    def _has_install_route(self) -> bool:
        if not self.coordinator.reachable:
            return self._session_route()
        if api_capability(self.coordinator.data) == "api":
            return True
        if (
            api_capability(self.coordinator.data) is None
            and self._legacy_api_ready_key is not None
            and self._legacy_api_ready_key == self._route_key()
        ):
            return True
        return (
            self._adb_ready_key is not None
            and self._adb_ready_key == self._route_key()
            and self._offer.adb_artifact() is not None
        )

    def _schedule_route_refresh(self) -> None:
        artifact = self._offer.host_release()
        build = self._offer.selected_feed_build(artifact)
        needs_verification = (
            build is not None
            and self._offer.feed is not None
            and self._offer.feed.verified_apk(build) is None
        )
        if api_capability(self.coordinator.data) == "api" and not needs_verification:
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
        return self.coordinator.data is not None and (
            self.coordinator.reachable or self._session_route()
        )

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
            build = self._offer.selected_feed_build(self._offer.host_release())
            if build is not None and self._offer.feed is not None:
                await self._offer.feed.async_verify_build(build)
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
            has_route = await async_observe_update_route(
                self.hass, self._entry_id, self.coordinator
            )
        if key == self._route_key():
            self._route_checked_key = key
            self._adb_ready_key = key if route == ROUTE_ADB else None
            self._legacy_api_ready_key = key if route == ROUTE_PANEL else None
            attributes = dict(self._attr_extra_state_attributes)
            if not self._has_install_route():
                attributes[ROUTE_UNAVAILABLE_ATTRIBUTE] = route_unavailable_reason(
                    error, has_adb_artifact=self._offer.adb_artifact() is not None
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
        return await async_install_route(
            self.hass, self._entry_id, self.coordinator, self._offer, self._panel_name
        )

    def _refresh_installed_code(self) -> None:
        """Read the running build number once per install, never per poll."""
        if not self.coordinator.reachable:
            return
        health = self.coordinator.data.health
        if health.version_code is not None:
            self._installed_code = health.version_code
            return
        if self._offer.feed is None:
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
        if self._offer.feed is not None:
            await self._offer.feed.async_refresh()
        if self._offer.release is not None:
            await self._offer.release.async_refresh()

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
                and (
                    operation.component == "ha-paneld"
                    or (
                        operation.component == "APK" and operation.awaiting_confirmation
                    )
                )
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
        artifact = self._offer.host_release()
        code = snapshot.health.version_code or self._installed_code
        if code is not None and (
            (
                self._offer.feed is not None
                and self._offer.feed.last_update_success
                and self._offer.feed.data is not None
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

    @property
    def latest_version(self) -> str | None:
        """Report the installed version when no newer admitted target exists."""
        if (attempt := self._progress.attempt) is not None:
            return attempt.latest
        if not self._has_install_route():
            return self.installed_version
        artifact = self._offer.host_release()
        if artifact is None:
            return self.installed_version
        build = self._offer.selected_feed_build(artifact)
        if (
            build is not None
            and self._offer.feed is not None
            and self._offer.feed.verified_apk(build) is None
        ):
            return self.installed_version
        return self._offer.label(artifact)

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Compare semantic version first and build number only as a tie-break.

        A signed feed build offer compares in the feed's own order instead,
        versionCode first.
        """
        release = self._offer.host_release()
        latest_name = latest_version.partition(" build ")[0]
        installed_name = installed_version.partition(" build ")[0]
        latest_key = _version_key(latest_name)
        installed_key = _version_key(installed_name)
        if latest_key is None or installed_key is None:
            return False
        if (
            release is not None
            and is_feed_build_tag(release.tag)
            and latest_version == self._offer.label(release)
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
        """Install while no other app-changing operation runs on the same panel."""
        if not claim_panel_operation(self.hass, self._entry_id):
            raise _update_error(
                "update_busy", "The panel is busy with another operation"
            )
        try:
            await self._async_install_claimed(version)
        finally:
            release_panel_operation(self.hass, self._entry_id)

    async def _async_install_claimed(self, version: str | None) -> None:
        """Start one exact admitted offer, then follow the expected panel restart."""
        if self.coordinator.identity_mismatch:
            raise _update_error("update_unavailable", "The panel identity has changed")
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if (
            entry is not None
            and self.coordinator.data is not None
            and runs_old_app(self.coordinator.data.health)
        ):
            raise _update_error(
                "old_app_needs_090",
                "This panel still runs the old app: install Panel Assistant 0.9.0 "
                "first, as its Repair explains",
            )
        try:
            route, adb_target, adb_credential = await self._async_install_route()
        except HomeAssistantError as route_error:
            if route_error.translation_key == "adb_authorization_required":
                artifact = self._offer.adb_artifact()
                failed_target = version
                if failed_target is None and artifact is not None:
                    failed_target = (
                        build_label(artifact.version, artifact.descriptor.version_code)
                        if self._offer.feed_mode() is not None and artifact.descriptor
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
        chosen_artifact = self._offer.host_release()
        feed = self._offer.feed_mode()
        if (
            version is not None
            and parse_build_request(version) is not None
            and self._offer.feed is not None
            and (
                chosen_artifact is None or version != self._offer.label(chosen_artifact)
            )
        ):
            feed = self._offer.feed.data
        failure_artifact: dict[str, object] | None = None
        if feed is not None:
            build = await self._async_select_feed_build(feed, version)
            target_version = build.label
            selected_artifact = feed_release_artifact(build)
            repair_artifact: ReleaseArtifact | None = selected_artifact
            verified_apk = (
                self._offer.feed.verified_apk(build) if self._offer.feed else None
            )
            failure_artifact = asdict(selected_artifact)
        else:
            offer = self._offer.stable_target()
            if route == ROUTE_ADB:
                release = self._offer.adb_artifact()
                if release is not None:
                    offer = PanelCachedUpdate(
                        self.coordinator.data.health.version,
                        self._offer.label(release),
                        release.tag,
                    )
            if (
                self.in_progress
                or offer is None
                or version not in (None, offer.target_version)
            ):
                raise _update_error(
                    "update_unavailable",
                    "The requested Panel Assistant update is unavailable",
                )
            target_version = offer.target_version
            release = self._offer.host_release()
            repair_artifact = release
        self._progress.begin(self.hass, self.installed_version, self.latest_version)
        self._delivery.session = route == ROUTE_SESSION
        self.async_write_ha_state()
        try:
            if feed is not None:
                if route == ROUTE_ADB:
                    assert adb_target is not None and adb_credential is not None
                    await self._delivery.async_deliver_adb(
                        self.hass,
                        selected_artifact,
                        adb_target,
                        adb_credential,
                        apk=verified_apk,
                    )
                else:
                    await self._delivery.async_deliver_build(
                        self.hass, selected_artifact, apk=verified_apk
                    )
            else:
                assert offer is not None
                if route == ROUTE_ADB:
                    assert release is not None
                    assert adb_target is not None and adb_credential is not None
                    await self._delivery.async_deliver_adb(
                        self.hass, release, adb_target, adb_credential
                    )
                    await async_clear_update_failure_if_installed(
                        self.hass,
                        self._entry_id,
                        target_version,
                        None,
                        verified_success=True,
                    )
                    return
                if route == ROUTE_SESSION:
                    # The panel's own download is not carried by its session.
                    if release is None or release.tag != offer.tag:
                        raise _update_error(
                            "update_unavailable",
                            "The requested Panel Assistant update is unavailable",
                        )
                    await self._delivery.async_deliver_build(self.hass, release)
                # Home Assistant sends the release over the LAN whenever it could
                # authenticate it. Only a panel that cannot take an upload at all
                # downloads the release itself, and verifies it itself.
                elif release is not None and release.tag == offer.tag:
                    if not await self._delivery.async_deliver_build(
                        self.hass, release, fallback=True
                    ):
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
                        admit_adb_target=partial(
                            async_admit_adb_target,
                            self.hass,
                            self._entry_id,
                            self.coordinator,
                            panel_name=self._panel_name,
                        ),
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
            self._delivery.session = False
            self._progress.end()
            self.async_write_ha_state()

    async def _async_start_panel_download(
        self, offer: PanelCachedUpdate, artifact: ReleaseArtifact | None
    ) -> None:
        """Ask the panel to fetch only the host-admitted release, then follow it."""
        await async_request_panel_download(
            self.coordinator.client, offer, artifact, self._offer.async_admit
        )
        self._record_route(ROUTE_PANEL, offer.tag)
        self._progress.enter("installing")
        observer = self._start_observer(offer.target_version)
        self.async_write_ha_state()
        await observer

    def _remember_code(self, code: int, key: tuple[str, str]) -> None:
        """Keep the build number a delivery proved, for the version just read."""
        self._installed_code = code
        self._code_key = key

    def _record_route(self, route: str, tag: str) -> None:
        """Say which way this update reached the panel."""
        self._attr_extra_state_attributes = {ROUTE_ATTRIBUTE: route}
        _LOGGER.info("Updating %s to %s: %s", self.entity_id, tag, route)

    async def _async_select_feed_build(
        self, feed: BuildFeed, version: str | None
    ) -> FeedBuild:
        """Resolve a valid signed-feed target before starting an update attempt."""
        package_id = self._offer.feed_package()
        newest = self._offer.selected_feed_build(self._offer.host_release())
        code = (
            parse_build_request(version)
            if version is not None
            else (newest.version_code if newest is not None else None)
        )
        build = feed.find(code, package_id) if code is not None else None
        if build is None and code is not None and self._offer.feed is not None:
            # The build may have been published since the last scheduled read.
            await self._offer.feed.async_refresh()
            if (
                self._offer.feed.last_update_success
                and self._offer.feed.data is not None
            ):
                build = self._offer.feed.data.find(code, package_id)
        if (
            self.in_progress
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
                and (
                    self._offer.feed is None
                    or self._offer.feed.verified_apk(build) is None
                )
            )
            or not self._offer.allowed(feed_release_artifact(build))
        ):
            raise _update_error(
                "update_unavailable",
                "The requested Panel Assistant update is unavailable",
            )
        return build


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

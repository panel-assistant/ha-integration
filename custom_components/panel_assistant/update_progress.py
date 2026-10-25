"""Waiting for an updated panel to come back, and what the update shows meanwhile.

Once a panel accepts an update it installs, restarts and returns on the new build.
This waits for that return, proves the exact build and its dashboard, and keeps the
update entity's versions, progress bar and availability steady through the
restart it caused.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .app_identity import PACKAGE_ID, reports_package
from .client import (
    CannotConnectError,
    HaPaneldError,
    InvalidResponseError,
    PanelInstallStatus,
    UpdateRejectedError,
    _version_key,
)
from .coordinator import HaPaneldDataUpdateCoordinator
from .device import panel_display_name
from .release import ReleaseArtifact
from .session_carrier import UpdateCarrier
from .status import home_ui_allows
from .transport_sessions import async_get_sessions
from .update_coordinator import PanelUpdateCoordinator
from .update_errors import _update_error, refusal_error

_ANDROID_DOWNLOAD_MAX_SECONDS = 10 * 60
_ANDROID_CONFIRMATION_MAX_SECONDS = 5 * 60
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


@dataclass
class _Attempt:
    """What one update shows from its start until the panel runs the target.

    The versions stay as they were when the update started, so a restart, a
    panel still answering from the old app, or a build number read late never
    shows an old or half-changed version; both change, with `in_progress`, in
    the single write that ends the attempt.
    """

    installed: str | None
    latest: str | None
    started: float = field(default_factory=lambda: _now())
    # After the panel accepts: installing, then away restarting, then back on
    # the target build until its dashboard shows. None until accepted. Names
    # the step on the dialog's line; the bar follows time alone.
    stage: str | None = None
    restart_projected: bool = False


_STAGES = ("installing", "away", "back")
# The bar follows elapsed time on one curve, so no step can make it jump.
# Fifty measured fleet updates (2026-09/10) took 75 s at the median, 112 s at
# the 90th percentile and 262 s at most; this pace puts those at about 60 %,
# 74 % and 92 %, and the bar never passes 95 % until the update ends.
_PROGRESS_PACE_SECONDS = 75
_PROGRESS_REDRAW = timedelta(seconds=2)


def _now() -> float:
    return time.monotonic()


def _confirmation_completion_deadline(
    operation: PanelInstallStatus | None, component: str
) -> float | None:
    """Bound the panel's five-minute prompt plus installation and fresh health."""
    if (
        operation is not None
        and operation.running
        and operation.component == component
        and operation.awaiting_confirmation
    ):
        return (
            asyncio.get_running_loop().time()
            + _ANDROID_CONFIRMATION_MAX_SECONDS
            + _ANDROID_PACKAGE_INSTALL_MAX_SECONDS
            + _RESTART_HEALTH_GRACE_SECONDS
        )
    return None


class UpdateProgress:
    """One panel's update attempt, from acceptance to its proven return."""

    def __init__(
        self,
        entry_id: str,
        coordinator: HaPaneldDataUpdateCoordinator,
        update_coordinator: PanelUpdateCoordinator,
        write_state: Callable[[], None],
    ) -> None:
        self._entry_id = entry_id
        self._coordinator = coordinator
        self._update_coordinator = update_coordinator
        self._write_state = write_state
        self.attempt: _Attempt | None = None
        self._stop_redraw: Callable[[], None] | None = None

    def begin(
        self, hass: HomeAssistant, installed: str | None, latest: str | None
    ) -> None:
        """Fix what the update shows until the panel runs the target build."""
        if self.attempt is None:
            self.attempt = _Attempt(installed, latest)
            self._stop_redraw = async_track_time_interval(
                hass, self._redraw, _PROGRESS_REDRAW
            )

    def end(self) -> None:
        self.attempt = None
        if self._stop_redraw is not None:
            self._stop_redraw()
            self._stop_redraw = None

    @callback
    def _redraw(self, _now: datetime) -> None:
        self._write_state()

    def enter(self, stage: str, *, write: bool = True) -> None:
        """Advance the update stage; stages never go back within a delivery."""
        attempt = self.attempt
        if attempt is None or (
            attempt.stage is not None
            and _STAGES.index(stage) <= _STAGES.index(attempt.stage)
        ):
            return
        attempt.stage = stage
        if write:
            self._write_state()

    def hold_through_restart(self, hass: HomeAssistant) -> None:
        """Show an accepted update's absent app as restarting, once.

        Called before any state write a failed poll causes, so the entity
        never shows Unavailable for the restart it asked for.
        """
        attempt = self.attempt
        if (
            attempt is None
            or attempt.stage is None
            or attempt.restart_projected
            or self._coordinator.available
        ):
            return
        attempt.restart_projected = True
        # The write about to follow must retain availability for this restart.
        self.enter("away", write=False)
        sessions = async_get_sessions(hass)
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

    @property
    def restarting(self) -> bool:
        """The panel is away for the restart its accepted update causes.

        Held for the whole delivery, not only while the notice is retained:
        the returning panel's poll ends the notice before it is marked a
        success, and a write in between would otherwise read Unavailable.
        The delivery's own wait still bounds it.
        """
        return (
            self.attempt is not None
            and self.attempt.restart_projected
            and not self._coordinator.available
        )

    @property
    def percentage(self) -> int | None:
        """Report how far the current update has got, by time alone."""
        if self.attempt is None:
            return None
        waited = max(0.0, _now() - self.attempt.started)
        return int(95 * (1 - math.exp(-waited / _PROGRESS_PACE_SECONDS)))

    def _raise_finished_failure(
        self, hass: HomeAssistant, operation: PanelInstallStatus | None, component: str
    ) -> None:
        """Keep explicit failed results; an empty or successful slot proves nothing."""
        if (
            operation is None
            or operation.running
            or operation.component != component
            or not operation.message.startswith(
                (
                    "refused (",
                    "update not installed:",
                    "update not confirmed",
                    "install failed",
                    "install blocked",
                    "cancelled",
                    "error:",
                )
            )
        ):
            return
        entry = hass.config_entries.async_get_entry(self._entry_id)
        panel = panel_display_name(hass, entry) if entry is not None else "This panel"
        # Raise the translated action error directly. A cancellation is not a
        # version-order refusal and must never trigger the staged ADB crossover.
        raise refusal_error(panel, UpdateRejectedError(operation.message))

    async def async_wait_for_installed_version(self, hass: HomeAssistant) -> bool:
        """Poll through restart; return false when recovered outcome is unknown."""
        coordinator = self._coordinator
        starting_health = (
            coordinator.data.health if coordinator.data is not None else None
        )
        deadline = asyncio.get_running_loop().time() + _UPDATE_TIMEOUT_SECONDS
        # Retain the first prompt observation; later polls cannot renew it.
        confirmation_deadline: float | None = None
        starting_version = (
            _version_key(starting_health.version)
            if starting_health is not None
            else None
        )
        terminal_status_deadline: float | None = None
        status = None
        verified = False
        while asyncio.get_running_loop().time() < deadline:
            await coordinator.async_request_refresh()
            self.hold_through_restart(hass)
            snapshot = coordinator.data
            verified = (
                snapshot is not None
                and starting_health is not None
                and (
                    (
                        starting_version is not None
                        and (current_version := _version_key(snapshot.health.version))
                        is not None
                        and current_version > starting_version
                    )
                    or (
                        snapshot.health.version == starting_health.version
                        and starting_health.version_code is not None
                        and snapshot.health.version_code is not None
                        and snapshot.health.version_code > starting_health.version_code
                    )
                )
            )
            if coordinator.reachable and verified:
                # The new build answers; the update ends once its dashboard
                # shows, as on the staged route.
                self.enter("back")
                try:
                    home = await coordinator.client.async_get_status(home_proof=True)
                except HaPaneldError:
                    home = None
                if home is not None and home_ui_allows(home):
                    await self._update_coordinator.async_request_refresh()
                    return True
                await asyncio.sleep(_UPDATE_RECHECK_SECONDS)
                continue
            try:
                await self._update_coordinator.async_request_refresh()
                status = self._update_coordinator.data.operation
            except CannotConnectError, InvalidResponseError:
                status = None
            if confirmation_deadline is None:
                confirmation_deadline = _confirmation_completion_deadline(
                    status, "ha-paneld"
                )
                if confirmation_deadline is not None:
                    deadline = max(deadline, confirmation_deadline)
            if (
                status is not None
                and not status.running
                and status.component == "ha-paneld"
            ):
                # Without a recovered target, a finished slot cannot prove
                # failure. Allow its health hand-off before ending as unknown.
                if terminal_status_deadline is None:
                    terminal_status_deadline = min(
                        deadline,
                        asyncio.get_running_loop().time()
                        + _TERMINAL_STATUS_GRACE_SECONDS,
                    )
                if asyncio.get_running_loop().time() >= terminal_status_deadline:
                    return False
            await asyncio.sleep(_UPDATE_RECHECK_SECONDS)
        if not verified and not (
            status is not None and status.component == "ha-paneld" and status.running
        ):
            return False
        raise _update_error("update_not_complete", "The panel update did not complete")

    async def async_wait_for_build(
        self,
        hass: HomeAssistant,
        artifact: ReleaseArtifact,
        before: tuple[str, str | None],
        *,
        watch_refusal: bool = False,
        carrier: UpdateCarrier | None = None,
    ) -> tuple[int, tuple[str, str]] | None:
        """Wait for the panel to restart into exactly the committed build.

        Returns the proven build number with the version and build it was read
        for, or None when the build carries no number to prove. With
        ``watch_refusal``, a panel that finished the committed install by
        refusing it raises ``UpdateRejectedError`` with its reason at once.
        """
        coordinator = self._coordinator
        # A carrier is the panel's session, used while its address cannot be.
        session = carrier is not None
        panel: UpdateCarrier = carrier if carrier is not None else coordinator.client
        build = artifact.descriptor
        package_id = build.package_id if build else PACKAGE_ID
        loop = asyncio.get_running_loop()
        deadline = (
            loop.time()
            + _ANDROID_PACKAGE_INSTALL_MAX_SECONDS
            + _RESTART_HEALTH_GRACE_SECONDS
        )
        # Retain the first prompt observation; later polls cannot renew it.
        confirmation_deadline: float | None = None
        while loop.time() < deadline:
            await coordinator.async_request_refresh()
            self.hold_through_restart(hass)
            health = coordinator.data.health if coordinator.data else None
            if (
                (
                    # Over the session, the snapshot the session read answers.
                    coordinator.last_update_success
                    if session
                    else coordinator.reachable
                )
                and health is not None
                and (health.build, health.package) != before
                and health.version == artifact.version
                and reports_package(health.package, package_id)
            ):
                code = health.version_code
                if build is not None and code is None and not session:
                    try:
                        name, code = await coordinator.client.async_get_version_code()
                    except HaPaneldError:
                        code = None
                    else:
                        if name != health.version:
                            code = None
                if (
                    build is not None
                    and code is not None
                    and code != build.version_code
                ):
                    code = None
                if build is None or code is not None:
                    self.enter("back")
                    try:
                        status = await panel.async_get_status(home_proof=True)
                    except HaPaneldError:
                        status = None
                    if status is not None and home_ui_allows(status):
                        if code is not None:
                            return code, (health.version, health.build)
                        return None
            # The session carries no install status yet, so a refusal after
            # the commit there shows only when this wait ends.
            if watch_refusal and not session:
                try:
                    install = await coordinator.client.async_get_panel_install_status()
                except HaPaneldError:
                    install = None
                # A started install resets the message, so a refusal is final.
                if install is not None and install.message.startswith("refused ("):
                    raise UpdateRejectedError(install.message)
                self._raise_finished_failure(hass, install, "APK")
                if confirmation_deadline is None:
                    confirmation_deadline = _confirmation_completion_deadline(
                        install, "APK"
                    )
                    if confirmation_deadline is not None:
                        deadline = max(deadline, confirmation_deadline)
            await asyncio.sleep(_UPDATE_RECHECK_SECONDS)
        raise _update_error("update_not_complete", "The panel update did not complete")

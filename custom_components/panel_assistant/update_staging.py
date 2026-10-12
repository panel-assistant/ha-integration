"""Home Assistant sending a signed build to a panel: over the LAN, or over ADB.

Each delivery backs the panel up first, fetches and verifies the exact build,
stages it on the panel or replaces the installed package over authorized ADB,
re-admits it immediately before the command that changes the panel, and then
waits for the panel to return on that build.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .adb_credentials import AdbCredential
from .adb_protocol import (
    AdbInstallTarget,
    InstallAdbError,
    InstallOutcome,
    LaunchOutcome,
)
from .app_identity import LEGACY_PACKAGE_ID, reports_package
from .build_feed import BuildDownloadError, BuildFeedError, async_download_build
from .client import (
    CannotConnectError,
    HaPaneldError,
    InvalidResponseError,
    StagingUnavailableError,
    UpdateApprovalRequiredError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
)
from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
from .install_adb import (
    async_launch_installed_app,
    async_preflight_install,
    async_update_installed_apk,
)
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup
from .release import _RELEASE_SIGNER_CERTIFICATE_SHA256, ReleaseArtifact
from .session_carrier import SessionCarrier, UpdateCarrier
from .update_errors import UPDATE_LOGGER as _LOGGER
from .update_errors import _update_error, _UpdateRefusalError, _verification_error
from .update_offer import UpdateOffer
from .update_panel_install import _retry_unstarted_install
from .update_progress import UpdateProgress
from .update_route import ROUTE_ADB, ROUTE_SESSION, ROUTE_STAGED, async_adb_route

# A panel's refusal of a build its own order ranks lower (panelUpdateRefusal).
_DOWNGRADE_REFUSAL = "panel-app downgrades are not permitted"


class BuildDelivery:
    """Send one panel a signed build and follow it until the panel runs it."""

    def __init__(
        self,
        entry_id: str,
        coordinator: HaPaneldDataUpdateCoordinator,
        offer: UpdateOffer,
        progress: UpdateProgress,
        *,
        installed_code: Callable[[], int | None],
        record_code: Callable[[int, tuple[str, str]], None],
        record_route: Callable[[str, str], None],
        panel_name: Callable[[], str],
    ) -> None:
        self._entry_id = entry_id
        self._coordinator = coordinator
        self._offer = offer
        self._progress = progress
        # The running build number: read, and set once a delivery proves it.
        self._installed_code = installed_code
        self._record_code = record_code
        # Says on the entity which way this update reached the panel.
        self._record_route = record_route
        self._panel_name = panel_name
        # Set for the length of one update carried by the panel's session.
        self.session = False

    def _carrier(self, hass: HomeAssistant) -> UpdateCarrier:
        """The panel calls of the update in progress: its session, or HTTP."""
        if self.session:
            return SessionCarrier(hass, self._entry_id)
        return self._coordinator.client

    async def async_deliver_build(
        self,
        hass: HomeAssistant,
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
        if self._coordinator.identity_mismatch:
            raise _update_error("update_unavailable", "The panel identity has changed")
        if not self._offer.allowed(artifact):
            raise _update_error(
                "update_unavailable", "The requested update is unavailable"
            )
        client = self._coordinator.client
        carrier = self._carrier(hass)
        route = ROUTE_SESSION if self.session else ROUTE_STAGED
        snapshot: PanelSnapshot | None = self._coordinator.data
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
            installed_successor_code = await self._offer.async_check_successor(
                artifact, snapshot
            )
        await self._async_backup_panel(hass)
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
            await self.async_wait_for_build(hass, artifact, before, minimum_code=True)
            return True
        if apk is None:
            try:
                apk = await async_download_build(
                    async_get_clientsession(hass), artifact
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
                    await carrier.async_stage_apk(apk, migration_sha256=artifact.sha256)
                    if migration
                    else await carrier.async_stage_apk(apk)
                )
            except StagingUnavailableError:
                if fallback:
                    return False
                raise
            health = self._coordinator.data.health if self._coordinator.data else None
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
                    await carrier.async_discard_apk(staged.token)
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
                    await self._offer.async_admit(artifact)
                except HomeAssistantError:
                    with contextlib.suppress(HaPaneldError):
                        await carrier.async_discard_apk(staged.token)
                    raise
                await carrier.async_commit_apk(staged.token)

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
        self._record_route(route, artifact.tag)
        self._progress.enter("installing")
        try:
            await self.async_wait_for_build(hass, artifact, before, watch_refusal=True)
        except UpdateRejectedError as refusal:
            # A panel whose own rule refuses a higher build (its version-name
            # order predates build-number order) can only be crossed by
            # Android's installer, which orders by build number alone.
            adb = (
                await async_adb_route(
                    hass,
                    self._entry_id,
                    self._coordinator,
                    self._offer,
                    self._panel_name,
                )
                if _DOWNGRADE_REFUSAL in str(refusal)
                and descriptor is not None
                and (
                    installed := snapshot.health.version_code or self._installed_code()
                )
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
            await self.async_deliver_adb(hass, artifact, *adb, apk=apk)
        return True

    async def _async_backup_panel(self, hass: HomeAssistant) -> None:
        """Keep the existing verified backup gate for both install routes."""
        try:
            await async_store_panel_backup(
                hass,
                self._entry_id,
                self._installed_code(),
                await self._carrier(hass).async_backup_panel(),
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

    async def async_deliver_adb(
        self,
        hass: HomeAssistant,
        artifact: ReleaseArtifact,
        target: AdbInstallTarget,
        credential: AdbCredential,
        *,
        apk: bytes | None = None,
    ) -> None:
        """Send one signed replacement through the already-authorized ADB peer."""
        descriptor = artifact.descriptor
        snapshot: PanelSnapshot | None = self._coordinator.data
        if not self._offer.allowed(artifact):
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
        await self._async_backup_panel(hass)
        if apk is None:
            try:
                apk = await async_download_build(
                    async_get_clientsession(hass), artifact
                )
            except BuildDownloadError as err:
                raise _update_error(
                    "release_download_failed", "The app release could not be downloaded"
                ) from err
            except BuildFeedError as err:
                raise _verification_error(artifact) from err
        # A download or backup may take minutes. Re-prove the same panel and
        # credential immediately before replacing its installed package.
        fresh = await async_adb_route(
            hass, self._entry_id, self._coordinator, self._offer, self._panel_name
        )
        fresh_target, fresh_credential = fresh if fresh else (None, None)
        if (
            not self._offer.allowed(artifact)
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
                await hass.async_add_executor_job(file.write, apk)
                await hass.async_add_executor_job(file.flush)
                outcome = await async_update_installed_apk(
                    target,
                    credential.signer,
                    descriptor,
                    Path(file.name),
                    before_install=lambda: self._offer.async_admit(artifact),
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
        await self.async_wait_for_build(hass, artifact, before)

    async def async_wait_for_build(
        self,
        hass: HomeAssistant,
        artifact: ReleaseArtifact,
        before: tuple[str, str | None],
        *,
        minimum_code: bool = False,
        watch_refusal: bool = False,
    ) -> None:
        """Wait for exactly the committed build, then remember its number."""
        proven = await self._progress.async_wait_for_build(
            hass,
            artifact,
            before,
            minimum_code=minimum_code,
            watch_refusal=watch_refusal,
            carrier=self._carrier(hass) if self.session else None,
        )
        if proven is not None:
            self._record_code(*proven)

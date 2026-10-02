"""Process-wide orchestration for one durable clean-panel installation.

The worker deliberately stops at ``HEALTHY_UNCLAIMED``.  Config-entry
creation remains a config-flow responsibility so a background worker can never
change the integration's endpoint identity contract.  Everything else about a
healthy receipt is owned here: the finalization lease, the read-only re-proof
before an entry is created, recording the entry it became, and settling it when
an entry loads after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from hashlib import sha256
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    async_get_durable_adb_credential,
)
from .app_identity import LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, reports_package
from .client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidResponseError,
    PanelHealth,
    normalize_address,
)
from .const import ANDROID_RELEASE_DOWNLOAD_ROOT, DOMAIN
from .feed_coordinator import async_get_feed_coordinator
from .ha_url import async_offer_ha_url
from .install_adb import (
    AdbInstallTarget,
    AdbPreflight,
    AdbRootMode,
    DefiniteCleanupReason,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
    StagedApk,
    async_cleanup_staged_apk,
    async_install_staged_apk,
    async_installed_artifact_size,
    async_launch_installed_app,
    async_preflight_install,
    async_stage_apk,
    async_verify_installed_target,
)
from .install_artifacts import (
    ArtifactCustodyError,
    ArtifactErrorCode,
    async_cleanup_install_artifact,
    async_download_install_artifact,
    async_reconcile_install_artifacts,
)
from .install_artifacts import (
    InstallArtifact as CustodiedArtifact,
)
from .install_jobs import (
    InstallArtifact,
    InstallJobCleanupRequiredError,
    InstallJobError,
    InstallJobManager,
    InstallJobReceipt,
    InstallJobRevisionError,
    InstallJobStoreError,
    InstallJobTransitionError,
    InstallPhase,
    InstallResultCode,
    async_get_install_job_manager,
)
from .install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
    PinnedPanelTarget,
    async_revalidate_install_target,
)
from .migration_repair import (
    async_delete_panel_migration_incomplete,
    async_raise_panel_migration_incomplete,
)
from .release import InstallDescriptor, ReleaseArtifact, is_feed_build_tag
from .status import home_ui_allows
from .update_policy import build_allowed, prereleases_allowed

_LOGGER = logging.getLogger(__name__)
_EXECUTOR_DATA_KEY = f"{DOMAIN}.install_executor"
_EXECUTOR_LOCK_DATA_KEY = f"{DOMAIN}.install_executor_lock"
_EXECUTION_ID_DOMAIN = b"ha-paneld-install-execution-v1\0"
# The path is device-local, so one fixed slot bounds ambiguous remote residue
# without creating a cross-panel collision. Stage proves the slot absent in the
# same ADB connection before writing; a later job refuses rather than overwrites.
_REMOTE_STAGING_SLOT_ID = sha256(
    b"ha-paneld-device-local-staging-slot-v1\0"
).hexdigest()[:32]
_RELEASE_DOWNLOAD_ROOT = ANDROID_RELEASE_DOWNLOAD_ROOT
_REMOTE_STAGING_PREFIX = "/data/local/tmp/ha-paneld-install-"
_HEALTH_ATTEMPTS = 12
_HEALTH_RETRY_SECONDS = 2.0
# Installing the successor beside the legacy package starts a handover the panel
# performs itself: the legacy app keeps answering on 8888 until it quiesces, and
# for part of that window nothing answers at all. The successor is therefore
# given a longer budget, and the wait ends on the successor's own identity
# rather than on the first reply. A clean install leaves this loop on its first
# healthy answer, so the longer budget costs nothing when nothing is migrating.
_HANDOVER_HEALTH_ATTEMPTS = 90
_FINALIZER_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$", flags=re.ASCII)

_SAFE_RESUME_PHASES = frozenset(
    {
        InstallPhase.APPROVED,
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.INSTALLED,
        InstallPhase.HEALTH_CHECK,
    }
)
_RESTART_AMBIGUOUS_PHASES = frozenset(
    {InstallPhase.STAGING, InstallPhase.INSTALLING, InstallPhase.LAUNCHING}
)

_AMBIGUOUS_ADB_ERRORS = frozenset(
    {
        InstallAdbErrorCode.STAGE_AMBIGUOUS,
        InstallAdbErrorCode.STAGE_VERIFICATION_FAILED,
        InstallAdbErrorCode.INSTALL_AMBIGUOUS,
        InstallAdbErrorCode.LAUNCH_AMBIGUOUS,
        InstallAdbErrorCode.CLEANUP_AMBIGUOUS,
    }
)
_PREFLIGHT_REJECTIONS = frozenset(
    {
        InstallAdbErrorCode.TARGET_CHANGED,
        InstallAdbErrorCode.TARGET_INCOMPATIBLE,
        InstallAdbErrorCode.TARGET_NOT_CLEAN,
        InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS,
    }
)
_ARTIFACT_REJECTIONS = frozenset(
    {
        ArtifactErrorCode.DESCRIPTOR_REQUIRED,
        ArtifactErrorCode.CONTRACT_INVALID,
        ArtifactErrorCode.JOB_ID_INVALID,
        ArtifactErrorCode.PATH_INVALID,
        ArtifactErrorCode.READY_INVALID,
        ArtifactErrorCode.REDIRECT_INVALID,
        ArtifactErrorCode.HTTP_STATUS,
        ArtifactErrorCode.RESPONSE_INVALID,
        ArtifactErrorCode.TOO_LARGE,
        ArtifactErrorCode.SIZE_MISMATCH,
        ArtifactErrorCode.DIGEST_MISMATCH,
        ArtifactErrorCode.IO_FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class _FrozenExecution:
    """Runtime forms reconstructed solely from one authenticated receipt."""

    pinned: PinnedPanelTarget
    adb_target: AdbInstallTarget
    descriptor: InstallDescriptor
    release: ReleaseArtifact
    execution_id: str


def health_is_installed_app(
    health: PanelHealth, installed: InstallArtifact | InstallDescriptor
) -> bool:
    """Decide whether this health line is the app a receipt installed.

    The one definition every consumer of a receipt applies: the worker's health
    phase, flow finalization and entry recovery must never disagree about what
    counts as installed.

    The version alone stops being enough during the identity migration: both
    packages are built from one tree, so the legacy app answering mid-handover
    can carry the very version being installed. The successor always reports
    its own application id, so a successor install requires that id and never
    accepts a reply that omits it. Builds older than the migration do not report
    a package at all, which is why a legacy install still accepts its absence.
    """
    return health.version == installed.version_name and reports_package(
        health.package, installed.package_id
    )


class FinalizationOutcome(StrEnum):
    """What re-proving one healthy receipt concluded, for the flow to present."""

    # The installed app is proven. The caller keeps the lease until the entry
    # exists and it hands the entry id to async_consume_finalization.
    VERIFIED = "verified"
    # Another owner holds finalization, this owner is already verifying, or the
    # receipt is no longer waiting to be claimed.
    BUSY = "busy"
    # Transient loss: the receipt is untouched and may be finalized again.
    RETRY = "retry"
    # The panel no longer matches the receipt; that drift is now durable.
    RECOVERY_REQUIRED = "recovery_required"
    # The durable store refused the receipt.
    RECEIPT_ERROR = "receipt_error"


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    """One finalization verdict, with the health that proved a VERIFIED one."""

    outcome: FinalizationOutcome
    health: PanelHealth | None = None


@dataclass(slots=True)
class _FinalizerLease:
    """The one owner allowed to finalize a healthy receipt, and what it is doing."""

    owner: str
    verifying: bool = False
    release_requested: bool = False


class _CancellationObserved(Exception):
    """Carry a freshly read cancellation without losing its receipt revision."""

    def __init__(self, receipt: InstallJobReceipt) -> None:
        self.receipt = receipt
        super().__init__()


class _PauseJob(Exception):
    """Leave a safe durable phase resumable after local cleanup could not finish."""

    def __init__(self, *, nonrestartable: bool = False) -> None:
        self.nonrestartable = nonrestartable
        super().__init__()


class InstallExecutor:
    """Own exactly one detached background worker for each durable job."""

    def __init__(self, hass: HomeAssistant, manager: InstallJobManager) -> None:
        self._hass = hass
        self._manager = manager
        self._lock = asyncio.Lock()
        self._resume_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._finalizers: dict[str, _FinalizerLease] = {}
        # A cancelled worker may have been interrupted inside a mutation phase.
        # Never restart it in the same process, whose manager still owns the old
        # in-memory claim. A new HA process will reclaim or quarantine durably.
        self._nonrestartable_workers: set[str] = set()

    def _feed_url(self) -> URL | None:
        """Where feed builds are fetched from, when a build feed is configured."""
        coordinator = async_get_feed_coordinator(self._hass)
        return coordinator.feed_url if coordinator is not None else None

    async def async_ensure_job(self, job_id: str) -> asyncio.Task[None] | None:
        """Start or join one process-wide worker without duplicating side effects."""
        async with self._lock:
            running = self._tasks.get(job_id)
            if running is not None:
                return running
            if job_id in self._nonrestartable_workers:
                return None
            receipt = await self._manager.async_get(job_id)
            if receipt.is_terminal or receipt.phase is InstallPhase.HEALTHY_UNCLAIMED:
                return None
            task = self._hass.async_create_background_task(
                self._async_run(job_id),
                f"ha-paneld install {job_id}",
                eager_start=False,
            )
            self._tasks[job_id] = task
            task.add_done_callback(partial(self._worker_done, job_id))
            return task

    async def async_wait(self, job_id: str) -> InstallJobReceipt:
        """Wait for current progress without giving the caller worker ownership."""
        task = await self.async_ensure_job(job_id)
        if task is not None:
            await asyncio.shield(task)
        return await self._manager.async_get(job_id)

    async def async_resume_loaded_jobs(self) -> tuple[str, ...]:
        """Resume durable jobs because an existing integration entry loaded us.

        This function does not register an HA startup hook. With zero config
        entries Home Assistant does not import the custom integration, so a
        later config flow must call :meth:`async_ensure_job` to resume the job.
        """
        async with self._resume_lock:
            resumed: list[str] = []
            for receipt in await self._manager.async_list():
                if (
                    receipt.is_terminal
                    or receipt.phase is InstallPhase.HEALTHY_UNCLAIMED
                ):
                    continue
                if receipt.phase in _RESTART_AMBIGUOUS_PHASES:
                    try:
                        await self._async_claim(receipt)
                    except _PauseJob:
                        _LOGGER.warning(
                            "Install resume paused: panel=%s stage=%s job=%s",
                            receipt.target.address,
                            receipt.phase.value,
                            receipt.job_id,
                        )
                        continue
                    except InstallJobRevisionError:
                        current = await self._manager.async_get(receipt.job_id)
                        if (
                            not current.is_terminal
                            and current.phase in _RESTART_AMBIGUOUS_PHASES
                        ):
                            try:
                                await self._async_claim(current)
                            except _PauseJob:
                                _LOGGER.warning(
                                    "Install resume paused: panel=%s stage=%s job=%s",
                                    current.target.address,
                                    current.phase.value,
                                    current.job_id,
                                )
                                continue
                            except InstallJobRevisionError:
                                continue
                    # An active same-manager worker retains its claim. It must
                    # never be duplicated or have its live artifact cleaned.
                    continue
                if receipt.phase not in _SAFE_RESUME_PHASES:
                    continue
                if await self.async_ensure_job(receipt.job_id) is not None:
                    resumed.append(receipt.job_id)
            return tuple(resumed)

    async def _async_acquire_finalizer(
        self, job_id: str, owner: str
    ) -> InstallJobReceipt | None:
        """Grant one owner the exclusive HEALTHY_UNCLAIMED finalization lease.

        Returns the receipt read under the lease, or None when another owner
        holds it or the receipt is no longer waiting to be claimed.
        """
        if not isinstance(owner, str) or _FINALIZER_ID.fullmatch(owner) is None:
            raise InstallJobTransitionError
        async with self._lock:
            receipt = await self._manager.async_get(job_id)
            if receipt.phase is not InstallPhase.HEALTHY_UNCLAIMED:
                return None
            lease = self._finalizers.get(job_id)
            if lease is None:
                self._finalizers[job_id] = _FinalizerLease(owner)
                return receipt
            return receipt if lease.owner == owner else None

    def release_finalizer(self, job_id: str, owner: str) -> None:
        """Release only this owner's lease, and never mid-verification.

        Synchronous, so no cancellation can interrupt a release half-done and
        nothing has to be drained. A release asked for while this owner's own
        verification is still reading the panel takes effect when that
        verification exits, so a second finalizer never verifies beside it.
        """
        lease = self._finalizers.get(job_id)
        if lease is None or lease.owner != owner:
            return
        if lease.verifying:
            lease.release_requested = True
            return
        del self._finalizers[job_id]

    def is_finalizer_active(self, job_id: str) -> bool:
        """Tell whether any owner currently holds finalization of this receipt."""
        return job_id in self._finalizers

    async def async_verify_finalization(
        self, job_id: str, owner: str
    ) -> FinalizationResult:
        """Re-prove one healthy receipt under the lease, before an entry exists.

        Every outcome but VERIFIED releases the lease before returning, and so
        does cancellation. VERIFIED keeps it, because the entry is not added
        yet: the owner hands the lease back through async_consume_finalization,
        or through release_finalizer if it adds no entry after all.
        """
        if not isinstance(owner, str) or _FINALIZER_ID.fullmatch(owner) is None:
            return FinalizationResult(FinalizationOutcome.RECEIPT_ERROR)
        # Reserved before the first await, so a release asked for while the
        # receipt is still being read is recorded rather than lost, and the
        # panel is never contacted for an owner that has already gone.
        lease = self._finalizers.get(job_id)
        if lease is None:
            lease = self._finalizers[job_id] = _FinalizerLease(owner, verifying=True)
        elif lease.owner != owner or lease.verifying:
            return FinalizationResult(FinalizationOutcome.BUSY)
        else:
            lease.verifying = True
        keep = False
        try:
            receipt = await self._manager.async_get(job_id)
            if (
                receipt.phase is not InstallPhase.HEALTHY_UNCLAIMED
                or lease.release_requested
            ):
                return FinalizationResult(FinalizationOutcome.BUSY)
            result = await self._async_reverify(receipt)
            keep = result.outcome is FinalizationOutcome.VERIFIED
            return result
        except InstallJobError:
            return FinalizationResult(FinalizationOutcome.RECEIPT_ERROR)
        except Exception:
            _LOGGER.exception("Unexpected exception while verifying final install")
            return FinalizationResult(FinalizationOutcome.RETRY)
        finally:
            lease.verifying = False
            if not keep or lease.release_requested:
                self.release_finalizer(job_id, owner)

    async def async_consume_finalization(
        self, job_id: str, owner: str, entry_id: str
    ) -> None:
        """Record the entry a verified receipt became, then release its lease.

        The entry already exists when this runs. Nothing here may raise into
        Home Assistant, which would report the entry failed or remove it.
        """
        try:
            receipt = await self._async_acquire_finalizer(job_id, owner)
            if receipt is not None:
                await self._manager.async_transition(
                    job_id,
                    receipt.revision,
                    InstallPhase.CONSUMED,
                    result_code=InstallResultCode.ENTRY_CREATED,
                    consumed_entry_id=entry_id,
                )
        except Exception:
            _LOGGER.warning("Unable to consume a completed ha-paneld install receipt")
        finally:
            self.release_finalizer(job_id, owner)

    async def async_reconcile_entry(
        self, entry_id: str, address: str, health: PanelHealth
    ) -> None:
        """Settle a healthy receipt a loaded entry turns out to be the result of.

        A restart between entry creation and consumption leaves the receipt
        waiting to be claimed. The entry's own first health read settles it with
        the same rule the worker and the flow apply, under the same lease, so a
        flow still finalizing it keeps it.
        """
        owner = f"setup_{entry_id}"
        job_id: str | None = None
        try:
            job_id = next(
                (
                    candidate.job_id
                    for candidate in await self._manager.async_list()
                    if candidate.phase is InstallPhase.HEALTHY_UNCLAIMED
                    and candidate.target.address == address
                ),
                None,
            )
            if job_id is None:
                return
            receipt = await self._async_acquire_finalizer(job_id, owner)
            if receipt is None:
                return
            if health_is_installed_app(health, receipt.artifact):
                await self._manager.async_transition(
                    job_id,
                    receipt.revision,
                    InstallPhase.CONSUMED,
                    result_code=InstallResultCode.ENTRY_CREATED,
                    consumed_entry_id=entry_id,
                )
            else:
                await self._manager.async_transition(
                    job_id,
                    receipt.revision,
                    InstallPhase.RECOVERY_REQUIRED,
                    result_code=InstallResultCode.VERIFICATION_REQUIRED,
                    result_subcode="finalization:entry_health_mismatch",
                )
        except Exception:
            _LOGGER.warning("Unable to reconcile a durable ha-paneld install receipt")
        finally:
            if job_id is not None:
                self.release_finalizer(job_id, owner)

    async def _async_reverify(self, receipt: InstallJobReceipt) -> FinalizationResult:
        """Prove the panel still is what this receipt installed, read-only."""
        try:
            await async_revalidate_install_target(self._hass, _pinned_target(receipt))
        except InstallNetworkError as err:
            if err.code in {
                InstallNetworkErrorCode.RESOLUTION_FAILED,
                InstallNetworkErrorCode.RESOLUTION_TIMEOUT,
            }:
                return FinalizationResult(FinalizationOutcome.RETRY)
            return await self._async_reject_healthy(receipt, _result_subcode(err))

        try:
            credential = await async_get_durable_adb_credential(self._hass)
        except AdbCredentialError as err:
            return await self._async_reject_healthy(receipt, _result_subcode(err))
        except Exception:
            _LOGGER.exception("Unexpected exception while loading final ADB identity")
            return await self._async_reject_healthy(
                receipt, "finalization:credential_error"
            )
        if credential.generation_id != receipt.adb_credential_id:
            return await self._async_reject_healthy(
                receipt, "finalization:credential_changed"
            )

        stored_root_mode = receipt.preflight_root_mode
        if stored_root_mode is None:
            return await self._async_reject_healthy(
                receipt, "finalization:root_mode_missing"
            )
        adb_target = _adb_target(receipt)
        try:
            await async_verify_installed_target(
                adb_target,
                credential.signer,
                expected_root_mode=AdbRootMode(stored_root_mode),
                # The package this receipt installed, which on a migrating
                # panel is not the package that panel was running before.
                package_id=receipt.artifact.package_id,
            )
        except InstallAdbError as err:
            if err.code is InstallAdbErrorCode.TARGET_UNREACHABLE:
                return FinalizationResult(FinalizationOutcome.RETRY)
            return await self._async_reject_healthy(receipt, _result_subcode(err))

        panel = HaPaneldClient(async_get_clientsession(self._hass), adb_target.address)
        try:
            health = await panel.async_get_health()
        except CannotConnectError:
            return FinalizationResult(FinalizationOutcome.RETRY)
        except InvalidResponseError:
            return await self._async_reject_healthy(receipt, "health:invalid_response")

        if not health_is_installed_app(health, receipt.artifact):
            return await self._async_reject_healthy(receipt, "health:identity_mismatch")
        try:
            status = await panel.async_get_status(home_proof=True)
        except CannotConnectError:
            return FinalizationResult(FinalizationOutcome.RETRY)
        except InvalidResponseError:
            return await self._async_reject_healthy(receipt, "home_ui:invalid_response")
        if not home_ui_allows(status, setup=True):
            return await self._async_reject_healthy(receipt, "home_ui:not_ready")
        return FinalizationResult(FinalizationOutcome.VERIFIED, health)

    async def _async_reject_healthy(
        self, receipt: InstallJobReceipt, subcode: str
    ) -> FinalizationResult:
        """Make final verification drift durable before refusing the entry."""
        try:
            await self._manager.async_transition(
                receipt.job_id,
                receipt.revision,
                InstallPhase.RECOVERY_REQUIRED,
                result_code=InstallResultCode.VERIFICATION_REQUIRED,
                result_subcode=subcode,
            )
        except InstallJobError:
            return FinalizationResult(FinalizationOutcome.RECEIPT_ERROR)
        except Exception:
            _LOGGER.exception("Unexpected exception while rejecting install receipt")
            return FinalizationResult(FinalizationOutcome.RECEIPT_ERROR)
        return FinalizationResult(FinalizationOutcome.RECOVERY_REQUIRED)

    def _worker_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        if task.cancelled() or task.exception() is not None:
            self._nonrestartable_workers.add(job_id)

    async def _async_run(self, job_id: str) -> None:
        local_artifact: CustodiedArtifact | None = None
        staged: StagedApk | None = None
        stale_partial_removed = False
        receipt: InstallJobReceipt | None = None
        try:
            receipt = await self._manager.async_get(job_id)
            try:
                receipt = await self._async_claim(receipt)
            except InstallJobRevisionError:
                receipt = await self._manager.async_get(job_id)
                receipt = await self._async_claim(receipt)
            while (
                not receipt.is_terminal
                and receipt.phase is not InstallPhase.HEALTHY_UNCLAIMED
            ):
                if receipt.cancel_requested:
                    receipt = await self._async_cancel_requested(
                        receipt, local_artifact, staged
                    )
                    continue

                execution = _frozen_execution(receipt, self._feed_url())
                phase = receipt.phase
                if phase is InstallPhase.APPROVED:
                    receipt = await self._async_transition(
                        receipt, InstallPhase.AUTHORIZING
                    )
                elif phase is InstallPhase.AUTHORIZING:
                    try:
                        await self._async_current_credential(receipt)
                    except AdbCredentialError as err:
                        receipt = await self._async_fail(
                            receipt,
                            InstallResultCode.AUTHORIZATION_FAILED,
                            _result_subcode(err),
                        )
                    else:
                        receipt = await self._async_transition(
                            receipt, InstallPhase.PREFLIGHT
                        )
                elif phase is InstallPhase.PREFLIGHT:
                    installed_bytes = None
                    try:
                        # This is the one read that may report a panel already
                        # holding the target. A retry has to converge on a
                        # panel that is already where its owner asked for, and
                        # refusing that as unclean is what used to strand them.
                        observed = await self._async_preflight(
                            receipt, execution, admit_installed_target=True
                        )
                        if observed.target_installed:
                            installed_bytes = await self._async_installed_bytes(
                                receipt, execution, observed
                            )
                            if installed_bytes is None:
                                # The app is there at other bytes. A clean
                                # install still never replaces it, which is
                                # exactly the refusal this path always made.
                                raise InstallAdbError(
                                    InstallAdbErrorCode.TARGET_NOT_CLEAN
                                )
                    except (AdbCredentialError, InstallNetworkError) as err:
                        receipt = await self._async_fail(
                            receipt,
                            InstallResultCode.TRANSPORT_FAILED,
                            _result_subcode(err),
                        )
                    except InstallAdbError as err:
                        receipt = await self._async_fail(
                            receipt, _preflight_result(err), _result_subcode(err)
                        )
                    else:
                        if installed_bytes is None:
                            receipt = await self._async_transition(
                                receipt,
                                InstallPhase.DOWNLOADING,
                                preflight_root_mode=observed.root_mode.value,
                            )
                        else:
                            # Nothing to download, copy or install: the panel
                            # already runs these exact bytes. Launch, health
                            # and setup still run, so the job converges on the
                            # same successful outcome as a fresh install.
                            receipt = await self._async_transition(
                                receipt,
                                InstallPhase.INSTALLED,
                                preflight_root_mode=observed.root_mode.value,
                                actual_apk_bytes=installed_bytes,
                            )
                elif phase is InstallPhase.DOWNLOADING:
                    try:
                        _require_build_admission(receipt.artifact)
                        local_artifact = await async_download_install_artifact(
                            self._hass,
                            async_get_clientsession(self._hass),
                            execution.release,
                            execution.execution_id,
                        )
                        _require_local_artifact(
                            local_artifact, execution.execution_id, receipt.artifact
                        )
                    except ArtifactCustodyError as err:
                        if err.code is ArtifactErrorCode.BUSY:
                            stale_partial_removed = (
                                await self._async_clear_stale_partial(
                                    execution.execution_id,
                                    already_removed=stale_partial_removed,
                                )
                            )
                        else:
                            receipt = await self._async_cleanup_local_then_fail(
                                receipt,
                                execution.execution_id,
                                _artifact_result(err),
                                _result_subcode(err),
                            )
                    else:
                        receipt = await self._async_transition(
                            receipt,
                            InstallPhase.ARTIFACT_READY,
                            actual_apk_bytes=local_artifact.size,
                        )
                elif phase is InstallPhase.ARTIFACT_READY:
                    try:
                        if local_artifact is None:
                            local_artifact = await self._async_ensure_local_artifact(
                                receipt, execution
                            )
                        else:
                            _require_local_artifact(
                                local_artifact,
                                execution.execution_id,
                                receipt.artifact,
                            )
                    except ArtifactCustodyError as err:
                        if err.code is ArtifactErrorCode.BUSY:
                            stale_partial_removed = (
                                await self._async_clear_stale_partial(
                                    execution.execution_id,
                                    already_removed=stale_partial_removed,
                                )
                            )
                        else:
                            receipt = await self._async_cleanup_local_then_fail(
                                receipt,
                                execution.execution_id,
                                _artifact_result(err),
                                _result_subcode(err),
                            )
                    else:
                        receipt = await self._async_transition(
                            receipt, InstallPhase.REVALIDATING
                        )
                elif phase is InstallPhase.REVALIDATING:
                    try:
                        _require_build_admission(receipt.artifact)
                        if local_artifact is None:
                            local_artifact = await self._async_ensure_local_artifact(
                                receipt, execution
                            )
                        observed = await self._async_preflight(receipt, execution)
                        if observed.root_mode.value != receipt.preflight_root_mode:
                            raise InstallAdbError(
                                InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS
                            )
                    except ArtifactCustodyError as err:
                        if err.code is ArtifactErrorCode.BUSY:
                            stale_partial_removed = (
                                await self._async_clear_stale_partial(
                                    execution.execution_id,
                                    already_removed=stale_partial_removed,
                                )
                            )
                        else:
                            receipt = await self._async_cleanup_local_then_fail(
                                receipt,
                                execution.execution_id,
                                _artifact_result(err),
                                _result_subcode(err),
                            )
                    except (AdbCredentialError, InstallNetworkError) as err:
                        receipt = await self._async_cleanup_local_then_fail(
                            receipt,
                            execution.execution_id,
                            InstallResultCode.TRANSPORT_FAILED,
                            _result_subcode(err),
                        )
                    except InstallAdbError as err:
                        receipt = await self._async_cleanup_local_then_fail(
                            receipt,
                            execution.execution_id,
                            _preflight_result(err),
                            _result_subcode(err),
                        )
                    else:
                        receipt = await self._async_transition(
                            receipt, InstallPhase.STAGING
                        )
                elif phase is InstallPhase.STAGING:
                    if local_artifact is None:
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.AMBIGUOUS_MUTATION,
                            "staging:local_artifact_missing",
                        )
                        continue
                    try:
                        credential, receipt = await self._async_mutation_authority(
                            receipt, execution
                        )
                        staged = await async_stage_apk(
                            execution.adb_target,
                            credential.signer,
                            execution.descriptor,
                            _REMOTE_STAGING_SLOT_ID,
                            Path(local_artifact.path),
                            expected_root_mode=_root_mode(receipt),
                        )
                        _require_staged(staged, receipt.artifact)
                    except _CancellationObserved as err:
                        receipt = err.receipt
                    except ArtifactCustodyError as err:
                        receipt = await self._async_cleanup_local_then_fail(
                            receipt,
                            execution.execution_id,
                            InstallResultCode.ARTIFACT_REJECTED,
                            _result_subcode(err),
                        )
                    except (AdbCredentialError, InstallNetworkError) as err:
                        receipt = await self._async_cleanup_local_then_fail(
                            receipt,
                            execution.execution_id,
                            InstallResultCode.TRANSPORT_FAILED,
                            _result_subcode(err),
                        )
                    except InstallAdbError as err:
                        if err.code in _AMBIGUOUS_ADB_ERRORS:
                            receipt = await self._async_recovery(
                                receipt,
                                InstallResultCode.AMBIGUOUS_MUTATION,
                                _result_subcode(err),
                            )
                        else:
                            receipt = await self._async_cleanup_local_then_fail(
                                receipt,
                                execution.execution_id,
                                _stage_result(err),
                                _result_subcode(err),
                            )
                    else:
                        receipt = await self._async_transition(
                            receipt, InstallPhase.INSTALLING
                        )
                elif phase is InstallPhase.INSTALLING:
                    if staged is None:
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.AMBIGUOUS_MUTATION,
                            "installing:staged_artifact_missing",
                        )
                        continue
                    try:
                        credential, receipt = await self._async_mutation_authority(
                            receipt, execution
                        )

                        async def admit_install(
                            artifact: InstallArtifact = receipt.artifact,
                        ) -> None:
                            _require_build_admission(artifact)

                        outcome = await async_install_staged_apk(
                            execution.adb_target,
                            credential.signer,
                            execution.descriptor,
                            _REMOTE_STAGING_SLOT_ID,
                            expected_root_mode=_root_mode(receipt),
                            before_install=admit_install,
                        )
                    except ArtifactCustodyError:
                        # Admission refused before the package manager ran;
                        # the staged file has the same safe cleanup as refusal.
                        receipt = await self._async_install_refused(
                            receipt, execution, staged
                        )
                    except (AdbCredentialError, InstallNetworkError) as err:
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.VERIFICATION_REQUIRED,
                            _result_subcode(err),
                        )
                    except InstallAdbError as err:
                        if err.code in _AMBIGUOUS_ADB_ERRORS:
                            receipt = await self._async_recovery(
                                receipt,
                                InstallResultCode.AMBIGUOUS_MUTATION,
                                _result_subcode(err),
                            )
                        else:
                            receipt = await self._async_recovery(
                                receipt,
                                InstallResultCode.VERIFICATION_REQUIRED,
                                _result_subcode(err),
                            )
                    else:
                        if outcome is InstallOutcome.REFUSED:
                            receipt = await self._async_install_refused(
                                receipt, execution, staged
                            )
                        elif outcome is InstallOutcome.INSTALLED:
                            receipt = await self._async_transition(
                                receipt, InstallPhase.INSTALLED
                            )
                        else:
                            # Retain a fail-closed runtime fallback if the dependency
                            # ever violates this currently exhaustive enum contract.
                            receipt = await self._async_recovery(  # type: ignore[unreachable]
                                receipt,
                                InstallResultCode.AMBIGUOUS_MUTATION,
                                "installing:unexpected_outcome",
                            )
                elif phase is InstallPhase.INSTALLED:
                    staged = _staged_from_receipt(receipt)
                    try:
                        await self._async_cleanup_local(execution.execution_id)
                    except ArtifactCustodyError as err:
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.VERIFICATION_REQUIRED,
                            _result_subcode(err),
                        )
                    else:
                        receipt = await self._async_transition(
                            receipt, InstallPhase.LAUNCHING
                        )
                elif phase is InstallPhase.LAUNCHING:
                    if staged is None:
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.AMBIGUOUS_MUTATION,
                            "launching:staged_artifact_missing",
                        )
                        continue
                    receipt = await self._async_cleanup_and_launch(
                        receipt, execution, staged
                    )
                elif phase is InstallPhase.HEALTH_CHECK:
                    # One client for the whole health phase: the same panel is
                    # asked whether it is up and then, if it is, told where Home
                    # Assistant is.
                    panel = HaPaneldClient(
                        async_get_clientsession(self._hass), execution.pinned.pinned
                    )
                    health, health_subcode = await self._async_health(execution, panel)
                    if health is None or not health_is_installed_app(
                        health, receipt.artifact
                    ):
                        # Only report a handover actually seen in progress:
                        # the old app answered for itself throughout the wait.
                        # A panel that answered nothing at all has failed an
                        # install, and telling its owner it is part-migrated
                        # would send them looking for the wrong thing.
                        if (
                            receipt.artifact.package_id == SUCCESSOR_PACKAGE_ID
                            and health is not None
                            and health.package == LEGACY_PACKAGE_ID
                        ):
                            async_raise_panel_migration_incomplete(
                                self._hass,
                                receipt.job_id,
                                execution.pinned.pinned.stored_value,
                                receipt.artifact.version_name,
                            )
                        receipt = await self._async_recovery(
                            receipt,
                            InstallResultCode.VERIFICATION_REQUIRED,
                            health_subcode or "health:identity_mismatch",
                        )
                    else:
                        async_delete_panel_migration_incomplete(
                            self._hass, receipt.job_id
                        )
                        # The panel Home Assistant just installed is up and
                        # answering, and its setup wizard has not been touched.
                        # This is the earliest honest moment to tell it where
                        # Home Assistant is, so its owner is never asked for an
                        # address the installer already knew. Best-effort: an
                        # install that worked must not be failed by a
                        # convenience that did not.
                        await async_offer_ha_url(self._hass, panel)
                        receipt = await self._async_transition(
                            receipt,
                            InstallPhase.HEALTHY_UNCLAIMED,
                            health_checked_at=_now_timestamp(),
                        )
                else:
                    raise InstallJobTransitionError
        except _PauseJob as err:
            if err.nonrestartable:
                self._nonrestartable_workers.add(job_id)
            _LOGGER.warning(
                "Install worker paused panel=%s stage=%s job=%s",
                receipt.target.address if receipt else "unknown",
                receipt.phase.value if receipt else "load",
                job_id,
            )
            return
        except asyncio.CancelledError:
            self._nonrestartable_workers.add(job_id)
            raise
        except (
            InstallJobRevisionError,
            InstallJobStoreError,
            InstallJobTransitionError,
        ) as err:
            # Fresh durable authority was lost. Some transition failures can
            # occur immediately after an external mutation, so never replay
            # this worker in the same process or invent a definite outcome.
            self._nonrestartable_workers.add(job_id)
            _LOGGER.warning(
                "Install worker stopped panel=%s stage=%s subcode=job:%s job=%s",
                receipt.target.address if receipt else "unknown",
                receipt.phase.value if receipt else "load",
                type(err).__name__,
                job_id,
            )
        except Exception as err:
            self._nonrestartable_workers.add(job_id)
            _LOGGER.warning(
                "Install worker stopped panel=%s stage=%s subcode=job:unexpected "
                "error=%s: %r job=%s",
                receipt.target.address if receipt else "unknown",
                receipt.phase.value if receipt else "load",
                type(err).__name__,
                err,
                job_id,
            )
            raise

    async def _async_claim(
        self,
        receipt: InstallJobReceipt,
    ) -> InstallJobReceipt:
        """Claim once, cleaning exact local custody before terminalization."""
        try:
            return await self._manager.async_claim(receipt.job_id, receipt.revision)
        except InstallJobCleanupRequiredError:
            current = await self._manager.async_get(receipt.job_id)
            try:
                await self._async_cleanup_local(_execution_id(current))
            except ArtifactCustodyError:
                raise _PauseJob(nonrestartable=True) from None
            return await self._manager.async_claim(
                current.job_id,
                current.revision,
                cleanup_confirmed_revision=current.revision,
            )

    async def _async_preflight(
        self,
        receipt: InstallJobReceipt,
        execution: _FrozenExecution,
        *,
        admit_installed_target: bool = False,
    ) -> AdbPreflight:
        await _require_pin(self._hass, execution.pinned)
        credential = await self._async_current_credential(receipt)
        observed = await async_preflight_install(
            execution.adb_target,
            credential.signer,
            execution.descriptor,
            admit_installed_target=admit_installed_target,
        )
        _require_preflight(observed, execution.adb_target)
        return observed

    async def _async_installed_bytes(
        self,
        receipt: InstallJobReceipt,
        execution: _FrozenExecution,
        observed: AdbPreflight,
    ) -> int | None:
        """Size of the installed APK when it is this artifact, byte for byte."""
        credential = await self._async_current_credential(receipt)
        return await async_installed_artifact_size(
            execution.adb_target,
            credential.signer,
            execution.descriptor,
            expected_root_mode=observed.root_mode,
        )

    async def _async_current_credential(
        self, receipt: InstallJobReceipt
    ) -> AdbCredential:
        credential = await async_get_durable_adb_credential(self._hass)
        if credential.generation_id != receipt.adb_credential_id:
            raise AdbCredentialError
        return credential

    async def _async_mutation_authority(
        self, receipt: InstallJobReceipt, execution: _FrozenExecution
    ) -> tuple[AdbCredential, InstallJobReceipt]:
        await _require_pin(self._hass, execution.pinned)
        current = await self._manager.async_get(receipt.job_id)
        if (
            current.revision != receipt.revision
            or current.phase is not receipt.phase
            or current.executor_generation != receipt.executor_generation
        ):
            if (
                current.phase is receipt.phase
                and current.executor_generation == receipt.executor_generation
                and current.cancel_requested
            ):
                raise _CancellationObserved(current)
            raise InstallJobRevisionError
        current = await self._manager.async_verify_mutation_barrier(
            current.job_id, current.revision, current.phase
        )
        credential = await self._async_current_credential(current)
        if current.phase in {InstallPhase.STAGING, InstallPhase.INSTALLING}:
            _require_build_admission(current.artifact)
        return credential, current

    async def _async_cleanup_authority(
        self, receipt: InstallJobReceipt, execution: _FrozenExecution
    ) -> tuple[AdbCredential, InstallJobReceipt]:
        await _require_pin(self._hass, execution.pinned)
        current = await self._manager.async_get(receipt.job_id)
        if (
            current.revision != receipt.revision
            or current.phase is not receipt.phase
            or current.executor_generation != receipt.executor_generation
        ):
            raise InstallJobRevisionError
        current = await self._manager.async_verify_cleanup_barrier(
            current.job_id, current.revision, current.phase
        )
        credential = await self._async_current_credential(current)
        return credential, current

    async def _async_ensure_local_artifact(
        self, receipt: InstallJobReceipt, execution: _FrozenExecution
    ) -> CustodiedArtifact:
        _require_build_admission(receipt.artifact)
        local = await async_download_install_artifact(
            self._hass,
            async_get_clientsession(self._hass),
            execution.release,
            execution.execution_id,
        )
        _require_local_artifact(local, execution.execution_id, receipt.artifact)
        return local

    async def _async_cleanup_local(self, execution_id: str) -> None:
        await async_cleanup_install_artifact(self._hass, execution_id)

    async def _async_clear_stale_partial(
        self, execution_id: str, *, already_removed: bool
    ) -> bool:
        """Remove one exact crash partial, with no same-process cleanup loop."""
        if already_removed:
            raise _PauseJob(nonrestartable=True)
        # The process singleton cannot race another download for this job, so
        # BUSY at a safe durable phase is residue from an earlier worker.
        try:
            await self._async_cleanup_local(execution_id)
        except ArtifactCustodyError:
            raise _PauseJob from None
        return True

    async def _async_cleanup_local_then_fail(
        self,
        receipt: InstallJobReceipt,
        execution_id: str,
        result: InstallResultCode,
        subcode: str,
    ) -> InstallJobReceipt:
        try:
            await self._async_cleanup_local(execution_id)
        except ArtifactCustodyError as err:
            if receipt.phase is InstallPhase.STAGING:
                return await self._async_recovery(
                    receipt,
                    InstallResultCode.VERIFICATION_REQUIRED,
                    _result_subcode(err),
                )
            raise _PauseJob from None
        return await self._async_fail(receipt, result, subcode)

    async def _async_install_refused(
        self,
        receipt: InstallJobReceipt,
        execution: _FrozenExecution,
        staged: StagedApk,
    ) -> InstallJobReceipt:
        try:
            credential, receipt = await self._async_cleanup_authority(
                receipt, execution
            )
            await async_cleanup_staged_apk(
                execution.adb_target,
                credential.signer,
                staged,
                DefiniteCleanupReason.INSTALL_REFUSED,
                expected_root_mode=_root_mode(receipt),
            )
            await self._async_cleanup_local(execution.execution_id)
        except (
            AdbCredentialError,
            ArtifactCustodyError,
            InstallNetworkError,
        ) as err:
            return await self._async_recovery(
                receipt,
                InstallResultCode.VERIFICATION_REQUIRED,
                _result_subcode(err),
            )
        except InstallAdbError as err:
            return await self._async_recovery(
                receipt, _cleanup_result(err), _result_subcode(err)
            )
        return await self._async_fail(
            receipt, InstallResultCode.INSTALL_FAILED, "install:refused"
        )

    async def _async_cleanup_and_launch(
        self,
        receipt: InstallJobReceipt,
        execution: _FrozenExecution,
        staged: StagedApk,
    ) -> InstallJobReceipt:
        try:
            credential, receipt = await self._async_cleanup_authority(
                receipt, execution
            )
            await async_cleanup_staged_apk(
                execution.adb_target,
                credential.signer,
                staged,
                DefiniteCleanupReason.INSTALL_SUCCEEDED,
                expected_root_mode=_root_mode(receipt),
            )
        except (
            AdbCredentialError,
            InstallNetworkError,
        ) as err:
            return await self._async_recovery(
                receipt,
                InstallResultCode.VERIFICATION_REQUIRED,
                _result_subcode(err),
            )
        except InstallAdbError as err:
            return await self._async_recovery(
                receipt, _cleanup_result(err), _result_subcode(err)
            )
        try:
            credential, receipt = await self._async_mutation_authority(
                receipt, execution
            )
            outcome = await async_launch_installed_app(
                execution.adb_target,
                credential.signer,
                execution.descriptor,
                expected_root_mode=_root_mode(receipt),
            )
        except (AdbCredentialError, InstallNetworkError) as err:
            return await self._async_fail(
                receipt, InstallResultCode.LAUNCH_FAILED, _result_subcode(err)
            )
        except InstallAdbError as err:
            if err.code in _AMBIGUOUS_ADB_ERRORS:
                return await self._async_recovery(
                    receipt, InstallResultCode.AMBIGUOUS_MUTATION, _result_subcode(err)
                )
            if err.code in {
                InstallAdbErrorCode.TARGET_CHANGED,
                InstallAdbErrorCode.ROOT_MODE_CHANGED,
            }:
                return await self._async_recovery(
                    receipt,
                    InstallResultCode.VERIFICATION_REQUIRED,
                    _result_subcode(err),
                )
            return await self._async_fail(
                receipt, InstallResultCode.LAUNCH_FAILED, _result_subcode(err)
            )
        if outcome is LaunchOutcome.STARTED:
            return await self._async_transition(receipt, InstallPhase.HEALTH_CHECK)
        if outcome is LaunchOutcome.REFUSED:
            return await self._async_fail(
                receipt, InstallResultCode.LAUNCH_FAILED, "launch:refused"
            )
        # Retain a fail-closed runtime fallback if the dependency ever violates
        # this currently exhaustive enum contract.
        return await self._async_recovery(  # type: ignore[unreachable]
            receipt,
            InstallResultCode.AMBIGUOUS_MUTATION,
            "launch:unexpected_outcome",
        )

    async def _async_health(
        self, execution: _FrozenExecution, client: HaPaneldClient
    ) -> tuple[PanelHealth | None, str | None]:
        artifact = execution.descriptor
        # Only a successor install can meet a handover, and only then does an
        # answer from another app mean "not yet" rather than "the wrong app". A
        # legacy install keeps exactly the budget and the failure it had.
        handover = artifact.package_id == SUCCESSOR_PACKAGE_ID
        attempts = _HANDOVER_HEALTH_ATTEMPTS if handover else _HEALTH_ATTEMPTS
        failure_subcode = "health:unavailable"
        for attempt in range(attempts):
            last = attempt + 1 >= attempts
            try:
                await _require_pin(self._hass, execution.pinned)
                health = await client.async_get_health()
            except InstallNetworkError as err:
                return None, _result_subcode(err)
            except (CannotConnectError, InvalidResponseError) as err:
                failure_subcode = (
                    "health:invalid_response"
                    if isinstance(err, InvalidResponseError)
                    else "health:cannot_connect"
                )
                # Nothing is answering on 8888. During a handover that is the
                # legacy app having released the port before the successor
                # bound it, so it is a reason to wait rather than to fail.
                if not last:
                    await asyncio.sleep(_HEALTH_RETRY_SECONDS)
                continue
            if health_is_installed_app(health, artifact):
                try:
                    status = await client.async_get_status(home_proof=True)
                except CannotConnectError, InvalidResponseError:
                    failure_subcode = "home_ui:unavailable"
                else:
                    if home_ui_allows(status, setup=True):
                        return health, None
                    failure_subcode = "home_ui:not_ready"
                if not last:
                    await asyncio.sleep(_HEALTH_RETRY_SECONDS)
                continue
            if not handover or last:
                return health, None
            # Something healthy answered, but it is not the app just installed:
            # on a migrating panel the legacy app still owns the port.
            await asyncio.sleep(_HEALTH_RETRY_SECONDS)
        return None, failure_subcode

    async def _async_cancel_requested(
        self,
        receipt: InstallJobReceipt,
        local_artifact: CustodiedArtifact | None,
        staged: StagedApk | None,
    ) -> InstallJobReceipt:
        execution = _frozen_execution(receipt, self._feed_url())
        if receipt.phase is InstallPhase.STAGING and staged is not None:
            try:
                credential, receipt = await self._async_cleanup_authority(
                    receipt, execution
                )
                await async_cleanup_staged_apk(
                    execution.adb_target,
                    credential.signer,
                    staged,
                    DefiniteCleanupReason.CANCELLED,
                    expected_root_mode=_root_mode(receipt),
                )
            except (
                AdbCredentialError,
                InstallNetworkError,
            ) as err:
                return await self._async_recovery(
                    receipt,
                    InstallResultCode.VERIFICATION_REQUIRED,
                    _result_subcode(err),
                )
            except InstallAdbError as err:
                return await self._async_recovery(
                    receipt, _cleanup_result(err), _result_subcode(err)
                )
        if (
            local_artifact is not None
            or receipt.actual_apk_bytes is not None
            or receipt.phase is InstallPhase.DOWNLOADING
        ):
            try:
                await self._async_cleanup_local(execution.execution_id)
            except ArtifactCustodyError as err:
                if receipt.phase in {
                    InstallPhase.STAGING,
                    InstallPhase.INSTALLING,
                    InstallPhase.INSTALLED,
                    InstallPhase.LAUNCHING,
                    InstallPhase.HEALTH_CHECK,
                    InstallPhase.HEALTHY_UNCLAIMED,
                }:
                    return await self._async_recovery(
                        receipt,
                        InstallResultCode.VERIFICATION_REQUIRED,
                        _result_subcode(err),
                    )
                raise _PauseJob from None
        result = (
            InstallResultCode.CANCELLED_AFTER_STAGING_CLEANUP
            if receipt.phase is InstallPhase.STAGING
            else InstallResultCode.CANCELLED_BY_USER
        )
        return await self._async_transition(
            receipt, InstallPhase.CANCELLED, result_code=result
        )

    async def _async_transition(
        self,
        receipt: InstallJobReceipt,
        phase: InstallPhase,
        *,
        actual_apk_bytes: int | None = None,
        health_checked_at: str | None = None,
        result_code: InstallResultCode | None = None,
        preflight_root_mode: str | None = None,
    ) -> InstallJobReceipt:
        try:
            root_mode = (
                {}
                if preflight_root_mode is None
                else {"preflight_root_mode": preflight_root_mode}
            )
            return await self._manager.async_transition(
                receipt.job_id,
                receipt.revision,
                phase,
                actual_apk_bytes=actual_apk_bytes,
                health_checked_at=health_checked_at,
                result_code=result_code,
                **root_mode,
            )
        except InstallJobRevisionError:
            current = await self._manager.async_get(receipt.job_id)
            if (
                current.phase is receipt.phase
                and current.executor_generation == receipt.executor_generation
                and current.cancel_requested
            ):
                return current
            raise

    async def _async_fail(
        self, receipt: InstallJobReceipt, result: InstallResultCode, subcode: str
    ) -> InstallJobReceipt:
        current = await self._manager.async_get(receipt.job_id)
        if current.is_terminal:
            return current
        return await self._manager.async_transition(
            current.job_id,
            current.revision,
            InstallPhase.FAILED,
            result_code=result,
            result_subcode=subcode,
        )

    async def _async_recovery(
        self,
        receipt: InstallJobReceipt,
        result: InstallResultCode,
        subcode: str,
    ) -> InstallJobReceipt:
        current = await self._manager.async_get(receipt.job_id)
        if current.is_terminal:
            return current
        try:
            # These bytes are reproducible from the frozen signed digest and
            # are not evidence of the panel-side mutation outcome. Cleanup is
            # restricted to this receipt's derived private custody ID.
            await self._async_cleanup_local(_execution_id(current))
        except ArtifactCustodyError:
            # The mutation truth remains in its current durable phase.  A new
            # process will remove local custody before it claims an ambiguous
            # phase, so a cleanup failure can never enable same-process replay.
            raise _PauseJob(nonrestartable=True) from None
        current = await self._manager.async_get(receipt.job_id)
        if current.is_terminal:
            return current
        return await self._manager.async_transition(
            current.job_id,
            current.revision,
            InstallPhase.RECOVERY_REQUIRED,
            result_code=result,
            result_subcode=subcode,
        )


def _now_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _execution_id(receipt: InstallJobReceipt) -> str:
    digest = sha256()
    digest.update(_EXECUTION_ID_DOMAIN)
    digest.update(bytes.fromhex(receipt.job_id))
    digest.update(bytes.fromhex(receipt.plan_sha256))
    return digest.hexdigest()[:32]


def _descriptor(artifact: InstallArtifact) -> InstallDescriptor:
    return InstallDescriptor(
        schema=artifact.descriptor_schema,
        release_tag=artifact.release_tag,
        version_name=artifact.version_name,
        version_code=artifact.version_code,
        apk_name=artifact.apk_name,
        apk_size=artifact.apk_size,
        apk_sha256=artifact.apk_sha256,
        package_id=artifact.package_id,
        signer_certificate_sha256=artifact.signer_certificate_sha256,
        min_sdk=artifact.min_sdk,
        supported_abis=artifact.supported_abis,
        database_compatibility=artifact.database_compatibility,
        launch_component=artifact.launch_component,
    )


def _apk_url(receipt: InstallJobReceipt, feed_url: URL | None) -> str:
    """Rebuild the download URL from the receipt, never from stored input.

    A feed build is fetched from the configured build feed. If no feed is
    configured any more, the empty URL is refused by custody and the job fails
    rather than fetching from anywhere else.
    """
    artifact = receipt.artifact
    if is_feed_build_tag(artifact.release_tag):
        if feed_url is None:
            return ""
        return str(feed_url.join(URL(f"apks/{artifact.apk_name}")))
    return f"{_RELEASE_DOWNLOAD_ROOT}/{artifact.release_tag}/{artifact.apk_name}"


def _pinned_target(receipt: InstallJobReceipt) -> PinnedPanelTarget:
    """Reconstruct the already-validated LAN pin from a durable receipt."""
    return PinnedPanelTarget(
        original=normalize_address(receipt.target.address),
        pinned=normalize_address(receipt.target.pinned_address),
    )


def _adb_target(receipt: InstallJobReceipt) -> AdbInstallTarget:
    """Reconstruct the exact ADB identity a durable receipt was approved for."""
    return AdbInstallTarget(
        address=normalize_address(receipt.target.pinned_address),
        serial=receipt.target.adb_serial,
        model=receipt.target.model,
        primary_abi=receipt.target.primary_abi,
        android_sdk=receipt.target.android_sdk,
    )


def _frozen_execution(
    receipt: InstallJobReceipt, feed_url: URL | None = None
) -> _FrozenExecution:
    descriptor = _descriptor(receipt.artifact)
    execution_id = _execution_id(receipt)
    return _FrozenExecution(
        pinned=_pinned_target(receipt),
        adb_target=_adb_target(receipt),
        descriptor=descriptor,
        release=ReleaseArtifact(
            tag=receipt.artifact.release_tag,
            version=receipt.artifact.version_name,
            apk_name=receipt.artifact.apk_name,
            apk_url=_apk_url(receipt, feed_url),
            sha256=receipt.artifact.apk_sha256,
            descriptor=descriptor,
            protocol_min=receipt.artifact.protocol_min,
            protocol_max=receipt.artifact.protocol_max,
        ),
        execution_id=execution_id,
    )


def _require_build_admission(artifact: InstallArtifact) -> None:
    """Recheck the running PA policy before retrieving or mutating an APK."""
    if not build_allowed(
        artifact.version_name,
        artifact.protocol_min,
        artifact.protocol_max,
        allow_prerelease=prereleases_allowed() or artifact.prerelease_opt_in,
    ):
        raise ArtifactCustodyError(ArtifactErrorCode.CONTRACT_INVALID)


async def _require_pin(hass: HomeAssistant, pinned: PinnedPanelTarget) -> None:
    revalidated = await async_revalidate_install_target(hass, pinned)
    if revalidated != pinned:
        raise InstallNetworkError(InstallNetworkErrorCode.PINNED_TARGET_REMOVED)


def _require_preflight(observed: AdbPreflight, target: AdbInstallTarget) -> None:
    if (
        not isinstance(observed, AdbPreflight)
        or observed.serial != target.serial
        or observed.model != target.model
        or observed.primary_abi != target.primary_abi
        or observed.android_sdk != target.android_sdk
        or observed.root_mode
        not in {AdbRootMode.ROOT_ADBD, AdbRootMode.ROOTLESS, AdbRootMode.ROOT_SU}
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)


def _require_local_artifact(
    local: CustodiedArtifact, execution_id: str, artifact: InstallArtifact
) -> None:
    if (
        not isinstance(local, CustodiedArtifact)
        or local.job_id != execution_id
        or local.size != artifact.apk_size
        or local.sha256 != artifact.apk_sha256
        or not isinstance(local.path, str)
        or not local.path
    ):
        raise ArtifactCustodyError(ArtifactErrorCode.READY_INVALID)


def _require_staged(staged: StagedApk, artifact: InstallArtifact) -> None:
    expected = _staged(artifact)
    if staged != expected:
        raise InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED)


def _staged(artifact: InstallArtifact) -> StagedApk:
    return StagedApk(
        job_id=_REMOTE_STAGING_SLOT_ID,
        remote_path=f"{_REMOTE_STAGING_PREFIX}{_REMOTE_STAGING_SLOT_ID}.apk",
        apk_size=artifact.apk_size,
        apk_sha256=artifact.apk_sha256,
    )


def _staged_from_receipt(receipt: InstallJobReceipt) -> StagedApk:
    if receipt.phase is not InstallPhase.INSTALLED:
        raise InstallJobTransitionError
    return _staged(receipt.artifact)


def _root_mode(receipt: InstallJobReceipt) -> AdbRootMode:
    stored_root_mode = receipt.preflight_root_mode
    if stored_root_mode is None:
        raise InstallJobStoreError
    try:
        return AdbRootMode(stored_root_mode)
    except ValueError:
        raise InstallJobStoreError from None


def _preflight_result(error: InstallAdbError) -> InstallResultCode:
    if error.code in _PREFLIGHT_REJECTIONS:
        return InstallResultCode.PREFLIGHT_REJECTED
    return InstallResultCode.TRANSPORT_FAILED


def _stage_result(error: InstallAdbError) -> InstallResultCode:
    if error.code in {
        InstallAdbErrorCode.INVALID_REQUEST,
        InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID,
        InstallAdbErrorCode.FILESYNC_UNSAFE,
        InstallAdbErrorCode.STAGING_PATH_OCCUPIED,
    }:
        return InstallResultCode.ARTIFACT_REJECTED
    return InstallResultCode.TRANSPORT_FAILED


def _cleanup_result(error: InstallAdbError) -> InstallResultCode:
    if error.code is InstallAdbErrorCode.CLEANUP_AMBIGUOUS:
        return InstallResultCode.AMBIGUOUS_MUTATION
    return InstallResultCode.VERIFICATION_REQUIRED


def _artifact_result(error: ArtifactCustodyError) -> InstallResultCode:
    if error.code in _ARTIFACT_REJECTIONS:
        return InstallResultCode.ARTIFACT_REJECTED
    return InstallResultCode.TRANSPORT_FAILED


def _result_subcode(
    error: AdbCredentialError
    | InstallNetworkError
    | InstallAdbError
    | ArtifactCustodyError,
) -> str:
    if isinstance(error, AdbCredentialError):
        return "credential:unavailable"
    if isinstance(error, InstallNetworkError):
        return f"network:{error.code.value}"
    if isinstance(error, InstallAdbError):
        return f"adb:{error.code.value}"
    return f"artifact:{error.code.value}"


async def async_get_install_executor(hass: HomeAssistant) -> InstallExecutor:
    """Return the one installer task owner for this Home Assistant process."""
    lock = hass.data.get(_EXECUTOR_LOCK_DATA_KEY)
    if lock is None:
        lock = asyncio.Lock()
        hass.data[_EXECUTOR_LOCK_DATA_KEY] = lock
    if not isinstance(lock, asyncio.Lock):
        raise InstallJobStoreError
    async with lock:
        executor = hass.data.get(_EXECUTOR_DATA_KEY)
        if executor is None:
            manager = await async_get_install_job_manager(hass)
            receipts = await manager.async_list()
            await async_reconcile_install_artifacts(
                hass,
                frozenset(
                    _execution_id(receipt)
                    for receipt in receipts
                    if not receipt.is_terminal
                ),
            )
            executor = InstallExecutor(hass, manager)
            hass.data[_EXECUTOR_DATA_KEY] = executor
        if not isinstance(executor, InstallExecutor):
            raise InstallJobStoreError
        return executor


async def async_resume_loaded_install_jobs(hass: HomeAssistant) -> tuple[str, ...]:
    """Resume safe jobs only after an existing config entry loaded the domain."""
    return await (await async_get_install_executor(hass)).async_resume_loaded_jobs()

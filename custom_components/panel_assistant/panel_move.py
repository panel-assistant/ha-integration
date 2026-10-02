"""Move a panel from the old ha-paneld app to the new one, driven from here.

The app's application id changed, and to Android a new id is a new app. A panel
that still runs the old id is moved by Panel Assistant over network ADB, never
by the panel handing itself over: that handover needed a root helper some
panels cannot hold. The order is fixed by what keeps the panel usable:

1. back the old app up and keep the backup here, verified, as the receipt;
2. install the new app beside it without starting it;
3. point HOME at the new app when HOME was the old one, confirmed by a fresh
   query, so removing the old app can never leave the panel without a launcher;
4. stop and remove the old app;
5. clear the new app and start it: with no old app beside it and no state of its
   own it starts as an ordinary app rather than waiting for a handover;
6. restore the receipt onto it, twice: the first restore gives it the panel's
   id, and only a restore onto that id returns the panel's own local state;
7. adopt the new app's identity for this entry.

The steps up to 4 are recorded on the entry as they complete, so a move that is
interrupted after the old app is gone keeps its Repair and a retry resumes from
the restore. Every step observes its result before the next one runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from secrets import token_hex
from tempfile import NamedTemporaryFile
from typing import Any, Final
from zipfile import BadZipFile, ZipFile

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .adb_credentials import (
    AdbCredentialError,
    async_get_adb_credential,
    async_get_durable_adb_credential,
)
from .app_identity import LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, reports_package
from .build_feed import (
    BuildDownloadError,
    BuildFeedError,
    async_download_build,
    feed_release_artifact,
)
from .client import (
    HaPaneldClient,
    HaPaneldError,
    PanelHealth,
    UpdateApprovalRequiredError,
    UpdateBusyError,
)
from .const import DOMAIN
from .device import panel_display_name
from .feed_coordinator import (
    async_get_feed_coordinator,
    async_get_stable_release_coordinator,
)
from .identity import adopt_moved_identity
from .install_adb import (
    AdbInstallTarget,
    DefiniteCleanupReason,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
    MoveObservation,
    MoveStep,
    async_cleanup_staged_apk,
    async_install_staged_apk,
    async_launch_installed_app,
    async_move_step,
    async_preflight_install,
    async_stage_apk,
)
from .install_network import InstallNetworkError, async_pin_install_target
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup
from .provisioning import InstallTargetState, async_probe_install_target
from .release import ReleaseArtifact

_LOGGER = logging.getLogger(__name__)

ISSUE_MOVE_TO_NEW_APP: Final = "move_to_new_app"
ISSUE_DATA_ENTRY_ID: Final = "entry_id"
#: The entry's record of a move whose old app is already gone.
CONF_SUCCESSOR_MOVE: Final = "successor_move"

_HEALTH_WAIT_SECONDS = 120.0
_RESTORE_WAIT_SECONDS = 120.0
_POLL_SECONDS = 3.0

# Abort reasons, each a translated message in the Repair.
REASON_ENTRY_REMOVED = "entry_removed"
REASON_ALREADY_MOVED = "already_moved"
REASON_RELEASE_UNAVAILABLE = "release_unavailable"
REASON_ADB_AUTHORIZATION = "adb_authorization"
REASON_ADB_UNREACHABLE = "adb_unreachable"
REASON_BACKUP_FAILED = "backup_failed"
REASON_NEW_APP_IS_HOME = "new_app_is_home"
REASON_MOVE_FAILED = "move_failed"
REASON_RESTORE_APPROVAL = "restore_approval"


class MoveError(Exception):
    """The move stopped; ``reason`` names the message the person sees."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def move_issue_id(entry_id: str) -> str:
    """Return the one move Repair of this entry."""
    return f"{ISSUE_MOVE_TO_NEW_APP}_{entry_id}"


def _needs_move(entry: ConfigEntry, health: PanelHealth | None) -> bool:
    if isinstance(entry.data.get(CONF_SUCCESSOR_MOVE), dict):
        return True
    return health is not None and reports_package(health.package, LEGACY_PACKAGE_ID)


@callback
def async_evaluate_successor_move(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer the move for a panel on the old app id, and withdraw it after."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "coordinator", None)
    snapshot = getattr(coordinator, "data", None)
    health = snapshot.health if snapshot is not None else None
    if health is None and not isinstance(entry.data.get(CONF_SUCCESSOR_MOVE), dict):
        # An offline panel keeps whatever it was last offered.
        return
    if not _needs_move(entry, health):
        ir.async_delete_issue(hass, DOMAIN, move_issue_id(entry.entry_id))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        move_issue_id(entry.entry_id),
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_MOVE_TO_NEW_APP,
        translation_placeholders={"panel": panel_display_name(hass, entry)},
        data={ISSUE_DATA_ENTRY_ID: entry.entry_id},
    )


def _successor_artifact(hass: HomeAssistant) -> ReleaseArtifact | None:
    """The new app's signed APK: the configured build feed first, else stable."""
    feed = async_get_feed_coordinator(hass)
    if feed is not None:
        build = feed.verified_newest(SUCCESSOR_PACKAGE_ID)
        if build is not None:
            return feed_release_artifact(build)
    stable = async_get_stable_release_coordinator(hass).data
    if (
        stable is not None
        and stable.descriptor is not None
        and stable.descriptor.package_id == SUCCESSOR_PACKAGE_ID
    ):
        return stable
    return None


def verify_move_receipt(data: bytes) -> str:
    """Prove a backup is the old app's whole state; return its panel id.

    The receipt is what the new app is restored from after the old app is
    removed, so beyond a readable archive it must name the old app, carry the
    device's discovery id and hold app state rows.
    """
    try:
        with ZipFile(BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    except (BadZipFile, OSError, ValueError, KeyError) as err:
        raise PanelBackupInvalidError from err
    state = manifest.get("state") if isinstance(manifest, dict) else None
    panel_id = manifest.get("panel_id") if isinstance(manifest, dict) else None
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("rows"), int)
        or state["rows"] <= 0
        or manifest.get("package") != LEGACY_PACKAGE_ID
        or not isinstance(manifest.get("discovery_id"), str)
        or not isinstance(panel_id, str)
        or not panel_id
    ):
        raise PanelBackupInvalidError
    return panel_id


async def _async_target(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[AdbInstallTarget, Any]:
    """Pin the panel's address and reach it with Panel Assistant's ADB key."""
    try:
        await async_get_adb_credential(hass)
        credential = await async_get_durable_adb_credential(hass)
        pinned = await async_pin_install_target(hass, entry.runtime_data.client.address)
    except (AdbCredentialError, InstallNetworkError, HaPaneldError) as err:
        raise MoveError(REASON_ADB_UNREACHABLE) from err
    probe = await async_probe_install_target(pinned.pinned, credential.signer)
    if probe.state is InstallTargetState.ADB_UNAUTHORIZED:
        raise MoveError(REASON_ADB_AUTHORIZATION)
    if probe.state not in {
        InstallTargetState.INSTALLED,
        InstallTargetState.MIGRATION_CANDIDATE,
    } or None in (probe.serial, probe.model, probe.primary_abi, probe.android_sdk):
        raise MoveError(REASON_ADB_UNREACHABLE)
    assert probe.serial is not None and probe.model is not None
    assert probe.primary_abi is not None and probe.android_sdk is not None
    return (
        AdbInstallTarget(
            address=pinned.pinned,
            serial=probe.serial,
            model=probe.model,
            primary_abi=probe.primary_abi,
            android_sdk=probe.android_sdk,
        ),
        credential.signer,
    )


async def _async_step(
    target: AdbInstallTarget, signer: Any, step: MoveStep
) -> MoveObservation:
    try:
        return await async_move_step(target, signer, step)
    except InstallAdbError as err:
        if err.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED:
            raise MoveError(REASON_ADB_AUTHORIZATION) from err
        raise MoveError(REASON_MOVE_FAILED) from err


async def _async_health(
    entry: ConfigEntry, accept: Callable[[PanelHealth], bool], seconds: float
) -> PanelHealth:
    client: HaPaneldClient = entry.runtime_data.client
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        try:
            health = await client.async_get_health()
        except HaPaneldError:
            health = None
        if health is not None and accept(health):
            return health
        if asyncio.get_running_loop().time() >= deadline:
            raise MoveError(REASON_MOVE_FAILED)
        await asyncio.sleep(_POLL_SECONDS)


async def _async_install_successor(
    hass: HomeAssistant, target: AdbInstallTarget, signer: Any
) -> ReleaseArtifact:
    artifact = _successor_artifact(hass)
    if artifact is None or artifact.descriptor is None:
        raise MoveError(REASON_RELEASE_UNAVAILABLE)
    try:
        apk = await async_download_build(async_get_clientsession(hass), artifact)
    except (BuildDownloadError, BuildFeedError) as err:
        raise MoveError(REASON_RELEASE_UNAVAILABLE) from err
    descriptor = artifact.descriptor
    try:
        admitted = await async_preflight_install(target, signer, descriptor)
        if not admitted.migration_candidate:
            raise MoveError(REASON_MOVE_FAILED)
        job_id = token_hex(16)
        with NamedTemporaryFile(prefix="panel-assistant-move-", suffix=".apk") as file:
            await hass.async_add_executor_job(file.write, apk)
            await hass.async_add_executor_job(file.flush)
            staged = await async_stage_apk(
                target,
                signer,
                descriptor,
                job_id,
                Path(file.name),
                expected_root_mode=admitted.root_mode,
            )
        outcome = await async_install_staged_apk(
            target, signer, descriptor, job_id, expected_root_mode=admitted.root_mode
        )
        await async_cleanup_staged_apk(
            target,
            signer,
            staged,
            DefiniteCleanupReason.INSTALL_SUCCEEDED
            if outcome is InstallOutcome.INSTALLED
            else DefiniteCleanupReason.INSTALL_REFUSED,
            expected_root_mode=admitted.root_mode,
        )
    except InstallAdbError as err:
        if err.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED:
            raise MoveError(REASON_ADB_AUTHORIZATION) from err
        raise MoveError(REASON_MOVE_FAILED) from err
    if outcome is not InstallOutcome.INSTALLED:
        raise MoveError(REASON_MOVE_FAILED)
    return artifact


async def _async_retire_legacy(
    hass: HomeAssistant, entry: ConfigEntry, target: AdbInstallTarget, signer: Any
) -> None:
    """Steps 1 to 4: receipt, new app installed, HOME safe, old app removed."""
    observed = await _async_step(target, signer, MoveStep.OBSERVE)
    if observed.successor_installed:
        if observed.home == SUCCESSOR_PACKAGE_ID:
            # The new app already owns HOME beside the old one: a part-finished
            # self-handover this release does not repair.
            raise MoveError(REASON_NEW_APP_IS_HOME)
        # Its state is a copy of the running old app's, taken by a handover
        # that never finished; the old app still holds the original.
        observed = await _async_step(target, signer, MoveStep.REMOVE_SUCCESSOR)
        if observed.successor_installed:
            raise MoveError(REASON_MOVE_FAILED)
    if not observed.legacy_installed:
        raise MoveError(REASON_MOVE_FAILED)

    client = entry.runtime_data.client
    try:
        legacy = await client.async_get_health()
        data = await client.async_backup_panel()
        panel_id = verify_move_receipt(data)
        receipt = await async_store_panel_backup(
            hass, entry.entry_id, legacy.version_code, data
        )
    except (HaPaneldError, PanelBackupInvalidError, OSError) as err:
        raise MoveError(REASON_BACKUP_FAILED) from err
    if not reports_package(legacy.package, LEGACY_PACKAGE_ID):
        raise MoveError(REASON_MOVE_FAILED)

    await _async_install_successor(hass, target, signer)
    observed = await _async_step(target, signer, MoveStep.OBSERVE)
    if not observed.successor_installed:
        raise MoveError(REASON_MOVE_FAILED)
    if observed.home == LEGACY_PACKAGE_ID:
        observed = await _async_step(target, signer, MoveStep.CLAIM_HOME)
        if observed.home != SUCCESSOR_PACKAGE_ID:
            raise MoveError(REASON_MOVE_FAILED)
    elif observed.home is None:
        # Nothing single answers HOME. Removing the old app could leave the
        # chooser or nothing; claim it rather than guess.
        observed = await _async_step(target, signer, MoveStep.CLAIM_HOME)
        if observed.home != SUCCESSOR_PACKAGE_ID:
            raise MoveError(REASON_MOVE_FAILED)

    # From here the old app is going; record what the rest needs first.
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_SUCCESSOR_MOVE: {
                "receipt": str(receipt.path),
                "sha256": receipt.sha256,
                "panel_id": panel_id,
                "config_hash": legacy.config_hash,
            },
        },
    )
    observed = await _async_step(target, signer, MoveStep.RETIRE_LEGACY)
    if observed.legacy_installed or not observed.successor_installed:
        raise MoveError(REASON_MOVE_FAILED)


async def _async_restore(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: AdbInstallTarget,
    signer: Any,
    record: dict[str, Any],
    *,
    resumed: bool,
) -> PanelHealth:
    """Steps 5 and 6: start the new app clean and restore the receipt twice.

    A resumed move keeps a new app that already serves: it was started clean
    by the attempt that got this far.
    """
    path = Path(record["receipt"])
    try:
        data = await hass.async_add_executor_job(path.read_bytes)
    except OSError as err:
        raise MoveError(REASON_MOVE_FAILED) from err
    if sha256(data).hexdigest() != record.get("sha256"):
        raise MoveError(REASON_MOVE_FAILED)

    client: HaPaneldClient = entry.runtime_data.client
    health: PanelHealth | None
    try:
        health = await client.async_get_health()
    except HaPaneldError:
        health = None
    serving = health is not None and reports_package(
        health.package, SUCCESSOR_PACKAGE_ID
    )
    if serving and record.get("restored") is True:
        assert health is not None
        return health
    if not (resumed and serving):
        await _async_start_clean(hass, entry, target, signer)
    restored = await _async_restore_twice(entry, data, record)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_SUCCESSOR_MOVE: {**record, "restored": True}},
    )
    return restored


async def _async_start_clean(
    hass: HomeAssistant, entry: ConfigEntry, target: AdbInstallTarget, signer: Any
) -> None:
    """Start the new app with no state of its own and no old app beside it."""
    await _async_step(target, signer, MoveStep.RESET_SUCCESSOR)
    artifact = _successor_artifact(hass)
    if artifact is None or artifact.descriptor is None:
        raise MoveError(REASON_RELEASE_UNAVAILABLE)
    try:
        admitted = await async_preflight_install(
            target, signer, artifact.descriptor, admit_installed_target=True
        )
        launched = await async_launch_installed_app(
            target,
            signer,
            artifact.descriptor,
            expected_root_mode=admitted.root_mode,
        )
    except InstallAdbError as err:
        raise MoveError(REASON_MOVE_FAILED) from err
    if launched is not LaunchOutcome.STARTED:
        raise MoveError(REASON_MOVE_FAILED)
    await _async_health(
        entry,
        lambda found: reports_package(found.package, SUCCESSOR_PACKAGE_ID),
        _HEALTH_WAIT_SECONDS,
    )


async def _async_restore_twice(
    entry: ConfigEntry, data: bytes, record: dict[str, Any]
) -> PanelHealth:
    """Restore the receipt, then again once the panel has adopted its id.

    A restore returns the panel's own local state only onto a panel already
    carrying the id the backup names, which the first restore gives it.
    """
    client: HaPaneldClient = entry.runtime_data.client
    panel_id = record["panel_id"]
    moved: PanelHealth | None = None
    for wanted in (
        lambda found: found.panel_id == panel_id,
        lambda found: (
            found.panel_id == panel_id
            and found.config_hash == record.get("config_hash")
        ),
    ):
        await _async_send_restore(client, data)
        moved = await _async_health(entry, wanted, _RESTORE_WAIT_SECONDS)
    assert moved is not None
    return moved


async def _async_send_restore(client: HaPaneldClient, data: bytes) -> None:
    """Start one restore, waiting out an operation the panel is still running.

    The panel adopts the backup's id before the restore that gave it has
    finished, so the next restore can find that one still holding the lane.
    """
    deadline = asyncio.get_running_loop().time() + _RESTORE_WAIT_SECONDS
    while True:
        try:
            await client.async_restore_panel(data)
        except UpdateApprovalRequiredError as err:
            raise MoveError(REASON_RESTORE_APPROVAL) from err
        except UpdateBusyError as err:
            if asyncio.get_running_loop().time() >= deadline:
                raise MoveError(REASON_MOVE_FAILED) from err
            await asyncio.sleep(_POLL_SECONDS)
            continue
        except HaPaneldError as err:
            raise MoveError(REASON_MOVE_FAILED) from err
        return


async def async_move_to_new_app(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move one panel to the new app; safe to run again after any failure."""
    record = entry.data.get(CONF_SUCCESSOR_MOVE)
    if not isinstance(record, dict):
        snapshot = entry.runtime_data.coordinator.data
        if snapshot is None or not reports_package(
            snapshot.health.package, LEGACY_PACKAGE_ID
        ):
            raise MoveError(REASON_ALREADY_MOVED)
        if _successor_artifact(hass) is None:
            raise MoveError(REASON_RELEASE_UNAVAILABLE)
        target, signer = await _async_target(hass, entry)
        await _async_retire_legacy(hass, entry, target, signer)
        record = entry.data[CONF_SUCCESSOR_MOVE]
        resumed = False
    else:
        target, signer = await _async_target(hass, entry)
        resumed = True
    health = await _async_restore(hass, entry, target, signer, record, resumed=resumed)
    did = health.discovery_id
    if (
        did is not None
        and did != entry.unique_id
        and not adopt_moved_identity(hass, entry, did)
    ):
        raise MoveError(REASON_MOVE_FAILED)
    data = dict(entry.data)
    data.pop(CONF_SUCCESSOR_MOVE, None)
    hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, move_issue_id(entry.entry_id))
    _LOGGER.info(
        "Moved %s to %s; restored configuration %s the old app's",
        entry.title,
        SUCCESSOR_PACKAGE_ID,
        "matches"
        if health.config_hash == record.get("config_hash")
        else "differs from",
    )


class SuccessorMoveFlow(RepairsFlow):
    """One click: move a panel from the old app to the new one."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id
        self._task: asyncio.Task[None] | None = None
        self._failure: str | None = None

    def _entry(self) -> ConfigEntry | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry if entry is not None and entry.domain == DOMAIN else None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm_move()

    async def async_step_confirm_move(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason=REASON_ENTRY_REMOVED)
        panel = panel_display_name(self.hass, entry)
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_move",
                data_schema=vol.Schema({}),
                description_placeholders={"panel": panel},
            )
        return await self.async_step_moving()

    async def _async_run(self, entry: ConfigEntry) -> None:
        try:
            await async_move_to_new_app(self.hass, entry)
        except MoveError as err:
            _LOGGER.warning(
                "Moving %s to the new app stopped: %s (%r)",
                entry.title,
                err,
                err.__cause__,
            )
            self._failure = err.reason
        except Exception:
            _LOGGER.exception("Moving %s to the new app failed", entry.title)
            self._failure = REASON_MOVE_FAILED

    async def async_step_moving(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason=REASON_ENTRY_REMOVED)
        if self._task is None:
            self._task = self.hass.async_create_task(
                self._async_run(entry), f"move {entry.title} to the new app"
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="moving",
                progress_action="moving",
                progress_task=self._task,
                description_placeholders={"panel": entry.title},
            )
        return self.async_show_progress_done(next_step_id="finished")

    async def async_step_finished(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        entry = self._entry()
        panel = entry.title if entry is not None else ""
        if self._failure is not None:
            return self.async_abort(
                reason=self._failure, description_placeholders={"panel": panel}
            )
        return self.async_create_entry(data={})

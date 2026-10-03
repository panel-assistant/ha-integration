"""Move a panel from the old ha-paneld app to the new one, driven from here.

The app's application id changed, and to Android a new id is a new app. A panel
that still runs the old id is moved by Panel Assistant over network ADB, never
by the panel handing itself over: that handover needed a root helper some
panels cannot hold. The order is fixed by what keeps the panel usable:

1. back the old app up and keep the backup here, verified, as the receipt;
2. install the new app beside it without starting it;
3. keep a copy of the old app's APK on the panel;
4. point HOME at the new app when HOME was the old one, confirmed by a fresh
   query, so setting the old app aside can never leave the panel without a
   launcher;
5. set the old app aside: uninstalled for the panel's user, so the new app
   cannot see it, while Android keeps its data;
6. clear the new app and start it: with no old app visible and no state of its
   own it starts as an ordinary app rather than waiting for a handover;
7. restore the receipt onto it, twice: the first restore gives it the panel's
   id, and only a restore onto that id returns the panel's own local state;
8. only once the restore succeeded and the new app reports the panel's
   configuration, remove the old app, its data and the copy;
9. adopt the new app's identity for this entry.

If the restore fails, the old app is reinstalled from its copy onto its kept
data, HOME returns to it, the new app is removed and the old app is started:
the panel is left as it was. Before the old app is set aside, the receipt's
location, the copy's digest and the identities it is bound to are written
durably beside the backups. Every run decides from what the panel reports now,
never from what the record expected: an old app still installed is set aside
again, a new app alone is restored from the record, and a record is deleted
only once the restore has finished and the new app reports the panel's
configuration under a valid identity. Every destructive command first proves
the device at the address is the one the move started on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict
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
    _version_key,
    is_valid_discovery_id,
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
    AdbRootMode,
    DefiniteCleanupReason,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
    MoveObservation,
    MoveStep,
    async_cleanup_staged_apk,
    async_install_staged_apk,
    async_installed_artifact_size,
    async_launch_installed_app,
    async_move_step,
    async_preflight_install,
    async_stage_apk,
)
from .install_network import InstallNetworkError, async_pin_install_target
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup
from .provisioning import InstallTargetState, async_probe_install_target
from .release import InstallDescriptor, ReleaseArtifact
from .update_policy import build_allowed, prereleases_allowed, version_not_older

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
REASON_PANEL_CHANGED = "panel_changed"
REASON_BUSY = "busy"

DATA_PANEL_OPERATIONS: Final = "panel_operations"


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


async def async_recover_moved_identity(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Before setup checks identity, finish adopting a finished move's identity.

    Adoption saves the entity registry and the config entry separately. When
    only the registry reached disk, the entry comes back with the old identity
    over entities already keyed to the new one, and setup would refuse it; the
    durable record names the identity this integration adopted, so it is
    adopted again here, without a reload, before any check runs.
    """
    record = await hass.async_add_executor_job(
        _read_record_quietly, _record_path(hass, entry.entry_id)
    )
    adopted = record.get("new_did") if record is not None else None
    if (
        record is not None
        and isinstance(adopted, str)
        and entry.unique_id == record.get("legacy_did")
    ):
        adopt_moved_identity(hass, entry, adopted, reload=False)


async def async_restore_move_offer(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer an unfinished move again after a restart, from its durable record.

    The entry's own copy of the offer is saved a moment after it is written,
    so a Core that stopped in that moment knows of the move only from disk.
    """
    record = await hass.async_add_executor_job(
        _read_record_quietly, _record_path(hass, entry.entry_id)
    )
    adopted = record.get("new_did") if record is not None else None
    if isinstance(adopted, str):
        # The move finished. Once its identity is on disk the record goes;
        # an entry that came back with the old identity adopts it again.
        if entry.unique_id == adopted:
            await hass.async_add_executor_job(
                _settle_record, hass, entry.entry_id, adopted
            )
        return
    if record is not None and not isinstance(entry.data.get(CONF_SUCCESSOR_MOVE), dict):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_SUCCESSOR_MOVE: {"panel_id": record["panel_id"]}},
        )


def _read_record_quietly(path: Path) -> dict[str, Any] | None:
    try:
        return _read_record(path)
    except MoveError:
        # Unreadable is still unfinished: keep offering the Repair, which
        # reports the problem when it runs.
        return {"panel_id": ""}


def _successor_artifact(
    hass: HomeAssistant, entry: ConfigEntry
) -> ReleaseArtifact | None:
    """The compatible new app on this panel's current PA-managed channel."""
    snapshot = entry.runtime_data.coordinator.data
    if snapshot is None:
        return None
    feed = async_get_feed_coordinator(hass)
    candidates: list[ReleaseArtifact] = []
    if feed is not None and feed.last_update_success and feed.data:
        candidates.extend(
            feed_release_artifact(build)
            for build in feed.data.builds
            if build.package_id == SUCCESSOR_PACKAGE_ID
        )
    candidates.extend(
        async_get_stable_release_coordinator(hass).candidates_for(SUCCESSOR_PACKAGE_ID)
    )
    return max(
        (
            artifact
            for artifact in candidates
            if artifact.descriptor is not None
            and artifact.descriptor.package_id == SUCCESSOR_PACKAGE_ID
            and _admissible(entry, artifact, snapshot.health)
        ),
        key=lambda artifact: (
            _version_key(artifact.version),
            artifact.descriptor.version_code if artifact.descriptor else 0,
        ),
        default=None,
    )


#: The app_state namespaces a restore writes back onto the panel that made the
#: backup: the panel's ``StateBackupPolicy`` DEVICE_LOCAL set. A restore that
#: writes fewer of the receipt's rows in them than it carries is incomplete.
_DEVICE_LOCAL_NAMESPACES: Final = frozenset(
    {
        "controller-state",
        "auto-sleep-learning",
        "profile-calibration",
        "performance-binding",
        "shizuku-consent",
        "power-safety-acknowledgement",
        "wifi-stability",
    }
)


def verify_move_receipt(data: bytes) -> tuple[str, str, int]:
    """Prove a backup is the old app's whole state.

    Return its panel id, its device's discovery id and how many of its state
    rows a restore onto the same panel must write back.

    The receipt is what the new app is restored from after the old app is
    removed, so beyond a readable archive it must name the old app, carry the
    device's discovery id and hold app state rows.
    """
    try:
        with ZipFile(BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            entry = (
                manifest.get("state", {}).get("entry")
                if isinstance(manifest, dict)
                and isinstance(manifest.get("state"), dict)
                else None
            )
            rows = (
                archive.read(entry).decode("utf-8").splitlines()
                if isinstance(entry, str)
                else []
            )
    except (BadZipFile, OSError, ValueError, KeyError) as err:
        raise PanelBackupInvalidError from err
    carried = sum(
        1
        for row in rows
        if len(fields := row.split("\t")) > 4 and fields[1] in _DEVICE_LOCAL_NAMESPACES
    )
    state = manifest.get("state") if isinstance(manifest, dict) else None
    panel_id = manifest.get("panel_id") if isinstance(manifest, dict) else None
    discovery_id = manifest.get("discovery_id") if isinstance(manifest, dict) else None
    if (
        not isinstance(state, dict)
        or not isinstance(state.get("rows"), int)
        or state["rows"] <= 0
        or manifest.get("package") != LEGACY_PACKAGE_ID
        or not isinstance(discovery_id, str)
        or not is_valid_discovery_id(discovery_id)
        or not isinstance(panel_id, str)
        or not panel_id
    ):
        raise PanelBackupInvalidError
    return panel_id, discovery_id, carried


def _record_path(hass: HomeAssistant, entry_id: str) -> Path:
    return Path(hass.config.path(DOMAIN, "backups", f"{entry_id}-move.json"))


def _write_record(path: Path, record: dict[str, Any]) -> None:
    """Write the record and make it durable before anything is removed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.partial")
    with partial.open("w", encoding="utf-8") as file:
        json.dump(record, file)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(partial, 0o600)
    os.replace(partial, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError, ValueError:
        # A record that cannot be read must not be mistaken for no move at all.
        raise MoveError(REASON_MOVE_FAILED) from None
    keys = ("receipt", "sha256", "panel_id", "config_hash", "legacy_did", "serial")
    if not isinstance(record, dict) or not all(
        isinstance(record.get(key), str) for key in keys
    ):
        raise MoveError(REASON_MOVE_FAILED)
    return record


async def _async_save_record(
    hass: HomeAssistant, entry: ConfigEntry, record: dict[str, Any]
) -> None:
    """Keep the record on disk first; the entry only carries the offer."""
    await hass.async_add_executor_job(
        _write_record, _record_path(hass, entry.entry_id), record
    )
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_SUCCESSOR_MOVE: {"panel_id": record["panel_id"]}},
    )


async def _async_withdraw_offer(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The move is done: stop offering it. The durable record stays until the
    adopted identity is on disk (``async_restore_move_offer``)."""
    data = dict(entry.data)
    if data.pop(CONF_SUCCESSOR_MOVE, None) is not None:
        hass.config_entries.async_update_entry(entry, data=data)
    ir.async_delete_issue(hass, DOMAIN, move_issue_id(entry.entry_id))


def _saved_unique_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """The entry's identity as Home Assistant last wrote it to disk."""
    try:
        stored = json.loads(
            Path(hass.config.path(".storage", "core.config_entries")).read_text(
                encoding="utf-8"
            )
        )
    except OSError, ValueError:
        return None
    for item in stored.get("data", {}).get("entries", []):
        if isinstance(item, dict) and item.get("entry_id") == entry_id:
            unique_id = item.get("unique_id")
            return unique_id if isinstance(unique_id, str) else None
    return None


def _settle_record(hass: HomeAssistant, entry_id: str, adopted: str) -> bool:
    """Delete a finished move's record once its identity is durably saved."""
    if _saved_unique_id(hass, entry_id) != adopted:
        return False
    _record_path(hass, entry_id).unlink(missing_ok=True)
    return True


def _operations(hass: HomeAssistant) -> set[str]:
    operations: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_PANEL_OPERATIONS, set()
    )
    return operations


@callback
def claim_panel_operation(hass: HomeAssistant, entry_id: str) -> bool:
    """Reserve the panel for one app-changing operation: a move or an update."""
    operations = _operations(hass)
    if entry_id in operations:
        return False
    operations.add(entry_id)
    return True


@callback
def release_panel_operation(hass: HomeAssistant, entry_id: str) -> None:
    """End the reservation taken by claim_panel_operation."""
    _operations(hass).discard(entry_id)


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
        if err.code is InstallAdbErrorCode.TARGET_CHANGED:
            raise MoveError(REASON_PANEL_CHANGED) from err
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


async def _async_admit_successor(entry: ConfigEntry, artifact: ReleaseArtifact) -> None:
    """Recheck current policy against live installed health at each APK boundary."""
    try:
        health = await entry.runtime_data.client.async_get_health()
    except HaPaneldError as err:
        raise MoveError(REASON_RELEASE_UNAVAILABLE) from err
    if not _admissible(entry, artifact, health):
        raise MoveError(REASON_RELEASE_UNAVAILABLE)


def _admissible(
    entry: ConfigEntry, artifact: ReleaseArtifact, health: PanelHealth
) -> bool:
    """The channel admits the new app, and it is no older than the old app it
    replaces: an older app refuses the newer app's backup, and by then the
    move would already have changed the panel."""
    return build_allowed(
        artifact.version,
        artifact.protocol_min,
        artifact.protocol_max,
        allow_prerelease=prereleases_allowed(entry),
    ) and version_not_older(
        artifact.version,
        artifact.descriptor.version_code if artifact.descriptor is not None else None,
        health.version,
        health.version_code,
    )


async def _async_install_successor(
    hass: HomeAssistant, entry: ConfigEntry, target: AdbInstallTarget, signer: Any
) -> tuple[ReleaseArtifact, AdbRootMode]:
    artifact = _successor_artifact(hass, entry)
    if artifact is None or artifact.descriptor is None:
        raise MoveError(REASON_RELEASE_UNAVAILABLE)
    await _async_admit_successor(entry, artifact)
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
            await _async_admit_successor(entry, artifact)
            staged = await async_stage_apk(
                target,
                signer,
                descriptor,
                job_id,
                Path(file.name),
                expected_root_mode=admitted.root_mode,
            )
        try:
            outcome = await async_install_staged_apk(
                target,
                signer,
                descriptor,
                job_id,
                expected_root_mode=admitted.root_mode,
                before_install=lambda: _async_admit_successor(entry, artifact),
            )
        except MoveError:
            await async_cleanup_staged_apk(
                target,
                signer,
                staged,
                DefiniteCleanupReason.INSTALL_REFUSED,
                expected_root_mode=admitted.root_mode,
            )
            raise
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
        if err.code is InstallAdbErrorCode.TARGET_CHANGED:
            raise MoveError(REASON_PANEL_CHANGED) from err
        raise MoveError(REASON_MOVE_FAILED) from err
    if outcome is not InstallOutcome.INSTALLED:
        raise MoveError(REASON_MOVE_FAILED)
    return artifact, admitted.root_mode


async def _async_take_receipt(
    hass: HomeAssistant, entry: ConfigEntry, target: AdbInstallTarget
) -> dict[str, Any]:
    """Back the old app up, bound to this entry and this device."""
    client: HaPaneldClient = entry.runtime_data.client
    try:
        legacy = await client.async_get_health()
    except HaPaneldError as err:
        raise MoveError(REASON_BACKUP_FAILED) from err
    if not reports_package(legacy.package, LEGACY_PACKAGE_ID):
        raise MoveError(REASON_MOVE_FAILED)
    # The device answering must be the one this entry knows, and the backup
    # must come from that same device: a replaced panel or a stale entry
    # never has another panel's app removed on its behalf.
    bound = legacy.discovery_id or entry.unique_id
    if entry.unique_id is not None and legacy.discovery_id not in (
        None,
        entry.unique_id,
    ):
        raise MoveError(REASON_PANEL_CHANGED)
    try:
        data = await client.async_backup_panel()
        panel_id, discovery_id, carried = verify_move_receipt(data)
    except (HaPaneldError, PanelBackupInvalidError) as err:
        raise MoveError(REASON_BACKUP_FAILED) from err
    if bound is not None and discovery_id != bound:
        raise MoveError(REASON_PANEL_CHANGED)
    try:
        receipt = await async_store_panel_backup(
            hass, entry.entry_id, legacy.version_code, data
        )
    except (PanelBackupInvalidError, OSError) as err:
        raise MoveError(REASON_BACKUP_FAILED) from err
    return {
        "receipt": str(receipt.path),
        "sha256": receipt.sha256,
        "panel_id": panel_id,
        "config_hash": legacy.config_hash,
        "legacy_did": entry.unique_id or discovery_id,
        "discovery_id": discovery_id,
        "serial": target.serial,
        "carried": carried,
    }


#: Records a new app writes only once it holds the panel's state: the
#: migration finished, or it started on its own, or it restored its receipt.
_OWNED_STATE_RECORDS: Final = frozenset({"complete.v1", "step-restore.v1"})


def _disposable(observed: MoveObservation) -> bool:
    """A new app beside the old one that provably holds no state of its own.

    Android still marks it never launched, or its own migration records, read
    through the panel's root, show it has only waited for the old app's
    handover: it never finished, never started as an ordinary app and never
    restored the panel's state into itself.
    """
    if observed.successor_unlaunched:
        return True
    records = observed.successor_records
    return records is not None and not records & _OWNED_STATE_RECORDS


async def _async_set_aside_legacy(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target: AdbInstallTarget,
    signer: Any,
    observed: MoveObservation,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Steps 1 to 5: receipt, new app installed, old app kept, HOME safe, old
    app set aside.

    ``previous`` is a move this integration recorded for this device: a new app
    beside the old one is then the one it installed, even if it owns HOME.
    """
    ours = previous is not None
    if (
        observed.successor_installed
        and not ours
        and (observed.home == SUCCESSOR_PACKAGE_ID or not _disposable(observed))
    ):
        # A new app this integration did not install has run beside the old
        # one, so it may hold state of its own: a part-finished self-handover,
        # or a panel set up on the new app first. Nothing proves that state
        # disposable, so it is left exactly as it is.
        raise MoveError(REASON_NEW_APP_IS_HOME)
    # The receipt comes first, from the old app answering as the panel's owner.
    record = await _async_take_receipt(hass, entry, target)
    if ours and observed.successor_installed:
        assert previous is not None
        record.update(
            successor_descriptor=previous.get("successor_descriptor"),
            successor_root_mode=previous.get("successor_root_mode"),
        )
    if observed.successor_installed and not ours:
        # Never launched, or provably only ever waiting for a handover: its
        # only data is a copy of what the receipt above has just taken.
        observed = await _async_step(target, signer, MoveStep.REMOVE_SUCCESSOR)
        if observed.successor_installed or not observed.legacy_installed:
            raise MoveError(REASON_MOVE_FAILED)
    if not observed.successor_installed:
        artifact, root_mode = await _async_install_successor(
            hass, entry, target, signer
        )
        assert artifact.descriptor is not None
        record.update(
            successor_descriptor=asdict(artifact.descriptor),
            successor_root_mode=root_mode.value,
        )
        observed = await _async_step(target, signer, MoveStep.OBSERVE)
        if not observed.successor_installed or not observed.legacy_installed:
            raise MoveError(REASON_MOVE_FAILED)
    await _async_verify_successor(target, signer, record)
    kept = await _async_step(target, signer, MoveStep.KEEP_LEGACY)
    if (
        kept.legacy_copy is None
        or kept.legacy_copy != kept.legacy_apk
        or not kept.legacy_installed
    ):
        # A copy that is not byte for byte the installed app cannot bring it
        # back, so the old app is not touched.
        raise MoveError(REASON_MOVE_FAILED)
    # Nothing single answering HOME is claimed too: setting the old app aside
    # could otherwise leave the chooser or nothing.
    claim_home = observed.home in (LEGACY_PACKAGE_ID, None)
    record.update(
        legacy_copy=kept.legacy_copy,
        # A retry finds HOME already claimed; a rollback must still return it.
        claimed_home=claim_home
        or (previous is not None and previous.get("claimed_home") is True),
    )
    # Keep the installed identity and the copy before either HOME or the old
    # app is changed.
    await _async_save_record(hass, entry, record)
    try:
        if claim_home:
            observed = await _async_step(target, signer, MoveStep.CLAIM_HOME)
            if observed.home != SUCCESSOR_PACKAGE_ID:
                raise MoveError(REASON_MOVE_FAILED)
        observed = await _async_step(target, signer, MoveStep.SET_ASIDE_LEGACY)
        if not observed.legacy_set_aside or not observed.successor_installed:
            raise MoveError(REASON_MOVE_FAILED)
    except MoveError as err:
        if err.reason != REASON_PANEL_CHANGED:
            # The old app is still installed: give it HOME back and start it,
            # so the panel is usable while the move waits for another try.
            with contextlib.suppress(MoveError):
                observed = await _async_step(target, signer, MoveStep.OBSERVE)
                if observed.legacy_installed:
                    await _async_revive_legacy(entry, target, signer, record, observed)
        raise
    return record


async def _async_retire_legacy(target: AdbInstallTarget, signer: Any) -> None:
    """Step 8: the new app is proven, so the old app, its data and copy go."""
    observed = await _async_step(target, signer, MoveStep.RETIRE_LEGACY)
    if (
        observed.legacy_installed
        or observed.legacy_set_aside
        or observed.legacy_copy is not None
        or not observed.successor_installed
    ):
        raise MoveError(REASON_MOVE_FAILED)


async def _async_roll_back(
    entry: ConfigEntry,
    target: AdbInstallTarget,
    signer: Any,
    record: dict[str, Any],
) -> None:
    """Return the panel to the old app after the new one failed its restore.

    The old app comes back from its own copy onto the data Android kept, HOME
    returns to it before the new app goes, and it must answer as the panel
    again. Without the copy this move recorded, the panel is left set aside
    for the next attempt to resume.
    """
    observed = await _async_step(target, signer, MoveStep.OBSERVE)
    if not observed.legacy_installed:
        if not _copy_restores(observed, record):
            raise MoveError(REASON_MOVE_FAILED)
        observed = await _async_step(target, signer, MoveStep.RESTORE_LEGACY)
        if not observed.legacy_installed:
            raise MoveError(REASON_MOVE_FAILED)
    if record.get("claimed_home") is True:
        observed = await _async_step(target, signer, MoveStep.RETURN_HOME)
        if observed.home != LEGACY_PACKAGE_ID:
            raise MoveError(REASON_MOVE_FAILED)
    if observed.successor_installed:
        observed = await _async_step(target, signer, MoveStep.REMOVE_SUCCESSOR)
        if observed.successor_installed or not observed.legacy_installed:
            raise MoveError(REASON_MOVE_FAILED)
    await _async_step(target, signer, MoveStep.START_LEGACY)
    await _async_health(
        entry,
        lambda found: reports_package(found.package, LEGACY_PACKAGE_ID),
        _HEALTH_WAIT_SECONDS,
    )


async def _async_revive_legacy(
    entry: ConfigEntry,
    target: AdbInstallTarget,
    signer: Any,
    record: dict[str, Any],
    observed: MoveObservation,
) -> MoveObservation:
    """Before a fresh receipt, undo what an earlier attempt left half done.

    An attempt that stopped after taking HOME, or after stopping the old app
    to set it aside, leaves the old app installed but not answering. HOME goes
    back to it and it is started, so the panel is as it was before the move.
    """
    if record.get("claimed_home") is True and observed.home != LEGACY_PACKAGE_ID:
        observed = await _async_step(target, signer, MoveStep.RETURN_HOME)
        if observed.home != LEGACY_PACKAGE_ID:
            raise MoveError(REASON_MOVE_FAILED)
    try:
        health = await entry.runtime_data.client.async_get_health()
    except HaPaneldError:
        health = None
    if health is not None and reports_package(health.package, LEGACY_PACKAGE_ID):
        return observed
    await _async_step(target, signer, MoveStep.START_LEGACY)
    await _async_health(
        entry,
        lambda found: reports_package(found.package, LEGACY_PACKAGE_ID),
        _HEALTH_WAIT_SECONDS,
    )
    return await _async_step(target, signer, MoveStep.OBSERVE)


def _copy_restores(observed: MoveObservation, record: dict[str, Any]) -> bool:
    """The old app is set aside beside the copy this move kept of it."""
    return (
        observed.legacy_set_aside
        and observed.legacy_copy is not None
        and observed.legacy_copy == record.get("legacy_copy")
    )


async def _async_mark_roll_back(
    hass: HomeAssistant, entry: ConfigEntry, record: dict[str, Any], *, under_way: bool
) -> dict[str, Any]:
    """Keep on disk whether a rollback has started, so a stop can be finished."""
    record = {key: value for key, value in record.items() if key != "rolling_back"}
    if under_way:
        record["rolling_back"] = True
    await hass.async_add_executor_job(
        _write_record, _record_path(hass, entry.entry_id), record
    )
    return record


async def _async_verify_successor(
    target: AdbInstallTarget, signer: Any, record: dict[str, Any]
) -> tuple[InstallDescriptor, AdbRootMode]:
    """Prove the recorded installed bytes, independently of current offers."""
    try:
        stored = record["successor_descriptor"]
        descriptor = InstallDescriptor(
            **{**stored, "supported_abis": tuple(stored["supported_abis"])}
        )
        root_mode = AdbRootMode(record["successor_root_mode"])
    except (KeyError, TypeError, ValueError) as err:
        # Old unfinished records cannot identify an installed build. Keep
        # their receipt and Repair; never guess from the current catalogue.
        raise MoveError(REASON_MOVE_FAILED) from err
    if descriptor.package_id != SUCCESSOR_PACKAGE_ID:
        raise MoveError(REASON_MOVE_FAILED)
    try:
        size = await async_installed_artifact_size(
            target, signer, descriptor, expected_root_mode=root_mode
        )
    except InstallAdbError as err:
        if err.code is InstallAdbErrorCode.TARGET_CHANGED:
            raise MoveError(REASON_PANEL_CHANGED) from err
        raise MoveError(REASON_MOVE_FAILED) from err
    if size != descriptor.apk_size:
        raise MoveError(REASON_MOVE_FAILED)
    return descriptor, root_mode


def _moved(health: PanelHealth, record: dict[str, Any]) -> bool:
    """The new app answers as this panel under a real identity."""
    return bool(
        reports_package(health.package, SUCCESSOR_PACKAGE_ID)
        and health.panel_id == record["panel_id"]
        and health.installation_identity
        and health.discovery_id is not None
        and is_valid_discovery_id(health.discovery_id)
    )


def _restored(health: PanelHealth, record: dict[str, Any]) -> bool:
    """The new app runs this panel's restored configuration under a real identity."""
    return _moved(health, record) and health.config_hash == record["config_hash"]


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
    if sha256(data).hexdigest() != record["sha256"]:
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
    descriptor, root_mode = await _async_verify_successor(target, signer, record)
    if not (resumed and serving):
        await _async_step(target, signer, MoveStep.RESET_SUCCESSOR)
        try:
            launched = await async_launch_installed_app(
                target, signer, descriptor, expected_root_mode=root_mode
            )
        except InstallAdbError as err:
            if err.code is InstallAdbErrorCode.TARGET_CHANGED:
                raise MoveError(REASON_PANEL_CHANGED) from err
            raise MoveError(REASON_MOVE_FAILED) from err
        if launched is not LaunchOutcome.STARTED:
            raise MoveError(REASON_MOVE_FAILED)
        await _async_health(
            entry,
            lambda found: reports_package(found.package, SUCCESSOR_PACKAGE_ID),
            _HEALTH_WAIT_SECONDS,
        )
    await _async_restore_twice(entry, data, record)
    return await _async_settled(entry, record)


async def _async_restore_twice(
    entry: ConfigEntry, data: bytes, record: dict[str, Any]
) -> None:
    """Restore the receipt, then again once the panel has adopted its id.

    A restore returns the panel's own local state only onto a panel already
    carrying the id the backup names, which the first restore gives it.
    """
    client: HaPaneldClient = entry.runtime_data.client
    panel_id = record["panel_id"]
    await _async_send_restore(client, data)
    await _async_health(
        entry, lambda found: found.panel_id == panel_id, _RESTORE_WAIT_SECONDS
    )
    await _async_send_restore(client, data)


async def _async_settled(entry: ConfigEntry, record: dict[str, Any]) -> PanelHealth:
    """Wait for the last restore to finish whole, then prove what the panel runs.

    Health can show the restored id and configuration before the restore that
    wrote them has finished, and an ordinary restore forgives a failed write of
    the panel's own state, so the restore's own outcome must show it succeeded
    and wrote the state rows the receipt carries.
    """
    client: HaPaneldClient = entry.runtime_data.client
    deadline = asyncio.get_running_loop().time() + _RESTORE_WAIT_SECONDS
    while True:
        try:
            outcome = await client.async_get_restore_outcome()
        except HaPaneldError:
            outcome = None
        if outcome is not None:
            succeeded, rows = outcome
            carried = record.get("carried")
            if (
                not succeeded
                or not isinstance(carried, int)
                or rows < carried
                or rows <= 0 < carried
            ):
                raise MoveError(REASON_MOVE_FAILED)
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise MoveError(REASON_MOVE_FAILED)
        await asyncio.sleep(_POLL_SECONDS)
    return await _async_health(
        entry, lambda found: _restored(found, record), _RESTORE_WAIT_SECONDS
    )


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
    if not claim_panel_operation(hass, entry.entry_id):
        raise MoveError(REASON_BUSY)
    try:
        await _async_move(hass, entry)
    finally:
        release_panel_operation(hass, entry.entry_id)


async def _async_move(hass: HomeAssistant, entry: ConfigEntry) -> None:
    record = await hass.async_add_executor_job(
        _read_record, _record_path(hass, entry.entry_id)
    )
    if record is None and entry.runtime_data.coordinator.identity_mismatch:
        raise MoveError(REASON_PANEL_CHANGED)
    if record is not None and entry.unique_id not in (
        record["legacy_did"],
        None,
    ):
        # The entry already carries another identity: either this move adopted
        # the new app's and stopped before forgetting the record, or the
        # entry now names a different panel.
        await _async_finish_adopted(hass, entry, record)
        return
    if record is not None and (
        not isinstance(record.get("successor_descriptor"), dict)
        or not isinstance(record.get("successor_root_mode"), str)
    ):
        raise MoveError(REASON_MOVE_FAILED)
    target, signer = await _async_target(hass, entry)
    ours = record is not None and record["serial"] == target.serial
    if record is not None and not ours:
        raise MoveError(REASON_PANEL_CHANGED)
    # Decide from what the panel has now, never from what a record expected.
    observed = await _async_step(target, signer, MoveStep.OBSERVE)
    if (
        record is not None
        and record.get("rolling_back") is True
        and (observed.legacy_installed or _copy_restores(observed, record))
    ):
        # A rollback that stopped part way is finished first: the old app may
        # be back but not started, or HOME and the new app not yet returned,
        # and it cannot give a fresh receipt until it runs again.
        await _async_roll_back(entry, target, signer, record)
        await _async_mark_roll_back(hass, entry, record, under_way=False)
        raise MoveError(REASON_MOVE_FAILED)
    if observed.legacy_installed:
        if record is not None:
            observed = await _async_revive_legacy(
                entry, target, signer, record, observed
            )
        if not (ours and observed.successor_installed):
            artifact = _successor_artifact(hass, entry)
            if artifact is None:
                raise MoveError(REASON_RELEASE_UNAVAILABLE)
            await _async_admit_successor(entry, artifact)
        record = await _async_set_aside_legacy(
            hass, entry, target, signer, observed, record
        )
        resumed = False
    elif record is None:
        raise MoveError(
            REASON_ALREADY_MOVED if observed.successor_installed else REASON_MOVE_FAILED
        )
    elif not observed.successor_installed:
        raise MoveError(REASON_MOVE_FAILED)
    else:
        resumed = True
    try:
        if resumed and record.get("restored") is True:
            # The receipt was already restored whole: from here the move only
            # goes forward. The new app holds the panel, perhaps with settings
            # changed since, so it is never cleared, restored again or rolled
            # back; it only has to answer as this panel.
            proven: dict[str, Any] = record
            health = await _async_health(
                entry, lambda found: _moved(found, proven), _HEALTH_WAIT_SECONDS
            )
        else:
            health = await _async_restore(
                hass, entry, target, signer, record, resumed=resumed
            )
    except MoveError as err:
        if err.reason != REASON_PANEL_CHANGED and record.get("restored") is not True:
            record = await _async_mark_roll_back(hass, entry, record, under_way=True)
            try:
                await _async_roll_back(entry, target, signer, record)
                await _async_mark_roll_back(hass, entry, record, under_way=False)
            except MoveError as rollback:
                _LOGGER.warning(
                    "Returning %s to the old app stopped; the next attempt "
                    "finishes it: %s (%r)",
                    entry.title,
                    rollback,
                    rollback.__cause__,
                )
        raise
    if record.get("restored") is not True:
        record = {**record, "restored": True}
        await hass.async_add_executor_job(
            _write_record, _record_path(hass, entry.entry_id), record
        )
    await _async_retire_legacy(target, signer)
    did = health.discovery_id
    assert did is not None
    if did != entry.unique_id:
        # The entry is saved a moment after it changes, so the record keeps
        # authority, naming the identity adopted, until a later setup reads
        # the new identity back from disk.
        record = {**record, "new_did": did}
        await hass.async_add_executor_job(
            _write_record, _record_path(hass, entry.entry_id), record
        )
        if not adopt_moved_identity(hass, entry, did):
            raise MoveError(REASON_MOVE_FAILED)
    await _async_withdraw_offer(hass, entry)
    _LOGGER.info("Moved %s to %s", entry.title, SUCCESSOR_PACKAGE_ID)


async def _async_finish_adopted(
    hass: HomeAssistant, entry: ConfigEntry, record: dict[str, Any]
) -> None:
    client: HaPaneldClient = entry.runtime_data.client
    try:
        health = await client.async_get_health()
    except HaPaneldError as err:
        raise MoveError(REASON_MOVE_FAILED) from err
    if not _restored(health, record) or health.discovery_id != entry.unique_id:
        raise MoveError(REASON_PANEL_CHANGED)
    await _async_withdraw_offer(hass, entry)
    raise MoveError(REASON_ALREADY_MOVED)


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

"""One private failure report and one Repairs issue for each panel."""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ADDRESS
from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .build_feed import parse_build_request
from .client import is_newer_stable_version
from .const import DOMAIN, INTEGRATION_BUILD, INTEGRATION_VERSION
from .device import panel_display_name

if TYPE_CHECKING:
    from .install_jobs import InstallJobReceipt

ISSUE_INSTALLER_FAILURE = "installer_failure"
_STORE_KEY = f"{DOMAIN}.failure_repairs"
_DATA_KEY = f"{DOMAIN}.failure_repair_store"
_MAX_EVENTS = 16


class RetrySafetyHold(Exception):
    """The old receipt no longer authorizes a new mutation."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def panel_failure_issue_id(identity: str) -> str:
    """Use physical install identity or the update entry, not a changeable address."""
    return f"{ISSUE_INSTALLER_FAILURE}_{sha256(identity.encode()).hexdigest()[:24]}"


class _FailureStore:
    """Serialize the small private history; the issue itself holds no report."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.store = Store[dict[str, Any]](
            hass, 1, _STORE_KEY, private=True, atomic_writes=True
        )
        self.lock = asyncio.Lock()
        self.records: dict[str, dict[str, Any]] | None = None

    async def load(self) -> dict[str, dict[str, Any]]:
        if self.records is None:
            saved = await self.store.async_load()
            self.records = saved if isinstance(saved, dict) else {}
        return self.records

    async def append(
        self,
        issue_id: str,
        event: dict[str, Any],
        after_save: Callable[[], None] | None = None,
    ) -> None:
        async with self.lock:
            records = await self.load()
            previous = records.get(issue_id, {})
            events = previous.get("events", [])
            if not isinstance(events, list):
                events = []
            updated = {
                **records,
                issue_id: {"events": [*events[-(_MAX_EVENTS - 1) :], event]},
            }
            await self.store.async_save(updated)
            self.records = updated
            if after_save is not None:
                after_save()

    async def clear(
        self,
        issue_id: str,
        after_clear: Callable[[], None] | None = None,
        when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        async with self.lock:
            records = await self.load()
            if when is not None and not when(records.get(issue_id, {})):
                return
            if issue_id in records:
                updated = {
                    key: value for key, value in records.items() if key != issue_id
                }
                await self.store.async_save(updated)
                self.records = updated
            if after_clear is not None:
                after_clear()


def _failure_store(hass: HomeAssistant) -> _FailureStore:
    store = hass.data.get(_DATA_KEY)
    if isinstance(store, _FailureStore):
        return store
    store = _FailureStore(hass)
    hass.data[_DATA_KEY] = store
    return store


_INSTALL_CAUSES = frozenset(
    {
        "authorization_failed",
        "preflight_rejected",
        "artifact_rejected",
        "transport_failed",
        "install_failed",
        "launch_failed",
        "health_check_failed",
        "ambiguous_mutation",
        "verification_required",
    }
)


def _issue(
    hass: HomeAssistant, issue_id: str, panel: str, kind: str, reason: str
) -> None:
    if kind == "install" and reason in _INSTALL_CAUSES:
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_{reason}"
    elif kind == "update":
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_update"
    elif kind == "retry_hold":
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_retry_hold"
    else:
        translation_key = ISSUE_INSTALLER_FAILURE
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        translation_placeholders={"panel": panel},
        data={"key": issue_id},
    )


def record_install_failure(hass: HomeAssistant, receipt: InstallJobReceipt) -> None:
    """Record a verified terminal receipt without delaying its store commit."""
    issue_id = panel_failure_issue_id(f"install:{receipt.target.adb_serial}")
    reason = receipt.result_code.value if receipt.result_code else "install_failed"
    event = {
        "kind": "install",
        "address": receipt.target.address,
        "panel": receipt.target.address,
        "reason": reason,
        "job_id": receipt.job_id,
        "receipt": asdict(receipt),
    }
    _issue(hass, issue_id, receipt.target.address, "install", reason)
    hass.async_create_task(
        _failure_store(hass).append(issue_id, event), eager_start=False
    )


def clear_install_failure(hass: HomeAssistant, receipt: InstallJobReceipt) -> None:
    """A successful install makes its old error obsolete."""
    issue_id = panel_failure_issue_id(f"install:{receipt.target.adb_serial}")
    hass.async_create_task(
        _failure_store(hass).clear(
            issue_id, lambda: ir.async_delete_issue(hass, DOMAIN, issue_id)
        ),
        eager_start=False,
    )


async def async_record_update_failure(
    hass: HomeAssistant,
    entry_id: str,
    panel_title: str,
    target_version: str | None,
    error: BaseException,
    *,
    artifact: dict[str, Any] | None = None,
    observed_before: tuple[str, int | None] | None = None,
) -> None:
    """Retain the cause that Home Assistant's update toast otherwise loses."""
    entry = hass.config_entries.async_get_entry(entry_id)
    address = entry.data.get(CONF_ADDRESS) if entry else None
    if entry is None or not isinstance(address, str):
        return
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    panel = panel_display_name(hass, entry)
    reason = str(error)
    event: dict[str, Any] = {
        "kind": "update",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "address": address,
        "panel": panel,
        "reason": reason,
        "entry_id": entry_id,
        "target_version": target_version,
        "exception": "".join(traceback.format_exception(error))[-12000:],
    }
    if artifact is not None:
        event["artifact"] = artifact
    if observed_before is not None:
        event["observed_before"] = list(observed_before)
    await _failure_store(hass).append(
        issue_id, event, lambda: _issue(hass, issue_id, panel, "update", reason)
    )


def async_refresh_update_failure_name(hass: HomeAssistant, entry_id: str) -> None:
    """Refresh an existing Repair after its panel device name becomes known."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is None:
        return
    kind = (
        "retry_hold"
        if issue.translation_key == "installer_failure_retry_hold"
        else "update"
    )
    _issue(hass, issue_id, panel_display_name(hass, entry), kind, "")


async def async_clear_update_failure_if_installed(
    hass: HomeAssistant,
    entry_id: str,
    installed_version: str,
    installed_code: int | None,
    *,
    verified_success: bool = False,
) -> None:
    """Resolve a saved failure only after this panel reaches its failed build."""
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
        return

    def reached(record: dict[str, Any]) -> bool:
        events = record.get("events", [])
        if not isinstance(events, list):
            return False
        previous = next(
            (
                event
                for event in reversed(events)
                if isinstance(event, dict) and event.get("kind") == "update"
            ),
            None,
        )
        target = previous.get("target_version") if previous is not None else None
        if not isinstance(target, str):
            before = previous.get("observed_before") if previous is not None else None
            if isinstance(before, list) and len(before) == 2:
                old_version, old_code = before
                if isinstance(old_version, str):
                    return is_newer_stable_version(installed_version, old_version) or (
                        installed_version == old_version
                        and isinstance(old_code, int)
                        and installed_code is not None
                        and installed_code > old_code
                    )
            return verified_success
        target_code = parse_build_request(target)
        if target_code is not None:
            return installed_code is not None and installed_code >= target_code
        # A prerelease of a later version is newer than the failed stable
        # version; one of the same version has not reached that stable build.
        return installed_version == target or is_newer_stable_version(
            installed_version.split("-", 1)[0], target
        )

    await _failure_store(hass).clear(
        issue_id,
        lambda: ir.async_delete_issue(hass, DOMAIN, issue_id),
        reached,
    )


async def async_failure_events(
    hass: HomeAssistant, issue_id: str
) -> list[dict[str, Any]]:
    """Read only through the admin-gated fix flow, never through issue data."""
    store = _failure_store(hass)
    async with store.lock:
        records = await store.load()
        value = records.get(issue_id, {}).get("events", [])
        if isinstance(value, list) and value:
            return value
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is None:
        return []
    from .install_jobs import InstallPhase, async_get_install_job_manager

    manager = await async_get_install_job_manager(hass)
    receipts = await manager.async_list()
    return [
        {
            "kind": "install",
            "address": receipt.target.address,
            "panel": receipt.target.address,
            "reason": (
                receipt.result_code.value if receipt.result_code else "install_failed"
            ),
            "job_id": receipt.job_id,
            "receipt": asdict(receipt),
        }
        for receipt in receipts
        if receipt.phase in {InstallPhase.FAILED, InstallPhase.RECOVERY_REQUIRED}
        and panel_failure_issue_id(f"install:{receipt.target.adb_serial}") == issue_id
    ]


async def async_clear_failure(hass: HomeAssistant, issue_id: str) -> None:
    """Clear the stored report when the owner clears the repair."""
    await _failure_store(hass).clear(issue_id)


async def async_record_retry_hold(
    hass: HomeAssistant, issue_id: str, previous: dict[str, Any], error: Exception
) -> None:
    """Keep the repair and show why its latest Retry could not proceed."""
    reason = error.reason if isinstance(error, RetrySafetyHold) else "retry_failed"
    entry_id = previous.get("entry_id")
    entry = (
        hass.config_entries.async_get_entry(entry_id)
        if isinstance(entry_id, str)
        else None
    )
    panel = (
        panel_display_name(hass, entry)
        if entry is not None
        else str(previous.get("panel", "Panel"))
    )
    await _failure_store(hass).append(
        issue_id,
        {
            "kind": "retry_hold",
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "address": previous.get("address"),
            "panel": panel,
            "reason": reason,
            "exception": "".join(traceback.format_exception(error))[-12000:],
            "previous": previous.get("job_id") or previous.get("entry_id"),
        },
    )
    _issue(hass, issue_id, panel, "retry_hold", reason)


async def async_support_report(hass: HomeAssistant, issue_id: str) -> str:
    """Produce a copyable, unredacted report for support."""
    events = await async_failure_events(hass, issue_id)
    return "\n".join(
        (
            f"Panel Assistant {INTEGRATION_VERSION} build {INTEGRATION_BUILD}",
            f"Home Assistant {ha_version}",
            f"Repair: {issue_id}",
            json.dumps(events, indent=2, ensure_ascii=False, default=str),
        )
    )


async def async_retry_install_job(
    hass: HomeAssistant, previous: InstallJobReceipt
) -> InstallJobReceipt:
    """Create a fresh job only if the previous physical target still matches."""
    from .adb_credentials import async_get_durable_adb_credential
    from .client import normalize_address
    from .install_adb import (
        AdbInstallTarget,
        async_installed_artifact_size,
        async_preflight_install,
    )
    from .install_jobs import (
        InstallPhase,
        async_get_install_job_manager,
    )
    from .install_network import (
        async_pin_install_target,
        async_revalidate_install_target,
    )
    from .install_plan import _build_artifact, build_install_plan
    from .provisioning import InstallTargetState, async_probe_install_target
    from .release import is_feed_build_tag, is_rc_release_tag
    from .release_catalog import async_resolve_install_choice

    if previous.phase not in {InstallPhase.FAILED, InstallPhase.RECOVERY_REQUIRED}:
        raise RetrySafetyHold("retry_no_longer_needed")
    try:
        address = normalize_address(previous.target.address)
        target = await async_pin_install_target(hass, address)
        if target.pinned.stored_value != previous.target.pinned_address:
            raise RetrySafetyHold("retry_target_changed")
        target = await async_revalidate_install_target(hass, target)
        credential = await async_get_durable_adb_credential(hass)
        if credential.generation_id != previous.adb_credential_id:
            raise RetrySafetyHold("retry_credential_changed")
        probe = await async_probe_install_target(target.pinned, credential.signer)
        if probe.state not in {
            InstallTargetState.INSTALL_CANDIDATE,
            InstallTargetState.INSTALLED,
            InstallTargetState.MIGRATION_CANDIDATE,
        } or (
            probe.serial,
            probe.model,
            probe.primary_abi,
            probe.android_sdk,
        ) != (
            previous.target.adb_serial,
            previous.target.model,
            previous.target.primary_abi,
            previous.target.android_sdk,
        ):
            raise RetrySafetyHold("retry_target_changed")
        rc_tag = (
            previous.artifact.release_tag
            if is_rc_release_tag(previous.artifact.release_tag)
            or is_feed_build_tag(previous.artifact.release_tag)
            else None
        )
        release = await async_resolve_install_choice(hass, rc_tag)
        if _build_artifact(release, rc_tag) != previous.artifact:
            raise RetrySafetyHold("retry_release_changed")
        if probe.state in {
            InstallTargetState.INSTALLED,
            InstallTargetState.MIGRATION_CANDIDATE,
        }:
            assert release.descriptor is not None
            assert probe.serial is not None
            assert probe.model is not None
            assert probe.primary_abi is not None
            assert probe.android_sdk is not None
            adb_target = AdbInstallTarget(
                address=target.pinned,
                serial=probe.serial,
                model=probe.model,
                primary_abi=probe.primary_abi,
                android_sdk=probe.android_sdk,
            )
            observed = await async_preflight_install(
                adb_target,
                credential.signer,
                release.descriptor,
                admit_installed_target=True,
            )
            if not observed.target_installed:
                raise RetrySafetyHold("retry_panel_ambiguous")
            installed_bytes = await async_installed_artifact_size(
                adb_target,
                credential.signer,
                release.descriptor,
                expected_root_mode=observed.root_mode,
            )
            if installed_bytes is None:
                raise RetrySafetyHold("retry_panel_ambiguous")
            probe = replace(probe, installed_artifact_size=installed_bytes)
        plan = build_install_plan(
            target, probe, release, credential.generation_id, expected_rc_tag=rc_tag
        )
        if plan.target != previous.target or plan.artifact != previous.artifact:
            raise RetrySafetyHold("retry_target_changed")
        manager = await async_get_install_job_manager(hass)
        receipt, _created = await manager.async_create_or_join(
            plan.target, plan.artifact, plan.plan_sha256, plan.adb_credential_id
        )
        return receipt
    except RetrySafetyHold:
        raise
    except Exception as err:
        raise RetrySafetyHold("retry_cannot_prove_safe") from err

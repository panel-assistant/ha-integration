"""One private failure report and one Repairs issue for each panel."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import traceback
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ADDRESS
from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .build import INTEGRATION_BUILD
from .build_feed import parse_build_request
from .client import InvalidResponseError, is_newer_stable_version, is_version_at_least
from .const import CONF_TRANSPORT_USER_ID, DOMAIN, INTEGRATION_VERSION, help_url
from .device import panel_display_name
from .install_adb import InstallAdbTransferError
from .transport_protocol import CAPABILITY_UI_SUBSCRIBE, OUTCOME_APPLIED
from .transport_sessions import async_get_sessions, session_manages
from .transport_sessions import async_manage as async_observe_management
from .ui_relay import async_manage
from .webview import parse_webview

if TYPE_CHECKING:
    from .install_receipt import InstallJobReceipt

_LOGGER = logging.getLogger(__name__)

ISSUE_INSTALLER_FAILURE = "installer_failure"
ISSUE_ADB_AUTHORIZATION = "adb_update_authorization"
_STORE_KEY = f"{DOMAIN}.failure_repairs"
_DATA_KEY = f"{DOMAIN}.failure_repair_store"
_MAX_EVENTS = 16
_PANEL_LOG_TIMEOUT = 3.0
_MAX_PANEL_LOG_BYTES = 16 * 1024
_LOG_WARNING = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\s+\d+\s+\d+\s+[WEF]\s")


class _PanelLogSnapshot:
    """One initial logs update, delivered by the existing session relay."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.backlog: asyncio.Future[list[Any] | None] = hass.loop.create_future()

    def update(self, observations: list[dict[str, Any]]) -> None:
        for observation in observations:
            if observation.get("channel") != "logs" or self.backlog.done():
                continue
            value = observation.get("value")
            self.backlog.set_result(
                value
                if observation.get("state") == "known" and isinstance(value, list)
                else None
            )

    def session_ended(self) -> None:
        if not self.backlog.done():
            self.backlog.set_result(None)


async def _async_panel_logs(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    unavailable = {
        "status": "unavailable",
        "reason": "No panel log lines could be taken.",
    }
    session = async_get_sessions(hass).get(entry_id)
    if session is None:
        return {
            **unavailable,
            "reason": "No panel log lines could be taken: panel not connected.",
        }
    if (
        not session_manages(session)
        or CAPABILITY_UI_SUBSCRIBE not in session.capabilities
    ):
        return unavailable
    command_id = secrets.token_urlsafe(24)
    # A separate origin keeps this source and lifetime independent of any Logs page.
    origin = {"user": session.user_id, "connection": command_id}
    snapshot = _PanelLogSnapshot(hass)
    session.ui_relays[command_id] = [snapshot]
    try:
        async with asyncio.timeout(_PANEL_LOG_TIMEOUT):
            outcome, _ = await async_manage(
                hass,
                session,
                "subscribe",
                {"family": "logs", "source": "app"},
                origin,
                command_id=command_id,
            )
            if outcome.outcome != OUTCOME_APPLIED:
                return unavailable
            backlog = await snapshot.backlog
        if backlog is None:
            return unavailable
        # Keep the newest warnings, bounded even if a panel sends oversized lines.
        lines: list[str] = []
        size = 0
        truncated = len(backlog) > 400
        for line in reversed(backlog[-400:]):
            if not isinstance(line, str) or not _LOG_WARNING.match(line):
                continue
            encoded = line.encode("utf-8")
            remaining = _MAX_PANEL_LOG_BYTES - size - bool(lines)
            if len(encoded) > remaining:
                truncated = True
                if not lines:
                    lines.append(encoded[:remaining].decode("utf-8", errors="ignore"))
                break
            lines.append(line)
            size += len(encoded) + (len(lines) > 1)
        return {
            "status": "captured",
            "lines": list(reversed(lines)),
            "truncated": truncated,
        }
    except HomeAssistantError, TimeoutError:
        return unavailable
    finally:
        session.ui_relays.pop(command_id, None)
        # The command ID remains the handle even when the subscribe answer was lost.
        if async_get_sessions(hass).get(entry_id) is session:
            try:
                async with asyncio.timeout(_PANEL_LOG_TIMEOUT):
                    await async_manage(
                        hass,
                        session,
                        "unsubscribe",
                        {"subscription": command_id},
                        origin,
                    )
            except HomeAssistantError, TimeoutError:
                pass  # Optional diagnostics must not prevent saving the failure.


class RetrySafetyHold(Exception):
    """The old receipt no longer authorizes a new mutation."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def panel_failure_issue_id(identity: str) -> str:
    """Use physical install identity or the update entry, not a changeable address."""
    return f"{ISSUE_INSTALLER_FAILURE}_{sha256(identity.encode()).hexdigest()[:24]}"


def adb_authorization_issue_id(entry_id: str) -> str:
    """Keep the authorization request attached to the configured panel."""
    return f"{ISSUE_ADB_AUTHORIZATION}_{entry_id}"


def async_request_adb_authorization(hass: HomeAssistant, entry_id: str) -> None:
    """Ask for the physical approval by the panel's Home Assistant name."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return
    failed = ir.async_get(hass).async_get_issue(
        DOMAIN, panel_failure_issue_id(f"update:{entry_id}")
    )
    if failed is not None:
        _issue(
            hass,
            panel_failure_issue_id(f"update:{entry_id}"),
            panel_display_name(hass, entry),
            "update",
            "adb_authorization_required",
            entry_id,
        )
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        adb_authorization_issue_id(entry_id),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ADB_AUTHORIZATION,
        learn_more_url=help_url("repair", issue=ISSUE_ADB_AUTHORIZATION),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "approval_url": help_url("repair", issue=ISSUE_ADB_AUTHORIZATION),
            "learn_more_url": help_url("repair", issue=ISSUE_ADB_AUTHORIZATION),
        },
        data={"entry_id": entry_id},
    )


def async_clear_adb_authorization(hass: HomeAssistant, entry_id: str) -> None:
    """An admitted ADB route no longer needs an approval request."""
    ir.async_delete_issue(hass, DOMAIN, adb_authorization_issue_id(entry_id))
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    entry = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is not None
        and issue is not None
        and issue.translation_key == "installer_failure_adb_authorization"
    ):
        _issue(hass, issue_id, panel_display_name(hass, entry), "update", "")


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
        when: Callable[[], bool] | None = None,
    ) -> None:
        async with self.lock:
            records = await self.load()
            if when is not None and not when():
                return
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
        "health_unreachable",
    }
)


def _issue(
    hass: HomeAssistant,
    issue_id: str,
    panel: str,
    kind: str,
    reason: str,
    entry_id: str | None = None,
    *,
    transfer_rate: str | None = None,
) -> None:
    if kind == "install" and reason in _INSTALL_CAUSES:
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_{reason}"
    elif kind == "update" and reason == "adb_authorization_required":
        translation_key = "installer_failure_adb_authorization"
    elif kind == "update" and reason == "update_webview_unfinished":
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_update_webview"
    elif kind == "update" and transfer_rate is not None:
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_update_slow_transfer"
    elif kind == "update":
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_update"
    elif kind == "retry_hold":
        translation_key = f"{ISSUE_INSTALLER_FAILURE}_retry_hold"
    else:
        translation_key = ISSUE_INSTALLER_FAILURE
    # These update explanations share the update Repair's help page, and an
    # unreachable panel shares the health check's.
    anchor = (
        translation_key.removesuffix("_webview")
        .removesuffix("_slow_transfer")
        .replace("_health_unreachable", "_health_check_failed")
    )
    if translation_key == "installer_failure_adb_authorization" and entry_id:
        ir.async_delete_issue(hass, DOMAIN, adb_authorization_issue_id(entry_id))
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=translation_key,
        learn_more_url=help_url("repair", issue=anchor),
        translation_placeholders={
            "panel": panel,
            "repair_url": help_url("repair", issue=anchor),
            "learn_more_url": help_url("repair", issue=anchor),
            "guide_url": help_url("support-report"),
            "approval_url": help_url("repair", issue="adb_update_authorization"),
            **({"transfer_rate": transfer_rate} if transfer_rate is not None else {}),
        },
        data={"key": issue_id, **({"entry_id": entry_id} if entry_id else {})},
    )


def record_install_failure(hass: HomeAssistant, receipt: InstallJobReceipt) -> None:
    """Record a verified terminal receipt without delaying its store commit."""
    from .install_receipt import HEALTH_UNREACHABLE_SUBCODE

    issue_id = panel_failure_issue_id(f"install:{receipt.target.adb_serial}")
    reason = receipt.result_code.value if receipt.result_code else "install_failed"
    cause = (
        "health_unreachable"
        if receipt.result_subcode == HEALTH_UNREACHABLE_SUBCODE
        else reason
    )
    event = {
        "kind": "install",
        "address": receipt.target.address,
        "panel": receipt.target.address,
        "reason": reason,
        "job_id": receipt.job_id,
        "receipt": asdict(receipt),
    }
    _issue(hass, issue_id, receipt.target.address, "install", cause)
    hass.async_create_task(
        _failure_store(hass).append(issue_id, event), eager_start=False
    )


def clear_install_failure(hass: HomeAssistant, receipt: InstallJobReceipt) -> None:
    """A successful install makes its old error obsolete."""
    clear_install_failure_of(hass, receipt.target.adb_serial)


def clear_install_failure_of(hass: HomeAssistant, adb_serial: str) -> None:
    """Clear the install failure Repair of the panel with this ADB serial."""
    issue_id = panel_failure_issue_id(f"install:{adb_serial}")
    hass.async_create_task(
        _failure_store(hass).clear(
            issue_id, lambda: ir.async_delete_issue(hass, DOMAIN, issue_id)
        ),
        eager_start=False,
    )


async def async_record_update_bundle(
    hass: HomeAssistant,
    entry_id: str,
    target_version: str,
    artifact: dict[str, Any] | None,
) -> None:
    """Retain unfinished components before an app restart can interrupt the bundle."""
    events = await async_failure_events(
        hass, panel_failure_issue_id(f"update:{entry_id}")
    )
    previous = next(
        (event for event in reversed(events) if event.get("kind") == "update"), None
    )
    if previous is not None and isinstance(previous.get("target_version"), str):
        saved = previous["target_version"]
        old_code, new_code = (
            parse_build_request(saved),
            parse_build_request(target_version),
        )
        if (old_code is not None and new_code is not None and old_code > new_code) or (
            old_code is None and is_newer_stable_version(saved, target_version)
        ):
            return
    entry = hass.config_entries.async_get_entry(entry_id)
    await async_record_update_failure(
        hass,
        entry_id,
        "",
        target_version,
        RuntimeError("The combined app and WebView update is unfinished"),
        artifact=artifact,
        webview_pending=bool(entry and entry.data.get(CONF_TRANSPORT_USER_ID)),
        # A normal update is not a failure: only a startup that finds this
        # record left behind turns it into the Repair.
        raise_issue=False,
    )


async def _async_failure_webview(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Read bounded failure-time facts without submitting any provider work."""
    unavailable = {"status": "unavailable"}
    entry = hass.config_entries.async_get_entry(entry_id)
    session = async_get_sessions(hass).get(entry_id)
    if (
        entry is None
        or not session_manages(session)
        or session is None
        or session.did != entry.unique_id
        or session.user_id != entry.data.get(CONF_TRANSPORT_USER_ID)
    ):
        return unavailable
    try:
        async with asyncio.timeout(_PANEL_LOG_TIMEOUT):
            status = parse_webview(
                await async_observe_management(hass, session, "webview.observe")
            )
        if (
            hass.config_entries.async_get_entry(entry_id) is not entry
            or async_get_sessions(hass).get(entry_id) is not session
            or session.did != entry.unique_id
            or session.user_id != entry.data.get(CONF_TRANSPORT_USER_ID)
        ):
            return unavailable
        observed = {
            "reason": status.reason,
            "transaction_state": status.transaction_state,
            "transaction_reason": status.transaction_reason,
        }
        # Only a failed download names a cause (app 1.0.3 and later).
        if status.transaction_cause is not None:
            observed["transaction_cause"] = status.transaction_cause
        return observed
    except HomeAssistantError, TimeoutError, InvalidResponseError:
        return unavailable


async def async_record_update_failure(
    hass: HomeAssistant,
    entry_id: str,
    panel_title: str,
    target_version: str | None,
    error: BaseException,
    *,
    artifact: dict[str, Any] | None = None,
    observed_before: tuple[str, int | None] | None = None,
    webview_pending: bool = False,
    raise_issue: bool = True,
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
    # The update service wraps transport failures for its toast. Keep only
    # structured transfer details in the Repair, never raw exception messages.
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, InstallAdbTransferError):
            event["transfer"] = {
                "reason": cause.reason,
                "bytes_sent": cause.bytes_sent,
                "total_bytes": cause.total_bytes,
                "elapsed_seconds": cause.elapsed_seconds,
                "bytes_per_second": cause.bytes_per_second,
                "slow": cause.slow,
            }
            break
        cause = cause.__cause__ or cause.__context__
    event["panel_logs"] = await _async_panel_logs(hass, entry_id)
    if artifact is not None:
        event["artifact"] = artifact
    if observed_before is not None:
        event["observed_before"] = list(observed_before)
    if webview_pending:
        event["webview_pending"] = True
        if raise_issue:
            event["webview"] = await _async_failure_webview(hass, entry_id)
    if isinstance(cause := getattr(error, "translation_key", None), str):
        # A restart restores the same wording from the saved report.
        event["translation_key"] = cause
    await _failure_store(hass).append(
        issue_id,
        event,
        None
        if not raise_issue
        else lambda: _issue(
            hass,
            issue_id,
            panel,
            "update",
            getattr(error, "translation_key", None) or reason,
            entry_id
            if getattr(error, "translation_key", None) == "adb_authorization_required"
            else None,
            transfer_rate=_slow_transfer_rate(event),
        ),
        # An entry removed during the awaits above keeps neither report nor Repair.
        lambda: hass.config_entries.async_get_entry(entry_id) is entry,
    )


def _slow_transfer_rate(event: dict[str, Any]) -> str | None:
    transfer = event.get("transfer")
    if isinstance(transfer, dict) and transfer.get("slow") is True:
        rate = transfer.get("bytes_per_second")
        if isinstance(rate, (int, float)) and rate >= 0:
            return f"{rate / 1000:.0f}"
    return None


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
    _issue(
        hass,
        issue_id,
        panel_display_name(hass, entry),
        kind,
        "adb_authorization_required"
        if issue.translation_key == "installer_failure_adb_authorization"
        else "update_webview_unfinished"
        if issue.translation_key == "installer_failure_update_webview"
        else "",
        entry_id
        if issue.translation_key == "installer_failure_adb_authorization"
        else None,
        transfer_rate=(issue.translation_placeholders or {}).get("transfer_rate")
        if issue.translation_key == "installer_failure_update_slow_transfer"
        else None,
    )


async def async_clear_update_failure_if_installed(
    hass: HomeAssistant,
    entry_id: str,
    installed_version: str,
    installed_code: int | None,
    *,
    verified_success: bool = False,
    verified_current_code: int | None = None,
    is_current: Callable[[], bool] | None = None,
    webview_completed: bool = False,
) -> None:
    """Resolve a saved failure only after this panel reaches its failed build."""
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    # A record can exist without its Repair (an update still running, or one
    # restored at startup), so the record decides, not the registry.
    cleared_event: dict[str, Any] | None = None

    def reached(record: dict[str, Any]) -> bool:
        nonlocal cleared_event
        if is_current is not None and not is_current():
            return False
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
        cleared_event = previous
        if (
            previous is not None
            and previous.get("webview_pending")
            and not webview_completed
        ):
            return False
        target = previous.get("target_version") if previous is not None else None
        if not isinstance(target, str):
            if previous is None:
                return verified_success
            before = previous.get("observed_before")
            if "observed_before" in previous:
                if isinstance(before, list) and len(before) == 2:
                    old_version, old_code = before
                    if isinstance(old_version, str) and (
                        is_newer_stable_version(installed_version, old_version)
                        or (
                            installed_version == old_version
                            and isinstance(old_code, int)
                            and installed_code is not None
                            and installed_code > old_code
                        )
                    ):
                        return True
                return verified_success
            return verified_success or (
                verified_current_code is not None
                and installed_code is not None
                and installed_code >= verified_current_code
            )
        target_code = parse_build_request(target)
        if target_code is not None:
            return installed_code is not None and installed_code >= target_code
        return is_version_at_least(installed_version, target) is True

    def cleared() -> None:
        if (
            not verified_success
            and cleared_event is not None
            and ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        ):
            elapsed = "unknown"
            at = cleared_event.get("at")
            if isinstance(at, str):
                try:
                    started = datetime.fromisoformat(at)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=UTC)
                    elapsed = (
                        f"{max(0, (datetime.now(UTC) - started).total_seconds()):.0f}s"
                    )
                except ValueError:
                    pass
            _LOGGER.warning(
                "Cleared update failure for %s after observing target build %s; "
                "elapsed since failure: %s",
                cleared_event.get("panel", entry_id),
                cleared_event.get("target_version") or "unknown",
                elapsed,
            )
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    await _failure_store(hass).clear(issue_id, cleared, reached)


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
    from .install_jobs import async_get_install_job_manager
    from .install_receipt import InstallPhase

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


async def async_delete_issue_and_report(hass: HomeAssistant, issue_id: str) -> None:
    """Delete a Repair together with any failure report saved for it."""
    await _failure_store(hass).clear(
        issue_id, lambda: ir.async_delete_issue(hass, DOMAIN, issue_id)
    )


def update_failure_entry_id(issue_id: str, events: list[Any]) -> str | None:
    """Return the entry an update failure Repair belongs to, if it is one."""
    for event in events:
        if not isinstance(event, dict):
            continue
        entry_id = event.get("entry_id")
        if (
            event.get("kind") in {"update", "retry_hold"}
            and isinstance(entry_id, str)
            and panel_failure_issue_id(f"update:{entry_id}") == issue_id
        ):
            return entry_id
    return None


async def async_update_failure_entry_ids(hass: HomeAssistant) -> set[str]:
    """Return every entry that a saved update failure report names."""
    store = _failure_store(hass)
    async with store.lock:
        records = await store.load()
    named = (
        update_failure_entry_id(issue_id, record.get("events", []))
        for issue_id, record in records.items()
        if isinstance(record, dict) and isinstance(record.get("events"), list)
    )
    return {entry_id for entry_id in named if entry_id is not None}


async def async_restore_update_failure(hass: HomeAssistant, entry_id: str) -> None:
    """Restore a Repair when its report outlived the registry's delayed save."""
    issue_id = panel_failure_issue_id(f"update:{entry_id}")
    store = _failure_store(hass)
    async with store.lock:
        if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None:
            return
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return
        records = await store.load()
        events = records.get(issue_id, {}).get("events", [])
        if (
            not isinstance(events, list)
            or update_failure_entry_id(issue_id, events) != entry_id
        ):
            return
        latest = next(
            (event for event in reversed(events) if isinstance(event, dict)), {}
        )
        kind = (
            "retry_hold"
            if latest.get("kind") == "retry_hold"
            and latest.get("translation_key") != "update_webview_unfinished"
            else "update"
        )
        reason = str(latest.get("translation_key") or latest.get("reason", ""))
        _issue(
            hass,
            issue_id,
            panel_display_name(hass, entry),
            kind,
            reason,
            entry_id,
            transfer_rate=_slow_transfer_rate(latest),
        )


async def async_record_retry_hold(
    hass: HomeAssistant, issue_id: str, previous: dict[str, Any], error: Exception
) -> None:
    """Keep the repair and show why its latest Retry could not proceed."""
    reason = error.reason if isinstance(error, RetrySafetyHold) else "retry_failed"
    entry_id = previous.get("entry_id")
    if not isinstance(entry_id, str):
        entry_id = None
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    panel = (
        panel_display_name(hass, entry)
        if entry is not None
        else str(previous.get("panel", "Panel"))
    )
    event: dict[str, Any] = {
        "kind": "retry_hold",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "address": previous.get("address"),
        "panel": panel,
        "reason": reason,
        "exception": "".join(traceback.format_exception(error))[-12000:],
        "previous": previous.get("job_id") or previous.get("entry_id"),
    }
    if entry_id is not None:
        event["entry_id"] = entry_id
    webview_failure = (
        previous.get("webview_pending")
        and entry_id is not None
        and str(error) in {"webview_deferred", "webview_unavailable"}
    )
    if webview_failure and entry_id is not None:
        event["webview"] = await _async_failure_webview(hass, entry_id)
        event["translation_key"] = "update_webview_unfinished"
    await _failure_store(hass).append(
        issue_id,
        event,
        lambda: _issue(
            hass,
            issue_id,
            panel,
            "update" if webview_failure else "retry_hold",
            "update_webview_unfinished" if webview_failure else reason,
        ),
        # A panel removed while its Retry ran took its Repair with it; an
        # update Retry records nothing once its entry is no longer current.
        None
        if entry_id is None
        else lambda: (
            entry is not None and hass.config_entries.async_get_entry(entry_id) is entry
        ),
    )


async def async_support_report(hass: HomeAssistant, issue_id: str) -> str:
    """Produce a copyable, unredacted report for support."""
    events = await async_failure_events(hass, issue_id)

    def detail_lines(path: str, value: Any) -> list[str]:
        if isinstance(value, dict) and value:
            return [
                line
                for key, item in value.items()
                for line in detail_lines(f"{path}.{key}" if path else str(key), item)
            ]
        if isinstance(value, list) and value:
            return [
                line
                for index, item in enumerate(value)
                for line in detail_lines(f"{path}[{index}]", item)
            ]
        scalar = "null" if value is None else str(value)
        escaped = scalar.replace("\r", "\\r")
        if "\n" in escaped:
            return [f"{path}:", *(f"  {line}" for line in escaped.split("\n"))]
        return [f"{path}: {escaped}"]

    details = "\n".join(
        detail_lines(
            "",
            {
                "panel_assistant": {
                    "version": INTEGRATION_VERSION,
                    "build": INTEGRATION_BUILD,
                },
                "home_assistant": {"version": ha_version},
                "repair": {"issue_id": issue_id},
                "events": events,
            },
        )
    )
    longest_ticks = max((len(run) for run in re.findall(r"`+", details)), default=0)
    fence = "`" * max(3, longest_ticks + 1)
    return f"{fence}text\n{details}\n{fence}"


async def async_retry_install_job(
    hass: HomeAssistant, previous: InstallJobReceipt
) -> InstallJobReceipt:
    """Create a fresh job only if the previous physical target still matches."""
    from .adb_credentials import async_get_durable_adb_credential
    from .adb_protocol import same_physical_target
    from .client import normalize_address
    from .install_adb import async_installed_artifact_size, async_preflight_install
    from .install_executor import _adb_target
    from .install_jobs import async_get_install_job_manager
    from .install_network import (
        async_pin_install_target,
        async_revalidate_install_target,
    )
    from .install_plan import _build_artifact, build_install_plan
    from .install_receipt import InstallPhase
    from .provisioning import InstallTargetState, async_probe_install_target
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
        # A panel holding only the old app is never given the new one beside it.
        if probe.state not in {
            InstallTargetState.INSTALL_CANDIDATE,
            InstallTargetState.INSTALLED,
        } or not same_physical_target(probe, _adb_target(previous)):
            raise RetrySafetyHold("retry_target_changed")
        rc_tag = previous.artifact.release_tag
        release = await async_resolve_install_choice(hass, rc_tag)
        if (
            _build_artifact(
                release, rc_tag, prerelease_opt_in=previous.artifact.prerelease_opt_in
            )
            != previous.artifact
        ):
            raise RetrySafetyHold("retry_release_changed")
        if probe.state is InstallTargetState.INSTALLED:
            assert release.descriptor is not None
            adb_target = probe.adb_target(target.pinned)
            assert adb_target is not None
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
            target,
            probe,
            release,
            credential.generation_id,
            expected_rc_tag=rc_tag,
            prerelease_opt_in=previous.artifact.prerelease_opt_in,
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

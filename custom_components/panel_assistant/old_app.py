"""Remove an old app left beside the new one, once the new app holds the panel.

The app's application id changed. A panel moved to the new app can keep the
old app installed beside it, its accessibility service still enabled and
waking it, so two apps answer for one panel. Panel Assistant looks for it over
ADB whenever the new app answers as the entry's panel and, once the new app
also holds HOME, backs the panel up and removes the old app with the move's own
retire step. This exists only for the transition and goes with the old-app
glue after v1.0.
"""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from . import panel_move
from .app_identity import SUCCESSOR_PACKAGE_ID, reports_package
from .client import HaPaneldClient, HaPaneldError, PanelHealth
from .const import DOMAIN
from .device import panel_display_name
from .install_adb import MoveStep
from .panel_backup import PanelBackupInvalidError, async_store_panel_backup

_LOGGER = logging.getLogger(__name__)

ISSUE_REMOVE_OLD_APP: Final = "remove_old_app"
REASON_NEW_APP_UNPROVEN: Final = "new_app_unproven"
DATA_OLD_APP_CHECKS: Final = "old_app_checks"
#: Why an old app beside the new one stays: one Repair text per reason its
#: removal stopped, the last for any other reason.
OLD_APP_ISSUES: Final = (
    "remove_old_app_adb",
    "remove_old_app_unproven",
    "remove_old_app_failed",
)
_OLD_APP_ISSUE_BY_REASON: Final = {
    panel_move.REASON_ADB_UNREACHABLE: OLD_APP_ISSUES[0],
    panel_move.REASON_ADB_AUTHORIZATION: OLD_APP_ISSUES[0],
    REASON_NEW_APP_UNPROVEN: OLD_APP_ISSUES[1],
}
#: How long a panel waits between attempts to check or remove its old app.
_OLD_APP_RETRY_SECONDS = 600.0


def old_app_issue_id(entry_id: str) -> str:
    """Return the one Repair saying why an old app beside the new one stays."""
    return f"{ISSUE_REMOVE_OLD_APP}_{entry_id}"


def _holds_panel(entry: ConfigEntry, health: PanelHealth) -> bool:
    """The new app answers as this entry's panel, under the entry's identity."""
    return bool(
        reports_package(health.package, SUCCESSOR_PACKAGE_ID)
        and health.installation_identity
        and health.discovery_id is not None
        and health.discovery_id == entry.unique_id
    )


@callback
def async_evaluate_old_app(
    hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth
) -> None:
    """On a poll where the new app answers as this panel, look for an old app."""
    if _holds_panel(entry, health):
        _async_schedule_old_app_check(hass, entry, health)


@callback
def _async_schedule_old_app_check(
    hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth
) -> None:
    """Look over ADB for an old app left beside the new one, and remove it.

    A panel seen clean is not looked at again until its build changes or Core
    restarts; one that could not be checked or cleaned waits between attempts.
    """
    checks: dict[str, tuple[int | None, bool, float]] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(DATA_OLD_APP_CHECKS, {})
    now = hass.loop.time()
    last = checks.get(entry.entry_id)
    if (
        last is not None
        and last[0] == health.version_code
        and (last[1] or now - last[2] < _OLD_APP_RETRY_SECONDS)
    ):
        return
    if not panel_move.claim_panel_operation(hass, entry.entry_id):
        # A move or an update is running; the next poll tries again.
        return
    checks[entry.entry_id] = (health.version_code, False, now)
    entry.async_create_background_task(
        hass,
        _async_check_old_app(hass, entry, checks),
        f"remove the old app from {entry.title}",
    )


async def _async_check_old_app(
    hass: HomeAssistant,
    entry: ConfigEntry,
    checks: dict[str, tuple[int | None, bool, float]],
) -> None:
    try:
        if await _async_remove_old_app(hass, entry):
            version_code, _clean, at = checks[entry.entry_id]
            checks[entry.entry_id] = (version_code, True, at)
    except (panel_move.MoveError, HaPaneldError) as err:
        _LOGGER.info(
            "The old app on %s is not removed yet: %s (%r)",
            entry.title,
            err,
            err.__cause__,
        )
    except Exception:
        _LOGGER.exception("Removing the old app from %s failed", entry.title)
    finally:
        panel_move.release_panel_operation(hass, entry.entry_id)


async def _async_remove_old_app(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove an old app the new app has replaced; True once none is left.

    Only once the new app is proven to hold the panel: it answers as this
    entry's panel and HOME resolves to it. The panel is backed up first.
    A move of this integration's own is offered while it is unfinished, so
    the old app it set aside is never removed here. A panel ADB cannot reach
    is not known to hold an old app, so it is left alone without a Repair;
    once an old app has been seen, a Repair says why it stays until it goes.
    """
    issue = old_app_issue_id(entry.entry_id)
    # An old app seen by an earlier check is still known to be there.
    seen = ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    try:
        target, signer = await panel_move._async_target(hass, entry)
        # Nothing here was started by the owner, so no connection may offer
        # Panel Assistant's key: a panel that stopped trusting it is reported.
        observed = await panel_move._async_step(
            target, signer, MoveStep.OBSERVE, authorize=False
        )
        if not observed.legacy_installed and not observed.legacy_set_aside:
            ir.async_delete_issue(hass, DOMAIN, issue)
            return True
        seen = True
        client: HaPaneldClient = entry.runtime_data.client
        try:
            health: PanelHealth | None = await client.async_get_health()
        except HaPaneldError:
            health = None
        if (
            health is None
            or not _holds_panel(entry, health)
            or entry.runtime_data.coordinator.identity_mismatch
            or not observed.successor_installed
            or observed.home != SUCCESSOR_PACKAGE_ID
        ):
            raise panel_move.MoveError(REASON_NEW_APP_UNPROVEN)
        try:
            await async_store_panel_backup(
                hass,
                entry.entry_id,
                health.version_code,
                await client.async_backup_panel(),
            )
        except (HaPaneldError, PanelBackupInvalidError, OSError) as err:
            raise panel_move.MoveError(panel_move.REASON_BACKUP_FAILED) from err
        retired = await panel_move._async_step(
            target, signer, MoveStep.RETIRE_LEGACY, authorize=False
        )
        if retired.legacy_installed and retired.home != SUCCESSOR_PACKAGE_ID:
            # HOME left the new app while the backup ran, and the step keeps
            # an old app HOME resolves to: the proof no longer holds.
            raise panel_move.MoveError(REASON_NEW_APP_UNPROVEN)
        panel_move._require_retired(retired)
    except panel_move.MoveError as err:
        if seen:
            key = _OLD_APP_ISSUE_BY_REASON.get(err.reason, OLD_APP_ISSUES[2])
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=key,
                translation_placeholders={"panel": panel_display_name(hass, entry)},
                data={panel_move.ISSUE_DATA_ENTRY_ID: entry.entry_id},
            )
        raise
    ir.async_delete_issue(hass, DOMAIN, issue)
    _LOGGER.info("Removed the old app from %s", entry.title)
    return True

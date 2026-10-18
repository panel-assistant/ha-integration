"""Remove the panel app from a panel, leaving the panel its own home screen.

The app first gives HOME back to another launcher and re-enables the vendor
apps it switched off (where an ordinary app cannot make the vendor's launcher
HOME, Panel Assistant does that over ADB), and the vendor settings
installation changed (``vendor_provisioning``) are put back; only then is it
uninstalled, under both of its ids, over the ADB connection Panel Assistant
already holds. The uninstall runs only while HOME resolves to another
launcher, read in the same shell, so a panel is never left without a home
screen. Every step is safe to repeat: a retry after an interrupted run finds
what is left and finishes it.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import adb_protocol, install_adb, vendor_provisioning
from .adb_credentials import (
    AdbCredentialError,
    async_get_adb_credential,
    async_get_durable_adb_credential,
)
from .adb_protocol import AdbInstallTarget, InstallAdbError, InstallAdbErrorCode
from .app_identity import LEGACY_PACKAGE_ID, PACKAGE_ID
from .client import (
    HandBackRefusedError,
    HaPaneldClient,
    HaPaneldError,
    UpdateApprovalRequiredError,
    normalize_address,
)
from .install_adb import MoveObservation, MoveStep, async_move_step
from .install_network import InstallNetworkError, async_pin_install_target
from .panel_operations import claim_panel_operation, release_panel_operation
from .provisioning import InstallTargetState, async_probe_install_target

_LOGGER = logging.getLogger(__name__)

#: Why a removal stopped; each is an options-flow error with its own text.
REASON_BUSY: Final = "busy"
REASON_ADB_UNREACHABLE: Final = "adb_unreachable"
REASON_ADB_AUTHORIZATION: Final = "adb_authorization"
REASON_PANEL_CHANGED: Final = "panel_changed"
REASON_APPROVAL_REQUIRED: Final = "approval_required"
REASON_NO_REPLACEMENT_HOME: Final = "no_replacement_home"
REASON_OWNERSHIP_UNREADABLE: Final = "ownership_unreadable"
REASON_PACKAGE_STATE_UNKNOWN: Final = "package_state_unknown"
REASON_HOME_NOT_HANDED: Final = "home_not_handed"
REASON_UNSUPPORTED: Final = "hand_back_unsupported"
REASON_APP_UNREACHABLE: Final = "app_unreachable"
REASON_REMOVE_FAILED: Final = "remove_failed"

_HAND_BACK_REASONS: Final = {
    "no-replacement-home": REASON_NO_REPLACEMENT_HOME,
    "ownership-unreadable": REASON_OWNERSHIP_UNREADABLE,
    "package-state-unknown": REASON_PACKAGE_STATE_UNKNOWN,
    "unsupported": REASON_UNSUPPORTED,
}
#: Packages HOME may resolve to that are not a home screen an owner can use.
_NOT_A_LAUNCHER: Final = frozenset(
    {LEGACY_PACKAGE_ID, PACKAGE_ID, "com.android.settings"}
)


class RemovalError(Exception):
    """A removal stopped; ``reason`` says why and what is still installed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def async_read_firmware(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Read the panel's firmware number for the risks page, or None."""
    try:
        found = await async_app_target(hass, entry)
        if found is None:
            return None
        return (await _async_step(found, MoveStep.OBSERVE)).firmware
    except RemovalError:
        return None


async def async_remove_app(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the app from the entry's panel; return once neither id is left."""
    if not claim_panel_operation(hass, entry.entry_id):
        raise RemovalError(REASON_BUSY)
    try:
        await _async_remove_app(hass, entry)
    finally:
        release_panel_operation(hass, entry.entry_id)


async def async_app_target(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[AdbInstallTarget, Any] | None:
    """Reach the panel over ADB; None when Android holds neither app id.

    A panel holding only the old app is reached too, so its stray app can go.
    """
    try:
        await async_get_adb_credential(hass)
        credential = await async_get_durable_adb_credential(hass)
        # An entry whose app is already gone may not have loaded, so the stored
        # address stands in for the running client's.
        client = getattr(getattr(entry, "runtime_data", None), "client", None)
        address = (
            client.address
            if client is not None
            else normalize_address(entry.data[CONF_ADDRESS])
        )
        pinned = await async_pin_install_target(hass, address)
    except (AdbCredentialError, InstallNetworkError, HaPaneldError) as err:
        raise RemovalError(REASON_ADB_UNREACHABLE) from err
    probe = await async_probe_install_target(pinned.pinned, credential.signer)
    if probe.state is InstallTargetState.ADB_UNAUTHORIZED:
        raise RemovalError(REASON_ADB_AUTHORIZATION)
    if probe.state is InstallTargetState.INSTALL_CANDIDATE:
        return None
    adb_target = probe.adb_target(pinned.pinned)
    if adb_target is None or probe.state not in {
        InstallTargetState.INSTALLED,
        InstallTargetState.MIGRATION_CANDIDATE,
    }:
        raise RemovalError(REASON_ADB_UNREACHABLE)
    return adb_target, credential.signer


async def _async_remove_app(hass: HomeAssistant, entry: ConfigEntry) -> None:
    found = await async_app_target(hass, entry)
    if found is None:
        return
    observed = await _async_step(found, MoveStep.OBSERVE)
    try:
        await _client(hass, entry).async_hand_back_home()
    except UpdateApprovalRequiredError as err:
        raise RemovalError(REASON_APPROVAL_REQUIRED) from err
    except HandBackRefusedError as err:
        # An app that could not make the vendor's launcher HOME answers
        # without one; the panel's shell still can.
        if (
            err.code != "not-handed"
            or (await _async_step(found, MoveStep.VENDOR_HOME)).home
            not in vendor_provisioning.VENDOR_HOME_PACKAGES
        ):
            raise RemovalError(
                _HAND_BACK_REASONS.get(err.code, REASON_HOME_NOT_HANDED)
            ) from err
    except HaPaneldError as err:
        # An interrupted run may already have handed HOME back and removed the
        # app under one id, which leaves nothing to answer. Only a panel whose
        # HOME is already another launcher goes on without the app.
        if observed.home is None or observed.home in _NOT_A_LAUNCHER:
            raise RemovalError(REASON_APP_UNREACHABLE) from err
    await _async_restore_vendor_setup(found)
    await _async_restore_display_sizing(found)
    try:
        removed = await _async_step(found, MoveStep.REMOVE_APP)
    except RemovalError as err:
        # The uninstall may have run before the connection dropped or the
        # answer stalled (a Shelly Wall Display's did, for 30 s, once the app
        # was gone), so what is left is read again; a retry does the rest.
        try:
            removed = await _async_step(found, MoveStep.OBSERVE)
        except RemovalError:
            raise RemovalError(REASON_REMOVE_FAILED) from err
    if _app_left(removed):
        raise RemovalError(
            REASON_HOME_NOT_HANDED
            if removed.home is None or removed.home in _NOT_A_LAUNCHER
            else REASON_REMOVE_FAILED
        )


async def _async_restore_vendor_setup(found: tuple[AdbInstallTarget, Any]) -> None:
    """Give the vendor back what installing changed, once HOME is not the app's.

    Never a reason to keep the app: a panel whose values could not be put
    back is reported, and removal goes on. A retry finds the record and tries
    again while the app is still there.
    """
    target, signer = found
    try:
        outcome = await install_adb.async_restore_vendor_setup(target, signer)
    except adb_protocol.InstallAdbError:
        outcome = "unreachable"
    if outcome not in ("restored", "none"):
        _LOGGER.warning(vendor_provisioning.RESTORE_WARNING, outcome)


async def _async_restore_display_sizing(found: tuple[AdbInstallTarget, Any]) -> None:
    """Put back the display and text size setup changed; never keeps the app."""
    target, signer = found
    try:
        outcome = await install_adb.async_restore_display_sizing(target, signer)
    except adb_protocol.InstallAdbError:
        outcome = "unreachable"
    if outcome not in ("restored", "none"):
        _LOGGER.warning(
            "Could not put back the display and text size this panel had before "
            "Panel Assistant set it up (%s); they stay as set after the app is "
            "removed",
            outcome,
        )


def _app_left(observed: MoveObservation) -> bool:
    return observed.legacy_installed or observed.successor_installed


async def _async_step(
    found: tuple[AdbInstallTarget, Any], step: MoveStep
) -> MoveObservation:
    target, signer = found
    try:
        return await async_move_step(target, signer, step)
    except InstallAdbError as err:
        if err.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED:
            raise RemovalError(REASON_ADB_AUTHORIZATION) from err
        if err.code is InstallAdbErrorCode.TARGET_CHANGED:
            raise RemovalError(REASON_PANEL_CHANGED) from err
        raise RemovalError(REASON_ADB_UNREACHABLE) from err


def _client(hass: HomeAssistant, entry: ConfigEntry) -> HaPaneldClient:
    """The entry's own client, or one at its stored address when not loaded."""
    client = getattr(getattr(entry, "runtime_data", None), "client", None)
    if isinstance(client, HaPaneldClient):
        return client
    return HaPaneldClient(
        async_get_clientsession(hass), normalize_address(entry.data[CONF_ADDRESS])
    )

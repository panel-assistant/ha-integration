"""What an update leaves to the panel after Home Assistant sends the build.

After any update Home Assistant did not send over ADB, an already-authorized ADB
route restores the runtime permissions Android dropped with the old package.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import ClientConnectorError
from homeassistant.core import HomeAssistant

from .adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    async_get_durable_adb_credential,
)
from .adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
)
from .app_identity import reports_package
from .client import (
    CannotConnectError,
    HaPaneldError,
)
from .coordinator import HaPaneldDataUpdateCoordinator
from .install_adb import async_repair_installed_app_permissions
from .install_network import InstallNetworkError
from .release import ReleaseArtifact
from .update_errors import UPDATE_LOGGER as _LOGGER

# How long the permission repair after an update waits for the panel's ADB to
# answer again: twelve tries, fifteen seconds apart.
_PERMISSION_REPAIR_ATTEMPTS = 12
_PERMISSION_REPAIR_RETRY_SECONDS = 15


async def _retry_unstarted_install(start: Callable[[], Awaitable[None]]) -> None:
    """Retry once only when no connection was made to the panel."""
    try:
        await start()
    except CannotConnectError as err:
        if not isinstance(err.__cause__, ClientConnectorError):
            raise
        await asyncio.sleep(1)
        await start()


async def async_repair_update_permissions(
    hass: HomeAssistant,
    coordinator: HaPaneldDataUpdateCoordinator,
    artifact: ReleaseArtifact,
    *,
    admit_adb_target: Callable[
        [ReleaseArtifact, AdbCredential | None],
        Awaitable[tuple[AdbInstallTarget, AdbCredential, AdbRootMode] | None],
    ],
    panel_name: Callable[[], str],
) -> None:
    """Reuse already-authorized ADB after a healthy LAN update, without relaunch."""
    for attempt in range(_PERMISSION_REPAIR_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_PERMISSION_REPAIR_RETRY_SECONDS)
        snapshot = coordinator.data
        # Read again on every try: the panel may have changed while ADB
        # was quiet.
        descriptor = artifact.descriptor
        skipped = (
            "no install descriptor"
            if descriptor is None
            else "no panel reading"
            if snapshot is None
            else "panel identity changed"
            if coordinator.identity_mismatch
            else "last panel reading failed"
            if not coordinator.reachable
            else f"panel reports {snapshot.health.version}, not {artifact.version}"
            if snapshot.health.version != artifact.version
            else f"panel reports package {snapshot.health.package}"
            if not reports_package(snapshot.health.package, descriptor.package_id)
            else None
        )
        if skipped is not None:
            _LOGGER.debug(
                "No permission repair after updating %s: %s",
                panel_name(),
                skipped,
            )
            return
        assert descriptor is not None
        try:
            # A successful update never requests new ADB trust or creates a key.
            credential = await async_get_durable_adb_credential(hass)
            admitted = await admit_adb_target(artifact, credential)
            if admitted is None:
                _LOGGER.debug(
                    "No permission repair after updating %s: ADB target not admitted",
                    panel_name(),
                )
                return
            target, credential, root_mode = admitted
            await async_repair_installed_app_permissions(
                target,
                credential.signer,
                descriptor,
                expected_root_mode=root_mode,
            )
        except InstallAdbError as err:
            # A restarting app re-asserts network ADB, so the panel's ADB
            # can stop answering for a while after the dashboard is back.
            if (
                err.code is InstallAdbErrorCode.TARGET_UNREACHABLE
                and attempt + 1 < _PERMISSION_REPAIR_ATTEMPTS
            ):
                _LOGGER.debug(
                    "ADB on %s not answering yet after the update; retrying",
                    panel_name(),
                )
                continue
            _LOGGER.debug(
                "No permission repair after updating %s: %r",
                panel_name(),
                err,
            )
            return
        except (
            AdbCredentialError,
            InstallNetworkError,
            HaPaneldError,
            OSError,
        ) as err:
            # The dashboard already runs. An unavailable optional repair
            # route does not change the successful update's result.
            _LOGGER.debug(
                "No permission repair after updating %s: %r",
                panel_name(),
                err,
            )
            return
        _LOGGER.debug("Repaired permissions after updating %s", panel_name())
        return

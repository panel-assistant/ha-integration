"""How an update reaches a panel: the panel installs it, or Home Assistant does.

A panel with a privileged installer downloads the build itself. Any other panel
is updated over Home Assistant's durable authorized ADB connection, admitted only
after the ADB peer is proved to be this entry's panel running an installed app
the exact signed build may replace.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    AdbCredentialMissingError,
    async_get_adb_credential,
    async_get_durable_adb_credential,
)
from .adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    LaunchOutcome,
)
from .client import HaPaneldError, is_valid_discovery_id
from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
from .install_adb import (
    async_installed_artifact_size,
    async_launch_installed_app,
    async_preflight_install,
)
from .install_network import (
    InstallNetworkError,
    PinnedPanelTarget,
    async_pin_entry_target,
    async_pin_install_target,
    async_revalidate_install_target,
)
from .provisioning import InstallTargetState, async_probe_install_target
from .release import ReleaseArtifact
from .repairs_failure import async_clear_adb_authorization
from .repairs_upgrade import speaks_this_protocol
from .transport_sessions import async_get_sessions, session_manages
from .update_errors import UPDATE_LOGGER as _LOGGER
from .update_errors import adb_authorization_error
from .update_offer import UpdateOffer

# Which way the last update reached the panel, shown on the entity.
ROUTE_ATTRIBUTE = "update_route"
ROUTE_STAGED = "staged_by_home_assistant"
ROUTE_PANEL = "downloaded_by_panel"
ROUTE_ADB = "installed_by_home_assistant_adb"
# The panel fetched the build from Home Assistant over its own session, because
# Home Assistant could not reach its address.
ROUTE_SESSION = "pulled_from_home_assistant"
ROUTE_UNAVAILABLE_ATTRIBUTE = "update_unavailable_reason"


def entry_discovery_id(hass: HomeAssistant, entry_id: str) -> str | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    unique_id = entry.unique_id if entry is not None else None
    return unique_id if unique_id and is_valid_discovery_id(unique_id) else None


def api_capability(snapshot: PanelSnapshot | None) -> str | None:
    if snapshot is not None and not speaks_this_protocol(snapshot.health):
        # An app on an older protocol has no session here, and installs an
        # update for itself only with the policy a session hands it, so only
        # the authorized ADB route can update it.
        return "none"
    return snapshot.status.install_capability if snapshot and snapshot.status else None


def session_route(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: HaPaneldDataUpdateCoordinator,
    offer: UpdateOffer,
) -> bool:
    """Whether the panel's session can carry an update its address cannot.

    Only a panel that installs for itself.
    """
    return (
        not coordinator.reachable
        and session_manages(async_get_sessions(hass).get(entry_id))
        and api_capability(coordinator.data) == "api"
    )


async def async_install_route(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: HaPaneldDataUpdateCoordinator,
    offer: UpdateOffer,
    panel_name: Callable[[], str],
) -> tuple[str | None, AdbInstallTarget | None, AdbCredential | None]:
    """Choose from the panel's live install route, then authorized ADB."""
    snapshot: PanelSnapshot | None = coordinator.data
    if snapshot is not None and session_route(hass, entry_id, coordinator, offer):
        return ROUTE_SESSION, None, None
    if snapshot is None or not coordinator.reachable:
        return None, None, None
    capability = api_capability(coordinator.data)
    if capability is None:
        try:
            # Older panels exported this exact privileged-route observation
            # as the dashboard's `shot` bit before the typed status field.
            if await coordinator.client.async_get_legacy_install_capability():
                return ROUTE_PANEL, None, None
        except HaPaneldError:
            pass
    elif capability == "api":
        return ROUTE_PANEL, None, None
    adb = await async_adb_route(hass, entry_id, coordinator, offer, panel_name)
    if adb is None:
        return None, None, None
    return ROUTE_ADB, *adb


async def async_adb_route(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: HaPaneldDataUpdateCoordinator,
    offer: UpdateOffer,
    panel_name: Callable[[], str],
) -> tuple[AdbInstallTarget, AdbCredential] | None:
    """Admit the authorized ADB route, whatever route the panel offers itself."""
    artifact = offer.adb_artifact()
    if artifact is None or artifact.descriptor is None:
        return None
    try:
        try:
            credential = await async_get_durable_adb_credential(hass)
        except AdbCredentialMissingError:
            credential = None
        admitted = await async_admit_adb_target(
            hass, entry_id, coordinator, artifact, credential, panel_name
        )
        if admitted is None:
            return None
        target, credential, _root_mode = admitted
    except InstallAdbError as err:
        if err.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED:
            raise adb_authorization_error(hass, entry_id, panel_name()) from err
        return None
    except (
        AdbCredentialError,
        InstallNetworkError,
        HaPaneldError,
        OSError,
    ):
        return None
    async_clear_adb_authorization(hass, entry_id)
    return target, credential


async def async_pin_adb_target(
    hass: HomeAssistant, entry_id: str, coordinator: HaPaneldDataUpdateCoordinator
) -> PinnedPanelTarget | None:
    """Bind passive ADB evidence to the configured panel's pinned HTTP identity."""
    snapshot = coordinator.data
    if snapshot is None or not coordinator.reachable:
        return None
    return await async_pin_entry_target(
        hass,
        coordinator.client.address,
        snapshot.health,
        entry_discovery_id(hass, entry_id),
    )


async def async_probe_adb_target(
    pinned: PinnedPanelTarget, credential: AdbCredential | None
) -> AdbInstallTarget | None:
    """Observe the current installed app without offering an authorization key."""
    probe = (
        await async_probe_install_target(pinned.pinned)
        if credential is None
        else await async_probe_install_target(pinned.pinned, credential.signer)
    )
    if probe.state is InstallTargetState.ADB_UNREACHABLE:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
    if probe.state is InstallTargetState.ADB_UNAUTHORIZED:
        raise InstallAdbError(InstallAdbErrorCode.AUTHORIZATION_REQUIRED)
    if probe.state is not InstallTargetState.INSTALLED:
        return None
    return probe.adb_target(pinned.pinned)


async def async_relaunch_stopped_app(
    hass: HomeAssistant,
    coordinator: HaPaneldDataUpdateCoordinator,
    artifact: ReleaseArtifact,
    panel_name: Callable[[], str],
) -> bool:
    """Start the updated app over authorized ADB when it never came back.

    Its HTTP identity went with the app, so the target is the entry's address,
    and what admits it is the panel's existing trust in this Home Assistant's
    ADB key and the exact signed bytes of this build installed there, proved
    before the launch can change anything. Never creates a key.
    """
    descriptor = artifact.descriptor
    if descriptor is None:
        return False
    try:
        credential = await async_get_durable_adb_credential(hass)
        pinned = await async_pin_install_target(hass, coordinator.client.address)
        target = await async_probe_adb_target(pinned, credential)
        if target is None:
            return False
        installed = await async_preflight_install(
            target, credential.signer, descriptor, admit_installed_target=True
        )
        if not installed.target_installed:
            return False
        if (
            await async_installed_artifact_size(
                target,
                credential.signer,
                descriptor,
                expected_root_mode=installed.root_mode,
            )
            is None
        ):
            return False
        outcome = await async_launch_installed_app(
            target,
            credential.signer,
            descriptor,
            expected_root_mode=installed.root_mode,
        )
    except (InstallAdbError, AdbCredentialError, InstallNetworkError, OSError) as err:
        _LOGGER.debug("Could not start the app on %s over ADB: %r", panel_name(), err)
        return False
    return outcome is LaunchOutcome.STARTED


async def async_observe_update_route(
    hass: HomeAssistant, entry_id: str, coordinator: HaPaneldDataUpdateCoordinator
) -> bool | None:
    """Check route recovery independently of a pending signed update."""
    try:
        capability = api_capability(coordinator.data)
        if capability == "api":
            return True
        if capability is None:
            try:
                client = coordinator.client
                if await client.async_get_legacy_install_capability():
                    return True
            except HaPaneldError:
                pass
        try:
            credential = await async_get_durable_adb_credential(hass)
        except AdbCredentialMissingError:
            credential = None
        pinned = await async_pin_adb_target(hass, entry_id, coordinator)
        if pinned is None:
            return None
        target = await async_probe_adb_target(pinned, credential)
        if target is None:
            return None
        await async_revalidate_install_target(hass, pinned)
        return True
    except InstallAdbError:
        return False
    except (
        AdbCredentialError,
        InstallNetworkError,
        HaPaneldError,
        OSError,
    ):
        return None


async def async_admit_adb_target(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: HaPaneldDataUpdateCoordinator,
    artifact: ReleaseArtifact,
    credential: AdbCredential | None,
    panel_name: Callable[[], str],
) -> tuple[AdbInstallTarget, AdbCredential, AdbRootMode] | None:
    """Bind an ADB target to the live HTTP identity and exact signed artifact."""
    snapshot = coordinator.data
    if snapshot is None or not coordinator.reachable or artifact.descriptor is None:
        return None
    pinned = await async_pin_adb_target(hass, entry_id, coordinator)
    if pinned is None:
        _LOGGER.debug(
            "ADB for %s refused: the panel at its address is not this entry's",
            panel_name(),
        )
        return None
    if credential is None:
        # Only an already-open peer permits install admission to create a key.
        if await async_probe_adb_target(pinned, None) is None:
            return None
        await async_get_adb_credential(hass)
        credential = await async_get_durable_adb_credential(hass)
    target = await async_probe_adb_target(pinned, credential)
    if target is None:
        _LOGGER.debug("ADB for %s refused: no installed app found", panel_name())
        return None
    admitted = await async_preflight_install(
        target,
        credential.signer,
        artifact.descriptor,
        admit_installed_target=True,
    )
    if not admitted.target_installed:
        return None
    await async_revalidate_install_target(hass, pinned)
    return target, credential, admitted.root_mode

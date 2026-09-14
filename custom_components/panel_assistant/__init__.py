"""The ha-paneld integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .browser_delivery import async_register_browser_delivery
from .browser_panel import async_register_browser_panel
from .build_feed import BuildFeedError, normalize_feed_url
from .client import HaPaneldClient, normalize_address
from .const import DOMAIN
from .coordinator import HaPaneldDataUpdateCoordinator
from .feed_coordinator import CONF_BUILD_FEED, DATA_BUILD_FEED, BuildFeedCoordinator
from .install_artifacts import register_feed_download_host
from .install_executor import (
    InstallExecutor,
    async_get_install_executor,
    async_resume_loaded_install_jobs,
)
from .install_jobs import (
    InstallPhase,
    InstallResultCode,
    async_get_install_job_manager,
)
from .native import CONF_NATIVE_ENTITIES, NATIVE_ONLY_PLATFORMS
from .transport import (
    DATA_NATIVE_ENTITIES,
    REASON_ENTRY_UNLOADED,
    async_apply_authority,
    async_delete_binding_issue,
    async_get_sessions,
    async_setup_transport,
    native_entities_enabled,
)
from .update_coordinator import PanelUpdateCoordinator

PLATFORMS = [Platform.SENSOR, Platform.UPDATE]
# Panels are config entries. YAML holds only development options: an optional
# build feed, without which nothing is fetched from anywhere but GitHub
# releases, and native entities, which stay dormant unless turned on.
CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Schema(
            {
                vol.Optional(CONF_BUILD_FEED): cv.string,
                vol.Optional(CONF_NATIVE_ENTITIES): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register browser delivery independently of panel config entries."""
    hass.data.setdefault(DOMAIN, {})[DATA_NATIVE_ENTITIES] = config.get(DOMAIN, {}).get(
        CONF_NATIVE_ENTITIES, False
    )
    async_setup_transport(hass)
    async_register_browser_delivery(hass)
    await async_register_browser_panel(hass)
    feed = config.get(DOMAIN, {}).get(CONF_BUILD_FEED)
    if feed is not None:
        try:
            feed_url = normalize_feed_url(feed)
        except BuildFeedError:
            _LOGGER.error(
                "Ignoring %s: it must be a plain https URL to a .json feed",
                CONF_BUILD_FEED,
            )
        else:
            if feed_url.host is not None:
                register_feed_download_host(feed_url.host)
            coordinator = BuildFeedCoordinator(hass, feed_url)
            hass.data.setdefault(DOMAIN, {})[DATA_BUILD_FEED] = coordinator
            hass.async_create_background_task(
                coordinator.async_refresh(), f"{DOMAIN} first build feed read"
            )
    return True


@dataclass(slots=True)
class HaPaneldRuntimeData:
    """Runtime data for one config entry."""

    client: HaPaneldClient
    coordinator: HaPaneldDataUpdateCoordinator
    update_coordinator: PanelUpdateCoordinator
    platforms: list[Platform]


type HaPaneldConfigEntry = ConfigEntry[HaPaneldRuntimeData]


async def _async_resume_install_jobs(hass: HomeAssistant) -> None:
    """Best-effort resume after an existing entry has loaded the domain."""
    try:
        await async_resume_loaded_install_jobs(hass)
    except Exception:
        _LOGGER.warning("Unable to resume durable ha-paneld install jobs")


async def _async_release_install_finalizer(
    hass: HomeAssistant,
    executor: InstallExecutor,
    job_id: str,
    finalizer_id: str,
) -> None:
    """Drain finalizer release before propagating caller cancellation."""
    release_task = hass.async_create_task(
        executor.async_release_finalizer(job_id, finalizer_id),
        f"release ha-paneld setup finalizer {job_id}",
    )
    cancelled = False
    while not release_task.done():
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        release_task.result()
    except Exception:
        _LOGGER.warning("Unable to release a durable ha-paneld install finalizer")
    if cancelled:
        raise asyncio.CancelledError


async def _async_reconcile_install_receipt(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    address: str,
    health_version: str,
) -> None:
    """Best-effort handoff for a healthy receipt left by a completed install."""
    executor = None
    receipt = None
    finalizer_id = f"setup_{entry.entry_id}"
    acquired = False
    try:
        executor = await async_get_install_executor(hass)
        manager = await async_get_install_job_manager(hass)
        receipts = await manager.async_list()
        receipt = next(
            (
                candidate
                for candidate in receipts
                if candidate.phase is InstallPhase.HEALTHY_UNCLAIMED
                and candidate.target.address == address
            ),
            None,
        )
        if receipt is None:
            return
        acquired = await executor.async_acquire_finalizer(receipt.job_id, finalizer_id)
        if not acquired:
            return
        if health_version == receipt.artifact.version_name:
            await manager.async_transition(
                receipt.job_id,
                receipt.revision,
                InstallPhase.CONSUMED,
                result_code=InstallResultCode.ENTRY_CREATED,
                consumed_entry_id=entry.entry_id,
            )
            return
        await manager.async_transition(
            receipt.job_id,
            receipt.revision,
            InstallPhase.RECOVERY_REQUIRED,
            result_code=InstallResultCode.VERIFICATION_REQUIRED,
        )
    except Exception:
        _LOGGER.warning("Unable to reconcile a durable ha-paneld install receipt")
    finally:
        if acquired and executor is not None and receipt is not None:
            await _async_release_install_finalizer(
                hass, executor, receipt.job_id, finalizer_id
            )


async def async_setup_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Set up ha-paneld from a config entry."""
    await _async_resume_install_jobs(hass)
    address = normalize_address(entry.data[CONF_ADDRESS])
    client = HaPaneldClient(async_get_clientsession(hass), address)
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)
    await coordinator.async_config_entry_first_refresh()
    update_coordinator = PanelUpdateCoordinator(hass, client)
    await update_coordinator.async_config_entry_first_refresh()

    await _async_reconcile_install_receipt(
        hass,
        entry,
        address.stored_value,
        coordinator.data.health.version,
    )

    platforms = list(PLATFORMS)
    if native_entities_enabled(hass):
        platforms.extend(NATIVE_ONLY_PLATFORMS)
    entry.runtime_data = HaPaneldRuntimeData(
        client=client,
        coordinator=coordinator,
        update_coordinator=update_coordinator,
        platforms=platforms,
    )
    entry.async_on_unload(
        lambda: async_get_sessions(hass).close_entry(
            entry.entry_id, REASON_ENTRY_UNLOADED
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply an options change of authority to the panel's live session."""
    async_apply_authority(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Unload a ha-paneld config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )


async def async_remove_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Withdraw a removed panel's request and forget its last session."""
    async_delete_binding_issue(hass, entry.entry_id)
    async_get_sessions(hass).forget_entry(entry.entry_id)


async def async_reload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Reload a ha-paneld config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

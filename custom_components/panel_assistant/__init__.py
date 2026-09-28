"""The ha-paneld integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .browser_delivery import async_register_browser_delivery
from .browser_panel import async_register_browser_panel
from .build_feed import BuildFeedError, normalize_feed_url
from .client import HaPaneldClient, PanelHealth, normalize_address
from .const import DOMAIN
from .coordinator import HaPaneldDataUpdateCoordinator
from .cutover import (
    async_apply_cutover,
    async_release_removed_entry,
    cutover_reconciliation_needed,
    cutover_reconciliation_waiting,
)
from .device import async_refresh_panel_device, panel_device_info
from .embed import (
    REASON_ENTRY_UNLOADED as EMBED_ENTRY_UNLOADED,
)
from .embed import (
    async_get_embed_sessions,
    async_setup_embed,
)
from .feed_coordinator import CONF_BUILD_FEED, DATA_BUILD_FEED, BuildFeedCoordinator
from .guards import (
    async_forget_removed_panel,
    async_load_removed_panels,
    async_remember_removed_panel,
    async_start_guards,
)
from .install_artifacts import register_feed_download_host
from .install_executor import (
    async_get_install_executor,
    async_resume_loaded_install_jobs,
)
from .native import CONF_NATIVE_ENTITIES, NATIVE_ONLY_PLATFORMS
from .transport import (
    DATA_NATIVE_ENTITIES,
    DEFAULT_AUTHORITY,
    REASON_ENTRY_UNLOADED,
    async_apply_authority,
    async_delete_binding_issue,
    async_delete_cutover_issues,
    async_delete_merged_identity_issue,
    async_get_sessions,
    async_setup_transport,
    cutover_record,
    effective_authority,
    native_enabled_for,
)
from .update_coordinator import PanelUpdateCoordinator
from .voice import async_follow_voice, async_setup_voice, satellite_known

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
    # True or False in YAML decides for every panel; absent, each panel's own
    # Control option decides (see native_enabled_for).
    hass.data.setdefault(DOMAIN, {})[DATA_NATIVE_ENTITIES] = config.get(DOMAIN, {}).get(
        CONF_NATIVE_ENTITIES
    )
    # Before the commands exist, so no hello is answered without them.
    await async_load_removed_panels(hass)
    async_setup_transport(hass)
    async_setup_voice(hass)
    async_setup_embed(hass)
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
    # The authority this load was set up with. Only a change of the effective
    # authority reloads the entry, so its cutover runs; writes to the entry's
    # data, as binding and the cutover record make, do not.
    authority: str = DEFAULT_AUTHORITY
    # Set only when forward migration was deferred because initial health was
    # unavailable. The first successful publication consumes it before asking
    # for one lifecycle reload, so a failed cutover cannot become a reload loop.
    cutover_reconciliation_pending: bool = False


type HaPaneldConfigEntry = ConfigEntry[HaPaneldRuntimeData]


@callback
def _async_request_cutover_reconciliation(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    runtime_data: HaPaneldRuntimeData,
) -> None:
    """Reload once when health or a hello makes a deferred ownership move actionable."""
    if (
        getattr(entry, "runtime_data", None) is not runtime_data
        or not runtime_data.cutover_reconciliation_pending
        or cutover_reconciliation_waiting(hass, entry)
    ):
        return
    # Re-read requested authority before acting. A person may have cancelled
    # the pending native move while health was unavailable.
    runtime_data.cutover_reconciliation_pending = False
    if not cutover_reconciliation_needed(hass, entry):
        return
    # Not eagerly: a hello that supplied the missing channels opened a session
    # and still owes the panel its reply, which must reach it before the
    # reload ends that session.
    hass.async_create_task(
        hass.config_entries.async_reload(entry.entry_id),
        f"reconcile {DOMAIN} ownership after its prerequisites arrived",
        eager_start=False,
    )


async def _async_resume_install_jobs(hass: HomeAssistant) -> None:
    """Best-effort resume after an existing entry has loaded the domain."""
    try:
        await async_resume_loaded_install_jobs(hass)
    except Exception:
        _LOGGER.warning("Unable to resume durable ha-paneld install jobs")


async def _async_reconcile_install_receipt(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    address: str,
    health: PanelHealth,
) -> None:
    """Best-effort handoff for a healthy receipt left by a completed install."""
    try:
        executor = await async_get_install_executor(hass)
    except Exception:
        _LOGGER.warning("Unable to reconcile a durable ha-paneld install receipt")
        return
    await executor.async_reconcile_entry(entry.entry_id, address, health)


async def async_setup_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Set up ha-paneld from a config entry."""
    from homeassistant.exceptions import ConfigEntryNotReady

    from .identity import reconcile_identity

    await _async_resume_install_jobs(hass)
    address = normalize_address(entry.data[CONF_ADDRESS])
    client = HaPaneldClient(async_get_clientsession(hass), address)
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)
    # A stored address that does not answer is no reason to refuse the entry.
    # The panel may be connected, or about to connect, from somewhere else,
    # and only a loaded entry can accept its session, which is then what
    # repairs the address. The entities load unavailable and become available
    # on the first answer from either side.
    await coordinator.async_refresh()
    if not reconcile_identity(hass, entry):
        raise ConfigEntryNotReady("Panel identity conflicts with its saved entities")
    update_coordinator = PanelUpdateCoordinator(hass, client)
    await update_coordinator.async_config_entry_first_refresh()

    if coordinator.data is not None:
        await _async_reconcile_install_receipt(
            hass,
            entry,
            address.stored_value,
            coordinator.data.health,
        )

    platforms = list(PLATFORMS)
    if native_enabled_for(hass, entry):
        platforms.extend(NATIVE_ONLY_PLATFORMS)
    if satellite_known(hass, entry):
        platforms.append(Platform.ASSIST_SATELLITE)
    runtime_data = entry.runtime_data = HaPaneldRuntimeData(
        client=client,
        coordinator=coordinator,
        update_coordinator=update_coordinator,
        platforms=platforms,
        authority=effective_authority(hass, entry),
    )
    await async_forget_removed_panel(hass, entry)
    # Before any platform loads, so no entity of this entry is loaded, and
    # before the update listener, so the record's writes reload nothing.
    await async_apply_cutover(hass, entry)
    runtime_data.cutover_reconciliation_pending = cutover_reconciliation_waiting(
        hass, entry
    )
    async_start_guards(hass, entry)
    entry.async_on_unload(coordinator.async_follow_session())
    entry.async_on_unload(
        lambda: async_get_sessions(hass).close_entry(
            entry.entry_id, REASON_ENTRY_UNLOADED
        )
    )
    entry.async_on_unload(
        lambda: async_get_embed_sessions(hass).end_entry(
            entry.entry_id, EMBED_ENTRY_UNLOADED
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    entry.async_on_unload(async_follow_voice(hass, entry, runtime_data.platforms))
    # The coordinator's listeners run on every poll and whenever the session
    # opens or closes, which is when the card can learn something new.
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: async_refresh_panel_device(
                hass,
                entry.entry_id,
                panel_device_info(
                    entry.entry_id,
                    coordinator.data,
                    client.configuration_url,
                    entry.title,
                    coordinator.app_build,
                ),
            )
        )
    )
    # Register last so this coordinator listener is removed first on unload,
    # even when a later teardown callback fails.
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_request_cutover_reconciliation(hass, entry, runtime_data)
        )
    )
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Regrant the panel's session, and reload the entry when its authority changed.

    The reload runs the cutover the new authority asks for. Every other write
    keeps the entry loaded: binding, the cutover record, and a changed address,
    which the running client simply polls from now on, so the panel's session
    survives its own repair.
    """
    async_apply_authority(hass, entry)
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return
    if effective_authority(hass, entry) != runtime_data.authority:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    address = normalize_address(entry.data[CONF_ADDRESS])
    if address != runtime_data.client.address:
        runtime_data.client.address = address
        await runtime_data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Unload a ha-paneld config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )


async def async_remove_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Withdraw a removed panel's request and issues, and forget its last session.

    Home Assistant has unloaded the entry and dropped it from its entries, but
    not yet cleared its registry entries: the moved entities go back to MQTT
    now, and the panel is remembered so its next hello learns of the removal.
    """
    async_delete_binding_issue(hass, entry.entry_id)
    async_delete_cutover_issues(hass, entry.entry_id)
    async_delete_merged_identity_issue(hass, entry)
    async_get_sessions(hass).forget_entry(entry.entry_id)
    record = cutover_record(entry)
    recorded_did = None if record is None else record.get("did")
    await async_release_removed_entry(hass, entry)
    await async_remember_removed_panel(
        hass, entry, recorded_did if isinstance(recorded_did, str) else None
    )


async def async_reload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Reload a ha-paneld config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

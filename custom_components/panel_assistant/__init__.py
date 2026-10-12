"""The ha-paneld integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from .blob import async_get_blobs
from .browser_delivery import async_register_browser_delivery
from .browser_panel import async_register_browser_panel
from .build_feed import BuildFeedError, normalize_feed_url
from .client import HaPaneldClient, PanelHealth, normalize_address
from .const import CONF_AUTHORITY, DOMAIN
from .coordinator import HaPaneldDataUpdateCoordinator
from .device import (
    async_refresh_panel_device,
    panel_device_info,
    panel_reported_name,
)
from .discovery import async_offer_migration_targets
from .embed import (
    REASON_ENTRY_UNLOADED as EMBED_ENTRY_UNLOADED,
)
from .embed import (
    async_get_embed_sessions,
    async_setup_embed,
)
from .feed_coordinator import CONF_BUILD_FEED, DATA_BUILD_FEED, BuildFeedCoordinator
from .ha_url import async_setup_connection_info
from .install_artifacts import register_feed_download_host
from .install_executor import (
    async_get_install_executor,
    async_resume_loaded_install_jobs,
)
from .lifecycle import async_setup_lifecycle
from .microphone_repair import async_delete_microphone_issue
from .native import CONF_NATIVE_ENTITIES, NATIVE_ONLY_PLATFORMS
from .panel_move import (
    async_evaluate_successor_move,
    async_recover_moved_identity,
    async_restore_move_offer,
)
from .permission_repair import permission_issue_id
from .proximity_repair import proximity_issue_id
from .removed_panels import (
    async_forget_removed_panel,
    async_load_removed_panels,
    async_remember_removed_panel,
)
from .restart_repair import async_setup_restart_check
from .transport import (
    REASON_ENTRY_UNLOADED,
    async_apply_update_policy,
    async_delete_binding_issue,
    async_delete_merged_identity_issue,
    async_get_sessions,
    async_setup_transport,
)
from .update_coordinator import PanelUpdateCoordinator
from .update_route_repair import no_update_route_issue_id
from .upgrade_repairs import (
    async_delete_upgrade_issues,
    async_evaluate_app_too_old,
    async_raise_needs_090,
    needs_090,
    needs_090_issue_id,
)
from .voice import (
    async_follow_voice,
    async_load_voice,
    async_setup_voice,
    satellite_known,
)
from .voice_stream import async_get_voice_stream, async_setup_voice_stream

PLATFORMS = [Platform.SENSOR, Platform.UPDATE, *NATIVE_ONLY_PLATFORMS]
# Panels are config entries. YAML holds only development options: an optional
# build feed, without which nothing is fetched from anywhere but GitHub
# releases. ``native_entities`` is still accepted, so a configuration that
# names it keeps loading, and ignored: every panel is native.
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
    if CONF_NATIVE_ENTITIES in config.get(DOMAIN, {}):
        _LOGGER.warning(
            "%s: %s is no longer used and can be removed: every panel talks to"
            " Panel Assistant over its own connection",
            DOMAIN,
            CONF_NATIVE_ENTITIES,
        )
    # Before the commands exist, so no hello is answered without them.
    await async_load_removed_panels(hass)
    await async_setup_connection_info(hass)
    await async_setup_lifecycle(hass)
    await async_setup_voice_stream(hass)
    async_setup_transport(hass)
    async_setup_voice(hass)
    async_setup_embed(hass)
    async_register_browser_delivery(hass)
    async_get_blobs(hass)
    await async_register_browser_panel(hass)
    await async_setup_restart_check(hass)
    # Installing or updating Panel Assistant means a restart, and someone at
    # work in Home Assistant: audit known panels at once, not at the first dhcp
    # event, which can be hours away on a network that names few devices.
    async_at_started(hass, async_offer_migration_targets)
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
    voice_setup: Callable[[], Awaitable[None]] | None = None


type HaPaneldConfigEntry = ConfigEntry[HaPaneldRuntimeData]


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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Drop the authority option: every panel is native.

    A minor version, so Panel Assistant 0.9.0, which a panel never moved off
    MQTT must go back to, still loads the entry. The move record stays.
    """
    if entry.version == 1 and entry.minor_version < 2:
        options = {
            key: value for key, value in entry.options.items() if key != CONF_AUTHORITY
        }
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Set up ha-paneld from a config entry."""
    from .identity import reconcile_identity

    await _async_resume_install_jobs(hass)
    # A finished move may have left its new identity saved in the entity
    # registry only; adopt it before anything compares identities.
    await async_recover_moved_identity(hass, entry)
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
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="panel_identity_conflict"
        )
    # Before anything is created: entities set up beside a panel's MQTT ones
    # would split their history, and only Panel Assistant 0.9.0 can move them.
    health = None if coordinator.data is None else coordinator.data.health
    if needs_090(hass, entry, health):
        async_raise_needs_090(hass, entry)
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="mqtt_panel_needs_090",
            translation_placeholders={"panel": entry.title},
        )
    ir.async_delete_issue(hass, DOMAIN, needs_090_issue_id(entry.entry_id))
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
    runtime_data = entry.runtime_data = HaPaneldRuntimeData(
        client=client,
        coordinator=coordinator,
        update_coordinator=update_coordinator,
        platforms=platforms,
    )
    await async_forget_removed_panel(hass, entry)
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

    def card() -> DeviceInfo:
        return panel_device_info(
            entry.entry_id, coordinator.data, entry.title, coordinator.app_build
        )

    # The one writer of the panel's device card: created here, before any
    # entity links to it by identifier, then kept current. The coordinator's
    # listeners run on every poll and whenever the session opens or closes,
    # which is when the card can learn something new.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **card())

    def refresh_card() -> None:
        async_refresh_panel_device(
            hass, entry.entry_id, card(), panel_reported_name(coordinator.data)
        )

    refresh_card()
    entry.async_on_unload(coordinator.async_add_listener(refresh_card))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    if satellite_known(hass, entry):
        await async_load_voice(hass, entry, platforms)
    runtime_data.voice_setup = async_follow_voice(hass, entry, platforms)
    # A panel still on the old app id is offered the move to the new app.
    await async_restore_move_offer(hass, entry)
    async_evaluate_successor_move(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: async_evaluate_successor_move(hass, entry)
        )
    )
    # After the move offer, which stands in for this when both apply.
    async_evaluate_app_too_old(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: async_evaluate_app_too_old(hass, entry))
    )
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Regrant the panel's session when its update grant changed.

    Every write keeps the entry loaded: binding, and a changed address, which
    the running client simply polls from now on, so the panel's session
    survives its own repair.
    """
    async_apply_update_policy(hass, entry)
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return
    address = normalize_address(entry.data[CONF_ADDRESS])
    if address != runtime_data.client.address:
        runtime_data.client.address = address
        await runtime_data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> bool:
    """Unload a ha-paneld config entry."""
    if entry.runtime_data.voice_setup is not None:
        await entry.runtime_data.voice_setup()
    async_delete_microphone_issue(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )


async def async_remove_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Withdraw a removed panel's request and issues, and forget its last session.

    The panel is remembered so its next hello learns of the removal.
    """
    async_delete_binding_issue(hass, entry.entry_id)
    ir.async_delete_issue(hass, DOMAIN, permission_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, proximity_issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, no_update_route_issue_id(entry.entry_id))
    async_delete_microphone_issue(hass, entry.entry_id)
    async_delete_upgrade_issues(hass, entry.entry_id)
    async_delete_merged_identity_issue(hass, entry)
    async_get_sessions(hass).forget_entry(entry.entry_id)
    # The panel's Sendspin key goes with its entry; a re-added panel gets a new one.
    if (voice_stream := async_get_voice_stream(hass)) is not None:
        await voice_stream.async_revoke(entry.entry_id)
    await async_remember_removed_panel(hass, entry)


async def async_reload_entry(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Reload a ha-paneld config entry."""
    await hass.config_entries.async_reload(entry.entry_id)

"""One device-card projection shared by every platform this integration exposes.

Two byte-identical copies of this used to live in `sensor.py` and `update.py`, which is
how a newly added field goes missing from one card. Every platform calls this instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED

from .client import PanelHealth
from .const import DOMAIN
from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
from .transport_protocol import MANAGE_HA_AREA_REFRESH, MANAGE_SETTINGS
from .transport_sessions import async_get_sessions, async_manage, session_manages

_LOGGER = logging.getLogger(__name__)

# The app's Configure page flashes and scrolls to the element its hash names; this is
# its Presence & wake card. A page without it opens at the top.
PROXIMITY_SETUP_ANCHOR = "cfg-proximity-learning"


def sidebar_url(entry_id: str, page: str = "", anchor: str = "") -> str:
    """Return the sidebar path that opens a panel, optionally at one of its pages."""
    return "/".join(("/panel-assistant", entry_id, page, anchor)).rstrip("/")


# The card's identifier is this config entry's own. Its MAC connections are the panel's
# hardware addresses: since Home Assistant 2026.8 a device belongs to one config entry,
# so a device another integration registers with the same MAC (the Shelly integration's
# for a Shelly Wall Display, Fully Kiosk's, ESPHome's) stays its own device and each
# card lists the other as a linked device.


def panel_display_name(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Name a panel as Home Assistant does, including the user's device rename."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    if device is None:
        return entry.title
    return device.name_by_user or device.name or entry.title


def panel_device_info(
    entry_id: str,
    snapshot: PanelSnapshot | None,
    fallback_name: str | None = None,
    app_build: tuple[str, int] | None = None,
    panel_url: str | None = None,
) -> DeviceInfo:
    """Return the fullest device card the panel's own reported facts support.

    The card's link opens the panel in the sidebar, or, given `panel_url`
    because the sidebar cannot show the panel now, the panel's own page, so
    its owner can still reach the settings that would reconnect it.

    With no snapshot yet, because the stored address has not answered since
    the entry loaded, the card carries the entry's own name. A fact nobody has
    stated is left out rather than cleared, so the registry keeps the last one
    the panel reported; the session's own version and build fill the firmware
    line whether or not the address answers.
    """
    health = snapshot.health if snapshot is not None else None
    status = snapshot.status if snapshot is not None else None
    device = status.panel_assistant_device if status is not None else None

    name: str | None
    if device is not None and device.name:
        name = device.name
    elif health is not None:
        name = health.panel_id
    else:
        name = fallback_name
    info = DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name=name,
        # Home Assistant turns this scheme into a same-window, same-origin link.
        configuration_url=panel_url or f"homeassistant:/{sidebar_url(entry_id)}",
    )
    software = _software_version(health, app_build)
    if software is not None:
        info["sw_version"] = software
    hardware = status.panel_assistant_hardware if status is not None else None
    if hardware:
        # The vendor firmware leads: most panels never change their Android release.
        parts = [
            hardware.get("firmware"),
            f"Android {hardware['android_release']}"
            if hardware.get("android_release")
            else None,
        ]
        if any(parts):
            info["hw_version"] = " · ".join(str(part) for part in parts if part)
        if hardware.get("serial_number"):
            info["serial_number"] = str(hardware["serial_number"])
        macs = hardware.get("network_macs")
        if isinstance(macs, list):
            info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac) for mac in macs}
    if device is None:
        return info

    if device.manufacturer:
        info["manufacturer"] = device.manufacturer
    if device.model:
        info["model"] = device.model
    # Home Assistant applies suggested_area only when it first registers the device and
    # never overrides a later manual move, matching the panel's own request semantics.
    if device.area:
        info["suggested_area"] = device.area
    return info


def version_with_build(version: str, build: int) -> str:
    """Display the exact version and build number without language-specific prose."""
    return f"{version} ({build})"


def _software_version(
    health: PanelHealth | None, app_build: tuple[str, int] | None
) -> str | None:
    """Name the running app's version with its build number."""
    if app_build is not None:
        return version_with_build(*app_build)
    if health is None:
        return None
    if health.version_code is not None:
        return version_with_build(health.version, health.version_code)
    return health.version


def _is_null_area_name(name: str) -> bool:
    return name.strip().casefold() == "null"


def is_literal_null_area(hass: HomeAssistant, area_id: str | None) -> bool:
    """Identify the accidental area by its name, preserving every other area ID."""
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area is not None and _is_null_area_name(area.name)


def panel_reported_name(snapshot: PanelSnapshot | None) -> str | None:
    """The name the panel itself reports (its friendly name), or None."""
    status = snapshot.status if snapshot is not None else None
    device = status.panel_assistant_device if status is not None else None
    return device.name if device is not None and device.name else None


@callback
def async_refresh_panel_device(
    hass: HomeAssistant,
    entry_id: str,
    info: DeviceInfo,
    reported_name: str | None = None,
) -> None:
    """Bring an already registered card up to date with what is now known.

    Entities write their card only when they are added, which is before the
    panel's session arrives and before any later upgrade, so without this the
    card would keep whatever the first answer said. That includes the name: a
    panel renamed on its own page (its friendly name) renames its card, while
    a rename made in Home Assistant, kept as ``name_by_user``, still wins. Only
    a name the panel reported replaces it, never the panel id or entry title
    the card falls back to before the panel has answered.
    """
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    if device is None:
        return
    connections = info.get("connections", UNDEFINED)
    # A field the card leaves out keeps its registered value.
    registry.async_update_device(
        device.id,
        area_id=None if is_literal_null_area(hass, device.area_id) else UNDEFINED,
        name=reported_name if reported_name else UNDEFINED,
        manufacturer=info.get("manufacturer", UNDEFINED),
        model=info.get("model", UNDEFINED),
        sw_version=info.get("sw_version", UNDEFINED),
        hw_version=info.get("hw_version", UNDEFINED),
        serial_number=info.get("serial_number", UNDEFINED),
        # Replaced, not merged, so an address the panel no longer has stops linking.
        # Only when changed: Home Assistant announces any replacement as an update,
        # and every dashboard refetches the whole device registry on each one.
        new_connections=UNDEFINED if connections == device.connections else connections,
        # Replace links registered by earlier versions as well.
        configuration_url=info.get("configuration_url", UNDEFINED),
    )


@callback
def async_follow_panel_area(
    hass: HomeAssistant, entry: ConfigEntry
) -> Callable[[], None]:
    """Ask the panel to read its Area again when Home Assistant moves its device.

    The panel owns the Area rule (its own choice, Home Assistant's or a write-back),
    so no value is sent. A panel that does not know the request refuses it and keeps
    its slower scheduled read.
    """
    registry = dr.async_get(hass)

    async def ask() -> None:
        session = async_get_sessions(hass).get(entry.entry_id)
        if not session_manages(session):
            return
        assert session is not None
        try:
            await async_manage(hass, session, MANAGE_HA_AREA_REFRESH)
        except HomeAssistantError as err:
            _LOGGER.debug(
                "%s: the panel did not read its Area again: %s", entry.title, err
            )

    @callback
    def registry_changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] != "update" or "area_id" not in event.data["changes"]:
            return
        device = registry.async_get(event.data["device_id"])
        if device is not None and (DOMAIN, entry.entry_id) in device.identifiers:
            hass.async_create_background_task(
                ask(), f"{DOMAIN} area read {entry.entry_id}"
            )

    return hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, registry_changed)


@callback
def async_follow_panel_name(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: HaPaneldDataUpdateCoordinator
) -> Callable[[], None]:
    """Deliver explicit HA renames, including intent saved while disconnected.

    The registry alias is the durable intent. Removing it withdraws pending
    delivery; it never renames the panel to an invented fallback. A successful
    setting response acknowledges app settings only, not a vendor UI rename.
    """
    registry = dr.async_get(hass)
    task: asyncio.Task[None] | None = None
    attempted: tuple[str, str] | None = None
    closed = False

    def desired_name() -> str | None:
        device = registry.async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        name = device.name_by_user if device is not None else None
        return name if name and name.strip() else None

    async def deliver() -> None:
        nonlocal attempted, task
        try:
            while not closed and not hass.is_stopping:
                name = desired_name()
                session = async_get_sessions(hass).get(entry.entry_id)
                if (
                    name is None
                    or not session_manages(session)
                    or coordinator.identity_mismatch
                ):
                    return
                assert session is not None
                key = (session.token, name)
                if key == attempted:
                    return
                # Refusals are retried on reconnect or a new owner rename,
                # never on each poll of a panel that cannot accept the setting.
                attempted = key
                try:
                    await async_manage(
                        hass, session, MANAGE_SETTINGS, settings={"friendly_name": name}
                    )
                except HomeAssistantError as err:
                    _LOGGER.warning(
                        "%s: the panel did not accept the Home Assistant name: %s",
                        entry.title,
                        err,
                    )
                # Read the registry again: changes made during delivery coalesce
                # to the latest alias, without concurrent writes arriving late.
        finally:
            task = None

    @callback
    def changed() -> None:
        nonlocal task, attempted
        if closed:
            return
        if desired_name() is None:
            attempted = None
            return
        if task is None or task.done():
            task = hass.async_create_background_task(
                deliver(),
                f"{DOMAIN} deliver device name {entry.entry_id}",
                # Session-open listeners run before the hello grant is sent.
                eager_start=False,
            )

    @callback
    def registry_changed(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        if event.data["action"] != "update":
            return
        if "name_by_user" not in event.data["changes"]:
            return
        device = registry.async_get(event.data["device_id"])
        if (
            device is not None
            and device.config_entry_id == entry.entry_id
            and (DOMAIN, entry.entry_id) in device.identifiers
        ):
            changed()

    unsubscribe_registry = hass.bus.async_listen(
        dr.EVENT_DEVICE_REGISTRY_UPDATED, registry_changed
    )
    unsubscribe_coordinator = coordinator.async_add_listener(changed)

    @callback
    def close() -> None:
        nonlocal closed
        closed = True
        unsubscribe_registry()
        unsubscribe_coordinator()
        if task is not None:
            task.cancel()

    changed()
    return close

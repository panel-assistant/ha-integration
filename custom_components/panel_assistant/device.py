"""One device-card projection shared by every platform this integration exposes.

Two byte-identical copies of this used to live in `sensor.py` and `update.py`, which is
how a newly added field goes missing from one card. Every platform calls this instead.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import UNDEFINED

from .client import PanelHealth
from .const import DOMAIN
from .coordinator import PanelSnapshot

# The MQTT bridge registers `ha-paneld-<panel_id>` and `ha-paneld-aid-<android_id>`, and
# Home Assistant merges a device on ANY matching identifier. Adopting either, or
# publishing a MAC through `connections`, would make two config entries write the same
# device and risk the MQTT bridge's authority. This integration keeps its own
# config-entry-scoped identity.


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
) -> DeviceInfo:
    """Return the fullest device card the panel's own reported facts support.

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
        configuration_url=f"homeassistant://panel-assistant/{entry_id}",
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
    if device is None:
        return info

    if device.manufacturer:
        info["manufacturer"] = device.manufacturer
    if device.model:
        info["model"] = device.model
    # Home Assistant applies suggested_area only when it first registers the device and
    # never overrides a later manual move, matching the panel's own request semantics.
    if device.area and not _is_null_area_name(device.area):
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


@callback
def async_refresh_panel_device(
    hass: HomeAssistant, entry_id: str, info: DeviceInfo
) -> None:
    """Bring an already registered card up to date with what is now known.

    Entities write their card only when they are added, which is before the
    panel's session arrives and before any later upgrade, so without this the
    card would keep whatever the first answer said.
    """
    registry = dr.async_get(hass)
    device = registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    if device is None:
        return
    # A field the card leaves out keeps its registered value.
    registry.async_update_device(
        device.id,
        area_id=None if is_literal_null_area(hass, device.area_id) else UNDEFINED,
        manufacturer=info.get("manufacturer", UNDEFINED),
        model=info.get("model", UNDEFINED),
        sw_version=info.get("sw_version", UNDEFINED),
        hw_version=info.get("hw_version", UNDEFINED),
        serial_number=info.get("serial_number", UNDEFINED),
        # Replace links registered by earlier versions as well.
        configuration_url=info.get("configuration_url", UNDEFINED),
    )

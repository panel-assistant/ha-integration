"""One device-card projection shared by every platform this integration exposes.

Two byte-identical copies of this used to live in `sensor.py` and `update.py`, which is
how a newly added field goes missing from one card. Every platform calls this instead.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
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


def panel_device_info(
    entry_id: str,
    snapshot: PanelSnapshot | None,
    configuration_url: str,
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
        configuration_url=configuration_url,
        # The panel's Android release and build string told a user nothing they
        # could act on. Cleared explicitly, so a card registered by an earlier
        # version loses it and an older panel that still sends it is not shown it.
        hw_version=None,
    )
    software = _software_version(health, app_build)
    if software is not None:
        info["sw_version"] = software
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
    """Write a version and its build number the way the panel's MQTT card did.

    Not the Update entity's `0.9.8 build 904`, which is a version it parses
    back; this is text on a device card, where panels moving off MQTT keep
    reading what they read before.
    """
    return f"{version} (build {build})"


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
        manufacturer=info.get("manufacturer", UNDEFINED),
        model=info.get("model", UNDEFINED),
        sw_version=info.get("sw_version", UNDEFINED),
        hw_version=info.get("hw_version", UNDEFINED),
        # The address can move while the entry stays loaded.
        configuration_url=info.get("configuration_url", UNDEFINED),
    )

"""One device-card projection shared by every platform this integration exposes.

Two byte-identical copies of this used to live in `sensor.py` and `update.py`, which is
how a newly added field goes missing from one card. Every platform calls this instead.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import PanelSnapshot

# The MQTT bridge registers `ha-paneld-<panel_id>` and `ha-paneld-aid-<android_id>`, and
# Home Assistant merges a device on ANY matching identifier. Adopting either, or
# publishing a MAC through `connections`, would make two config entries write the same
# device and risk the MQTT bridge's authority. This integration keeps its own
# config-entry-scoped identity.
_APP_MODEL_FALLBACK = "ha-paneld"


def panel_device_info(
    entry_id: str,
    snapshot: PanelSnapshot | None,
    configuration_url: str,
    fallback_name: str | None = None,
) -> DeviceInfo:
    """Return the fullest device card the panel's own reported facts support.

    With no snapshot yet, because the stored address has not answered since
    the entry loaded, the card carries the entry's own name and no version.
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
        model=(
            device.model if device is not None and device.model else _APP_MODEL_FALLBACK
        ),
        sw_version=health.version if health is not None else None,
        configuration_url=configuration_url,
    )
    if device is None:
        return info

    if device.manufacturer:
        info["manufacturer"] = device.manufacturer
    if device.hw_version:
        info["hw_version"] = device.hw_version
    # Home Assistant applies suggested_area only when it first registers the device and
    # never overrides a later manual move, matching the panel's own request semantics.
    if device.area:
        info["suggested_area"] = device.area
    return info

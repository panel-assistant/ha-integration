"""Panels Home Assistant already knows through another integration.

The add-panel form offers them so nobody has to look up an address, and each is
raised as a discovery so it appears under Discovered before anyone opens Add
integration. A device is a definite target when its network ADB port answers,
because that is what the installer needs. Otherwise it is listed only when its
integration runs on nothing but Android screens; a relay with ADB closed is
left out, and a phone never appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.const import CONF_ADDRESS, CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import InvalidAddressError, normalize_address
from .const import DOMAIN
from .install_adb import ADB_PORT
from .install_network import is_allowed_install_address

# Integration domain: (display name, runs only on Android screens).
_HOST_SOURCES: dict[str, tuple[str, bool]] = {
    "fully_kiosk": ("Fully Kiosk", True),
    "shelly": ("Shelly", False),
}
_COMPANION = "Home Assistant Companion"
# The Companion app's "Wi-Fi IP address" sensor; Core prefixes the webhook id.
_COMPANION_IP_SUFFIX = "_wifi_ip_address"
# The Companion app registers these, enabled or not, only on a device with
# telephony (`FEATURE_TELEPHONY`): a phone, never a wall panel.
_COMPANION_TELEPHONY_SUFFIXES = ("_phone_state", "_mobile_data", "_sim_1")
_ADB_PROBE_SECONDS = 2


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    """One panel another integration knows, and whether its ADB answers."""

    address: str
    name: str
    source: str
    adb_open: bool


def _known(hass: HomeAssistant) -> list[tuple[str, str, str, str, bool]]:
    """Return (source entry id, host, name, source, panel_only) for each address."""
    found: list[tuple[str, str, str, str, bool]] = []
    for domain, (source, panel_only) in _HOST_SOURCES.items():
        for entry in hass.config_entries.async_entries(domain):
            host = entry.data.get(CONF_HOST)
            if isinstance(host, str) and host.strip():
                found.append((entry.entry_id, host, entry.title, source, panel_only))

    devices = dr.async_get(hass)
    # Homes hold hundreds of ESP32 boards, so ESPHome counts only for a device
    # reporting an Android model: ESPHome on Android is used by wall panels.
    for entry in hass.config_entries.async_entries("esphome"):
        host = entry.data.get(CONF_HOST)
        if isinstance(host, str) and any(
            (device.model or "").startswith("Android ")
            for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
        ):
            found.append((entry.entry_id, host, entry.title, "ESPHome", True))

    entities = er.async_get(hass)
    # One Companion registration (config entry) per device.
    for entry in hass.config_entries.async_entries("mobile_app"):
        registered = er.async_entries_for_config_entry(entities, entry.entry_id)
        if any(
            entity.unique_id.endswith(_COMPANION_TELEPHONY_SUFFIXES)
            for entity in registered
        ):
            continue
        for entity in registered:
            if not entity.unique_id.endswith(_COMPANION_IP_SUFFIX):
                continue
            state = hass.states.get(entity.entity_id)
            if state is None:
                continue
            try:
                # An offline device reports "unavailable", which must not become a host.
                ipaddress.ip_address(state.state)
            except ValueError:
                continue
            device = devices.async_get(entity.device_id) if entity.device_id else None
            name = (device.name_by_user or device.name) if device else None
            found.append(
                (
                    entry.entry_id,
                    state.state,
                    name or entity.entity_id,
                    _COMPANION,
                    False,
                )
            )
    return found


async def _async_adb_answers(host: str) -> bool:
    """Whether the host accepts a connection on the network ADB port."""
    try:
        async with asyncio.timeout(_ADB_PROBE_SECONDS):
            _reader, writer = await asyncio.open_connection(host, ADB_PORT)
    except OSError, TimeoutError:
        return False
    writer.close()
    with contextlib.suppress(OSError, TimeoutError, asyncio.CancelledError):
        await writer.wait_closed()
    return True


def _candidates(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Known, unconfigured LAN addresses, keyed by stored address; nothing probed."""
    configured = {
        entry.data.get(CONF_ADDRESS)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    candidates: dict[str, dict[str, Any]] = {}
    for key, host, name, source, panel_only in _known(hass):
        try:
            address = normalize_address(host)
        except InvalidAddressError:
            continue
        try:
            literal = ipaddress.ip_address(address.host)
        except ValueError:
            literal = None
        if literal is not None and not is_allowed_install_address(literal):
            continue
        value = address.stored_value
        if value in configured:
            continue
        # One device can be known twice; the panel-only sources come first.
        if value not in candidates:
            candidates[value] = {
                "key": f"migration:{key}",
                "address": value,
                "host": address.host,
                "name": name,
                "source": source,
                "panel_only": panel_only,
            }
    return candidates


async def async_check_migration_target(
    candidate: dict[str, Any],
) -> MigrationTarget | None:
    """Probe one candidate: a target when ADB answers or its source is panel-only."""
    adb_open = await _async_adb_answers(candidate["host"])
    if not (adb_open or candidate["panel_only"]):
        return None
    return MigrationTarget(
        candidate["address"], candidate["name"], candidate["source"], adb_open
    )


async def async_find_migration_targets(hass: HomeAssistant) -> list[MigrationTarget]:
    """Return known, unconfigured panels: ADB-open ones first."""
    checked = await asyncio.gather(
        *(async_check_migration_target(c) for c in _candidates(hass).values())
    )
    targets = [target for target in checked if target is not None]
    return sorted(targets, key=lambda target: not target.adb_open)


@callback
def async_offer_migration_targets(hass: HomeAssistant) -> None:
    """Raise each known, unconfigured candidate as a discovery.

    Only registries are read here. Each discovery flow probes its candidate
    after its unique id has turned away one that is ignored or already offered,
    so repeated searches probe nothing twice.
    """
    for candidate in _candidates(hass).values():
        discovery_flow.async_create_flow(
            hass,
            DOMAIN,
            {"source": SOURCE_INTEGRATION_DISCOVERY},
            candidate,
        )


def address_schema(targets: list[MigrationTarget] | None) -> vol.Schema | None:
    """The address field offering these panels, still accepting a typed address."""
    if not targets:
        return None
    options = [
        SelectOptionDict(
            value=target.address,
            label=(
                f"{'✓ ' if target.adb_open else ''}"
                f"{target.name} · {target.source} · {target.address}"
            ),
        )
        for target in targets
    ]
    return vol.Schema(
        {
            vol.Required(CONF_ADDRESS): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )

"""Panels Home Assistant already knows through another integration.

The add-panel form offers them so nobody has to look up an address. A device is
a definite target when its network ADB port answers, because that is what the
installer needs. Otherwise it is listed only when its integration runs on
nothing but Android screens; a phone or a relay with ADB closed is left out.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.const import CONF_ADDRESS, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
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
    "esphome": ("ESPHome", False),
    "shelly": ("Shelly", False),
}
_COMPANION = "Home Assistant Companion"
# The Companion app's "Wi-Fi IP address" sensor; Core prefixes the webhook id.
_COMPANION_IP_SUFFIX = "_wifi_ip_address"
_ADB_PROBE_SECONDS = 2


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    """One panel another integration knows, and whether its ADB answers."""

    address: str
    name: str
    source: str
    adb_open: bool


def _known(hass: HomeAssistant) -> list[tuple[str, str, str, bool]]:
    """Return (host, name, source, panel_only) for every address on record."""
    found: list[tuple[str, str, str, bool]] = []
    for domain, (source, panel_only) in _HOST_SOURCES.items():
        for entry in hass.config_entries.async_entries(domain):
            host = entry.data.get(CONF_HOST)
            if isinstance(host, str) and host.strip():
                found.append((host, entry.title, source, panel_only))

    devices = dr.async_get(hass)
    for entity in er.async_get(hass).entities.values():
        if entity.platform != "mobile_app" or not entity.unique_id.endswith(
            _COMPANION_IP_SUFFIX
        ):
            continue
        state = hass.states.get(entity.entity_id)
        if state is None:
            continue
        try:
            # An offline phone reports "unavailable", which must not become a host.
            ipaddress.ip_address(state.state)
        except ValueError:
            continue
        device = devices.async_get(entity.device_id) if entity.device_id else None
        name = (device.name_by_user or device.name) if device else None
        found.append((state.state, name or entity.entity_id, _COMPANION, False))
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


async def async_find_migration_targets(hass: HomeAssistant) -> list[MigrationTarget]:
    """Return known, unconfigured panels: ADB-open ones first."""
    configured = {
        entry.data.get(CONF_ADDRESS)
        for entry in hass.config_entries.async_entries(DOMAIN)
    }
    candidates: dict[str, tuple[str, str, bool]] = {}
    hosts: dict[str, str] = {}
    for host, name, source, panel_only in _known(hass):
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
            candidates[value] = (name, source, panel_only)
            hosts[value] = address.host

    answers = await asyncio.gather(*(_async_adb_answers(h) for h in hosts.values()))
    targets = []
    for (value, (name, source, panel_only)), adb_open in zip(
        candidates.items(), answers, strict=True
    ):
        if adb_open or panel_only:
            targets.append(MigrationTarget(value, name, source, adb_open))
    return sorted(targets, key=lambda target: not target.adb_open)


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

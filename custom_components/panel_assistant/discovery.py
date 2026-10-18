"""Every way a panel is discovered, and the one-card-per-panel rule.

The config flow's discovery steps delegate here: the app's own announcement
(`_ha-paneld._tcp`), a vendor's announcement (profile `vendor_mdns`), Android's
network ADB announcement, and DHCP, which is the cue to raise the panels other
integrations already know.

Those known panels are also offered in the add-panel form so nobody has to look
up an address, and each is raised as a discovery so it appears under Discovered
before anyone opens Add integration. A device is a definite target when its
network ADB port answers, because that is what the installer needs. Otherwise it
is listed only when its integration runs on nothing but Android screens; a relay
with ADB closed is left out, and a phone never appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import logging
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ADDRESS, CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import discovery_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .adb_protocol import ADB_PORT
from .client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidAddressError,
    InvalidResponseError,
    is_valid_discovery_id,
    normalize_address,
)
from .const import DEFAULT_PORT, DOMAIN
from .ha_url import async_offer_ha_url
from .identity import accept_health, is_installation, reinstalled
from .install_network import is_allowed_install_address
from .provisioning import InstallTargetState, async_probe_install_target

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .config_flow import HaPaneldConfigFlow

_LOGGER = logging.getLogger(__name__)

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
# Android's adbd advertises this while network ADB listens (manifest zeroconf).
ADB_SERVICE_TYPE = "_adb._tcp.local."
# Services a panel's own vendor software announces, whether or not the app runs,
# with the label its card shows. Each mirrors a device profile's
# `platform.vendor_mdns`; the manifest's zeroconf matchers narrow it by name.
VENDOR_SERVICE_TYPES: dict[str, str] = {"_shelly._tcp.local.": "Shelly Wall Display"}
# An ignored card keeps its address, because the same device can be raised by
# the registries under one identity and by its ADB announcement under another.
_IGNORED_ADDRESS = "ignored_address"
# A candidate at the same address is raised at most this often, so a device that
# is not a panel (ADB closed) is probed once an hour, not on every dhcp event.
_REOFFER_SECONDS = 3600
# Set in a card's own title placeholders the moment someone presses its button:
# the flow is theirs from then on, though Core keeps reporting the card's step
# until the press has been handled. The card's title shows only {name}.
CONFIRMED = "confirmed"
# Set in a card's title placeholders while one of its steps runs (the flow sets
# it; see `closes_when_done`). A card is never aborted then: `close_offers` writes
# CLOSING instead, and the flow ends itself once that step returns.
RUNNING = "running"
CLOSING = "closing"
_DATA_OFFERED = "migration_offered"
_DATA_DISCOVERY_NAMES = "discovery_names"
_FRIENDLY_NAME_MAX_LENGTH = 64


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


def _lan_address(host: str) -> tuple[str, str] | None:
    """(stored value, host) for an address the installer may use, else None."""
    try:
        address = normalize_address(host)
    except InvalidAddressError:
        return None
    try:
        literal = ipaddress.ip_address(address.host)
    except ValueError:
        literal = None
    if literal is not None and not is_allowed_install_address(literal):
        return None
    return address.stored_value, address.host


def _candidates(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Known LAN addresses, keyed by stored address; nothing probed."""
    candidates: dict[str, dict[str, Any]] = {}
    for key, host, name, source, panel_only in _known(hass):
        lan = _lan_address(host)
        if lan is None:
            continue
        value, bare = lan
        # One device can be known twice; the panel-only sources come first.
        if value not in candidates:
            candidates[value] = {
                "key": f"migration:{key}",
                "address": value,
                "host": bare,
                "name": name,
                "source": source,
                "panel_only": panel_only,
            }
    return candidates


def adb_candidate(hass: HomeAssistant, host: str, serial: str) -> dict[str, Any] | None:
    """The device advertising network ADB, unless it is off the LAN.

    Network ADB is what the installer needs, so any such device is a candidate.
    One another integration already knows keeps that integration's name and
    card, so it is never offered twice.
    """
    return _unconfigured(
        hass, host, f"adb:{serial}", f"Android {serial}", "network ADB", False
    )


def vendor_candidate(
    hass: HomeAssistant, host: str, instance: str, source: str
) -> dict[str, Any] | None:
    """The panel its vendor's software announces, unless it is off the LAN.

    The announcement names a panel model, so it is offered even with ADB closed.
    """
    return _unconfigured(hass, host, f"mdns:{instance.lower()}", instance, source, True)


def _unconfigured(
    hass: HomeAssistant, host: str, key: str, name: str, source: str, panel_only: bool
) -> dict[str, Any] | None:
    """A candidate card at this host, or the one another integration raises.

    A configured address is offered only after a clean-package probe.
    """
    lan = _lan_address(host)
    if lan is None:
        return None
    value, bare = lan
    if (known := _candidates(hass).get(value)) is not None:
        # Keep the other integration's card, but a vendor announcement naming a
        # panel model still proves a panel where that integration cannot.
        return {**known, "panel_only": known["panel_only"] or panel_only}
    return {
        "key": key,
        "address": value,
        "host": bare,
        "name": name,
        "source": source,
        "panel_only": panel_only,
    }


def offer_blocked(hass: HomeAssistant, address: str, flow_id: str) -> bool:
    """Whether another card offers this address, or it was ignored."""
    if any(
        entry.source == SOURCE_IGNORE and entry.data.get(_IGNORED_ADDRESS) == address
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        return True
    return any(
        flow["flow_id"] != flow_id
        and flow["context"].get("title_placeholders", {}).get("address") == address
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )


@callback
def close_offers(
    hass: HomeAssistant,
    address: str,
    flow_id: str,
    *,
    pressed: bool = False,
    discovery_id: str | None = None,
) -> None:
    """Close every other card offering this address: its panel has a better one.

    Called where the panel's identity is proven at the address, by the app's
    own announcement or by an entry; every card is keyed by its channel, so
    Core's own same-identity close never reaches the cards of other channels.
    Only a card still waiting is closed: one a person has confirmed may be
    installing the very app whose announcement calls this, and still holds
    the address, so that announcement then stands down instead. Once the panel
    has an entry, `pressed` closes those too: nothing they could add remains,
    and one left on a form after its dialog was shut would stay listed. One
    whose step is running is only asked to close, so a person's press never
    meets a vanished flow.
    """
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        placeholders = flow["context"].get("title_placeholders", {})
        if (
            flow["flow_id"] == flow_id
            or (
                flow["context"].get("unique_id") != discovery_id
                if discovery_id is not None
                else placeholders.get("address") != address
            )
            or not (pressed or CONFIRMED not in placeholders)
        ):
            continue
        if RUNNING in placeholders:
            flow["context"]["title_placeholders"] = {**placeholders, RUNNING: CLOSING}
        else:
            hass.config_entries.flow.async_abort(flow["flow_id"])


def ignored_card_data(hass: HomeAssistant, unique_id: str) -> dict[str, str]:
    """Entry data for an Ignore: the address of the card being ignored, if any."""
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        if flow["context"].get("unique_id") != unique_id:
            continue
        address = flow["context"].get("title_placeholders", {}).get("address")
        if isinstance(address, str):
            return {_IGNORED_ADDRESS: address}
    return {}


async def async_check_migration_target(
    hass: HomeAssistant,
    candidate: dict[str, Any],
) -> MigrationTarget | None:
    """Probe one candidate: a target when ADB answers or its source is panel-only."""
    configured = {
        entry.entry_id
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get(CONF_ADDRESS) == candidate["address"]
    }
    if configured or any(
        is_installation(entry) for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        try:
            address = normalize_address(candidate["address"])
            health = await HaPaneldClient(
                async_get_clientsession(hass), address
            ).async_get_health()
        except InvalidAddressError, CannotConnectError, InvalidResponseError:
            pass
        except Exception:
            _LOGGER.debug("Could not verify a discovery target", exc_info=True)
            return None
        else:
            known = (
                hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, health.discovery_id
                )
                if health.discovery_id is not None
                else None
            )
            if configured or (known is not None and is_installation(known)):
                return None
    if configured:
        # An unavailable app is not evidence of removal. Only the normal ADB
        # classifier can offer its kept entry for a clean reinstall.
        try:
            address = normalize_address(candidate["address"])
            probe = await async_probe_install_target(address)
        except Exception:
            _LOGGER.debug(
                "Could not classify a configured discovery target", exc_info=True
            )
            return None
        if probe.state not in {
            InstallTargetState.INSTALL_CANDIDATE,
            InstallTargetState.ADB_UNAUTHORIZED,
        }:
            return None
    adb_open = await _async_adb_answers(candidate["host"])
    if any(
        entry.data.get(CONF_ADDRESS) == candidate["address"]
        and entry.entry_id not in configured
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        return None
    if not (adb_open or candidate["panel_only"]):
        return None
    return MigrationTarget(
        candidate["address"], candidate["name"], candidate["source"], adb_open
    )


async def async_find_migration_targets(hass: HomeAssistant) -> list[MigrationTarget]:
    """Return known, unconfigured panels: ADB-open ones first."""
    checked = await asyncio.gather(
        *(async_check_migration_target(hass, c) for c in _candidates(hass).values())
    )
    targets = [target for target in checked if target is not None]
    return sorted(targets, key=lambda target: not target.adb_open)


@callback
def async_offer_migration_targets(
    hass: HomeAssistant, *, recheck: bool = False
) -> None:
    """Raise each known, unconfigured candidate as a discovery.

    Only registries are read here. Each discovery flow probes its candidate
    after its unique id has turned away one that is ignored or already offered.
    One that was rejected is raised again after an hour, or at once from a new
    address, so turning ADB on later still brings it under Discovered.
    `recheck` skips that wait while someone is working in Home Assistant.
    """
    offered: dict[str, tuple[str, float]] = hass.data.setdefault(DOMAIN, {}).setdefault(
        _DATA_OFFERED, {}
    )
    now = time.monotonic()
    for candidate in _candidates(hass).values():
        last = offered.get(candidate["key"])
        if (
            not recheck
            and last is not None
            and last[0] == candidate["address"]
            and now - last[1] < _REOFFER_SECONDS
        ):
            continue
        offered[candidate["key"]] = (candidate["address"], now)
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


async def async_zeroconf(
    flow: HaPaneldConfigFlow, discovery_info: ZeroconfServiceInfo
) -> ConfigFlowResult:
    """Offer a verified local mDNS discovery for explicit confirmation."""
    if discovery_info.type == ADB_SERVICE_TYPE:
        return await _async_discovered_adb(flow, discovery_info)
    if discovery_info.type in VENDOR_SERVICE_TYPES:
        return await _async_discovered_vendor(flow, discovery_info)
    discovery_id = discovery_info.properties.get("did")
    if (
        discovery_info.port != DEFAULT_PORT
        or not isinstance(discovery_id, str)
        or not is_valid_discovery_id(discovery_id)
    ):
        return flow.async_abort(reason="invalid_discovery")
    return await _async_discovered_app(
        flow, discovery_info, discovery_id, discovery_info.properties.get("name")
    )


async def async_dhcp(
    flow: HaPaneldConfigFlow, discovery_info: DhcpServiceInfo
) -> ConfigFlowResult:
    """Any device on the network is the cue to offer panels other apps know.

    Without an entry nothing else runs this integration's code, and Core
    matches dhcp hostnames by their first character, hence the manifest's
    one matcher per character.
    """
    async_offer_migration_targets(flow.hass)
    return flow.async_abort(reason="not_panel")


async def async_integration_discovery(
    flow: HaPaneldConfigFlow, discovery_info: dict[str, Any]
) -> ConfigFlowResult:
    """Offer a panel another integration knows, unless ignored or added."""
    await flow.async_set_unique_id(discovery_info["key"])
    flow._abort_if_unique_id_configured()
    # The registries and the ADB announcement can name one device twice.
    if offer_blocked(flow.hass, discovery_info["address"], flow.flow_id):
        return flow.async_abort(reason="already_configured")
    target = await async_check_migration_target(flow.hass, discovery_info)
    if target is None:
        return flow.async_abort(reason="not_panel")
    # Again after the probe: the other route may have shown its card while
    # this one waited. Nothing awaits between here and publishing ours.
    if offer_blocked(flow.hass, discovery_info["address"], flow.flow_id):
        return flow.async_abort(reason="already_configured")
    flow._migration_target = target
    flow.context["title_placeholders"] = {
        "name": target.name,
        "address": target.address,
    }
    return await flow.async_step_confirm_migration()


async def async_ignore(
    flow: HaPaneldConfigFlow, user_input: dict[str, Any]
) -> ConfigFlowResult:
    """Ignore as Core does, keeping the card's address for the other route."""
    data = ignored_card_data(flow.hass, user_input["unique_id"])
    await flow.async_set_unique_id(user_input["unique_id"], raise_on_progress=False)
    return flow.async_create_entry(title=user_input["title"], data=data)


async def async_confirm_migration(
    flow: HaPaneldConfigFlow, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Take the discovered address into the ordinary add-panel path."""
    target = flow._migration_target
    assert target is not None
    if user_input is not None:
        _mark_confirmed(flow)
        return await flow.async_step_add_panel({CONF_ADDRESS: target.address})
    return flow.async_show_form(
        step_id="confirm_migration",
        data_schema=vol.Schema({}),
        description_placeholders={
            "name": markdown_literal(target.name),
            "source": target.source,
            "address": target.address,
        },
    )


async def async_confirm_discovery(
    flow: HaPaneldConfigFlow, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Re-verify the discovered panel before creating its first entry."""
    if flow._pending_address is None or flow._pending_discovery_id is None:
        return flow.async_abort(reason="unknown")
    if user_input is not None:
        _mark_confirmed(flow)
        try:
            health = await HaPaneldClient(
                async_get_clientsession(flow.hass), flow._pending_address
            ).async_get_health()
        except CannotConnectError, InvalidResponseError:
            return _show_discovery_confirmation(flow, {"base": "cannot_connect"})
        except Exception:
            _LOGGER.exception(
                "Unexpected exception while re-verifying discovered ha-paneld"
            )
            return _show_discovery_confirmation(flow, {"base": "unknown"})
        if health.discovery_id != flow._pending_discovery_id:
            return flow.async_abort(reason="invalid_discovery")
        # Adoption counts as Home Assistant starting this panel just as much
        # as installing it does, so a panel adopted mid-setup is handed the
        # address too rather than being asked for one Home Assistant knows.
        await async_offer_ha_url(
            flow.hass,
            HaPaneldClient(async_get_clientsession(flow.hass), flow._pending_address),
        )
        return await flow._async_create_panel_entry(flow._pending_address, health)

    return _show_discovery_confirmation(flow)


async def _async_discovered_app(
    flow: HaPaneldConfigFlow,
    discovery_info: ZeroconfServiceInfo,
    discovery_id: str,
    advertised_name: object,
) -> ConfigFlowResult:
    """Offer, or move to its new address, the panel with this discovery id."""
    # An IPv6 literal must be bracketed before a port can follow it; bare, its
    # colons read as a port and every IPv6-only panel was refused here.
    advertised = discovery_info.ip_address
    host = f"[{advertised}]" if advertised.version == 6 else str(advertised)
    try:
        address = normalize_address(host)
    except InvalidAddressError:
        return flow.async_abort(reason="invalid_discovery")

    flow._pending_discovery_id = discovery_id
    flow._pending_friendly_name = _presentation_safe_name(advertised_name)
    if flow.hass.config_entries.flow.async_has_matching_flow(flow):
        return flow.async_abort(reason="already_in_progress")
    await flow.async_set_unique_id(discovery_id)
    for existing in flow.hass.config_entries.async_entries(DOMAIN):
        if (
            existing.data.get(CONF_ADDRESS) == address.stored_value
            and existing.unique_id == discovery_id
        ):
            return flow.async_abort(reason="already_configured")

    try:
        health = await HaPaneldClient(
            async_get_clientsession(flow.hass), address
        ).async_get_health()
    except CannotConnectError, InvalidResponseError:
        return flow.async_abort(reason="invalid_discovery")
    except Exception:
        _LOGGER.exception("Unexpected exception while verifying discovered ha-paneld")
        return flow.async_abort(reason="unknown")
    if health.discovery_id != discovery_id:
        return flow.async_abort(reason="invalid_discovery")
    # The panel has just answered as itself here: whatever happens next, an
    # install card for this address is the same panel on a weaker channel.
    close_offers(flow.hass, address.stored_value, flow.flow_id)
    for existing in flow.hass.config_entries.async_entries(DOMAIN):
        if reinstalled(flow.hass, existing, health) and not _entry_holds_its_address(
            flow, existing
        ):
            return await flow._async_create_panel_entry(address, health)
        if existing.data.get(CONF_ADDRESS) == address.stored_value:
            accept_health(flow.hass, existing, health)
            return flow.async_abort(reason="already_configured")
    known = flow.hass.config_entries.async_entry_for_domain_unique_id(
        DOMAIN, discovery_id
    )
    if known is not None and not is_installation(known):
        return flow.async_abort(reason="already_configured")
    # A known panel advertising from a new address has moved, when its
    # stored address has stopped answering and it is not connected. Its
    # health has just answered as it at the new address, so the stored one
    # is replaced; the running entry polls the new address without a
    # reload, which would end the panel's session for nothing. The identity
    # and the health line are both public on the LAN, so an advertisement
    # never redirects a panel whose stored address still answers, nor one
    # holding a session: that session is the credentialed authority on
    # where the panel is, repairs the address itself, and reports when it
    # cannot. One failed poll is then never enough to move a live panel.
    if _entry_holds_its_address(flow):
        flow._abort_if_unique_id_configured()
    flow._abort_if_unique_id_configured(
        updates={CONF_ADDRESS: address.stored_value}, reload_on_update=False
    )
    # An Ignore on the card another channel showed covers the panel.
    if offer_blocked(flow.hass, address.stored_value, flow.flow_id):
        return flow.async_abort(reason="already_configured")

    flow._pending_address = address
    flow._pending_health = health
    # Without a title placeholder the card for every discovered panel falls back
    # to the bare integration name, so a fleet is a list of indistinguishable
    # duplicates. Prefer the human name the panel advertises, and fall back to the
    # panel id only when there is no usable name or the name is already taken.
    _apply_discovery_title(flow)
    return _show_discovery_confirmation(flow)


async def _async_discovered_adb(
    flow: HaPaneldConfigFlow, discovery_info: ZeroconfServiceInfo
) -> ConfigFlowResult:
    """Offer an Android device whose network ADB Android itself advertises."""
    advertised = discovery_info.ip_address
    host = f"[{advertised}]" if advertised.version == 6 else str(advertised)
    serial = discovery_info.name.removesuffix(f".{ADB_SERVICE_TYPE}")
    candidate = (
        adb_candidate(flow.hass, host, serial.removeprefix("adb-"))
        if discovery_info.port == ADB_PORT
        else None
    )
    if candidate is None:
        return flow.async_abort(reason="not_panel")
    try:
        await HaPaneldClient(
            async_get_clientsession(flow.hass), normalize_address(host)
        ).async_get_health()
    except CannotConnectError, InvalidResponseError:
        return await flow.async_step_integration_discovery(candidate)
    # The app already answers here; its own advertisement offers it.
    return flow.async_abort(reason="not_panel")


async def _async_discovered_vendor(
    flow: HaPaneldConfigFlow, discovery_info: ZeroconfServiceInfo
) -> ConfigFlowResult:
    """A panel found by its vendor's own announcement (profile `vendor_mdns`).

    The app's health line names the panel when the app answers, and the
    panel is then handled as if it had announced itself; a panel whose app
    does not answer is offered for installation like a network ADB device.
    """
    advertised = discovery_info.ip_address
    host = f"[{advertised}]" if advertised.version == 6 else str(advertised)
    try:
        health = await HaPaneldClient(
            async_get_clientsession(flow.hass), normalize_address(host)
        ).async_get_health()
    except InvalidAddressError:
        return flow.async_abort(reason="invalid_discovery")
    except CannotConnectError, InvalidResponseError:
        instance = discovery_info.name.removesuffix(f".{discovery_info.type}")
        candidate = vendor_candidate(
            flow.hass, host, instance, VENDOR_SERVICE_TYPES[discovery_info.type]
        )
        if candidate is None:
            return flow.async_abort(reason="not_panel")
        return await flow.async_step_integration_discovery(candidate)
    if health.discovery_id is None:
        return flow.async_abort(reason="invalid_discovery")
    return await _async_discovered_app(flow, discovery_info, health.discovery_id, None)


def _apply_discovery_title(flow: HaPaneldConfigFlow) -> None:
    """Title this flow, disambiguating both sides of a friendly-name clash."""
    assert flow._pending_health is not None
    panel_id = flow._pending_health.panel_id
    friendly = flow._pending_friendly_name
    announced = _announced_discovery_names(flow)
    announced[flow.flow_id] = (friendly, panel_id)

    if friendly is None:
        _set_discovery_title(flow, panel_id)
        return

    clashing = {
        flow_id: other_panel_id
        for flow_id, (other_friendly, other_panel_id) in announced.items()
        if flow_id != flow.flow_id and other_friendly == friendly
    }
    taken = bool(clashing) or any(
        entry.title == friendly for entry in flow._async_current_entries()
    )
    # A clash is symmetric. Qualifying only the newcomer would leave two cards
    # that still cannot be told apart, so qualify the ones already showing it.
    for progress in flow.hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        other_panel_id = clashing.get(progress["flow_id"])
        if other_panel_id is not None:
            # Keep its address: the other channels and an Ignore match on it.
            progress["context"]["title_placeholders"] = {
                **progress["context"]["title_placeholders"],
                "name": _qualified_name(friendly, other_panel_id),
            }
    _set_discovery_title(
        flow, _qualified_name(friendly, panel_id) if taken else friendly
    )


def _mark_confirmed(flow: HaPaneldConfigFlow) -> None:
    """Keep this card's flow from being closed as a waiting card from now on."""
    flow.context["title_placeholders"] = {
        **flow.context.get("title_placeholders", {}),
        CONFIRMED: "yes",
    }


def _set_discovery_title(flow: HaPaneldConfigFlow, display: str) -> None:
    """Record the rendered title for the card, the form and the created entry."""
    # The address lets the other channels' cards, and an Ignore, find this one.
    assert flow._pending_address is not None
    flow.context["title_placeholders"] = {
        **flow.context.get("title_placeholders", {}),
        "name": display,
        "address": flow._pending_address.stored_value,
    }


def _announced_discovery_names(
    flow: HaPaneldConfigFlow,
) -> dict[str, tuple[str | None, str]]:
    """Names claimed by live discovery flows, pruned of any that finished.

    `ConfigFlowContext` is a closed TypedDict, so this cannot ride along in the
    flow context. Keying on flow id makes the store self-pruning.
    """
    store: dict[str, tuple[str | None, str]] = flow.hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(_DATA_DISCOVERY_NAMES, {})
    live = {
        progress["flow_id"]
        for progress in flow.hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    }
    for flow_id in [f for f in store if f not in live and f != flow.flow_id]:
        del store[flow_id]
    return store


def _show_discovery_confirmation(
    flow: HaPaneldConfigFlow, errors: dict[str, str] | None = None
) -> ConfigFlowResult:
    """Show only verified, presentation-safe discovery details."""
    assert flow._pending_address is not None
    assert flow._pending_health is not None
    return flow.async_show_form(
        step_id="confirm_discovery",
        data_schema=vol.Schema({}),
        description_placeholders={
            "address": flow._pending_address.stored_value,
            "panel_name": markdown_literal(
                flow.context.get("title_placeholders", {}).get("name")
                or flow._pending_health.panel_id
            ),
        },
        errors=errors,
    )


def _entry_holds_its_address(
    flow: HaPaneldConfigFlow, entry: ConfigEntry | None = None
) -> bool:
    """Return whether an advertisement must leave this panel's address alone.

    It must while the stored address still answers, or while the panel is
    connected, whose own session then moves the address if it has moved.
    """
    if entry is None:
        if flow.unique_id is None:
            return False
        entry = flow.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, flow.unique_id
        )
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    return coordinator is not None and (
        bool(coordinator.reachable) or coordinator.connected
    )


def _qualified_name(friendly: str, panel_id: str) -> str:
    """Show the panel id only when the friendly name alone is ambiguous."""
    return f"{friendly} ({panel_id})"


def _presentation_safe_name(value: object) -> str | None:
    """Accept an mDNS-advertised name only as bounded, printable, single-line text.

    The TXT record is writable by anything on the LAN, so it is presentation input
    and never an identity. Identity stays with the verified discovery id.
    """
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _FRIENDLY_NAME_MAX_LENGTH:
        return None
    if any(unicodedata.category(character).startswith("C") for character in name):
        return None
    if any(character.isspace() and character != " " for character in name):
        return None
    return name


def markdown_literal(value: str) -> str:
    """Keep interpolated device text literal in HA's Markdown descriptions."""
    return "".join(
        f"&#{ord(character)};" if character in "\\`*_{}[]()<>!&:./@" else character
        for character in value
    )


def closes_when_done(
    step: Callable[[HaPaneldConfigFlow, Any], Awaitable[ConfigFlowResult]],
) -> Callable[[HaPaneldConfigFlow, Any], Awaitable[ConfigFlowResult]]:
    """Mark a card's flow RUNNING while any step runs; end it if asked to close.

    Core turns anything but an abort from a flow removed mid-step into an
    unknown-flow error for the person who pressed it, so `close_offers` never
    aborts a running card; it writes CLOSING and the card ends itself here.
    Runs are counted, not flagged: a step can call another, and opening a
    card's dialog runs its step again while an earlier run still waits.
    Every run that ends after CLOSING was written returns the abort.
    """

    @functools.wraps(step)
    async def run(flow: HaPaneldConfigFlow, user_input: Any = None) -> ConfigFlowResult:
        placeholders = flow.context.get("title_placeholders", {})
        if "address" not in placeholders:
            return await step(flow, user_input)
        flow._steps_running += 1
        if RUNNING not in placeholders:
            flow.context["title_placeholders"] = {**placeholders, RUNNING: "yes"}
        try:
            result = await step(flow, user_input)
        finally:
            flow._steps_running -= 1
            placeholders = dict(flow.context["title_placeholders"])
            closing = placeholders.get(RUNNING) == CLOSING
            if flow._steps_running == 0:
                placeholders.pop(RUNNING, None)
                flow.context["title_placeholders"] = placeholders
        if closing:
            return flow.async_abort(reason="already_configured")
        return result

    return run

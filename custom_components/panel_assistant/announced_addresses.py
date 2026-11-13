"""Where an announced panel is reached: each address its announcements carried.

A dual-stack panel can be unreachable in one address family, IPv6 dropped
between networks for example. Its discovery reads health at each announced
address in turn, including any a later announcement of the same panel hands
over while the first read waits, and offers the panel at the first address
that answers as it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidAddressError,
    InvalidResponseError,
    PanelAddress,
    PanelHealth,
    normalize_address,
)
from .const import DOMAIN

if TYPE_CHECKING:
    from .config_flow import HaPaneldConfigFlow

_LOGGER = logging.getLogger(__name__)

# At most this many addresses are tried for one announced panel.
_ADDRESSES_MAX = 6


def announced_addresses(
    first_host: str, discovery_info: ZeroconfServiceInfo, announced: frozenset[str]
) -> list[PanelAddress] | None:
    """Where to read an announced panel's health, best first; None if malformed.

    The host discovery prefers comes first. The other addresses the
    announcement carried that name only this device follow, IPv4 before IPv6,
    so a panel whose first address cannot be reached (IPv6 dropped between
    networks, say) is still offered at one that answers.
    """
    # A vendor's SRV port is its own service, not the panel HTTP listener.
    port = (
        f":{discovery_info.port}"
        if discovery_info.type == "_ha-paneld._tcp.local."
        else ""
    )
    try:
        addresses = [normalize_address(f"{first_host}{port}")]
    except InvalidAddressError:
        return None
    others = sorted(
        (
            a
            for a in (discovery_info.ip_address, *discovery_info.ip_addresses)
            if str(a) in announced
        ),
        key=lambda a: a.version,
    )
    for other in others:
        host = f"[{other}]" if other.version == 6 else str(other)
        try:
            address = normalize_address(f"{host}{port}")
        except InvalidAddressError:
            continue
        if address not in addresses:
            addresses.append(address)
    return addresses[:_ADDRESSES_MAX]


def hand_over_addresses(probing: HaPaneldConfigFlow, heard: HaPaneldConfigFlow) -> None:
    """Give a flow still reading its panel's health the addresses heard since.

    The same panel's next announcement, often its other address family a
    moment later, is turned away while the first flow holds the panel's id.
    Its addresses are not: the first flow tries them before it gives up.
    """
    addresses = probing._discovery_addresses
    if addresses is None:
        return
    for address, hosts in (heard._discovery_addresses or {}).items():
        if address not in addresses and len(addresses) < _ADDRESSES_MAX:
            addresses[address] = hosts


async def async_read_announced_health(
    flow: HaPaneldConfigFlow, discovery_id: str
) -> tuple[PanelAddress, PanelHealth] | ConfigFlowResult:
    """The first announced address that answers as this panel, and its health.

    Each address is tried in turn, including any a later announcement of the
    same panel hands over meanwhile. A new panel heard but reachable at none of
    them is a refusal of its own, logged with every address tried. One that
    answered there as something else, or one already added or ignored, whose
    announcements arrive all day, stays a quiet invalid discovery.
    """
    addresses = flow._discovery_addresses
    assert addresses is not None
    session = async_get_clientsession(flow.hass)
    unreachable: list[str] = []
    answered_otherwise: list[str] = []
    flow._discovery_probing = True
    try:
        index = 0
        while index < len(addresses):
            # Keys in the order heard; a handover may add some meanwhile.
            address = list(addresses)[index]
            index += 1
            try:
                health = await HaPaneldClient(session, address).async_get_health()
            except CannotConnectError as err:
                unreachable.append(f"{_authority(address)} ({_error_text(err)})")
                continue
            except InvalidResponseError as err:
                answered_otherwise.append(f"{_authority(address)} ({_error_text(err)})")
                continue
            except Exception:
                _LOGGER.exception(
                    "Unexpected exception while verifying discovered ha-paneld"
                )
                return flow.async_abort(reason="unknown")
            if health.discovery_id == discovery_id:
                return address, health
            answered_otherwise.append(f"{_authority(address)} (another panel)")
    finally:
        flow._discovery_probing = False
    name = flow._pending_friendly_name or discovery_id[:12]
    flow._refusal_cause = "; ".join(
        [
            f"announced as {name!r}",
            *(f"no connection at {u}" for u in unreachable),
            *(f"not this panel at {o}" for o in answered_otherwise),
        ]
    )
    known = flow.hass.config_entries.async_entry_for_domain_unique_id(
        DOMAIN, discovery_id
    )
    return flow.async_abort(
        reason="invalid_discovery"
        if answered_otherwise or known is not None
        else "cannot_connect"
    )


def _authority(address: PanelAddress) -> str:
    host = f"[{address.host}]" if ":" in address.host else address.host
    return f"{host}:{address.port}"


def _error_text(err: BaseException) -> str:
    """The network error itself, briefly: a refusal and a timeout read apart."""
    cause = err.__cause__ or err
    text = str(cause)
    return f"{type(cause).__name__}: {text}" if text else type(cause).__name__

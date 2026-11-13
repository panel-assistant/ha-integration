"""Panels that opened a session but have no entry to accept it.

Such a panel can reach Home Assistant. When adding it at the same address then
fails on its app port, the firewall stops Home Assistant reaching the panel,
and the add form says so instead of blaming the address or the power.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .transport_protocol import ERR_ENTRY_REMOVED

_LOGGER = logging.getLogger(__name__)

DATA_REFUSED_HELLOS: Final = "refused_hellos"
# A refused panel retries about every 30 s, so a sighting this old means the
# panel has stopped calling in, and an add attempt is not explained by it.
REFUSED_HELLO_TTL: Final = timedelta(minutes=30)
MAX_REFUSED_HELLOS: Final = 16


@dataclass(frozen=True, slots=True)
class RefusedHello:
    """One panel's latest refused hello."""

    did: str
    code: str
    addresses: tuple[str, ...]
    peer: str | None
    at: datetime


def _ip(value: str | None) -> IPv4Address | IPv6Address | None:
    if not value:
        return None
    try:
        parsed = ip_address(value.strip("[]"))
    except ValueError:
        return None
    return getattr(parsed, "ipv4_mapped", None) or parsed


def _sightings(hass: HomeAssistant) -> dict[str, RefusedHello]:
    sightings: dict[str, RefusedHello] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_REFUSED_HELLOS, {}
    )
    return sightings


@callback
def async_record_refused_hello(
    hass: HomeAssistant,
    did: str,
    code: str,
    addresses: tuple[str, ...],
    peer: str | None,
) -> None:
    """Remember a refused hello, and log it the first time it is refused so."""
    sightings = _sightings(hass)
    now = dt_util.utcnow()
    previous = sightings.get(did)
    # A local proxy's address says nothing about where the panel is.
    peer = (
        str(address)
        if (address := _ip(peer)) is not None and not address.is_loopback
        else None
    )
    sightings[did] = RefusedHello(did, code, addresses, peer, now)
    for stale in sorted(sightings, key=lambda panel: sightings[panel].at)[
        :-MAX_REFUSED_HELLOS
    ]:
        del sightings[stale]
    if (
        previous is not None
        and now - previous.at <= REFUSED_HELLO_TTL
        and (previous.code, previous.addresses, previous.peer)
        == (code, addresses, peer)
    ):
        return
    # A removed panel keeps calling until it is reset; that was the owner's
    # choice, so it is noted once a window, not raised as a warning.
    removed = code == ERR_ENTRY_REMOVED
    _LOGGER.log(
        logging.INFO if removed else logging.WARNING,
        "Panel Assistant refused a panel session: error=%s panel=%s "
        "addresses=%s connected_from=%s; %s",
        code,
        did,
        ",".join(addresses) or "none reported",
        peer or "unknown",
        "its entry was removed" if removed else "add the panel to accept it",
    )


@callback
def async_forget_refused_hello(hass: HomeAssistant, did: str) -> None:
    """Forget a panel whose session is now accepted."""
    hass.data.get(DOMAIN, {}).get(DATA_REFUSED_HELLOS, {}).pop(did, None)


@callback
def async_refused_hello_at(hass: HomeAssistant, *hosts: str) -> RefusedHello | None:
    """Return a fresh refused hello from a panel at one of these addresses."""
    wanted = {address for host in hosts if (address := _ip(host)) is not None}
    if not wanted:
        return None
    now = dt_util.utcnow()
    for sighting in sorted(
        _sightings(hass).values(), key=lambda seen: seen.at, reverse=True
    ):
        if now - sighting.at > REFUSED_HELLO_TTL:
            continue
        # Only what the panel reports: behind NAT its peer is the router.
        seen = {_ip(value) for value in sighting.addresses}
        if wanted & seen:
            return sighting
    return None

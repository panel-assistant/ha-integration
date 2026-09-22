"""Where a connected panel can be reached, and the report when it cannot be.

The stored address is where Home Assistant polls the panel. A panel's own
session knows the address it is talking from, which is the one place the
stored address can be repaired from when the panel has moved: no discovery has
to reach Home Assistant, and no new message has to be invented. What may be
written is a separate question from whether the panel is available, and is
answered here: a candidate is only ever adopted after a health read at that
address has proved the same identity. A refused candidate leaves the stored
address alone and says so as a Repairs issue.
"""

from __future__ import annotations

import ipaddress
from typing import Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .client import InvalidAddressError, PanelAddress, normalize_address
from .const import DOMAIN

# The panel is connected, but the stored address does not answer and the
# session offers no other address to try (it came from the stored one, or
# from an address no panel can be polled at).
ISSUE_PANEL_ADDRESS_UNREACHABLE: Final = "panel_address_unreachable"
# The panel is connected from another address, but a health read there did
# not answer as this panel, so the stored address was left alone.
ISSUE_PANEL_ADDRESS_UNVERIFIED: Final = "panel_address_unverified"
ADDRESS_ISSUES: Final = frozenset(
    {ISSUE_PANEL_ADDRESS_UNREACHABLE, ISSUE_PANEL_ADDRESS_UNVERIFIED}
)


def address_issue_id(entry_id: str) -> str:
    """Return the one Repairs issue ID an entry's address report uses."""
    return f"panel_address_{entry_id}"


def session_candidate(remote: str | None, stored: PanelAddress) -> PanelAddress | None:
    """Return the address a session's peer suggests polling, if it is one.

    The port is the stored one: the session says where the panel is, not which
    port its web server listens on. A peer that is not a usable literal address
    (a proxy hostname, a scoped link-local address) or that is a public one
    yields nothing, so nothing is ever adopted outside the networks a panel
    lives on; a panel reached over a tailnet or carrier-grade NAT is on one.
    """
    if remote is None:
        return None
    try:
        parsed = ipaddress.ip_address(remote)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped
        elif parsed.scope_id is not None:
            # A link-local address is only meaningful on the interface it was
            # seen on, which a URL cannot name.
            return None
    if parsed.is_global or parsed.is_multicast or parsed.is_unspecified:
        return None
    host = f"[{parsed}]" if isinstance(parsed, ipaddress.IPv6Address) else str(parsed)
    try:
        candidate = normalize_address(f"{host}:{stored.port}")
    except InvalidAddressError:
        return None
    if candidate == stored:
        return None
    return candidate


@callback
def async_raise_address_issue(
    hass: HomeAssistant,
    entry_id: str,
    issue: str,
    *,
    panel: str,
    address: str,
    session_address: str,
) -> None:
    """Report that the stored address does not answer while the panel is connected."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        address_issue_id(entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue,
        translation_placeholders={
            "panel": panel,
            "address": address,
            "session_address": session_address,
        },
    )


@callback
def async_delete_address_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the report once the stored address answers again."""
    ir.async_delete_issue(hass, DOMAIN, address_issue_id(entry_id))

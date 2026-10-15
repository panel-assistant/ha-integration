"""Which Home Assistant account may hold a panel's session.

A panel's identity is public on the LAN, so ``hello`` never binds a panel to the
account that sends it. Only an administrator binds one, by confirming the
Repairs issue that an unconfirmed ``hello`` raises, or the add-panel flow that
offers the account a panel asked as before it had an entry.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from homeassistant.auth.models import User
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import CONF_TRANSPORT_USER_ID, DOMAIN
from .transport_protocol import (
    ERR_PANEL_USER_MISMATCH,
    REASON_BINDING_CHANGED,
)
from .transport_sessions import async_get_sessions

# The account a panel asked to connect as before it had a config entry at all,
# kept only so the add-panel flow can offer the same confirmation the Repairs
# issue does, while the administrator adding the panel is still present.
DATA_BINDING_REQUESTS: Final = "binding_requests"

# The Repairs issues an unconfirmed hello raises, one per entry, chosen by what
# the administrator is actually being asked. A panel with no account yet is
# unfinished onboarding. A panel whose account is already confirmed is someone
# asking to take it over, which must never read as routine. The wire code the
# panel renders stays one code for both.
ISSUE_PANEL_AWAITING_CONFIRMATION: Final = "panel_awaiting_confirmation"
ISSUE_PANEL_USER_MISMATCH: Final = ERR_PANEL_USER_MISMATCH
ISSUE_DATA_ENTRY_ID: Final = "entry_id"
ISSUE_DATA_USER_ID: Final = "user_id"


# ---------------------------------------------------------------------------
# Binding. A panel's user is bound only on an administrator's confirmation.

# A panel that asks before it has a config entry is remembered for this long, so
# that the add-panel flow can name the account it asked as. Long enough to cover
# setting a panel up and then adding it, short enough that an account which
# asked in some earlier session is not offered as though it were asking now.
BINDING_REQUEST_TTL: Final = timedelta(minutes=30)
MAX_BINDING_REQUESTS: Final = 16
BINDING_ISSUES: Final = (ISSUE_PANEL_AWAITING_CONFIRMATION, ISSUE_PANEL_USER_MISMATCH)


def binding_issue_id(issue: str, entry_id: str) -> str:
    """Return one of the two Repairs issue IDs that ask about an entry's user."""
    return f"{issue}_{entry_id}"


def _binding_issue_for(entry: ConfigEntry) -> str:
    """Return the issue describing what this entry's administrator is asked."""
    if entry.data.get(CONF_TRANSPORT_USER_ID) is None:
        return ISSUE_PANEL_AWAITING_CONFIRMATION
    return ISSUE_PANEL_USER_MISMATCH


@callback
def _may_ask_to_bind(hass: HomeAssistant, connection: ActiveConnection) -> bool:
    """Return whether this connection may ask to be bound at all.

    A removed user's socket survives its removal, but its refresh token does
    not. Such a connection may not even ask.
    """
    return (
        connection.refresh_token_id is not None
        and hass.auth.async_get_refresh_token(connection.refresh_token_id) is not None
    )


@callback
def async_record_binding_request(hass: HomeAssistant, did: str, user_id: str) -> None:
    """Remember the account a panel asked to connect as before binding."""
    requests: dict[str, tuple[str, datetime]] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(DATA_BINDING_REQUESTS, {})
    requests[did] = (user_id, dt_util.utcnow())
    for stale in sorted(requests, key=lambda panel: requests[panel][1])[
        :-MAX_BINDING_REQUESTS
    ]:
        del requests[stale]


@callback
def async_binding_request(hass: HomeAssistant, did: str | None) -> str | None:
    """Return the account a panel asked to connect as, while that is still fresh."""
    if did is None:
        return None
    requests: dict[str, tuple[str, datetime]] = hass.data.get(DOMAIN, {}).get(
        DATA_BINDING_REQUESTS, {}
    )
    record = requests.get(did)
    if record is None:
        return None
    user_id, asked_at = record
    if dt_util.utcnow() - asked_at > BINDING_REQUEST_TTL:
        del requests[did]
        return None
    return user_id


async def async_confirmable_user(
    hass: HomeAssistant, user_id: str | None
) -> User | None:
    """The account an administrator may still bind: present, active, not Core's."""
    user = await hass.auth.async_get_user(user_id) if user_id else None
    if user is None or not user.is_active or user.system_generated:
        return None
    return user


@callback
def async_discard_binding_request(hass: HomeAssistant, did: str | None) -> None:
    """Forget a request the add-panel flow has now put to an administrator."""
    if did is None:
        return
    hass.data.get(DOMAIN, {}).get(DATA_BINDING_REQUESTS, {}).pop(did, None)


@callback
def async_raise_binding_issue(
    hass: HomeAssistant, entry: ConfigEntry, user_id: str
) -> None:
    """Ask an administrator whether this user may connect as the entry's panel."""
    from .foreign_ha import async_clear_foreign_ha_issue

    async_clear_foreign_ha_issue(hass, entry.entry_id)
    asked = _binding_issue_for(entry)
    issue_id = binding_issue_id(asked, entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is not None and (issue.data or {}).get(ISSUE_DATA_USER_ID) != user_id:
        # Replacing an issue keeps its dismissal, and any signed-in user may
        # dismiss one. A request from someone new must be seen again.
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    for other in BINDING_ISSUES:
        # An entry that has gained or lost an account is being asked a different
        # question, so the question it was asked before goes rather than lingering
        # beside this one. This also retires an issue raised before the split.
        if other != asked:
            ir.async_delete_issue(hass, DOMAIN, binding_issue_id(other, entry.entry_id))
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data={ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_USER_ID: user_id},
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=asked,
        translation_placeholders={"panel": entry.title},
    )


@callback
def async_delete_binding_issue(
    hass: HomeAssistant, entry_id: str, user_id: str | None = None
) -> None:
    """Delete an entry's binding issues, or only one that proposes this user."""
    registry = ir.async_get(hass)
    for asked in BINDING_ISSUES:
        issue_id = binding_issue_id(asked, entry_id)
        issue = registry.async_get_issue(DOMAIN, issue_id)
        if issue is None:
            continue
        if user_id is None or (issue.data or {}).get(ISSUE_DATA_USER_ID) == user_id:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_bind_user(hass: HomeAssistant, entry: ConfigEntry, user_id: str) -> None:
    """Bind an entry to a user. Only an administrator's confirmation calls this."""
    sessions = async_get_sessions(hass)
    session = sessions.get(entry.entry_id)
    if session is not None and session.user_id != user_id:
        sessions.close(session, REASON_BINDING_CHANGED)
    if entry.data.get(CONF_TRANSPORT_USER_ID) != user_id:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TRANSPORT_USER_ID: user_id}
        )
    async_delete_binding_issue(hass, entry.entry_id, user_id)

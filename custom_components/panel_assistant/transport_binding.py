"""Which Home Assistant account may hold a panel's session.

A new panel is bound to the first live account it signs in as, so neither
install route asks the owner anything (maintainer, 2026-10-15: home networks
are trusted, and the question only slowed the first install). Another account
asking for a panel that is already bound goes to an administrator, through the
Repairs issue that ``hello`` raises.
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
from .device import panel_display_name
from .transport_protocol import (
    ERR_PANEL_USER_MISMATCH,
    REASON_BINDING_CHANGED,
)
from .transport_sessions import async_get_sessions

# The account a panel asked to connect as before it had a config entry at all,
# kept so that adding the panel binds that account at once, rather than leaving
# the panel to wait for its next retry.
DATA_BINDING_REQUESTS: Final = "binding_requests"

# The Repairs issue raised when another account asks for a bound panel: someone
# asking to take it over, which must never read as routine.
ISSUE_PANEL_USER_MISMATCH: Final = ERR_PANEL_USER_MISMATCH
ISSUE_DATA_ENTRY_ID: Final = "entry_id"
ISSUE_DATA_USER_ID: Final = "user_id"


# ---------------------------------------------------------------------------
# Binding. A new panel binds its own account; a change of account is confirmed.

# A panel that asks before it has a config entry is remembered for this long, so
# that adding it binds the account it asked as. Long enough to cover
# setting a panel up and then adding it, short enough that an account which
# asked in some earlier session is not bound as though it were asking now.
BINDING_REQUEST_TTL: Final = timedelta(minutes=30)
MAX_BINDING_REQUESTS: Final = 16


def binding_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID that asks about an entry's user."""
    return f"{ISSUE_PANEL_USER_MISMATCH}_{entry_id}"


@callback
def _may_ask_to_bind(hass: HomeAssistant, connection: ActiveConnection) -> bool:
    """Return whether this connection's account may be bound at all.

    A removed user's socket survives its removal, but its refresh token does
    not. Such a connection, and Home Assistant's own accounts, may not even ask.
    """
    return (
        not connection.user.system_generated
        and connection.refresh_token_id is not None
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
    issue_id = binding_issue_id(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is not None and (issue.data or {}).get(ISSUE_DATA_USER_ID) != user_id:
        # Replacing an issue keeps its dismissal, and any signed-in user may
        # dismiss one. A request from someone new must be seen again.
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        data={ISSUE_DATA_ENTRY_ID: entry.entry_id, ISSUE_DATA_USER_ID: user_id},
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PANEL_USER_MISMATCH,
        translation_placeholders={"panel": panel_display_name(hass, entry)},
    )


@callback
def async_delete_binding_issue(
    hass: HomeAssistant, entry_id: str, user_id: str | None = None
) -> None:
    """Delete an entry's binding issue, or only one that proposes this user."""
    issue_id = binding_issue_id(entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is None:
        return
    if user_id is None or (issue.data or {}).get(ISSUE_DATA_USER_ID) == user_id:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def async_bind_user(hass: HomeAssistant, entry: ConfigEntry, user_id: str) -> None:
    """Bind an entry to a user, ending another user's session on it."""
    sessions = async_get_sessions(hass)
    session = sessions.get(entry.entry_id)
    if session is not None and session.user_id != user_id:
        sessions.close(session, REASON_BINDING_CHANGED)
    if entry.data.get(CONF_TRANSPORT_USER_ID) != user_id:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TRANSPORT_USER_ID: user_id}
        )
    async_delete_binding_issue(hass, entry.entry_id, user_id)


@callback
def async_unbind_user(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget an entry's account, so the panel's next hello binds its own."""
    if CONF_TRANSPORT_USER_ID in entry.data:
        data = dict(entry.data)
        del data[CONF_TRANSPORT_USER_ID]
        hass.config_entries.async_update_entry(entry, data=data)

"""The live panel sessions and the commands sent on them.

One accepted ``hello`` (see ``transport.py``) opens a session; the table here
keeps at most one per config entry, announces each change, and ends a session
with the reason the panel is told. A command or management request is sent on
a session once and waited for until its outcome (``async_send_command``,
``async_manage``); a session that ends fails every command still waiting.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .transport_protocol import (
    CAPABILITY_COMMANDS,
    CAPABILITY_MANAGEMENT,
    COMMAND_DEADLINE_MS,
    COMMAND_TIMEOUT,
    ERR_APPROVAL_PENDING,
    ERR_NOT_COMMANDABLE,
    ERR_PANEL_UNAVAILABLE,
    MANAGE_SNAPSHOT,
    MANAGE_TIMEOUT,
    MAX_COMMAND_DEADLINE_MS,
    MAX_LATE_COMMANDS,
    MAX_RECENT_OUTCOMES,
    OUTCOME_APPLIED,
    OUTCOMES_WITH_CODE,
    REASON_SUPERSEDED,
    REASON_UPDATE_POLICY_CHANGED,
)
from .update_policy import prereleases_allowed
from .voice_stream import async_get_voice_stream

# Logged under the transport's own name, which predates this module.
_LOGGER = logging.getLogger(f"{__package__}.transport")

DATA_TRANSPORT: Final = "transport"


def signal_session_changed(entry_id: str) -> str:
    """Return the dispatcher signal fired when an entry's session changes."""
    return f"{DOMAIN}_transport_session_{entry_id}"


def signal_observations(entry_id: str) -> str:
    """Return the signal fired with the channels a report changed."""
    return f"{DOMAIN}_transport_observations_{entry_id}"


def signal_event(entry_id: str) -> str:
    """Return the signal fired with a channel and event type, once per event."""
    return f"{DOMAIN}_transport_event_{entry_id}"


def signal_native_removed(entry_id: str) -> str:
    """Return the signal fired with the unique IDs of removed native entities."""
    return f"{DOMAIN}_transport_native_removed_{entry_id}"


# ---------------------------------------------------------------------------
# Sessions.


@dataclass(slots=True)
class Observation:
    """The validated latest observation of one channel."""

    state: str
    value: Any
    attributes: dict[str, Any]
    refresh: bool
    received_at: datetime
    # Why an ``unknown`` observation has no value; None for every other state.
    reason: str | None = None


@dataclass(slots=True)
class PanelSession:
    """One accepted ``hello`` subscription on one connection."""

    entry_id: str
    did: str
    token: str
    connection: ActiveConnection
    subscription_id: int
    user_id: str
    protocol: int
    app_version: str
    app_version_code: int
    contract_digest: str
    capabilities: frozenset[str]
    descriptors: dict[str, dict[str, Any]]
    opened_at: datetime
    # The address the panel's connection came from, as Home Assistant saw it:
    # the peer of the WebSocket, or the forwarded address behind a trusted
    # proxy. It is where the panel can be reached only once a health read
    # there has proved the same identity, which the coordinator does before
    # adopting it. Never shown: an address identifies a network.
    remote: str | None = field(default=None, repr=False)
    addresses: tuple[str, ...] = field(default=(), repr=False)
    # Described channels the vendored catalogue does not know. They are
    # accepted and their reports stored, but nothing renders them.
    unknown_channels: frozenset[str] = frozenset()
    observations: dict[str, Observation] = field(default_factory=dict)
    full_sync_begun: bool = False
    full_sync_complete: bool = False
    rejected_observations: int = 0
    events_received: int = 0
    # Event IDs increase within a session, so one number remembers every event
    # already counted, however long the session lasts.
    last_event_id: int = -1
    # The latest rejection code of each described channel, cleared when a later
    # observation of that channel is accepted.
    rejections: dict[str, str] = field(default_factory=dict)
    closed_at: datetime | None = None
    # The channel granted for this live session. A changed option revokes it.
    prerelease_panel_builds: bool | None = None
    # The sidebar subscriptions relayed on this session, by the panel's
    # subscription ID (see ``ui_relay.py``). Each ends with the session.
    ui_relays: dict[str, list[Any]] = field(default_factory=dict)
    # The panel's Sendspin client id, when ``voice_stream_session`` was granted.
    voice_stream_client_id: str | None = None
    # Commands sent and still waited for, by command ID.
    pending: dict[str, PendingCommand] = field(default_factory=dict)
    # Commands whose wait ended without a final outcome, oldest first.
    late: dict[str, PendingCommand] = field(default_factory=dict)
    recent_outcomes: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_RECENT_OUTCOMES)
    )
    command_counts: dict[str, int] = field(default_factory=dict)
    # The panel's own wake words and their pipelines, as it last reported them
    # (``voice.VoiceConfiguration``). A projection of the panel's settings,
    # never a second store: it ends with the session.
    voice: Any = None

    def count(self, name: str) -> None:
        """Count one command fact for diagnostics."""
        self.command_counts[name] = self.command_counts.get(name, 0) + 1


@dataclass(slots=True)
class CommandOutcome:
    """The final outcome a panel reported for one command."""

    outcome: str
    code: str | None
    result: Any = None
    # The answering route's own status and headers, for a relayed request.
    status: int | None = None
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class PendingCommand:
    """One command sent on a session and not yet answered with a final outcome.

    The future resolves with the final outcome, or with None when the session
    ends first.
    """

    channel: str
    future: asyncio.Future[CommandOutcome | None]
    approval_pending: bool = False
    # Run when an interim pending_approval arrives, to wait longer for it.
    on_interim: Callable[[], None] | None = None
    # Run once a command no longer waited for is finally answered, or can no
    # longer be (its session ended, or it was forgotten for a newer one).
    on_settled: Callable[[], None] | None = None

    def settle(self) -> None:
        """Run ``on_settled`` once."""
        settled, self.on_settled = self.on_settled, None
        if settled is not None:
            settled()


@dataclass(frozen=True, slots=True)
class RestartNotice:
    """A bounded panel restart announcement with a monotonic deadline."""

    scope: str
    reason: str
    expected_back_ms: int
    deadline: float


class TransportSessions:
    """Every live panel session, at most one per config entry.

    Each entry's most recent session is also kept after it ends, until a new
    one opens or the entry is removed, so diagnostics can still show what the
    panel last reported. Only a live session makes a panel available.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty session table."""
        self._hass = hass
        self._by_entry: dict[str, PanelSession] = {}
        self._by_token: dict[str, PanelSession] = {}
        self._last_by_entry: dict[str, PanelSession] = {}
        self._restart: dict[str, RestartNotice] = {}
        self._restart_timers: dict[str, asyncio.TimerHandle] = {}

    @callback
    def broadcast_lifecycle(self, lifecycle: dict[str, Any]) -> None:
        """Best-effort notification to every authority; one failure is isolated."""
        for session in tuple(self._by_entry.values()):
            try:
                session.connection.send_message(
                    event_message(
                        session.subscription_id, {"kind": "lifecycle", **lifecycle}
                    )
                )
            except Exception:
                _LOGGER.debug("Could not enqueue Core lifecycle notice", exc_info=True)

    def restart_notice(self, entry_id: str) -> RestartNotice | None:
        """Return only an unexpired notice."""
        notice = self._restart.get(entry_id)
        return (
            notice
            if notice is not None and notice.deadline > self._hass.loop.time()
            else None
        )

    @callback
    def set_restart_notice(
        self, entry_id: str, scope: str, reason: str, remaining_ms: int
    ) -> None:
        """Replace a notice and schedule its state change at expiry."""
        previous = self._restart_timers.pop(entry_id, None)
        if previous is not None:
            previous.cancel()
        notice = RestartNotice(
            scope, reason, remaining_ms, self._hass.loop.time() + remaining_ms / 1000
        )
        self._restart[entry_id] = notice
        self._restart_timers[entry_id] = self._hass.loop.call_later(
            remaining_ms / 1000, self.clear_restart_notice, entry_id
        )
        self._changed(entry_id)

    @callback
    def clear_restart_notice(self, entry_id: str) -> None:
        """Forget a returned or timed-out panel's notice."""
        timer = self._restart_timers.pop(entry_id, None)
        if timer is not None:
            timer.cancel()
        if self._restart.pop(entry_id, None) is not None:
            self._changed(entry_id)

    def get(self, entry_id: str) -> PanelSession | None:
        """Return the entry's live session, if any."""
        return self._by_entry.get(entry_id)

    def latest(self, entry_id: str) -> PanelSession | None:
        """Return the entry's live session, else the last one that ended."""
        return self._by_entry.get(entry_id) or self._last_by_entry.get(entry_id)

    @callback
    def forget_entry(self, entry_id: str) -> None:
        """Drop a removed entry's ended session."""
        self._last_by_entry.pop(entry_id, None)
        self.clear_restart_notice(entry_id)

    def for_request(
        self, token: str, connection: ActiveConnection
    ) -> PanelSession | None:
        """Return the session a request names, only on its own connection."""
        session = self._by_token.get(token)
        if session is None or session.connection is not connection:
            return None
        return session

    @callback
    def open(self, session: PanelSession) -> None:
        """Install a session, superseding any earlier one for its entry.

        The panel held a session throughout a supersede, so the change is
        announced once, with the new session in place: announcing the close
        first would let every entity write an unavailable state for a panel
        that never went away.
        """
        if (previous := self._by_entry.get(session.entry_id)) is not None:
            self.close(previous, REASON_SUPERSEDED, announce=False)
        self.clear_restart_notice(session.entry_id)
        # The kept session holds its closed connection; a live one replaces it.
        self._last_by_entry.pop(session.entry_id, None)
        self._by_entry[session.entry_id] = session
        self._by_token[session.token] = session
        session.connection.subscriptions[session.subscription_id] = (
            self._teardown_callback(session)
        )
        self._changed(session.entry_id)

    @callback
    def close(
        self, session: PanelSession, reason: str, *, announce: bool = True
    ) -> None:
        """End a session from this side and tell the panel why."""
        if not self._forget(session):
            return
        connection = session.connection
        connection.subscriptions.pop(session.subscription_id, None)
        connection.send_message(
            event_message(
                session.subscription_id, {"kind": "session_closed", "reason": reason}
            )
        )
        if announce:
            self._changed(session.entry_id)

    @callback
    def close_entry(self, entry_id: str, reason: str) -> None:
        """End an entry's session, if it has one."""
        if (session := self._by_entry.get(entry_id)) is not None:
            self.close(session, reason)

    @callback
    def close_user(self, user_id: str, reason: str) -> None:
        """End every session signed in as one user."""
        for session in [s for s in self._by_entry.values() if s.user_id == user_id]:
            self.close(session, reason)

    @callback
    def mark_changed(self, session: PanelSession) -> None:
        """Announce a change of a live session's availability."""
        if self._by_entry.get(session.entry_id) is session:
            self._changed(session.entry_id)

    def _teardown_callback(self, session: PanelSession) -> Callable[[], None]:
        @callback
        def _teardown() -> None:
            # Home Assistant runs this when the connection closes, and when the
            # panel unsubscribes. Either way the panel is gone.
            if self._forget(session):
                self._changed(session.entry_id)

        return _teardown

    def _forget(self, session: PanelSession) -> bool:
        if self._by_entry.get(session.entry_id) is not session:
            return False
        del self._by_entry[session.entry_id]
        self._by_token.pop(session.token, None)
        session.closed_at = dt_util.utcnow()
        self._last_by_entry[session.entry_id] = session
        # A command is never sent again on a later session, so every command
        # still waited for fails now.
        for command in session.pending.values():
            if not command.future.done():
                command.future.set_result(None)
        session.pending.clear()
        late = list(session.late.values())
        session.late.clear()
        for command in late:
            command.settle()
        # A relayed subscription cannot outlive the session that carries it.
        relays = [relay for group in session.ui_relays.values() for relay in group]
        session.ui_relays.clear()
        for relay in relays:
            relay.session_ended()
        # The session carried the panel's Sendspin connection; it ends too.
        if session.voice_stream_client_id is not None and (
            voice_stream := async_get_voice_stream(self._hass)
        ):
            voice_stream.detach(session.token)
        return True

    @callback
    def _changed(self, entry_id: str) -> None:
        async_dispatcher_send(self._hass, signal_session_changed(entry_id))


@callback
def async_apply_update_policy(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """End a live session whose update grant is no longer current.

    Every change to an entry calls this, binding writes included, so it acts
    only on a real change. The panel sends hello again and is granted anew.
    """
    sessions = async_get_sessions(hass)
    session = sessions.get(entry.entry_id)
    if (
        session is not None
        and session.prerelease_panel_builds is not None
        and session.prerelease_panel_builds != prereleases_allowed(entry)
    ):
        sessions.close(session, REASON_UPDATE_POLICY_CHANGED)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def async_get_sessions(hass: HomeAssistant) -> TransportSessions:
    """Return the process-wide session table, creating it on first use."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    sessions = domain_data.get(DATA_TRANSPORT)
    if sessions is None:
        sessions = domain_data[DATA_TRANSPORT] = TransportSessions(hass)
    return sessions


def session_available(hass: HomeAssistant, entry_id: str) -> bool:
    """Return whether an entry holds a session with a completed full sync."""
    session = async_get_sessions(hass).get(entry_id)
    return session is not None and session.full_sync_complete


def session_diagnostics(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Return language-neutral session facts without identity or values.

    An ended session is still described, as not connected, until a new one
    opens or the entry is removed.
    """
    sessions = async_get_sessions(hass)
    session = sessions.latest(entry_id)
    if session is None:
        return {"connected": False}
    return {
        "connected": sessions.get(entry_id) is session,
        "closed_at": _iso(session.closed_at),
        "protocol": session.protocol,
        "app_version": session.app_version,
        "app_version_code": session.app_version_code,
        "contract_digest": session.contract_digest,
        "capabilities": sorted(session.capabilities),
        "opened_at": session.opened_at.isoformat(),
        "full_sync_complete": session.full_sync_complete,
        "channels": len(session.descriptors),
        "unknown_channels": sorted(session.unknown_channels),
        "observations": len(session.observations),
        "rejected_observations": session.rejected_observations,
        "events_received": session.events_received,
        # Outcomes and counts only: a command's value may be user data.
        "commands": {
            "counts": dict(sorted(session.command_counts.items())),
            "pending": len(session.pending),
            "recent": list(session.recent_outcomes),
        },
    }


def _command_error(translation_key: str | None) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=translation_key
    )


@callback
def _stop_waiting(session: PanelSession, command_id: str) -> None:
    """Keep a command whose wait ended, so a late outcome is still recorded."""
    command = session.pending.pop(command_id, None)
    if command is None or session.closed_at is not None:
        return
    session.late[command_id] = command
    while len(session.late) > MAX_LATE_COMMANDS:
        session.late.pop(next(iter(session.late))).settle()


async def async_send_command(
    hass: HomeAssistant, session: PanelSession | None, channel: str, value: Any
) -> None:
    """Send one command to a panel and wait for its outcome.

    Only a live, fully synced session with the commands capability carries
    commands. A command is sent once and never
    again: if the session ends first, or no outcome arrives in time, the call
    fails and the panel is not asked twice. Applied and superseded return;
    every other outcome raises its translated error.
    """
    if (
        session is None
        or async_get_sessions(hass).get(session.entry_id) is not session
        or not session.full_sync_complete
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_PANEL_UNAVAILABLE
        )
    if CAPABILITY_COMMANDS not in session.capabilities:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_NOT_COMMANDABLE
        )
    try:
        result = await async_deliver(
            hass,
            session,
            channel,
            {"channel": channel, "value": value},
            COMMAND_TIMEOUT,
        )
    except HomeAssistantError:
        # A reboot that works takes the panel down before it can answer, so a
        # session that ends after the command was sent is the reboot happening.
        if channel == "reboot" and session.closed_at is not None:
            return
        raise
    if result.outcome in OUTCOMES_WITH_CODE:
        raise _command_error(result.code)


def session_manages(session: PanelSession | None) -> bool:
    """Return whether a session carries management, so HTTP is not needed for it."""
    return session is not None and CAPABILITY_MANAGEMENT in session.capabilities


async def async_manage(
    hass: HomeAssistant, session: PanelSession, op: str, **fields: Any
) -> dict[str, Any]:
    """Ask a panel one management request on its session and return its result.

    The same delivery as a command, with the same session token, deadline
    and command ID, so a repeat is answered from the panel's record rather
    than run twice. It needs only a live session that granted management:
    management is not entity state, so no full sync is required. Any outcome
    but applied raises its translated error.
    """
    timeout = MANAGE_TIMEOUT if op == MANAGE_SNAPSHOT else COMMAND_TIMEOUT
    outcome = await async_manage_outcome(hass, session, op, timeout, **fields)
    if outcome.outcome in OUTCOMES_WITH_CODE:
        raise _command_error(outcome.code)
    if outcome.outcome != OUTCOME_APPLIED or not isinstance(
        outcome.result, dict | None
    ):
        raise _command_error(ERR_PANEL_UNAVAILABLE)
    return outcome.result or {}


async def async_manage_outcome(
    hass: HomeAssistant,
    session: PanelSession,
    op: str,
    wait_seconds: float,
    **fields: Any,
) -> CommandOutcome:
    """Ask one management request and return its final outcome, whatever it is.

    For a caller that maps a refusal's code and body itself. A request the
    panel never answered in time, or a session that ended first, still raises
    the translated error ``async_manage`` raises.
    """
    if async_get_sessions(hass).get(session.entry_id) is not session or not (
        session_manages(session)
    ):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_PANEL_UNAVAILABLE
        )
    return await async_deliver(
        hass,
        session,
        f"manage:{op}",
        {"op": op, **fields},
        wait_seconds,
        kind="manage",
    )


async def async_deliver(
    hass: HomeAssistant,
    session: PanelSession,
    label: str,
    fields: dict[str, Any],
    wait_seconds: float,
    kind: str = "command",
    *,
    command_id: str | None = None,
    deadline_ms: int = COMMAND_DEADLINE_MS,
    approval_seconds: float = 0,
    on_settled: Callable[[], None] | None = None,
) -> CommandOutcome:
    """Send one event on a session once and wait for its final outcome.

    With ``approval_seconds``, an interim pending_approval extends the wait to
    that long after it arrived, for a request a person is approving on the
    panel's screen. ``on_settled`` runs if the wait ends first and the command
    is kept: when its final outcome arrives late, or never can.
    """
    if command_id is None:
        command_id = secrets.token_urlsafe(24)
    command = PendingCommand(
        channel=label, future=hass.loop.create_future(), on_settled=on_settled
    )
    session.pending[command_id] = command
    session.count("sent")
    session.connection.send_message(
        event_message(
            session.subscription_id,
            {
                "kind": kind,
                "command_id": command_id,
                "session": session.token,
                **fields,
                # How long the panel may take to start it; the wait may be longer.
                "deadline_ms": min(deadline_ms, MAX_COMMAND_DEADLINE_MS),
            },
        )
    )
    future = command.future
    try:
        async with asyncio.timeout(wait_seconds) as scope:
            if approval_seconds:
                command.on_interim = lambda: scope.reschedule(
                    hass.loop.time() + approval_seconds
                )
            # Shielded, so a cancelled caller leaves the command to be recorded.
            result = await asyncio.shield(future)
    except TimeoutError:
        if not future.done():
            session.count("timed_out")
            raise _command_error(
                ERR_APPROVAL_PENDING
                if command.approval_pending
                else ERR_PANEL_UNAVAILABLE
            ) from None
        result = future.result()
    finally:
        # The wait is over: a late interim answer has no timeout left to extend.
        command.on_interim = None
        if not future.done():
            _stop_waiting(session, command_id)

    if result is None:
        session.count("session_ended")
        raise _command_error(ERR_PANEL_UNAVAILABLE)
    return result

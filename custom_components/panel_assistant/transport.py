"""Native panel transport over Home Assistant's own WebSocket.

A panel signs in to Home Assistant with its own account and calls the
``panel_assistant/*`` commands below. ``hello`` is both the handshake and the
subscription that will carry commands, so the subscription's lifetime is the
session: when the connection closes, Home Assistant unsubscribes it, and that
is the signal that the panel is gone.

Everything a panel sends is untrusted. Each message is validated and bounded
(see ``transport_protocol.py``) before anything is stored. Established-session
handlers perform no I/O. A moved legacy panel must complete bounded health
verification before its hello can grant a session.

This integration owns every panel's entities: the panel reports its state
here, the native entities render it (see ``native.py``), and the panel's
commands are sent on the session, each waited for until its outcome (see
``transport_sessions.py``). A removed entry's panel is told so (see
``removed_panels.py``). This module itself writes no device registry entry.

A panel's identity is public on the LAN, so ``hello`` never binds a panel to the
account that sends it. Only an administrator binds one, by confirming the
Repairs issue that an unconfirmed ``hello`` raises (see
``transport_binding.py``).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Collection, Container
from functools import partial
from typing import Any, Final

import voluptuous as vol
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import ERR_INVALID_FORMAT
from homeassistant.components.websocket_api.decorators import websocket_command
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .client import is_valid_discovery_id
from .const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    INTEGRATION_VERSION,
)
from .const import (
    PROTOCOL_MAX as PROTOCOL_MAX,
)
from .const import (
    PROTOCOL_MIN as PROTOCOL_MIN,
)
from .contract import catalogue_entry, catalogue_entry_for_channel
from .ha_url import DATA_INSTANCE_ID, async_connection_urls
from .lifecycle import DATA_LIFECYCLE
from .native import shown_elsewhere
from .transport_binding import (
    _may_ask_to_bind,
    async_bind_user,
    async_delete_binding_issue,
    async_raise_binding_issue,
    async_record_binding_request,
)
from .transport_protocol import (
    _VALUE_VALIDATORS,
    CAPABILITY_VOICE_STREAM_SESSION,
    COMMAND_RESULT_SCHEMA,
    ERR_ENTRY_REMOVED,
    ERR_INVALID_VALUE,
    ERR_PANEL_IDENTITY_UNAVAILABLE,
    ERR_PANEL_USER_MISMATCH,
    ERR_PROTOCOL_UNSUPPORTED,
    ERR_SESSION_UNKNOWN,
    ERR_UNKNOWN_CHANNEL,
    ERR_UNKNOWN_PANEL,
    HELLO_SCHEMA,
    KNOWN_CAPABILITIES,
    OUTCOME_PENDING_APPROVAL,
    REASON_USER_REMOVED,
    REPORT_EVENT_SCHEMA,
    REPORT_STATE_SCHEMA,
    RESTART_NOTICE_SCHEMA,
    STATE_KNOWN,
    SYNC_FULL_BEGIN,
    SYNC_FULL_END,
    ValueRejected,
    _validate_attributes,
)
from .transport_sessions import (
    CommandOutcome,
    Observation,
    PanelSession,
    PendingCommand,
    async_get_sessions,
    signal_event,
    signal_native_removed,
    signal_observations,
)
from .update_policy import overlaps, policy_for, prereleases_allowed
from .voice_stream import async_get_voice_stream
from .volume_number import async_retire_volume_number

_LOGGER = logging.getLogger(__name__)

# The identities of panels whose entry was removed (see ``guards.py``).
DATA_REMOVED_PANELS: Final = "removed_panels"
ISSUE_MERGED_PANEL_IDENTITY: Final = "merged_panel_identity"


# ---------------------------------------------------------------------------
# The channels a panel cannot serve. A hello that lists a channel as
# unsupported, the panel's explicit statement that it cannot serve it, removes
# that channel's native entity; a channel merely missing from one session is
# only unavailable.


@callback
def async_remove_unsupported_channels(
    hass: HomeAssistant, entry: ConfigEntry, did: str, channels: Collection[str]
) -> None:
    """Remove this panel's native entity of each channel it cannot serve.

    Only this integration's own entry for the channel's native unique ID is
    looked up, under this panel identity and this config entry: an MQTT
    entity, another identity's entity and another entry's entity are never
    found. A channel the catalogue does not know removes nothing.
    """
    registry = er.async_get(hass)
    removed: set[str] = set()
    for channel in channels:
        if (match := catalogue_entry_for_channel(channel)) is None:
            continue
        catalogue, suffix = match
        unique_id = f"{did}_{suffix}"
        entity_id = registry.async_get_entity_id(
            catalogue["platform"], DOMAIN, unique_id
        )
        if entity_id is None:
            continue
        item = registry.async_get(entity_id)
        if item is None or item.config_entry_id != entry.entry_id:
            continue
        if channel in {"reboot", "reload"}:
            continue
        registry.async_remove(entity_id)
        removed.add(unique_id)
    if removed:
        # So that a later hello describing the channel again adds its entity.
        async_dispatcher_send(
            hass, signal_native_removed(entry.entry_id), frozenset(removed)
        )


# ---------------------------------------------------------------------------
# Command handlers. All synchronous, so they run in arrival order.


def _reported_did(entry: ConfigEntry) -> str | None:
    """Return the identity the entry's panel reported in its last health read."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    snapshot = getattr(coordinator, "data", None)
    health = getattr(snapshot, "health", None)
    reported = getattr(health, "discovery_id", None)
    return reported if isinstance(reported, str) else None


def _panel_did(entry: ConfigEntry) -> str | None:
    """Return saved identity; only entries predating it may use reported identity."""
    if entry.unique_id is not None and is_valid_discovery_id(entry.unique_id):
        return entry.unique_id
    return _reported_did(entry)


def _removed_panels(hass: HomeAssistant) -> Container[str]:
    """Return the identities of panels whose entry was removed."""
    removed: Container[str] = hass.data.get(DOMAIN, {}).get(DATA_REMOVED_PANELS, ())
    return removed


def _entry_for_did(hass: HomeAssistant, did: str) -> ConfigEntry | None:
    """Return the one loaded entry for a panel identity, never a guess.

    An entry whose panel has answered health with this identity outranks one
    that only remembers it: a stale duplicate left behind by an earlier
    re-add, now loaded with its dead address rather than held in retry, must
    not make the panel's live entry ambiguous. Two entries whose panels both
    answer with one identity are still refused, as cloned panels always were.
    """
    matches = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if _panel_did(entry) == did
    ]
    if len(matches) > 1:
        answering = [entry for entry in matches if _reported_did(entry) == did]
        if len(answering) == 1:
            return answering[0]
        # Two entries whose panels both answered with this identity are
        # clones, or one is a duplicate whose address has since died while
        # it kept its last snapshot; a hello cannot tell those apart, so
        # neither is guessed. Preferring the one still polling would bind a
        # clone's session to the other clone's entry when its address is down.
        raise SharedPanelIdentityError(matches)
    return matches[0] if matches else None


class SharedPanelIdentityError(Exception):
    """Several loaded panel entries claim the identity in one hello."""

    def __init__(self, entries: list[ConfigEntry]) -> None:
        super().__init__("Several panel entries report one identity")
        self.entries = entries


def merged_identity_issue_id(did: str) -> str:
    """Return the one Repairs issue ID for a shared panel identity."""
    return f"{ISSUE_MERGED_PANEL_IDENTITY}_{did}"


@callback
def async_raise_merged_identity_issue(
    hass: HomeAssistant, did: str, entries: list[ConfigEntry]
) -> None:
    """Tell the user which panel entries report one device identity."""
    panels = ", ".join(sorted(entry.title for entry in entries))
    ir.async_create_issue(
        hass,
        DOMAIN,
        merged_identity_issue_id(did),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_MERGED_PANEL_IDENTITY,
        translation_placeholders={"panels": panels},
    )


@callback
def async_delete_merged_identity_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the shared-identity issue when an entry no longer conflicts."""
    if (did := _panel_did(entry)) is not None:
        ir.async_delete_issue(hass, DOMAIN, merged_identity_issue_id(did))


@callback
@websocket_command(vol.All(HELLO_SCHEMA))
def ws_hello(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept a panel session; its subscription will carry commands."""
    did = msg["did"]
    if did is None:
        connection.send_error(
            msg["id"],
            ERR_PANEL_IDENTITY_UNAVAILABLE,
            "The panel has no identity to open a session with.",
        )
        return

    requested = msg["protocol"]
    high = min(requested["max"], PROTOCOL_MAX)
    if not overlaps(requested["min"], requested["max"]):
        _follow_refused_panel(hass, connection, msg)
        connection.send_error(
            msg["id"],
            ERR_PROTOCOL_UNSUPPORTED,
            "No protocol version in common.",
            # Core sends placeholders only with a key. The panel renders the
            # text from its own catalogue; this names the code, not a string.
            translation_key=ERR_PROTOCOL_UNSUPPORTED,
            translation_domain=DOMAIN,
            translation_placeholders={
                "panel_min": str(requested["min"]),
                "panel_max": str(requested["max"]),
                "integration_min": str(PROTOCOL_MIN),
                "integration_max": str(PROTOCOL_MAX),
            },
        )
        return

    try:
        entry = _entry_for_did(hass, did)
    except SharedPanelIdentityError as err:
        _LOGGER.warning("Several panel entries report one identity; refusing hello")
        async_raise_merged_identity_issue(hass, did, err.entries)
        connection.send_error(
            msg["id"],
            ERR_UNKNOWN_PANEL,
            "Several panel entries report this identity.",
        )
        return
    if entry is None:
        _follow_refused_panel(hass, connection, msg)
        if _may_ask_to_bind(hass, connection):
            # There is no entry to raise a Repairs issue against, so remember the
            # account instead. The administrator who adds this panel is then asked
            # to confirm it while they are still here, rather than afterwards.
            async_record_binding_request(hass, did, connection.user.id)
        # An entry added again that failed setup still owns the panel, which then
        # retries as an unknown panel rather than slowly as a removed one.
        if did in _removed_panels(hass) and not any(
            _panel_did(other) == did
            for other in hass.config_entries.async_entries(DOMAIN)
        ):
            connection.send_error(
                msg["id"], ERR_ENTRY_REMOVED, "The panel's entry was removed."
            )
            return
        connection.send_error(
            msg["id"], ERR_UNKNOWN_PANEL, "No loaded panel entry has this identity."
        )
        return
    from .identity import CONF_IDENTITY_PENDING, is_installation

    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if (
        entry.data.get(CONF_IDENTITY_PENDING) is not None
        or getattr(coordinator, "identity_mismatch", False)
        or (not is_installation(entry) and high >= 3)
    ):
        connection.send_error(
            msg["id"],
            ERR_UNKNOWN_PANEL,
            "The panel identity is not confirmed at this endpoint.",
        )
        return
    user_id = connection.user.id
    bound_id = entry.data.get(CONF_TRANSPORT_USER_ID)
    if bound_id != user_id:
        may_ask = _may_ask_to_bind(hass, connection)
        if bound_id is None and may_ask:
            # A new panel is bound to the account it signs in as, so an install
            # never stops to ask. Only another account asking for a bound panel
            # goes to an administrator.
            async_bind_user(hass, entry, user_id)
        else:
            if may_ask:
                async_raise_binding_issue(hass, entry, user_id)
            connection.send_error(
                msg["id"],
                ERR_PANEL_USER_MISMATCH,
                "An administrator has not confirmed this user for this panel.",
            )
            return
    _accept_hello(hass, connection, msg, entry, high)


@callback
def _follow_refused_panel(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Tell a panel's poll where the panel is, though its hello was refused.

    An app too old for a session is served over HTTP, so a panel whose address
    changed is reachable only through what its hello says. A panel no entry
    knows may be one reinstalled under a new identity, so every entry that
    can link a reinstall is told. Only the account bound to an entry is heard,
    and the poll still proves the identity, or the link, there.
    """
    from .identity import CONF_LEGACY_DID

    try:
        entry = _entry_for_did(hass, msg["did"])
    except SharedPanelIdentityError:
        return
    entries = (
        [entry]
        if entry is not None
        else [
            other
            for other in hass.config_entries.async_loaded_entries(DOMAIN)
            if CONF_LEGACY_DID in other.data
        ]
    )
    for candidate in entries:
        if candidate.data.get(CONF_TRANSPORT_USER_ID) != connection.user.id:
            continue
        coordinator = getattr(
            getattr(candidate, "runtime_data", None), "coordinator", None
        )
        if coordinator is not None:
            coordinator.async_follow_refused_hello(
                msg["did"],
                connection.remote if isinstance(connection.remote, str) else None,
                tuple(msg.get("addresses", ())),
            )


@callback
def _accept_hello(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
    entry: ConfigEntry,
    high: int,
) -> None:
    """Grant a session only after synchronous admission or completed address proof."""
    did = msg["did"]
    user_id = connection.user.id
    async_delete_merged_identity_issue(hass, entry)
    async_delete_binding_issue(hass, entry.entry_id, user_id)

    # Every capability this integration implements that the panel offered.
    offered = frozenset(msg["capabilities"])
    capabilities = KNOWN_CAPABILITIES.intersection(offered)
    voice_stream = async_get_voice_stream(hass)
    stream_client_id = (msg.get("voice_stream_session") or {}).get("client_id")
    if stream_client_id is None or voice_stream is None:
        capabilities -= {CAPABILITY_VOICE_STREAM_SESSION}
    descriptors = {item["channel"]: item for item in msg["channels"]}
    unknown = frozenset(
        channel
        for channel, descriptor in descriptors.items()
        if catalogue_entry(descriptor) is None
    )
    # A channel the hello also describes is served, whatever it claims.
    unsupported = frozenset(msg["unsupported"]).difference(descriptors)
    # The entry was found by this very identity, so the entities removed are
    # this panel's own.
    retired_entities = [("image", "camera_snapshot")]
    if (
        "camera_enabled" in descriptors
        and "camera_enabled" not in unknown
        and descriptors["camera_enabled"]["platform"] == "camera"
    ) or "camera_enabled" in unsupported:
        retired_entities.append(("switch", "camera_enabled"))
    registry = er.async_get(hass)
    for platform, suffix in retired_entities:
        legacy_id = registry.async_get_entity_id(platform, DOMAIN, f"{did}_{suffix}")
        if (
            legacy_id is not None
            and (legacy := registry.async_get(legacy_id)) is not None
            and legacy.config_entry_id == entry.entry_id
        ):
            registry.async_remove(legacy_id)
            async_dispatcher_send(
                hass,
                signal_native_removed(entry.entry_id),
                frozenset({legacy.unique_id}),
            )
    if shown_elsewhere("volume", descriptors, unknown):
        async_retire_volume_number(hass, entry, did)
    async_remove_unsupported_channels(hass, entry, did, unsupported)
    session = PanelSession(
        entry_id=entry.entry_id,
        did=did,
        token=secrets.token_urlsafe(24),
        connection=connection,
        subscription_id=msg["id"],
        user_id=user_id,
        protocol=high,
        app_version=msg["app"]["version"],
        app_version_code=msg["app"]["version_code"],
        contract_digest=msg["contract_digest"],
        capabilities=capabilities,
        descriptors=descriptors,
        opened_at=dt_util.utcnow(),
        remote=connection.remote if isinstance(connection.remote, str) else None,
        addresses=tuple(msg.get("addresses", ())),
        unknown_channels=unknown,
        prerelease_panel_builds=prereleases_allowed(entry),
    )
    result: dict[str, Any] = {
        "protocol": high,
        "session": session.token,
        # Optional on the wire: the panel finds its own device, (DOMAIN, entry_id),
        # from this and probes the entity registry only when it is absent.
        "entry_id": entry.entry_id,
        "capabilities": sorted(capabilities),
        "integration": {"version": INTEGRATION_VERSION},
        "lifecycle": hass.data[DOMAIN][DATA_LIFECYCLE].snapshot(),
        "update_policy": policy_for(entry),
        "connection": {
            "instance_id": hass.data[DOMAIN][DATA_INSTANCE_ID],
            "user_id": user_id,
            "urls": async_connection_urls(hass),
        },
        # Unknown descriptors are accepted too, but render nothing.
        "channels": {"accepted": len(descriptors), "unknown": sorted(unknown)},
    }
    if CAPABILITY_VOICE_STREAM_SESSION in capabilities:
        assert voice_stream is not None and stream_client_id is not None
        session.voice_stream_client_id = stream_client_id
        result["voice_stream_session"] = voice_stream.grant(
            stream_client_id, entry.entry_id
        )
    async_get_sessions(hass).open(session)
    if session.voice_stream_client_id is not None:
        assert voice_stream is not None
        voice_stream.attach(
            session.token,
            session.voice_stream_client_id,
            partial(_send_event, connection, msg["id"]),
        )
    connection.send_result(msg["id"], result)


def _send_event(
    connection: ActiveConnection, subscription_id: int, event: dict[str, Any]
) -> None:
    connection.send_message(event_message(subscription_id, event))


@callback
@websocket_command(vol.All(REPORT_STATE_SCHEMA))
def ws_report_state(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate and store a batch of observations, then acknowledge it."""
    sessions = async_get_sessions(hass)
    session = sessions.for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return

    sync = msg["sync"]
    # A retried full_end after a completed sync is harmless and accepted.
    if (
        sync == SYNC_FULL_END
        and not session.full_sync_begun
        and not session.full_sync_complete
    ):
        connection.send_error(
            msg["id"],
            ERR_INVALID_FORMAT,
            "A full sync must begin before it ends.",
        )
        return

    rejected: list[dict[str, str]] = []
    accepted: set[str] = set()
    now = dt_util.utcnow()
    for observation in msg["observations"]:
        channel = observation["channel"]
        descriptor = session.descriptors.get(channel)
        if descriptor is None or descriptor["platform"] not in _VALUE_VALIDATORS:
            rejected.append({"channel": channel, "code": ERR_UNKNOWN_CHANNEL})
            continue
        try:
            if observation["state"] == STATE_KNOWN:
                value = _VALUE_VALIDATORS[descriptor["platform"]](
                    observation["value"], descriptor
                )
                attributes = _validate_attributes(observation.get("attributes"))
            else:
                value, attributes = None, {}
        except ValueRejected as err:
            rejected.append({"channel": channel, "code": str(err)})
            # Only described channels are remembered: they are bounded, and
            # undescribed names are whatever the panel chose to send.
            session.rejections[channel] = str(err)
            continue
        session.rejections.pop(channel, None)
        accepted.add(channel)
        session.observations[channel] = Observation(
            state=observation["state"],
            value=value,
            attributes=attributes,
            refresh=observation["refresh"],
            received_at=now,
            reason=observation.get("reason"),
        )
    session.rejected_observations += len(rejected)
    if accepted:
        async_dispatcher_send(
            hass, signal_observations(session.entry_id), frozenset(accepted)
        )

    if sync == SYNC_FULL_BEGIN:
        session.full_sync_begun = True
    elif sync == SYNC_FULL_END:
        session.full_sync_begun = False
        if not session.full_sync_complete:
            session.full_sync_complete = True
            sessions.mark_changed(session)
    connection.send_result(msg["id"], {"rejected": rejected})


@callback
@websocket_command(vol.All(RESTART_NOTICE_SCHEMA))
def ws_restart_notice(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Record a deliberate restart from the panel's current session."""
    sessions = async_get_sessions(hass)
    session = sessions.for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    sessions.set_restart_notice(
        session.entry_id, msg["scope"], msg["reason"], msg["expected_back_ms"]
    )
    connection.send_result(msg["id"], {})


@callback
@websocket_command(vol.All(REPORT_EVENT_SCHEMA))
def ws_report_event(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Acknowledge one transient event, counting each event ID once per session."""
    session = async_get_sessions(hass).for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    descriptor = session.descriptors.get(msg["channel"])
    if descriptor is None or descriptor["platform"] != "event":
        connection.send_error(msg["id"], ERR_UNKNOWN_CHANNEL, "Not an event channel.")
        return
    if not session.full_sync_complete:
        # Not counted, so the panel's retry after its full sync still fires.
        connection.send_error(
            msg["id"], ERR_INVALID_FORMAT, "Events wait for the full sync."
        )
        return
    options = descriptor["options"]
    if options is not None and msg["event_type"] not in options:
        connection.send_error(msg["id"], ERR_INVALID_VALUE, "Unknown event type.")
        return
    # A repeat, or any ID not above the last one counted, is acknowledged again
    # but never counted twice.
    if msg["event_id"] > session.last_event_id:
        session.last_event_id = msg["event_id"]
        session.events_received += 1
        # Only a counted event reaches an entity, so a retry never fires twice.
        async_dispatcher_send(
            hass, signal_event(session.entry_id), msg["channel"], msg["event_type"]
        )
    connection.send_result(msg["id"])


def _record_outcome(
    session: PanelSession, command: PendingCommand, msg: dict[str, Any], late: bool
) -> None:
    session.count(msg["outcome"])
    if late:
        session.count("late")
    session.recent_outcomes.append(
        {
            "channel": command.channel,
            "outcome": msg["outcome"],
            "code": msg.get("code"),
            "late": late,
            "at": dt_util.utcnow().isoformat(),
        }
    )


@callback
@websocket_command(COMMAND_RESULT_SCHEMA)
def ws_command_result(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Resolve a sent command with the outcome the panel reports.

    Synchronous, so outcomes apply in arrival order. A result for a command
    that is not waiting for one, such as a repeat or an interim outcome after
    the final one, is acknowledged and changes nothing.
    """
    session = async_get_sessions(hass).for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    command_id = msg["command_id"]
    late = False
    command = session.pending.get(command_id)
    if command is None and (command := session.late.get(command_id)) is not None:
        late = True
    if command is None or (
        msg["outcome"] == OUTCOME_PENDING_APPROVAL and command.approval_pending
    ):
        session.count("ignored")
        connection.send_result(msg["id"])
        return

    _record_outcome(session, command, msg, late)
    if msg["outcome"] == OUTCOME_PENDING_APPROVAL:
        command.approval_pending = True
        if command.on_interim is not None:
            command.on_interim()
    elif late:
        # Too late for its service call; recorded above and nothing else.
        del session.late[command_id]
        command.settle()
    else:
        del session.pending[command_id]
        if not command.future.done():
            command.future.set_result(
                CommandOutcome(
                    msg["outcome"],
                    msg.get("code"),
                    msg.get("result"),
                    msg.get("status"),
                    msg.get("headers"),
                )
            )
    connection.send_result(msg["id"])


@callback
def async_setup_transport(hass: HomeAssistant) -> None:
    """Register the commands once for the domain, never per entry."""
    async_get_sessions(hass)
    websocket_api.async_register_command(hass, ws_hello)
    websocket_api.async_register_command(hass, ws_report_state)
    websocket_api.async_register_command(hass, ws_restart_notice)
    websocket_api.async_register_command(hass, ws_report_event)
    websocket_api.async_register_command(hass, ws_command_result)

    @callback
    def _user_removed(event: Event[Any]) -> None:
        # Removing a user does not close its sockets, so end its sessions here,
        # release its panels for an administrator to bind to a replacement
        # account, and withdraw any request to bind it.
        user_id = event.data.get("user_id")
        if not isinstance(user_id, str):
            return
        async_get_sessions(hass).close_user(user_id, REASON_USER_REMOVED)
        for entry in hass.config_entries.async_entries(DOMAIN):
            async_delete_binding_issue(hass, entry.entry_id, user_id)
            if entry.data.get(CONF_TRANSPORT_USER_ID) == user_id:
                data = dict(entry.data)
                del data[CONF_TRANSPORT_USER_ID]
                hass.config_entries.async_update_entry(entry, data=data)

    hass.bus.async_listen(EVENT_USER_REMOVED, _user_removed)

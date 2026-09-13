"""Native panel transport over Home Assistant's own WebSocket.

A panel signs in to Home Assistant with its own account and calls the
``panel_assistant/*`` commands below. ``hello`` is both the handshake and the
subscription that will carry commands, so the subscription's lifetime is the
session: when the connection closes, Home Assistant unsubscribes it, and that
is the signal that the panel is gone.

Everything a panel sends is untrusted. Each message is validated and bounded
before anything is stored, stored values are the validated copies, and no
handler performs I/O, so Home Assistant applies them in arrival order.

This transport creates no entities and changes no registry state yet. MQTT
stays the authority for every panel entity, so ``hello`` answers with the
``shadow`` authority and grants no commands: the panel reports its state here
only so diagnostics can compare it with the MQTT entities (see
``shadow_comparison``).

A panel's identity is public on the LAN, so ``hello`` never binds a panel to the
account that sends it. Only an administrator binds one, by confirming the
Repairs issue that an unconfirmed ``hello`` raises.
"""

from __future__ import annotations

import logging
import math
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import voluptuous as vol
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import ERR_INVALID_FORMAT
from homeassistant.components.websocket_api.decorators import websocket_command
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from yarl import URL

from .client import is_valid_discovery_id, is_valid_panel_version
from .const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    INTEGRATION_VERSION,
    MAX_ANDROID_INTEGER,
)

_LOGGER = logging.getLogger(__name__)

PROTOCOL_MIN: Final = 1
PROTOCOL_MAX: Final = 1

COMMAND_HELLO: Final = f"{DOMAIN}/hello"
COMMAND_REPORT_STATE: Final = f"{DOMAIN}/report_state"
COMMAND_REPORT_EVENT: Final = f"{DOMAIN}/report_event"

AUTHORITY_MQTT: Final = "mqtt"
# While no native authority exists, shadow is the only mode this side serves.
AUTHORITY_SHADOW: Final = "shadow"
# Capabilities this integration can serve today. Commands and approval are not
# served yet, so a panel is never granted them and never needs the command that
# reports a command's outcome.
KNOWN_CAPABILITIES: Final = frozenset({"state", "events", "commands", "approval"})
SERVED_CAPABILITIES: Final = frozenset({"state", "events"})

# Session end reasons sent in a ``session_closed`` event.
REASON_SUPERSEDED: Final = "superseded"
REASON_ENTRY_UNLOADED: Final = "entry_unloaded"
REASON_USER_REMOVED: Final = "user_removed"
REASON_BINDING_CHANGED: Final = "binding_changed"

# Error codes returned to the panel. The panel renders its own text from them.
ERR_PROTOCOL_UNSUPPORTED: Final = "protocol_unsupported"
ERR_UNKNOWN_PANEL: Final = "unknown_panel"
ERR_PANEL_USER_MISMATCH: Final = "panel_user_mismatch"
ERR_PANEL_IDENTITY_UNAVAILABLE: Final = "panel_identity_unavailable"
ERR_SESSION_UNKNOWN: Final = "session_unknown"
ERR_UNKNOWN_CHANNEL: Final = "unknown_channel"
ERR_INVALID_VALUE: Final = "invalid_value"

# Bounds. Dynamic relays and button LEDs are capped at 64 each on the panel,
# which with every other channel stays well under this.
MAX_CHANNELS: Final = 256
MAX_OBSERVATIONS: Final = MAX_CHANNELS
MAX_CAPABILITIES: Final = 16
MAX_OPTIONS: Final = 64
MAX_ATTRIBUTES: Final = 32
MAX_FAMILY_INDEX: Final = 63
MAX_STRING_LENGTH: Final = 255
MAX_UNIT_LENGTH: Final = 16
MAX_URL_LENGTH: Final = 2048
MAX_SESSION_TOKEN_LENGTH: Final = 64
MAX_EVENT_ID: Final = 2**63 - 1
MAX_JSON_INTEGER: Final = 2**63

PLATFORMS: Final = frozenset(
    {
        "binary_sensor",
        "button",
        "event",
        "image",
        "light",
        "number",
        "select",
        "sensor",
        "switch",
        "text",
        "update",
    }
)
SYNC_FULL_BEGIN: Final = "full_begin"
SYNC_DELTA: Final = "delta"
SYNC_FULL_END: Final = "full_end"
STATE_KNOWN: Final = "known"
STATE_UNAVAILABLE: Final = "unavailable"

_CHANNEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_UNIQUE_SUFFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

DATA_TRANSPORT: Final = "transport"

# The Repairs issue an unconfirmed hello raises, one per entry.
ISSUE_PANEL_USER_MISMATCH: Final = ERR_PANEL_USER_MISMATCH
ISSUE_DATA_ENTRY_ID: Final = "entry_id"
ISSUE_DATA_USER_ID: Final = "user_id"


def signal_session_changed(entry_id: str) -> str:
    """Return the dispatcher signal fired when an entry's session changes."""
    return f"{DOMAIN}_transport_session_{entry_id}"


class ValueRejected(Exception):
    """An observation or event failed validation against its descriptor."""


# ---------------------------------------------------------------------------
# Scalar validators. JSON gives Python types directly, so these check types
# strictly rather than coercing: ``True`` is not a number and "1" is not 1.


def _pattern(pattern: re.Pattern[str]) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if type(value) is not str or pattern.fullmatch(value) is None:
            raise vol.Invalid("invalid format")
        return value

    return validate


_channel = _pattern(_CHANNEL_PATTERN)
_code = _pattern(_CODE_PATTERN)
_unique_suffix = _pattern(_UNIQUE_SUFFIX_PATTERN)
_digest = _pattern(_DIGEST_PATTERN)
_session_token = _pattern(_SESSION_TOKEN_PATTERN)


def _strict_bool(value: Any) -> bool:
    if type(value) is not bool:
        raise vol.Invalid("expected a boolean")
    return value


def _strict_int(minimum: int, maximum: int) -> Callable[[Any], int]:
    def validate(value: Any) -> int:
        if type(value) is not int or not minimum <= value <= maximum:
            raise vol.Invalid("expected a bounded integer")
        return value

    return validate


def _is_finite_number(value: Any) -> bool:
    if type(value) is int:
        return -MAX_JSON_INTEGER <= value <= MAX_JSON_INTEGER
    return type(value) is float and math.isfinite(value)


def _finite_number(value: Any) -> float | int:
    if not _is_finite_number(value):
        raise vol.Invalid("expected a finite number")
    return value  # type: ignore[no-any-return]


def _optional(validator: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def validate(value: Any) -> Any:
        return None if value is None else validator(value)

    return validate


def _plain_string(max_length: int) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if (
            type(value) is not str
            or len(value) > max_length
            or _CONTROL_CHARACTERS.search(value) is not None
        ):
            raise vol.Invalid("expected a bounded string")
        return value

    return validate


def _unit(value: Any) -> str:
    text = _plain_string(MAX_UNIT_LENGTH)(value)
    if not text:
        raise vol.Invalid("empty unit")
    return text


def _bounded_list(max_length: int, item: Any) -> Callable[[Any], list[Any]]:
    """Check a list's type and length before validating any element."""
    item_schema = vol.Schema(item)

    def validate(value: Any) -> list[Any]:
        if type(value) is not list or len(value) > max_length:
            raise vol.Invalid("expected a bounded list")
        return [item_schema(element) for element in value]

    return validate


def _options(value: Any) -> list[str]:
    options = _bounded_list(MAX_OPTIONS, _code)(value)
    if not options or len(set(options)) != len(options):
        raise vol.Invalid("options must be unique and non-empty")
    return options


# ---------------------------------------------------------------------------
# Message schemas. Unknown fields are dropped, never stored or rejected, so a
# newer panel can add fields without breaking an older integration.


def _descriptor_consistent(descriptor: dict[str, Any]) -> dict[str, Any]:
    if (descriptor["family"] is None) != (descriptor["index"] is None):
        raise vol.Invalid("family and index go together")
    low, high, step = descriptor["min"], descriptor["max"], descriptor["step"]
    if low is not None and high is not None and low > high:
        raise vol.Invalid("min exceeds max")
    if step is not None and step <= 0:
        raise vol.Invalid("step must be positive")
    if descriptor["platform"] == "select" and descriptor["options"] is None:
        raise vol.Invalid("a select needs options")
    return descriptor


DESCRIPTOR_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("channel"): _channel,
            vol.Required("platform"): vol.In(PLATFORMS),
            vol.Required("translation_key"): _code,
            vol.Required("unique_suffix"): _unique_suffix,
            vol.Optional("family", default=None): _optional(_code),
            vol.Optional("index", default=None): _optional(
                _strict_int(0, MAX_FAMILY_INDEX)
            ),
            vol.Optional("entity_category", default=None): vol.In(
                (None, "config", "diagnostic")
            ),
            vol.Optional("enabled_default", default=True): _strict_bool,
            vol.Optional("device_class", default=None): _optional(_code),
            vol.Optional("unit", default=None): _optional(_unit),
            vol.Optional("state_class", default=None): _optional(_code),
            vol.Optional("force_update", default=False): _strict_bool,
            vol.Optional("options", default=None): _optional(_options),
            vol.Optional("min", default=None): _optional(_finite_number),
            vol.Optional("max", default=None): _optional(_finite_number),
            vol.Optional("step", default=None): _optional(_finite_number),
            vol.Optional("commandable", default=False): _strict_bool,
            vol.Optional("sensitive", default=False): _strict_bool,
            vol.Optional("retain_last", default=False): _strict_bool,
        },
        extra=vol.REMOVE_EXTRA,
    ),
    _descriptor_consistent,
)


def _channels(value: Any) -> list[dict[str, Any]]:
    descriptors = _bounded_list(MAX_CHANNELS, DESCRIPTOR_SCHEMA)(value)
    names = [descriptor["channel"] for descriptor in descriptors]
    if len(set(names)) != len(names):
        raise vol.Invalid("duplicate channel")
    return descriptors


def _protocol_range(value: dict[str, int]) -> dict[str, int]:
    if value["min"] > value["max"]:
        raise vol.Invalid("protocol min exceeds max")
    return value


def _panel_version(value: Any) -> str:
    if type(value) is not str or not is_valid_panel_version(value):
        raise vol.Invalid("invalid app version")
    return value


def _did(value: Any) -> str:
    if type(value) is not str or not is_valid_discovery_id(value):
        raise vol.Invalid("invalid panel identity")
    return value


HELLO_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_HELLO,
        vol.Required("protocol"): vol.All(
            vol.Schema(
                {
                    vol.Required("min"): _strict_int(1, 1_000),
                    vol.Required("max"): _strict_int(1, 1_000),
                },
                extra=vol.REMOVE_EXTRA,
            ),
            _protocol_range,
        ),
        # Absent or null when the panel has no Android ID to derive it from.
        vol.Optional("did", default=None): _optional(_did),
        vol.Required("app"): vol.Schema(
            {
                vol.Required("version"): _panel_version,
                vol.Required("version_code"): _strict_int(0, MAX_ANDROID_INTEGER),
            },
            extra=vol.REMOVE_EXTRA,
        ),
        vol.Required("contract_digest"): _digest,
        vol.Required("capabilities"): _bounded_list(MAX_CAPABILITIES, _code),
        vol.Required("channels"): _channels,
    },
    extra=vol.REMOVE_EXTRA,
)


def _observation_consistent(observation: dict[str, Any]) -> dict[str, Any]:
    if observation["state"] == STATE_KNOWN and "value" not in observation:
        raise vol.Invalid("a known observation needs a value")
    return observation


OBSERVATION_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("channel"): _channel,
            # "unknown" is never sent: it would change meaning, so it fails.
            vol.Required("state"): vol.In((STATE_KNOWN, STATE_UNAVAILABLE)),
            vol.Optional("value"): object,
            vol.Optional("attributes"): object,
            vol.Optional("refresh", default=False): _strict_bool,
        },
        extra=vol.REMOVE_EXTRA,
    ),
    _observation_consistent,
)

REPORT_STATE_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_REPORT_STATE,
        vol.Required("session"): _session_token,
        vol.Required("sync"): vol.In((SYNC_FULL_BEGIN, SYNC_DELTA, SYNC_FULL_END)),
        vol.Required("observations"): _bounded_list(
            MAX_OBSERVATIONS, OBSERVATION_SCHEMA
        ),
    },
    extra=vol.REMOVE_EXTRA,
)

REPORT_EVENT_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_REPORT_EVENT,
        vol.Required("session"): _session_token,
        vol.Required("channel"): _channel,
        vol.Required("event_id"): _strict_int(0, MAX_EVENT_ID),
        vol.Required("event_type"): _code,
    },
    extra=vol.REMOVE_EXTRA,
)


# ---------------------------------------------------------------------------
# Values, typed by the platform the channel's descriptor declared.


def _reject() -> ValueRejected:
    return ValueRejected(ERR_INVALID_VALUE)


def _byte(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise _reject()
    return value


def _url(value: Any, *, schemes: frozenset[str]) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_URL_LENGTH
        or any(character.isspace() for character in value)
    ):
        raise _reject()
    try:
        url = URL(value)
    except ValueError as err:
        raise _reject() from err
    if url.scheme not in schemes or not url.host:
        raise _reject()
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise _reject()
    return value


def _validate_light(value: Any, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    light = _mapping(value)
    if type(light.get("on")) is not bool:
        raise _reject()
    result: dict[str, Any] = {"on": light["on"]}
    if light.get("brightness") is not None:
        result["brightness"] = _byte(light["brightness"])
    if light.get("color") is not None:
        color = _mapping(light["color"])
        result["color"] = {channel: _byte(color.get(channel)) for channel in "rgb"}
    if light.get("effect") is not None:
        effect = light["effect"]
        options = descriptor["options"]
        if (
            type(effect) is not str
            or _CODE_PATTERN.fullmatch(effect) is None
            or (options is not None and effect not in options)
        ):
            raise _reject()
        result["effect"] = effect
    return result


def _validate_number(value: Any, descriptor: Mapping[str, Any]) -> float | int:
    if not _is_finite_number(value):
        raise _reject()
    low, high = descriptor["min"], descriptor["max"]
    if (low is not None and value < low) or (high is not None and value > high):
        raise _reject()
    return value  # type: ignore[no-any-return]


def _validate_option(value: Any, descriptor: Mapping[str, Any]) -> str:
    if type(value) is not str or value not in (descriptor["options"] or ()):
        raise _reject()
    return value


def _validate_text(value: Any, _descriptor: Mapping[str, Any]) -> str:
    try:
        return _plain_string(MAX_STRING_LENGTH)(value)
    except vol.Invalid as err:
        raise _reject() from err


def _validate_sensor(value: Any, descriptor: Mapping[str, Any]) -> float | int | str:
    if descriptor["options"] is not None:
        return _validate_option(value, descriptor)
    if not _is_finite_number(value):
        raise _reject()
    return value  # type: ignore[no-any-return]


def _validate_boolean(value: Any, _descriptor: Mapping[str, Any]) -> bool:
    if type(value) is not bool:
        raise _reject()
    return value


def _validate_image(value: Any, _descriptor: Mapping[str, Any]) -> dict[str, str]:
    image = _mapping(value)
    return {"url": _url(image.get("url"), schemes=frozenset({"http", "https"}))}


def _optional_version(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return _plain_string(MAX_STRING_LENGTH)(value)
    except vol.Invalid as err:
        raise _reject() from err


def _validate_update(value: Any, _descriptor: Mapping[str, Any]) -> dict[str, Any]:
    update = _mapping(value)
    release_url = update.get("release_url")
    in_progress = update.get("in_progress", False)
    if type(in_progress) is not bool:
        raise _reject()
    return {
        "installed_version": _optional_version(update.get("installed_version")),
        "latest_version": _optional_version(update.get("latest_version")),
        "release_url": (
            None
            if release_url is None
            else _url(release_url, schemes=frozenset({"https"}))
        ),
        "in_progress": in_progress,
    }


def _not_reported(_value: Any, _descriptor: Mapping[str, Any]) -> Any:
    raise _reject()


_VALUE_VALIDATORS: Final[dict[str, Callable[[Any, Mapping[str, Any]], Any]]] = {
    "binary_sensor": _validate_boolean,
    "button": _not_reported,
    "event": _not_reported,
    "image": _validate_image,
    "light": _validate_light,
    "number": _validate_number,
    "select": _validate_option,
    "sensor": _validate_sensor,
    "switch": _validate_boolean,
    "text": _validate_text,
    "update": _validate_update,
}


def _validate_attributes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    attributes = _mapping(value)
    if len(attributes) > MAX_ATTRIBUTES:
        raise _reject()
    result: dict[str, Any] = {}
    for key, item in attributes.items():
        if _CODE_PATTERN.fullmatch(key) is None:
            raise _reject()
        if (
            item is None
            or type(item) is bool
            or _is_finite_number(item)
            or (
                type(item) is str
                and len(item) <= MAX_STRING_LENGTH
                and _CONTROL_CHARACTERS.search(item) is None
            )
        ):
            result[key] = item
        else:
            raise _reject()
    return result


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


@dataclass(slots=True)
class PanelSession:
    """One accepted ``hello`` subscription on one connection."""

    entry_id: str
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
        """Install a session, superseding any earlier one for its entry."""
        if (previous := self._by_entry.get(session.entry_id)) is not None:
            self.close(previous, REASON_SUPERSEDED)
        self._by_entry[session.entry_id] = session
        self._by_token[session.token] = session
        session.connection.subscriptions[session.subscription_id] = (
            self._teardown_callback(session)
        )
        self._changed(session.entry_id)

    @callback
    def close(self, session: PanelSession, reason: str) -> None:
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
        return True

    @callback
    def _changed(self, entry_id: str) -> None:
        async_dispatcher_send(self._hass, signal_session_changed(entry_id))


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
        "authority": AUTHORITY_SHADOW,
        "app_version": session.app_version,
        "app_version_code": session.app_version_code,
        "contract_digest": session.contract_digest,
        "capabilities": sorted(session.capabilities),
        "opened_at": session.opened_at.isoformat(),
        "full_sync_complete": session.full_sync_complete,
        "channels": len(session.descriptors),
        "observations": len(session.observations),
        "rejected_observations": session.rejected_observations,
        "events_received": session.events_received,
    }


# ---------------------------------------------------------------------------
# Shadow comparison. Diagnostics only: computed on demand from cached state,
# never writing to a registry, and never sending anything to the panel.

MQTT_DOMAIN: Final = "mqtt"
REDACTED: Final = "**REDACTED**"
COMPARISON_MATCH: Final = "match"
COMPARISON_DIFFERS: Final = "differs"
COMPARISON_WS_MISSING: Final = "ws_missing"
COMPARISON_WS_REJECTED: Final = "ws_rejected"
COMPARISON_MQTT_MISSING: Final = "mqtt_missing"
COMPARISON_NOT_COMPARED: Final = "not_compared"
COMPARISONS: Final = (
    COMPARISON_MATCH,
    COMPARISON_DIFFERS,
    COMPARISON_WS_MISSING,
    COMPARISON_WS_REJECTED,
    COMPARISON_MQTT_MISSING,
    COMPARISON_NOT_COMPARED,
)
_NUMBER_ABS_TOLERANCE: Final = 1e-3
_NUMBER_REL_TOLERANCE: Final = 1e-6


def _slug(value: Any) -> str:
    """Reduce a label or a code to lowercase letters and digits."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _same_boolean(value: bool, state: State) -> bool:
    return state.state == (STATE_ON if value else STATE_OFF)


def _same_number(value: Any, state: State) -> bool:
    ws, mqtt = _as_float(value), _as_float(state.state)
    return (
        ws is not None
        and mqtt is not None
        and math.isclose(
            ws, mqtt, rel_tol=_NUMBER_REL_TOLERANCE, abs_tol=_NUMBER_ABS_TOLERANCE
        )
    )


def _same_sensor(value: Any, state: State) -> bool:
    if type(value) is str:
        return state.state == value
    return _same_number(value, state)


def _same_option(value: str, state: State) -> bool:
    # MQTT carries display labels such as "Pre-release"; the wire carries codes.
    return _slug(state.state) == _slug(value)


def _same_text(value: str, state: State) -> bool:
    return state.state == value


def _same_light(value: dict[str, Any], state: State) -> bool:
    if not _same_boolean(value["on"], state):
        return False
    if not value["on"]:
        # Home Assistant drops brightness, colour and effect while a light is off.
        return True
    attributes = state.attributes
    if "brightness" in value and attributes.get("brightness") != value["brightness"]:
        return False
    if "color" in value and list(attributes.get("rgb_color") or ()) != [
        value["color"][channel] for channel in "rgb"
    ]:
        return False
    return "effect" not in value or _slug(attributes.get("effect")) == _slug(
        value["effect"]
    )


def _same_update(value: dict[str, Any], state: State) -> bool:
    attributes = state.attributes
    return all(
        attributes.get(key) == value[key]
        for key in ("installed_version", "latest_version")
    )


# How a validated wire value compares with the MQTT entity's state. A platform
# absent here (button, event, image) has no state both sides report alike.
_SHADOW_COMPARATORS: Final[dict[str, Callable[[Any, State], bool]]] = {
    "binary_sensor": _same_boolean,
    "light": _same_light,
    "number": _same_number,
    "select": _same_option,
    "sensor": _same_sensor,
    "switch": _same_boolean,
    "text": _same_text,
    "update": _same_update,
}
_SHADOW_ATTRIBUTES: Final[dict[str, tuple[str, ...]]] = {
    "light": ("brightness", "rgb_color", "effect"),
    "update": ("installed_version", "latest_version"),
}


def _is_user_data(descriptor: Mapping[str, Any], value: Any) -> bool:
    """Return whether a value may be a path, SSID or other user data."""
    if type(value) is not str:
        return False
    if descriptor["platform"] == "text":
        return True
    return (
        descriptor["platform"] == "sensor"
        and descriptor["options"] is None
        and _as_float(value) is None
    )


def _shown_state(descriptor: Mapping[str, Any], state: State) -> str:
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return state.state
    return REDACTED if _is_user_data(descriptor, state.state) else state.state


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _age(now: datetime, moment: datetime) -> float:
    return round((now - moment).total_seconds(), 3)


def _compare(
    descriptor: Mapping[str, Any],
    observation: Observation | None,
    rejected: str | None,
    state: State | None,
) -> str:
    comparator = _SHADOW_COMPARATORS.get(descriptor["platform"])
    if comparator is None:
        return COMPARISON_NOT_COMPARED
    if state is None:
        return COMPARISON_MQTT_MISSING
    if rejected is not None:
        return COMPARISON_WS_REJECTED
    if observation is None:
        return COMPARISON_WS_MISSING
    if STATE_UNAVAILABLE in (observation.state, state.state):
        both = observation.state == state.state == STATE_UNAVAILABLE
        return COMPARISON_MATCH if both else COMPARISON_DIFFERS
    if comparator(observation.value, state):
        return COMPARISON_MATCH
    return COMPARISON_DIFFERS


def _shadow_channel(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    panel_id: str,
    session: PanelSession,
    descriptor: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    platform = descriptor["platform"]
    observation = session.observations.get(descriptor["channel"])
    rejected = session.rejections.get(descriptor["channel"])
    ws: dict[str, Any] | None = None
    if observation is not None or rejected is not None:
        ws = {
            "state": None,
            "value": None,
            "attributes": {},
            "received_at": None,
            "age_s": None,
            "refresh": None,
            "rejected": rejected,
        }
        if observation is not None:
            ws |= {
                "state": observation.state,
                "value": (
                    REDACTED
                    if _is_user_data(descriptor, observation.value)
                    else observation.value
                ),
                "attributes": dict(observation.attributes),
                "received_at": observation.received_at.isoformat(),
                "age_s": _age(now, observation.received_at),
                "refresh": observation.refresh,
            }

    entity_id = entity_registry.async_get_entity_id(
        platform, MQTT_DOMAIN, f"{panel_id}_{descriptor['unique_suffix']}"
    )
    state = None if entity_id is None else hass.states.get(entity_id)
    mqtt: dict[str, Any] | None = None
    if entity_id is not None:
        mqtt = {
            "entity_id": entity_id,
            "state": None,
            "attributes": {},
            "last_reported": None,
            "last_changed": None,
            "age_s": None,
        }
        if state is not None:
            mqtt |= {
                "state": _shown_state(descriptor, state),
                "attributes": {
                    key: state.attributes[key]
                    for key in _SHADOW_ATTRIBUTES.get(platform, ())
                    if key in state.attributes
                },
                "last_reported": state.last_reported.isoformat(),
                "last_changed": state.last_changed.isoformat(),
                "age_s": _age(now, state.last_reported),
            }

    return {
        "platform": platform,
        "unique_suffix": descriptor["unique_suffix"],
        "ws": ws,
        "mqtt": mqtt,
        "comparison": _compare(descriptor, observation, rejected, state),
        "freshness_delta_s": (
            None
            if observation is None or state is None
            else round(
                (observation.received_at - state.last_reported).total_seconds(), 3
            )
        ),
    }


def shadow_comparison(
    hass: HomeAssistant, entry_id: str, panel_id: str
) -> dict[str, Any] | None:
    """Compare what the panel last reported natively with its MQTT entities.

    Reads the registries and the state machine only. Returns None when the
    entry has never held a session.
    """
    session = async_get_sessions(hass).latest(entry_id)
    if session is None:
        return None
    now = dt_util.utcnow()
    entity_registry = er.async_get(hass)
    channels = {
        channel: _shadow_channel(
            hass, entity_registry, panel_id, session, descriptor, now
        )
        for channel, descriptor in sorted(session.descriptors.items())
    }
    summary = dict.fromkeys(COMPARISONS, 0)
    for item in channels.values():
        summary[item["comparison"]] += 1

    device = dr.async_get(hass).async_get_device(
        identifiers={(MQTT_DOMAIN, f"ha-paneld-{panel_id}")}
    )
    mqtt_only: list[str] = []
    if device is not None:
        prefix = f"{panel_id}_"
        described = {
            descriptor["unique_suffix"] for descriptor in session.descriptors.values()
        }
        mqtt_only = sorted(
            {
                item.unique_id.removeprefix(prefix)
                for item in er.async_entries_for_device(
                    entity_registry, device.id, include_disabled_entities=True
                )
                if item.platform == MQTT_DOMAIN and item.unique_id.startswith(prefix)
            }
            - described
        )
    return {
        "mqtt_device": device is not None,
        "summary": summary,
        "channels": channels,
        "mqtt_only": mqtt_only,
    }


# ---------------------------------------------------------------------------
# Binding. A panel's user is bound only on an administrator's confirmation.


def binding_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID asking an administrator to bind an entry."""
    return f"{ISSUE_PANEL_USER_MISMATCH}_{entry_id}"


@callback
def async_raise_binding_issue(
    hass: HomeAssistant, entry: ConfigEntry, user_id: str
) -> None:
    """Ask an administrator whether this user may connect as the entry's panel."""
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
        translation_placeholders={"panel": entry.title},
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


# ---------------------------------------------------------------------------
# Command handlers. All synchronous, so they run in arrival order.


def _panel_did(entry: ConfigEntry) -> str | None:
    """Return the identity an entry's panel reports, else its discovery ID."""
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    snapshot = getattr(coordinator, "data", None)
    health = getattr(snapshot, "health", None)
    reported = getattr(health, "discovery_id", None)
    if isinstance(reported, str):
        return reported
    if entry.unique_id is not None and is_valid_discovery_id(entry.unique_id):
        return entry.unique_id
    return None


def _entry_for_did(hass: HomeAssistant, did: str) -> ConfigEntry | None:
    """Return the one loaded entry for a panel identity, never a guess."""
    matches = [
        entry
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if _panel_did(entry) == did
    ]
    if len(matches) > 1:
        _LOGGER.warning("Several panel entries report one identity; refusing hello")
        return None
    return matches[0] if matches else None


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
    low = max(requested["min"], PROTOCOL_MIN)
    high = min(requested["max"], PROTOCOL_MAX)
    if low > high:
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

    entry = _entry_for_did(hass, did)
    if entry is None:
        connection.send_error(
            msg["id"], ERR_UNKNOWN_PANEL, "No loaded panel entry has this identity."
        )
        return

    user_id = connection.user.id
    if entry.data.get(CONF_TRANSPORT_USER_ID) != user_id:
        # A removed user's socket survives its removal, but its refresh token
        # does not. Such a connection may not even ask to be bound.
        if (
            connection.refresh_token_id is not None
            and hass.auth.async_get_refresh_token(connection.refresh_token_id)
            is not None
        ):
            async_raise_binding_issue(hass, entry, user_id)
        connection.send_error(
            msg["id"],
            ERR_PANEL_USER_MISMATCH,
            "An administrator has not confirmed this user for this panel.",
        )
        return
    async_delete_binding_issue(hass, entry.entry_id, user_id)

    capabilities = SERVED_CAPABILITIES.intersection(msg["capabilities"])
    session = PanelSession(
        entry_id=entry.entry_id,
        token=secrets.token_urlsafe(24),
        connection=connection,
        subscription_id=msg["id"],
        user_id=user_id,
        protocol=high,
        app_version=msg["app"]["version"],
        app_version_code=msg["app"]["version_code"],
        contract_digest=msg["contract_digest"],
        capabilities=capabilities,
        descriptors={item["channel"]: item for item in msg["channels"]},
        opened_at=dt_util.utcnow(),
    )
    async_get_sessions(hass).open(session)
    connection.send_result(
        msg["id"],
        {
            "protocol": high,
            "session": session.token,
            "authority": AUTHORITY_SHADOW,
            "capabilities": sorted(capabilities),
            "integration": {"version": INTEGRATION_VERSION},
            # The shared channel catalogue arrives with the vendored contract
            # file; until then every well-formed descriptor is accepted as is.
            "channels": {"accepted": len(session.descriptors), "unknown": []},
        },
    )


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
    now = dt_util.utcnow()
    for observation in msg["observations"]:
        channel = observation["channel"]
        descriptor = session.descriptors.get(channel)
        if descriptor is None:
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
        session.observations[channel] = Observation(
            state=observation["state"],
            value=value,
            attributes=attributes,
            refresh=observation["refresh"],
            received_at=now,
        )
    session.rejected_observations += len(rejected)

    if sync == SYNC_FULL_BEGIN:
        session.full_sync_begun = True
    elif sync == SYNC_FULL_END:
        session.full_sync_begun = False
        if not session.full_sync_complete:
            session.full_sync_complete = True
            sessions.mark_changed(session)
    connection.send_result(msg["id"], {"rejected": rejected})


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
    options = descriptor["options"]
    if options is not None and msg["event_type"] not in options:
        connection.send_error(msg["id"], ERR_INVALID_VALUE, "Unknown event type.")
        return
    # A repeat, or any ID not above the last one counted, is acknowledged again
    # but never counted twice.
    if msg["event_id"] > session.last_event_id:
        session.last_event_id = msg["event_id"]
        session.events_received += 1
    connection.send_result(msg["id"])


@callback
def async_setup_transport(hass: HomeAssistant) -> None:
    """Register the commands once for the domain, never per entry."""
    async_get_sessions(hass)
    websocket_api.async_register_command(hass, ws_hello)
    websocket_api.async_register_command(hass, ws_report_state)
    websocket_api.async_register_command(hass, ws_report_event)

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

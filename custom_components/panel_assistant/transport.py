"""Native panel transport over Home Assistant's own WebSocket.

A panel signs in to Home Assistant with its own account and calls the
``panel_assistant/*`` commands below. ``hello`` is both the handshake and the
subscription that will carry commands, so the subscription's lifetime is the
session: when the connection closes, Home Assistant unsubscribes it, and that
is the signal that the panel is gone.

Everything a panel sends is untrusted. Each message is validated and bounded
before anything is stored. Established-session handlers perform no I/O. A
moved legacy panel must complete bounded health verification before its hello
can grant a session.

``hello`` answers with the entry's authority. By default that is ``shadow``:
MQTT owns every panel entity and its commands, and the panel reports its state
here so diagnostics can compare it with the MQTT entities (see
``shadow_comparison``). The native entities that render these reports stay
dormant unless the ``native_entities`` option is set (see ``native.py``), and
only with that option can an entry's options choose ``native``, under which
this integration sends the panel's commands on the session and waits for each
outcome (see ``async_send_command``), and the panel's MQTT entities are moved
to this integration at the entry's next setup (see ``cutover.py``). ``hello``
also answers whether the panel should withdraw its MQTT discovery, which it
does only once that move completed and nothing holds it back (see
``mqtt_discovery_claim``). While this integration owns them, what a panel that
still announces its MQTT discovery creates is kept disabled, and a removed
entry's panel is told so (see ``guards.py``). This module itself never writes
an entity or device registry.

A panel's identity is public on the LAN, so ``hello`` never binds a panel to the
account that sends it. Only an administrator binds one, by confirming the
Repairs issue that an unconfirmed ``hello`` raises.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets
from collections import deque
from collections.abc import Callable, Collection, Container, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import Any, Final

import voluptuous as vol
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.const import ERR_INVALID_FORMAT
from homeassistant.components.websocket_api.decorators import websocket_command
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from yarl import URL

from .client import is_valid_discovery_id, is_valid_panel_version
from .const import (
    CONF_AUTHORITY,
    CONF_CUTOVER,
    CONF_SUPPORTED_CHANNELS,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    INTEGRATION_VERSION,
    MAX_ANDROID_INTEGER,
)
from .contract import CONTRACT, catalogue_entry, catalogue_entry_for_channel
from .embed_proof import encode_key, new_key
from .ha_url import DATA_INSTANCE_ID, async_connection_urls

_LOGGER = logging.getLogger(__name__)

PROTOCOL_MIN: Final = 1
PROTOCOL_MAX: Final = 3

COMMAND_HELLO: Final = f"{DOMAIN}/hello"
COMMAND_REPORT_STATE: Final = f"{DOMAIN}/report_state"
COMMAND_REPORT_EVENT: Final = f"{DOMAIN}/report_event"
COMMAND_COMMAND_RESULT: Final = f"{DOMAIN}/command_result"
COMMAND_RESTART_NOTICE: Final = f"{DOMAIN}/restart_notice"

# Who owns a panel's entities and commands. MQTT, unless an entry's options
# choose otherwise while native entities are turned on.
AUTHORITY_MQTT: Final = "mqtt"
AUTHORITY_SHADOW: Final = "shadow"
AUTHORITY_NATIVE: Final = "native"
AUTHORITIES: Final = (AUTHORITY_MQTT, AUTHORITY_SHADOW, AUTHORITY_NATIVE)
DEFAULT_AUTHORITY: Final = AUTHORITY_SHADOW
# What ``hello`` tells the panel to do with its MQTT discovery. It withdraws
# its MQTT entities only once this integration owns them: the entry's
# authority is native, its cutover record is complete, and no MQTT entity that
# stayed behind carries a person's customisation or waits for its channel to be
# described (see ``mqtt_discovery_claim``).
MQTT_DISCOVERY_WITHDRAW: Final = "withdraw"
MQTT_DISCOVERY_ANNOUNCE: Final = "announce"
MQTT_DISCOVERIES: Final = (MQTT_DISCOVERY_WITHDRAW, MQTT_DISCOVERY_ANNOUNCE)

# The cutover record an entry's data carries while, and after, its MQTT
# entities are moved to this integration (see ``cutover.py``).
CUTOVER_IN_PROGRESS: Final = "in_progress"
CUTOVER_COMPLETE: Final = "complete"
CUTOVER_REVERSING: Final = "reversing"
CUTOVER_STATE: Final = "state"
CUTOVER_UNMIGRATED: Final = "unmigrated"
CUTOVER_REGISTRY_ID: Final = "registry_id"
CUTOVER_ENTITIES: Final = "entities"
CUTOVER_REASON: Final = "reason"
CUTOVER_CHANNEL: Final = "channel"
# Why an MQTT entity stayed behind: the catalogue knows its channel, but the
# panel has never described that channel, so no native entity would render
# into it. It stays MQTT's, and the next move after the panel describes the
# channel takes it (see ``cutover.py``).
CUTOVER_NOT_DESCRIBED: Final = "not_described"
# The registry IDs of MQTT duplicates kept disabled while this integration owns
# the panel's entities (see ``guards.py``).
CUTOVER_QUARANTINED: Final = "quarantined"

CAPABILITY_STATE: Final = "state"
CAPABILITY_EVENTS: Final = "events"
CAPABILITY_COMMANDS: Final = "commands"
CAPABILITY_APPROVAL: Final = "approval"
# A panel that offers this withdraws or announces its MQTT discovery exactly as
# the hello reply says. It is granted whenever offered, under every authority.
CAPABILITY_MQTT_WITHDRAW: Final = "mqtt_withdraw"
# A panel that offers this receives a key to check the sidebar's proofs with
# (see ``embed_proof.py``). It is granted whenever offered, under every authority.
CAPABILITY_EMBED_PROOF: Final = "embed_proof"
# A panel that offers this is an Assist satellite (see ``voice.py``). Voice has
# no MQTT counterpart to defer to, so it is granted whenever offered.
CAPABILITY_VOICE: Final = "voice"
KNOWN_CAPABILITIES: Final = frozenset(
    {
        CAPABILITY_STATE,
        CAPABILITY_EVENTS,
        CAPABILITY_COMMANDS,
        CAPABILITY_APPROVAL,
        CAPABILITY_MQTT_WITHDRAW,
        CAPABILITY_EMBED_PROOF,
        CAPABILITY_VOICE,
    }
)
# What each authority lets a session use, before intersecting with what the
# panel offered. The panel reports state under shadow and native alike, since a
# native entity is available only while its channel is reported.
AUTHORITY_GRANTS: Final[dict[str, frozenset[str]]] = {
    AUTHORITY_MQTT: frozenset(),
    AUTHORITY_SHADOW: frozenset({CAPABILITY_STATE, CAPABILITY_EVENTS}),
    AUTHORITY_NATIVE: frozenset(
        {CAPABILITY_STATE, CAPABILITY_EVENTS, CAPABILITY_COMMANDS, CAPABILITY_APPROVAL}
    ),
}

# Session end reasons sent in a ``session_closed`` event.
REASON_SUPERSEDED: Final = "superseded"
REASON_ENTRY_UNLOADED: Final = "entry_unloaded"
REASON_USER_REMOVED: Final = "user_removed"
REASON_BINDING_CHANGED: Final = "binding_changed"
REASON_AUTHORITY_CHANGED: Final = "authority_changed"

# Command outcomes a panel reports. Interim ``pending_approval`` is followed by
# exactly one final outcome.
OUTCOME_APPLIED: Final = "applied"
OUTCOME_SUPERSEDED: Final = "superseded"
OUTCOME_PENDING_APPROVAL: Final = "pending_approval"
OUTCOME_REFUSED: Final = "refused"
OUTCOME_FAILED: Final = "failed"
OUTCOMES: Final = (
    OUTCOME_APPLIED,
    OUTCOME_SUPERSEDED,
    OUTCOME_PENDING_APPROVAL,
    OUTCOME_REFUSED,
    OUTCOME_FAILED,
)
# The outcomes that must name a code from the contract's closed list.
OUTCOMES_WITH_CODE: Final = frozenset({OUTCOME_REFUSED, OUTCOME_FAILED})
OUTCOME_CODES: Final = frozenset(CONTRACT["outcome_codes"])

# Translated errors a native command raises in Home Assistant, besides the
# outcome codes a panel reports.
ERR_PANEL_UNAVAILABLE: Final = "panel_unavailable"
ERR_AUTHORITY_MISMATCH: Final = "authority_mismatch"
ERR_NOT_COMMANDABLE: Final = "not_commandable"
ERR_APPROVAL_PENDING: Final = "approval_pending"
COMMAND_ERRORS: Final = OUTCOME_CODES | {ERR_PANEL_UNAVAILABLE, ERR_APPROVAL_PENDING}

# How long a service call waits for a command's outcome, from sending it.
COMMAND_TIMEOUT: Final = 30.0
# How long the panel may hold a command before it answers that it expired.
COMMAND_DEADLINE_MS: Final = 10_000
# Commands whose wait ended without a final outcome, remembered so a late one
# is still recorded; and outcomes kept for diagnostics.
MAX_LATE_COMMANDS: Final = 16
MAX_RECENT_OUTCOMES: Final = 16
MAX_PLACEHOLDERS: Final = 8

# Error codes returned to the panel. The panel renders its own text from them.
ERR_PROTOCOL_UNSUPPORTED: Final = "protocol_unsupported"
ERR_UNKNOWN_PANEL: Final = "unknown_panel"
ERR_PANEL_USER_MISMATCH: Final = "panel_user_mismatch"
ERR_PANEL_IDENTITY_UNAVAILABLE: Final = "panel_identity_unavailable"
# No loaded entry has the panel's identity, and an entry that had it was
# removed: the panel releases its claim on its MQTT entities.
ERR_ENTRY_REMOVED: Final = "entry_removed"
ERR_SESSION_UNKNOWN: Final = "session_unknown"
ERR_UNKNOWN_CHANNEL: Final = "unknown_channel"
ERR_INVALID_VALUE: Final = "invalid_value"

# Bounds. Dynamic relays and button LEDs are capped at 64 each on the panel,
# which with every other channel stays well under this.
MAX_CHANNELS: Final = 256
MAX_OBSERVATIONS: Final = MAX_CHANNELS
MAX_CAPABILITIES: Final = 16
MAX_UNSUPPORTED: Final = 128
MAX_OPTIONS: Final = 64
MAX_ATTRIBUTES: Final = 32
MAX_FAMILY_INDEX: Final = 64
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
DATA_NATIVE_ENTITIES: Final = "native_entities"
# The identities of panels whose entry was removed (see ``guards.py``).
DATA_REMOVED_PANELS: Final = "removed_panels"
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
ISSUE_MERGED_PANEL_IDENTITY: Final = "merged_panel_identity"
ISSUE_DATA_ENTRY_ID: Final = "entry_id"
ISSUE_DATA_USER_ID: Final = "user_id"
# The Repairs issues a cutover raises: one when a step failed and the move
# resumes on the next setup, one when MQTT entities that stay behind carry a
# person's customisation and so hold back the panel's MQTT withdrawal.
ISSUE_CUTOVER_INCOMPLETE: Final = "cutover_incomplete"
ISSUE_CUTOVER_BLOCKED: Final = "cutover_blocked_by_customised_entities"
ISSUE_NATIVE_CONTROLS_UNAVAILABLE: Final = "native_controls_unavailable"


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


def _interface_address(value: Any) -> str:
    """Bound panel address hints to usable, unscoped IP literals."""
    if not isinstance(value, str) or len(value) > 45 or "%" in value:
        raise vol.Invalid("expected an IP address")
    try:
        parsed = ip_address(value)
        address = getattr(parsed, "ipv4_mapped", None) or parsed
    except ValueError as err:
        raise vol.Invalid("expected an IP address") from err
    if (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        raise vol.Invalid("expected a panel interface address")
    return str(address)


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
        vol.Optional("addresses", default=list): _bounded_list(16, _interface_address),
        vol.Required("contract_digest"): _digest,
        vol.Required("capabilities"): _bounded_list(MAX_CAPABILITIES, _code),
        vol.Required("channels"): _channels,
        # The channels the panel states it cannot serve. Absent from an older
        # panel, which states nothing.
        vol.Optional("unsupported", default=list): _bounded_list(
            MAX_UNSUPPORTED, _channel
        ),
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

RESTART_NOTICE_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_RESTART_NOTICE,
        vol.Required("session"): _session_token,
        vol.Required("scope"): vol.In(("app", "panel")),
        vol.Required("reason"): vol.In(("update", "settings", "recovery", "reboot")),
        vol.Required("expected_back_ms"): _strict_int(1, 300_000),
    },
    extra=vol.REMOVE_EXTRA,
)


def _placeholders(value: Any) -> dict[str, str]:
    if type(value) is not dict or len(value) > MAX_PLACEHOLDERS:
        raise vol.Invalid("expected bounded placeholders")
    return {
        _code(key): _plain_string(MAX_STRING_LENGTH)(item)
        for key, item in value.items()
    }


def _outcome_consistent(result: dict[str, Any]) -> dict[str, Any]:
    if (result["outcome"] in OUTCOMES_WITH_CODE) != ("code" in result):
        raise vol.Invalid("refused and failed carry a code, other outcomes none")
    return result


COMMAND_RESULT_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("type"): COMMAND_COMMAND_RESULT,
            vol.Required("session"): _session_token,
            vol.Required("command_id"): _session_token,
            # An unknown outcome would change meaning, so it fails.
            vol.Required("outcome"): vol.In(OUTCOMES),
            vol.Optional("code"): vol.In(OUTCOME_CODES),
            vol.Optional("placeholders"): _placeholders,
        },
        extra=vol.REMOVE_EXTRA,
    ),
    _outcome_consistent,
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


def _validate_timestamp(value: Any) -> str:
    text = _validate_text(value, {})
    moment = dt_util.parse_datetime(text)
    if moment is None or moment.tzinfo is None:
        raise _reject()
    return text


def _validate_sensor(value: Any, descriptor: Mapping[str, Any]) -> float | int | str:
    """Type a sensor value by what its descriptor declares.

    Options make an enum, a timestamp class a zone-aware ISO time, and a unit,
    state class or other device class a measurement. A sensor declaring none of
    these, such as an IP address or a Wi-Fi network name, carries text.
    """
    if descriptor["options"] is not None:
        return _validate_option(value, descriptor)
    if descriptor["device_class"] == "timestamp":
        return _validate_timestamp(value)
    measured = (
        descriptor["unit"] is not None
        or descriptor["state_class"] is not None
        or descriptor["device_class"] is not None
    )
    if _is_finite_number(value):
        return value  # type: ignore[no-any-return]
    if measured:
        raise _reject()
    return _validate_text(value, descriptor)


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
    # The authority the hello reply granted. An options change that makes the
    # entry's authority differ ends the session.
    authority: str = DEFAULT_AUTHORITY
    # What the hello reply told the panel to do with its MQTT discovery.
    mqtt_discovery: str = MQTT_DISCOVERY_ANNOUNCE
    # Whether the panel offered to follow that answer. One that did not
    # announces its MQTT discovery whatever the reply says.
    mqtt_withdraw_offered: bool = False
    # The key the panel checks the sidebar's proofs with, while this session
    # lasts, and the counter of the last proof issued under it. Never shown.
    embed_key_id: str | None = field(default=None, repr=False)
    embed_key: bytes | None = field(default=None, repr=False)
    embed_counter: int = 0
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


@dataclass(slots=True)
class PendingCommand:
    """One command sent on a session and not yet answered with a final outcome.

    The future resolves with the final outcome, or with None when the session
    ends first.
    """

    channel: str
    future: asyncio.Future[CommandOutcome | None]
    approval_pending: bool = False


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
        session.late.clear()
        # The panel discards its key when the session ends; so does this side.
        session.embed_key_id = None
        session.embed_key = None
        return True

    @callback
    def _changed(self, entry_id: str) -> None:
        async_dispatcher_send(self._hass, signal_session_changed(entry_id))


def native_entities_enabled(hass: HomeAssistant) -> bool:
    """Return whether native entities were turned on for every panel in YAML."""
    return hass.data.get(DOMAIN, {}).get(DATA_NATIVE_ENTITIES) is True


def native_entities_turned_off(hass: HomeAssistant) -> bool:
    """Return whether YAML turned native entities off for every panel."""
    return hass.data.get(DOMAIN, {}).get(DATA_NATIVE_ENTITIES) is False


def native_enabled_for(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return whether this entry may use native entities.

    ``native_entities: true`` in YAML turns them on for every panel and
    ``false`` turns them off for every panel. Without either, an entry has them
    when it chose ``native``, which every panel added from 0.6.3 does, so
    running a panel natively never needs a YAML edit.
    """
    if native_entities_turned_off(hass):
        return False
    return (
        native_entities_enabled(hass)
        or entry.options.get(CONF_AUTHORITY) == AUTHORITY_NATIVE
    )


def effective_authority(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the authority a panel session of this entry is granted.

    The entry's option counts only while native entities are turned on, so a
    release carries the choice dark and answers shadow.
    """
    if not native_enabled_for(hass, entry):
        return DEFAULT_AUTHORITY
    authority = entry.options.get(CONF_AUTHORITY, DEFAULT_AUTHORITY)
    return authority if authority in AUTHORITIES else DEFAULT_AUTHORITY


@callback
def async_apply_authority(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """End a live session whose granted authority is no longer the entry's.

    Every change to an entry calls this, binding writes included, so it acts
    only on a real change. The panel sends hello again and is granted anew.
    """
    sessions = async_get_sessions(hass)
    session = sessions.get(entry.entry_id)
    if session is not None and session.authority != effective_authority(hass, entry):
        sessions.close(session, REASON_AUTHORITY_CHANGED)


def async_get_sessions(hass: HomeAssistant) -> TransportSessions:
    """Return the process-wide session table, creating it on first use."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    sessions = domain_data.get(DATA_TRANSPORT)
    if sessions is None:
        sessions = domain_data[DATA_TRANSPORT] = TransportSessions(hass)
    return sessions


def session_connected(hass: HomeAssistant, entry_id: str) -> bool:
    """Return whether an entry's panel holds an open session.

    An accepted hello is proof the panel is talking to Home Assistant, which is
    what the device's availability answers. Whether the session has also
    described and reported every channel is `session_available`, the native
    entities' own bar; the two are different questions and stay separate.
    """
    return async_get_sessions(hass).get(entry_id) is not None


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
        "authority": session.authority,
        "mqtt_discovery_granted": session.mqtt_discovery,
        "mqtt_withdraw_offered": session.mqtt_withdraw_offered,
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

    # The MQTT integration owns this device, so the lookup cannot name our
    # config entry; the multi-device form is the one Core 2026.9 still allows.
    devices = dr.async_get(hass).async_get_devices(
        identifiers={(MQTT_DOMAIN, f"ha-paneld-{panel_id}")}
    )
    device = devices[0] if devices else None
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
# The cutover record and the MQTT discovery claim it decides. The record is
# written by ``cutover.py`` during setup; here it is only read.


def cutover_record(entry: ConfigEntry) -> Mapping[str, Any] | None:
    """Return an entry's cutover record, if it holds one."""
    record = entry.data.get(CONF_CUTOVER)
    return record if isinstance(record, Mapping) else None


def is_customised(item: er.RegistryEntry) -> bool:
    """Return whether a person changed this registry entry.

    A name, icon, area, label or alias of their own, hiding it, or disabling it
    themselves all count. What an integration or a config entry did does not,
    nor does the computed-name alias Home Assistant gives every new entry.
    """
    return (
        item.name is not None
        or item.icon is not None
        or item.area_id is not None
        or bool(item.labels)
        or any(alias is not er.COMPUTED_NAME for alias in item.aliases)
        or item.hidden_by is not None
        or item.disabled_by is er.RegistryEntryDisabler.USER
    )


def blocking_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return the customised MQTT entities the cutover left behind, as of now.

    They are looked up by registry ID, so one a person deleted since no longer
    counts and no reload is needed for the claim to change.
    """
    record = cutover_record(entry)
    if record is None:
        return []
    registry = er.async_get(hass)
    blocking: list[str] = []
    for unmigrated in record.get(CUTOVER_UNMIGRATED, ()):
        if "disabled_by_before" in unmigrated:
            continue
        item = registry.entities.get_entry(unmigrated[CUTOVER_REGISTRY_ID])
        if item is not None and is_customised(item):
            blocking.append(item.entity_id)
    return sorted(blocking)


def suspended_control_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> list[str]:
    """Return preserved MQTT controls the native panel has not offered."""
    record = cutover_record(entry)
    if record is None:
        return []
    registry = er.async_get(hass)
    return sorted(
        item.entity_id
        for row in record.get(CUTOVER_UNMIGRATED, ())
        if "disabled_by_before" in row
        and (item := registry.entities.get_entry(row[CUTOVER_REGISTRY_ID])) is not None
        and item.platform == MQTT_DOMAIN
    )


def entity_owner(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return who owns the panel's entities now.

    This integration does once a cutover completed under the native authority;
    until then, and again after a release, MQTT does.
    """
    record = cutover_record(entry)
    if (
        effective_authority(hass, entry) == AUTHORITY_NATIVE
        and record is not None
        and record.get(CUTOVER_STATE) == CUTOVER_COMPLETE
    ):
        return AUTHORITY_NATIVE
    return AUTHORITY_MQTT


def undescribed_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[Mapping[str, Any]]:
    """Return the cutover's leftovers still waiting for their channel, as of now.

    Each is an MQTT entity whose known channel the panel has not described.
    One deleted since, or no longer MQTT's, no longer counts.
    """
    record = cutover_record(entry)
    if record is None:
        return []
    registry = er.async_get(hass)
    waiting: list[Mapping[str, Any]] = []
    for unmigrated in record.get(CUTOVER_UNMIGRATED, ()):
        if unmigrated.get(CUTOVER_REASON) != CUTOVER_NOT_DESCRIBED:
            continue
        item = registry.entities.get_entry(unmigrated[CUTOVER_REGISTRY_ID])
        if item is not None and item.platform == MQTT_DOMAIN:
            waiting.append(unmigrated)
    return waiting


def mqtt_discovery_claim(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return what the next hello tells the panel about its MQTT discovery.

    Withdraw only once this integration owns the panel's entities and nothing
    blocks it. An MQTT entity waiting for its channel to be described holds
    the withdrawal back too: the panel's tombstones would delete it, and no
    native entity replaces it yet.
    """
    if (
        entity_owner(hass, entry) != AUTHORITY_NATIVE
        or blocking_entity_ids(hass, entry)
        or undescribed_entities(hass, entry)
        or suspended_control_entity_ids(hass, entry)
    ):
        return MQTT_DISCOVERY_ANNOUNCE
    return MQTT_DISCOVERY_WITHDRAW


# ---------------------------------------------------------------------------
# The channels a panel supports. A cutover moves an MQTT entity only when the
# panel has described its channel, since only then does a native entity render
# into it. The set is the union of every hello's known channels, kept in the
# entry's data: a later session that omits a channel leaves it in, because a
# channel missing from one session is only unavailable. It never shrinks from
# an omission. It shrinks only when a hello lists a channel as unsupported, the
# panel's explicit statement that it cannot serve it; that channel's native
# entity is removed at the same time. It is replaced only when a hello comes
# from another panel identity, whose channels the earlier set says nothing
# about, and it goes with the entry.


def supported_channels(entry: ConfigEntry, did: str | None) -> frozenset[str] | None:
    """Return the channels this identity has described, or None before any hello."""
    stored = entry.data.get(CONF_SUPPORTED_CHANNELS)
    if (
        did is None
        or not isinstance(stored, Mapping)
        or stored.get("did") != did
        or not isinstance(channels := stored.get("channels"), list)
    ):
        return None
    return frozenset(channel for channel in channels if isinstance(channel, str))


@callback
def async_record_supported_channels(
    hass: HomeAssistant,
    entry: ConfigEntry,
    did: str,
    channels: Collection[str],
    unsupported: Collection[str] = (),
) -> None:
    """Add what a hello described to the entry's channels, and drop what it
    listed as unsupported; write only a change.
    """
    known = supported_channels(entry, did)
    updated = (known or frozenset()).union(channels).difference(unsupported)
    if known is not None and known == updated:
        return
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_SUPPORTED_CHANNELS: {"did": did, "channels": sorted(updated)},
        },
    )


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


def cutover_issue_id(issue: str, entry_id: str) -> str:
    """Return the Repairs issue ID of one cutover issue for one entry."""
    return f"{issue}_{entry_id}"


@callback
def async_raise_cutover_incomplete_issue(
    hass: HomeAssistant, entry: ConfigEntry, step: str, error: str
) -> None:
    """Report a cutover step that failed; the next setup carries on from the record."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        cutover_issue_id(ISSUE_CUTOVER_INCOMPLETE, entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_CUTOVER_INCOMPLETE,
        translation_placeholders={"panel": entry.title, "step": step, "error": error},
    )


@callback
def async_raise_cutover_blocked_issue(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]
) -> None:
    """Report the customised MQTT entities that hold back the panel's withdrawal."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        cutover_issue_id(ISSUE_CUTOVER_BLOCKED, entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CUTOVER_BLOCKED,
        translation_placeholders={
            "panel": entry.title,
            "entities": ", ".join(entity_ids),
        },
    )


@callback
def async_raise_native_controls_unavailable_issue(
    hass: HomeAssistant, entry: ConfigEntry, entity_ids: list[str]
) -> None:
    """Report controls that this panel has not offered over native transport."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        cutover_issue_id(ISSUE_NATIVE_CONTROLS_UNAVAILABLE, entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NATIVE_CONTROLS_UNAVAILABLE,
        translation_placeholders={
            "panel": entry.title,
            "entities": ", ".join(entity_ids),
        },
    )


@callback
def async_delete_cutover_issues(
    hass: HomeAssistant, entry_id: str, *issues: str
) -> None:
    """Delete an entry's cutover issues, or only the ones named."""
    for issue in issues or (
        ISSUE_CUTOVER_INCOMPLETE,
        ISSUE_CUTOVER_BLOCKED,
        ISSUE_NATIVE_CONTROLS_UNAVAILABLE,
    ):
        ir.async_delete_issue(hass, DOMAIN, cutover_issue_id(issue, entry_id))


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
        if _may_ask_to_bind(hass, connection):
            # There is no entry to raise a Repairs issue against, so remember the
            # account instead. The administrator who adds this panel is then asked
            # to confirm it while they are still here, rather than afterwards.
            async_record_binding_request(hass, did, connection.user.id)
        if did in _removed_panels(hass) and not any(
            _panel_did(other) == did
            for other in hass.config_entries.async_loaded_entries(DOMAIN)
        ):
            connection.send_error(
                msg["id"], ERR_ENTRY_REMOVED, "The panel's entry was removed."
            )
            return
        connection.send_error(
            msg["id"], ERR_UNKNOWN_PANEL, "No loaded panel entry has this identity."
        )
        return
    from .identity import CONF_IDENTITY_PENDING, is_installation, legacy_peer_matches

    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    legacy_move = not is_installation(entry) and not legacy_peer_matches(
        entry, connection.remote
    )
    if (
        entry.data.get(CONF_IDENTITY_PENDING) is not None
        or (getattr(coordinator, "identity_mismatch", False) and not legacy_move)
        or (is_installation(entry) and high < 3)
        or (not is_installation(entry) and high >= 3)
    ):
        connection.send_error(
            msg["id"],
            ERR_UNKNOWN_PANEL,
            "The panel identity is not confirmed at this endpoint.",
        )
        return
    user_id = connection.user.id
    if entry.data.get(CONF_TRANSPORT_USER_ID) != user_id:
        if _may_ask_to_bind(hass, connection):
            # The Add-panel continuation and Repairs consume the same
            # authenticated, panel-originated request. Neither grants access.
            if entry.data.get(CONF_TRANSPORT_USER_ID) is None:
                async_record_binding_request(hass, did, user_id)
            async_raise_binding_issue(hass, entry, user_id)
        connection.send_error(
            msg["id"],
            ERR_PANEL_USER_MISMATCH,
            "An administrator has not confirmed this user for this panel.",
        )
        return
    pending = hass.data[DOMAIN].get("address_proofs", {})
    if entry.entry_id in pending:
        pending[entry.entry_id].conflicted = True
        connection.send_error(
            msg["id"], ERR_UNKNOWN_PANEL, "Competing panel address proofs."
        )
        return
    if legacy_move:
        _start_address_proof(hass, connection, msg, entry, high)
        return
    _accept_hello(hass, connection, msg, entry, high)


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

    authority = effective_authority(hass, entry)
    offered = frozenset(msg["capabilities"])
    capabilities = AUTHORITY_GRANTS[authority].intersection(offered)
    mqtt_withdraw_offered = CAPABILITY_MQTT_WITHDRAW in offered
    if mqtt_withdraw_offered:
        capabilities |= {CAPABILITY_MQTT_WITHDRAW}
    if CAPABILITY_EMBED_PROOF in offered:
        capabilities |= {CAPABILITY_EMBED_PROOF}
    if CAPABILITY_VOICE in offered:
        capabilities |= {CAPABILITY_VOICE}
    mqtt_discovery = mqtt_discovery_claim(hass, entry)
    if mqtt_discovery == MQTT_DISCOVERY_WITHDRAW:
        # Whatever held the withdrawal back, such as a customised entity a
        # person has since deleted, no longer does.
        async_delete_cutover_issues(hass, entry.entry_id, ISSUE_CUTOVER_BLOCKED)
    descriptors = {item["channel"]: item for item in msg["channels"]}
    unknown = frozenset(
        channel
        for channel, descriptor in descriptors.items()
        if catalogue_entry(descriptor) is None
    )
    if native_enabled_for(hass, entry):
        # A channel the hello also describes is served, whatever it claims.
        unsupported = frozenset(msg["unsupported"]).difference(descriptors)
        # Before the session opens: opening it is what tells a cutover waiting
        # for the panel's channels to run. The entry was found by this very
        # identity, so the entities removed are this panel's own.
        async_remove_unsupported_channels(hass, entry, did, unsupported)
        async_record_supported_channels(
            hass, entry, did, frozenset(descriptors).difference(unknown), unsupported
        )
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
        authority=authority,
        mqtt_discovery=mqtt_discovery,
        mqtt_withdraw_offered=mqtt_withdraw_offered,
    )
    result: dict[str, Any] = {
        "protocol": high,
        "session": session.token,
        "authority": authority,
        "mqtt_discovery": mqtt_discovery,
        "capabilities": sorted(capabilities),
        "integration": {"version": INTEGRATION_VERSION},
        "connection": {
            "instance_id": hass.data[DOMAIN][DATA_INSTANCE_ID],
            "user_id": user_id,
            "urls": async_connection_urls(hass),
        },
        # Unknown descriptors are accepted too, but render nothing.
        "channels": {"accepted": len(descriptors), "unknown": sorted(unknown)},
    }
    if CAPABILITY_EMBED_PROOF in capabilities:
        session.embed_key_id, session.embed_key = new_key()
        result["embed"] = {
            "key_id": session.embed_key_id,
            "key": encode_key(session.embed_key),
        }
    async_get_sessions(hass).open(session)
    connection.send_result(msg["id"], result)


@dataclass(slots=True)
class _AddressProof:
    """One bounded proof per entry; another requester invalidates both attempts."""

    conflicted: bool = False


@callback
def _start_address_proof(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
    entry: ConfigEntry,
    high: int,
) -> None:
    from .address import async_probe_addresses, session_candidates
    from .client import normalize_address
    from .identity import CONF_IDENTITY_PENDING, accept_health, is_installation

    did = msg["did"]
    stored = entry.data[CONF_ADDRESS]
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    proof = _AddressProof()
    pending = hass.data[DOMAIN].setdefault("address_proofs", {})

    def valid() -> bool:
        return (
            not proof.conflicted
            and _may_ask_to_bind(hass, connection)
            and connection.user.is_active
            and hass.config_entries.async_get_entry(entry.entry_id) is entry
            and entry in hass.config_entries.async_loaded_entries(DOMAIN)
            and entry.runtime_data is runtime
            and entry.data[CONF_ADDRESS] == stored
            and entry.data.get(CONF_TRANSPORT_USER_ID) == connection.user.id
            and entry.data.get(CONF_IDENTITY_PENDING) is None
            and not is_installation(entry)
            and [
                other.entry_id
                for other in hass.config_entries.async_entries(DOMAIN)
                if _panel_did(other) == did
            ]
            == [entry.entry_id]
            and async_get_sessions(hass).get(entry.entry_id) is None
        )

    def refuse() -> None:
        connection.send_error(
            msg["id"], ERR_UNKNOWN_PANEL, "The panel address could not be verified."
        )

    if not valid():
        refuse()
        return
    candidates = session_candidates(
        connection.remote, normalize_address(stored), tuple(msg.get("addresses", ()))
    )
    if not candidates:
        refuse()
        return
    pending[entry.entry_id] = proof

    async def verify() -> None:
        try:
            async with asyncio.timeout(10):
                previous, *answers = await async_probe_addresses(
                    hass, (normalize_address(stored), *candidates)
                )
                if not valid() or (
                    previous is not None and previous.discovery_id == did
                ):
                    refuse()
                    return
                for candidate, health in zip(candidates, answers, strict=True):
                    if not valid():
                        refuse()
                        return
                    if health is None or health.discovery_id != did:
                        continue
                    # Installation identity requires protocol 3 enrollment;
                    # a legacy address proof cannot establish that provenance.
                    if health.installation_identity or not accept_health(
                        hass, entry, health
                    ):
                        refuse()
                        return
                    # No await divides the final ownership check, address write,
                    # and session grant. The normal entry listener moves polling.
                    hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_ADDRESS: candidate.stored_value}
                    )
                    coordinator.identity_mismatch = False
                    _accept_hello(hass, connection, msg, entry, high)
                    return
                refuse()
        except TimeoutError:
            refuse()
        finally:
            if pending.get(entry.entry_id) is proof:
                pending.pop(entry.entry_id)
            if connection.subscriptions.get(msg["id"]) is cancel:
                connection.subscriptions.pop(msg["id"])

    @callback
    def cancel() -> None:
        proof.conflicted = True
        if pending.get(entry.entry_id) is proof:
            pending.pop(entry.entry_id)
        task.cancel()

    connection.subscriptions[msg["id"]] = cancel
    task = hass.async_create_task(
        verify(), f"{DOMAIN} verify moved panel address", eager_start=False
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
    accepted: set[str] = set()
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
        accepted.add(channel)
        session.observations[channel] = Observation(
            state=observation["state"],
            value=value,
            attributes=attributes,
            refresh=observation["refresh"],
            received_at=now,
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
    """Record a deliberate restart from the panel's current protocol-2 session."""
    sessions = async_get_sessions(hass)
    session = sessions.for_request(msg["session"], connection)
    if session is None:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    if session.protocol < 2:
        connection.send_error(
            msg["id"], ERR_INVALID_FORMAT, "Restart notice requires protocol 2."
        )
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
    elif late:
        # Too late for its service call; recorded above and nothing else.
        del session.late[command_id]
    else:
        del session.pending[command_id]
        if not command.future.done():
            command.future.set_result(CommandOutcome(msg["outcome"], msg.get("code")))
    connection.send_result(msg["id"])


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
        del session.late[next(iter(session.late))]


async def async_send_command(
    hass: HomeAssistant, session: PanelSession | None, channel: str, value: Any
) -> None:
    """Send one command to a panel and wait for its outcome.

    Only a live, fully synced session with the native authority and the
    commands capability carries commands. A command is sent once and never
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
    if session.authority != AUTHORITY_NATIVE:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_AUTHORITY_MISMATCH
        )
    if CAPABILITY_COMMANDS not in session.capabilities:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_NOT_COMMANDABLE
        )

    command_id = secrets.token_urlsafe(24)
    command = PendingCommand(channel=channel, future=hass.loop.create_future())
    session.pending[command_id] = command
    session.count("sent")
    session.connection.send_message(
        event_message(
            session.subscription_id,
            {
                "kind": "command",
                "command_id": command_id,
                "session": session.token,
                "channel": channel,
                "value": value,
                "deadline_ms": COMMAND_DEADLINE_MS,
            },
        )
    )
    future = command.future
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT):
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
        if not future.done():
            _stop_waiting(session, command_id)

    if result is None:
        session.count("session_ended")
        raise _command_error(ERR_PANEL_UNAVAILABLE)
    if result.outcome in OUTCOMES_WITH_CODE:
        raise _command_error(result.code)


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

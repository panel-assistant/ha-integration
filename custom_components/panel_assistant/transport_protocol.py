"""The native transport's wire vocabulary and the checks every message passes.

Commands, capabilities, outcomes, error codes, bounds and the schema of each
message a panel sends, with the validators that check an observation's value
against its channel's descriptor. Everything a panel sends is untrusted, so
these check types strictly and bound every size before anything is stored.
Pure: nothing here reads or writes Home Assistant state.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from ipaddress import ip_address
from typing import Any, Final

import voluptuous as vol
from homeassistant.util import dt as dt_util
from yarl import URL

from .client import is_valid_discovery_id, is_valid_panel_version
from .const import DOMAIN, MAX_ANDROID_INTEGER
from .contract import CONTRACT

COMMAND_HELLO: Final = f"{DOMAIN}/hello"
COMMAND_REPORT_STATE: Final = f"{DOMAIN}/report_state"
COMMAND_REPORT_EVENT: Final = f"{DOMAIN}/report_event"
COMMAND_COMMAND_RESULT: Final = f"{DOMAIN}/command_result"
COMMAND_RESTART_NOTICE: Final = f"{DOMAIN}/restart_notice"
COMMAND_UI_UPDATE: Final = f"{DOMAIN}/ui_update"

CAPABILITY_STATE: Final = "state"
CAPABILITY_EVENTS: Final = "events"
CAPABILITY_COMMANDS: Final = "commands"
CAPABILITY_APPROVAL: Final = "approval"
# A panel that offers this is an Assist satellite (see ``voice.py``).
CAPABILITY_VOICE: Final = "voice"
# A panel that offers this describes its media channel once granted: an older
# integration would refuse a whole hello naming the media_player platform. The
# channel's commands still need ``commands``.
CAPABILITY_MEDIA: Final = "media"
# A panel that offers this, with its Sendspin key, receives speech in step with
# other panels over Sendspin inside its session (see ``voice_stream.py``).
# Granted only when offered with a key and the Sendspin server runs.
CAPABILITY_VOICE_STREAM_SESSION: Final = "voice_stream_session"
# A panel that offers this answers ``manage`` requests on its session: its
# health and status snapshot, and the settings Panel Assistant writes. A panel
# without it is read and written over HTTP.
CAPABILITY_MANAGEMENT: Final = "management"
# A panel that offers this answers ``subscribe`` with ``ui_update`` requests,
# which Panel Assistant forwards to the sidebar that subscribed (see
# ``ui_relay.py``).
CAPABILITY_UI_SUBSCRIBE: Final = "ui_subscribe"
# A panel that offers this uploads its page bundle on request, so the sidebar
# shows it with no route to the panel's address (see ``ui_relay.py``).
CAPABILITY_UI_BUNDLE: Final = "ui_bundle"
# A panel that offers this may send a sensor with no reading as state
# ``unknown`` with a ``reason`` code; without the grant it sends ``unavailable``.
CAPABILITY_UNKNOWN_STATE: Final = "unknown_state"
# Never granted here: ``rest_retired`` lets a panel close its REST routes, and
# Panel Assistant still calls some of them.
KNOWN_CAPABILITIES: Final = frozenset(
    {
        CAPABILITY_STATE,
        CAPABILITY_EVENTS,
        CAPABILITY_COMMANDS,
        CAPABILITY_APPROVAL,
        CAPABILITY_VOICE,
        CAPABILITY_MEDIA,
        CAPABILITY_VOICE_STREAM_SESSION,
        CAPABILITY_MANAGEMENT,
        CAPABILITY_UI_SUBSCRIBE,
        CAPABILITY_UI_BUNDLE,
        CAPABILITY_UNKNOWN_STATE,
    }
)
# Session end reasons sent in a ``session_closed`` event.
REASON_SUPERSEDED: Final = "superseded"
REASON_ENTRY_UNLOADED: Final = "entry_unloaded"
REASON_USER_REMOVED: Final = "user_removed"
REASON_BINDING_CHANGED: Final = "binding_changed"
REASON_UPDATE_POLICY_CHANGED: Final = "update_policy_changed"

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
ERR_NOT_COMMANDABLE: Final = "not_commandable"
ERR_APPROVAL_PENDING: Final = "approval_pending"
COMMAND_ERRORS: Final = OUTCOME_CODES | {ERR_PANEL_UNAVAILABLE, ERR_APPROVAL_PENDING}

# How long a service call waits for a command's outcome, from sending it.
COMMAND_TIMEOUT: Final = 30.0
# How long the panel may hold a command before it answers that it expired.
COMMAND_DEADLINE_MS: Final = 10_000
# The longest deadline a command or manage event may carry (section 5); a panel
# refuses a longer one. A caller may still wait longer for the answer.
MAX_COMMAND_DEADLINE_MS: Final = 60_000
# How long a management read waits for its answer. Shorter than a command's
# wait, so a coordinator poll every 30 s never stacks on a slow panel.
MANAGE_TIMEOUT: Final = 10.0
MANAGE_SNAPSHOT: Final = "snapshot"
MANAGE_SETTINGS: Final = "settings"
# Asks the panel to read its Area from Home Assistant again; it carries no value.
MANAGE_HA_AREA_REFRESH: Final = "ha_area.refresh"
# Commands whose wait ended without a final outcome, remembered so a late one
# is still recorded; and outcomes kept for diagnostics.
MAX_LATE_COMMANDS: Final = 16
MAX_RECENT_OUTCOMES: Final = 16
MAX_PLACEHOLDERS: Final = 8
MAX_HEADERS: Final = 16
MAX_HEADER_LENGTH: Final = 1024

# Error codes returned to the panel. The panel renders its own text from them.
ERR_PROTOCOL_UNSUPPORTED: Final = "protocol_unsupported"
ERR_UNKNOWN_PANEL: Final = "unknown_panel"
ERR_PANEL_USER_MISMATCH: Final = "panel_user_mismatch"
ERR_PANEL_IDENTITY_UNAVAILABLE: Final = "panel_identity_unavailable"
# No loaded entry has the panel's identity, and an entry that had it was
# removed: the panel waits to be added again.
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
MAX_FAMILY_INDEX: Final[int] = CONTRACT["max_family_index"]
MAX_STRING_LENGTH: Final = 255
MAX_UNIT_LENGTH: Final = 16
MAX_URL_LENGTH: Final = 2048
MAX_EVENT_ID: Final = 2**63 - 1
MAX_JSON_INTEGER: Final = 2**63

# Older panels may still describe the removed image entity. Admit its hello
# shape as unknown so it cannot prevent the panel's remaining channels working.
PLATFORMS: Final = frozenset(entry["platform"] for entry in CONTRACT["channels"]) | {
    "image"
}
SYNC_FULL_BEGIN: Final = "full_begin"
SYNC_DELTA: Final = "delta"
SYNC_FULL_END: Final = "full_end"
STATE_KNOWN: Final = "known"
STATE_UNAVAILABLE: Final = "unavailable"
# A sensor the panel serves but has no reading for, with a ``reason`` code.
STATE_UNKNOWN: Final = "unknown"

_CHANNEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_UNIQUE_SUFFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# An X25519 public key, base64url without padding.
_SENDSPIN_KEY = re.compile(r"^[A-Za-z0-9_-]{43}$")
_HEADER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


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
        # The panel's Sendspin public key, offered with ``voice_stream_session``.
        vol.Optional("voice_stream_session"): vol.Schema(
            {vol.Required("client_id"): _pattern(_SENDSPIN_KEY)},
            extra=vol.REMOVE_EXTRA,
        ),
    },
    extra=vol.REMOVE_EXTRA,
)


def _observation_consistent(observation: dict[str, Any]) -> dict[str, Any]:
    if observation["state"] == STATE_KNOWN and "value" not in observation:
        raise vol.Invalid("a known observation needs a value")
    if (observation["state"] == STATE_UNKNOWN) != ("reason" in observation):
        raise vol.Invalid("an unknown observation, and only one, carries a reason")
    return observation


OBSERVATION_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("channel"): _channel,
            vol.Required("state"): vol.In(
                (STATE_KNOWN, STATE_UNAVAILABLE, STATE_UNKNOWN)
            ),
            vol.Optional("reason"): _code,
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


UI_UPDATE_SCHEMA: Final = vol.Schema(
    {
        vol.Required("type"): COMMAND_UI_UPDATE,
        vol.Required("session"): _session_token,
        vol.Required("subscription"): _session_token,
        vol.Required("observations"): _bounded_list(
            MAX_OBSERVATIONS,
            vol.All(
                vol.Schema(
                    {
                        vol.Required("channel"): _channel,
                        vol.Required("state"): vol.In((STATE_KNOWN, STATE_UNAVAILABLE)),
                        vol.Optional("value"): object,
                    },
                    extra=vol.REMOVE_EXTRA,
                ),
                _observation_consistent,
            ),
        ),
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


def _headers(value: Any) -> dict[str, str]:
    if type(value) is not dict or len(value) > MAX_HEADERS:
        raise vol.Invalid("expected bounded headers")
    return {
        _pattern(_HEADER_NAME_PATTERN)(key): _plain_string(MAX_HEADER_LENGTH)(item)
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
            # What a ``manage`` request answered with: the JSON body its route
            # returns, or a page's text.
            vol.Optional("result"): object,
            # The answering route's own status and response headers, and an
            # interim answer that a final one for the same command follows.
            vol.Optional("status"): _strict_int(100, 599),
            vol.Optional("headers"): _headers,
            vol.Optional("interim"): _strict_bool,
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


MEDIA_STATES: Final = ("idle", "playing", "paused", "buffering")


def _validate_media(value: Any, _descriptor: Mapping[str, Any]) -> dict[str, Any]:
    media = _mapping(value)
    state, muted = media.get("state"), media.get("muted")
    if type(state) is not str or state not in MEDIA_STATES or type(muted) is not bool:
        raise _reject()
    return {"state": state, "muted": muted}


def _not_reported(_value: Any, _descriptor: Mapping[str, Any]) -> Any:
    raise _reject()


_VALUE_VALIDATORS: Final[dict[str, Callable[[Any, Mapping[str, Any]], Any]]] = {
    "binary_sensor": _validate_boolean,
    "button": _not_reported,
    "camera": _validate_boolean,
    "event": _not_reported,
    "light": _validate_light,
    "media_player": _validate_media,
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

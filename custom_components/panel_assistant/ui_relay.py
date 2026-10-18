"""The sidebar's relay: a panel's own interface, carried over its session.

The sidebar frames a panel's pages without Home Assistant ever opening a
connection to the panel. The frame's page sends each request to the sidebar,
which calls ``panel_assistant/ui``; Panel Assistant sends it to the panel as a
``manage`` or ``command`` event on the panel's session, naming the
administrator and the browser connection it came from, and answers with the
panel's reply. A subscription's updates arrive as ``panel_assistant/ui_update``
and go to the one browser subscription that asked for them.

Bytes too large for a frame move as blobs: the panel uploads an answer's bytes
to Panel Assistant, which hands the browser a one-use ``blob/<id>`` under the
embed view, and a browser's upload is handed to the panel the same way in the
other direction.

The page bundle (the panel's scripts, styles and icons) is fetched from the
panel once per build and kept in memory while a loaded panel reports that
build, so two panels on two builds are each shown their own.
"""

from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import re
import secrets
import time
import zipfile
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, Final

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .blob import BLOB_PATH, async_get_blobs
from .const import DOMAIN
from .transport_protocol import (
    CAPABILITY_UI_BUNDLE,
    CAPABILITY_UI_SUBSCRIBE,
    ERR_INVALID_VALUE,
    ERR_PANEL_UNAVAILABLE,
    ERR_SESSION_UNKNOWN,
    OUTCOME_APPLIED,
    OUTCOME_FAILED,
    OUTCOME_REFUSED,
    UI_UPDATE_SCHEMA,
    _strict_int,
)
from .transport_sessions import (
    CommandOutcome,
    PanelSession,
    async_deliver,
    async_get_sessions,
)
from .wake_words import OP_RELAYED_IMPORT, async_get_shared_wake_words

_LOGGER = logging.getLogger(__name__)

DATA_BUNDLES: Final = "ui_bundles"
ERR_NOT_FOUND: Final = "not_found"
ERR_NOT_LOADED: Final = "not_loaded"

OP_SUBSCRIBE: Final = "subscribe"
OP_UNSUBSCRIBE: Final = "unsubscribe"
OP_BLOB_UPLOAD: Final = "blob.upload"
OP_BUNDLE: Final = "bundle"

DEFAULT_DEADLINE_MS: Final = 10_000
MAX_DEADLINE_MS: Final = 120_000
# How long past its deadline an answer may take to arrive.
ANSWER_GRACE_SECONDS: Final = 5.0
# A bundle fetch that failed is not asked again for this long, so a frame's many
# file requests cannot each start a fresh upload from a panel that is too slow.
BUNDLE_RETRY_SECONDS: Final = 30.0
# The panel's approval window: a relayed request someone is approving on the
# panel's screen is waited for this long after the panel says so.
APPROVAL_SECONDS: Final = 600.0
# The most a browser may upload for a panel: the panel's own app upload limit.
MAX_BROWSER_UPLOAD: Final = 256 * 1024 * 1024
# A bundle's zip, and everything inside it once expanded.
MAX_BUNDLE_BYTES: Final = 64 * 1024 * 1024
MAX_BUNDLE_FILES: Final = 4096

_OP = re.compile(r"[a-z][a-z0-9_.]{0,63}")
_COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_CHANNEL = re.compile(r"[a-z][a-z0-9_]{0,47}")
# A blob the browser holds: the base-relative path the embed view serves.
BROWSER_BLOB: Final = re.compile(r"blob/([A-Za-z0-9_-]{43})")
_MEDIA_TYPE = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}"
)
_CONTENT_TYPES: Final = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}
_CONNECTION_KEY: Final = (DOMAIN, "ui")


def _matching(pattern: re.Pattern[str]) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if type(value) is not str or pattern.fullmatch(value) is None:
            raise vol.Invalid("invalid format")
        return value

    return validate


def _request_consistent(request: dict[str, Any]) -> dict[str, Any]:
    if ("op" in request) != (request["kind"] == "manage") or ("channel" in request) != (
        request["kind"] == "command"
    ):
        raise vol.Invalid("a manage request names an op, a command a channel")
    return request


# A page never sets its origin: anything it sent is dropped here and replaced.
REQUEST_SCHEMA: Final = vol.All(
    vol.Schema(
        {
            vol.Required("kind"): vol.In(("manage", "command")),
            vol.Required("command_id"): _matching(_COMMAND_ID),
            vol.Optional("op"): _matching(_OP),
            vol.Optional("args", default=dict): dict,
            vol.Optional("channel"): _matching(_CHANNEL),
            vol.Optional("value"): object,
            vol.Optional("deadline_ms", default=DEFAULT_DEADLINE_MS): _strict_int(
                1, MAX_DEADLINE_MS
            ),
        },
        extra=vol.REMOVE_EXTRA,
    ),
    _request_consistent,
)


def media_type(value: Any) -> str:
    """Return a content type safe to send as a header, else bytes.

    Parameters such as a charset are dropped: the type before them is kept.
    """
    kind = value.split(";", 1)[0].strip().lower() if isinstance(value, str) else ""
    return kind if _MEDIA_TYPE.fullmatch(kind) else "application/octet-stream"


def content_type(path: str) -> str:
    """Return the content type a bundle file is served with."""
    suffix = PurePosixPath(path).suffix.lower()
    return (
        _CONTENT_TYPES.get(suffix)
        or mimetypes.guess_type(path)[0]
        or "application/octet-stream"
    )


# ---------------------------------------------------------------------------
# One request on the panel's session.


def live_ui_session(hass: HomeAssistant, entry_id: str) -> PanelSession | None:
    """Return the entry's live session when it can serve the sidebar."""
    session = async_get_sessions(hass).get(entry_id)
    if session is None or CAPABILITY_UI_BUNDLE not in session.capabilities:
        return None
    return session


async def async_manage(
    hass: HomeAssistant,
    session: PanelSession,
    op: str,
    args: dict[str, Any],
    origin: dict[str, str] | None,
    deadline_ms: int = DEFAULT_DEADLINE_MS,
    command_id: str | None = None,
    lent: tuple[str, ...] = (),
) -> tuple[CommandOutcome, bytes | None]:
    """Ask one ``manage`` request with a fresh upload slot, and return its answer.

    An answer the panel moved as a blob comes back with its bytes. The slot,
    and any download ``lent`` to the panel for the request, are discarded once
    the panel has answered or never can: a wait that ends first leaves them for
    a panel still moving the bytes. A relayed request (one with an origin) is
    waited for through the panel's approval window.
    """
    blobs = async_get_blobs(hass)
    if async_get_sessions(hass).get(session.entry_id) is not session:
        for held in lent:
            blobs.discard(held)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key=ERR_PANEL_UNAVAILABLE
        )
    token, received = blobs.upload(session.entry_id)
    if command_id is None:
        command_id = secrets.token_urlsafe(24)

    def release() -> None:
        for held in (token, *lent):
            blobs.discard(held)

    fields: dict[str, Any] = {"op": op, "args": {**args, "blob_token": token}}
    if origin is not None:
        fields["origin"] = origin
    try:
        outcome = await async_deliver(
            hass,
            session,
            f"manage:{op}",
            fields,
            deadline_ms / 1000 + ANSWER_GRACE_SECONDS,
            kind="manage",
            command_id=command_id,
            deadline_ms=deadline_ms,
            approval_seconds=APPROVAL_SECONDS if origin is not None else 0,
            on_settled=release,
        )
        result = outcome.result
        reference = result.get("blob") if isinstance(result, dict) else None
        if not isinstance(reference, dict):
            return outcome, None
        url = reference.get("url")
        if (
            not isinstance(url, str)
            or not url.endswith(BLOB_PATH + token)
            or not received.done()
            or received.cancelled()
            or received.exception() is not None
        ):
            return CommandOutcome(OUTCOME_FAILED, OUTCOME_FAILED), None
        return outcome, received.result()
    finally:
        # A command still kept for its late answer releases them then.
        if command_id not in session.late:
            release()


def _frame(command_id: str, outcome: CommandOutcome) -> dict[str, Any]:
    """Return a panel's answer as the page reads it, under the page's own ID."""
    frame: dict[str, Any] = {"command_id": command_id, "outcome": outcome.outcome}
    for key, value in (
        ("code", outcome.code),
        ("result", outcome.result),
        ("status", outcome.status),
        ("headers", outcome.headers),
    ):
        if value is not None:
            frame[key] = value
    return frame


def _refused(command_id: str, **result: Any) -> dict[str, Any]:
    return {
        "command_id": command_id,
        "outcome": OUTCOME_REFUSED,
        "code": ERR_INVALID_VALUE,
        **({"result": result} if result else {}),
    }


# ---------------------------------------------------------------------------
# The browser side: ``panel_assistant/ui``.


class _ConnectionRelay:
    """One browser connection's relay ID, and its requests still in flight.

    Kept among the connection's subscriptions, which Core calls when the
    connection closes: a request the browser can no longer receive stops
    waiting then.
    """

    def __init__(self) -> None:
        self.id = secrets.token_urlsafe(16)
        self.tasks: set[asyncio.Task[Any]] = set()

    def __call__(self) -> None:
        for task in self.tasks:
            task.cancel()


def _connection_relay(connection: ActiveConnection) -> _ConnectionRelay:
    relay = connection.subscriptions.get(_CONNECTION_KEY)
    if not isinstance(relay, _ConnectionRelay):
        relay = connection.subscriptions[_CONNECTION_KEY] = _ConnectionRelay()
    return relay


class _Subscription:
    """One browser subscription relayed to one panel subscription."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ActiveConnection,
        msg_id: int,
        browser_id: str,
        session: PanelSession,
        origin: dict[str, str],
        panel_id: str,
    ) -> None:
        self.hass = hass
        self.connection = connection
        self.msg_id = msg_id
        self.browser_id = browser_id
        self.session = session
        self.origin = origin
        self.panel_id = panel_id
        self.applied = False
        self.ended = False
        # Updates that arrived before the answer was forwarded, in order.
        self.held: list[dict[str, Any]] | None = []
        session.ui_relays.setdefault(panel_id, []).append(self)

    def _send(self, event: dict[str, Any]) -> None:
        self.connection.send_message(event_message(self.msg_id, event))

    @callback
    def answered(self, frame: dict[str, Any]) -> None:
        """Forward the subscribe's answer as the first event, then any held."""
        self.applied = frame["outcome"] == OUTCOME_APPLIED
        result = frame.get("result")
        panel_id = result.get("subscription") if isinstance(result, dict) else None
        if self.applied and isinstance(panel_id, str) and panel_id != self.panel_id:
            # The panel shares one subscription per origin and family.
            self._leave()
            self.panel_id = panel_id
            self.session.ui_relays.setdefault(panel_id, []).append(self)
        if self.applied:
            frame = {**frame, "result": {"subscription": self.browser_id}}
        if self.ended:
            self._unsubscribe()
            return
        self._send(frame)
        held, self.held = self.held or [], None
        for update in held:
            self._send(update)
        if not self.applied:
            self.ended = True
            self._leave()
            self.connection.subscriptions.pop(self.msg_id, None)

    @callback
    def update(self, observations: list[dict[str, Any]]) -> None:
        """Forward one ``ui_update`` to this browser subscription only."""
        if self.ended:
            return
        event = {"subscription": self.browser_id, "observations": observations}
        if self.held is not None:
            self.held.append(event)
        else:
            self._send(event)

    @callback
    def session_ended(self) -> None:
        """Tell the browser its subscription ended with the panel's session."""
        if self.ended:
            return
        self.ended = True
        self.connection.subscriptions.pop(self.msg_id, None)
        self._send({"closed": True})

    @callback
    def browser_ended(self) -> None:
        """Unsubscribe from the panel once the browser stops listening."""
        if self.ended:
            return
        self.ended = True
        if self.held is None:
            self._unsubscribe()

    def _leave(self) -> bool:
        """Leave the panel subscription; return whether no relay is left on it."""
        group = self.session.ui_relays.get(self.panel_id, [])
        if self in group:
            group.remove(self)
        if group:
            return False
        self.session.ui_relays.pop(self.panel_id, None)
        return True

    def _unsubscribe(self) -> None:
        if not self._leave() or not self.applied:
            return
        session = self.session
        if async_get_sessions(self.hass).get(session.entry_id) is not session:
            return

        async def unsubscribe() -> None:
            try:
                await async_manage(
                    self.hass,
                    session,
                    OP_UNSUBSCRIBE,
                    {"subscription": self.panel_id},
                    self.origin,
                )
            except HomeAssistantError:
                _LOGGER.debug("A relayed subscription ended without its answer")

        self.hass.async_create_background_task(
            unsubscribe(), f"{DOMAIN} relayed unsubscribe"
        )


def _blob_reference(
    hass: HomeAssistant, entry_id: str, user_id: str, data: bytes, reference: Any
) -> dict[str, Any]:
    """Hand the browser a panel's blob, once, under the embed view."""
    reference = reference if isinstance(reference, dict) else {}
    kind = media_type(reference.get("type"))
    token, minted = async_get_blobs(hass).download(entry_id, data, kind, user_id)
    return {**reference, **minted, "url": f"blob/{token}"}


async def _async_relay(
    hass: HomeAssistant,
    session: PanelSession,
    request: dict[str, Any],
    origin: dict[str, str],
) -> dict[str, Any]:
    """Send one page request to the panel and return its answer as a frame."""
    command_id = request["command_id"]
    deadline_ms = request["deadline_ms"]
    user_id = origin["user"]
    entry_id = session.entry_id
    blobs = async_get_blobs(hass)
    if request["kind"] == "command":
        outcome = await async_deliver(
            hass,
            session,
            request["channel"],
            {
                "channel": request["channel"],
                "value": request.get("value"),
                "origin": origin,
            },
            deadline_ms / 1000 + ANSWER_GRACE_SECONDS,
            deadline_ms=deadline_ms,
            approval_seconds=APPROVAL_SECONDS,
        )
        return _frame(command_id, outcome)

    args = dict(request["args"])
    sent: str | None = None
    if "blob" in args:
        # Only an upload this browser made for this panel is handed on.
        reference = args["blob"]
        url = reference.get("url") if isinstance(reference, dict) else None
        match = BROWSER_BLOB.fullmatch(url) if isinstance(url, str) else None
        data = blobs.take(match[1], entry_id, user_id) if match else None
        if data is None:
            return _refused(command_id)
        assert isinstance(reference, dict)
        sent, minted = blobs.download(entry_id, data, media_type(reference.get("type")))
        args["blob"] = {**reference, "url": minted["url"]}
    outcome, data = await async_manage(
        hass,
        session,
        request["op"],
        args,
        origin,
        deadline_ms,
        lent=() if sent is None else (sent,),
    )
    if (
        request["op"] == OP_RELAYED_IMPORT
        and outcome.outcome == OUTCOME_APPLIED
        and "model" in args
        and "download" not in args
    ):
        # A wake word imported here goes to every panel (wake_words.py).
        hass.async_create_background_task(
            async_get_shared_wake_words(hass).async_record(
                entry_id, args, outcome.result
            ),
            f"{DOMAIN} share a wake word",
        )
    frame = _frame(command_id, outcome)
    if data is not None:
        assert isinstance(outcome.result, dict)
        frame["result"] = {
            **outcome.result,
            "blob": _blob_reference(
                hass, entry_id, user_id, data, outcome.result["blob"]
            ),
        }
    return frame


def _blob_upload(
    hass: HomeAssistant, entry_id: str, user_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    """Answer ``blob.upload`` here: where the browser puts the bytes it will name."""
    size = request["args"].get("size")
    if size is not None and (type(size) is not int or size < 0):
        return _refused(request["command_id"])
    limit = MAX_BROWSER_UPLOAD if size is None else size
    if limit > MAX_BROWSER_UPLOAD:
        return _refused(
            request["command_id"], error="too-large", max_size=MAX_BROWSER_UPLOAD
        )
    token, _ = async_get_blobs(hass).upload(entry_id, limit, user_id)
    return {
        "command_id": request["command_id"],
        "outcome": OUTCOME_APPLIED,
        "result": {"url": f"blob/{token}", "max_size": limit},
    }


async def _async_subscribe(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg_id: int,
    session: PanelSession,
    request: dict[str, Any],
    origin: dict[str, str],
) -> None:
    """Relay ``subscribe``; the answer arrives as the subscription's first event."""
    command_id = secrets.token_urlsafe(24)
    subscription = _Subscription(
        hass,
        connection,
        msg_id,
        request["command_id"],
        session,
        origin,
        command_id,
    )
    connection.subscriptions[msg_id] = subscription.browser_ended
    connection.send_result(msg_id)
    try:
        outcome, _ = await async_manage(
            hass,
            session,
            OP_SUBSCRIBE,
            request["args"],
            origin,
            request["deadline_ms"],
            command_id,
        )
    except HomeAssistantError:
        outcome = CommandOutcome(OUTCOME_FAILED, OUTCOME_FAILED)
    subscription.answered(_frame(request["command_id"], outcome))


@require_admin
@websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/ui",
        vol.Required("entry_id"): str,
        vol.Required("request"): REQUEST_SCHEMA,
    }
)
@async_response
async def ws_ui(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Relay one request from a sidebar's frame to its panel."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such panel.")
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], ERR_NOT_LOADED, "The panel is not loaded.")
        return
    request: dict[str, Any] = msg["request"]
    session = live_ui_session(hass, entry.entry_id)
    if session is None or (
        request.get("op") == OP_SUBSCRIBE
        and CAPABILITY_UI_SUBSCRIBE not in session.capabilities
    ):
        connection.send_error(
            msg["id"], ERR_PANEL_UNAVAILABLE, "The panel cannot be reached now."
        )
        return
    relay = _connection_relay(connection)
    origin = {"user": connection.user.id, "connection": relay.id}
    if request.get("op") == OP_BLOB_UPLOAD:
        connection.send_result(
            msg["id"], _blob_upload(hass, entry.entry_id, origin["user"], request)
        )
        return
    if request.get("op") == OP_SUBSCRIBE:
        await _async_subscribe(hass, connection, msg["id"], session, request, origin)
        return
    task = asyncio.current_task()
    assert task is not None
    relay.tasks.add(task)
    try:
        frame = await _async_relay(hass, session, request, origin)
    except HomeAssistantError:
        connection.send_error(
            msg["id"], ERR_PANEL_UNAVAILABLE, "The panel did not answer."
        )
        return
    finally:
        relay.tasks.discard(task)
    connection.send_result(msg["id"], frame)


# ---------------------------------------------------------------------------
# The panel side: ``panel_assistant/ui_update``.


@callback
@websocket_command(vol.All(UI_UPDATE_SCHEMA))
def ws_ui_update(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Forward one subscription's update to the browser that subscribed."""
    session = async_get_sessions(hass).for_request(msg["session"], connection)
    if session is None or CAPABILITY_UI_SUBSCRIBE not in session.capabilities:
        connection.send_error(msg["id"], ERR_SESSION_UNKNOWN, "No such session.")
        return
    for relay in list(session.ui_relays.get(msg["subscription"], ())):
        relay.update(msg["observations"])
    connection.send_result(msg["id"])


# ---------------------------------------------------------------------------
# Page bundles, one per build.


def reported_build(entry: ConfigEntry) -> str | None:
    """Return the build token the entry's panel last reported."""
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    health = getattr(getattr(coordinator, "data", None), "health", None)
    build = getattr(health, "build", None)
    return build if isinstance(build, str) and build else None


def _unzip(data: bytes) -> dict[str, bytes] | None:
    """Return a bundle's files by relative path, or nothing for a bad bundle."""
    files: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) > MAX_BUNDLE_FILES:
                return None
            for info in members:
                parts = PurePosixPath(info.filename).parts
                total += info.file_size
                if (
                    not parts
                    or info.filename.startswith("/")
                    or "\\" in info.filename
                    or any(part in ("", ".", "..") for part in parts)
                    or total > MAX_BUNDLE_BYTES
                ):
                    return None
                files["/".join(parts)] = archive.read(info)
    except zipfile.BadZipFile, ValueError, OSError:
        return None
    return files


class BundleStore:
    """The page bundles of the builds loaded panels report, in memory."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._bundles: dict[str, dict[str, bytes]] = {}
        self._fetching: dict[str, asyncio.Task[dict[str, bytes] | None]] = {}
        # A panel that answered another build than its entry reports (it was
        # updated since the last poll), by entry: (reported, answered).
        self._answered: dict[str, tuple[str, str]] = {}
        # When a fetch of a build last failed (monotonic seconds).
        self._failed: dict[str, float] = {}

    def _build(self, entry: ConfigEntry) -> str | None:
        """Return the build whose bundle serves the entry's pages now."""
        reported = reported_build(entry)
        answered = self._answered.get(entry.entry_id)
        if answered is None:
            return reported
        if answered[0] == reported:
            return answered[1]
        # The entry has caught up, or moved on: what it reports is current.
        del self._answered[entry.entry_id]
        return reported

    def _prune(self) -> None:
        kept = {
            build
            for entry in self._hass.config_entries.async_loaded_entries(DOMAIN)
            if (build := self._build(entry)) is not None
        }
        for build in set(self._bundles) - kept:
            del self._bundles[build]

    async def async_file(
        self, entry: ConfigEntry, session: PanelSession, path: str
    ) -> bytes | None:
        """Return one file of the entry's build, fetching its bundle once."""
        reported = reported_build(entry)
        build = self._build(entry)
        if reported is None or build is None:
            return None
        self._prune()
        files = self._bundles.get(build)
        if files is None:
            fetch = self._fetching.get(reported)
            failed = self._failed.get(reported)
            if (
                fetch is None
                and failed is not None
                and time.monotonic() - failed < BUNDLE_RETRY_SECONDS
            ):
                return None
            if fetch is None:
                fetch = self._fetching[reported] = self._hass.async_create_task(
                    self._fetch(entry.entry_id, session, reported),
                    f"{DOMAIN} fetch page bundle",
                )
                fetch.add_done_callback(lambda _: self._fetching.pop(reported, None))
            files = await asyncio.shield(fetch)
        return None if files is None else files.get(path)

    async def _fetch(
        self, entry_id: str, session: PanelSession, build: str
    ) -> dict[str, bytes] | None:
        files = await self._fetch_once(entry_id, session, build)
        if files is None:
            self._failed[build] = time.monotonic()
        else:
            self._failed.pop(build, None)
        return files

    async def _fetch_once(
        self, entry_id: str, session: PanelSession, build: str
    ) -> dict[str, bytes] | None:
        try:
            # A panel on slow Wi-Fi needs time to upload the bundle once.
            outcome, data = await async_manage(
                self._hass,
                session,
                OP_BUNDLE,
                {"build": build},
                None,
                deadline_ms=MAX_DEADLINE_MS,
            )
        except HomeAssistantError:
            return None
        answered = (
            outcome.result.get("build") if isinstance(outcome.result, dict) else None
        )
        if (
            outcome.outcome != OUTCOME_APPLIED
            or data is None
            or not isinstance(answered, str)
        ):
            return None
        files = await self._hass.async_add_executor_job(_unzip, data)
        if files is not None:
            self._bundles[answered] = files
            if answered != build:
                self._answered[entry_id] = (build, answered)
            self._prune()
        return files


@callback
def async_get_bundles(hass: HomeAssistant) -> BundleStore:
    """Return the domain's bundle store."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    store: BundleStore = domain_data.setdefault(DATA_BUNDLES, BundleStore(hass))
    return store


@callback
def async_setup_ui_relay(hass: HomeAssistant) -> None:
    """Register the relay's commands once for the domain."""
    websocket_api.async_register_command(hass, ws_ui)
    websocket_api.async_register_command(hass, ws_ui_update)

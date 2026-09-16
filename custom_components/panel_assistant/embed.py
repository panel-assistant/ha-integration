"""The sidebar's embedded panel interface: sessions and the proxy.

An administrator's sidebar opens an embed session over its WebSocket. The
session's token names a path under which Home Assistant proxies the panel's own
web interface, so the browser never needs to reach the panel and the panel never
sees the browser's credential. The integration gives the panel no credential of
its own either: a panel signs in to Home Assistant through its own setup, and
everything before that runs over its REST API.

Every proxied request is checked again, and a request that fails the check is
answered 404 rather than 401: Core records each 401 as a failed login and
notifies about it, which an expired frame would do for every one of its
subresources.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import aiohttp
import voluptuous as vol
from aiohttp import hdrs, web
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    require_admin,
    websocket_command,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView
from yarl import URL

from . import embed_proof
from .client import PanelAddress, normalize_address
from .const import DOMAIN
from .transport import PanelSession, async_get_sessions

_LOGGER = logging.getLogger(__name__)

DATA_EMBED: Final = "embed"
EMBED_PREFIX: Final = "/api/panel_assistant/embed"

REASON_ENTRY_UNLOADED: Final = "entry_unloaded"
REASON_USER_REMOVED: Final = "user_removed"

STATE_REACHABLE: Final = "reachable"
STATE_UNREACHABLE: Final = "unreachable"
STATE_NOT_LOADED: Final = "not_loaded"

ERR_NOT_FOUND: Final = "not_found"
ERR_NOT_LOADED: Final = "not_loaded"

MAX_PANELS: Final = 200
RESUME_SECONDS: Final = 60
RECHECK_SECONDS: Final = 30
MAX_CONCURRENT: Final = 16
MAX_LONG_LIVED: Final = 2
MAX_REQUEST_BODY: Final = 256 * 1024 * 1024
MAX_REWRITTEN_BODY: Final = 2 * 1024 * 1024
BASE_WINDOW: Final = 4096
BASE_ELEMENT: Final = b'<base href="/">'
# Streams of logs and held captures run as long as they are open; only reaching
# the panel is bounded.
PROXY_TIMEOUT: Final = aiohttp.ClientTimeout(total=None, sock_connect=10)

EMBED_HEADER: Final = "X-Panel-Assistant-Embed"
# The API page is a developer page whose calls would escape the proxy.
ALWAYS_HIDDEN: Final = ("api",)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_LANGUAGE = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*")
_MAX_LANGUAGE = 35

REQUEST_HEADERS: Final = (
    hdrs.ACCEPT,
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.IF_NONE_MATCH,
    hdrs.IF_MODIFIED_SINCE,
    hdrs.LAST_EVENT_ID,
    hdrs.USER_AGENT,
)
RESPONSE_HEADERS: Final = (
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_DISPOSITION,
    hdrs.CONTENT_LANGUAGE,
    hdrs.CACHE_CONTROL,
    hdrs.ETAG,
    hdrs.LAST_MODIFIED,
    hdrs.VARY,
    hdrs.RETRY_AFTER,
)
# Core's header middleware stamps these only after the handler returns, which is
# too late for a response that has already started streaming.
SECURITY_HEADERS: Final = {
    "Content-Security-Policy": "frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}
# Aiohttp adds these unasked; only the allowlist may reach the panel.
_SKIP_AUTO_HEADERS: Final = frozenset(
    {hdrs.ACCEPT, hdrs.ACCEPT_ENCODING, hdrs.CONTENT_TYPE, hdrs.USER_AGENT}
)


@dataclass(slots=True, eq=False)
class EmbedSession:
    """One administrator's view of one panel, named by an unguessable token."""

    token: str
    entry_id: str
    user_id: str
    refresh_token_id: str
    language: str | None
    theme: str
    hidden_tabs: tuple[str, ...]
    created: float
    connection: ActiveConnection | None = None
    subscription_id: int | None = None
    # When the subscription ended without the session ending: a reconnecting
    # sidebar may resume the token for a short while, so its frame stays loaded.
    detached_at: float | None = None
    active: int = 0
    long_lived: int = 0
    upstream: set[aiohttp.ClientResponse] = field(default_factory=set)

    @property
    def url(self) -> str:
        """Return the path the sidebar frames."""
        return f"{EMBED_PREFIX}/{self.token}/"

    @property
    def embed_header(self) -> str:
        """Return the panel's embedded-mode switch for this session."""
        parts = ["v=1"]
        if self.language is not None:
            parts.append(f"lang={self.language}")
        parts.append(f"theme={self.theme}")
        parts.append("hide=" + ",".join(self.hidden_tabs))
        return ";".join(parts)


def _valid_language(value: str) -> str | None:
    """Return a language tag the panel's grammar accepts, else nothing."""
    if len(value) <= _MAX_LANGUAGE and _LANGUAGE.fullmatch(value):
        return value
    return None


class EmbedSessions:
    """Every embed session, by token. Held in memory only, never persisted."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty table."""
        self._hass = hass
        self._by_token: dict[str, EmbedSession] = {}

    @callback
    def open(
        self,
        connection: ActiveConnection,
        subscription_id: int,
        entry_id: str,
        language: str,
        theme: str,
        resume: str | None,
    ) -> EmbedSession:
        """Open a session, or resume a detached one from the same login."""
        assert connection.user is not None
        assert connection.refresh_token_id is not None
        self._expire()
        session = self._by_token.get(resume) if resume is not None else None
        if (
            session is None
            or session.detached_at is None
            or session.entry_id != entry_id
            or session.refresh_token_id != connection.refresh_token_id
        ):
            session = EmbedSession(
                token=secrets.token_urlsafe(32),
                entry_id=entry_id,
                user_id=connection.user.id,
                refresh_token_id=connection.refresh_token_id,
                language=None,
                theme=theme,
                hidden_tabs=ALWAYS_HIDDEN,
                created=time.monotonic(),
            )
            self._by_token[session.token] = session
        session.language = _valid_language(language)
        session.theme = theme
        session.connection = connection
        session.subscription_id = subscription_id
        session.detached_at = None
        connection.subscriptions[subscription_id] = self._detach_callback(session)
        return session

    def _detach_callback(self, session: EmbedSession) -> Callable[[], None]:
        subscription_id = session.subscription_id

        @callback
        def _detach() -> None:
            # Core runs this when the sidebar unsubscribes or its connection
            # closes. A resumed session has moved to another subscription.
            if (
                self._by_token.get(session.token) is not session
                or session.subscription_id != subscription_id
            ):
                return
            session.connection = None
            session.subscription_id = None
            session.detached_at = time.monotonic()

        return _detach

    @callback
    def end(self, session: EmbedSession, reason: str) -> None:
        """End a session for good, and tell a sidebar still subscribed why."""
        if self._by_token.pop(session.token, None) is not session:
            return
        for response in list(session.upstream):
            response.close()
        connection = session.connection
        if connection is not None and session.subscription_id is not None:
            connection.subscriptions.pop(session.subscription_id, None)
            connection.send_message(
                event_message(
                    session.subscription_id, {"kind": "closed", "reason": reason}
                )
            )
        session.connection = None
        session.subscription_id = None

    @callback
    def end_entry(self, entry_id: str, reason: str) -> None:
        """End every session of one entry."""
        for session in [s for s in self._by_token.values() if s.entry_id == entry_id]:
            self.end(session, reason)

    @callback
    def end_user(self, user_id: str, reason: str) -> None:
        """End every session of one user."""
        for session in [s for s in self._by_token.values() if s.user_id == user_id]:
            self.end(session, reason)

    @callback
    def admit(self, token: str) -> tuple[EmbedSession, ConfigEntry] | None:
        """Return the session and entry a request may reach, checked now."""
        self._expire()
        session = self._by_token.get(token)
        if session is None:
            return None
        entry = self._hass.config_entries.async_get_entry(session.entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            return None
        refresh_token = self._hass.auth.async_get_refresh_token(
            session.refresh_token_id
        )
        if refresh_token is None:
            return None
        user = refresh_token.user
        if user.id != session.user_id or not user.is_active or not user.is_admin:
            return None
        return session, entry

    @callback
    def _expire(self) -> None:
        now = time.monotonic()
        for session in [
            s
            for s in self._by_token.values()
            if s.detached_at is not None and now - s.detached_at > RESUME_SECONDS
        ]:
            # Nothing is subscribed to a detached session, so no reason is sent.
            self.end(session, "expired")


@callback
def async_get_embed_sessions(hass: HomeAssistant) -> EmbedSessions:
    """Return the domain's embed session table."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    sessions: EmbedSessions = domain_data.setdefault(DATA_EMBED, EmbedSessions(hass))
    return sessions


# ---------------------------------------------------------------------------
# WebSocket commands. Both refuse anyone who is not an administrator.


def panel_state(entry: ConfigEntry) -> str:
    """Return an entry's reachability, read from its coordinator's last poll."""
    if entry.state is not ConfigEntryState.LOADED:
        return STATE_NOT_LOADED
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if coordinator is None:
        return STATE_NOT_LOADED
    return STATE_REACHABLE if coordinator.last_update_success else STATE_UNREACHABLE


def _panel_device(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})


def panel_display_name(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Prefer the device's own (possibly user-renamed) name over the raw entry.title.

    The config entry title is set once, at add time, from the panel's reported panel_id
    (a device identifier, not a friendly name). The device registry entry for the same
    panel already carries whatever nicer name the panel itself reports, or a user's own
    rename via the Devices page (name_by_user): this only changes which of those two
    already-existing values the sidebar reads, not how either is set.
    """
    device = _panel_device(hass, entry)
    if device is None:
        return entry.title
    return device.name_by_user or device.name or entry.title


@require_admin
@websocket_command({vol.Required("type"): "panel_assistant/embed_panels"})
@callback
def ws_embed_panels(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the panels attached to Home Assistant, by display name."""
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda entry: (entry.title.casefold(), entry.entry_id),
    )
    connection.send_result(
        msg["id"],
        {
            "panels": [
                {
                    "entry_id": entry.entry_id,
                    "title": panel_display_name(hass, entry),
                    "state": panel_state(entry),
                    "device_id": device.id
                    if (device := _panel_device(hass, entry))
                    else None,
                }
                for entry in entries[:MAX_PANELS]
            ]
        },
    )


@require_admin
@websocket_command(
    {
        vol.Required("type"): "panel_assistant/embed_session",
        vol.Required("entry_id"): str,
        vol.Required("language"): str,
        vol.Required("theme"): vol.In(("light", "dark")),
        vol.Optional("resume"): str,
    }
)
@callback
def ws_embed_session(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Open an embed session for one panel, for as long as it is subscribed."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], ERR_NOT_FOUND, "No such panel.")
        return
    if entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], ERR_NOT_LOADED, "The panel is not loaded.")
        return
    if connection.refresh_token_id is None:
        raise Unauthorized
    session = async_get_embed_sessions(hass).open(
        connection,
        msg["id"],
        entry.entry_id,
        msg["language"],
        msg["theme"],
        msg.get("resume"),
    )
    connection.send_result(msg["id"], {"url": session.url})
    # The frontend's subscription helper drops a result, so the URL also
    # arrives as the subscription's first event.
    connection.send_message(
        event_message(msg["id"], {"kind": "opened", "url": session.url})
    )


# ---------------------------------------------------------------------------
# The proxy.


def _host_header(address: PanelAddress) -> str:
    """Return the panel's Host header, always with its port."""
    host = f"[{address.host}]" if ":" in address.host else address.host
    return f"{host}:{address.port}"


def _not_found() -> web.Response:
    return web.Response(status=404, headers=SECURITY_HEADERS)


def _plain(status: int, extra: dict[str, str] | None = None) -> web.Response:
    return web.Response(status=status, headers={**SECURITY_HEADERS, **(extra or {})})


class _BoundedBody:
    """Stream the browser's body to the panel, stopping past the proxy's limit."""

    def __init__(self, request: web.Request) -> None:
        self._request = request
        self.too_large = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        sent = 0
        async for chunk in self._request.content.iter_any():
            sent += len(chunk)
            if sent > MAX_REQUEST_BODY:
                # Aiohttp reports a failing body as a connection error, so the
                # reason is kept here.
                self.too_large = True
                raise ValueError("request body too large")
            yield chunk


def _is_long_lived_request(request: web.Request, target: URL) -> bool:
    accept = request.headers.get(hdrs.ACCEPT, "")
    return "text/event-stream" in accept.lower() or (
        target.path == "/api/v1/input" and target.query.get("capture") == "1"
    )


async def _read_bounded(result: aiohttp.ClientResponse, limit: int) -> bytes:
    """Read a response until it ends or passes the limit, whichever is first."""
    chunks: list[bytes] = []
    size = 0
    while size <= limit and (chunk := await result.content.readany()):
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _is_long_lived_response(result: aiohttp.ClientResponse) -> bool:
    content_type = result.headers.get(hdrs.CONTENT_TYPE, "")
    return (
        content_type.partition(";")[0].strip().lower() == "text/event-stream"
        or hdrs.CONTENT_DISPOSITION in result.headers
    )


def _rewrite_location(location: str, address: PanelAddress, prefix: str) -> str | None:
    """Return a redirect that stays inside the proxy, or nothing."""
    if location.startswith("/") and not location.startswith("//"):
        return prefix + location
    try:
        url = URL(location)
    except ValueError:
        return None
    if (
        url.is_absolute()
        and url.scheme == "http"
        and url.host is not None
        and url.host.lower() == address.host
        and url.port == address.port
    ):
        return prefix + (url.raw_path_qs or "/")
    return None


class EmbedProxyView(HomeAssistantView):
    """Serve a panel's own web interface to the administrator who opened it."""

    url = EMBED_PREFIX + "/{token}/{path:.*}"
    name = "api:panel_assistant:embed"
    # The token is the credential, checked on every request below.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def _handle(
        self, request: web.Request, token: str, path: str
    ) -> web.StreamResponse:
        if not _TOKEN.fullmatch(token):
            return _not_found()
        sessions = async_get_embed_sessions(self.hass)
        admitted = sessions.admit(token)
        prefix = f"{EMBED_PREFIX}/{token}"
        if admitted is None or not request.raw_path.startswith(prefix + "/"):
            return _not_found()
        session, entry = admitted
        raw_target = request.raw_path[len(prefix) :]
        target = URL(raw_target, encoded=True)
        long_lived = _is_long_lived_request(request, target)
        if session.active >= MAX_CONCURRENT or (
            long_lived and session.long_lived >= MAX_LONG_LIVED
        ):
            return _plain(429, {hdrs.RETRY_AFTER: "2"})
        declared = request.content_length
        if declared is not None and declared > MAX_REQUEST_BODY:
            return _plain(413)

        address = normalize_address(entry.data[CONF_ADDRESS])
        headers: dict[str, str] = {hdrs.HOST: _host_header(address)}
        for name in REQUEST_HEADERS:
            if (value := request.headers.get(name)) is not None:
                headers[name] = value
        headers["Sec-Fetch-Site"] = "same-origin"
        if session.language is not None:
            headers[hdrs.ACCEPT_LANGUAGE] = session.language
        headers[EMBED_HEADER] = session.embed_header

        session.active += 1
        if long_lived:
            session.long_lived += 1
        try:
            return await self._forward(
                request, session, address, raw_target, headers, prefix, long_lived
            )
        finally:
            session.active -= 1
            if long_lived:
                session.long_lived -= 1

    async def _forward(
        self,
        request: web.Request,
        session: EmbedSession,
        address: PanelAddress,
        raw_target: str,
        headers: dict[str, str],
        prefix: str,
        long_lived: bool,
    ) -> web.StreamResponse:
        url = URL(str(address.base_url) + raw_target, encoded=True)
        body: _BoundedBody | bytes | None
        if not request.body_exists:
            body = None
            proof = self._proof(session, request.method, raw_target, b"")
        elif (
            request.content_length is not None
            and request.content_length <= embed_proof.MAX_PROVEN_BODY
            and self._signing_key(session) is not None
        ):
            # Read whole, so the digest covers exactly the bytes that are sent.
            body = await request.read()
            proof = self._proof(session, request.method, raw_target, body)
        else:
            # A large body, one of unknown length, or any body while the panel
            # holds no key, streams unproven exactly as it always has.
            body = _BoundedBody(request)
            proof = None
        if proof is not None:
            headers[embed_proof.PROOF_HEADER] = proof
        try:
            result = await async_get_clientsession(self.hass).request(
                request.method,
                url,
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=PROXY_TIMEOUT,
                skip_auto_headers=_SKIP_AUTO_HEADERS,
                auto_decompress=False,
            )
        except aiohttp.ClientError, TimeoutError:
            too_large = isinstance(body, _BoundedBody) and body.too_large
            return _plain(413 if too_large else 502)
        session.upstream.add(result)
        counted = False
        try:
            out = {**SECURITY_HEADERS}
            for name in RESPONSE_HEADERS:
                if (value := result.headers.get(name)) is not None:
                    out[name] = value
            if (location := result.headers.get(hdrs.LOCATION)) is not None:
                rewritten = _rewrite_location(location, address, prefix)
                if rewritten is None:
                    return _plain(502)
                out[hdrs.LOCATION] = rewritten

            if request.method == hdrs.METH_HEAD or result.status in (204, 304):
                out.pop(hdrs.CONTENT_LENGTH, None)
                return web.Response(status=result.status, headers=out)

            content_type = result.headers.get(hdrs.CONTENT_TYPE, "")
            if content_type.partition(";")[0].strip().lower() == "text/html":
                prefix_bytes = await _read_bounded(result, MAX_REWRITTEN_BODY)
                if len(prefix_bytes) <= MAX_REWRITTEN_BODY:
                    page = prefix_bytes
                    if (at := page.find(BASE_ELEMENT, 0, BASE_WINDOW)) >= 0:
                        page = b"".join(
                            (
                                page[:at],
                                f'<base href="{prefix}/">'.encode(),
                                page[at + len(BASE_ELEMENT) :],
                            )
                        )
                    out.pop(hdrs.CONTENT_LENGTH, None)
                    return web.Response(status=result.status, headers=out, body=page)
            else:
                prefix_bytes = b""

            if not long_lived and _is_long_lived_response(result):
                if session.long_lived >= MAX_LONG_LIVED:
                    return _plain(429, {hdrs.RETRY_AFTER: "2"})
                session.long_lived += 1
                counted = True
            return await self._stream(
                request, session, result, out, prefix_bytes, long_lived or counted
            )
        finally:
            if counted:
                session.long_lived -= 1
            session.upstream.discard(result)
            result.release()

    def _signing_key(
        self, session: EmbedSession
    ) -> tuple[PanelSession, str, bytes] | None:
        """Return the panel's live session, key ID and key when it can sign."""
        panel = async_get_sessions(self.hass).get(session.entry_id)
        if (
            panel is None
            or panel.embed_key is None
            or panel.embed_key_id is None
            or panel.embed_counter >= embed_proof.MAX_COUNTER
        ):
            return None
        return panel, panel.embed_key_id, panel.embed_key

    def _proof(
        self, session: EmbedSession, method: str, target: str, body: bytes
    ) -> str | None:
        """Sign one request with the panel's live session key, if it holds one.

        Only a request this session has just admitted gets here, so the proof
        names the administrator who opened it.
        """
        signing = self._signing_key(session)
        if signing is None:
            return None
        panel, key_id, key = signing
        panel.embed_counter += 1
        return embed_proof.sign(
            key,
            did=panel.did,
            key_id=key_id,
            counter=panel.embed_counter,
            user_id=session.user_id,
            method=method,
            target=target,
            body=body,
        )

    async def _stream(
        self,
        request: web.Request,
        session: EmbedSession,
        result: aiohttp.ClientResponse,
        headers: dict[str, str],
        first: bytes,
        long_lived: bool,
    ) -> web.StreamResponse:
        response = web.StreamResponse(status=result.status, headers=headers)
        watcher = (
            self.hass.async_create_background_task(
                self._watch(session, result), f"{DOMAIN} embed stream check"
            )
            if long_lived
            else None
        )
        try:
            await response.prepare(request)
            if first:
                await response.write(first)
            async for data in result.content.iter_any():
                await response.write(data)
        except aiohttp.ClientError, ConnectionError:
            # Either side went away after the headers were sent, including a session
            # end closing the panel's response: the browser sees a cut-off stream,
            # and there is no status left to change.
            pass
        finally:
            if watcher is not None:
                watcher.cancel()
        return response

    async def _watch(
        self, session: EmbedSession, result: aiohttp.ClientResponse
    ) -> None:
        """Close a long-lived response once its session no longer passes."""
        sessions = async_get_embed_sessions(self.hass)
        while True:
            await asyncio.sleep(RECHECK_SECONDS)
            admitted = sessions.admit(session.token)
            if admitted is None or admitted[0] is not session:
                result.close()
                return

    get = _handle
    head = _handle
    post = _handle
    put = _handle
    patch = _handle
    delete = _handle


@callback
def async_setup_embed(hass: HomeAssistant) -> None:
    """Register both commands once for the domain; the sidebar registers the view."""
    async_get_embed_sessions(hass)
    websocket_api.async_register_command(hass, ws_embed_panels)
    websocket_api.async_register_command(hass, ws_embed_session)

    @callback
    def _user_removed(event: Event[Any]) -> None:
        user_id = event.data.get("user_id")
        if not isinstance(user_id, str):
            return
        async_get_embed_sessions(hass).end_user(user_id, REASON_USER_REMOVED)

    hass.bus.async_listen(EVENT_USER_REMOVED, _user_removed)

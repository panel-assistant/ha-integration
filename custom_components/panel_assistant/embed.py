"""The sidebar's embedded panel interface: sessions and the embed view.

An administrator's sidebar opens an embed session over its WebSocket. The
session's token names a path under which Home Assistant serves the panel's own
interface without opening any connection to the panel: its pages come over the
panel's session, its scripts and styles from the bundle Panel Assistant keeps
for its build, and its requests through the sidebar's relay (see
``ui_relay.py``). The panel never sees the browser's credential, and the
integration gives the panel no credential of its own either.

Every request is checked again, and a request that fails the check is answered
404 rather than 401: Core records each 401 as a failed login and notifies
about it, which an expired frame would do for every one of its subresources.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Final

import voluptuous as vol
from aiohttp import web
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    require_admin,
    websocket_command,
)
from homeassistant.components.websocket_api.messages import event_message
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.http import HomeAssistantView

from .blob import async_get_blobs, receive, send
from .const import DOMAIN
from .device import panel_display_name
from .transport_protocol import ERR_PANEL_UNAVAILABLE, OUTCOME_APPLIED
from .ui_relay import (
    BROWSER_BLOB,
    async_get_bundles,
    async_manage,
    content_type,
    live_ui_session,
)

DATA_EMBED: Final = "embed"
EMBED_PREFIX: Final = "/api/panel_assistant/embed"


class _PageShell(HTMLParser):
    """Read the shell's build and whether its requested tab is visible."""

    build: str | None = None
    has_nav = False
    active_tab = False
    _nav_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "body":
            self.build = attributes.get("data-build") or None
        if tag == "div":
            if self._nav_depth:
                self._nav_depth += 1
            elif "nav" in classes:
                self.has_nav = True
                self._nav_depth = 1
        if self._nav_depth and tag == "a" and "active" in classes:
            self.active_tab = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._nav_depth:
            self._nav_depth -= 1


REASON_ENTRY_UNLOADED: Final = "entry_unloaded"
REASON_USER_REMOVED: Final = "user_removed"

STATE_REACHABLE: Final = "reachable"
STATE_UNREACHABLE: Final = "unreachable"
STATE_NOT_LOADED: Final = "not_loaded"

ERR_NOT_FOUND: Final = "not_found"
ERR_NOT_LOADED: Final = "not_loaded"

MAX_PANELS: Final = 200
RESUME_SECONDS: Final = 60
BASE_WINDOW: Final = 4096
BASE_ELEMENT: Final = b'<base href="/">'

# The API page is a developer page whose calls would escape the embed view.
ALWAYS_HIDDEN: Final = ("api",)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_LANGUAGE = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*")
_MAX_LANGUAGE = 35
# The page each path names, answered by ``page.<name>`` over the session.
PAGES: Final = {
    "": "info",
    "configure": "configure",
    "setup": "setup",
    "profiles": "profiles",
    "install": "install",
    "logs": "logs",
    "entities": "entities",
}
# The bundle's files outside ``assets/``.
BUNDLE_FILES: Final = frozenset({"info.js", "info.css", "icon.svg", "favicon.svg"})
# Every document here comes from a panel's session, which a non-administrator
# account may hold, so it runs with an opaque origin and never reaches Home
# Assistant's frontend. The header, not the frame, carries the sandbox: it holds
# for a top-level visit and for any document the frame navigates to. Each token
# is one the panel's pages use: dialogs, downloads, new-tab links and forms.
SECURITY_HEADERS: Final = {
    "Content-Security-Policy": (
        "frame-ancestors 'self'; sandbox allow-scripts allow-forms allow-downloads "
        "allow-modals allow-popups allow-popups-to-escape-sandbox"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}


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
    # The relay connection its page requests name to the panel.
    relay_id: str = field(default_factory=lambda: secrets.token_urlsafe(16))

    @property
    def url(self) -> str:
        """Return the path the sidebar frames."""
        return f"{EMBED_PREFIX}/{self.token}/"

    @property
    def embed_header(self) -> str:
        """Return the panel's embedded-mode switch for this session's pages."""
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
    if coordinator.restart_notice is not None:
        return "restarting"
    return STATE_REACHABLE if coordinator.available else STATE_UNREACHABLE


def panel_restart_reason(entry: ConfigEntry) -> str | None:
    """Return the reason shown beside one restarting panel."""
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    notice = None if coordinator is None else coordinator.restart_notice
    return None if notice is None else notice.reason


def _panel_device(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry | None:
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )


@require_admin
@websocket_command({vol.Required("type"): "panel_assistant/embed_panels"})
@callback
def ws_embed_panels(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """List the panels attached to Home Assistant, by display name."""
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda entry: (panel_display_name(hass, entry).casefold(), entry.entry_id),
    )
    connection.send_result(
        msg["id"],
        {
            "panels": [
                {
                    "entry_id": entry.entry_id,
                    "title": panel_display_name(hass, entry),
                    "state": panel_state(entry),
                    **(
                        {"reason": reason}
                        if (reason := panel_restart_reason(entry)) is not None
                        else {}
                    ),
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
    if live_ui_session(hass, entry.entry_id) is None:
        # Without a session that serves the sidebar the frame would stay blank;
        # refused, the sidebar shows why and asks again on its next refresh.
        connection.send_error(
            msg["id"], ERR_PANEL_UNAVAILABLE, "The panel cannot be reached now."
        )
        return
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
# The embed view.


def _respond(status: int, **fields: Any) -> web.Response:
    response = web.Response(status=status, **fields)
    response.headers.update(SECURITY_HEADERS)
    return response


# A page asked for while the panel's session is down (it restarts with every app
# update) asks again shortly, so an open sidebar shows the panel once it is back
# instead of staying blank until another panel is chosen.
RETRY_SECONDS: Final = 3


def _retry(session: EmbedSession) -> web.Response:
    return _respond(
        503,
        body=(
            '<!doctype html><meta name="panel-assistant-wait" content="true">'
            # The sidebar cannot read a sandboxed document, so this one says so.
            # It may arrive before or after this document's load (WebKit).
            '<script>parent.postMessage({type:"panel-assistant-wait"},"*")</script>'
            f'<meta name="color-scheme" content="{session.theme}">'
            f'<meta http-equiv="refresh" content="{RETRY_SECONDS}">'
        ),
        content_type="text/html",
        headers={"Retry-After": str(RETRY_SECONDS)},
    )


# Every served page says, from its own load, that it is a page to show. The
# sidebar shows the frame on that message, never on the frame's load event:
# WebKit delivers the retry document's message after that document's load, so
# a load cannot tell a page from a retry document (Safari, 2026-10-24).
READY_SCRIPT: Final = (
    b'<script>addEventListener("load",function(){'
    b'parent.postMessage({type:"panel-assistant-ready"},"*")})</script>'
)


def _page_body(page: bytes, prefix: str) -> bytes:
    """The page as the frame gets it: inside the embed view, and announcing itself."""
    return _rebase(page, prefix) + READY_SCRIPT


def _rebase(page: bytes, prefix: str) -> bytes:
    """Point the page's base element at the embed view, so its URLs stay inside."""
    at = page.find(BASE_ELEMENT, 0, BASE_WINDOW)
    if at < 0:
        return page
    return b"".join(
        (page[:at], f'<base href="{prefix}/">'.encode(), page[at + len(BASE_ELEMENT) :])
    )


class EmbedView(HomeAssistantView):
    """Serve a panel's own interface to the administrator who opened it.

    Nothing here opens a connection to the panel: pages come over its session,
    bundle files from the store, and blobs from the browser's own transfers.
    """

    url = EMBED_PREFIX + "/{token}/{path:.*}"
    name = "api:panel_assistant:embed"
    # The token is the credential, checked on every request below.
    requires_auth = False
    # A sandboxed page's requests come from origin "null", so its downloads and
    # uploads are cross-origin; the token, not the origin, admits them.
    cors_allowed = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    def _admit(self, token: str) -> tuple[EmbedSession, ConfigEntry] | None:
        if not _TOKEN.fullmatch(token):
            return None
        return async_get_embed_sessions(self.hass).admit(token)

    async def get(self, request: web.Request, token: str, path: str) -> web.Response:
        """Serve a page, a bundle file or a download."""
        if (admitted := self._admit(token)) is None:
            return _respond(404)
        session, entry = admitted
        if (name := PAGES.get(path)) is not None:
            return await self._page(request, session, entry, name)
        if path in BUNDLE_FILES or path.startswith("assets/"):
            panel = live_ui_session(self.hass, entry.entry_id)
            data = (
                None
                if panel is None
                else await async_get_bundles(self.hass).async_file(entry, panel, path)
            )
            if data is None:
                return _respond(404)
            return _respond(
                200,
                body=data,
                content_type=content_type(path),
                headers={"Cache-Control": "no-cache"},
            )
        if (match := BROWSER_BLOB.fullmatch(path)) is not None and (
            transfer := async_get_blobs(self.hass).claim(
                match[1], session.user_id, upload=False, entry_id=entry.entry_id
            )
        ) is not None:
            response = send(transfer)
            response.headers.update(SECURITY_HEADERS)
            return response
        return _respond(404)

    async def put(self, request: web.Request, token: str, path: str) -> web.Response:
        """Receive one browser upload into the slot ``blob.upload`` opened."""
        if (admitted := self._admit(token)) is None:
            return _respond(404)
        session, entry = admitted
        if (match := BROWSER_BLOB.fullmatch(path)) is None or (
            transfer := async_get_blobs(self.hass).claim(
                match[1], session.user_id, upload=True, entry_id=entry.entry_id
            )
        ) is None:
            return _respond(404)
        response = await receive(request, transfer)
        response.headers.update(SECURITY_HEADERS)
        return response

    async def _page(
        self,
        request: web.Request,
        session: EmbedSession,
        entry: ConfigEntry,
        name: str,
    ) -> web.Response:
        panel = live_ui_session(self.hass, entry.entry_id)
        if panel is None:
            return _retry(session)
        args: dict[str, Any] = {"embed": session.embed_header}
        for key in ("lang", "ha_lang"):
            if (value := request.query.get(key)) and _valid_language(value):
                args[key] = value
        if (theme := request.query.get("theme")) in ("light", "dark"):
            args["theme"] = theme
        try:
            outcome, data = await async_manage(
                self.hass,
                panel,
                f"page.{name}",
                args,
                {"user": session.user_id, "connection": session.relay_id},
            )
        except HomeAssistantError:
            return _retry(session)
        if data is None and isinstance(outcome.result, str):
            data = outcome.result.encode()
        if live_ui_session(self.hass, entry.entry_id) is not panel:
            return _retry(session)
        if name != "info" and outcome.status == 404:
            return _respond(
                302, headers={"Location": f"{EMBED_PREFIX}/{session.token}/"}
            )
        if data is None or (outcome.outcome != OUTCOME_APPLIED and not outcome.status):
            return _respond(502)
        if (outcome.status or 200) == 200:
            shell = _PageShell()
            shell.feed(data.decode("utf-8", errors="replace"))
            if shell.build is not None:
                async_get_bundles(self.hass).observe_build(entry, panel, shell.build)
            if name != "info" and shell.has_nav and not shell.active_tab:
                return _respond(
                    302, headers={"Location": f"{EMBED_PREFIX}/{session.token}/"}
                )
            if session.connection is not None and session.subscription_id is not None:
                session.connection.send_message(
                    event_message(
                        session.subscription_id,
                        {"kind": "page", "page": "" if name == "info" else name},
                    )
                )
        return _respond(
            outcome.status or 200,
            body=_page_body(data, f"{EMBED_PREFIX}/{session.token}"),
            content_type="text/html",
            charset="utf-8",
        )


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

"""The sidebar's embed sessions and its proxy to a panel."""

import asyncio
import base64
import hashlib
import hmac
import logging
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import embed
from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.transport import (
    async_get_sessions,
    session_diagnostics,
)

from .test_cutover import _HELLO_TAIL as HELLO_TAIL
from .test_cutover import NATIVE
from .test_native import _setup as _native_setup

DID = "d" * 64
HEALTH = PanelHealth(
    version="0.9.8-rc1",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    discovery_id=DID,
)
STATUS = PanelStatus(warning_count=0, capability_count=0)
HELLO: dict[str, Any] = {
    "type": "panel_assistant/hello",
    "protocol": {"min": 1, "max": 1},
    "did": DID,
    "app": {"version": "0.9.8-rc1", "version_code": 790},
    "contract_digest": "c" * 64,
    "capabilities": ["state"],
    "channels": [],
}
PROOF_HEADER = "X-Panel-Assistant-Proof"
PAGE = (
    b'<!doctype html><html><head><base href="/"><title>x</title></head>'
    b'<body><a href="configure">c</a><base href="/"></body></html>'
)

type WsClientFactory = Callable[..., Awaitable[Any]]


class FakePanel:
    """A panel web server that records what reaches it."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.config_posts: list[dict[str, str]] = []
        self.config_status = 200
        self.stream_release = asyncio.Event()
        self.stream_closed = asyncio.Event()
        # Room for a body just past the largest one the proxy signs.
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.server = TestServer(app, host="127.0.0.1")

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.server.port}"

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        self.requests.append(
            {
                "method": request.method,
                "target": request.raw_path,
                "headers": dict(request.headers),
                # Every line, so a second header line cannot hide in a mapping.
                "proofs": request.headers.getall(PROOF_HEADER, []),
                "body": body,
            }
        )
        path = request.path
        if path == "/api/v1/config" and request.method == "POST":
            self.config_posts.append(dict(parse_qsl(body.decode())))
            return web.json_response({"ok": True}, status=self.config_status)
        if path == "/page":
            return web.Response(
                body=PAGE,
                content_type="text/html",
                headers={
                    "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "frame-ancestors 'none'",
                    "Set-Cookie": "a=b",
                    "Vary": "Accept-Language",
                    "X-Other": "dropped",
                },
            )
        if path == "/late-base":
            return web.Response(
                body=b" " * embed.BASE_WINDOW + b'<base href="/">',
                content_type="text/html",
            )
        if path == "/huge":
            return web.Response(
                body=b'<base href="/">' + b" " * embed.MAX_REWRITTEN_BODY,
                content_type="text/html",
            )
        if path == "/redirect":
            return web.Response(status=302, headers={"Location": "/setup?x=1"})
        if path == "/redirect-absolute":
            return web.Response(
                status=308,
                headers={"Location": f"http://{self.address}/api/v1/status"},
            )
        if path == "/redirect-away":
            return web.Response(
                status=302, headers={"Location": "http://ha.example/auth/authorize"}
            )
        if path == "/api/v1/logs/stream":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            try:
                await self.stream_release.wait()
                await response.write(b"data: second\n\n")
            finally:
                self.stream_closed.set()
            return response
        if path == "/echo":
            return web.Response(body=body, content_type="application/octet-stream")
        return web.Response(text="ok")


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Serve a fake panel on loopback, which the proxy reaches like a real one."""
    fake = FakePanel()
    await fake.server.start_server()
    try:
        yield fake
    finally:
        fake.stream_release.set()
        await fake.server.close()


async def _load_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    executor = SimpleNamespace(
        async_acquire_finalizer=AsyncMock(return_value=True),
        async_release_finalizer=AsyncMock(),
    )
    manager = SimpleNamespace(
        async_list=AsyncMock(return_value=()), async_transition=AsyncMock()
    )
    with (
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.fixture
async def entry(
    hass: HomeAssistant, panel: FakePanel
) -> AsyncGenerator[MockConfigEntry]:
    """Load one panel entry whose address is the fake panel."""
    assert await async_setup_component(hass, "http", {})
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: panel.address}
    )
    config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        await _load_entry(hass, config_entry)
        assert config_entry.state is ConfigEntryState.LOADED
        yield config_entry


async def _receive(client: Any) -> dict[str, Any]:
    async with asyncio.timeout(5):
        message: dict[str, Any] = await client.receive_json()
    return message


async def _open_session(client: Any, entry_id: str, **extra: Any) -> tuple[int, str]:
    """Subscribe, and return the subscription ID and the URL both replies name."""
    await client.send_json_auto_id(
        {
            "type": "panel_assistant/embed_session",
            "entry_id": entry_id,
            "language": "de",
            "theme": "dark",
            **extra,
        }
    )
    result = await _receive(client)
    assert result["success"], result
    opened = await _receive(client)
    assert opened["event"] == {"kind": "opened", "url": result["result"]["url"]}
    url: str = result["result"]["url"]
    return result["id"], url


def _notified_login_failure(hass: HomeAssistant) -> bool:
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    return "http-login" in notifications


# ---------------------------------------------------------------------------
# Commands


async def test_panels_list_by_device_name_falling_back_to_title(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, entry: MockConfigEntry
) -> None:
    """entry.title is the panel_id at add time, not a friendly name (config_flow.py sets
    it from health.panel_id); the device registry already carries whatever nicer name
    the panel reports or a user set via the Devices page, so the sidebar prefers that
    when it exists."""
    other_health = PanelHealth(
        version="0.9.8-rc1",
        panel_id="attic-unit",
        build="1000",
        config_hash="1a2b3c4d",
        discovery_id="c" * 64,
    )
    unreachable = MockConfigEntry(
        domain=DOMAIN, title="attic", data={CONF_ADDRESS: "127.0.0.1:1"}
    )
    unreachable.add_to_hass(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=other_health),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        await _load_entry(hass, unreachable)
    unreachable.runtime_data.coordinator.last_update_success = False
    # No device is ever created for an entry that never finished loading, so this one
    # has no registry row to prefer and keeps showing its title-at-add-time verbatim.
    MockConfigEntry(
        domain=DOMAIN, title="Bedroom", data={CONF_ADDRESS: "127.0.0.1:2"}
    ).add_to_hass(hass)
    MockConfigEntry(domain="other", title="Aardvark").add_to_hass(hass)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    reply = await _receive(client)
    assert reply["success"], reply
    assert [(p["title"], p["state"]) for p in reply["result"]["panels"]] == [
        # No panel_assistant_device.name in STATUS, so this falls back within the device
        # to health.panel_id -- still nicer than the raw entry.title "attic" here.
        ("attic-unit", "unreachable"),
        ("Bedroom", "not_loaded"),
        # entry fixture's own panel_id ("alpha") reads the same either way.
        ("alpha", "reachable"),
    ]
    assert set(reply["result"]["panels"][2]) == {"entry_id", "title", "state"}

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    device_registry.async_update_device(device.id, name_by_user="Custom Name")
    await client.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    reply = await _receive(client)
    assert reply["success"], reply
    renamed = next(
        p for p in reply["result"]["panels"] if p["entry_id"] == entry.entry_id
    )
    assert renamed["title"] == "Custom Name"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "panel_assistant/embed_panels"},
        {
            "type": "panel_assistant/embed_session",
            "entry_id": "x",
            "language": "en",
            "theme": "light",
        },
    ],
)
async def test_both_commands_refuse_a_non_administrator(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    message: dict[str, Any],
) -> None:
    if message.get("entry_id") == "x":
        message = {**message, "entry_id": entry.entry_id}
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(message)
    reply = await _receive(client)
    assert not reply["success"]
    assert reply["error"]["code"] == "unauthorized"
    assert not embed.async_get_embed_sessions(hass)._by_token


async def test_session_refuses_an_unknown_or_unloaded_entry(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, entry: MockConfigEntry
) -> None:
    client = await hass_ws_client(hass)
    for entry_id, code in (("missing", "not_found"), (entry.entry_id, "not_loaded")):
        if code == "not_loaded":
            assert await hass.config_entries.async_unload(entry.entry_id)
        await client.send_json_auto_id(
            {
                "type": "panel_assistant/embed_session",
                "entry_id": entry_id,
                "language": "en",
                "theme": "light",
            }
        )
        reply = await _receive(client)
        assert reply["error"]["code"] == code


# ---------------------------------------------------------------------------
# The proxy


async def test_request_to_the_panel_carries_exactly_the_allowlist(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    assert url.startswith("/api/panel_assistant/embed/") and url.endswith("/")
    assert len(url.split("/")[4]) == 43
    browser = await hass_client_no_auth()
    response = await browser.post(
        url + "api/v1/config%2Fx?b=1&a=%20",
        data=b"k=v",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "browser",
            "Cookie": "session=1",
            "Authorization": "Bearer admin",
            "Origin": "http://ha.example",
            "Referer": "http://ha.example/panel-assistant",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Accept-Language": "fr",
            "Accept-Encoding": "gzip",
            "X-Panel-Assistant-Proof": "v1;forged",
            "X-Panel-Assistant-Embed": "v=1;lang=fr",
            "Last-Event-ID": "7",
        },
    )
    assert response.status == 200
    seen = panel.requests[-1]
    assert seen["method"] == "POST"
    assert seen["target"] == "/api/v1/config%2Fx?b=1&a=%20"
    assert seen["body"] == b"k=v"
    assert {name.lower(): value for name, value in seen["headers"].items()} == {
        "host": panel.address,
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "content-length": "3",
        "user-agent": "browser",
        "last-event-id": "7",
        "sec-fetch-site": "same-origin",
        "accept-language": "de",
        "x-panel-assistant-embed": "v=1;lang=de;theme=dark;hide=api",
    }


async def test_host_header_always_names_the_port() -> None:
    from custom_components.panel_assistant.client import normalize_address

    assert embed._host_header(normalize_address("panel.local")) == "panel.local:8888"
    assert embed._host_header(normalize_address("[fd00::1]")) == "[fd00::1]:8888"


async def test_an_invalid_language_is_left_out_of_the_switch(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id, language="x;hide=setup")
    browser = await hass_client_no_auth()
    assert (await browser.get(url)).status == 200
    headers = {k.lower(): v for k, v in panel.requests[-1]["headers"].items()}
    assert headers["x-panel-assistant-embed"] == "v=1;theme=dark;hide=api"
    assert "accept-language" not in headers


async def test_response_keeps_the_allowlist_and_owns_framing(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "page")
    assert response.status == 200
    body = await response.read()
    token_base = f'<base href="{url}">'.encode()
    # Only the first base element, and only that, is rewritten.
    assert body == PAGE.replace(b'<base href="/">', token_base, 1)
    assert response.headers["Content-Length"] == str(len(body))
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Vary"] == "Accept-Language"
    assert "Set-Cookie" not in response.headers
    assert "X-Other" not in response.headers


@pytest.mark.parametrize("path", ["late-base", "huge"])
async def test_base_outside_its_window_or_in_a_large_page_is_untouched(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    path: str,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    body = await (await browser.get(url + path)).read()
    assert url.encode() not in body
    assert b'<base href="/">' in body


@pytest.mark.parametrize(
    ("path", "status", "location"),
    [
        ("redirect", 302, "{url}setup?x=1"),
        ("redirect-absolute", 308, "{url}api/v1/status"),
        ("redirect-away", 502, None),
    ],
)
async def test_redirects_stay_inside_the_proxy(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    path: str,
    status: int,
    location: str | None,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + path, allow_redirects=False)
    assert response.status == status
    if location is None:
        assert "Location" not in response.headers
    else:
        assert response.headers["Location"] == location.format(url=url[:-1] + "/")


async def test_the_integration_never_creates_a_user_or_signs_a_panel_in(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    """The panel signs in through its own setup; the proxy only forwards."""
    users_before = sorted(user.id for user in await hass.auth.async_get_users())
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.post(url + "_panel_assistant/sign-in")
    assert response.status == 200
    assert panel.requests[-1]["target"] == "/_panel_assistant/sign-in"
    assert not panel.config_posts
    assert sorted(user.id for user in await hass.auth.async_get_users()) == (
        users_before
    )
    assert set(entry.data) == {CONF_ADDRESS}
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert sorted(user.id for user in await hass.auth.async_get_users()) == (
        users_before
    )


async def test_request_bodies_stream_and_stop_at_the_limit(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.post(url + "echo", data=b"x" * 100_000)
    assert await response.read() == b"x" * 100_000

    monkeypatch.setattr(embed, "MAX_REQUEST_BODY", 10)
    assert (await browser.post(url + "echo", data=b"x" * 11)).status == 413

    async def chunks() -> AsyncGenerator[bytes]:
        for _ in range(3):
            yield b"12345"

    assert (await browser.post(url + "echo", data=chunks())).status == 413


async def test_event_stream_is_relayed_as_it_arrives(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "api/v1/logs/stream")
    assert response.headers["Content-Type"] == "text/event-stream"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    async with asyncio.timeout(5):
        assert await response.content.readuntil(b"\n\n") == b"data: first\n\n"
    panel.stream_release.set()
    async with asyncio.timeout(5):
        assert await response.content.readuntil(b"\n\n") == b"data: second\n\n"
    response.close()


async def test_a_long_lived_stream_closes_once_its_administrator_is_demoted(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embed, "RECHECK_SECONDS", 0.05)
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "api/v1/logs/stream")
    async with asyncio.timeout(5):
        await response.content.readuntil(b"\n\n")
    user_group = await hass.auth.async_get_group(GROUP_ID_USER)
    assert user_group is not None
    await hass.auth.async_update_user(hass_admin_user, group_ids=[GROUP_ID_USER])
    async with asyncio.timeout(5):
        await panel.stream_closed.wait()
        assert await response.content.read() == b""


async def test_ending_a_session_closes_its_streams(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    subscription, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "api/v1/logs/stream")
    async with asyncio.timeout(5):
        await response.content.readuntil(b"\n\n")
    assert await hass.config_entries.async_unload(entry.entry_id)
    closed = await _receive(ws)
    assert closed == {
        "id": subscription,
        "type": "event",
        "event": {"kind": "closed", "reason": "entry_unloaded"},
    }
    async with asyncio.timeout(5):
        await panel.stream_closed.wait()
    assert (await browser.get(url)).status == 404


async def test_session_limits_answer_429(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    session = embed.async_get_embed_sessions(hass)._by_token[url.split("/")[4]]
    browser = await hass_client_no_auth()
    session.active = embed.MAX_CONCURRENT
    response = await browser.get(url + "page")
    assert (response.status, response.headers["Retry-After"]) == (429, "2")
    session.active = 0
    session.long_lived = embed.MAX_LONG_LIVED
    assert (await browser.get(url + "api/v1/logs/stream")).status == 429
    assert (await browser.post(url + "api/v1/input?capture=1")).status == 429
    assert (await browser.get(url + "page")).status == 200


async def test_refused_requests_answer_404_without_a_failed_login(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    browser = await hass_client_no_auth()
    # The notification the proxy must never cause exists in this harness.
    with patch(
        "homeassistant.components.http.ban.gethostbyaddr",
        return_value=("client", [], []),
    ):
        response = await browser.get(
            "/api/config", headers={"Authorization": "Bearer bad"}
        )
    assert response.status == 401
    assert _notified_login_failure(hass)
    persistent_notification.async_dismiss(hass, "http-login")
    assert not _notified_login_failure(hass)

    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    token = url.split("/")[4]
    assert (await browser.get(url + "page")).status == 200

    refused = [
        "/api/panel_assistant/embed/" + "A" * 43 + "/page",
        "/api/panel_assistant/embed/short/page",
        f"/api/panel_assistant/embed/{token}",
    ]
    for path in refused:
        assert (await browser.get(path)).status == 404, path
    # A non-administrator's own credential opens nothing.
    assert (
        await browser.get(
            "/api/panel_assistant/embed/" + "B" * 43 + "/page",
            headers={"Authorization": f"Bearer {hass_read_only_access_token}"},
        )
    ).status == 404

    # A demoted administrator's session no longer passes.
    await hass.auth.async_update_user(hass_admin_user, group_ids=[GROUP_ID_USER])
    assert (await browser.get(url + "page")).status == 404
    admin_group = "system-admin"
    await hass.auth.async_update_user(hass_admin_user, group_ids=[admin_group])
    assert (await browser.get(url + "page")).status == 200

    await hass.async_block_till_done()
    assert not _notified_login_failure(hass)
    assert len(panel.requests) == 2


# Core closes a revoked token's socket from its side, leaving the server's
# heartbeat timer to run out after the test.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_session_whose_sign_in_ended_is_refused(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
) -> None:
    browser = await hass_client_no_auth()
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    assert (await browser.get(url + "page")).status == 200
    session = embed.async_get_embed_sessions(hass)._by_token[url.split("/")[4]]
    refresh_token = hass.auth.async_get_refresh_token(session.refresh_token_id)
    assert refresh_token is not None
    hass.auth.async_remove_refresh_token(refresh_token)
    assert (await browser.get(url + "page")).status == 404
    assert not _notified_login_failure(hass)


async def test_a_session_of_a_non_administrator_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    entry: MockConfigEntry,
) -> None:
    refresh_token = await hass.auth.async_create_refresh_token(
        hass_read_only_user, "http://client/"
    )
    connection = SimpleNamespace(
        user=hass_read_only_user, refresh_token_id=refresh_token.id, subscriptions={}
    )
    session = embed.async_get_embed_sessions(hass).open(
        connection,  # type: ignore[arg-type]
        1,
        entry.entry_id,
        "en",
        "light",
        None,
    )
    browser = await hass_client_no_auth()
    assert (await browser.get(session.url + "page")).status == 404
    assert not _notified_login_failure(hass)


async def test_a_session_of_an_entry_that_is_not_loaded_is_refused(
    hass: HomeAssistant,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    refresh_token = await hass.auth.async_create_refresh_token(
        hass_admin_user, "http://client/"
    )
    connection = SimpleNamespace(
        user=hass_admin_user, refresh_token_id=refresh_token.id, subscriptions={}
    )
    session = embed.async_get_embed_sessions(hass).open(
        connection,  # type: ignore[arg-type]
        1,
        entry.entry_id,
        "en",
        "light",
        None,
    )
    browser = await hass_client_no_auth()
    assert (await browser.get(session.url + "page")).status == 404
    assert not panel.requests


async def test_resume_keeps_the_token_only_briefly_and_for_the_same_sign_in(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(embed.time, "monotonic", lambda: clock[0])
    browser = await hass_client_no_auth()
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    token = url.split("/")[4]
    await ws.close()
    await hass.async_block_till_done()
    # Detached, the frame keeps working while the sidebar reconnects.
    assert (await browser.get(url + "page")).status == 200

    ws = await hass_ws_client(hass)
    clock[0] += embed.RESUME_SECONDS - 1
    _, resumed = await _open_session(ws, entry.entry_id, resume=token)
    assert resumed == url

    await ws.close()
    await hass.async_block_till_done()
    clock[0] += embed.RESUME_SECONDS + 1
    assert (await browser.get(url + "page")).status == 404
    ws = await hass_ws_client(hass)
    _, fresh = await _open_session(ws, entry.entry_id, resume=token)
    assert fresh != url


async def test_resume_from_another_sign_in_gets_a_new_token(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_admin_user: Any,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    await ws.close()
    await hass.async_block_till_done()
    other = await hass.auth.async_create_refresh_token(hass_admin_user, "http://other/")
    ws = await hass_ws_client(hass, hass.auth.async_create_access_token(other))
    _, second = await _open_session(ws, entry.entry_id, resume=url.split("/")[4])
    assert second != url


async def test_removing_the_user_ends_its_sessions(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_admin_user: Any,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    subscription, _ = await _open_session(ws, entry.entry_id)
    sessions = embed.async_get_embed_sessions(hass)
    sessions.end_user(hass_admin_user.id, embed.REASON_USER_REMOVED)
    assert (await _receive(ws))["event"] == {"kind": "closed", "reason": "user_removed"}
    assert not sessions._by_token
    del subscription


# ---------------------------------------------------------------------------
# The integration credential: the key a panel session receives, and the proof
# the proxy signs with it.

PROVEN_LIMIT = 1024 * 1024


def _bind(hass: HomeAssistant, entry: MockConfigEntry, user_id: str) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: user_id}
    )


async def _panel_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    capabilities: list[str],
) -> tuple[Any, dict[str, Any]]:
    """Say hello as the panel's own user, and return its socket and the result."""
    panel_ws = await hass_ws_client(hass, token)
    await panel_ws.send_json_auto_id(HELLO | {"capabilities": capabilities})
    reply = await _receive(panel_ws)
    assert reply["success"], reply
    result: dict[str, Any] = reply["result"]
    return panel_ws, result


async def test_no_proof_once_the_panel_session_has_ended(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    assert (await browser.post(url + "api/v1/config", data=b"a=1")).status == 200
    assert panel.requests[-1]["proofs"] == []

    _bind(hass, entry, hass_read_only_user.id)
    panel_ws, _ = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    await panel_ws.close()
    await hass.async_block_till_done()
    sessions = async_get_sessions(hass)
    ended = sessions.latest(entry.entry_id)
    assert ended is not None and sessions.get(entry.entry_id) is None
    # An ended session discards its key.
    assert (ended.embed_key_id, ended.embed_key) == (None, None)
    # Even one that somehow kept it never signs.
    ended.embed_key_id, ended.embed_key = "0" * 16, b"k" * 32
    assert (await browser.post(url + "api/v1/config", data=b"a=1")).status == 200
    assert (await browser.get(url + "api/v1/status")).status == 200
    assert [r["proofs"] for r in panel.requests[-2:]] == [[], []]


async def test_without_a_key_a_small_body_streams_as_it_always_has(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[int] = []
    original = web.Request.read

    async def counting_read(self: web.Request) -> bytes:
        # The fake panel reads its own echo body; count only the proxy's reads.
        if self.path.startswith("/api/panel_assistant/embed/"):
            reads.append(1)
        return await original(self)

    monkeypatch.setattr(web.Request, "read", counting_read)
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.post(url + "echo", data=b"a=1")
    assert await response.read() == b"a=1"
    assert panel.requests[-1]["proofs"] == []
    assert reads == []


async def test_no_proof_or_key_for_a_panel_that_did_not_offer_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    _bind(hass, entry, hass_read_only_user.id)
    _, result = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state"]
    )
    assert "embed" not in result
    assert "embed_proof" not in result["capabilities"]
    live = async_get_sessions(hass).get(entry.entry_id)
    assert live is not None and live.embed_key is None

    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    assert (await browser.post(url + "api/v1/config", data=b"a=1")).status == 200
    assert panel.requests[-1]["proofs"] == []


async def test_no_proof_for_a_body_over_1_mib_or_of_unknown_length(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    _bind(hass, entry, hass_read_only_user.id)
    await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()

    largest = b"x" * PROVEN_LIMIT
    response = await browser.post(url + "echo", data=largest)
    assert await response.read() == largest
    assert len(panel.requests[-1]["proofs"]) == 1

    large = largest + b"x"
    response = await browser.post(url + "echo", data=large)
    assert await response.read() == large
    assert panel.requests[-1]["proofs"] == []

    async def chunks() -> AsyncGenerator[bytes]:
        for _ in range(3):
            yield b"12345"

    response = await browser.post(url + "echo", data=chunks())
    assert await response.read() == b"123451234512345"
    assert panel.requests[-1]["proofs"] == []


async def test_a_browser_proof_never_reaches_the_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    forged = "v1;k=0123456789abcdef;n=1;u=" + "a" * 32 + ";m=" + "A" * 43
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.post(
        url + "api/v1/config", data=b"a=1", headers={PROOF_HEADER: forged}
    )
    assert response.status == 200
    assert panel.requests[-1]["proofs"] == []

    # Once the panel holds a key, only the proxy's own proof arrives.
    _bind(hass, entry, hass_read_only_user.id)
    await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    response = await browser.post(
        url + "api/v1/config", data=b"a=1", headers={PROOF_HEADER: forged}
    )
    assert response.status == 200
    [proof] = panel.requests[-1]["proofs"]
    assert proof != forged


async def test_the_key_never_reaches_diagnostics_a_repr_or_the_log(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _bind(hass, entry, hass_read_only_user.id)
    panel_ws, result = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    assert (await browser.post(url + "api/v1/config", data=b"a=1")).status == 200
    live = async_get_sessions(hass).get(entry.entry_id)
    assert live is not None
    secrets_ = [
        result["embed"]["key_id"],
        result["embed"]["key"],
        repr(live.embed_key),
        live.embed_key.hex() if live.embed_key else "",
    ]
    assert all(secrets_)
    rendered = [
        repr(live),
        repr(session_diagnostics(hass, entry.entry_id)),
        repr(await async_get_config_entry_diagnostics(hass, entry)),
    ]
    await panel_ws.close()
    await hass.async_block_till_done()
    rendered.append(repr(session_diagnostics(hass, entry.entry_id)))
    rendered.extend(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.panel_assistant")
    )
    for text in rendered:
        for secret in secrets_:
            assert secret not in text


def _proof_for(
    embed_key: dict[str, str], did: str, counter: int, user_id: str, seen: Any
) -> str:
    """Compute a proof from what the panel received, without the integration."""
    key = base64.urlsafe_b64decode(embed_key["key"] + "=")
    body: bytes = seen["body"]
    text = "\n".join(
        (
            "panel-assistant-embed-proof-v1",
            did,
            embed_key["key_id"],
            str(counter),
            user_id,
            seen["method"],
            seen["target"],
            hashlib.sha256(body).hexdigest() if body else "-",
        )
    )
    digest = hmac.new(key, text.encode(), hashlib.sha256).digest()
    mac = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"v1;k={embed_key['key_id']};n={counter};u={user_id};m={mac}"


@pytest.mark.parametrize(
    ("options", "granted"),
    [
        ({"authority": "mqtt"}, []),
        ({}, ["events", "state"]),
        (NATIVE, ["approval", "commands", "events", "state"]),
    ],
    ids=["mqtt", "shadow", "native"],
)
@pytest.mark.parametrize("offered", [True, False])
async def test_hello_grants_a_key_whenever_it_is_offered(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    options: dict[str, str],
    granted: list[str],
    offered: bool,
) -> None:
    """Under every authority, one fresh key per session."""
    native_entry = await _native_setup(
        hass, hass_read_only_user.id, native=True, options=options
    )
    capabilities = ["state", "events", "commands", "approval"]
    if offered:
        capabilities.append("embed_proof")
    panel_ws = await hass_ws_client(hass, hass_read_only_access_token)

    results = []
    for _ in range(2):
        await panel_ws.send_json_auto_id(
            {"type": "panel_assistant/hello"}
            | HELLO_TAIL
            | {"capabilities": capabilities}
        )
        while (reply := await _receive(panel_ws))["type"] != "result":
            pass  # the superseded session's close event
        assert reply["success"], reply
        results.append(reply["result"])

    live = async_get_sessions(hass).get(native_entry.entry_id)
    assert live is not None
    for result in results:
        assert result["capabilities"] == sorted(
            [*granted, "embed_proof"] if offered else granted
        )
    if not offered:
        assert all("embed" not in result for result in results)
        assert (live.embed_key_id, live.embed_key) == (None, None)
        return
    for result in results:
        assert set(result["embed"]) == {"key_id", "key"}
        assert re.fullmatch(r"[0-9a-f]{16}", result["embed"]["key_id"])
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", result["embed"]["key"])
    first, second = (result["embed"] for result in results)
    # A new session replaces the key, and only the live one holds it.
    assert first["key_id"] != second["key_id"] and first["key"] != second["key"]
    assert live.embed_key_id == second["key_id"]
    assert live.embed_key == base64.urlsafe_b64decode(second["key"] + "=")
    assert live.embed_key is not None and len(live.embed_key) == 32
    assert live.embed_counter == 0


async def test_proxied_requests_carry_a_proof_the_panel_can_verify(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    _bind(hass, entry, hass_read_only_user.id)
    _, result = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    target = "/api/v1/config?x=a%2Fb+c&y=%E2%9C%93"

    response = await browser.post(
        # Encoded, so the browser's client sends the target exactly as written.
        URL(url + target[1:], encoded=True),
        data=b"screen_brightness=40",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status == 200
    assert (await browser.get(url + "api/v1/status")).status == 200
    assert (await browser.delete(url + "api/v1/profile")).status == 200

    first, second, third = panel.requests[-3:]
    assert (first["target"], first["body"]) == (target, b"screen_brightness=40")
    assert (second["method"], second["body"]) == ("GET", b"")
    assert third["method"] == "DELETE"
    for counter, seen in enumerate((first, second, third), start=1):
        expected = _proof_for(result["embed"], DID, counter, hass_admin_user.id, seen)
        assert seen["proofs"] == [expected]
    assert second["proofs"][0].count(";") == 4
    live = async_get_sessions(hass).get(entry.entry_id)
    assert live is not None and live.embed_counter == 3


async def test_a_body_of_zero_bytes_signs_a_dash(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    _bind(hass, entry, hass_read_only_user.id)
    _, result = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    assert (await browser.get(url + "api/v1/status")).status == 200
    assert (await browser.post(url + "echo", data=b"")).status == 200
    for counter, seen in enumerate(panel.requests[-2:], start=1):
        assert seen["body"] == b""
        embed_key = result["embed"]
        key = base64.urlsafe_b64decode(embed_key["key"] + "=")
        text = "\n".join(
            (
                "panel-assistant-embed-proof-v1",
                DID,
                embed_key["key_id"],
                str(counter),
                hass_admin_user.id,
                seen["method"],
                seen["target"],
                "-",
            )
        )
        mac = hmac.new(key, text.encode(), hashlib.sha256).digest()
        assert seen["proofs"][0].endswith(
            ";m=" + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
        )


async def test_no_proof_once_the_counter_is_spent(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
) -> None:
    _bind(hass, entry, hass_read_only_user.id)
    _, result = await _panel_hello(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "embed_proof"]
    )
    live = async_get_sessions(hass).get(entry.entry_id)
    assert live is not None
    live.embed_counter = 2**63 - 2
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    assert (await browser.get(url + "api/v1/status")).status == 200
    assert panel.requests[-1]["proofs"] == [
        _proof_for(
            result["embed"], DID, 2**63 - 1, hass_admin_user.id, panel.requests[-1]
        )
    ]
    assert (await browser.get(url + "api/v1/status")).status == 200
    assert panel.requests[-1]["proofs"] == []

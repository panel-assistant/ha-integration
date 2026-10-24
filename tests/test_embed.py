"""The sidebar: embed sessions, the embed view and the relay to a panel's session."""

import asyncio
import dataclasses
import gc
import hashlib
import io
import re
import weakref
import zipfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

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
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.panel_assistant import embed, transport_protocol, ui_relay
from custom_components.panel_assistant.blob import (
    BLOB_LIFETIME_SECONDS,
    async_get_blobs,
)
from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.status import PanelStatus

DID = "d" * 64
HEALTH = PanelHealth(
    version="0.9.8-rc1",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    discovery_id=DID,
    installation_identity=True,
    protocol=(5, 5),
)
STATUS = PanelStatus(warning_count=0, capability_count=0)
RELAY = ["state", "management", "ui_subscribe", "ui_bundle"]
HELLO: dict[str, Any] = {
    "type": "panel_assistant/hello",
    "protocol": {"min": 5, "max": 5},
    "did": DID,
    "app": {"version": "0.9.8-rc1", "version_code": 790},
    "contract_digest": "c" * 64,
    "capabilities": RELAY,
    "channels": [],
}
# What every embed response tells the browser: framed only by Home Assistant, and
# run with an opaque origin, never Home Assistant's own.
ISOLATED = (
    "frame-ancestors 'self'; sandbox allow-scripts allow-forms allow-downloads "
    "allow-modals allow-popups allow-popups-to-escape-sandbox"
)
PAGE = (
    '<!doctype html><html><head><base href="/"><title>x</title></head>'
    '<body data-ui-relay><a href="configure">c</a><base href="/"></body></html>'
)

type WsClientFactory = Callable[..., Awaitable[Any]]
type Answer = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class FakePanel:
    """A panel web server that records anything that reaches it."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.server = TestServer(app, host="127.0.0.1")

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.server.port}"

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(request.raw_path)
        return web.Response(text="ok")


@pytest.fixture
async def panel(socket_enabled: None) -> AsyncGenerator[FakePanel]:
    """Serve a fake panel on loopback, where only a proxy would reach it."""
    fake = FakePanel()
    await fake.server.start_server()
    try:
        yield fake
    finally:
        await fake.server.close()


async def _load_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    executor = SimpleNamespace(
        async_reconcile_entry=AsyncMock(),
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


def blob_registry(hass: HomeAssistant) -> dict[str, Any]:
    """Return the transfers Panel Assistant still holds, bytes included."""
    transfers: dict[str, Any] = async_get_blobs(hass)._transfers
    return transfers


def _notified_login_failure(hass: HomeAssistant) -> bool:
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    return "http-login" in notifications


# ---------------------------------------------------------------------------
# The panel's own session, answering as the app does.


class SessionPanel:
    """A panel holding its session, answering each relayed request by name.

    It moves blobs through the real blob view with its own user's credential.
    """

    def __init__(self, ws: Any, token: str, http: Any) -> None:
        self.ws = ws
        self.token = token
        self.http = http
        self.events: list[dict[str, Any]] = []
        # Home Assistant's answers to what this panel sent (its command_result calls).
        self.results: list[dict[str, Any]] = []
        self.acknowledged = asyncio.Event()
        self.answers: dict[str, Answer] = {"page.info": self.page}
        self.arrived = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def page(self, event: dict[str, Any]) -> dict[str, Any]:
        return {"outcome": "applied", "result": PAGE}

    async def upload(self, event: dict[str, Any], data: bytes) -> dict[str, Any]:
        """Put an answer's bytes where its request said, and name them."""
        url = f"/api/panel_assistant/blob/{event['args']['blob_token']}"
        assert (await self.http.put(url, data=data)).status == 200
        return {
            "url": url,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "type": "application/zip",
        }

    async def run(self) -> None:
        while True:
            message = await self.ws.receive_json()
            if message.get("type") == "result":
                self.results.append(message)
                self.acknowledged.set()
                continue
            if message.get("type") != "event":
                continue
            event = message["event"]
            if event.get("kind") not in ("manage", "command"):
                continue
            self.events.append(event)
            self.arrived.set()
            if not 1 <= event.get("deadline_ms", 10_000) <= 60_000:
                # As the app does: a deadline outside section 5's range is refused.
                await self.send_result(event, outcome="refused", code="invalid_value")
                continue
            answer = self.answers.get(event.get("op") or event.get("channel", ""))
            fields = (
                await answer(event)
                if answer is not None
                else {"outcome": "refused", "code": "unknown_command"}
            )
            await self.send_result(event, **fields)

    async def send_result(self, event: dict[str, Any], **fields: Any) -> None:
        await self.ws.send_json_auto_id(
            {
                "type": "panel_assistant/command_result",
                "session": self.token,
                "command_id": event["command_id"],
                **fields,
            }
        )

    async def update(self, subscription: str, value: Any) -> None:
        await self.ws.send_json_auto_id(
            {
                "type": "panel_assistant/ui_update",
                "session": self.token,
                "subscription": subscription,
                "observations": [{"channel": "perf", "state": "known", "value": value}],
            }
        )

    async def event(self, op: str) -> dict[str, Any]:
        """Wait for the panel to have been asked one op, and return the request."""
        async with asyncio.timeout(5):
            while not (found := [e for e in self.events if e.get("op") == op]):
                self.arrived.clear()
                await self.arrived.wait()
        return found[-1]


@pytest.fixture
async def session_panels() -> AsyncGenerator[list[SessionPanel]]:
    """Stop every session panel a test started."""
    started: list[SessionPanel] = []
    yield started
    for started_panel in started:
        if started_panel.task is not None:
            started_panel.task.cancel()


async def _connect_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
    user_id: str,
    token: str,
    hello: dict[str, Any] = HELLO,
) -> SessionPanel:
    """Bind the panel's account, say hello as the panel and start answering."""
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: user_id}
    )
    ws = await hass_ws_client(hass, token)
    await ws.send_json_auto_id(hello)
    reply = await _receive(ws)
    assert reply["success"], reply
    started = SessionPanel(ws, reply["result"]["session"], await hass_client(token))
    started.task = asyncio.create_task(started.run())
    session_panels.append(started)
    return started


@pytest.fixture
async def live(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
) -> SessionPanel:
    """The entry's panel, holding a session that granted the sidebar relay."""
    return await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
    )


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
        protocol=(5, 5),
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
    unreachable.runtime_data.coordinator.reachable = False
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
        ("alpha", "reachable"),
        # The device name is shown rather than the setup-time title "attic".
        ("attic-unit", "unreachable"),
        ("Bedroom", "not_loaded"),
    ]
    assert set(reply["result"]["panels"][0]) == {
        "entry_id",
        "title",
        "state",
        "device_id",
    }
    assert reply["result"]["panels"][1]["device_id"] is not None
    assert reply["result"]["panels"][2]["device_id"] is None

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), config_entry_id=entry.entry_id
    )
    assert reply["result"]["panels"][0]["device_id"] == device.id
    device_registry.async_update_device(device.id, name_by_user="Custom Name")
    await client.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    reply = await _receive(client)
    assert reply["success"], reply
    renamed = next(
        p for p in reply["result"]["panels"] if p["entry_id"] == entry.entry_id
    )
    assert renamed["title"] == "Custom Name"


@pytest.mark.parametrize("names", [("Alpha", "Zulu"), ("alpha", "Alpha")])
async def test_panels_list_sorts_owner_names_with_entry_id_ties(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    names: tuple[str, str],
) -> None:
    """The sidebar's default and list order follow the names the owner sees."""
    hass.config_entries.async_update_entry(entry, title="zz-host")
    other = MockConfigEntry(
        domain=DOMAIN,
        title="aa-host",
        entry_id="0" * 26,
        data={CONF_ADDRESS: "127.0.0.1:1"},
    )
    other.add_to_hass(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(
                return_value=PanelHealth(
                    version="1.0.0-preview1",
                    panel_id="aa-host",
                    build="1000",
                    config_hash="1a2b3c4d",
                    discovery_id="c" * 64,
                    protocol=(5, 5),
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        await _load_entry(hass, other)
    registry = dr.async_get(hass)
    for panel, name in zip((entry, other), names, strict=True):
        device = registry.async_get_device_by_identifier(
            (DOMAIN, panel.entry_id), config_entry_id=panel.entry_id
        )
        assert device is not None
        registry.async_update_device(device.id, name_by_user=name)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    reply = await _receive(client)
    assert reply["success"], reply
    expected = sorted(
        zip((entry.entry_id, other.entry_id), names, strict=True),
        key=lambda panel: (panel[1].casefold(), panel[0]),
    )
    assert [
        (panel["entry_id"], panel["title"]) for panel in reply["result"]["panels"]
    ] == expected


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


async def test_a_panel_whose_session_cannot_serve_the_sidebar_is_refused(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
) -> None:
    """No session, or one without the bundle, gets the sidebar's failure copy.

    The panel still answers its poll, so it is listed reachable; an embed
    session for it would frame a blank page.
    """
    request = {
        "type": "panel_assistant/embed_session",
        "entry_id": entry.entry_id,
        "language": "en",
        "theme": "light",
    }
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(request)
    reply = await _receive(client)
    assert reply["error"]["code"] == "panel_unavailable"

    await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
        {**HELLO, "capabilities": [c for c in RELAY if c != "ui_bundle"]},
    )
    await client.send_json_auto_id(request)
    reply = await _receive(client)
    assert reply["error"]["code"] == "panel_unavailable"
    assert not embed.async_get_embed_sessions(hass)._by_token


# ---------------------------------------------------------------------------
# The proxy


# ---------------------------------------------------------------------------
# Sessions


async def test_refused_requests_answer_404_without_a_failed_login(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_access_token: str,
    panel: FakePanel,
    entry: MockConfigEntry,
    live: SessionPanel,
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
    assert (await browser.get(url)).status == 200

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
    assert (await browser.get(url)).status == 404
    admin_group = "system-admin"
    await hass.auth.async_update_user(hass_admin_user, group_ids=[admin_group])
    assert (await browser.get(url)).status == 200

    await hass.async_block_till_done()
    assert not _notified_login_failure(hass)
    assert not panel.requests


# Core closes a revoked token's socket from its side, leaving the server's
# heartbeat timer to run out after the test.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_a_session_whose_sign_in_ended_is_refused(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    browser = await hass_client_no_auth()
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    assert (await browser.get(url)).status == 200
    session = embed.async_get_embed_sessions(hass)._by_token[url.split("/")[4]]
    refresh_token = hass.auth.async_get_refresh_token(session.refresh_token_id)
    assert refresh_token is not None
    hass.auth.async_remove_refresh_token(refresh_token)
    assert (await browser.get(url)).status == 404
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
    assert (await browser.get(session.url)).status == 404
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
    assert (await browser.get(session.url)).status == 404
    assert not panel.requests


async def test_resume_keeps_the_token_only_briefly_and_for_the_same_sign_in(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
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
    assert (await browser.get(url)).status == 200

    ws = await hass_ws_client(hass)
    clock[0] += embed.RESUME_SECONDS - 1
    _, resumed = await _open_session(ws, entry.entry_id, resume=token)
    assert resumed == url

    await ws.close()
    await hass.async_block_till_done()
    clock[0] += embed.RESUME_SECONDS + 1
    assert (await browser.get(url)).status == 404
    ws = await hass_ws_client(hass)
    _, fresh = await _open_session(ws, entry.entry_id, resume=token)
    assert fresh != url


async def test_resume_from_another_sign_in_gets_a_new_token(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_admin_user: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
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
    live: SessionPanel,
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


# ---------------------------------------------------------------------------
# The embed view: pages over the session, bundles from the store, no proxy.


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _bundle(session_panel: SessionPanel, files: dict[str, bytes]) -> Answer:
    """Answer ``bundle`` with these files, under whichever build was asked for."""

    async def answer(event: dict[str, Any]) -> dict[str, Any]:
        # Panel Assistant asks for a bundle itself, never as a browser.
        assert "origin" not in event
        reference = await session_panel.upload(event, _zip(files))
        return {
            "outcome": "applied",
            "result": {"blob": reference, "build": event["args"]["build"]},
        }

    return answer


def _set_build(entry: MockConfigEntry, build: str) -> None:
    coordinator = entry.runtime_data.coordinator
    coordinator.data = dataclasses.replace(
        coordinator.data,
        health=dataclasses.replace(coordinator.data.health, build=build),
    )


async def test_a_page_comes_over_the_session_with_its_base_rewritten(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    panel: FakePanel,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    # Home Assistant cannot reach the panel's address; its session is enough.
    entry.runtime_data.coordinator.reachable = False
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    listed = (await _receive(ws))["result"]["panels"]
    assert [(p["entry_id"], p["state"]) for p in listed] == [
        (entry.entry_id, "reachable")
    ]
    # A language the panel's grammar refuses is left out of the switch.
    _, url = await _open_session(ws, entry.entry_id, language="x;hide=setup")
    browser = await hass_client_no_auth()
    response = await browser.get(url + "?lang=fr&ha_lang=de-CH&theme=light&x=1")

    assert response.status == 200
    assert response.content_type == "text/html"
    assert response.headers["Content-Security-Policy"] == ISOLATED
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # Only the first base element, the page's own, is pointed at the view, and
    # the page says from its own load that it is a page: the sidebar shows the
    # frame on that message alone, in Chromium and WebKit alike.
    assert await response.text() == PAGE.replace(
        '<base href="/">', f'<base href="{url}">', 1
    ) + (
        '<script>addEventListener("load",function(){'
        'parent.postMessage({type:"panel-assistant-ready"},"*")})</script>'
    )
    request = await live.event("page.info")
    assert {k: v for k, v in request["args"].items() if k != "blob_token"} == {
        "embed": "v=1;theme=dark;hide=api",
        "lang": "fr",
        "ha_lang": "de-CH",
        "theme": "light",
    }
    assert request["origin"]["user"] == hass_admin_user.id
    assert not panel.requests


async def test_page_events_follow_navigation_inside_the_embed_frame(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """The sidebar learns the current tab even when the frame navigates itself."""

    async def configure(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "applied",
            "result": PAGE.replace(
                '<a href="configure">c</a>',
                '<div class="nav"><a class="active" href="configure">Configure</a>'
                '<a href="logs">Logs</a></div>',
            ),
        }

    async def logs(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "applied",
            "result": PAGE.replace(
                '<a href="configure">c</a>',
                '<div class="nav"><a href="configure">Configure</a>'
                '<a class="active" href="logs">Logs</a></div>',
            ),
        }

    live.answers.update({"page.configure": configure, "page.logs": logs})
    ws = await hass_ws_client(hass)
    subscription, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "configure")
    assert response.status == 200
    assert await _receive(ws) == {
        "id": subscription,
        "type": "event",
        "event": {"kind": "page", "page": "configure"},
    }

    # Follow the frame's own link; no sidebar command changes the selected tab.
    document = await response.text()
    link = re.search(r'<a href="([^"]+)">Logs</a>', document)
    assert link is not None
    response = await browser.get(url + link.group(1))
    assert response.status == 200
    assert await _receive(ws) == {
        "id": subscription,
        "type": "event",
        "event": {"kind": "page", "page": "logs"},
    }


@pytest.mark.parametrize("missing", [False, True])
async def test_unavailable_page_falls_back_to_default_before_reporting_current_tab(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
    missing: bool,
) -> None:
    """A tab hidden or not served by this panel resolves to its default page."""

    async def hidden_setup(event: dict[str, Any]) -> dict[str, Any]:
        if missing:
            return {"outcome": "refused", "code": "unknown_command", "status": 404}
        return {
            "outcome": "applied",
            "result": PAGE.replace(
                '<a href="configure">c</a>',
                '<div class="nav"><a href="">Dashboard</a>'
                '<a href="configure">Configure</a></div>'
                '<main><a class="active" href="setup">Unrelated active link</a></main>',
            ),
        }

    live.answers["page.setup"] = hidden_setup
    ws = await hass_ws_client(hass)
    subscription, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "setup?theme=light", allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"] == url
    # Ordering against a WS command proves the redirect emitted no page event.
    await ws.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    assert (await _receive(ws))["type"] == "result"

    response = await browser.get(response.headers["Location"])
    assert response.status == 200
    assert await _receive(ws) == {
        "id": subscription,
        "type": "event",
        "event": {"kind": "page", "page": ""},
    }


@pytest.mark.parametrize("status", [None, 503])
async def test_failed_page_does_not_replace_the_current_tab(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
    status: int | None,
) -> None:
    """Only a successfully rendered page changes the sidebar's current tab."""

    async def failed(event: dict[str, Any]) -> dict[str, Any]:
        answer: dict[str, Any] = {
            "outcome": "refused",
            "code": "failed",
            "result": PAGE,
        }
        if status is not None:
            answer["status"] = status
        return answer

    live.answers["page.configure"] = failed
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    response = await browser.get(url + "configure")
    assert response.status == (status or 502)
    await ws.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    assert (await _receive(ws))["type"] == "result"


@pytest.mark.parametrize("theme", ["light", "dark"])
async def test_a_page_asked_for_while_the_panel_session_is_down_asks_again(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
    theme: str,
) -> None:
    """An open sidebar is not left blank while the panel restarts after an update."""
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id, theme=theme)
    browser = await hass_client_no_auth()
    assert (await browser.get(url)).status == 200
    assert live.task is not None
    live.task.cancel()
    await live.ws.close()
    await hass.async_block_till_done()

    response = await browser.get(url)

    assert response.status == 503
    assert response.content_type == "text/html"
    document = await response.text()
    assert '<meta http-equiv="refresh" content="3">' in document
    assert '<meta name="panel-assistant-wait" content="true">' in document
    # The sidebar cannot read the sandboxed document, so it says what it is.
    assert (
        '<script>parent.postMessage({type:"panel-assistant-wait"},"*")</script>'
        in document
    )
    assert f'<meta name="color-scheme" content="{theme}">' in document
    assert "panel-assistant-ready" not in document


async def test_each_panel_is_served_the_bundle_of_its_own_build(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    panel: FakePanel,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    other_did = "e" * 64
    other = MockConfigEntry(
        domain=DOMAIN, title="Hall", data={CONF_ADDRESS: "127.0.0.1:1"}
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(
            return_value=dataclasses.replace(
                HEALTH, panel_id="beta", build="2000", discovery_id=other_did
            )
        ),
    ):
        await _load_entry(hass, other)
    other_panel = await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        other,
        hass_read_only_user.id,
        hass_read_only_access_token,
        HELLO | {"did": other_did},
    )
    live.answers["bundle"] = _bundle(
        live, {"info.js": b"one", "assets/app.css": b"a{}", "icon.svg": b"<svg/>"}
    )
    other_panel.answers["bundle"] = _bundle(other_panel, {"info.js": b"two"})
    ws = await hass_ws_client(hass)
    _, first = await _open_session(ws, entry.entry_id)
    _, second = await _open_session(ws, other.entry_id)
    browser = await hass_client_no_auth()

    # A page load asks for many files at once; the bundle is fetched once.
    responses = await asyncio.gather(
        *(browser.get(first + "info.js") for _ in range(3))
    )
    assert [await response.read() for response in responses] == [b"one"] * 3
    assert responses[0].content_type == "text/javascript"
    css = await browser.get(first + "assets/app.css")
    assert (css.content_type, await css.read()) == ("text/css", b"a{}")
    assert (await browser.get(first + "icon.svg")).content_type == "image/svg+xml"
    second_file = await browser.get(second + "info.js")
    assert await second_file.read() == b"two"
    asked = [e["args"]["build"] for e in live.events if e["op"] == "bundle"]
    assert asked == ["1000"]
    assert [e["args"]["build"] for e in other_panel.events if e["op"] == "bundle"] == [
        "2000"
    ]

    for path in (
        "assets/missing.js",
        "api/v1/status",
        "api/v1/config",
        "health",
        "openapi.json",
        "blob/" + "A" * 43,
    ):
        assert (await browser.get(first + path)).status == 404, path

    # A new build is fetched again; a build no panel reports any more is dropped,
    # so returning to it fetches it once more.
    _set_build(entry, "1001")
    assert (await browser.get(first + "info.js")).status == 200
    _set_build(entry, "1000")
    assert (await browser.get(first + "info.js")).status == 200
    asked = [e["args"]["build"] for e in live.events if e["op"] == "bundle"]
    assert asked == ["1000", "1001", "1000"]
    assert not panel.requests


async def test_a_panel_answering_a_newer_build_is_fetched_once(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """A panel updated since the last poll answers its new build; it is kept."""

    async def newer(event: dict[str, Any]) -> dict[str, Any]:
        reference = await live.upload(event, _zip({"info.js": b"new"}))
        return {"outcome": "applied", "result": {"blob": reference, "build": "2000"}}

    live.answers["bundle"] = newer
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    for _ in range(2):
        response = await browser.get(url + "info.js")
        assert (response.status, await response.read()) == (200, b"new")
    assert [e["args"]["build"] for e in live.events if e["op"] == "bundle"] == ["1000"]
    # The poll catches up: the same bundle serves it without another fetch.
    _set_build(entry, "2000")
    assert await (await browser.get(url + "info.js")).read() == b"new"
    assert len([e for e in live.events if e["op"] == "bundle"]) == 1


async def test_a_panel_answering_another_panels_build_never_serves_that_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_client_no_auth: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """A session's bundle serves only its own panel, whatever build it names."""
    other_did = "e" * 64
    other = MockConfigEntry(
        domain=DOMAIN, title="Hall", data={CONF_ADDRESS: "127.0.0.1:1"}
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(
            return_value=dataclasses.replace(
                HEALTH, panel_id="beta", build="2000", discovery_id=other_did
            )
        ),
    ):
        await _load_entry(hass, other)
    other_panel = await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        other,
        hass_read_only_user.id,
        hass_read_only_access_token,
        HELLO | {"did": other_did},
    )
    other_panel.answers["bundle"] = _bundle(other_panel, {"info.js": b"honest"})

    async def claims_2000(event: dict[str, Any]) -> dict[str, Any]:
        reference = await live.upload(event, _zip({"info.js": b"planted"}))
        return {"outcome": "applied", "result": {"blob": reference, "build": "2000"}}

    live.answers["bundle"] = claims_2000
    ws = await hass_ws_client(hass)
    _, first = await _open_session(ws, entry.entry_id)
    _, second = await _open_session(ws, other.entry_id)
    browser = await hass_client_no_auth()

    assert await (await browser.get(second + "info.js")).read() == b"honest"
    assert await (await browser.get(first + "info.js")).read() == b"planted"
    assert await (await browser.get(second + "info.js")).read() == b"honest"


async def test_every_embed_response_runs_with_an_opaque_origin_and_transfers_still_work(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """The panel's pages cannot reach Home Assistant's origin, yet move files.

    The live session is held by a non-administrator, so its pages are not trusted
    with the administrator's origin. A sandboxed document's requests carry
    ``Origin: null``, so a download and an upload need cross-origin answers.
    """
    live.answers["bundle"] = _bundle(live, {"info.js": b"one"})
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    blobs = async_get_blobs(hass)
    download, _ = blobs.download(
        entry.entry_id, b"export", "application/zip", hass_admin_user.id
    )
    upload, _ = blobs.upload(entry.entry_id, 1024, hass_admin_user.id)
    null = {"Origin": "null"}

    page = await browser.get(url, headers=null)
    bundle_file = await browser.get(url + "info.js", headers=null)
    missing = await browser.get(url + "nothing", headers=null)
    got = await browser.get(f"{url}blob/{download}", headers=null)
    preflight = await browser.options(
        f"{url}blob/{upload}",
        headers=null
        | {
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    put = await browser.put(
        f"{url}blob/{upload}",
        data=b"app",
        headers=null | {"Content-Type": "application/zip"},
    )

    for response in (page, bundle_file, missing, got, put):
        assert response.headers["Content-Security-Policy"] == ISOLATED
    sandbox = ISOLATED.split("sandbox ", 1)[1].split()
    assert "allow-same-origin" not in sandbox
    assert {"allow-scripts", "allow-downloads"} <= set(sandbox)
    assert (got.status, await got.read()) == (200, b"export")
    assert preflight.status == 200
    assert "PUT" in preflight.headers["Access-Control-Allow-Methods"]
    assert put.status == 200
    for response in (got, preflight, put):
        assert response.headers["Access-Control-Allow-Origin"] in ("*", "null")


async def test_a_bundle_gets_two_minutes_and_a_failed_one_is_not_asked_again_at_once(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """A slow panel gets time, and a frame's many files never multiply its uploads."""

    async def too_slow(event: dict[str, Any]) -> dict[str, Any]:
        return {"outcome": "failed", "code": "failed"}

    live.answers["bundle"] = too_slow
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    browser = await hass_client_no_auth()
    for name in ("info.js", "info.css", "assets/transport.js", "assets/i18n.js"):
        assert (await browser.get(url + name)).status == 404
    asked = [e for e in live.events if e["op"] == "bundle"]
    assert len(asked) == 1
    assert asked[0]["deadline_ms"] == transport_protocol.MAX_COMMAND_DEADLINE_MS
    # After the pause the panel is asked again, and its answer serves the frame.
    live.answers["bundle"] = _bundle(live, {"info.js": b"js"})
    with patch.object(
        ui_relay.time,
        "monotonic",
        return_value=ui_relay.time.monotonic() + ui_relay.BUNDLE_RETRY_SECONDS + 1,
    ):
        response = await browser.get(url + "info.js")
    assert (response.status, await response.read()) == (200, b"js")
    assert len([e for e in live.events if e["op"] == "bundle"]) == 2


# ---------------------------------------------------------------------------
# The relay: panel_assistant/ui.


async def _ui(ws: Any, entry_id: str, request: dict[str, Any]) -> dict[str, Any]:
    await ws.send_json_auto_id(
        {"type": "panel_assistant/ui", "entry_id": entry_id, "request": request}
    )
    return await _receive(ws)


async def _applied(event: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": "applied"}


async def test_the_relay_refuses_a_non_administrator(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    live.answers["perf"] = _applied
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    reply = await _ui(
        ws, entry.entry_id, {"kind": "manage", "command_id": "r1", "op": "perf"}
    )
    assert reply["error"]["code"] == "unauthorized"
    admin = await hass_ws_client(hass)
    await _ui(admin, entry.entry_id, {"kind": "manage", "command_id": "r2", "op": "x"})
    assert [e.get("op") for e in live.events] == ["x"]


async def test_a_relayed_request_names_its_caller_and_returns_the_answer(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_admin_user: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    async def revisions(event: dict[str, Any]) -> dict[str, Any]:
        # A route whose body is a list answers with that list.
        return {"outcome": "applied", "result": [{"id": 1}, {"id": 2}]}

    async def perf(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "refused",
            "code": "invalid_value",
            "status": 422,
            "headers": {"x-ha-paneld-input-route": "tap"},
            "result": {"error": "bad"},
        }

    live.answers.update(
        {"perf": perf, "screen": _applied, "config.revisions": revisions}
    )
    ws = await hass_ws_client(hass)
    forged = {"user": "someone-else", "connection": "theirs"}
    reply = await _ui(
        ws,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "r1",
            "op": "perf",
            "args": {"hours": 1},
            "origin": forged,
            "deadline_ms": 5000,
        },
    )
    assert reply["result"] == {
        "command_id": "r1",
        "outcome": "refused",
        "code": "invalid_value",
        "status": 422,
        "headers": {"x-ha-paneld-input-route": "tap"},
        "result": {"error": "bad"},
    }
    request = await live.event("perf")
    assert request["kind"] == "manage"
    assert request["origin"]["user"] == hass_admin_user.id
    assert request["origin"]["connection"] not in ("", "theirs")
    assert set(request["origin"]) == {"user", "connection"}
    # The panel's own command IDs never collide between two browsers' requests.
    assert request["command_id"] != "r1"
    assert request["args"]["hours"] == 1
    assert request["deadline_ms"] == 5000

    reply = await _ui(
        ws,
        entry.entry_id,
        {
            "kind": "command",
            "command_id": "r2",
            "channel": "screen",
            "value": True,
            "origin": forged,
        },
    )
    assert reply["result"] == {"command_id": "r2", "outcome": "applied"}
    command = next(e for e in live.events if e.get("channel") == "screen")
    assert (command["kind"], command["value"]) == ("command", True)
    assert command["origin"] == request["origin"]

    reply = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "r3", "op": "config.revisions"},
    )
    assert reply["result"] == {
        "command_id": "r3",
        "outcome": "applied",
        "result": [{"id": 1}, {"id": 2}],
    }


async def test_a_relayed_request_waits_through_its_approval(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    live: SessionPanel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)

    async def reboot(event: dict[str, Any]) -> dict[str, Any]:
        await live.send_result(event, outcome="pending_approval", interim=True)
        # Approved on the panel's screen well after the request's deadline.
        await asyncio.sleep(1.0)
        return {"outcome": "applied", "result": {"ok": True}}

    live.answers["device.reboot"] = reboot
    ws = await hass_ws_client(hass)
    reply = await _ui(
        ws,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "r1",
            "op": "device.reboot",
            "deadline_ms": 500,
        },
    )
    assert reply["result"] == {
        "command_id": "r1",
        "outcome": "applied",
        "result": {"ok": True},
    }


async def test_a_late_approval_answer_after_the_browser_stopped_waiting_is_accepted(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    live: SessionPanel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel's interim and final answers are taken after the wait ended."""
    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)
    gone = asyncio.Event()

    async def reboot(event: dict[str, Any]) -> dict[str, Any]:
        await gone.wait()
        await live.send_result(event, outcome="pending_approval", interim=True)
        return {"outcome": "applied", "result": {"ok": True}}

    live.answers["device.reboot"] = reboot
    ws = await hass_ws_client(hass)
    reply = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "r1", "op": "device.reboot", "deadline_ms": 1},
    )
    assert reply["success"] is False
    answered = len(live.results)
    gone.set()
    async with asyncio.timeout(5):
        while len(live.results) < answered + 2:
            live.acknowledged.clear()
            await live.acknowledged.wait()
    # Both late answers were acknowledged; neither raised in Home Assistant.
    assert [r["success"] for r in live.results[answered:]] == [True, True]


async def test_without_a_session_that_serves_the_sidebar_the_request_rejects(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
) -> None:
    ws = await hass_ws_client(hass)
    request = {"kind": "manage", "command_id": "r1", "op": "perf"}
    assert (await _ui(ws, "missing", request))["error"]["code"] == "not_found"
    assert (await _ui(ws, entry.entry_id, request))["error"][
        "code"
    ] == "panel_unavailable"
    await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
        HELLO | {"capabilities": ["state", "management"]},
    )
    assert (await _ui(ws, entry.entry_id, request))["error"][
        "code"
    ] == "panel_unavailable"


async def _subscribe(ws: Any, entry_id: str, command_id: str) -> None:
    await ws.send_json_auto_id(
        {
            "type": "panel_assistant/ui",
            "entry_id": entry_id,
            "request": {
                "kind": "manage",
                "command_id": command_id,
                "op": "subscribe",
                "args": {"family": "perf"},
            },
        }
    )
    assert (await _receive(ws))["success"]
    applied = await _receive(ws)
    # The applied answer is the first event, under the page's own ID.
    assert applied["event"] == {
        "command_id": command_id,
        "outcome": "applied",
        "result": {"subscription": command_id},
    }


async def _subscribed(event: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": "applied", "result": {"subscription": event["command_id"]}}


async def test_an_update_reaches_only_its_subscriber_until_the_browser_leaves(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    live.answers.update(
        {"subscribe": _subscribed, "unsubscribe": _applied, "perf": _applied}
    )
    subscriber = await hass_ws_client(hass)
    other = await hass_ws_client(hass)
    await _subscribe(subscriber, entry.entry_id, "s1")
    request = await live.event("subscribe")
    # The other browser holds a subscription of its own.
    await _subscribe(other, entry.entry_id, "s2")

    await live.update(request["command_id"], {"cpu": 1})
    assert (await _receive(subscriber))["event"] == {
        "subscription": "s1",
        "observations": [{"channel": "perf", "state": "known", "value": {"cpu": 1}}],
    }
    # Nothing reached the other browser: its next message answers its next request.
    reply = await _ui(
        other, entry.entry_id, {"kind": "manage", "command_id": "p", "op": "perf"}
    )
    assert reply["result"] == {"command_id": "p", "outcome": "applied"}

    await subscriber.close()
    ended = await live.event("unsubscribe")
    assert ended["args"]["subscription"] == request["command_id"]
    assert ended["origin"] == request["origin"]


async def test_a_subscription_ends_with_the_panel_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    live.answers["subscribe"] = _subscribed
    subscriber = await hass_ws_client(hass)
    await _subscribe(subscriber, entry.entry_id, "s1")
    assert live.task is not None
    live.task.cancel()
    await live.ws.close()
    assert (await _receive(subscriber))["event"] == {"closed": True}


async def test_a_large_answer_is_reachable_once_at_its_blob(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    data = b"x" * 300_000

    async def export(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "applied",
            "result": {"blob": await live.upload(event, data)},
        }

    live.answers["config.export"] = export
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    reply = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "e1", "op": "config.export"},
    )
    reference = reply["result"]["result"]["blob"]
    assert re.fullmatch(r"blob/[A-Za-z0-9_-]{43}", reference["url"])
    assert reference["size"] == len(data)
    assert reference["sha256"] == hashlib.sha256(data).hexdigest()

    browser = await hass_client_no_auth()
    first = await browser.get(url + reference["url"])
    assert first.status == 200
    assert await first.read() == data
    assert (await browser.get(url + reference["url"])).status == 404
    # Its bytes are not held once sent: the registry is where they would stay.
    assert reference["url"].removeprefix("blob/") not in blob_registry(hass)


async def test_an_unused_transfer_is_forgotten_when_it_expires(
    hass: HomeAssistant, entry: MockConfigEntry, hass_admin_user: Any
) -> None:
    """Expired bytes go without waiting for another transfer to be minted."""
    blobs = async_get_blobs(hass)
    blobs.download(entry.entry_id, b"x" * 1024, "text/plain", hass_admin_user.id)
    blobs.upload(entry.entry_id, 1024, hass_admin_user.id)
    assert len(blob_registry(hass)) == 2
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=BLOB_LIFETIME_SECONDS + 1)
    )
    await hass.async_block_till_done()
    assert not blob_registry(hass)


async def test_a_taken_upload_is_not_held_once_used(
    hass: HomeAssistant, entry: MockConfigEntry, hass_admin_user: Any
) -> None:
    """A browser upload handed on to its request leaves Home Assistant's memory."""
    blobs = async_get_blobs(hass)
    token, received = blobs.upload(entry.entry_id, 1024, hass_admin_user.id)
    received.set_result(b"y" * 1024)
    watched = weakref.ref(received)
    assert blobs.take(token, entry.entry_id, hass_admin_user.id) == b"y" * 1024
    del received
    gc.collect()
    assert watched() is None


async def test_a_blob_typed_with_parameters_keeps_its_media_type(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """A large text answer, typed with a charset, reaches the page as text."""
    data = b"y" * 300_000

    async def profile(event: dict[str, Any]) -> dict[str, Any]:
        reference = await live.upload(event, data)
        reference["type"] = "text/plain; charset=utf-8"
        return {"outcome": "applied", "result": {"blob": reference}}

    live.answers["profile.read"] = profile
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    reply = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "t1", "op": "profile.read"},
    )
    reference = reply["result"]["result"]["blob"]
    assert reference["type"] == "text/plain"
    response = await (await hass_client_no_auth()).get(url + reference["url"])
    assert response.status == 200
    assert response.content_type == "text/plain"
    assert await response.read() == data


async def test_a_browser_transfer_belongs_to_one_administrator_and_one_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_client_no_auth: Any,
    hass_admin_user: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    """Another administrator, another panel and the panel itself are refused."""
    other_did = "e" * 64
    other = MockConfigEntry(
        domain=DOMAIN, title="Hall", data={CONF_ADDRESS: "127.0.0.1:1"}
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(
            return_value=dataclasses.replace(
                HEALTH, panel_id="beta", discovery_id=other_did
            )
        ),
    ):
        await _load_entry(hass, other)
    other_panel = await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        other,
        hass_read_only_user.id,
        hass_read_only_access_token,
        HELLO | {"did": other_did},
    )
    second_admin = await hass.auth.async_create_user(
        "Second", group_ids=["system-admin"]
    )
    second_token = await hass.auth.async_create_refresh_token(
        second_admin, "http://second/"
    )
    mine = await hass_ws_client(hass)
    _, own = await _open_session(mine, entry.entry_id)
    _, own_elsewhere = await _open_session(mine, other.entry_id)
    theirs = await hass_ws_client(
        hass, hass.auth.async_create_access_token(second_token)
    )
    _, their_view = await _open_session(theirs, entry.entry_id)
    browser = await hass_client_no_auth()
    panel_http = live.http

    blobs = async_get_blobs(hass)
    download, _ = blobs.download(
        entry.entry_id, b"export", "application/zip", hass_admin_user.id
    )
    upload, _ = blobs.upload(entry.entry_id, 1024, hass_admin_user.id)
    for view in (their_view, own_elsewhere):
        assert (await browser.get(f"{view}blob/{download}")).status == 404
        assert (await browser.put(f"{view}blob/{upload}", data=b"x")).status == 404
    # The panel's own route never reaches a browser's transfer.
    assert (await panel_http.get(f"/api/panel_assistant/blob/{download}")).status == 404
    assert (
        await panel_http.put(f"/api/panel_assistant/blob/{upload}", data=b"x")
    ).status == 404
    # Still there for the one browser it was minted for.
    assert (await browser.get(f"{own}blob/{download}")).status == 200
    assert (await browser.put(f"{own}blob/{upload}", data=b"app")).status == 200

    live.answers["install.apk"] = _applied
    other_panel.answers["install.apk"] = _applied
    install = {
        "kind": "manage",
        "command_id": "i1",
        "op": "install.apk",
        "args": {"blob": {"url": f"blob/{upload}"}},
    }
    for ws, entry_id in ((theirs, entry.entry_id), (mine, other.entry_id)):
        reply = await _ui(ws, entry_id, install)
        assert reply["result"]["outcome"] == "refused", entry_id
    assert not [
        e for p in (live, other_panel) for e in p.events if e.get("op") == "install.apk"
    ]
    reply = await _ui(mine, entry.entry_id, install)
    assert reply["result"]["outcome"] == "applied"


async def _drained(hass: HomeAssistant) -> None:
    """Wait for Panel Assistant to hold no transfer at all."""
    for _ in range(500):
        if not blob_registry(hass):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"still held: {list(blob_registry(hass))}")


async def test_a_long_relayed_request_keeps_its_slot_until_the_panel_answers(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,
    live: SessionPanel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser stops waiting; the panel still uploads, then answers."""
    ws = await hass_ws_client(hass)
    await _open_session(ws, entry.entry_id)
    # A long operation keeps its two-minute wait here, while the panel is given the
    # longest start deadline it accepts (it refuses one past 60 s).
    live.answers["config.import"] = _applied
    reply = await _ui(
        ws,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "c1",
            "op": "config.import",
            "deadline_ms": 120_000,
        },
    )
    assert reply["result"]["outcome"] == "applied"
    assert (await live.event("config.import"))["deadline_ms"] == 60_000

    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)
    go = asyncio.Event()
    uploaded: list[int] = []

    async def backup(event: dict[str, Any]) -> dict[str, Any]:
        await go.wait()
        url = f"/api/panel_assistant/blob/{event['args']['blob_token']}"
        uploaded.append((await live.http.put(url, data=b"archive")).status)
        return {"outcome": "applied", "result": {"blob": {"url": url}}}

    live.answers["backup"] = backup
    reply = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "b1", "op": "backup", "deadline_ms": 1},
    )
    assert reply["error"]["code"] == "panel_unavailable"
    go.set()
    await _drained(hass)
    assert uploaded == [200]


async def test_a_lent_download_outlives_the_browser_wait_until_the_session_ends(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow install still fetches the browser's file; the session's end frees it."""
    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    upload = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "u1", "op": "blob.upload", "args": {}},
    )
    target = upload["result"]["result"]["url"]
    browser = await hass_client_no_auth()
    assert (await browser.put(url + target, data=b"app")).status == 200
    go = asyncio.Event()
    fetched: list[int] = []
    asked = asyncio.Event()

    async def install(event: dict[str, Any]) -> dict[str, Any]:
        await go.wait()
        fetched.append((await live.http.get(event["args"]["blob"]["url"])).status)
        asked.set()
        await asyncio.Event().wait()  # never answers
        raise AssertionError

    live.answers["install.apk"] = install
    reply = await _ui(
        ws,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "i1",
            "op": "install.apk",
            "args": {"blob": {"url": target}},
            "deadline_ms": 1,
        },
    )
    assert reply["error"]["code"] == "panel_unavailable"
    go.set()
    async with asyncio.timeout(5):
        await asked.wait()
    assert fetched == [200]
    assert blob_registry(hass)
    await live.ws.close()
    await _drained(hass)


async def test_an_upload_reaches_the_panel_as_its_own_download(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client_no_auth: Any,
    entry: MockConfigEntry,
    live: SessionPanel,
) -> None:
    fetched: list[Any] = []

    async def install(event: dict[str, Any]) -> dict[str, Any]:
        reference = event["args"]["blob"]
        response = await live.http.get(reference["url"])
        fetched.extend([reference, response.status, await response.read()])
        fetched.append((await live.http.get(reference["url"])).status)
        return {"outcome": "applied", "result": {"ok": True}}

    live.answers["install.apk"] = install
    ws = await hass_ws_client(hass)
    _, url = await _open_session(ws, entry.entry_id)
    data = b"app bytes"
    claimed = {
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "type": "application/vnd.android.package-archive",
    }
    upload = await _ui(
        ws,
        entry.entry_id,
        {"kind": "manage", "command_id": "u1", "op": "blob.upload", "args": claimed},
    )
    target = upload["result"]["result"]["url"]
    assert re.fullmatch(r"blob/[A-Za-z0-9_-]{43}", target)
    # Answered by Panel Assistant itself.
    assert not [e for e in live.events if e.get("op") == "blob.upload"]
    browser = await hass_client_no_auth()
    assert (await browser.put(url + target, data=data)).status == 200
    assert (await browser.put(url + target, data=data)).status == 404

    install_request = {
        "kind": "manage",
        "command_id": "i1",
        "op": "install.apk",
        "args": {"blob": {**claimed, "url": target}},
    }
    reply = await _ui(ws, entry.entry_id, install_request)
    assert reply["result"]["outcome"] == "applied"
    reference, status, body, again = fetched
    assert reference["url"].startswith("/api/panel_assistant/blob/")
    assert {k: reference[k] for k in claimed} == claimed
    assert (status, body, again) == (200, data, 404)
    # Used once: naming it again is refused without reaching the panel.
    reply = await _ui(ws, entry.entry_id, {**install_request, "command_id": "i2"})
    assert reply["result"] == {
        "command_id": "i2",
        "outcome": "refused",
        "code": "invalid_value",
    }
    assert len([e for e in live.events if e.get("op") == "install.apk"]) == 1


@pytest.mark.parametrize(
    "mode", ["left_before_on_time_answer", "left_before_timeout", "left_after_timeout"]
)
async def test_late_subscription_is_unsubscribed_on_its_original_session(
    hass, hass_ws_client, entry, live, monkeypatch, mode
):
    from custom_components.panel_assistant.transport_sessions import async_get_sessions

    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)
    go = asyncio.Event()

    async def delayed(event):
        await go.wait()
        return await _subscribed(event)

    live.answers.update({"subscribe": delayed, "unsubscribe": _applied})
    browser = await hass_ws_client(hass)
    deadline = 10000 if mode == "left_before_on_time_answer" else 25
    await browser.send_json_auto_id(
        {
            "type": "panel_assistant/ui",
            "entry_id": entry.entry_id,
            "request": {
                "kind": "manage",
                "command_id": "independent",
                "op": "subscribe",
                "args": {"family": "perf"},
                "deadline_ms": deadline,
            },
        }
    )
    assert (await _receive(browser))["success"]
    request = await live.event("subscribe")
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    if mode.startswith("left_before"):
        await browser.close()
    if mode != "left_before_on_time_answer":
        async with asyncio.timeout(3):
            while not session.late:  # noqa: ASYNC110 - production timeout state has no event hook
                await asyncio.sleep(0.001)
        if mode == "left_after_timeout":
            assert (await _receive(browser))["event"]["outcome"] == "failed"
            await browser.close()
    go.set()
    ended = await live.event("unsubscribe")
    assert ended["args"]["subscription"] == request["command_id"]
    assert ended["origin"] == request["origin"]
    async with asyncio.timeout(3):
        while session.late:  # noqa: ASYNC110 - observe final protocol settlement
            await asyncio.sleep(0.001)
    assert not session.ui_relays


async def test_timed_out_subscription_cleanup_survives_late_map_eviction(
    hass, hass_ws_client, entry, live, monkeypatch
):
    monkeypatch.setattr(ui_relay, "ANSWER_GRACE_SECONDS", 0)
    release = asyncio.Event()
    remote = set()

    async def subscribe(event):
        await release.wait()
        remote.add(event["command_id"])
        return await _subscribed(event)

    async def unsubscribe(event):
        remote.discard(event["args"]["subscription"])
        return await _applied(event)

    live.answers.update({"subscribe": subscribe, "unsubscribe": unsubscribe})
    browser = await hass_ws_client(hass)
    for index in range(18):
        await browser.send_json_auto_id(
            {
                "type": "panel_assistant/ui",
                "entry_id": entry.entry_id,
                "request": {
                    "kind": "manage",
                    "command_id": f"old-{index}",
                    "op": "subscribe",
                    "args": {"family": "perf"},
                    "deadline_ms": 25,
                },
            }
        )
        # The immediate subscription result and eventual failure share the socket.
    results = 0
    async with asyncio.timeout(5):
        while results < 18:
            message = await _receive(browser)
            if (
                message.get("type") == "event"
                and message.get("event", {}).get("outcome") == "failed"
            ):
                results += 1
    from custom_components.panel_assistant.transport_sessions import async_get_sessions

    session = async_get_sessions(hass).get(entry.entry_id)
    assert len(session.late) == 16
    release.set()
    async with asyncio.timeout(5):
        while (  # noqa: ASYNC110 - bounded protocol-observation fixture
            len([event for event in live.events if event.get("op") == "unsubscribe"])
            < 18
        ):
            await asyncio.sleep(0.001)
    await hass.async_block_till_done()
    assert not remote
    assert not session.ui_relays
    await browser.close()


@pytest.mark.parametrize("same_version", [True, False])
@pytest.mark.parametrize("reachable", [True, False])
@pytest.mark.parametrize("health_current", [True, False])
async def test_returning_page_and_assets_match_before_update_health_refresh(
    hass,
    hass_ws_client,
    hass_client,
    hass_client_no_auth,
    hass_read_only_user,
    hass_read_only_access_token,
    session_panels,
    entry,
    monkeypatch,
    reachable,
    health_current,
    same_version,
):
    """Use real hello/embed and update wait while its health I/O is pending."""
    from custom_components.panel_assistant.transport_sessions import async_get_sessions
    from custom_components.panel_assistant.update_progress import UpdateProgress

    coordinator = entry.runtime_data.coordinator
    coordinator.data = dataclasses.replace(
        coordinator.data,
        health=dataclasses.replace(
            coordinator.data.health,
            version="1.0.0-rc1",
            version_code=2159,
        ),
    )
    connect_args = (
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
    )
    old = await _connect_panel(
        *connect_args, HELLO | {"app": {"version": "1.0.0-rc1", "version_code": 2159}}
    )
    old.answers["bundle"] = _bundle(
        old, {"info.js": b"old-script", "info.css": b"old-style"}
    )
    admin = await hass_ws_client(hass)
    _, first = await _open_session(admin, entry.entry_id)
    browser = await hass_client_no_auth()
    assert await (await browser.get(first + "info.js")).read() == b"old-script"
    old.task.cancel()
    await old.ws.close()
    await hass.async_block_till_done()
    async with asyncio.timeout(3):
        while coordinator.connected:  # noqa: ASYNC110 - observe real session teardown
            await asyncio.sleep(0.001)
    coordinator.reachable = reachable
    progress = UpdateProgress(
        entry.entry_id, coordinator, SimpleNamespace(), lambda: None, lambda: 2159
    )
    progress.begin(hass, "1.0.0-rc1", "1.0.0-rc1")
    progress.enter("installing")
    progress.hold_through_restart(hass)
    sessions = async_get_sessions(hass)
    if not reachable:
        assert sessions.restart_notice(entry.entry_id) is not None
    entered, release = asyncio.Event(), asyncio.Event()
    new_snapshot = dataclasses.replace(
        coordinator.data,
        health=dataclasses.replace(
            coordinator.data.health,
            build="2000",
            version_code=2159 if same_version else 2160,
        ),
    )

    async def delayed_snapshot(session):
        entered.set()
        await release.wait()
        return new_snapshot

    monkeypatch.setattr(coordinator, "_async_read_session", delayed_snapshot)
    monkeypatch.setattr(
        coordinator, "_async_probe_address", AsyncMock(return_value=True)
    )
    returning = await _connect_panel(
        *connect_args,
        HELLO
        | {
            "app": {
                "version": "1.0.0-rc1",
                "version_code": 2159 if same_version else 2160,
            }
        },
    )
    assert sessions.restart_notice(entry.entry_id) is None
    if health_current:
        coordinator.async_set_updated_data(new_snapshot)

    async def new_bundle(event):
        blob = await returning.upload(
            event, _zip({"info.js": b"new-script", "info.css": b"new-style"})
        )
        return {"outcome": "applied", "result": {"blob": blob, "build": "2000"}}

    returning.answers["bundle"] = new_bundle

    async def page(event):
        return {
            "outcome": "applied",
            "result": PAGE.replace("<body ", '<body data-build="2000" ').replace(
                "</body>",
                '<script src="info.js"></script>'
                '<link href="info.css" rel="stylesheet"></body>',
            ),
        }

    returning.answers["page.info"] = page
    waiter = asyncio.create_task(
        progress.async_wait_for_build(
            hass, SimpleNamespace(descriptor=None, version="1.0.0-rc1"), ("1000", None)
        )
    )
    await asyncio.wait_for(entered.wait(), 3)
    _, current = await _open_session(admin, entry.entry_id)
    try:
        html = await (await browser.get(current)).text()
        assert 'data-build="2000"' in html
        resources = re.findall(r'(?:src|href)="((?:info\.js|info\.css))"', html)
        assert resources == ["info.js", "info.css"]
        responses = await asyncio.gather(
            *(browser.get(current + path) for path in resources)
        )
        assert [await response.read() for response in responses] == [
            b"new-script",
            b"new-style",
        ]
        assert sum(e.get("op") == "bundle" for e in returning.events) == 1
        assert not waiter.done()
        release.set()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert await (await browser.get(current + "info.js")).read() == b"new-script"
        assert sum(e.get("op") == "bundle" for e in returning.events) == 1
    finally:
        waiter.cancel()
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        progress.end()


@pytest.mark.parametrize("warm_returning_bundle", [False, True])
async def test_same_install_reconnect_reuses_its_page_bundle(
    hass,
    hass_ws_client,
    hass_client,
    hass_client_no_auth,
    hass_read_only_user,
    hass_read_only_access_token,
    session_panels,
    entry,
    live,
    warm_returning_bundle,
):
    """Reconnect between the returning HTML and its assets must keep its token."""
    live.answers["bundle"] = _bundle(
        live, {"info.js": b"old-js", "info.css": b"old-css"}
    )
    admin = await hass_ws_client(hass)
    _, url = await _open_session(admin, entry.entry_id)
    browser = await hass_client_no_auth()
    assert await (await browser.get(url + "info.js")).read() == b"old-js"
    connect_args = (
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
        HELLO,
    )

    async def bundle(event):
        blob = await current.upload(
            event, _zip({"info.js": b"new-js", "info.css": b"new-css"})
        )
        return {"outcome": "applied", "result": {"blob": blob, "build": "2000"}}

    async def page(_event):
        return {
            "outcome": "applied",
            "result": PAGE.replace("<body ", '<body data-build="2000" ').replace(
                "</body>",
                '<script src="info.js"></script><link href="info.css"></body>',
            ),
        }

    live.task.cancel()
    await live.ws.close()
    await hass.async_block_till_done()
    current = await _connect_panel(*connect_args)
    current.answers.update({"page.info": page, "bundle": bundle})
    html = await (await browser.get(url)).text()
    resources = re.findall(r'(?:src|href)="((?:info\.js|info\.css))"', html)
    assert resources == ["info.js", "info.css"]
    if warm_returning_bundle:
        assert await (await browser.get(url + "info.js")).read() == b"new-js"
    current.task.cancel()
    await current.ws.close()
    await hass.async_block_till_done()
    current = await _connect_panel(*connect_args)
    current.answers["bundle"] = bundle
    # Use the document already returned; no fresh page request can repair the hint.
    responses = await asyncio.gather(*(browser.get(url + path) for path in resources))
    assert [await response.read() for response in responses] == [b"new-js", b"new-css"]
    asks = [
        event["args"]["build"] for event in current.events if event["op"] == "bundle"
    ]
    assert asks == ([] if warm_returning_bundle else ["2000"])


async def test_a_retired_sessions_delayed_bundle_cannot_replace_current_page_assets(
    hass,
    hass_ws_client,
    hass_client,
    hass_client_no_auth,
    hass_read_only_user,
    hass_read_only_access_token,
    session_panels,
    entry,
    live,
    monkeypatch,
):
    """An old upload finishing unzip after reconnect cannot restore its cache alias."""
    import threading

    parsing = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    unzip = ui_relay._unzip

    def delayed(data):
        files = unzip(data)
        if files and files.get("info.js") == b"old":
            loop.call_soon_threadsafe(parsing.set)
            assert release.wait(10)
        return files

    monkeypatch.setattr(ui_relay, "_unzip", delayed)
    live.answers["bundle"] = _bundle(live, {"info.js": b"old"})
    admin = await hass_ws_client(hass)
    _, url = await _open_session(admin, entry.entry_id)
    browser = await hass_client_no_auth()
    old_request = asyncio.create_task(browser.get(url + "info.js"))
    try:
        await asyncio.wait_for(parsing.wait(), 3)
        live.task.cancel()
        await live.ws.close()
        returning = await _connect_panel(
            hass,
            hass_ws_client,
            hass_client,
            session_panels,
            entry,
            hass_read_only_user.id,
            hass_read_only_access_token,
            HELLO,
        )

        async def page(_event):
            return {
                "outcome": "applied",
                "result": PAGE.replace("<body ", '<body data-build="2000" '),
            }

        async def bundle(event):
            blob = await returning.upload(event, _zip({"info.js": b"new"}))
            return {"outcome": "applied", "result": {"blob": blob, "build": "2000"}}

        returning.answers.update({"page.info": page, "bundle": bundle})
        assert (await browser.get(url)).status == 200
        assert await (await browser.get(url + "info.js")).read() == b"new"
        release.set()
        assert (await old_request).status == 404
        assert await (await browser.get(url + "info.js")).read() == b"new"
        assert sum(event["op"] == "bundle" for event in returning.events) == 1
    finally:
        release.set()
        await asyncio.gather(old_request, return_exceptions=True)

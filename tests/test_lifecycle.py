"""Core lifecycle notices travel through authenticated production sessions."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import WSMsgType, web
from aiohttp._websocket.writer import WebSocketWriter
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN

from .test_transport import (
    HEALTH,
    OTHER_DID,
    WsClientFactory,
    _hello,
    _send,
    entry,
)


@pytest.fixture(autouse=True)
async def _shutdown_socket_cleanup() -> AsyncGenerator[None]:
    """Core's forced socket cancellation leaves aiohttp heartbeat timers behind."""
    sockets: list[web.WebSocketResponse] = []
    prepare = web.WebSocketResponse.prepare

    async def capture(self: web.WebSocketResponse, request: Any) -> Any:
        sockets.append(self)
        return await prepare(self, request)

    with patch.object(web.WebSocketResponse, "prepare", capture):
        yield
    for socket in sockets:
        socket._cancel_heartbeat()


@pytest.mark.parametrize("failed_sender", [False, True])
async def test_shutdown_broadcasts_unknown_to_every_authority(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    failed_sender: bool,
) -> None:
    """Stop's empty event and a failed service intent cannot invent a reason."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_DID,
        data={
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
            "address": "other.local",
        },
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(return_value=replace(HEALTH, discovery_id=OTHER_DID)),
    ):
        assert await hass.config_entries.async_setup(other.entry_id)
    clients = [
        await hass_ws_client(hass, hass_read_only_access_token) for _ in range(2)
    ]
    for client, did in zip(clients, (entry.unique_id, OTHER_DID), strict=True):
        reply = await _send(client, _hello(did=did))
        assert reply["success"]
        assert reply["result"]["lifecycle"]["phase"] == "ready"
        assert reply["result"]["protocol"] == 3
    hass.bus.async_fire(
        EVENT_CALL_SERVICE, {"domain": "hassio", "service": "host_reboot"}
    )
    await hass.async_block_till_done()
    if failed_sender:
        from custom_components.panel_assistant.transport import async_get_sessions

        session = async_get_sessions(hass).get(entry.entry_id)
        assert session is not None
        session.connection.send_message = lambda _message: (_ for _ in ()).throw(
            RuntimeError("closed writer")
        )
    await asyncio.wait_for(hass.async_stop(force=True), 2)
    for client in clients[1:] if failed_sender else clients:
        event = await client.receive_json()
        assert event["event"] == {
            "kind": "lifecycle",
            "phase": "shutting_down",
            "reason": "unknown",
            "elapsed_ms": 0,
            "expected_ms": None,
        }
        await client.close()


async def test_blocked_writer_never_holds_core_shutdown(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The authenticated writer attempts the notice but shutdown ignores delivery."""
    entered = asyncio.Event()
    never = asyncio.Event()
    send_frame = WebSocketWriter.send_frame

    async def blocked(
        self: WebSocketWriter, message: bytes, opcode: int, compress: int | None = None
    ) -> None:
        if (
            opcode == WSMsgType.TEXT
            and b'"kind"' in message
            and b'"lifecycle"' in message
        ):
            entered.set()
            await never.wait()
        await send_frame(self, message, opcode, compress)

    with patch.object(WebSocketWriter, "send_frame", blocked):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        assert (await _send(client, _hello()))["success"]
        await asyncio.wait_for(hass.async_stop(force=True), 2)
    assert entered.is_set()
    assert not never.is_set()
    await client.close()


async def test_loaded_stamp_starting_ready_and_next_stop_measurement(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Early authentication stays starting; STARTED supplies measured timing."""
    hass.set_state(CoreState.starting)
    now = dt_util.utcnow().timestamp()
    hass_storage[f"{DOMAIN}.lifecycle"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.lifecycle",
        "data": {
            "pending": {"reason": "restart", "at": now - 20},
            "history": {"restart": [{"at": now - 100, "duration_ms": 30000}]},
        },
    }
    async for config_entry in entry.__wrapped__(hass, hass_read_only_user):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        reply = await _send(client, _hello())
        assert reply["success"]
        lifecycle = reply["result"]["lifecycle"]
        assert lifecycle["phase"] == "starting"
        assert lifecycle["reason"] == "restart"
        assert lifecycle["elapsed_ms"] >= 20000
        assert lifecycle["expected_ms"] == 30000
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        ready = (await client.receive_json())["event"]
        assert ready["phase"] == "ready"
        assert 24000 <= ready["expected_ms"] <= 26000
        hass.data["hassio_jobs_coordinator"] = SimpleNamespace(
            last_update_success=True,
            current_jobs=[
                SimpleNamespace(name="home_assistant_core_restart", done=False),
            ],
        )
        await asyncio.wait_for(hass.async_stop(force=True), 2)
        stop = (await client.receive_json())["event"]
        assert stop["reason"] == "restart"
        assert stop["expected_ms"] == ready["expected_ms"]
        saved = hass_storage[f"{DOMAIN}.lifecycle"]["data"]
        assert saved["pending"]["reason"] == "restart"
        assert len(saved["history"]["restart"]) == 2
        await client.close()
        assert config_entry.entry_id


@pytest.mark.parametrize(
    "reason,done,expected",
    [
        ("home_assistant_core_update", False, "core_update"),
        ("home_assistant_core_restart", False, "restart"),
        ("home_assistant_core_update", True, "unknown"),
        ("home_assistant_core_update", None, "unknown"),
    ],
)
async def test_only_unfinished_cached_jobs_classify_shutdown(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    reason: str,
    done: bool | None,
    expected: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    assert (await _send(client, _hello()))["success"]
    hass.data["hassio_jobs_coordinator"] = SimpleNamespace(
        last_update_success=True,
        current_jobs=[
            SimpleNamespace(name=reason, done=done),
        ],
    )
    await asyncio.wait_for(hass.async_stop(force=True), 2)
    event = (await client.receive_json())["event"]
    assert event["reason"] == expected
    assert event["expected_ms"] is None
    await client.close()


@pytest.mark.parametrize(
    "stamp,samples",
    [
        (-7200, [{"duration_ms": 30000, "at_offset": -100}]),
        (100, [{"duration_ms": 30000, "at_offset": -100}]),
        (
            -20,
            [
                {"duration_ms": 0, "at_offset": -100},
                {"duration_ms": True, "at_offset": -100},
                {"duration_ms": 4000000, "at_offset": -100},
                {"duration_ms": 30000, "at_offset": -3000000},
                {"duration_ms": 30000, "at_offset": 100},
            ],
        ),
    ],
)
async def test_invalid_or_stale_clock_samples_are_not_advertised(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    stamp: int,
    samples: list[dict[str, Any]],
) -> None:
    hass.set_state(CoreState.starting)
    now = dt_util.utcnow().timestamp()
    hass_storage[f"{DOMAIN}.lifecycle"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.lifecycle",
        "data": {
            "pending": {"reason": "restart", "at": now + stamp},
            "history": {
                "restart": [
                    {
                        "duration_ms": sample["duration_ms"],
                        "at": now + sample["at_offset"],
                    }
                    for sample in samples
                ]
            },
        },
    }
    async for _config_entry in entry.__wrapped__(hass, hass_read_only_user):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        reply = await _send(client, _hello())
        assert reply["success"]
        lifecycle = reply["result"]["lifecycle"]
        assert lifecycle["expected_ms"] is None
        if stamp != -20:
            assert lifecycle["reason"] == "unknown"
            assert lifecycle["elapsed_ms"] is None
        await client.close()

"""Continuous logs through the authenticated panel socket and real destinations."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import pytest
from aiohttp import web
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.test_transport import (
    DID,
    WsClientFactory,
    _hello,
    _receive,
    _send,
)
from tests.test_transport import (
    entry as entry,
)

RECORD = {
    "timestamp": "2026-10-25T12:34:56.123Z",
    "source": "app",
    "priority": 5,
    "tag": "Transport",
    "pid": 1234,
    "message": "A redacted warning",
}


@pytest.fixture(autouse=True)
def log_policy(hass_storage: dict[str, Any]) -> dict[str, Any]:
    """Seed the installation store before Core sets the integration up."""
    policy = {
        "destination": {"protocol": "home_assistant", "host": "", "port": 514},
        "sources": {"app": 5},
    }
    hass_storage["panel_assistant.panel_logs"] = {
        "version": 1,
        "minor_version": 1,
        "key": "panel_assistant.panel_logs",
        "data": policy,
    }
    return policy


async def _logs(client: Any, **changes: Any) -> dict[str, Any]:
    return await _send(client, _hello(capabilities=["logs"], **changes))


def _batch(token: str, *records: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "panel_assistant/report_log",
        "session": token,
        "records": list(records),
        "dropped": 0,
    }


async def test_old_panel_still_connects_without_logs(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    result = await _send(client, _hello())
    assert result["success"]
    assert "logs" not in result["result"]["capabilities"]
    assert "logs" not in result["result"]
    refused = await _send(client, _batch(result["result"]["session"], RECORD))
    assert refused["error"]["code"] == "logs_not_granted"


async def test_granted_floor_and_ha_logger_preserve_event_identity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    grant = await _logs(client)
    assert grant["success"]
    assert grant["result"].get("logs") == {"sources": {"app": 5}}
    with caplog.at_level(
        logging.DEBUG, logger="custom_components.panel_assistant.panel"
    ):
        answer = await _send(
            client,
            _batch(
                grant["result"]["session"],
                {**RECORD, "priority": 4, "message": "too verbose"},
                {**RECORD, "source": "system", "message": "system is off"},
                RECORD,
                {**RECORD, "priority": 7, "message": "fatal"},
            ),
        )
    assert answer["success"], answer
    assert answer["result"] == {"accepted": 2, "dropped": 0}
    written = [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.panel_assistant.panel.")
    ]
    assert [r.name for r in written] == [
        f"custom_components.panel_assistant.panel.{DID}"
    ] * 2
    assert [r.levelno for r in written] == [logging.WARNING, logging.CRITICAL]
    assert written[0].panel_timestamp == RECORD["timestamp"]
    assert written[0].created == datetime.fromisoformat(RECORD["timestamp"]).timestamp()
    assert written[0].panel_source == "app"
    assert written[0].panel_tag == "Transport"
    assert written[0].panel_pid == 1234
    assert "alpha" in written[0].getMessage()
    assert RECORD["message"] in written[0].getMessage()
    assert all("too verbose" not in r.getMessage() for r in written)
    assert all("system is off" not in r.getMessage() for r in written)


@pytest.mark.parametrize("also_home_assistant", [False, True])
@pytest.mark.parametrize("protocol", ["udp", "tcp", "http"])
async def test_forwarder_delivers_current_format_to_local_receiver(
    hass: HomeAssistant,
    log_policy: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    protocol: str,
    also_home_assistant: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    received: asyncio.Future[bytes] = hass.loop.create_future()

    def accept(data: bytes) -> None:
        if not received.done():
            received.set_result(data)

    class Receiver(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: Any) -> None:
            accept(data)

    async def tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accept(await reader.readline())
        writer.close()
        await writer.wait_closed()

    async def http(req: web.Request) -> web.Response:
        assert req.content_type == "application/x-ndjson"
        accept(await req.read())
        return web.Response()

    if protocol == "udp":
        transport, _ = await hass.loop.create_datagram_endpoint(
            Receiver, local_addr=("127.0.0.1", 0)
        )
        port = transport.get_extra_info("sockname")[1]
    elif protocol == "tcp":
        server = await asyncio.start_server(tcp, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
    else:
        app = web.Application()
        app.router.add_post("/", http)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
    log_policy["destination"] = {
        "protocol": protocol,
        "host": "127.0.0.1",
        "port": port,
    }
    log_policy["also_home_assistant"] = also_home_assistant
    try:
        client = await hass_ws_client(hass, hass_read_only_access_token)
        grant = await _logs(
            client,
            app={
                "version": "0.9.8-rc1",
                "version_code": 790,
                "package": "io.panelassistant.android",
            },
        )
        assert grant["success"]
        assert "logs" in grant["result"]["capabilities"]
        from custom_components.panel_assistant.panel_logs import async_get_log_router

        await async_get_log_router(hass).async_configure(log_policy)
        await _receive(client)  # New policy closes the old grant.
        grant = await _logs(
            client,
            app={
                "version": "0.9.8-rc1",
                "version_code": 790,
                "package": "io.panelassistant.android",
            },
        )
        response = await _send(client, _batch(grant["result"]["session"], RECORD))
        assert response["success"], response
        async with asyncio.timeout(5):
            payload = await received
        written = [r for r in caplog.records if r.name.endswith(f".panel.{DID}")]
        assert len(written) == int(also_home_assistant)
        if protocol == "http":
            assert json.loads(payload) == {
                "timestamp": RECORD["timestamp"],
                "host": DID,
                "app": "ha-paneld",
                "versionCode": 790,
                "package": "io.panelassistant.android",
                "message": f"app W/Transport(1234): {RECORD['message']}",
            }
        else:
            assert (
                payload.decode() == f"<12>1 {RECORD['timestamp']} {DID} ha-paneld - - "
                '[hapaneld@32473 versionCode="790" '
                'package="io.panelassistant.android"] '
                f"app W/Transport(1234): {RECORD['message']}\n"
            )
    finally:
        if protocol == "udp":
            transport.close()
        elif protocol == "tcp":
            server.close()
            await server.wait_closed()
        else:
            await runner.cleanup()


@pytest.mark.parametrize(
    "change",
    [
        {"priority": True},
        {"priority": 8},
        {"pid": -1},
        {"timestamp": "not a time"},
        {"timestamp": "2026-10-25T12:34:56"},
        {"message": "é" * 4097},
        {"tag": "t" * 129},
        {"source": "webView"},
    ],
)
async def test_invalid_record_never_reaches_ha_logging(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
    change: dict[str, Any],
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    grant = await _logs(client)
    assert "logs" in grant["result"]["capabilities"]
    response = await _send(
        client, _batch(grant["result"]["session"], {**RECORD, **change})
    )
    assert response["error"]["code"] == "invalid_format"
    assert not any(
        r.name.startswith("custom_components.panel_assistant.panel.")
        for r in caplog.records
    )


async def test_only_current_connection_can_report_logs(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    grant = await _logs(client)
    assert "logs" in grant["result"]["capabilities"]
    other = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(other, _batch(grant["result"]["session"], RECORD))
    assert response["error"]["code"] == "session_unknown"
    renewed = await _logs(other)
    assert renewed["success"]
    await _receive(client)
    response = await _send(client, _batch(grant["result"]["session"], RECORD))
    assert response["error"]["code"] == "session_unknown"


async def test_one_outstanding_batch_and_receiver_failure_do_not_queue(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A slow receiver blocks this panel's next batch, then releases custody."""
    from custom_components.panel_assistant.panel_logs import async_get_log_router

    entered = asyncio.Event()
    release = asyncio.Event()
    deliveries: list[bytes] = []

    async def receiver(req: web.Request) -> web.Response:
        deliveries.append(await req.read())
        entered.set()
        await release.wait()
        return web.Response(status=503)

    app = web.Application()
    app.router.add_post("/logs", receiver)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        await async_get_log_router(hass).async_configure(
            {
                "destination": {
                    "protocol": "http",
                    "host": f"http://127.0.0.1:{runner.addresses[0][1]}/logs",
                    "port": 514,
                },
                "sources": {"app": 5},
            }
        )
        client = await hass_ws_client(hass, hass_read_only_access_token)
        grant = await _logs(client)
        batch = {**_batch(grant["result"]["session"], RECORD), "dropped": 17}
        await client.send_json({**batch, "id": 2})
        async with asyncio.timeout(5):
            await entered.wait()
        await client.send_json({**batch, "id": 3})
        refused = await _receive(client)
        assert refused["id"] == 3
        assert refused["error"]["code"] == "log_busy"
        release.set()
        failed = await _receive(client)
        assert failed["id"] == 2
        assert failed["error"]["code"] == "log_delivery_failed"
        await client.send_json({**batch, "id": 4})
        again = await _receive(client)
        assert again["error"]["code"] == "log_delivery_failed"
        await hass.async_block_till_done()
        assert len(deliveries) == 2  # No queued or retried duplicate.
        lines = [json.loads(line) for line in deliveries[0].splitlines()]
        assert len(lines) == 2
        assert lines[1]["message"] == (
            "app W/PanelLogs(0): Panel dropped 17 log records before this batch."
        )
    finally:
        release.set()
        await runner.cleanup()


async def test_drop_notice_reaches_the_selected_ha_logger(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    grant = await _logs(client)
    response = await _send(
        client, {**_batch(grant["result"]["session"]), "dropped": 17}
    )
    assert response["result"] == {"accepted": 0, "dropped": 17}
    records = [r for r in caplog.records if r.name.endswith(f".panel.{DID}")]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "17 log records" in records[0].getMessage()

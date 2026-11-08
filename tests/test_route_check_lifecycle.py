"""Update route checks release their TCP peers and never hold up setup."""

import asyncio
from types import SimpleNamespace

import pytest
from adb_shell.adb_device_async import AdbDeviceAsync

from custom_components.panel_assistant import adb_protocol, provisioning, update_route
from tests.test_update_adb_route import route as route


@pytest.mark.parametrize("close_failure", ["hang", "error"])
async def test_route_check_closes_tcp_when_device_close_fails(
    route, monkeypatch, close_failure, socket_enabled
):
    """The update entity's actual ADB probe must leave no live TCP peer."""
    await _check_route_tcp_close(route, monkeypatch, close_failure)


async def test_route_check_graceful_tcp_close_still_completes(
    route, monkeypatch, socket_enabled
):
    """The library's ordinary close still completes and releases its TCP peer."""
    await _check_route_tcp_close(route, monkeypatch, "graceful")


async def _check_route_tcp_close(route, monkeypatch, close_failure):
    peers = []
    writers = []
    devices = []

    async def peer(reader, writer):
        closed = asyncio.get_running_loop().create_future()
        peers.append(closed)
        writers.append(writer)
        try:
            await reader.read()
            closed.set_result(True)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport_type = adb_protocol._BoundedTcpTransportAsync

    class Device(AdbDeviceAsync):
        def __init__(self, transport, **kwargs):
            super().__init__(transport, **kwargs)
            self.close_started = False
            devices.append(self)

        async def connect(self, **_kwargs):
            transport = self._io_manager._transport
            await transport.connect(1)
            writers.append(transport._writer)
            return False

        async def close(self):
            self.close_started = True
            if close_failure == "error":
                raise OSError("peer no longer answers")
            if close_failure == "graceful":
                await super().close()
                return
            await asyncio.Event().wait()

    monkeypatch.setattr(adb_protocol, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(provisioning, "AdbDeviceAsync", Device)
    monkeypatch.setattr(
        provisioning,
        "_BoundedTcpTransportAsync",
        lambda *_args: transport_type("127.0.0.1", port),
    )
    monkeypatch.setattr(
        update_route,
        "async_probe_install_target",
        provisioning.async_probe_install_target,
    )
    try:
        await route.entity._async_refresh_route()
        assert devices and all(device.close_started for device in devices)
        assert len(peers) == len(devices)
        # Waiting for EOF on the other side proves teardown, even when the
        # graceful device close never reached its own transport close call.
        async with asyncio.timeout(1):
            await asyncio.gather(*(asyncio.shield(peer) for peer in peers))
    finally:
        for writer in writers:
            writer.transport.abort()
        server.close()
        await server.wait_closed()
        await asyncio.sleep(0)


async def test_hung_route_check_does_not_delay_entity_setup(route, hass, monkeypatch):
    """Bootstrap's task drain finishes while the entity's route probe is hung."""
    route.entity.entity_id = "update.panel_assistant"
    route.entity.platform = SimpleNamespace(platform_name="update", config_entry=None)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hung_route():
        started.set()
        await release.wait()
        return None, None, None

    monkeypatch.setattr(route.entity, "_async_install_route", hung_route)
    try:
        await route.entity.async_internal_added_to_hass()
        await route.entity.async_added_to_hass()
        await asyncio.wait_for(started.wait(), 1)
        async with asyncio.timeout(0.5):
            await hass.async_block_till_done()
        assert not release.is_set()
    finally:
        release.set()
        await route.entity.async_remove()
        await hass.async_block_till_done(wait_background_tasks=True)

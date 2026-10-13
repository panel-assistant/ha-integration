"""The ADB wire protocol's transport bounds, tested on their own."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from adb_shell import constants as adb_constants
from adb_shell.exceptions import AdbConnectionError
from adb_shell.transport.tcp_transport_async import TcpTransportAsync

from custom_components.panel_assistant import adb_protocol


async def test_bounded_transport_honors_valid_phase_read_and_write_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, float]] = []

    async def bulk_read(
        _self: TcpTransportAsync, _numbytes: int, wait_seconds: float
    ) -> bytes:
        observed.append(("read", wait_seconds))
        return b"x"

    async def bulk_write(
        _self: TcpTransportAsync, data: bytes | bytearray, wait_seconds: float
    ) -> int:
        observed.append(("write", wait_seconds))
        return len(data)

    monkeypatch.setattr(TcpTransportAsync, "bulk_read", bulk_read)
    monkeypatch.setattr(TcpTransportAsync, "bulk_write", bulk_write)
    transport = adb_protocol._BoundedTcpTransportAsync("192.168.1.23", 5555)

    assert await transport.bulk_read(1, 23.0) == b"x"
    assert await transport.bulk_write(bytearray(b"x"), 179.0) == 1
    assert observed == [("read", 23.0), ("write", 179.0)]


@pytest.mark.parametrize("wait_seconds", [None, 0, -1, 180.1, True, float("inf")])
async def test_bounded_transport_rejects_unbounded_timeout_values(
    wait_seconds: object,
) -> None:
    transport = adb_protocol._BoundedTcpTransportAsync("192.168.1.23", 5555)

    with pytest.raises(adb_protocol._UnsafeAdbPacket):
        await transport.bulk_read(1, wait_seconds)  # type: ignore[arg-type]


async def test_connection_has_cumulative_read_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many valid-sized reads cannot fill adb-shell's unbounded packet store."""
    packet = adb_protocol._MAX_ADB_PACKET_BYTES
    transport = adb_protocol._BoundedTcpTransportAsync("panel.local", 5555)
    underlying_read = AsyncMock(side_effect=lambda numbytes, _timeout: b"x" * numbytes)
    monkeypatch.setattr(TcpTransportAsync, "bulk_read", underlying_read)

    budget = adb_protocol._MAX_CONNECTION_READ_BYTES
    for _index in range(budget // packet):
        assert len(await transport.bulk_read(packet, 5.0)) == packet
    with pytest.raises(adb_protocol._UnsafeAdbPacket):
        await transport.bulk_read(packet, 5.0)

    assert underlying_read.await_count == budget // packet


async def test_connection_budget_counts_received_not_requested_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fragmented reads consume only bytes that actually reached the transport."""
    transport = adb_protocol._BoundedTcpTransportAsync("panel.local", 5555)
    call_count = 0

    async def _fragmented_read(numbytes: int, _timeout: float) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count <= 4:
            return b"x"
        return b"y" * numbytes

    underlying_read = AsyncMock(side_effect=_fragmented_read)
    monkeypatch.setattr(TcpTransportAsync, "bulk_read", underlying_read)

    for _index in range(4):
        assert (
            await transport.bulk_read(adb_protocol._MAX_ADB_PACKET_BYTES, 5.0) == b"x"
        )
    try:
        final = await transport.bulk_read(adb_protocol._MAX_ADB_PACKET_BYTES, 5.0)
    except adb_protocol._UnsafeAdbPacket as err:
        raise AssertionError(
            "fragmented reads exhausted the actual-byte budget"
        ) from err

    assert len(final) == adb_protocol._MAX_ADB_PACKET_BYTES
    assert underlying_read.await_args_list[-1].args == (
        adb_protocol._MAX_ADB_PACKET_BYTES,
        5.0,
    )


async def test_connection_eof_fails_without_busy_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A positive-length read at EOF raises instead of spinning in adb-shell."""
    transport = adb_protocol._BoundedTcpTransportAsync("panel.local", 5555)
    underlying_read = AsyncMock(return_value=b"")
    monkeypatch.setattr(TcpTransportAsync, "bulk_read", underlying_read)

    with pytest.raises(AdbConnectionError, match="closed"):
        await transport.bulk_read(adb_constants.MESSAGE_SIZE, 5.0)

    underlying_read.assert_awaited_once_with(adb_constants.MESSAGE_SIZE, 5.0)

"""Add panel says which port failed when the panel app's port 8888 does not answer.

On a segmented network Home Assistant's connections to a running panel's port
8888 can be refused by a firewall, or taken and never answered. Adding that
panel used to tell the owner the panel answered and to turn on Android Debug
Bridge, which plays no part in adding a running panel. These tests drive the
config flow against real sockets standing in for port 8888.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_socket import socket_allow_hosts

from custom_components.panel_assistant import client as client_module
from custom_components.panel_assistant import config_flow
from custom_components.panel_assistant.client import PanelAddress, normalize_address
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
    PinnedPanelTarget,
)

from .config_flow_helpers import _start_step

FLOW = "custom_components.panel_assistant.config_flow"
TYPED = "10.77.2.20"
# Captured before conftest replaces it, so these tests probe the real socket.
REAL_HOST_ANSWERS = config_flow._async_host_answers
ADB_UNREACHABLE = SimpleNamespace(
    state=SimpleNamespace(value="adb_unreachable"),
    model=None,
    serial=None,
    primary_abi=None,
    android_sdk=None,
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@asynccontextmanager
async def _refusing() -> AsyncIterator[int]:
    """A loopback port nothing listens on: every connection is refused."""
    yield _free_port()


@asynccontextmanager
async def _stalling(first_bytes: bytes = b"") -> AsyncIterator[int]:
    """Take the connection, read the request, send ``first_bytes``, then stall."""
    held: list[asyncio.StreamWriter] = []

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)
        # Home Assistant gives up and closes first; that is not a test failure.
        with suppress(ConnectionError):
            await reader.read(1024)
            if first_bytes:
                writer.write(first_bytes)
                await writer.drain()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


def _answering_not_panel() -> Callable[[], object]:
    return lambda: _stalling(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nhello\n")


@pytest.fixture
def loopback(socket_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real sockets on loopback, with short timeouts so a stall ends quickly."""
    socket_allow_hosts(["127.0.0.1"])
    monkeypatch.setattr(client_module, "DEFAULT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(config_flow, "_HOST_PROBE_SECONDS", 0.5)
    monkeypatch.setattr(config_flow, "_async_host_answers", REAL_HOST_ANSWERS)


async def _add(hass: HomeAssistant, port: int, caplog) -> tuple[dict, list[str]]:
    """Add the panel at TYPED, with its port 8888 standing in at ``port``."""
    pinned = PanelAddress("127.0.0.1", port)
    target = PinnedPanelTarget(original=normalize_address(TYPED), pinned=pinned)
    manager = SimpleNamespace(async_find_active=AsyncMock(return_value=None))
    with (
        patch(f"{FLOW}.async_pin_install_target", AsyncMock(return_value=target)),
        patch(f"{FLOW}.async_get_install_job_manager", AsyncMock(return_value=manager)),
        patch(
            f"{FLOW}.async_probe_install_target",
            AsyncMock(return_value=ADB_UNREACHABLE),
        ),
        caplog.at_level(logging.WARNING),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: TYPED}
        )
    refusals = [
        record.getMessage()
        for record in caplog.records
        if "Panel Assistant setup refused" in record.getMessage()
    ]
    return result, refusals


@pytest.mark.parametrize(
    ("stand_in", "error", "outcome"),
    [
        (_refusing, "app_port_refused", "refused"),
        (_stalling, "app_port_no_answer", "timed_out"),
        (
            lambda: _stalling(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"),
            "app_port_no_answer",
            "timed_out",
        ),
        (_answering_not_panel(), "app_port_not_panel", "invalid_response"),
    ],
    ids=["refused", "accepts-then-stalls", "banner-then-stalls", "not-the-app"],
)
async def test_add_panel_names_the_port_that_did_not_answer(
    hass: HomeAssistant, loopback, caplog, stand_in, error: str, outcome: str
) -> None:
    """Port 8888 failing is named in the form and in the log, not blamed on ADB."""
    async with stand_in() as port:
        result, refusals = await _add(hass, port, caplog)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert refusals == [
        f"Panel Assistant setup refused: step=add_panel error={error} "
        f"panel={TYPED} cause=port_8888={outcome} adb_state=adb_unreachable "
        f"pinned=127.0.0.1:{port}"
    ]


async def test_nothing_at_the_address_is_still_told_so(
    hass: HomeAssistant, loopback, caplog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A health read that times out where nothing listens keeps its own text."""
    monkeypatch.setattr(
        config_flow, "_async_host_answers", AsyncMock(return_value=False)
    )
    async with _stalling() as port:
        result, refusals = await _add(hass, port, caplog)

    assert result["errors"] == {"base": "panel_unreachable"}
    assert refusals[0].endswith(
        f"cause=port_8888=timed_out adb_state=adb_unreachable pinned=127.0.0.1:{port}"
    )


async def test_a_name_that_does_not_resolve_logs_what_was_typed(
    hass: HomeAssistant, caplog
) -> None:
    """Nothing was pinned, so the log names the address as typed."""
    refusal = InstallNetworkError(InstallNetworkErrorCode.RESOLUTION_FAILED)
    with (
        patch(f"{FLOW}.async_pin_install_target", AsyncMock(side_effect=refusal)),
        caplog.at_level(logging.WARNING),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.nowhere.example"}
        )

    assert result["errors"] == {"base": "install_resolution_failed"}
    assert [
        record.getMessage()
        for record in caplog.records
        if "Panel Assistant setup refused" in record.getMessage()
    ] == [
        "Panel Assistant setup refused: step=add_panel "
        "error=install_resolution_failed panel=panel.nowhere.example "
        "cause=resolution_failed"
    ]


@pytest.mark.parametrize(
    "error", ["app_port_refused", "app_port_no_answer", "app_port_not_panel"]
)
def test_each_text_names_both_ports_and_never_only_adb(error: str) -> None:
    """The owner reads which port failed and what each port is for."""
    import json
    from pathlib import Path

    strings = json.loads(
        (Path(config_flow.__file__).parent / "strings.json").read_text()
    )
    text = strings["config"]["error"][error]
    assert "port 8888" in text
    assert "(5555)" in text
    assert "The panel answered" not in text

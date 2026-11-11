"""A lost panel session is logged when it outlasts a reconnect, and its return."""

import logging
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
)
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_transport import (
    WsClientFactory,
    _open,
    _send,
    entry,  # noqa: F401
)


def _lost(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.panel_assistant")
        and "connection to Home Assistant" in record.getMessage()
    ]


async def test_a_lost_session_is_warned_after_the_grace_and_its_return_noted(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A session that ends and is not replaced is logged once, naming the panel."""
    caplog.set_level(logging.INFO, logger="custom_components.panel_assistant")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    await client.close()
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=45))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # A panel that is still reconnecting is left alone.
    assert _lost(caplog) == []

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    [warning] = _lost(caplog)
    assert warning.levelno == logging.WARNING
    message = warning.getMessage()
    assert "alpha" in message
    assert "connection_closed" in message

    # Later changes while it is still away do not log the same loss again.
    sessions = async_get_sessions(hass)
    sessions.set_restart_notice(entry.entry_id, "app", "settings", 1000)
    sessions.clear_restart_notice(entry.entry_id)
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(_lost(caplog)) == 1

    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    [_, back] = _lost(caplog)
    assert back.levelno == logging.INFO
    assert "alpha" in back.getMessage()


async def test_a_session_that_returns_inside_the_grace_logs_nothing(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A quick reconnect, and a newer hello that supersedes, are not faults."""
    caplog.set_level(logging.INFO, logger="custom_components.panel_assistant")
    old = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(old)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await old.close()
    await hass.async_block_till_done()
    await client.close()
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=20))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # A second drop has its own whole grace, not what was left of the first.
    await client.close()
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=45))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _lost(caplog) == []

    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _lost(caplog) == []


async def test_an_unloaded_panel_is_not_warned_about(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unloading an entry ends its session on purpose."""
    caplog.set_level(logging.INFO, logger="custom_components.panel_assistant")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _lost(caplog) == []


async def test_a_restart_the_panel_announced_is_warned_only_past_its_bound(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel that said it is restarting has until its own bound plus the grace."""
    caplog.set_level(logging.INFO, logger="custom_components.panel_assistant")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    reply = await _send(
        client,
        {
            "type": "panel_assistant/restart_notice",
            "session": token,
            "scope": "app",
            "reason": "update",
            "expected_back_ms": 120_000,
        },
    )
    assert reply["success"], reply
    await client.close()
    await hass.async_block_till_done()

    # The restarting app answers nothing until it is back.
    with patch.object(
        HaPaneldClient, "async_get_health", AsyncMock(side_effect=CannotConnectError)
    ):
        for _ in range(3):
            freezer.tick(timedelta(seconds=50))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
        assert _lost(caplog) == []

        freezer.tick(timedelta(seconds=40))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert [record.levelno for record in _lost(caplog)] == [logging.WARNING]

        # Logged once for the whole loss, however long it lasts.
        for _ in range(10):
            freezer.tick(timedelta(minutes=1))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
    assert [record.levelno for record in _lost(caplog)] == [logging.WARNING]


async def test_an_update_panel_assistant_started_is_not_warned_about(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """While Panel Assistant's own update restarts the app, the loss is expected."""
    caplog.set_level(logging.INFO, logger="custom_components.panel_assistant")
    coordinator = entry.runtime_data.coordinator
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()
    coordinator.expected_restart = True
    await client.close()
    await hass.async_block_till_done()

    for _ in range(5):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _lost(caplog) == []

    # An update that ends without the panel back is a loss like any other.
    coordinator.expected_restart = False
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [record.levelno for record in _lost(caplog)] == [logging.WARNING]

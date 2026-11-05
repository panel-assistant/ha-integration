"""The address Repair follows the panel's session.

It says the panel is connected but its stored address does not answer. A
panel switched off is not connected, so its Repair goes when the session
does, and a session that has stopped answering never raises one.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant.client import CannotConnectError, HaPaneldClient

from .test_availability import _connect as _connect_unmanaged
from .test_availability import _issue, _load, _poll
from .test_management import MANAGED, STATUS_BODY, Panel
from .test_management import _connect as _connect_managed
from .test_transport import HEALTH, STATUS, WsClientFactory

HEALTH_LINE = (
    "ha-paneld 0.9.11-rc1 panel=alpha build=2000 cfg=1a2b3c4d"
    f" did={HEALTH.discovery_id} identity=install vc=1200 proto=5-5\n"
)


async def test_session_end_withdraws_the_address_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """A panel switched off goes unavailable with nothing left in Repairs."""
    entry = await _load(hass, hass_read_only_user.id)
    client = await _connect_unmanaged(
        hass, hass_ws_client, hass_read_only_access_token, entry, None
    )
    await _poll(hass, entry, {"panel.local": CannotConnectError()})
    assert _issue(hass, entry) is not None

    await client.close()
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


def _http_blocked(calls: list[str]) -> Any:
    async def _get_health(self: HaPaneldClient) -> Any:
        calls.append(self.address.host)
        raise CannotConnectError

    return patch.multiple(
        HaPaneldClient,
        async_get_health=_get_health,
        async_get_status=AsyncMock(return_value=STATUS),
    )


async def _connected(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user_id: str,
    token: str,
) -> tuple[Any, Panel]:
    entry = await _load(hass, user_id)
    with patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=HEALTH),
        async_get_status=AsyncMock(return_value=STATUS),
    ):
        panel = await _connect_managed(hass, hass_ws_client, token, MANAGED)
        await hass.async_block_till_done()
    return entry, panel


async def _probed(calls: list[str]) -> None:
    """Wait until the stored address and the session's own have both failed."""
    for _ in range(50):
        await asyncio.sleep(0)
    assert len(calls) >= 2


async def test_a_power_cut_never_leaves_an_address_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session outlives the power by a heartbeat; it answers nothing then."""
    entry, panel = await _connected(
        hass, hass_ws_client, hass_read_only_user.id, hass_read_only_access_token
    )
    monkeypatch.setattr(
        "custom_components.panel_assistant.transport_sessions.MANAGE_TIMEOUT", 0.05
    )
    calls: list[str] = []
    with _http_blocked(calls):
        refresh = asyncio.ensure_future(entry.runtime_data.coordinator.async_refresh())
        assert (await panel.manage())["op"] == "snapshot"
        # The panel has no power: neither its address nor its session answers.
        await refresh
    assert len(calls) >= 2
    assert _issue(hass, entry) is None

    # The heartbeat then closes the session.
    await panel.client.close()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_a_connected_panel_with_a_dead_address_is_reported_once_it_answers(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """The Repair still comes, after the session has proved the panel is there."""
    entry, panel = await _connected(
        hass, hass_ws_client, hass_read_only_user.id, hass_read_only_access_token
    )
    calls: list[str] = []
    with _http_blocked(calls):
        refresh = asyncio.ensure_future(entry.runtime_data.coordinator.async_refresh())
        request = await panel.manage()
        await _probed(calls)
        # Every address has failed, but the session has not answered yet.
        assert _issue(hass, entry) is None
        await panel.answer(
            request, "applied", result={"health": HEALTH_LINE, "status": STATUS_BODY}
        )
        await refresh
        await hass.async_block_till_done()

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_placeholders["address"] == "panel.local"
    assert entry.runtime_data.coordinator.connected

    # And it goes with the session, like any other.
    await panel.client.close()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None


async def test_a_session_ending_during_the_probe_raises_nothing_after_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """A late failed probe cannot put back the Repair the session end withdrew."""
    entry = await _load(hass, hass_read_only_user.id)
    client = await _connect_unmanaged(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    probing = asyncio.Event()
    release = asyncio.Event()

    async def _get_health(self: HaPaneldClient) -> Any:
        if self.address.host != "panel.local":
            probing.set()
            await release.wait()
        raise CannotConnectError

    with patch.multiple(
        HaPaneldClient,
        async_get_health=_get_health,
        async_get_status=AsyncMock(return_value=STATUS),
    ):
        refresh = asyncio.ensure_future(entry.runtime_data.coordinator.async_refresh())
        await probing.wait()
        await client.close()
        await hass.async_block_till_done(wait_background_tasks=False)
        release.set()
        await refresh
        await hass.async_block_till_done()

    assert _issue(hass, entry) is None

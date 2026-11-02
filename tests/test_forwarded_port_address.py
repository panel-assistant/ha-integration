"""A panel reached through a forwarded port keeps it without an address Repair.

The panel reports the port its own listener uses. An entry stored at another
port, such as a forward from host:18888 to the panel's 8888, still answers as
this panel, so nothing needs the owner and the panel's own endpoints that
never answer from Home Assistant are not tried on every poll.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import coordinator as coordinator_module
from custom_components.panel_assistant.address import address_issue_id
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_availability import _load
from .test_transport import DID, HEALTH, STATUS, WsClientFactory, _hello, _send

FORWARDED = "203.0.113.7:18888"
LISTENER = "203.0.113.7"  # The forward's host at the panel's own port 8888.
REPORTED = "192.168.1.40"  # The panel's own interface address at port 8888.


class _Network:
    """Which endpoints answer as this panel, and every health read made."""

    def __init__(self) -> None:
        self.answering = {FORWARDED}
        self.reads: list[str] = []

    async def health(self, client: HaPaneldClient) -> Any:
        self.reads.append(client.address.stored_value)
        if client.address.stored_value in self.answering:
            return HEALTH
        raise CannotConnectError

    @property
    def read(self) -> Any:
        """The health read as an unbound method of every panel client."""
        return lambda client: self.health(client)

    def count(self, endpoint: str) -> int:
        return self.reads.count(endpoint)


async def _forwarded_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user: Any,
    token: str,
    network: _Network,
) -> tuple[Any, list[str]]:
    entry = await _load(hass, user.id, unique_id=DID, address=FORWARDED)
    created: list[str] = []

    @callback
    def _issue(event: Event) -> None:
        if event.data["action"] == "create" and event.data["issue_id"] == (
            address_issue_id(entry.entry_id)
        ):
            created.append(event.data["issue_id"])

    hass.bus.async_listen(ir.EVENT_REPAIRS_ISSUE_REGISTRY_UPDATED, _issue)
    client = await hass_ws_client(hass, token)
    response = await _send(client, _hello(http_port=8888, addresses=[REPORTED]))
    assert response["success"], response
    await hass.async_block_till_done()
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.http_port == 8888
    return entry, created


async def _refresh(hass: HomeAssistant, entry: Any) -> None:
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert coordinator.available


async def test_working_forwarded_port_raises_no_address_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: now[0])
    network = _Network()
    with (
        patch.object(HaPaneldClient, "async_get_health", network.read),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        entry, created = await _forwarded_panel(
            hass,
            hass_ws_client,
            hass_read_only_user,
            hass_read_only_access_token,
            network,
        )
        for _ in range(4):
            now[0] += 30
            await _refresh(hass, entry)
        assert created == []
        assert entry.data[CONF_ADDRESS] == FORWARDED
        assert (
            ir.async_get(hass).async_get_issue(DOMAIN, address_issue_id(entry.entry_id))
            is None
        )
        # The panel's own endpoints were tried once for this session, not on
        # each of the polls (the hello's own poll included).
        assert network.count(LISTENER) == 1
        assert network.count(REPORTED) == 1
        # Much later they are tried once more, so a port change still lands.
        now[0] += 3600
        await _refresh(hass, entry)
        await _refresh(hass, entry)
        assert network.count(LISTENER) == 2
        assert network.count(REPORTED) == 2
        # A new session (the app restarted, perhaps on a new port) is tried at
        # once, and then held off again.
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello(http_port=8888, addresses=[REPORTED]))
        assert response["success"], response
        await hass.async_block_till_done()
        now[0] += 30
        await _refresh(hass, entry)
        now[0] += 30
        await _refresh(hass, entry)
        assert network.count(LISTENER) == 3
        assert network.count(REPORTED) == 3
        assert created == []
        assert entry.data[CONF_ADDRESS] == FORWARDED


async def test_reported_endpoint_is_adopted_once_the_forward_stops_answering(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: now[0])
    network = _Network()
    with (
        patch.object(HaPaneldClient, "async_get_health", network.read),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        entry, created = await _forwarded_panel(
            hass,
            hass_ws_client,
            hass_read_only_user,
            hass_read_only_access_token,
            network,
        )
        now[0] += 30
        await _refresh(hass, entry)
        assert created == []
        # The forward goes away; the panel answers at its own endpoint.
        network.answering = {REPORTED}
        now[0] += 30
        await _refresh(hass, entry)
        assert entry.data[CONF_ADDRESS] == REPORTED
        assert entry.runtime_data.client.address.port == 8888
        assert created == []
        assert (
            ir.async_get(hass).async_get_issue(DOMAIN, address_issue_id(entry.entry_id))
            is None
        )

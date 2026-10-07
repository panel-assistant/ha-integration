"""Management over the panel's own session.

A panel whose session grants management is read and written on that session,
so Panel Assistant needs no route back to the panel. HTTP stays the carrier
for a panel that does not offer it.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, HaPaneldClient
from custom_components.panel_assistant.transport import async_get_sessions

from .test_availability import STATUS_ENTITY, _load, _state
from .test_transport import DID, HEALTH, HELLO, STATUS, WsClientFactory, _receive, _send

STATUS_BODY = (Path(__file__).parent / "fixtures" / "status.json").read_text()
# The panel's own health line, as /health serves it, at a build HTTP never saw.
HEALTH_LINE = (
    f"ha-paneld 0.9.11-rc1 panel=alpha build=2000 cfg=1a2b3c4d did={DID}"
    " identity=install vc=1200\n"
)
MANAGED = [*HELLO["capabilities"], "management"]


class Panel:
    """A panel's session as the panel sees it."""

    def __init__(self, client: Any, hello: dict[str, Any]) -> None:
        self.client = client
        self.result = hello["result"]
        self.token: str = hello["result"]["session"]

    async def manage(self) -> dict[str, Any]:
        """Receive the next event, which must be a management request."""
        message = await _receive(self.client)
        assert message["type"] == "event", message
        event: dict[str, Any] = message["event"]
        assert event["kind"] == "manage", event
        assert event["session"] == self.token
        return event

    async def answer(self, event: dict[str, Any], outcome: str, **fields: Any) -> Any:
        return await _send(
            self.client,
            {
                "type": "panel_assistant/command_result",
                "session": self.token,
                "command_id": event["command_id"],
                "outcome": outcome,
                **fields,
            },
        )


async def _connect(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    capabilities: list[str],
) -> Panel:
    client = await hass_ws_client(hass, token)
    await client.send_json_auto_id({**HELLO, "capabilities": capabilities})
    hello = await _receive(client)
    assert hello["success"], hello
    return Panel(client, hello)


def _http_answers() -> Any:
    """The panel's stored address answers as it always has."""
    return patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=HEALTH),
        async_get_status=AsyncMock(return_value=STATUS),
    )


def _http_blocked() -> Any:
    """Home Assistant cannot open a connection to the panel at any address."""
    return patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(side_effect=CannotConnectError),
        async_get_status=AsyncMock(side_effect=CannotConnectError),
    )


async def test_health_and_status_arrive_over_the_session_with_http_blocked(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    with _http_blocked():
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        panel = await _connect(
            hass, hass_ws_client, hass_read_only_access_token, MANAGED
        )
        assert "management" in panel.result["capabilities"]
        # The session opening while the address fails asks for a read at once.
        request = await panel.manage()
        assert request["op"] == "snapshot"
        answered = await panel.answer(
            request,
            "applied",
            result={"health": HEALTH_LINE, "status": STATUS_BODY},
        )
        assert answered["success"], answered
        await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert coordinator.data.health.build == "2000"
    assert coordinator.data.health.version_code == 1200
    assert coordinator.data.status is not None
    assert coordinator.data.status.warning_count == 1
    # The address still does not answer, which the operations HTTP carries need.
    assert not coordinator.reachable
    assert _state(hass, STATUS_ENTITY) == "connected"
    assert hass.states.get(STATUS_ENTITY).attributes["build"] == "2000"


async def test_a_session_read_that_fails_never_falls_back_to_http_data(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        request = await panel.manage()
        await panel.answer(request, "failed", code="failed")
        await refresh

    assert not coordinator.last_update_success
    # The address answered, so HTTP-carried operations still may run.
    assert coordinator.reachable
    assert coordinator.available


async def _probe_answering_after(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    panel: Panel,
    change: Any,
    *,
    answered: bool = True,
) -> None:
    """Refresh once while the address probe's health read is still in flight
    when ``change`` happens; the stale health then answers as this panel."""

    async def health_after_change(_client: HaPaneldClient) -> Any:
        change()
        await asyncio.sleep(0)
        return HEALTH

    with patch.multiple(
        HaPaneldClient,
        async_get_health=health_after_change,
        async_get_status=AsyncMock(return_value=STATUS),
    ):
        refresh = asyncio.ensure_future(entry.runtime_data.coordinator.async_refresh())
        if answered:
            request = await panel.manage()
            await panel.answer(
                request,
                "applied",
                result={"health": HEALTH_LINE, "status": STATUS_BODY},
            )
        await refresh
        await hass.async_block_till_done()


async def test_an_address_edited_during_the_probe_is_not_called_reachable(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)

    await _probe_answering_after(
        hass,
        entry,
        panel,
        lambda: hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: "192.168.1.77"}
        ),
    )

    coordinator = entry.runtime_data.coordinator
    # The snapshot still arrived over the session; only the probe is void.
    assert coordinator.last_update_success
    assert not coordinator.reachable


async def test_a_session_replaced_during_the_probe_is_not_called_reachable(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    sessions = async_get_sessions(hass)
    live = sessions.get(entry.entry_id)
    assert live is not None

    def replace_session() -> None:
        sessions.open(dataclasses.replace(live, token="newer-session"))

    # The old session is closed before its read is sent, so nothing is asked.
    await _probe_answering_after(hass, entry, panel, replace_session, answered=False)

    assert not entry.runtime_data.coordinator.reachable


async def test_a_status_body_over_the_http_bound_is_refused(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    oversized = STATUS_BODY.replace("{", '{"padding": "' + "x" * 70_000 + '",', 1)
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        request = await panel.manage()
        await panel.answer(
            request, "applied", result={"health": HEALTH_LINE, "status": oversized}
        )
        await refresh

    assert not coordinator.last_update_success


async def test_a_panel_without_management_is_still_polled_over_http(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, HELLO["capabilities"]
    )
    assert "management" not in panel.result["capabilities"]
    with _http_blocked():
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert not coordinator.reachable
    # Nothing was asked on the session: the next message answers a request.
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and not session.pending


async def test_a_request_on_a_superseded_session_fails_and_its_late_answer_is_ignored(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry: MockConfigEntry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    first = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        request = await first.manage()
        # The panel reconnects before answering: the old session's request fails.
        await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
        await refresh
    previous = coordinator.data

    await first.client.send_json_auto_id(
        {
            "type": "panel_assistant/command_result",
            "session": first.token,
            "command_id": request["command_id"],
            "outcome": "applied",
            "result": {"health": HEALTH_LINE, "status": STATUS_BODY},
        }
    )
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert coordinator.data is previous

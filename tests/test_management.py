"""Management over the panel's own session.

A panel whose session grants management is read and written on that session,
so Panel Assistant needs no route back to the panel. HTTP stays the carrier
for a panel that does not offer it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, HaPaneldClient
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    _failure_store,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_availability import STATUS_ENTITY, _load, _state
from .test_transport import DID, HEALTH, HELLO, STATUS, WsClientFactory, _receive, _send

STATUS_BODY = (Path(__file__).parent / "fixtures" / "status.json").read_text()
# The panel's own health line, as /health serves it, at a build HTTP never saw.
HEALTH_LINE = (
    f"ha-paneld 0.9.11-rc1 panel=alpha build=2000 cfg=1a2b3c4d did={DID}"
    " identity=install vc=1200 proto=5-5\n"
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


@pytest.mark.parametrize("address_answers", [True, False])
async def test_snapshot_timeouts_preserve_only_verified_http_health(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    address_answers: bool,
) -> None:
    """Slow optional diagnostics cannot hide health that this panel just answered."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    monkeypatch.setattr(
        "custom_components.panel_assistant.transport_sessions.MANAGE_TIMEOUT", 0.01
    )
    health = AsyncMock(
        return_value=HEALTH,
        side_effect=None if address_answers else CannotConnectError,
    )
    status = AsyncMock(return_value=STATUS)
    with patch.multiple(
        HaPaneldClient, async_get_health=health, async_get_status=status
    ):
        for _ in range(2):
            refresh = asyncio.ensure_future(coordinator.async_refresh())
            request = await panel.manage()
            assert request["op"] == "snapshot"
            # No answer: exercise the actual management deadline twice.
            await refresh
            assert coordinator.last_update_success is address_answers
            if address_answers:
                assert coordinator.data.health == HEALTH
                assert coordinator.data.status is None
                assert coordinator.data.status_error == "unavailable"

    if address_answers:
        assert "Unable to read panel health" not in caplog.text
    else:
        assert "Unable to read panel health" in caplog.text
    assert health.await_count >= 2
    status.assert_not_awaited()


@pytest.mark.parametrize("retired_by", ["address", "identity", "session", "repair"])
async def test_a_snapshot_timeout_cannot_publish_a_retired_health_probe(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    retired_by: str,
) -> None:
    """A good probe loses authority when its address, identity or session retires."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    monkeypatch.setattr(
        "custom_components.panel_assistant.transport_sessions.MANAGE_TIMEOUT", 0.01
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    saved_events: list[dict[str, Any]] = []
    if retired_by == "repair":
        await async_record_update_failure(
            hass, entry.entry_id, entry.title, "0.9.8-rc1 build 1", RuntimeError()
        )

        saved_events = await async_failure_events(hass, issue_id)
        assert saved_events
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

        async def version_after_address_edit(
            _client: HaPaneldClient,
        ) -> tuple[str, int]:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_ADDRESS: "192.168.1.77"}
            )
            await asyncio.sleep(0)
            return HEALTH.version, 1200

        monkeypatch.setattr(
            HaPaneldClient, "async_get_version_code", version_after_address_edit
        )
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        await panel.manage()
        # The concurrent HTTP read has completed before the snapshot deadline.
        if retired_by == "address":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_ADDRESS: "192.168.1.77"}
            )
        elif retired_by == "identity":
            hass.config_entries.async_update_entry(entry, unique_id="b" * 64)
        elif retired_by == "session":
            sessions = async_get_sessions(hass)
            live = sessions.get(entry.entry_id)
            assert live is not None
            sessions.open(dataclasses.replace(live, token="newer-session"))
        await refresh

    assert not coordinator.last_update_success
    if retired_by == "repair":
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        assert await async_failure_events(hass, issue_id) == saved_events


@pytest.mark.parametrize(
    "retired_by", ["address", "identity", "session", pytest.param(None, id="current")]
)
async def test_a_timeout_probe_can_clear_a_repair_only_while_current_at_the_store(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    retired_by: str | None,
) -> None:
    """A probe waiting to save cannot resolve a Repair after losing its panel."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    monkeypatch.setattr(
        "custom_components.panel_assistant.transport_sessions.MANAGE_TIMEOUT", 0.01
    )
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "0.9.11-rc1 build 1200", RuntimeError()
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    saved_events = await async_failure_events(hass, issue_id)
    assert saved_events
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    version_answered = asyncio.Event()

    async def version_code(_client: HaPaneldClient) -> tuple[str, int]:
        version_answered.set()
        return HEALTH.version, 1200

    monkeypatch.setattr(HaPaneldClient, "async_get_version_code", version_code)
    store = _failure_store(hass)
    await store.lock.acquire()
    try:
        with _http_answers():
            refresh = asyncio.ensure_future(coordinator.async_refresh())
            await panel.manage()
            # Let the real snapshot deadline pass and the healthy HTTP probe
            # reach its version read; its Repair clear then waits on this lock.
            await asyncio.wait_for(version_answered.wait(), timeout=1)
            assert not refresh.done()
            if retired_by == "address":
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_ADDRESS: "192.168.1.77"}
                )
            elif retired_by == "identity":
                hass.config_entries.async_update_entry(entry, unique_id="b" * 64)
            elif retired_by == "session":
                sessions = async_get_sessions(hass)
                live = sessions.get(entry.entry_id)
                assert live is not None
                sessions.open(dataclasses.replace(live, token="newer-session"))
            store.lock.release()
            await refresh
    finally:
        if store.lock.locked():
            store.lock.release()

    if retired_by is None:
        assert coordinator.last_update_success
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        assert await async_failure_events(hass, issue_id) == []
    else:
        assert not coordinator.last_update_success
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        assert await async_failure_events(hass, issue_id) == saved_events


@pytest.mark.parametrize("health_line", ["invalid", HEALTH_LINE.replace(DID, "b" * 64)])
async def test_a_rejected_snapshot_health_cannot_use_the_address_probe(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    health_line: str,
) -> None:
    """A deadline is recoverable; corrupt or different-panel health stays refused."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        request = await panel.manage()
        await panel.answer(
            request, "applied", result={"health": health_line, "status": STATUS_BODY}
        )
        await refresh

    assert not coordinator.last_update_success


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
    response = await _send(
        panel.client,
        {
            "type": "panel_assistant/report_state",
            "session": panel.token,
            "sync": "delta",
            "observations": [],
        },
    )
    assert response["type"] == "result", response
    assert response["success"], response


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


@pytest.mark.parametrize(
    ("webview", "reason", "settled"),
    [
        ("installed", "current", True),
        # The panel reports the candidate current, so the uncertain send landed.
        ("submitted_unknown", "current", True),
        ("submitted_unknown", None, False),
    ],
)
async def test_a_session_snapshot_ends_an_update_repair_once_its_webview_settles(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    webview: str,
    reason: str | None,
    settled: bool,
) -> None:
    """A managed panel never reaches the HTTP status read, so its snapshot clears."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    record = asyncio.ensure_future(
        async_record_update_failure(
            hass,
            entry.entry_id,
            entry.title,
            "0.9.11-rc1 build 1200",
            RuntimeError(),
            webview_pending=True,
        )
    )
    # The failure report first asks the managed panel why its WebView is unfinished.
    observe = await panel.manage()
    assert observe["op"] == "webview.observe"
    pending = {
        "schema": 1,
        "supported": True,
        "eligible": True,
        "reason": "eligible",
        "candidate": {"sha256": "c" * 64},
        "transaction": {
            "state": "submitted_unknown",
            "request_id": "r",
            "reason": None,
        },
    }
    answered = await panel.answer(observe, "applied", result=pending)
    assert answered["success"], answered
    await record
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    events = await async_failure_events(hass, issue_id)
    assert events[-1]["webview"]["transaction_state"] == "submitted_unknown"
    status_body = json.dumps(
        {
            **json.loads(STATUS_BODY),
            "webview": {
                "schema": 1,
                "supported": True,
                "eligible": False,
                "reason": reason,
                "candidate": {"sha256": "c" * 64},
                "transaction": {"state": webview, "request_id": "r", "reason": None},
            },
        }
    )
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        request = await panel.manage()
        assert request["op"] == "snapshot"
        answered = await panel.answer(
            request, "applied", result={"health": HEALTH_LINE, "status": status_body}
        )
        assert answered["success"], answered
        await refresh

    assert coordinator.last_update_success
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert (issue is None) is settled


async def test_a_session_that_ends_mid_poll_finishes_the_poll_over_http(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The panel still answers health, so losing its session is no outage."""
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    with _http_answers():
        refresh = asyncio.ensure_future(coordinator.async_refresh())
        await panel.manage()
        # The session ends before the panel answers the management request.
        await panel.client.close()
        await refresh
        await hass.async_block_till_done()

    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert coordinator.last_update_success
    assert coordinator.data.health == HEALTH
    assert _state(hass, STATUS_ENTITY) != "unavailable"
    assert "Unable to read panel health" not in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

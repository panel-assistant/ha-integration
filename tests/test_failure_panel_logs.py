"""Update Repairs retain a bounded snapshot from the panel's logs subscription."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import repairs_failure, ui_relay
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    async_support_report,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.transport import ws_command_result
from custom_components.panel_assistant.transport_sessions import (
    PanelSession,
    async_get_sessions,
)


def line(level: str, message: str) -> str:
    return f"10-25 17:20:30.123  1234  5678 {level} PanelUpdate: {message}"


class BacklogPanel:
    """A fake socket peer answering real management and ui_update callers."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        backlog: list[Any],
        *,
        before_answer: bool = False,
        mode: str = "captured",
    ) -> None:
        self.hass = hass
        self.backlog = backlog
        self.before_answer = before_answer
        self.mode = mode
        self.requests: list[dict[str, Any]] = []
        self.subscriptions: dict[Any, Any] = {}
        self.send_result = Mock()
        self.send_error = Mock()
        self.session = PanelSession(
            entry_id,
            "panel-id",
            "session",
            self,
            1,
            "owner",
            5,
            "1.0.0",
            1234,
            "c" * 64,
            frozenset({"management", "ui_subscribe"}),
            {},
            datetime.now(UTC),
        )
        async_get_sessions(hass).open(self.session)

    def send_message(self, message: dict[str, Any]) -> None:
        event = message["event"]
        if event.get("kind") != "manage":
            return
        self.requests.append(event)
        if event["op"] == "unsubscribe" and self.mode == "cleanup_timeout":
            return
        if event["op"] == "subscribe" and self.mode == "timeout":
            return
        if event["op"] == "subscribe" and self.mode == "disconnected":
            self.hass.loop.call_soon(
                async_get_sessions(self.hass).close, self.session, "test disconnect"
            )
            return
        self.hass.loop.call_soon(self.answer, event)

    def update(self, subscription: str) -> None:
        ui_relay.ws_ui_update(
            self.hass,
            self,
            {
                "id": 101,
                "session": self.session.token,
                "subscription": subscription,
                "observations": [
                    {"channel": "logs", "state": "known", "value": self.backlog}
                    if self.mode != "unknown"
                    else {"channel": "logs", "state": "unavailable", "value": None}
                ],
            },
        )

    def answer(self, event: dict[str, Any]) -> None:
        subscribe = event["op"] == "subscribe"
        subscription = event["command_id"]
        if subscribe and self.before_answer:
            self.update(subscription)
        ws_command_result(
            self.hass,
            self,
            {
                "id": 102,
                "session": self.session.token,
                "command_id": event["command_id"],
                "outcome": "refused"
                if self.mode == "refused" and subscribe
                else "applied",
                "code": "not_commandable" if self.mode == "refused" else None,
                "result": {"subscription": subscription} if subscribe else {},
            },
        )
        if subscribe and not self.before_answer and self.mode != "refused":
            self.hass.loop.call_soon(self.update, subscription)


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    assert await async_setup_component(hass, DOMAIN, {})
    result = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: "kitchen.local"}
    )
    result.add_to_hass(hass)
    return result


async def record(hass: HomeAssistant, entry: MockConfigEntry) -> tuple[Any, str]:
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, "1.0.0-rc2", RuntimeError("install failed")
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    events = await async_failure_events(hass, issue_id)
    return events[-1], issue_id


@pytest.mark.parametrize("before_answer", [False, True])
async def test_failure_report_saves_warning_backlog_at_failure_time(
    hass: HomeAssistant, entry: MockConfigEntry, before_answer: bool
) -> None:
    warning = line("W", "warning [REDACTED]")
    error = line("E", "install error ```\ncontinuation")
    fatal = line("F", "fatal")
    panel = BacklogPanel(
        hass,
        entry.entry_id,
        [
            line("V", "verbose"),
            line("D", "debug"),
            line("I", "info"),
            "unparsed W text",
            warning,
            error,
            fatal,
        ],
        before_answer=before_answer,
    )
    event, issue_id = await record(hass, entry)
    assert event["panel_logs"] == {
        "status": "captured",
        "lines": [warning, error, fatal],
        "truncated": False,
    }
    assert [request["op"] for request in panel.requests] == ["subscribe", "unsubscribe"]
    assert panel.requests[0]["args"]["family"] == "logs"
    assert panel.requests[0]["args"]["source"] == "app"
    assert panel.requests[0]["origin"]["user"] == "owner"
    assert panel.requests[1]["origin"] == panel.requests[0]["origin"]
    assert not panel.session.ui_relays
    async_get_sessions(hass).close(panel.session, "later disconnected")
    panel.backlog[:] = [line("E", "later line")]
    report = await async_support_report(hass, issue_id)
    assert warning in report and fatal in report
    assert "install error ```\n  continuation" in report
    assert report.startswith("````text\n")
    assert "later line" not in report and "verbose" not in report
    assert "panel_logs.status: captured" in report


async def test_failure_report_bounds_utf8_backlog_and_keeps_recent_lines(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    panel = BacklogPanel(
        hass,
        entry.entry_id,
        [line("W", f"old-{i} " + "é" * 2000) for i in range(400)]
        + [line("E", "newest line")],
    )
    event, issue_id = await record(hass, entry)
    saved = event["panel_logs"]
    assert saved["truncated"] is True
    assert len("\n".join(saved["lines"]).encode("utf-8")) <= 16 * 1024
    assert saved["lines"][-1] == line("E", "newest line")
    report = await async_support_report(hass, issue_id)
    assert "old-0 " not in report
    assert "newest line" in report and "panel_logs.truncated: True" in report
    assert not panel.session.ui_relays


async def test_disconnected_failure_report_says_no_lines_could_be_taken(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    event, issue_id = await record(hass, entry)
    assert event["panel_logs"]["status"] == "unavailable"
    report = await async_support_report(hass, issue_id)
    assert "No panel log lines could be taken: panel not connected." in report


@pytest.mark.parametrize("mode", ["refused", "unknown", "timeout", "disconnected"])
async def test_capture_failure_still_saves_update_failure_and_report(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setattr(repairs_failure, "_PANEL_LOG_TIMEOUT", 0.01, raising=False)
    panel = BacklogPanel(hass, entry.entry_id, [], mode=mode)
    event, issue_id = await record(hass, entry)
    assert event["panel_logs"]["status"] == "unavailable"
    assert "install failed" in await async_support_report(hass, issue_id)
    assert not panel.session.ui_relays
    if mode != "disconnected":
        assert panel.requests[-1]["op"] == "unsubscribe"


async def test_empty_warning_backlog_is_captured_not_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    BacklogPanel(hass, entry.entry_id, [line("I", "info only")])
    event, issue_id = await record(hass, entry)
    assert event["panel_logs"] == {
        "status": "captured",
        "lines": [],
        "truncated": False,
    }
    assert "panel_logs.status: captured" in await async_support_report(hass, issue_id)


async def test_single_oversized_warning_is_bounded_without_splitting_utf8(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    BacklogPanel(hass, entry.entry_id, [line("E", "é" * (32 * 1024))])
    event, _ = await record(hass, entry)
    saved = event["panel_logs"]
    assert saved["status"] == "captured" and saved["truncated"] is True
    assert len(saved["lines"]) == 1
    assert 16380 <= len(saved["lines"][0].encode("utf-8")) <= 16384
    assert "\ufffd" not in saved["lines"][0]


async def test_unanswered_cleanup_keeps_report_and_existing_browser_subscription(
    hass: HomeAssistant, entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repairs_failure, "_PANEL_LOG_TIMEOUT", 0.01)
    panel = BacklogPanel(
        hass, entry.entry_id, [line("W", "saved warning")], mode="cleanup_timeout"
    )
    browser = Mock()
    panel.session.ui_relays["browser-subscription"] = [browser]
    event, issue_id = await record(hass, entry)
    assert event["panel_logs"]["status"] == "captured"
    assert "saved warning" in await async_support_report(hass, issue_id)
    assert panel.requests[-1]["op"] == "unsubscribe"
    assert panel.requests[-1]["args"]["subscription"] == panel.requests[0]["command_id"]
    assert panel.session.ui_relays == {"browser-subscription": [browser]}
    browser.update.assert_not_called()

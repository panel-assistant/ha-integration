"""Native commands: the authority a session is granted, and the command path."""

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import transport
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_native import (
    DESCRIPTORS,
    _native_entries,
    _observations,
    _report,
    _setup,
)
from .test_transport import DID, WsClientFactory, _receive, _send

ALL_CAPABILITIES = ["state", "events", "commands", "approval"]
_COMMAND_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _hello(capabilities: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "panel_assistant/hello",
        "protocol": {"min": 1, "max": 1},
        "did": DID,
        "app": {"version": "0.9.8-rc1", "version_code": 790},
        "contract_digest": "c" * 64,
        "capabilities": ALL_CAPABILITIES if capabilities is None else capabilities,
        "channels": DESCRIPTORS,
    }


def _result(token: str, command_id: str, outcome: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "panel_assistant/command_result",
        "session": token,
        "command_id": command_id,
        "outcome": outcome,
        **fields,
    }


class Panel:
    """One signed-in panel connection with an open session."""

    def __init__(self, client: Any, hello: dict[str, Any]) -> None:
        self.client = client
        self.subscription = hello["id"]
        self.result = hello["result"]
        self.token: str = hello["result"]["session"]

    async def sync(self, hass: HomeAssistant) -> None:
        for sync, observations in (
            ("full_begin", []),
            ("full_end", _observations()),
        ):
            response = await _send(self.client, _report(self.token, sync, observations))
            assert response["success"], response
        await hass.async_block_till_done()

    async def command(self) -> dict[str, Any]:
        """Receive the next message, which must be a command event."""
        message = await _receive(self.client)
        assert message["type"] == "event", message
        assert message["id"] == self.subscription
        event: dict[str, Any] = message["event"]
        assert event["kind"] == "command", event
        return event

    async def answer(self, command_id: str, outcome: str, **fields: Any) -> Any:
        return await _send(
            self.client, _result(self.token, command_id, outcome, **fields)
        )

    async def next_after_request(self) -> dict[str, Any]:
        """Send a request and return the first message that follows.

        Anything already queued, such as a session end, arrives before the
        request's own result, so a missing message fails as an assertion.
        """
        await self.client.send_json_auto_id(_report(self.token, "delta", []))
        message: dict[str, Any] = await _receive(self.client)
        return message

    async def nothing_sent(self) -> None:
        """Prove no command is queued: the next message answers a request."""
        response = await _send(self.client, _report(self.token, "delta", []))
        assert response["type"] == "result", response
        assert response["success"], response


async def _connect(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    capabilities: list[str] | None = None,
    sync: bool = True,
) -> Panel:
    client = await hass_ws_client(hass, token)
    await client.send_json_auto_id(_hello(capabilities))
    hello = await _receive(client)
    assert hello["success"], hello
    panel = Panel(client, hello)
    if sync:
        await panel.sync(hass)
    return panel


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, suffix: str) -> str:
    return _native_entries(hass, entry.entry_id)[f"{DID}_{suffix}"].entity_id


def _call(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any]
) -> asyncio.Task[Any]:
    # Not tracked by Home Assistant, so waiting for the loop to settle never
    # waits for a service call that is itself waiting for the panel.
    coroutine: Awaitable[Any] = hass.services.async_call(
        domain, service, data, blocking=True
    )
    return asyncio.ensure_future(coroutine)


async def _raised(task: asyncio.Task[Any]) -> HomeAssistantError:
    with pytest.raises(HomeAssistantError) as raised:
        async with asyncio.timeout(5):
            await task
    assert raised.value.translation_domain == DOMAIN
    return raised.value


async def _commands(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    commands: dict[str, Any] = diagnostics["transport"]["commands"]
    return commands


@pytest.fixture
async def native(hass: HomeAssistant, hass_read_only_user: Any) -> MockConfigEntry:
    """A bound entry whose options chose the native authority."""
    return await _setup(
        hass, hass_read_only_user.id, native=True, options={"authority": "native"}
    )


# ---------------------------------------------------------------------------
# The authority and what it grants.


@pytest.mark.parametrize(
    ("flag", "options", "offered", "authority", "granted"),
    [
        (False, {}, None, "shadow", ["events", "state"]),
        (False, {"authority": "native"}, None, "shadow", ["events", "state"]),
        (True, {}, None, "shadow", ["events", "state"]),
        (True, {"authority": "shadow"}, None, "shadow", ["events", "state"]),
        (True, {"authority": "mqtt"}, None, "mqtt", []),
        (
            True,
            {"authority": "native"},
            None,
            "native",
            ["approval", "commands", "state"],
        ),
        (True, {"authority": "native"}, ["state", "events"], "native", ["state"]),
        (True, {"authority": "shadow"}, ["commands", "approval"], "shadow", []),
    ],
)
async def test_hello_grants_follow_the_effective_authority(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    flag: bool,
    options: dict[str, str],
    offered: list[str] | None,
    authority: str,
    granted: list[str],
) -> None:
    """The option counts only with native entities on; grants meet the offer."""
    entry = await _setup(hass, hass_read_only_user.id, native=flag, options=options)
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, offered, sync=False
    )

    assert panel.result["authority"] == authority
    assert panel.result["capabilities"] == granted
    transport_diagnostics = (await async_get_config_entry_diagnostics(hass, entry))[
        "transport"
    ]
    assert transport_diagnostics["authority"] == authority
    assert transport_diagnostics["effective_authority"] == authority
    assert transport_diagnostics["capabilities"] == granted


# ---------------------------------------------------------------------------
# The options flow.


async def test_options_flow_aborts_without_native_entities(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A release carries the choice dark."""
    entry = await _setup(hass, hass_read_only_user.id, native=False)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "native_entities_disabled"
    assert entry.options == {}


def _default(result: Any, key: str) -> Any:
    for marker in result["data_schema"].schema:
        if marker == key:
            return marker.default()
    raise AssertionError(key)


async def test_changing_the_authority_ends_the_session_and_regrants(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Saving a new authority closes the session; the next hello gets it."""
    entry = await _setup(hass, hass_read_only_user.id, native=True)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert panel.result["authority"] == "shadow"

    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "transport"
    assert _default(form, "authority") == "shadow"
    done = await hass.config_entries.options.async_configure(
        form["flow_id"], {"authority": "native"}
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"authority": "native"}
    closed = await panel.next_after_request()
    assert closed.get("event") == {
        "kind": "session_closed",
        "reason": "authority_changed",
    }, closed
    assert closed["id"] == panel.subscription
    refused = await _receive(panel.client)
    assert refused.get("error", {}).get("code") == "session_unknown", refused

    again = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert again.result["authority"] == "native"
    assert again.result["capabilities"] == ["approval", "commands", "state"]
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert _default(form, "authority") == "native"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_entry_writes_that_keep_the_authority_keep_the_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A data write, as binding makes, and an unchanged choice leave it open."""
    entry = await _setup(
        hass, hass_read_only_user.id, native=True, options={"authority": "native"}
    )
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)

    # A data write, as binding and user removal make, notifies the entry.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_ADDRESS: "panel-2.local"}
    )
    await hass.async_block_till_done()
    await panel.nothing_sent()

    form = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        form["flow_id"], {"authority": "native"}
    )
    await hass.async_block_till_done()
    await panel.nothing_sent()
    assert transport.async_get_sessions(hass).get(entry.entry_id) is not None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_dark_option_change_keeps_a_shadow_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """Without native entities, storing another authority changes nothing."""
    entry = await _setup(hass, hass_read_only_user.id, native=False)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)

    hass.config_entries.async_update_entry(entry, options={"authority": "native"})
    await hass.async_block_till_done()

    await panel.nothing_sent()
    assert transport.async_get_sessions(hass).get(entry.entry_id) is not None


# ---------------------------------------------------------------------------
# Round trips.


@pytest.mark.parametrize(
    ("domain", "suffix", "service", "data", "value"),
    [
        ("switch", "relay1", "turn_on", {}, True),
        ("switch", "relay1", "turn_off", {}, False),
        ("light", "screen", "turn_on", {}, {"on": True}),
        (
            "light",
            "screen",
            "turn_on",
            {"brightness": 128},
            {"on": True, "brightness": 128},
        ),
        ("light", "screen", "turn_off", {}, {"on": False}),
        (
            "light",
            "led",
            "turn_on",
            {"rgb_color": [1, 2, 3], "effect": "pulse"},
            {"on": True, "color": {"r": 1, "g": 2, "b": 3}, "effect": "pulse"},
        ),
        ("number", "volume", "set_value", {"value": 10}, 10),
        ("number", "volume", "set_value", {"value": 2.5}, 2.5),
        ("select", "cpu_governor", "select_option", {"option": "auto"}, "auto"),
        ("text", "navigate", "set_value", {"value": "/x"}, "/x"),
        ("button", "reboot", "press", {}, None),
    ],
)
async def test_each_platform_sends_its_typed_value(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
    domain: str,
    suffix: str,
    service: str,
    data: dict[str, Any],
    value: Any,
) -> None:
    """The command carries the session, channel and value, and applied returns."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    entity_id = _entity_id(hass, native, suffix)

    call = _call(hass, domain, service, {"entity_id": entity_id, **data})
    command = await panel.command()

    assert set(command) == {
        "kind",
        "command_id",
        "session",
        "channel",
        "value",
        "deadline_ms",
    }
    assert command["session"] == panel.token
    assert command["channel"] == suffix
    assert command["value"] == value
    assert type(command["value"]) is type(value)
    assert command["deadline_ms"] == 10000
    assert _COMMAND_ID.fullmatch(command["command_id"])
    assert not call.done()
    ack = await panel.answer(command["command_id"], "applied")
    assert ack["success"], ack
    async with asyncio.timeout(5):
        assert await call is None
    commands = await _commands(hass, native)
    assert commands["counts"] == {"applied": 1, "sent": 1}
    assert commands["pending"] == 0
    assert [
        (r["channel"], r["outcome"], r["code"], r["late"]) for r in commands["recent"]
    ] == [(suffix, "applied", None, False)]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_superseded_returns_and_values_stay_out_of_diagnostics(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A conflated command succeeds; what it carried is never recorded."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass,
        "text",
        "set_value",
        {"entity_id": _entity_id(hass, native, "navigate"), "value": "/private-route"},
    )
    command = await panel.command()
    assert (await panel.answer(command["command_id"], "superseded"))["success"]
    async with asyncio.timeout(5):
        await call

    diagnostics = await async_get_config_entry_diagnostics(hass, native)
    assert diagnostics["transport"]["commands"]["counts"] == {
        "sent": 1,
        "superseded": 1,
    }
    rendered = repr(diagnostics)
    assert "/private-route" not in rendered
    assert command["command_id"] not in rendered


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ("refused", "approval_denied"),
        ("refused", "unknown_channel"),
        ("failed", "hardware_unavailable"),
        ("failed", "failed"),
    ],
)
async def test_refused_and_failed_raise_their_code(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    outcome: str,
    code: str,
) -> None:
    """The panel's code is the translation key of the raised error."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass, "switch", "turn_on", {"entity_id": _entity_id(hass, native, "relay1")}
    )
    command = await panel.command()
    ack = await panel.answer(
        command["command_id"], outcome, code=code, placeholders={"seconds": "30"}
    )
    assert ack["success"], ack

    error = await _raised(call)
    assert error.translation_key == code
    commands = await _commands(hass, native)
    assert commands["recent"][-1]["code"] == code
    assert commands["counts"][outcome] == 1


async def test_approval_then_final_outcome_within_the_wait_returns(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An interim approval keeps the call waiting for the final outcome."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass, "button", "press", {"entity_id": _entity_id(hass, native, "reboot")}
    )
    command = await panel.command()

    assert (await panel.answer(command["command_id"], "pending_approval"))["success"]
    # A repeated interim outcome changes nothing.
    assert (await panel.answer(command["command_id"], "pending_approval"))["success"]
    await asyncio.sleep(0.05)
    assert not call.done()
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        assert await call is None

    commands = await _commands(hass, native)
    assert [r["outcome"] for r in commands["recent"]] == ["pending_approval", "applied"]
    assert commands["counts"] == {
        "applied": 1,
        "ignored": 1,
        "pending_approval": 1,
        "sent": 1,
    }


async def test_approval_that_outlasts_the_wait_raises_and_records_the_late_outcome(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Past the wait the call raises approval_pending; the final is kept as late."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    with patch.object(transport, "COMMAND_TIMEOUT", 0.3):
        call = _call(
            hass, "button", "press", {"entity_id": _entity_id(hass, native, "reboot")}
        )
        command = await panel.command()
        assert (await panel.answer(command["command_id"], "pending_approval"))[
            "success"
        ]
        error = await _raised(call)
    assert error.translation_key == "approval_pending"

    assert (
        await panel.answer(command["command_id"], "refused", code="approval_denied")
    )["success"]
    # Only one final outcome counts, however late.
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    commands = await _commands(hass, native)
    assert [(r["outcome"], r["code"], r["late"]) for r in commands["recent"]] == [
        ("pending_approval", None, False),
        ("refused", "approval_denied", True),
    ]
    assert commands["counts"] == {
        "ignored": 1,
        "late": 1,
        "pending_approval": 1,
        "refused": 1,
        "sent": 1,
        "timed_out": 1,
    }
    assert commands["pending"] == 0


async def test_no_outcome_raises_panel_unavailable(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel that never answers leaves the call failing, not hanging."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    with patch.object(transport, "COMMAND_TIMEOUT", 0.2):
        call = _call(
            hass,
            "switch",
            "turn_off",
            {"entity_id": _entity_id(hass, native, "relay1")},
        )
        command = await panel.command()
        error = await _raised(call)
    assert error.translation_key == "panel_unavailable"

    assert (await panel.answer(command["command_id"], "applied"))["success"]
    commands = await _commands(hass, native)
    assert [(r["outcome"], r["late"]) for r in commands["recent"]] == [
        ("applied", True)
    ]


async def test_session_end_fails_the_waiting_call_at_once(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A closed socket fails the command now; it is never sent again."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass, "switch", "turn_on", {"entity_id": _entity_id(hass, native, "relay1")}
    )
    await panel.command()

    await panel.client.close()
    await asyncio.wait({call}, timeout=2)

    assert call.done()
    error = await _raised(call)
    assert error.translation_key == "panel_unavailable"
    commands = await _commands(hass, native)
    assert commands["counts"] == {"sent": 1, "session_ended": 1}
    assert commands["pending"] == 0

    again = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    await again.nothing_sent()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# Nothing is sent unless the session may carry it.


async def test_no_command_before_the_full_sync(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An unsynced session is not available for commands."""
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, sync=False
    )
    entity = hass.data["switch"].get_entity(_entity_id(hass, native, "relay1"))

    with pytest.raises(HomeAssistantError) as raised:
        await entity.async_turn_on()

    assert raised.value.translation_key == "panel_unavailable"
    await panel.nothing_sent()


async def test_no_command_without_a_session(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """With the panel gone the call raises at once."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    entity = hass.data["switch"].get_entity(_entity_id(hass, native, "relay1"))
    await panel.client.close()
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError) as raised:
        async with asyncio.timeout(2):
            await entity.async_turn_on()

    assert raised.value.translation_key == "panel_unavailable"


async def test_a_session_that_has_ended_carries_no_command(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A session held from before its end is refused, never written to."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    ended = transport.async_get_sessions(hass).get(native.entry_id)
    assert ended is not None
    replacement = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    closed = await _receive(panel.client)
    assert closed["event"] == {"kind": "session_closed", "reason": "superseded"}

    sent = asyncio.ensure_future(
        transport.async_send_command(hass, ended, "relay1", True)
    )
    await asyncio.wait({sent}, timeout=1)

    # Refused at once, not left waiting for an answer that cannot come.
    assert sent.done()
    error = await _raised(sent)
    assert error.translation_key == "panel_unavailable"
    assert ended.command_counts == {}
    await replacement.nothing_sent()


async def test_no_command_when_commands_are_not_granted(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A native panel that did not offer commands is not commandable."""
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, ["state", "events"]
    )

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": _entity_id(hass, native, "relay1")},
            blocking=True,
        )

    assert raised.value.translation_key == "not_commandable"
    await panel.nothing_sent()
    assert (await _commands(hass, native))["counts"] == {}


# ---------------------------------------------------------------------------
# command_result handling.


async def test_command_result_needs_the_current_session_on_its_connection(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Another connection, or a made-up token, is refused session_unknown."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass, "switch", "turn_on", {"entity_id": _entity_id(hass, native, "relay1")}
    )
    command = await panel.command()
    other = await hass_ws_client(hass, hass_read_only_access_token)

    stolen = await _send(other, _result(panel.token, command["command_id"], "applied"))
    invented = await _send(
        panel.client, _result("x" * 32, command["command_id"], "applied")
    )

    assert stolen.get("error", {}).get("code") == "session_unknown", stolen
    assert invented.get("error", {}).get("code") == "session_unknown", invented
    assert not call.done()
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        await call


async def test_unknown_and_repeated_results_are_acknowledged_and_ignored(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Only the first final outcome of a sent command counts."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)

    unknown = await panel.answer("never-sent", "refused", code="expired")
    assert unknown["success"], unknown
    call = _call(
        hass, "switch", "turn_on", {"entity_id": _entity_id(hass, native, "relay1")}
    )
    command = await panel.command()
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        await call
    for repeat in (
        {"outcome": "failed", "code": "failed"},
        {"outcome": "applied"},
        {"outcome": "pending_approval"},
    ):
        response = await panel.answer(command["command_id"], **repeat)
        assert response["success"], response

    commands = await _commands(hass, native)
    assert [r["outcome"] for r in commands["recent"]] == ["applied"]
    assert commands["counts"] == {"applied": 1, "ignored": 4, "sent": 1}


@pytest.mark.parametrize(
    "fields",
    [
        {"outcome": "refused"},
        {"outcome": "failed"},
        {"outcome": "applied", "code": "failed"},
        {"outcome": "refused", "code": "busy"},
        {"outcome": "done"},
    ],
)
async def test_malformed_results_are_invalid_format_and_resolve_nothing(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    fields: dict[str, Any],
) -> None:
    """A result without its code, or with the wrong one, is refused."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    call = _call(
        hass, "switch", "turn_on", {"entity_id": _entity_id(hass, native, "relay1")}
    )
    command = await panel.command()

    response = await _send(
        panel.client,
        {
            "type": "panel_assistant/command_result",
            "session": panel.token,
            "command_id": command["command_id"],
            **fields,
        },
    )

    assert response.get("error", {}).get("code") == "invalid_format", response
    assert not call.done()
    assert (await panel.answer(command["command_id"], "applied"))["success"]
    async with asyncio.timeout(5):
        await call

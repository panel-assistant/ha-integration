"""Native transport WebSocket command tests."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.auth import EVENT_USER_REMOVED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, PanelHealth
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.transport import (
    _VALUE_VALIDATORS,
    DESCRIPTOR_SCHEMA,
    MAX_CHANNELS,
    ValueRejected,
    _validate_attributes,
    async_get_sessions,
    session_available,
    signal_session_changed,
)

DID = "d" * 64
OTHER_DID = "e" * 64
DIGEST = "c" * 64
HEALTH = PanelHealth(
    version="0.9.8-rc1",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    discovery_id=DID,
)
STATUS = PanelStatus(warning_count=0, capability_count=0)

SWITCH = {
    "channel": "relay3",
    "platform": "switch",
    "translation_key": "relay",
    "family": "relay",
    "index": 3,
    "unique_suffix": "relay3",
    "commandable": True,
}
NUMBER = {
    "channel": "volume",
    "platform": "number",
    "translation_key": "volume",
    "unique_suffix": "volume",
    "min": 0,
    "max": 100,
    "step": 1,
}
BUTTON_EVENT = {
    "channel": "button",
    "platform": "event",
    "translation_key": "button",
    "unique_suffix": "button",
    "options": ["keycode_home", "keycode_back"],
}
HELLO: dict[str, Any] = {
    "type": "panel_assistant/hello",
    "protocol": {"min": 1, "max": 1},
    "did": DID,
    "app": {"version": "0.9.8-rc1", "version_code": 790},
    "contract_digest": DIGEST,
    "capabilities": ["state", "events", "commands", "approval"],
    "channels": [SWITCH, NUMBER, BUTTON_EVENT],
}

type WsClientFactory = Callable[..., Awaitable[Any]]


def _hello(**changes: Any) -> dict[str, Any]:
    message = deepcopy(HELLO)
    message.update(changes)
    return message


async def _receive(client: Any) -> dict[str, Any]:
    """Wait a bounded time, so a missing reply fails instead of hanging."""
    async with asyncio.timeout(5):
        message: dict[str, Any] = await client.receive_json()
    return message


async def _send(client: Any, message: dict[str, Any]) -> dict[str, Any]:
    await client.send_json_auto_id(message)
    response: dict[str, Any] = await _receive(client)
    return response


async def _open(client: Any, **changes: Any) -> str:
    response = await _send(client, _hello(**changes))
    assert response["success"], response
    token: str = response["result"]["session"]
    return token


def _registry_digest(hass: HomeAssistant, entry_id: str) -> tuple[list[Any], list[Any]]:
    # Per-entry helpers, not the registries' mappings: newer Core refuses those.
    return (
        sorted(
            (item.platform, item.unique_id, item.entity_id, item.disabled_by)
            for item in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
        ),
        sorted(
            (sorted(item.identifiers), sorted(item.config_entries))
            for item in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
        ),
    )


@pytest.fixture
async def entry(hass: HomeAssistant) -> AsyncGenerator[MockConfigEntry]:
    """Load one panel entry whose health reports the test identity."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "panel.local"}
    )
    config_entry.add_to_hass(hass)
    executor = SimpleNamespace(
        async_acquire_finalizer=AsyncMock(return_value=True),
        async_release_finalizer=AsyncMock(),
    )
    manager = SimpleNamespace(
        async_list=AsyncMock(return_value=()), async_transition=AsyncMock()
    )
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.LOADED
        yield config_entry


async def test_non_admin_panel_account_opens_a_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """A dedicated non-admin panel account is accepted without admin rights."""
    assert not hass_read_only_user.is_admin
    registry_before = _registry_digest(hass, entry.entry_id)
    # The status sensor, the update entity and their device: never an empty digest.
    assert (len(registry_before[0]), len(registry_before[1])) == (2, 1)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello(future_field={"ignored": True}))

    assert response["success"], response
    result = response["result"]
    assert result["protocol"] == 1
    assert result["authority"] == "mqtt"
    assert result["capabilities"] == ["events", "state"]
    assert result["channels"] == {"accepted": 3, "unknown": []}
    assert isinstance(result["session"], str) and result["session"]
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    assert session.user_id == hass_read_only_user.id
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id
    assert entry.unique_id is None
    assert not session_available(hass, entry.entry_id)
    assert _registry_digest(hass, entry.entry_id) == registry_before


async def test_connection_close_marks_the_panel_gone(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Home Assistant's subscription teardown on close ends the session."""
    changes: list[bool] = []

    @callback
    def _record() -> None:
        # A callback runs on the event loop at once, so it sees each change.
        changes.append(session_available(hass, entry.entry_id))

    async_dispatcher_connect(hass, signal_session_changed(entry.entry_id), _record)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    response = await _send(
        client,
        {
            "type": "panel_assistant/report_state",
            "session": token,
            "sync": "full_begin",
            "observations": [],
        },
    )
    assert response["success"]
    response = await _send(
        client,
        {
            "type": "panel_assistant/report_state",
            "session": token,
            "sync": "full_end",
            "observations": [
                {"channel": "relay3", "state": "known", "value": True},
            ],
        },
    )
    assert response["success"]
    assert session_available(hass, entry.entry_id)

    await client.close()
    await hass.async_block_till_done()

    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert not session_available(hass, entry.entry_id)
    assert changes == [False, True, False]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["transport"] == {"connected": False}


async def test_panel_unsubscribe_also_ends_the_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Unsubscribing the hello subscription is the same teardown as a close."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_hello())
    hello = await _receive(client)
    assert hello["success"]

    response = await _send(
        client, {"type": "unsubscribe_events", "subscription": hello["id"]}
    )

    assert response["success"]
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_unknown_command_is_refused_by_core(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A command this integration does not register is Core's unknown_command."""
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, {"type": "panel_assistant/command_result"})

    assert not response["success"]
    assert response["error"]["code"] == "unknown_command"


def _too_many_channels() -> list[dict[str, Any]]:
    return [
        {
            "channel": f"relay{index}",
            "platform": "switch",
            "translation_key": "relay",
            "unique_suffix": f"relay{index}",
        }
        for index in range(MAX_CHANNELS + 1)
    ]


@pytest.mark.parametrize(
    "message",
    [
        pytest.param({"type": "panel_assistant/hello"}, id="empty_hello"),
        pytest.param(_hello(did="D" * 64), id="uppercase_did"),
        pytest.param(_hello(did="d" * 63), id="short_did"),
        pytest.param(_hello(protocol={"min": 2, "max": 1}), id="inverted_range"),
        pytest.param(_hello(protocol={"min": True, "max": 1}), id="boolean_version"),
        pytest.param(
            _hello(app={"version": "0.9.8-rc1", "version_code": "790"}),
            id="string_version_code",
        ),
        pytest.param(
            _hello(app={"version": "latest", "version_code": 790}),
            id="bad_app_version",
        ),
        pytest.param(_hello(contract_digest="x"), id="bad_digest"),
        pytest.param(_hello(capabilities="state"), id="capabilities_not_list"),
        pytest.param(
            _hello(channels=[{**SWITCH, "channel": "Relay3"}]), id="bad_channel"
        ),
        pytest.param(_hello(channels=[SWITCH, SWITCH]), id="duplicate_channel"),
        pytest.param(_hello(channels=_too_many_channels()), id="too_many_channels"),
        pytest.param(
            _hello(channels=[{**SWITCH, "platform": "vacuum"}]), id="bad_platform"
        ),
        pytest.param(
            _hello(channels=[{**SWITCH, "index": None}]), id="family_without_index"
        ),
        pytest.param(
            _hello(channels=[{**NUMBER, "min": 10, "max": 1}]), id="inverted_bounds"
        ),
        pytest.param(
            _hello(
                channels=[
                    {
                        "channel": "navbar",
                        "platform": "select",
                        "translation_key": "navbar",
                        "unique_suffix": "navbar",
                    }
                ]
            ),
            id="select_without_options",
        ),
        pytest.param(
            _hello(channels=[{**SWITCH, "unit": "\x00"}]), id="control_in_unit"
        ),
        pytest.param(_hello(channels=[{**SWITCH, "unit": ""}]), id="empty_unit"),
        pytest.param(
            _hello(channels=[{**SWITCH, "enabled_default": "yes"}]),
            id="string_boolean",
        ),
        pytest.param(_hello(channels=[{**NUMBER, "max": "100"}]), id="string_bound"),
        pytest.param(_hello(channels=[{**NUMBER, "step": 0}]), id="zero_step"),
        pytest.param(
            _hello(channels=[{**BUTTON_EVENT, "options": ["a", "a"]}]),
            id="duplicate_options",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_state",
                "session": "token",
                "sync": "delta",
                "observations": [{"channel": "relay3", "state": "unknown"}],
            },
            id="unknown_state",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_state",
                "session": "token",
                "sync": "later",
                "observations": [],
            },
            id="unknown_sync",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_state",
                "session": "token",
                "sync": "delta",
                "observations": [{"channel": "relay3", "state": "known"}],
            },
            id="known_without_value",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_state",
                "session": "token",
                "sync": "delta",
                "observations": {"channel": "relay3"},
            },
            id="observations_not_list",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_state",
                "session": "a b",
                "sync": "delta",
                "observations": [],
            },
            id="bad_session_token",
        ),
        pytest.param(
            {
                "type": "panel_assistant/report_event",
                "session": "token",
                "channel": "button",
                "event_id": -1,
                "event_type": "keycode_home",
            },
            id="negative_event_id",
        ),
    ],
)
async def test_malformed_envelope_is_invalid_format(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    message: dict[str, Any],
) -> None:
    """Every schema failure is refused as invalid_format and stores nothing."""
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, deepcopy(message))

    assert not response["success"]
    assert response["error"]["code"] == "invalid_format"
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert CONF_TRANSPORT_USER_ID not in entry.data


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"did": OTHER_DID}, "unknown_panel"),
        ({"did": None}, "panel_identity_unavailable"),
        ({"did": "absent"}, "panel_identity_unavailable"),
        ({"protocol": {"min": 2, "max": 3}}, "protocol_unsupported"),
    ],
)
async def test_hello_refusals(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    changes: dict[str, Any],
    code: str,
) -> None:
    """Refusals carry a closed code and open no session."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    message = _hello(**changes)
    if changes.get("did") == "absent":
        del message["did"]

    response = await _send(client, message)

    assert not response["success"]
    assert response["error"]["code"] == code
    assert async_get_sessions(hass).get(entry.entry_id) is None
    if code == "protocol_unsupported":
        assert response["error"]["translation_placeholders"] == {
            "panel_min": "2",
            "panel_max": "3",
            "integration_min": "1",
            "integration_max": "1",
        }


async def test_integration_loads_and_answers_with_no_panel_entry(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """With no entry the domain loads and hello says unknown_panel, not unknown."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello())

    assert response["error"]["code"] == "unknown_panel"


async def test_entry_not_ready_is_unknown_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An entry retrying setup is not a session target."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "panel.local"}, unique_id=DID
    )
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(side_effect=CannotConnectError),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello())

    assert response["error"]["code"] == "unknown_panel"


async def test_panel_is_bound_to_its_first_user(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_access_token: str,
    hass_admin_user: Any,
) -> None:
    """A later hello from a different user is refused and keeps the binding."""
    admin = await hass_ws_client(hass, hass_access_token)
    await _open(admin)
    other = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(other, _hello())

    assert response["error"]["code"] == "panel_user_mismatch"
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_admin_user.id
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.user_id == hass_admin_user.id


async def test_session_token_is_bound_to_its_connection(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A token on another connection, or an invented one, is session_unknown."""
    first = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(first)
    second = await hass_ws_client(hass, hass_read_only_access_token)
    report = {
        "type": "panel_assistant/report_state",
        "sync": "delta",
        "observations": [{"channel": "relay3", "state": "known", "value": True}],
    }

    stolen = await _send(second, {**report, "session": token})
    invented = await _send(first, {**report, "session": "invented"})

    assert stolen["error"]["code"] == "session_unknown"
    assert invented["error"]["code"] == "session_unknown"
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.observations == {}


async def test_newer_hello_supersedes_the_old_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A half-open old socket is told it was superseded and loses its token."""
    old = await hass_ws_client(hass, hass_read_only_access_token)
    await old.send_json_auto_id(_hello())
    old_hello = await _receive(old)
    old_token = old_hello["result"]["session"]
    new = await hass_ws_client(hass, hass_read_only_access_token)

    new_token = await _open(new)
    closed = await _receive(old)
    refused = await _send(
        old,
        {
            "type": "panel_assistant/report_state",
            "session": old_token,
            "sync": "delta",
            "observations": [],
        },
    )

    assert closed == {
        "id": old_hello["id"],
        "type": "event",
        "event": {"kind": "session_closed", "reason": "superseded"},
    }
    assert refused["error"]["code"] == "session_unknown"
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.token == new_token
    # Closing the superseded socket must not end the new session.
    await old.close()
    await hass.async_block_till_done()
    assert async_get_sessions(hass).get(entry.entry_id) is session


async def test_report_state_validates_each_observation(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Values are typed by the descriptor; failures are listed, not stored."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)

    response = await _send(
        client,
        {
            "type": "panel_assistant/report_state",
            "session": token,
            "sync": "delta",
            "observations": [
                {"channel": "relay3", "state": "known", "value": "ON"},
                {"channel": "volume", "state": "known", "value": 101},
                {"channel": "volume", "state": "known", "value": True},
                {"channel": "button", "state": "known", "value": "keycode_home"},
                {"channel": "screen", "state": "known", "value": True},
                {
                    "channel": "relay3",
                    "state": "known",
                    "value": False,
                    "attributes": {"Bad Key": 1},
                },
                {
                    "channel": "volume",
                    "state": "known",
                    "value": 40,
                    "refresh": True,
                    "attributes": {"source": "panel", "extra": None},
                    "unknown_field": "dropped",
                },
            ],
        },
    )

    assert response["success"]
    assert response["result"]["rejected"] == [
        {"channel": "relay3", "code": "invalid_value"},
        {"channel": "volume", "code": "invalid_value"},
        {"channel": "volume", "code": "invalid_value"},
        {"channel": "button", "code": "invalid_value"},
        {"channel": "screen", "code": "unknown_channel"},
        {"channel": "relay3", "code": "invalid_value"},
    ]
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    assert set(session.observations) == {"volume"}
    volume = session.observations["volume"]
    assert (volume.state, volume.value, volume.refresh) == ("known", 40, True)
    assert volume.attributes == {"source": "panel", "extra": None}
    assert session.rejected_observations == 6


async def test_full_sync_must_begin_before_it_ends(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Availability needs a full sync, and a stray full_end is refused."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)

    response = await _send(
        client,
        {
            "type": "panel_assistant/report_state",
            "session": token,
            "sync": "full_end",
            "observations": [],
        },
    )

    assert response["error"]["code"] == "invalid_format"
    assert not session_available(hass, entry.entry_id)


async def test_retried_full_end_is_accepted_with_its_observations(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel retrying a full_end whose result it missed does not loop."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    full_end = {
        "type": "panel_assistant/report_state",
        "session": token,
        "sync": "full_end",
        "observations": [{"channel": "relay3", "state": "known", "value": True}],
    }
    begin = await _send(client, {**full_end, "sync": "full_begin"})
    first = await _send(client, dict(full_end))

    retry = await _send(client, dict(full_end))

    assert begin["success"] and first["success"]
    assert retry["result"] == {"rejected": []}
    assert session_available(hass, entry.entry_id)


async def test_report_event_is_deduplicated_per_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A retried event is acknowledged again but counted once."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    event = {
        "type": "panel_assistant/report_event",
        "session": token,
        "channel": "button",
        "event_id": 1042,
        "event_type": "keycode_home",
    }

    first = await _send(client, dict(event))
    retry = await _send(client, dict(event))
    wrong_channel = await _send(client, {**event, "channel": "relay3"})
    wrong_type = await _send(client, {**event, "event_type": "keycode_menu"})

    assert first["success"] and retry["success"]
    assert wrong_channel["error"]["code"] == "unknown_channel"
    assert wrong_type["error"]["code"] == "invalid_value"
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.events_received == 1


async def test_entry_unload_closes_the_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Unloading an entry tells its panel and removes the subscription."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_hello())
    hello = await _receive(client)

    assert await hass.config_entries.async_unload(entry.entry_id)
    closed = await _receive(client)

    assert closed["id"] == hello["id"]
    assert closed["event"] == {"kind": "session_closed", "reason": "entry_unloaded"}
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_removed_user_loses_its_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Removing a user does not close its socket, so the session ends here."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)

    hass.bus.async_fire(EVENT_USER_REMOVED, {"user_id": hass_read_only_user.id})
    await hass.async_block_till_done()
    closed = await _receive(client)

    assert closed["event"] == {"kind": "session_closed", "reason": "user_removed"}
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_removed_user_releases_its_panel_for_a_new_account(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    hass_admin_user: Any,
) -> None:
    """A replaced panel account must not lock the panel out for good."""
    old_account = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(old_account)
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="beta",
        data={CONF_ADDRESS: "beta.local", CONF_TRANSPORT_USER_ID: "someone-else"},
    )
    other_entry.add_to_hass(hass)

    hass.bus.async_fire(EVENT_USER_REMOVED, {"user_id": hass_read_only_user.id})
    await hass.async_block_till_done()
    new_account = await hass_ws_client(hass, hass_access_token)

    await _open(new_account)
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_admin_user.id
    assert other_entry.data[CONF_TRANSPORT_USER_ID] == "someone-else"


async def test_diagnostics_show_the_session_without_identity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Diagnostics carry session facts but no token, identity or user."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    transport = diagnostics["transport"]
    assert transport["connected"] is True
    assert transport["authority"] == "mqtt"
    assert transport["channels"] == 3
    assert diagnostics["entry"][CONF_TRANSPORT_USER_ID] == "**REDACTED**"
    rendered = repr(diagnostics)
    for secret in (token, DID, hass_read_only_user.id):
        assert secret not in rendered


async def test_unavailable_observation_replaces_the_value(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A channel that left the runtime shape keeps no stale value."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    for observation in (
        {"channel": "relay3", "state": "known", "value": True},
        {"channel": "relay3", "state": "unavailable", "value": True},
    ):
        response = await _send(
            client,
            {
                "type": "panel_assistant/report_state",
                "session": token,
                "sync": "delta",
                "observations": [observation],
            },
        )
        assert response["result"] == {"rejected": []}

    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    relay = session.observations["relay3"]
    assert (relay.state, relay.value, relay.attributes) == ("unavailable", None, {})


async def test_two_entries_with_one_identity_refuse_hello(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An ambiguous identity is never resolved by guessing an entry."""
    twin = MockConfigEntry(
        domain=DOMAIN, title="twin", data={CONF_ADDRESS: "twin.local"}, unique_id=DID
    )
    twin.add_to_hass(hass)
    twin.mock_state(hass, ConfigEntryState.LOADED)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(client, _hello())

    assert response["error"]["code"] == "unknown_panel"
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_report_event_needs_a_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An event without a live session on this connection is session_unknown."""
    client = await hass_ws_client(hass, hass_read_only_access_token)

    response = await _send(
        client,
        {
            "type": "panel_assistant/report_event",
            "session": "invented",
            "channel": "button",
            "event_id": 1,
            "event_type": "keycode_home",
        },
    )

    assert response["error"]["code"] == "session_unknown"


def _descriptor(platform: str, **fields: Any) -> dict[str, Any]:
    descriptor: dict[str, Any] = DESCRIPTOR_SCHEMA(
        {
            "channel": "leaf",
            "platform": platform,
            "translation_key": "leaf",
            "unique_suffix": "leaf",
            **fields,
        }
    )
    return descriptor


@pytest.mark.parametrize(
    ("descriptor", "value", "stored"),
    [
        (_descriptor("binary_sensor"), False, False),
        (
            _descriptor("light", options=["rainbow"]),
            {
                "on": True,
                "brightness": 180,
                "color": {"r": 1, "g": 2, "b": 3},
                "effect": "rainbow",
                "extra": 1,
            },
            {
                "on": True,
                "brightness": 180,
                "color": {"r": 1, "g": 2, "b": 3},
                "effect": "rainbow",
            },
        ),
        (_descriptor("select", options=["stable", "prerelease"]), "stable", "stable"),
        (_descriptor("sensor"), 12.5, 12.5),
        (_descriptor("sensor", options=["auto"]), "auto", "auto"),
        (_descriptor("text"), "lovelace/home", "lovelace/home"),
        (
            _descriptor("image"),
            {"url": "http://panel.local:8888/snapshot.jpg"},
            {"url": "http://panel.local:8888/snapshot.jpg"},
        ),
        (
            _descriptor("update"),
            {
                "installed_version": "0.9.7",
                "latest_version": "0.9.8",
                "release_url": "https://example.com/r",
                "in_progress": False,
                "title": "dropped",
            },
            {
                "installed_version": "0.9.7",
                "latest_version": "0.9.8",
                "release_url": "https://example.com/r",
                "in_progress": False,
            },
        ),
    ],
)
def test_values_are_stored_as_validated_copies(
    descriptor: dict[str, Any], value: Any, stored: Any
) -> None:
    """Accepted values keep only the fields the platform defines."""
    assert _VALUE_VALIDATORS[descriptor["platform"]](value, descriptor) == stored


@pytest.mark.parametrize(
    ("descriptor", "value"),
    [
        (_descriptor("switch"), 1),
        (_descriptor("light"), {"brightness": 10}),
        (_descriptor("light"), {"on": True, "brightness": 256}),
        (_descriptor("light"), {"on": True, "color": {"r": 1, "g": 2}}),
        (_descriptor("light", options=["rainbow"]), {"on": True, "effect": "strobe"}),
        (_descriptor("number"), float("inf")),
        (_descriptor("number"), 2**64),
        (_descriptor("select", options=["stable"]), "Pre-release"),
        (_descriptor("sensor"), "12.5"),
        (_descriptor("text"), "x" * 256),
        (_descriptor("text"), "line\nbreak"),
        (_descriptor("image"), {"url": "file:///etc/passwd"}),
        (_descriptor("image"), {"url": "http://"}),
        (_descriptor("image"), {"url": "http://panel.local/snap shot.jpg"}),
        (_descriptor("update"), {"release_url": "http://example.com/r"}),
        (_descriptor("update"), {"in_progress": "no"}),
        (_descriptor("update"), {"installed_version": 7}),
        (_descriptor("button"), None),
        (_descriptor("event"), "keycode_home"),
    ],
)
def test_values_that_do_not_fit_the_platform_are_rejected(
    descriptor: dict[str, Any], value: Any
) -> None:
    """A mistyped, unbounded or unsafe value never reaches the session store."""
    with pytest.raises(ValueRejected):
        _VALUE_VALIDATORS[descriptor["platform"]](value, descriptor)


@pytest.mark.parametrize(
    "attributes",
    [
        {f"key{index}": index for index in range(33)},
        {"key": [1]},
        {"key": {"nested": 1}},
        {"key": "x" * 256},
        [],
    ],
)
def test_attributes_are_bounded(attributes: Any) -> None:
    """Attributes are a small flat map of facts."""
    with pytest.raises(ValueRejected):
        _validate_attributes(attributes)


async def test_zeroconf_identity_matches_before_health_reports_one(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A discovered entry is found by its identity while health omits it."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "panel.local"}, unique_id=DID
    )
    config_entry.add_to_hass(hass)
    health = PanelHealth(
        version="0.9.8-rc1", panel_id="alpha", build="1000", config_hash="1a2b3c4d"
    )
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant._async_reconcile_install_receipt",
            AsyncMock(),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    client = await hass_ws_client(hass, hass_read_only_access_token)

    await _open(client)

    assert async_get_sessions(hass).get(config_entry.entry_id) is not None
    assert config_entry.unique_id == DID

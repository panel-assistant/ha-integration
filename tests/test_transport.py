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
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.translation import async_get_translations
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
    async_bind_user,
    async_get_sessions,
    async_raise_binding_issue,
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


ISSUE_ID_PREFIX = "panel_user_mismatch_"


def _issue(hass: HomeAssistant, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID_PREFIX + entry_id)


@pytest.fixture
async def entry(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """Load one panel entry that an administrator bound to the panel account."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: "panel.local",
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
        },
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
    assert result["authority"] == "shadow"
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


async def test_highest_family_index_opens_a_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel serving the 64th relay, the panel's cap, is described and accepted."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    relay64 = {**SWITCH, "channel": "relay64", "unique_suffix": "relay64", "index": 64}

    response = await _send(client, _hello(channels=[relay64]))

    assert response["success"], response
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    assert session.descriptors["relay64"]["index"] == 64


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
    transport = diagnostics["transport"]
    assert transport["connected"] is False
    assert transport["closed_at"] is not None
    assert transport["full_sync_complete"] is True
    assert transport["observations"] == 1


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

    response = await _send(client, {"type": "panel_assistant/subscribe_commands"})

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
            _hello(
                channels=[
                    {
                        **SWITCH,
                        "channel": "relay65",
                        "unique_suffix": "relay65",
                        "index": 65,
                    }
                ]
            ),
            id="family_index_above_cap",
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
    assert _issue(hass, entry.entry_id) is None


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


async def _synced(client: Any, token: str) -> None:
    for sync in ("full_begin", "full_end"):
        response = await _send(
            client,
            {
                "type": "panel_assistant/report_state",
                "session": token,
                "sync": sync,
                "observations": [],
            },
        )
        assert response["success"], response


async def test_an_event_before_the_full_sync_is_refused_and_not_counted(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A refused early event leaves its ID free for the retry after the sync."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    event = {
        "type": "panel_assistant/report_event",
        "session": token,
        "channel": "button",
        "event_id": 5,
        "event_type": "keycode_home",
    }

    early = await _send(client, dict(event))
    await _synced(client, token)
    retry = await _send(client, dict(event))

    assert early["error"]["code"] == "invalid_format"
    assert retry["success"]
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    assert (session.events_received, session.last_event_id) == (1, 5)


async def test_report_event_is_deduplicated_per_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A retried event is acknowledged again but counted once."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    await _synced(client, token)
    event = {
        "type": "panel_assistant/report_event",
        "session": token,
        "channel": "button",
        "event_id": 1042,
        "event_type": "keycode_home",
    }

    first = await _send(client, dict(event))
    retry = await _send(client, dict(event))
    older = await _send(client, {**event, "event_id": 1041})
    wrong_channel = await _send(client, {**event, "channel": "relay3"})
    wrong_type = await _send(client, {**event, "event_type": "keycode_menu"})

    assert first["success"] and retry["success"] and older["success"]
    assert wrong_channel["error"]["code"] == "unknown_channel"
    assert wrong_type["error"]["code"] == "invalid_value"
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.events_received == 1


async def test_event_deduplication_holds_for_the_whole_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """However many events a session carries, an early event is never recounted."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _open(client)
    await _synced(client, token)
    event = {
        "type": "panel_assistant/report_event",
        "session": token,
        "channel": "button",
        "event_type": "keycode_home",
    }
    count = 1000
    for event_id in range(count):
        await client.send_json_auto_id({**event, "event_id": event_id})
    for _ in range(count):
        assert (await _receive(client))["success"]

    retry = await _send(client, {**event, "event_id": 0})
    newer = await _send(client, {**event, "event_id": count})

    assert retry["success"] and newer["success"]
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.events_received == count + 1


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


async def test_removed_user_releases_its_panel_and_its_requests(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_read_only_user: Any,
) -> None:
    """Removal clears the user's bindings and requests, and nobody else's."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        title="beta",
        data={CONF_ADDRESS: "beta.local", CONF_TRANSPORT_USER_ID: "someone-else"},
    )
    other_entry.add_to_hass(hass)
    async_raise_binding_issue(hass, other_entry, hass_read_only_user.id)
    third_entry = MockConfigEntry(
        domain=DOMAIN, title="gamma", data={CONF_ADDRESS: "gamma.local"}
    )
    third_entry.add_to_hass(hass)
    async_raise_binding_issue(hass, third_entry, "someone-else")

    hass.bus.async_fire(EVENT_USER_REMOVED, {"user_id": hass_read_only_user.id})
    await hass.async_block_till_done()

    assert CONF_TRANSPORT_USER_ID not in entry.data
    assert other_entry.data[CONF_TRANSPORT_USER_ID] == "someone-else"
    assert _issue(hass, other_entry.entry_id) is None
    assert _issue(hass, third_entry.entry_id) is not None


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
    assert transport["authority"] == "shadow"
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
        (_descriptor("sensor", unit="%"), "12.5"),
        (_descriptor("sensor", state_class="measurement"), "12.5"),
        (_descriptor("sensor", device_class="temperature"), "12.5"),
        (_descriptor("sensor", device_class="timestamp"), "yesterday"),
        (_descriptor("sensor", device_class="timestamp"), "2026-09-14T10:00:00"),
        (_descriptor("sensor", device_class="timestamp"), 1700000000),
        (_descriptor("sensor"), "line\nbreak"),
        (_descriptor("text"), "x" * 256),
        (_descriptor("text"), "line\nbreak"),
        (_descriptor("image"), {"url": "file:///etc/passwd"}),
        (_descriptor("image"), {"url": "http://"}),
        (_descriptor("image"), {"url": "http://panel.local/snap shot.jpg"}),
        (_descriptor("update"), {"release_url": "http://example.com/r"}),
        (_descriptor("update"), {"in_progress": "no"}),
        (_descriptor("update"), {"installed_version": 7}),
        (_descriptor("button"), None),
        (_descriptor("event", options=["keycode_home"]), "keycode_home"),
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
    hass_read_only_user: Any,
) -> None:
    """A discovered entry is found by its identity while health omits it."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: "panel.local",
            CONF_TRANSPORT_USER_ID: hass_read_only_user.id,
        },
        unique_id=DID,
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


# ---------------------------------------------------------------------------
# Binding: only an administrator's confirmation binds a panel to a user.


async def _unbind(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    data = dict(entry.data)
    del data[CONF_TRANSPORT_USER_ID]
    hass.config_entries.async_update_entry(entry, data=data)
    await hass.async_block_till_done()


async def _start_fix_flow(client: Any, entry_id: str) -> tuple[int, dict[str, Any]]:
    response = await client.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": ISSUE_ID_PREFIX + entry_id},
    )
    body: dict[str, Any] = await response.json() if response.status == 200 else {}
    return response.status, body


async def _submit_fix_flow(client: Any, flow_id: str) -> dict[str, Any]:
    response = await client.post(f"/api/repairs/issues/fix/{flow_id}", json={})
    assert response.status == 200, await response.text()
    body: dict[str, Any] = await response.json()
    return body


@pytest.fixture
async def repairs(hass: HomeAssistant) -> None:
    """Load Repairs, whose fix flow views are an administrator's only way in."""
    assert await async_setup_component(hass, "repairs", {})
    await hass.async_block_till_done()


async def test_squatter_cannot_bind_an_unbound_panel(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Abuse case 1: the first account to say hello no longer claims the panel."""
    await _unbind(hass, entry)
    squatter = await hass_ws_client(hass, hass_read_only_access_token)

    first = await _send(squatter, _hello())
    second = await _send(squatter, _hello())

    for response in (first, second):
        assert response["error"]["code"] == "panel_user_mismatch"
    assert CONF_TRANSPORT_USER_ID not in entry.data
    assert async_get_sessions(hass).get(entry.entry_id) is None
    issue = _issue(hass, entry.entry_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_key == "panel_user_mismatch"
    assert issue.translation_placeholders == {"panel": "alpha"}
    assert issue.data == {"entry_id": entry.entry_id, "user_id": hass_read_only_user.id}


async def test_other_user_cannot_take_over_a_bound_panel(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    hass_admin_user: Any,
) -> None:
    """Abuse case 2: even an administrator's own socket cannot rebind by hello."""
    panel = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(panel)
    session = async_get_sessions(hass).get(entry.entry_id)
    intruder = await hass_ws_client(hass, hass_access_token)

    refused = await _send(intruder, _hello())
    reconnect = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(reconnect)

    assert refused["error"]["code"] == "panel_user_mismatch"
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id
    current = async_get_sessions(hass).get(entry.entry_id)
    assert current is not None and current is not session
    assert current.user_id == hass_read_only_user.id
    # The bound panel reconnecting does not hide someone else's request.
    issue = _issue(hass, entry.entry_id)
    assert issue is not None and issue.data is not None
    assert issue.data["user_id"] == hass_admin_user.id


async def test_removed_users_surviving_socket_cannot_reclaim_the_panel(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Abuse case 3: a real removal leaves a socket that can neither bind nor ask."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)

    await hass.auth.async_remove_user(hass_read_only_user)
    await hass.async_block_till_done()
    closed = await _receive(client)
    refused = await _send(client, _hello())

    assert closed["event"] == {"kind": "session_closed", "reason": "user_removed"}
    assert refused["error"]["code"] == "panel_user_mismatch"
    assert CONF_TRANSPORT_USER_ID not in entry.data
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert _issue(hass, entry.entry_id) is None


async def test_bound_user_hello_withdraws_its_own_request(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """A request that the binding already satisfies is not left behind."""
    async_raise_binding_issue(hass, entry, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    await _open(client)

    assert _issue(hass, entry.entry_id) is None


async def test_administrator_confirms_the_binding(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """The Repairs flow shows who asked, and binds them only on confirmation."""
    await _unbind(hass, entry)
    panel = await hass_ws_client(hass, hass_read_only_access_token)
    assert (await _send(panel, _hello()))["error"]["code"] == "panel_user_mismatch"
    admin = await hass_client()

    status, form = await _start_fix_flow(admin, entry.entry_id)

    assert status == 200
    assert form["type"] == "form"
    assert form["step_id"] == "confirm_bind"
    assert form["description_placeholders"] == {
        "panel": "alpha",
        "user": hass_read_only_user.name,
    }
    assert CONF_TRANSPORT_USER_ID not in entry.data

    result = await _submit_fix_flow(admin, form["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id
    assert _issue(hass, entry.entry_id) is None
    await _open(panel)


async def test_non_administrator_cannot_confirm_a_binding(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Core's Repairs views refuse the fix flow to a non-administrator."""
    await _unbind(hass, entry)
    panel = await hass_ws_client(hass, hass_read_only_access_token)
    await _send(panel, _hello())
    user = await hass_client(hass_read_only_access_token)

    status, _ = await _start_fix_flow(user, entry.entry_id)

    assert status == 401
    assert CONF_TRANSPORT_USER_ID not in entry.data


async def test_administrator_rebinds_and_the_old_session_ends(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    hass_admin_user: Any,
) -> None:
    """A rebind names both users, then ends the old user's session."""
    old = await hass_ws_client(hass, hass_read_only_access_token)
    await old.send_json_auto_id(_hello())
    old_hello = await _receive(old)
    new = await hass_ws_client(hass, hass_access_token)
    assert (await _send(new, _hello()))["error"]["code"] == "panel_user_mismatch"
    admin = await hass_client()

    _, form = await _start_fix_flow(admin, entry.entry_id)
    assert form["step_id"] == "confirm_rebind"
    assert form["description_placeholders"] == {
        "panel": "alpha",
        "user": hass_admin_user.name,
        "bound_user": hass_read_only_user.name,
    }
    result = await _submit_fix_flow(admin, form["flow_id"])
    closed = await _receive(old)

    assert result["type"] == "create_entry"
    assert closed == {
        "id": old_hello["id"],
        "type": "event",
        "event": {"kind": "session_closed", "reason": "binding_changed"},
    }
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_admin_user.id
    await _open(new)
    refused = await _send(old, _hello())
    assert refused["error"]["code"] == "panel_user_mismatch"


async def test_confirmation_binds_the_user_it_showed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """A request arriving while the form is open cannot swap the user bound."""
    await _unbind(hass, entry)
    panel = await hass_ws_client(hass, hass_read_only_access_token)
    await _send(panel, _hello())
    admin = await hass_client()
    _, form = await _start_fix_flow(admin, entry.entry_id)
    racer = await hass_ws_client(hass, hass_access_token)
    await _send(racer, _hello())

    result = await _submit_fix_flow(admin, form["flow_id"])

    assert result["type"] == "create_entry"
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id


async def test_confirmation_refuses_a_user_removed_while_the_form_is_open(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Confirming a user who no longer exists binds nothing."""
    await _unbind(hass, entry)
    panel = await hass_ws_client(hass, hass_read_only_access_token)
    await _send(panel, _hello())
    admin = await hass_client()
    _, form = await _start_fix_flow(admin, entry.entry_id)

    await hass.auth.async_remove_user(hass_read_only_user)
    await hass.async_block_till_done()
    result = await _submit_fix_flow(admin, form["flow_id"])

    assert result["type"] == "abort"
    assert result["reason"] == "user_unavailable"
    assert CONF_TRANSPORT_USER_ID not in entry.data


@pytest.mark.parametrize("user_id", ["no-such-user", "inactive", "system"])
async def test_request_for_an_unusable_user_is_withdrawn(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    user_id: str,
) -> None:
    """A missing, deactivated or system user is never offered for binding."""
    await _unbind(hass, entry)
    if user_id == "inactive":
        user = await hass.auth.async_create_user("Inactive")
        await hass.auth.async_deactivate_user(user)
        user_id = user.id
    elif user_id == "system":
        user_id = (await hass.auth.async_create_system_user("System")).id
    async_raise_binding_issue(hass, entry, user_id)
    admin = await hass_client()

    _, result = await _start_fix_flow(admin, entry.entry_id)

    assert result["type"] == "abort"
    assert result["reason"] == "user_unavailable"
    assert CONF_TRANSPORT_USER_ID not in entry.data
    assert _issue(hass, entry.entry_id) is None


async def test_removed_entry_withdraws_its_request(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_read_only_user: Any,
) -> None:
    """Removing a panel removes its issue, and an open form cannot bind it."""
    async_raise_binding_issue(hass, entry, hass_read_only_user.id)
    admin = await hass_client()
    _, form = await _start_fix_flow(admin, entry.entry_id)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry.entry_id) is None
    result = await _submit_fix_flow(admin, form["flow_id"])

    assert result["type"] == "abort"
    assert result["reason"] == "entry_removed"


async def test_binding_repair_is_translated_in_english(hass: HomeAssistant) -> None:
    """The issue title and both confirmations load with their placeholders."""
    assert await async_setup_component(hass, DOMAIN, {})
    strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.panel_user_mismatch"

    assert strings[f"{prefix}.title"] == "Confirm the Home Assistant user for {panel}"
    bind = strings[f"{prefix}.fix_flow.step.confirm_bind.description"]
    rebind = strings[f"{prefix}.fix_flow.step.confirm_rebind.description"]
    assert "{panel}" in bind and "{user}" in bind
    assert all(name in rebind for name in ("{panel}", "{user}", "{bound_user}"))
    for reason in ("entry_removed", "user_unavailable"):
        assert strings[f"{prefix}.fix_flow.abort.{reason}"]


async def test_confirming_the_bound_user_keeps_its_session(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """Only a change of user ends a session; confirming the same user does not."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    session = async_get_sessions(hass).get(entry.entry_id)

    async_bind_user(hass, entry, hass_read_only_user.id)

    assert async_get_sessions(hass).get(entry.entry_id) is session


async def test_request_from_someone_new_is_not_hidden_by_an_ignore(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_admin_user: Any,
    hass_read_only_user: Any,
) -> None:
    """Anyone may ignore an issue, so an ignore covers only the user it named."""
    await _unbind(hass, entry)
    async_raise_binding_issue(hass, entry, "squatter")
    ir.async_ignore_issue(hass, DOMAIN, ISSUE_ID_PREFIX + entry.entry_id, True)

    async_raise_binding_issue(hass, entry, "squatter")
    repeated = _issue(hass, entry.entry_id)
    async_raise_binding_issue(hass, entry, hass_read_only_user.id)
    fresh = _issue(hass, entry.entry_id)

    assert repeated is not None and repeated.dismissed_version is not None
    assert fresh is not None and fresh.dismissed_version is None
    assert fresh.data == {"entry_id": entry.entry_id, "user_id": hass_read_only_user.id}


async def test_stale_confirmation_does_not_undo_a_newer_binding(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    repairs: None,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_admin_user: Any,
) -> None:
    """A form describing a binding that has since changed is shown again."""
    await _unbind(hass, entry)
    async_raise_binding_issue(hass, entry, hass_read_only_user.id)
    admin = await hass_client()
    _, form = await _start_fix_flow(admin, entry.entry_id)
    assert form["step_id"] == "confirm_bind"
    async_bind_user(hass, entry, hass_admin_user.id)

    again = await _submit_fix_flow(admin, form["flow_id"])

    assert again["type"] == "form"
    assert again["step_id"] == "confirm_rebind"
    assert again["description_placeholders"]["bound_user"] == hass_admin_user.name
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_admin_user.id
    result = await _submit_fix_flow(admin, form["flow_id"])
    assert result["type"] == "create_entry"
    assert entry.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id

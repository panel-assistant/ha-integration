"""Availability is the union of the outbound poll and the inbound session.

A panel that is talking to Home Assistant is connected, whatever a poll of
the address stored for it says. These tests pin that invariant, the address
repair that follows from it, and the one thing that must not follow: a panel
with neither a session nor an answering address is still unavailable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, EVENT_STATE_CHANGED, STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.address import (
    ISSUE_PANEL_ADDRESS_UNREACHABLE,
    ISSUE_PANEL_ADDRESS_UNVERIFIED,
    address_issue_id,
    session_candidate,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelAddress,
    normalize_address,
)
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.transport import async_get_sessions

from .test_transport import DID, HEALTH, OTHER_DID, STATUS, WsClientFactory, _open

STATUS_ENTITY = "sensor.alpha_status"
UPDATE_ENTITY = "update.alpha_ha_paneld_update"
STORED = "panel.local"
MOVED = "192.0.2.9"
# The add flow pins install targets to the installer's LAN ranges, so the
# address a person types for a moved panel is a private one.
LAN = "192.168.1.29"


def _health_by_host(
    answers: dict[str, Any],
) -> Callable[[HaPaneldClient], Any]:
    """Answer a health read by the address the client polls, like a network."""

    async def _get_health(self: HaPaneldClient) -> Any:
        answer = answers[self.address.host]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return _get_health


async def _load(
    hass: HomeAssistant,
    user_id: str,
    unique_id: str | None = None,
    address: str = STORED,
) -> MockConfigEntry:
    """Load one bound panel entry whose stored address answers."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: address, CONF_TRANSPORT_USER_ID: user_id},
        unique_id=unique_id,
    )
    config_entry.add_to_hass(hass)
    executor = SimpleNamespace(
        async_reconcile_entry=AsyncMock(),
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
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry


@pytest.fixture
async def entry(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """One loaded, bound panel entry."""
    yield await _load(hass, hass_read_only_user.id)


async def _connect(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    entry: MockConfigEntry,
    remote: str | None = MOVED,
    protocol: dict[str, int] | None = None,
) -> Any:
    """Open the panel's session, connecting from the given address."""
    client = await hass_ws_client(hass, token)
    await _open(client, **({"protocol": protocol} if protocol else {}))
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    # The test socket connects from loopback; the panel connects from wherever
    # it is, which is what the address repair reads.
    session.remote = remote
    return client


async def test_restart_notice_tracks_session_return_and_keeps_entities_available(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    client = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        entry,
        protocol={"min": 3, "max": 3},
    )
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    await client.send_json_auto_id(
        {
            "type": "panel_assistant/restart_notice",
            "session": session.token,
            "scope": "app",
            "reason": "settings",
            "expected_back_ms": 45000,
        }
    )
    reply = await client.receive_json()
    assert reply["success"], reply
    await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state is not None and state.state == "Restarting (settings)"
    assert state.attributes["reason"] == "settings"
    assert state.attributes["expected_back_ms"] == 45000
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE
    admin = await hass_ws_client(hass)
    await admin.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    listed = await admin.receive_json()
    assert listed["success"], listed
    assert listed["result"]["panels"][0]["state"] == "restarting"
    assert listed["result"]["panels"][0]["reason"] == "settings"

    # The old socket goes away before the replacement appears.
    await client.close()
    await hass.async_block_till_done()
    assert _state(hass, STATUS_ENTITY) == "Restarting (settings)"
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    await hass.async_block_till_done()
    assert _state(hass, STATUS_ENTITY) == "online"


async def test_http_restart_fallback_clears_when_health_returns(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    restarting = replace(HEALTH, restart=("panel", "reboot", 45000))
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=restarting)
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
    state = hass.states.get(STATUS_ENTITY)
    assert state is not None and state.state == "Restarting (reboot)"
    assert state.attributes["scope"] == "panel"
    assert state.attributes["reason"] == "reboot"
    await _poll(hass, entry, {STORED: HEALTH})
    assert _state(hass, STATUS_ENTITY) == "online"


async def test_restart_notice_requires_protocol_two_and_bounded_schema(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "installation_identity": False, CONF_ADDRESS: "127.0.0.1"},
    )
    client = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        entry,
        protocol={"min": 1, "max": 1},
    )
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    request = {
        "type": "panel_assistant/restart_notice",
        "session": session.token,
        "scope": "app",
        "reason": "recovery",
        "expected_back_ms": 1,
    }
    await client.send_json_auto_id(request)
    assert (await client.receive_json())["error"]["code"] == "invalid_format"
    assert _state(hass, STATUS_ENTITY) in {"online", "connected"}
    await client.close()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": True}
    )

    client = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        entry,
        protocol={"min": 3, "max": 3},
    )
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    request["session"] = session.token
    for change in (
        {"scope": "other"},
        {"reason": "arbitrary"},
        {"expected_back_ms": True},
        {"expected_back_ms": 300_001},
        {"session": "wrong"},
    ):
        await client.send_json_auto_id({**request, **change})
        refused = await client.receive_json()
        assert not refused["success"]
        assert _state(hass, STATUS_ENTITY) in {"online", "connected"}
    await client.send_json_auto_id(request)
    assert (await client.receive_json())["success"]


async def test_restart_notice_expires_to_unavailable_without_a_return(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A vanished panel remains Restarting only for its announced window."""
    client = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        entry,
        remote=None,
        protocol={"min": 3, "max": 3},
    )
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _state(hass, STATUS_ENTITY) == "connected"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE

    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    await client.send_json_auto_id(
        {
            "type": "panel_assistant/restart_notice",
            "session": session.token,
            "scope": "panel",
            "reason": "reboot",
            "expected_back_ms": 1000,
        }
    )
    assert (await client.receive_json())["success"]
    await client.close()
    await hass.async_block_till_done()
    assert _state(hass, STATUS_ENTITY) == "Restarting (reboot)"
    assert hass.states.get(STATUS_ENTITY).attributes["reason"] == "reboot"
    assert _state(hass, UPDATE_ENTITY) == STATE_UNAVAILABLE

    admin = await hass_ws_client(hass)
    await admin.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    listed = await admin.receive_json()
    assert listed["success"]
    assert listed["result"]["panels"][0]["state"] == "restarting"
    assert listed["result"]["panels"][0]["reason"] == "reboot"

    await asyncio.sleep(1.05)
    await hass.async_block_till_done()
    assert _state(hass, STATUS_ENTITY) == STATE_UNAVAILABLE
    await admin.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    listed = await admin.receive_json()
    assert listed["success"]
    assert listed["result"]["panels"][0]["state"] == "unreachable"


async def _poll(
    hass: HomeAssistant, entry: MockConfigEntry, answers: dict[str, Any]
) -> None:
    """Run one poll against a network that answers per address."""
    with (
        patch.object(HaPaneldClient, "async_get_health", _health_by_host(answers)),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, address_issue_id(entry.entry_id))


# ---------------------------------------------------------------------------
# The union.


async def test_failed_poll_with_a_live_session_stays_available(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel holding an open session is connected, whatever the poll says."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
    assert _state(hass, STATUS_ENTITY) == "online"

    await _poll(hass, entry, {STORED: CannotConnectError()})

    coordinator = entry.runtime_data.coordinator
    assert not coordinator.last_update_success
    assert coordinator.connected
    assert coordinator.available
    assert _state(hass, STATUS_ENTITY) == "connected"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE
    attributes = hass.states.get(STATUS_ENTITY).attributes
    assert attributes["reachable"] is False
    assert attributes["connected"] is True
    admin = await hass_ws_client(hass)
    await admin.send_json_auto_id({"type": "panel_assistant/embed_panels"})
    reply = await admin.receive_json()
    assert reply["success"]
    assert reply["result"]["panels"][0]["state"] == "reachable"


async def test_successful_poll_without_a_session_is_available(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The outbound poll alone still answers, as it always has."""
    coordinator = entry.runtime_data.coordinator
    assert coordinator.last_update_success
    assert not coordinator.connected
    assert coordinator.available
    assert _state(hass, STATUS_ENTITY) == "online"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE
    attributes = hass.states.get(STATUS_ENTITY).attributes
    assert attributes["reachable"] is True
    assert attributes["connected"] is False


async def test_neither_poll_nor_session_is_unavailable(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """A panel nothing can reach is unavailable; nothing became permanent."""
    await _poll(hass, entry, {STORED: CannotConnectError()})

    coordinator = entry.runtime_data.coordinator
    assert not coordinator.last_update_success
    assert not coordinator.connected
    assert not coordinator.available
    assert _state(hass, STATUS_ENTITY) == STATE_UNAVAILABLE
    assert _state(hass, UPDATE_ENTITY) == STATE_UNAVAILABLE
    # No session means nothing to report beyond the failed poll itself.
    assert _issue(hass, entry) is None


async def test_session_ending_while_the_poll_fails_takes_the_panel_unavailable(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Availability follows the session at once, not at the next poll."""
    client = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, entry, None
    )
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _state(hass, STATUS_ENTITY) == "connected"

    await client.close()
    await hass.async_block_till_done()

    assert _state(hass, STATUS_ENTITY) == STATE_UNAVAILABLE


async def test_session_opening_while_the_poll_fails_polls_at_once(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel connecting is the moment its address can be repaired."""
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _state(hass, STATUS_ENTITY) == STATE_UNAVAILABLE

    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({STORED: CannotConnectError(), "127.0.0.1": HEALTH}),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await _connect(
            hass, hass_ws_client, hass_read_only_access_token, entry, "127.0.0.1"
        )
        await hass.async_block_till_done()

    # The session alone made the panel available; the poll it triggered then
    # adopted the address the panel connected from.
    assert entry.runtime_data.coordinator.last_update_success
    assert entry.data[CONF_ADDRESS] == "127.0.0.1"
    assert _state(hass, STATUS_ENTITY) == "online"


# ---------------------------------------------------------------------------
# Address repair from the session.


async def test_session_address_is_adopted_after_identity_verification(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The address a connected panel talks from replaces a dead stored one."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)

    await _poll(hass, entry, {STORED: CannotConnectError(), MOVED: HEALTH})

    assert entry.data[CONF_ADDRESS] == MOVED
    assert entry.runtime_data.client.address == normalize_address(MOVED)
    assert entry.runtime_data.coordinator.last_update_success
    assert _state(hass, STATUS_ENTITY) == "online"
    assert _issue(hass, entry) is None
    # The entry was written, not reloaded: the session survived its repair.
    assert async_get_sessions(hass).get(entry.entry_id) is not None
    assert entry.state is ConfigEntryState.LOADED


async def test_the_device_card_links_to_the_adopted_address(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The card's configuration link follows a moved panel without a reload."""
    card = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert card is not None
    assert card.configuration_url == str(normalize_address(STORED).base_url)

    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    await _poll(hass, entry, {STORED: CannotConnectError(), MOVED: HEALTH})

    card = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert card is not None
    assert card.configuration_url == str(normalize_address(MOVED).base_url)
    assert entry.state is ConfigEntryState.LOADED


async def test_the_poll_that_adopts_an_address_reads_status_there_too(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The entry write moves the running client before the same poll goes on."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    polled: list[str] = []

    async def _get_status(self: HaPaneldClient, **_kwargs: Any) -> Any:
        polled.append(self.address.host)
        return STATUS

    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({STORED: CannotConnectError(), MOVED: HEALTH}),
        ),
        patch.object(HaPaneldClient, "async_get_status", _get_status),
    ):
        await entry.runtime_data.coordinator.async_refresh()

    assert polled == [MOVED]


@pytest.mark.parametrize(
    "answer",
    [
        replace(HEALTH, discovery_id=OTHER_DID),
        replace(HEALTH, discovery_id=None),
        CannotConnectError(),
    ],
    ids=["another_panel", "no_identity", "no_answer"],
)
async def test_unverified_session_address_is_refused_and_reported(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    answer: Any,
) -> None:
    """An address that does not answer as this panel is never written."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)

    await _poll(hass, entry, {STORED: CannotConnectError(), MOVED: answer})

    assert entry.data[CONF_ADDRESS] == STORED
    assert entry.runtime_data.client.address == normalize_address(STORED)
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_PANEL_ADDRESS_UNVERIFIED
    assert issue.translation_placeholders == {
        "panel": "alpha",
        "address": STORED,
        "session_address": MOVED,
    }
    # Refused is not unavailable: the panel is still connected.
    assert _state(hass, STATUS_ENTITY) == "connected"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE


@pytest.mark.parametrize(
    "remote", [None, "8.8.8.8", "proxy.example"], ids=["unknown", "public", "name"]
)
async def test_session_without_a_usable_address_is_reported_as_unreachable(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    remote: str | None,
) -> None:
    """Connected but unreachable is said as exactly that, never as unavailable."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, remote)

    await _poll(hass, entry, {STORED: CannotConnectError()})

    assert entry.data[CONF_ADDRESS] == STORED
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_PANEL_ADDRESS_UNREACHABLE
    assert issue.translation_placeholders == {
        "panel": "alpha",
        "address": STORED,
        "session_address": remote or STORED,
    }
    assert _state(hass, STATUS_ENTITY) == "connected"


async def test_address_report_clears_when_the_stored_address_answers(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A transient outbound fault leaves nothing behind once it passes."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _issue(hass, entry) is not None

    await _poll(hass, entry, {STORED: HEALTH})

    assert _issue(hass, entry) is None
    assert _state(hass, STATUS_ENTITY) == "online"


@pytest.mark.parametrize(
    ("remote", "stored", "expected"),
    [
        ("192.0.2.9", "panel.local", "192.0.2.9"),
        ("192.0.2.9", "panel.local:8889", "192.0.2.9:8889"),
        ("::ffff:192.0.2.9", "panel.local", "192.0.2.9"),
        ("fd00::9", "panel.local", "[fd00::9]"),
        ("100.64.1.9", "panel.local", "100.64.1.9"),
        ("192.0.2.9", "192.0.2.9", None),
        ("192.0.2.9", "192.0.2.9:8888", None),
        ("8.8.8.8", "panel.local", None),
        ("fe80::1%eth0", "panel.local", None),
        ("proxy.example", "panel.local", None),
        ("", "panel.local", None),
        (None, "panel.local", None),
    ],
)
def test_session_candidate(
    remote: str | None, stored: str, expected: str | None
) -> None:
    """Only a literal, non-public peer at the stored port is a candidate."""
    candidate = session_candidate(remote, normalize_address(stored))
    if expected is None:
        assert candidate is None
    else:
        assert isinstance(candidate, PanelAddress)
        assert candidate.stored_value == expected


async def _load_offline(
    hass: HomeAssistant, user_id: str, unique_id: str | None
) -> MockConfigEntry:
    """Load one bound panel entry whose stored address does not answer."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={
            CONF_ADDRESS: STORED,
            CONF_TRANSPORT_USER_ID: user_id,
            "installation_identity": True,
        },
        unique_id=unique_id,
    )
    config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    return config_entry


async def test_panel_connecting_after_a_restart_with_a_dead_address_recovers(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """The case a restart makes: nothing has answered since the entry loaded.

    The entry loads with no snapshot and unavailable entities, accepts the
    panel's hello on its recorded identity, becomes available on the session
    alone, and adopts the address the panel connected from once health there
    proves the same identity.
    """
    entry = await _load_offline(hass, hass_read_only_user.id, DID)
    assert _state(hass, STATUS_ENTITY) == STATE_UNAVAILABLE
    assert _state(hass, UPDATE_ENTITY) == STATE_UNAVAILABLE

    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({STORED: CannotConnectError(), "127.0.0.1": HEALTH}),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await _connect(
            hass, hass_ws_client, hass_read_only_access_token, entry, "127.0.0.1"
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_ADDRESS] == "127.0.0.1"
    assert entry.runtime_data.coordinator.data is not None
    assert _state(hass, STATUS_ENTITY) == "online"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE
    assert _issue(hass, entry) is None


async def test_panel_connecting_to_an_unread_entry_is_available_before_any_poll(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """With no snapshot at all, the session is still enough to be available."""
    entry = await _load_offline(hass, hass_read_only_user.id, DID)

    with patch.object(
        HaPaneldClient,
        "async_get_health",
        _health_by_host(
            {STORED: CannotConnectError(), "127.0.0.1": CannotConnectError()}
        ),
    ):
        await _connect(
            hass, hass_ws_client, hass_read_only_access_token, entry, "127.0.0.1"
        )
        await hass.async_block_till_done()

    assert entry.runtime_data.coordinator.data is None
    assert _state(hass, STATUS_ENTITY) == "connected"
    assert _state(hass, UPDATE_ENTITY) != STATE_UNAVAILABLE
    state = hass.states.get(UPDATE_ENTITY)
    assert state is not None
    assert state.attributes["installed_version"] is None
    # The address it connected from answered nothing, so it was not adopted.
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_PANEL_ADDRESS_UNVERIFIED


async def test_entry_added_by_address_learns_its_identity_from_health(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A manual entry records the identity its panel reports, once."""
    entry = await _load(hass, hass_read_only_user.id)

    assert entry.unique_id == DID

    # Another panel's identity is never taken over an entry that has one.
    await _poll(hass, entry, {STORED: replace(HEALTH, discovery_id=OTHER_DID)})
    assert entry.unique_id == DID


async def test_identity_another_entry_holds_is_not_learned(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Two entries reporting one identity leave the second without it."""
    first = await _load(hass, hass_read_only_user.id)
    assert first.unique_id == DID
    second = MockConfigEntry(
        domain=DOMAIN,
        title="beta",
        data={CONF_ADDRESS: MOVED, CONF_TRANSPORT_USER_ID: hass_read_only_user.id},
    )
    second.add_to_hass(hass)
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
            "custom_components.panel_assistant._async_reconcile_install_receipt",
            AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    assert second.unique_id is None
    assert first.unique_id == DID


async def test_entry_without_reported_identity_learns_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A panel whose health carries no identity leaves the entry as it was."""
    entry = await _load(hass, hass_read_only_user.id)
    await _poll(hass, entry, {STORED: replace(HEALTH, discovery_id=None)})
    assert entry.unique_id == DID


# ---------------------------------------------------------------------------
# Address repair from mDNS.


def _advertisement(host: str) -> ZeroconfServiceInfo:
    address = ip_address(host)
    return ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=8888,
        hostname="alpha.local.",
        type="_ha-paneld._tcp.local.",
        name="alpha._ha-paneld._tcp.local.",
        properties={"did": DID},
    )


async def test_known_panel_advertising_a_new_address_updates_the_stored_one(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A re-advertised panel repairs its dead address once its identity is proved."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    await _poll(hass, entry, {STORED: CannotConnectError()})
    polled: list[str] = []

    async def _get_health(self: HaPaneldClient) -> Any:
        polled.append(self.address.host)
        return HEALTH

    with (
        patch.object(HaPaneldClient, "async_get_health", _get_health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(MOVED),
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Verified at the advertised address before it was written, then polled
    # there by the running entry.
    assert polled == [MOVED, MOVED]
    assert entry.data[CONF_ADDRESS] == MOVED
    assert entry.runtime_data.client.address.host == MOVED
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    "answer",
    [replace(HEALTH, discovery_id=OTHER_DID), CannotConnectError()],
    ids=["another_panel", "no_answer"],
)
async def test_advertised_address_that_fails_verification_is_not_written(
    hass: HomeAssistant, hass_read_only_user: Any, answer: Any
) -> None:
    """The mDNS token alone never moves a configured panel."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    health_mock = AsyncMock(
        side_effect=answer if isinstance(answer, Exception) else None,
        return_value=answer,
    )
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(MOVED),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert entry.data[CONF_ADDRESS] == STORED


async def test_advertisement_never_moves_a_panel_whose_address_still_answers(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """The identity and the health line are public; neither redirects a live panel."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    health_mock = AsyncMock(return_value=HEALTH)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(MOVED),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_ADDRESS] == STORED
    assert entry.runtime_data.client.address.host == STORED


# A LAN host replaying the panel's public identity and health line.
REPLAY = "192.0.2.66"


async def _advertise(hass: HomeAssistant, host: str) -> Any:
    """Deliver one advertisement for the panel from a host that answers as it."""
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(host),
        )
        await hass.async_block_till_done()
    return result


async def test_one_failed_poll_does_not_let_an_advertisement_move_a_connected_panel(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A replayed advertisement never takes the address of a panel talking to Core."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert not entry.runtime_data.coordinator.last_update_success

    result = await _advertise(hass, REPLAY)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_ADDRESS] == STORED
    assert entry.runtime_data.client.address.host == STORED
    # Refused, not silent: the stored address is reported, and the panel
    # stays available on its session.
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_PANEL_ADDRESS_UNREACHABLE
    assert _state(hass, STATUS_ENTITY) != STATE_UNAVAILABLE


async def test_a_connected_panel_is_moved_by_its_session_not_by_an_advertisement(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The credentialed session repairs the address; an advertisement cannot."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, MOVED)
    # The panel's web server has not answered at its new address yet.
    await _poll(
        hass, entry, {STORED: CannotConnectError(), MOVED: CannotConnectError()}
    )
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_PANEL_ADDRESS_UNVERIFIED

    await _advertise(hass, REPLAY)
    assert entry.data[CONF_ADDRESS] == STORED

    await _poll(
        hass,
        entry,
        {STORED: CannotConnectError(), MOVED: HEALTH, REPLAY: HEALTH},
    )
    assert entry.data[CONF_ADDRESS] == MOVED
    assert entry.runtime_data.client.address.host == MOVED
    assert _issue(hass, entry) is None


async def test_an_advertisement_moves_a_panel_again_once_its_session_has_closed(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Only a live session holds the address; a closed one leaves mDNS to repair it."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID)
    client = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, entry, None
    )
    await _poll(hass, entry, {STORED: CannotConnectError()})
    await client.close()
    await hass.async_block_till_done()
    assert not entry.runtime_data.coordinator.connected

    await _advertise(hass, MOVED)

    assert entry.data[CONF_ADDRESS] == MOVED


async def test_adding_a_known_panel_at_a_new_address_repairs_its_entry(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Adding the panel again where it now is updates the entry, never duplicates it."""
    entry = await _load(hass, hass_read_only_user.id)
    assert entry.unique_id == DID
    await _poll(hass, entry, {STORED: CannotConnectError()})
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "add_panel"}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_ADDRESS: LAN}
        )
        assert flow["type"] is FlowResultType.MENU, flow
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "connect_found"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert [e.entry_id for e in hass.config_entries.async_entries(DOMAIN)] == [
        entry.entry_id
    ]
    assert entry.data[CONF_ADDRESS] == LAN
    assert entry.runtime_data.client.address.host == LAN
    assert entry.state is ConfigEntryState.LOADED


async def test_adding_a_panel_by_hand_completes_while_its_discovery_card_is_pending(
    hass: HomeAssistant,
) -> None:
    """A pending discovery of the same panel never refuses the manual add."""
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        AsyncMock(return_value=HEALTH),
    ):
        discovery = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(LAN),
        )
        assert discovery["type"] is FlowResultType.FORM
        assert discovery["step_id"] == "confirm_discovery"

    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
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
            "custom_components.panel_assistant._async_reconcile_install_receipt",
            AsyncMock(),
        ),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "add_panel"}
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_ADDRESS: LAN}
        )
        assert flow["type"] is FlowResultType.MENU, flow
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"next_step_id": "connect_found"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["result"].unique_id == DID
    # Core closed the discovery card when the entry was created.
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["flow_id"] == discovery["flow_id"]
    ]


async def test_a_superseding_hello_never_writes_an_unavailable_state(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel that reconnects held a session throughout, and its states say so."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _state(hass, STATUS_ENTITY) == "connected"
    written: list[tuple[str, str]] = []

    @callback
    def _record(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is not None and event.data["entity_id"] in (
            STATUS_ENTITY,
            UPDATE_ENTITY,
        ):
            written.append((event.data["entity_id"], new_state.state))

    hass.bus.async_listen(EVENT_STATE_CHANGED, _record)
    with patch.object(
        HaPaneldClient,
        "async_get_health",
        _health_by_host({STORED: CannotConnectError()}),
    ):
        await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
        await hass.async_block_till_done()

    assert STATE_UNAVAILABLE not in [state for _entity_id, state in written], written
    assert _state(hass, STATUS_ENTITY) == "connected"


async def test_known_panel_advertising_its_stored_address_changes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A panel where it already is is not contacted for its advertisement."""
    entry = await _load(hass, hass_read_only_user.id, unique_id=DID, address=MOVED)
    health_mock = AsyncMock(return_value=HEALTH)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_advertisement(MOVED),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    health_mock.assert_not_awaited()
    assert entry.data[CONF_ADDRESS] == MOVED


async def test_reported_secondary_address_recovers_panel_behind_router(
    hass, entry, hass_ws_client, hass_read_only_access_token
):
    """A failed primary address falls back to the panel's verified secondary."""
    primary, secondary, router = "192.168.4.20", "192.168.5.20", "192.168.4.1"
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client, addresses=[primary, secondary])
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    session.remote = router
    card = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert card is not None
    card_id, entry_id = card.id, entry.entry_id

    await _poll(
        hass,
        entry,
        {
            STORED: CannotConnectError(),
            primary: CannotConnectError(),
            secondary: HEALTH,
            router: replace(HEALTH, discovery_id=OTHER_DID),
        },
    )

    assert entry.data[CONF_ADDRESS] == secondary
    assert entry.runtime_data.client.address == normalize_address(secondary)
    assert entry.runtime_data.coordinator.last_update_success
    assert entry.entry_id == entry_id
    assert (
        dr.async_get(hass)
        .async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        .id
        == card_id
    )
    assert async_get_sessions(hass).get(entry.entry_id) is session
    assert _issue(hass, entry) is None


async def test_closed_session_cannot_adopt_a_late_health_answer(
    hass, entry, hass_ws_client, hass_read_only_access_token
):
    """An HTTP result from a retired connection cannot move the saved endpoint."""
    client = await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_health(panel_client):
        if panel_client.address.host == STORED:
            raise CannotConnectError()
        started.set()
        await release.wait()
        return HEALTH

    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        delayed_health,
    ):
        poll = asyncio.create_task(entry.runtime_data.coordinator.async_refresh())
        await asyncio.wait_for(started.wait(), 1)
        await client.close()
        # Closing the session is observable before its delayed HTTP read completes.
        async_get_sessions(hass).close_entry(entry.entry_id, "entry_unloaded")
        release.set()
        await asyncio.wait_for(poll, 1)

    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert not entry.runtime_data.coordinator.last_update_success


async def test_address_edit_during_health_proof_is_not_overwritten(
    hass, entry, hass_ws_client, hass_read_only_access_token
):
    """An entry update wins before its asynchronous client listener has run."""
    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    chosen = "192.168.8.11"

    async def health(panel_client):
        if panel_client.address.host == STORED:
            raise CannotConnectError()
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: chosen}
        )
        return HEALTH

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == chosen
    assert entry.runtime_data.client.address == normalize_address(chosen)


async def test_old_address_poll_cannot_close_newly_admitted_moved_panel(
    hass, entry, hass_ws_client, hass_read_only_access_token
):
    """A response from a recycled address cannot revoke the proven new session."""
    from .test_transport import _hello, _send

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    started, release = asyncio.Event(), asyncio.Event()
    legacy_health = replace(HEALTH, installation_identity=False)

    async def health(panel_client):
        if panel_client.address.host == STORED:
            if panel_client is entry.runtime_data.client:
                started.set()
                await release.wait()
            return replace(legacy_health, discovery_id=OTHER_DID)
        return legacy_health

    client = await hass_ws_client(hass, hass_read_only_access_token)
    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        poll = asyncio.create_task(coordinator.async_refresh())
        await asyncio.wait_for(started.wait(), 1)
        try:
            response = await _send(
                client, _hello(protocol={"min": 1, "max": 2}, addresses=[MOVED])
            )
            assert response["success"], response
            session = async_get_sessions(hass).get(entry.entry_id)
            assert session is not None
        finally:
            release.set()
            await asyncio.wait_for(poll, 1)
        await hass.async_block_till_done()

    assert entry.data[CONF_ADDRESS] == MOVED
    assert async_get_sessions(hass).get(entry.entry_id) is session
    assert not coordinator.identity_mismatch

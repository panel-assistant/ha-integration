"""A moved legacy panel proves its address before receiving native controls."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, HaPaneldClient
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.transport import async_get_sessions

from .test_availability import STORED, _health_by_host, _load
from .test_transport import (
    DID,
    HEALTH,
    OTHER_DID,
    STATUS,
    _hello,
    _receive,
    _registry_digest,
    _send,
)

LEGACY_HEALTH = replace(HEALTH, installation_identity=False)


async def test_legacy_panel_moves_after_authenticated_matching_health(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    entry = await _load(hass, hass_read_only_user.id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await hass.async_block_till_done()
    original_registry = _registry_digest(hass, entry.entry_id)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({STORED: CannotConnectError(), "127.0.0.1": LEGACY_HEALTH}),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        response = await _send(client, _hello(protocol={"min": 1, "max": 2}))
        assert response["success"], response
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == "127.0.0.1"
    assert async_get_sessions(hass).get(entry.entry_id) is not None
    assert _registry_digest(hass, entry.entry_id) == original_registry


async def _legacy_entry(hass, user):
    entry = await _load(hass, user.id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(
    "old,candidate",
    [
        (LEGACY_HEALTH, LEGACY_HEALTH),
        (CannotConnectError(), replace(LEGACY_HEALTH, discovery_id=None)),
        (CannotConnectError(), replace(LEGACY_HEALTH, discovery_id=OTHER_DID)),
        (CannotConnectError(), CannotConnectError()),
    ],
)
async def test_ambiguous_or_unverified_endpoint_never_gets_session(
    hass,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    old,
    candidate,
):
    entry = await _legacy_entry(hass, hass_read_only_user)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with patch.object(
        HaPaneldClient,
        "async_get_health",
        _health_by_host(
            {
                STORED: old,
                "127.0.0.1": candidate,
            }
        ),
    ):
        result = await _send(client, _hello(protocol={"min": 1, "max": 2}))
    assert result["success"] is False
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize(
    "obstacle", ["wrong_user", "unloaded_duplicate", "loaded_duplicate"]
)
async def test_ineligible_request_never_probes_address(
    hass,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    hass_access_token,
    obstacle,
):
    from homeassistant.config_entries import ConfigEntryState

    entry = await _legacy_entry(hass, hass_read_only_user)
    if obstacle != "wrong_user":
        twin = MockConfigEntry(
            domain=DOMAIN, unique_id=DID, data={CONF_ADDRESS: "192.0.2.20"}
        )
        twin.add_to_hass(hass)
        if obstacle == "loaded_duplicate":
            twin.mock_state(hass, ConfigEntryState.LOADED)
    token = (
        hass_access_token if obstacle == "wrong_user" else hass_read_only_access_token
    )
    client = await hass_ws_client(hass, token)
    with patch.object(HaPaneldClient, "async_get_health", AsyncMock()) as health:
        result = await _send(client, _hello(protocol={"min": 1, "max": 2}))
    assert result["success"] is False
    health.assert_not_awaited()
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize(
    "change",
    [
        "binding",
        "removed",
        "identity",
        "duplicate",
        "address",
        "disconnected",
        "deleted_user",
        "pending",
    ],
)
async def test_changes_while_health_is_pending_cannot_adopt(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, change
):
    entry = await _legacy_entry(hass, hass_read_only_user)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def health(panel):
        if panel.address.host == STORED:
            raise CannotConnectError
        started.set()
        try:
            await release.wait()
            return LEGACY_HEALTH
        finally:
            finished.set()

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await client.send_json_auto_id(_hello(protocol={"min": 1, "max": 2}))
        async with asyncio.timeout(2):
            await started.wait()
        if change == "binding":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "transport_user_id": "different"}
            )
        elif change == "deleted_user":
            await hass.auth.async_remove_user(hass_read_only_user)
        elif change == "pending":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "identity_pending": {"did": OTHER_DID}}
            )
        elif change == "removed":
            await hass.config_entries.async_remove(entry.entry_id)
        elif change == "identity":
            hass.config_entries.async_update_entry(entry, unique_id=OTHER_DID)
        elif change == "duplicate":
            twin = MockConfigEntry(
                domain=DOMAIN, unique_id=DID, data={CONF_ADDRESS: "192.0.2.20"}
            )
            twin.add_to_hass(hass)
        elif change == "address":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_ADDRESS: "192.0.2.30"}
            )
        else:
            await client.close()
        release.set()
        async with asyncio.timeout(2):
            await finished.wait()
        if change != "disconnected":
            result = await _receive(client)
            assert result["success"] is False
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] != "127.0.0.1"
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_concurrent_candidates_both_refused_then_retry_can_succeed(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    entry = await _legacy_entry(hass, hass_read_only_user)
    first = await hass_ws_client(hass, hass_read_only_access_token)
    second = await hass_ws_client(hass, hass_read_only_access_token)
    started, release = asyncio.Event(), asyncio.Event()

    async def health(panel):
        if panel.address.host == STORED:
            raise CannotConnectError
        started.set()
        await release.wait()
        return LEGACY_HEALTH

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        await first.send_json_auto_id(
            _hello(protocol={"min": 1, "max": 2}, addresses=["192.0.2.11"])
        )
        async with asyncio.timeout(2):
            await started.wait()
        result = await _send(
            second, _hello(protocol={"min": 1, "max": 2}, addresses=["192.0.2.12"])
        )
        assert result["success"] is False
        release.set()
        assert (await _receive(first))["success"] is False
        assert entry.data[CONF_ADDRESS] == STORED
        assert async_get_sessions(hass).get(entry.entry_id) is None
        # The completed collision cannot leave a pending owner blocking recovery.
        result = await _send(first, _hello(protocol={"min": 1, "max": 2}))
        assert result["success"], result
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == "127.0.0.1"


async def test_disconnected_proof_cannot_block_the_next_connection(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    entry = await _legacy_entry(hass, hass_read_only_user)
    first = await hass_ws_client(hass, hass_read_only_access_token)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def waiting(panel):
        if panel.address.host == STORED:
            raise CannotConnectError
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch.object(HaPaneldClient, "async_get_health", waiting):
        await first.send_json_auto_id(_hello(protocol={"min": 1, "max": 2}))
        async with asyncio.timeout(2):
            await started.wait()
            await first.close()
            await cancelled.wait()
    assert entry.data[CONF_ADDRESS] == STORED
    second = await hass_ws_client(hass, hass_read_only_access_token)
    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host(
                {
                    STORED: CannotConnectError(),
                    "127.0.0.1": LEGACY_HEALTH,
                }
            ),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        assert (await _send(second, _hello(protocol={"min": 1, "max": 2})))["success"]
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == "127.0.0.1"


async def test_existing_session_prevents_legacy_candidate_takeover(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    entry = await _load(hass, hass_read_only_user.id)
    first = await hass_ws_client(hass, hass_read_only_access_token)
    assert (await _send(first, _hello()))["success"]
    session = async_get_sessions(hass).get(entry.entry_id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await hass.async_block_till_done()
    second = await hass_ws_client(hass, hass_read_only_access_token)
    with patch.object(HaPaneldClient, "async_get_health", AsyncMock()) as health:
        assert (await _send(second, _hello(protocol={"min": 1, "max": 2})))[
            "success"
        ] is False
    health.assert_not_awaited()
    assert async_get_sessions(hass).get(entry.entry_id) is session
    assert entry.data[CONF_ADDRESS] == STORED


async def test_slow_old_and_primary_addresses_do_not_starve_secondary_proof(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    """Every candidate gets its read while another route is still waiting."""
    entry = await _legacy_entry(hass, hass_read_only_user)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    primary, secondary = "192.168.4.20", "192.168.5.20"
    release, secondary_started = asyncio.Event(), asyncio.Event()

    async def health(panel_client):
        if panel_client.address.host == secondary:
            secondary_started.set()
            await release.wait()
            return LEGACY_HEALTH
        await release.wait()
        raise CannotConnectError()

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        reply = asyncio.create_task(
            _send(
                client,
                _hello(protocol={"min": 1, "max": 2}, addresses=[primary, secondary]),
            )
        )
        try:
            await asyncio.wait_for(secondary_started.wait(), 0.5)
            concurrent = True
        except TimeoutError:
            concurrent = False
        finally:
            release.set()
        result = await reply
        await hass.async_block_till_done()
    assert concurrent, "secondary proof waited for the old endpoint to finish"
    assert result["success"], result
    assert entry.data[CONF_ADDRESS] == secondary


@pytest.mark.parametrize("installation", [False, True])
async def test_reassigned_old_address_recovers_only_the_matching_legacy_panel(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, installation
):
    """A wrong panel at the former address cannot permanently fence its owner."""
    from homeassistant.helpers import issue_registry as ir

    from .test_availability import _poll

    entry = await _legacy_entry(hass, hass_read_only_user)
    registry_before = _registry_digest(hass, entry.entry_id)
    wrong_health = replace(LEGACY_HEALTH, discovery_id=OTHER_DID)
    await _poll(hass, entry, {STORED: wrong_health})
    coordinator = entry.runtime_data.coordinator
    assert coordinator.identity_mismatch
    issue_id = f"panel_identity_mismatch_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host(
                {
                    STORED: wrong_health,
                    "127.0.0.1": replace(
                        LEGACY_HEALTH, installation_identity=installation
                    ),
                }
            ),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        result = await _send(client, _hello(protocol={"min": 1, "max": 2}))
        assert result["success"] is (not installation), result
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == (STORED if installation else "127.0.0.1")
    assert coordinator.identity_mismatch is installation
    assert (async_get_sessions(hass).get(entry.entry_id) is None) is installation
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    ) is installation
    assert _registry_digest(hass, entry.entry_id) == registry_before
    assert not entry.data["installation_identity"]


async def test_same_peer_cannot_bypass_a_health_identity_mismatch(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    from .test_availability import _poll

    entry = await _load(hass, hass_read_only_user.id, address="127.0.0.1")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await _poll(
        hass, entry, {"127.0.0.1": replace(LEGACY_HEALTH, discovery_id=OTHER_DID)}
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with patch.object(HaPaneldClient, "async_get_health", AsyncMock()) as health:
        result = await _send(client, _hello(protocol={"min": 1, "max": 2}))
    assert result["success"] is False
    health.assert_not_awaited()
    assert entry.runtime_data.coordinator.identity_mismatch
    assert async_get_sessions(hass).get(entry.entry_id) is None

"""Panel interface hints are bounded and do not authorize a session or endpoint."""

import pytest
from homeassistant.const import CONF_ADDRESS

from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_availability import STORED, _load
from .test_transport import _hello, _send


@pytest.mark.parametrize(
    "addresses",
    [
        ["192.168.1.2"] * 17,
        "192.168.1.2",
        ["panel.local"],
        ["http://192.168.1.2:8888"],
        ["127.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["::1"],
        ["fe80::1%eth0"],
        ["0.0.0.0"],
        ["224.0.0.1"],
        [None],
    ],
)
async def test_bad_interface_hints_do_not_open_a_session(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, addresses
):
    entry = await _load(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(client, _hello(addresses=addresses))
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_format"
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize("http_port", [True, "9999", 9999.0, None, 1023, 65536])
async def test_invalid_listener_port_does_not_open_a_session(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, http_port
):
    entry = await _load(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(client, _hello(http_port=http_port))
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_format"
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize("host", ["panel.local", "192.168.1.28", "192.168.1.29"])
@pytest.mark.parametrize("http_port", [1024, 9999, 65535, None])
@pytest.mark.parametrize("old_answers", [False, True])
async def test_accepted_listener_port_recovers_only_a_verified_endpoint(
    hass,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    host,
    http_port,
    old_answers,
):
    """A hello reaches the real coordinator; HTTP proves its replacement address."""
    from unittest.mock import AsyncMock, patch

    from custom_components.panel_assistant.client import (
        CannotConnectError,
        HaPaneldClient,
    )

    from .test_transport import HEALTH, STATUS

    stored = host if host == "192.168.1.28" else STORED
    entry = await _load(hass, hass_read_only_user.id, address=stored)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    changes = {"addresses": [host]} if host != stored else {}
    if http_port is not None:
        changes["http_port"] = http_port
    expected_port = http_port or 8888
    expected = host if expected_port == 8888 else f"{host}:{expected_port}"

    async def health(panel_client):
        if panel_client.address.stored_value == expected and expected != stored:
            return HEALTH
        if old_answers and panel_client.address.stored_value == stored:
            return HEALTH
        raise CannotConnectError()

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        response = await _send(client, _hello(**changes))
        assert response["success"]
        session = async_get_sessions(hass).get(entry.entry_id)
        assert session is not None
        session.remote = None
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
    if old_answers and http_port is None:
        expected, expected_port = stored, 8888
    assert entry.data[CONF_ADDRESS] == expected
    assert entry.runtime_data.client.address.port == expected_port
    assert async_get_sessions(hass).get(entry.entry_id) is session


@pytest.mark.parametrize("wrong_identity", [False, True])
async def test_listener_port_is_not_written_without_matching_health(
    hass,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    wrong_identity,
):
    from dataclasses import replace
    from unittest.mock import patch

    from custom_components.panel_assistant.client import (
        CannotConnectError,
        HaPaneldClient,
    )

    from .test_transport import HEALTH, OTHER_DID

    entry = await _load(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_read_only_access_token)

    async def health(panel_client):
        if panel_client.address.port == 9999 and wrong_identity:
            return replace(HEALTH, discovery_id=OTHER_DID)
        raise CannotConnectError()

    with patch.object(HaPaneldClient, "async_get_health", health):
        assert (await _send(client, _hello(http_port=9999)))["success"]
        session = async_get_sessions(hass).get(entry.entry_id)
        assert session is not None
        session.remote = None
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is session


async def test_unbound_user_cannot_change_listener_port(
    hass, hass_ws_client, hass_read_only_user
):
    """An administrator's socket still needs the entry's panel account binding."""
    entry = await _load(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass)
    response = await _send(client, _hello(http_port=9999))
    assert response["success"] is False
    assert response["error"]["code"] == "panel_user_mismatch"
    assert entry.data[CONF_ADDRESS] == STORED
    assert async_get_sessions(hass).get(entry.entry_id) is None

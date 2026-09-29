"""Panel interface hints are bounded and do not authorize a session or endpoint."""

import pytest
from homeassistant.const import CONF_ADDRESS

from custom_components.panel_assistant.transport import async_get_sessions

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

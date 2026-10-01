"""A USB-installed panel is told Home Assistant set it up, through the admin's window.

The browser installer reads the panel's address over USB and has no Home
Assistant credential, so the admin's own Home Assistant window relays that
address to this endpoint. These tests drive the real HTTP view and stop at the
panel's HTTP client, so what the panel would receive is what is asserted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant import browser_delivery as delivery
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelSetupState,
)

URL = "/api/panel_assistant/usb/handover"
_CLIENT = "custom_components.panel_assistant.client.HaPaneldClient"


class _Panel:
    """Record which panel was contacted and what it was posted."""

    def __init__(self, state: PanelSetupState | Exception) -> None:
        self.state = state
        self.hosts: list[str] = []
        self.posted: list[str | None] = []
        self.post_error: Exception | None = None

    async def get_state(self, client: HaPaneldClient) -> PanelSetupState:
        self.hosts.append(f"{client.address.host}:{client.address.port}")
        if isinstance(self.state, Exception):
            raise self.state
        return self.state

    async def hand_over(self, client: HaPaneldClient, ha_url: str | None) -> None:
        if self.post_error is not None:
            raise self.post_error
        self.posted.append(ha_url)


@pytest.fixture
async def endpoint(hass: HomeAssistant, hass_client: Any) -> Any:
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(internal_url="http://192.168.1.5:8123")
    delivery.async_register_browser_delivery(hass)
    return await hass_client()


def _patched(panel: _Panel) -> Any:
    get_state = panel.get_state
    hand_over = panel.hand_over

    async def _state(self: HaPaneldClient) -> PanelSetupState:
        return await get_state(self)

    async def _hand(self: HaPaneldClient, ha_url: str | None) -> None:
        await hand_over(self, ha_url)

    return (
        patch(f"{_CLIENT}.async_get_setup_state", _state),
        patch(f"{_CLIENT}.async_hand_over_ha_url", _hand),
    )


async def _post(endpoint: Any, panel: _Panel, body: Any) -> tuple[int, Any]:
    state, hand = _patched(panel)
    with state, hand:
        response = await endpoint.post(URL, json=body)
        return response.status, await response.json()


async def test_a_usb_installed_panel_gets_the_handover_every_install_path_sends(
    endpoint: Any,
) -> None:
    panel = _Panel(PanelSetupState(complete=False, accepts_handover=True))
    status, body = await _post(endpoint, panel, {"address": "192.168.1.20"})
    assert status == 200
    assert body == {"outcome": "handed_over"}
    assert panel.hosts == ["192.168.1.20:8888"]
    assert panel.posted == ["http://192.168.1.5:8123"]


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",  # a public host
        "192.168.1.20:9000",  # another port on a home address
        "127.0.0.1",  # this server itself
        "169.254.1.1",  # link-local
        "100.64.0.1",  # carrier-grade NAT, not a home network
        "192.0.2.1",  # documentation range Python calls private
        "panel.local",  # a name this server would resolve
        "[fd00::1]",  # IPv6, which the installer never reads
        "[a00::1]",  # IPv6 whose first bytes look like a home range
        "http://192.168.1.20/",  # a URL rather than an address
        42,
    ],
)
async def test_anything_but_a_home_network_panel_on_its_own_port_is_refused(
    endpoint: Any, address: Any
) -> None:
    """The endpoint must not become a way to make this server reach other hosts."""
    panel = _Panel(PanelSetupState(complete=False, accepts_handover=True))
    status, body = await _post(endpoint, panel, {"address": address})
    assert status == 400
    assert body == {"error": "browser_handover_invalid_request"}
    assert panel.hosts == []


@pytest.mark.parametrize(
    "body",
    [{}, {"address": "192.168.1.20", "url": "http://x"}, ["192.168.1.20"]],
)
async def test_only_the_address_is_accepted(endpoint: Any, body: Any) -> None:
    panel = _Panel(PanelSetupState(complete=False, accepts_handover=True))
    status, _ = await _post(endpoint, panel, body)
    assert status == 400
    assert panel.hosts == []


async def test_without_a_panel_facing_url_the_marker_still_goes(
    endpoint: Any, hass: HomeAssistant
) -> None:
    """The owner then types only the address; the MQTT step stays skipped."""
    panel = _Panel(PanelSetupState(complete=False, accepts_handover=True))
    with patch(
        "custom_components.panel_assistant.ha_url.async_panel_facing_url",
        return_value=None,
    ):
        status, body = await _post(endpoint, panel, {"address": "10.20.30.7"})
    assert status == 200
    assert body == {"outcome": "handed_over_without_url"}
    assert panel.posted == [None]


async def test_a_finished_panel_is_left_alone_and_says_why(endpoint: Any) -> None:
    panel = _Panel(PanelSetupState(complete=True, accepts_handover=True))
    status, body = await _post(endpoint, panel, {"address": "172.16.4.2"})
    assert status == 200
    assert body == {"outcome": "setup_complete"}
    assert panel.posted == []


async def test_a_panel_that_refuses_is_reported_not_raised(endpoint: Any) -> None:
    """The installer shows the outcome in its support log and opens setup anyway."""
    panel = _Panel(PanelSetupState(complete=False, accepts_handover=True))
    panel.post_error = CannotConnectError()
    status, body = await _post(endpoint, panel, {"address": "192.168.1.20"})
    assert status == 200
    assert body == {"outcome": "handover_refused"}


async def test_an_unreachable_panel_is_reported_not_raised(endpoint: Any) -> None:
    panel = _Panel(CannotConnectError())
    status, body = await _post(endpoint, panel, {"address": "192.168.1.20"})
    assert status == 200
    assert body == {"outcome": "setup_state_unreadable"}


async def test_only_an_admin_may_hand_over(
    endpoint: Any, hass_client_no_auth: Any, hass_read_only_access_token: str
) -> None:
    client = await hass_client_no_auth()
    hand_over = AsyncMock()
    with patch(f"{_CLIENT}.async_hand_over_ha_url", hand_over):
        for headers in (
            {},
            {"Authorization": f"Bearer {hass_read_only_access_token}"},
        ):
            response = await client.post(
                URL, json={"address": "192.168.1.20"}, headers=headers
            )
            assert response.status == 401
    hand_over.assert_not_awaited()

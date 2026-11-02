"""USB relay endpoints retain their port through real view and entry creation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant import browser_delivery as delivery
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import (
    HaPaneldClient,
    PanelHealth,
    PanelSetupState,
)
from custom_components.panel_assistant.const import DOMAIN

from .config_flow_helpers import _zeroconf_info

_URL = "/api/panel_assistant/usb/handover"
_CLIENT = "custom_components.panel_assistant.client.HaPaneldClient"
_HEALTH = PanelHealth(
    version="1.0.0",
    panel_id="usb-panel",
    build="1220",
    config_hash="1a2b3c4d",
    discovery_id="d" * 64,
    package=PACKAGE_ID,
)


@pytest.fixture
async def relay(hass: HomeAssistant, hass_client: Any) -> Any:
    """Use the authenticated admin HTTP view; fake only the panel's responses."""
    assert await async_setup_component(hass, "http", {})
    await hass.config.async_update(internal_url="http://192.168.1.5:8123")
    delivery.async_register_browser_delivery(hass)
    return await hass_client()


@pytest.mark.parametrize("port", [8888, 8889])
async def test_usb_relay_creates_entry_on_the_health_verified_listener(
    hass: HomeAssistant, relay: Any, port: int
) -> None:
    """A real discovery health check grants only its endpoint to a new USB entry."""
    contacted: list[str] = []
    handed: list[tuple[str, str | None]] = []
    complete = False

    async def health(client: HaPaneldClient) -> PanelHealth:
        contacted.append(client.address.stored_value)
        return _HEALTH

    async def state(client: HaPaneldClient) -> PanelSetupState:
        contacted.append(client.address.stored_value)
        return PanelSetupState(complete=complete, accepts_handover=True)

    async def handover(client: HaPaneldClient, ha_url: str | None) -> None:
        nonlocal complete
        handed.append((client.address.stored_value, ha_url))
        complete = True

    address = "192.168.1.70" + (f":{port}" if port != 8888 else "")
    with (
        patch(f"{_CLIENT}.async_get_health", health),
        patch(f"{_CLIENT}.async_get_setup_state", state),
        patch(f"{_CLIENT}.async_hand_over_ha_url", handover),
        patch(f"{_CLIENT}.async_get_display", AsyncMock(return_value={})),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        discovery = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_zeroconf_info(
                host="192.168.1.70", port=port, discovery_id=_HEALTH.discovery_id
            ),
        )
        assert discovery["type"] is FlowResultType.FORM
        assert contacted == [address], (
            "the actual discovery must health-check this port"
        )
        assert not hass.config_entries.async_entries(DOMAIN)
        response = await relay.post(_URL, json={"address": address})
        assert response.status == 200
        body = await response.json()
        assert body["registration"] == "added"
        assert body["outcome"] == "handed_over"
        assert handed == [(address, "http://192.168.1.5:8123")]
        assert [
            (entry.data["address"], entry.unique_id)
            for entry in hass.config_entries.async_entries(DOMAIN)
        ] == [(address, _HEALTH.discovery_id)]
        assert len(contacted) >= 3
        assert set(contacted) == {address}, (
            "view, registration and discovery stay on the listener"
        )


@pytest.mark.parametrize(
    "address", ["192.168.1.70:8889", "8.8.8.8:8889", "192.168.1.70:65536"]
)
async def test_usb_relay_does_not_probe_an_unverified_endpoint(
    relay: Any, address: str
) -> None:
    health = AsyncMock(return_value=_HEALTH)
    with patch(f"{_CLIENT}.async_get_health", health):
        response = await relay.post(_URL, json={"address": address})
    assert response.status == 400
    assert await response.json() == {"error": "browser_handover_invalid_request"}
    health.assert_not_awaited()

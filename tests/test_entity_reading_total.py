"""Entity scan estimates are supplied through the authenticated hello caller."""

from typing import Any

import pytest
from homeassistant.const import (
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import CoreState, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.lifecycle import async_setup_lifecycle

from .test_transport import WsClientFactory, _hello, _send, entry


async def test_running_hello_refreshes_expected_entity_count(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Every accepted hello gets the current state count, without an opt-in."""
    for index in range(2):
        hass.states.async_set(f"sensor.reading_total_{index}", index)
        await hass.async_block_till_done()
        expected = len(hass.states.async_entity_ids())
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello())
        assert response["success"]
        assert response["result"]["expected_entity_count"] == expected
        await client.close()
        await hass.async_block_till_done()


@pytest.mark.parametrize("cached", [None, 0, -1, True, "123", 123])
async def test_starting_hello_uses_only_positive_cached_count(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    cached: Any,
) -> None:
    """Partial startup states never replace the last complete running estimate."""
    hass.set_state(CoreState.starting)
    hass_storage[f"{DOMAIN}.lifecycle"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.lifecycle",
        "data": {"expected_entity_count": cached},
    }
    async for _config_entry in entry.__wrapped__(hass, hass_read_only_user):
        hass.states.async_set("sensor.partial_startup", "ready")
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello())
        assert response["success"]
        if type(cached) is int and cached > 0:
            assert response["result"]["expected_entity_count"] == cached
        else:
            assert "expected_entity_count" not in response["result"]
        await client.close()


async def test_core_started_persists_count_for_next_starting_hello(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    """A new lifecycle owner restores Core's completed count before any hello."""
    hass.set_state(CoreState.starting)
    async for _config_entry in entry.__wrapped__(hass, hass_read_only_user):
        hass.states.async_set("sensor.fully_started", "ready")
        await hass.async_block_till_done()
        expected = len(hass.states.async_entity_ids())
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        assert (
            hass_storage[f"{DOMAIN}.lifecycle"]["data"]["expected_entity_count"]
            == expected
        )

        # Recreate the production owner from its Store, as on a Core restart.
        # The newly filling state machine must not replace the saved count.
        hass.states.async_remove("sensor.fully_started")
        hass.set_state(CoreState.starting)
        await async_setup_lifecycle(hass)
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello())
        assert response["success"]
        assert response["result"]["expected_entity_count"] == expected
        await client.close()

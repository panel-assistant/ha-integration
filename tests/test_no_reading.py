"""A panel sensor with no reading is unknown in Home Assistant, with its reason."""

import json
from pathlib import Path
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_native import (
    DESCRIPTORS,
    _hello,
    _native_entries,
    _report,
    _session,
    _sync,
)
from .test_native import native as native
from .test_transport import DID, WsClientFactory, _send


def _described(channel: str) -> dict[str, Any]:
    return next(item for item in DESCRIPTORS if item["channel"] == channel)


async def test_a_sensor_with_no_reading_reads_unknown_with_the_panels_reason(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A connected panel's sensor with no reading is unknown and says why."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    token = await _session(client)
    await _sync(hass, client, token)
    entries = _native_entries(hass, native.entry_id)

    def entity_id(channel: str) -> str:
        return entries[f"{DID}_{_described(channel)['unique_suffix']}"].entity_id

    for channel, value, shown in (
        ("illuminance", 42, "42"),
        ("proximity", True, "on"),
    ):
        for observation, state, reason in (
            ({"state": "unknown", "reason": "waiting"}, STATE_UNKNOWN, "waiting"),
            ({"state": "known", "value": value}, shown, None),
            ({"state": "unknown", "reason": "not_shared"}, STATE_UNKNOWN, "not_shared"),
        ):
            response = await _send(
                client, _report(token, "delta", [{"channel": channel, **observation}])
            )
            assert response["success"], response
            assert response["result"]["rejected"] == []
            await hass.async_block_till_done()
            entity = hass.states.get(entity_id(channel))
            assert (entity.state, entity.attributes.get("unknown_reason")) == (
                state,
                reason,
            ), channel

    # Only a sensor shows a reason; another platform with no reading is unavailable.
    response = await _send(
        client,
        _report(
            token,
            "delta",
            [{"channel": "kiosk_lock", "state": "unknown", "reason": "waiting"}],
        ),
    )
    assert response["success"], response
    await hass.async_block_till_done()
    assert hass.states.get(entity_id("kiosk_lock")).state == STATE_UNAVAILABLE
    await client.close()


async def test_a_hello_offering_unknown_state_is_granted_it(
    hass: HomeAssistant,
    native: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The grant tells a panel it may send a sensor as unknown with a reason."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    hello = _hello(DESCRIPTORS) | {"capabilities": ["state", "unknown_state"]}
    response = await _send(client, hello)
    assert response["success"], response
    assert "unknown_state" in response["result"]["capabilities"]
    await client.close()


def test_every_sensor_translates_each_no_reading_reason() -> None:
    """Home Assistant shows the reason in words for every sensor and binary sensor."""
    component = Path(__file__).parents[1] / "custom_components" / "panel_assistant"
    strings = json.loads((component / "strings.json").read_text(encoding="utf-8"))
    reasons = {
        "waiting",
        "not_shared",
        "not_readable",
        "not_on_wifi",
        "turned_off",
        "not_ready",
    }
    for descriptor in DESCRIPTORS:
        if descriptor["platform"] not in ("sensor", "binary_sensor"):
            continue
        platform = strings["entity"][descriptor["platform"]]
        entity = platform[descriptor["translation_key"]]
        attribute = entity["state_attributes"]["unknown_reason"]
        assert attribute["name"], descriptor["channel"]
        assert set(attribute["state"]) == reasons, descriptor["channel"]

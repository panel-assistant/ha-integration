"""Fleet log policy is configured through an entry without becoming its option."""

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.test_transport import DID, WsClientFactory, _hello, _send
from tests.test_transport import entry as entry


async def test_log_options_are_fleet_wide(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    opened = await hass.config_entries.options.async_init(entry.entry_id)
    assert "panel_logs" in opened["menu_options"]
    form = await hass.config_entries.options.async_configure(
        opened["flow_id"], {"next_step_id": "panel_logs"}
    )
    assert form["type"] is FlowResultType.FORM
    assert form["data_schema"]({}) == {
        "destination": "none",
        "host": "",
        "port": 514,
        "app_level": 5,
        "system_level": 0,
        "also_home_assistant": False,
    }
    rendered = FlowManagerIndexView(hass.config_entries.options)._prepare_result_json(
        form
    )["data_schema"]
    destination = next(field for field in rendered if field["name"] == "destination")
    assert destination["selector"]["select"].get("translation_key") == "destination"
    # HA's legacy numeric select translates by field name and returns integer keys.
    for name, expected_default in (("app_level", 5), ("system_level", 0)):
        level = next(field for field in rendered if field["name"] == name)
        assert level["type"] == "select"
        assert level["default"] == expected_default
        assert all(type(value) is int for value, _label in level["options"])
    before = dict(entry.options)
    saved = await hass.config_entries.options.async_configure(
        opened["flow_id"],
        {
            "destination": "udp",
            "host": "logs.local",
            "port": 5514,
            "app_level": 4,
            "system_level": 0,
            "also_home_assistant": True,
        },
    )
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == before
    from custom_components.panel_assistant.panel_logs import async_get_log_router

    assert async_get_log_router(hass).policy == {
        "destination": {"protocol": "udp", "host": "logs.local", "port": 5514},
        "sources": {"app": 4},
        "also_home_assistant": True,
    }
    other = MockConfigEntry(domain="panel_assistant", title="Another panel", data={})
    other.add_to_hass(hass)
    reopened = await hass.config_entries.options.async_init(other.entry_id)
    form = await hass.config_entries.options.async_configure(
        reopened["flow_id"], {"next_step_id": "panel_logs"}
    )
    assert form["data_schema"]({})["host"] == "logs.local"
    assert form["data_schema"]({})["also_home_assistant"] is True
    assert dict(other.options) == {}


@pytest.mark.parametrize("system_level", [1, 8, -1])
async def test_log_options_reject_invalid_system_level(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    system_level: int,
) -> None:
    opened = await hass.config_entries.options.async_init(entry.entry_id)
    assert "panel_logs" in opened["menu_options"]
    form = await hass.config_entries.options.async_configure(
        opened["flow_id"], {"next_step_id": "panel_logs"}
    )
    with pytest.raises(vol.Invalid):
        form["data_schema"]({"system_level": system_level})


async def test_saved_destination_is_offered_and_adopted_only_explicitly(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    from custom_components.panel_assistant.panel_logs import async_get_log_router

    client = await hass_ws_client(hass, hass_read_only_access_token)
    offered = {"protocol": "udp", "host": "saved.local", "port": 5514}
    hello = await _send(
        client,
        _hello(
            capabilities=["state", "logs"],
            logs={"saved_sink": {"enabled": True, "system_enabled": True, **offered}},
        ),
    )
    assert hello["success"], hello
    router = async_get_log_router(hass)
    assert router.policy["destination"] is None
    opened = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        opened["flow_id"], {"next_step_id": "panel_logs"}
    )
    rendered = FlowManagerIndexView(hass.config_entries.options)._prepare_result_json(
        form
    )["data_schema"]
    destination = next(field for field in rendered if field["name"] == "destination")
    assert {"value": f"saved:{DID}", "label": "saved.local:5514 (udp)"} in (
        destination["selector"]["select"]["options"]
    )
    values = form["data_schema"]({"destination": f"saved:{DID}"})
    assert router.policy["destination"] is None
    saved = await hass.config_entries.options.async_configure(opened["flow_id"], values)
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert router.policy == {"destination": offered, "sources": {"app": 5}}
    reopened = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        reopened["flow_id"], {"next_step_id": "panel_logs"}
    )
    with pytest.raises(vol.Invalid):
        form["data_schema"]({"destination": f"saved:{DID}"})


@pytest.mark.parametrize("destination", ["none", "home_assistant"])
async def test_local_destinations_need_no_host(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    destination: str,
) -> None:
    from custom_components.panel_assistant.panel_logs import async_get_log_router

    opened = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(
        opened["flow_id"], {"next_step_id": "panel_logs"}
    )
    values = form["data_schema"](
        {"destination": destination, "system_level": 6, "also_home_assistant": True}
    )
    saved = await hass.config_entries.options.async_configure(opened["flow_id"], values)
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert async_get_log_router(hass).policy == {
        "destination": None
        if destination == "none"
        else {"protocol": "home_assistant"},
        "sources": {"app": 5, "system": 6},
    }

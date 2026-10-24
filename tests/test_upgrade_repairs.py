"""Panel Assistant 1.0 at entry setup: a native panel, a panel never moved off
MQTT, and a panel whose app speaks an older protocol.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import CannotConnectError, PanelHealth
from custom_components.panel_assistant.const import (
    CONF_CUTOVER,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)

from .test_transport import DID, HEALTH, STATUS, WsClientFactory, _hello, _send

CURRENT = replace(HEALTH, version="1.0.0", package=PACKAGE_ID, protocol=(5, 5))
# A 0.9.11 app, already on the new app id: its health line carries no protocol.
OLDER_APP = replace(CURRENT, version="0.9.11", protocol=None)


@contextmanager
def _panel(health: PanelHealth | Exception) -> Iterator[AsyncMock]:
    """Answer the panel's health (or fail it) and status, and the install reads."""
    get_health = AsyncMock(
        side_effect=health if isinstance(health, Exception) else None,
        return_value=None if isinstance(health, Exception) else health,
    )
    executor = SimpleNamespace(async_reconcile_entry=AsyncMock())
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            get_health,
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
            "custom_components.panel_assistant.update_route.async_pin_adb_target",
            AsyncMock(return_value=None),
        ),
    ):
        yield get_health


def _entry(
    hass: HomeAssistant,
    user_id: str,
    *,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """An entry made by Panel Assistant 0.9.0 or earlier (minor version 1)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=DID,
        data={
            CONF_ADDRESS: "panel.local",
            "installation_identity": True,
            CONF_TRANSPORT_USER_ID: user_id,
            **(data or {}),
        },
        options=options or {},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    return entry


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry, health: Any) -> None:
    with _panel(health):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _mqtt_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """The device the panel's MQTT discovery made, under its panel ID."""
    mqtt = MockConfigEntry(domain="mqtt", title="MQTT")
    mqtt.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt.entry_id, identifiers={("mqtt", "ha-paneld-alpha")}
    )


def _issue(hass: HomeAssistant, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def _hello_response(
    hass: HomeAssistant, ws_client: WsClientFactory, token: str, **changes: Any
) -> dict[str, Any]:
    client = await ws_client(hass, token)
    return await _send(client, _hello(**changes))


async def test_a_native_entry_sets_up_on_protocol_5_without_its_authority(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Whatever authority or channel record it stored, the entry is native."""
    entry = _entry(
        hass,
        hass_read_only_user.id,
        options={"authority": "shadow"},
        data={"supported_channels": {"did": DID, "channels": ["relay1"]}},
    )

    await _set_up(hass, entry, CURRENT)

    assert entry.state is ConfigEntryState.LOADED
    assert "authority" not in entry.options
    assert "supported_channels" not in entry.data
    assert (entry.version, entry.minor_version) == (1, 2)
    assert ir.async_get(hass).issues == {}
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["success"], response
    result = response["result"]
    assert result["protocol"] == 5
    assert "authority" not in result
    assert "mqtt_discovery" not in result
    assert "commands" in result["capabilities"]
    # Commands reach the panel: the switch it described renders.
    assert er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{DID}_relay3")


async def test_a_panel_never_moved_off_mqtt_is_refused_until_its_device_goes(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A straight 0.8 to 1.0 upgrade: told to install 0.9.0 first, nothing made."""
    device = _mqtt_device(hass)
    entry = _entry(hass, hass_read_only_user.id, options={"authority": "mqtt"})

    await _set_up(hass, entry, CURRENT)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    issue = _issue(hass, f"mqtt_panel_needs_090_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"panel": "alpha"}
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["error"]["code"] == "unknown_panel"

    dr.async_get(hass).async_remove_device(device.id)
    with _panel(CURRENT):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, f"mqtt_panel_needs_090_{entry.entry_id}") is None
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["success"], response


async def test_a_re_added_panel_still_on_mqtt_is_told_it_is_unknown_not_removed(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A re-added panel keeps its fast retry, so it connects once the device goes."""
    removed = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, removed, CURRENT)
    await hass.config_entries.async_remove(removed.entry_id)
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["error"]["code"] == "entry_removed"

    device = _mqtt_device(hass)
    entry = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, entry, CURRENT)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["error"]["code"] == "unknown_panel"

    dr.async_get(hass).async_remove_device(device.id)
    with _panel(CURRENT):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["success"], response


async def test_a_completed_move_or_an_unknown_panel_id_sets_up(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """0.9.0 moved it, or nothing names the panel: no issue, the entry loads."""
    _mqtt_device(hass)
    moved = _entry(
        hass,
        hass_read_only_user.id,
        data={CONF_CUTOVER: {"state": "complete", "panel_id": "alpha"}},
    )
    await _set_up(hass, moved, CURRENT)
    assert moved.state is ConfigEntryState.LOADED

    unknown = _entry(hass, hass_read_only_user.id, data={CONF_ADDRESS: "other.local"})
    hass.config_entries.async_update_entry(unknown, unique_id="e" * 64)
    await _set_up(hass, unknown, CannotConnectError())
    assert unknown.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).issues == {}


@pytest.mark.parametrize("protocol", [None, (4, 4)])
async def test_an_older_panel_is_refused_and_told_it_needs_app_1_0_0_preview1(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    protocol: tuple[int, int] | None,
) -> None:
    """Refused at hello; its health raises the Repair; protocol 5 clears it."""
    entry = _entry(hass, hass_read_only_user.id)
    issue_id = f"panel_app_too_old_{entry.entry_id}"

    await _set_up(hass, entry, replace(OLDER_APP, protocol=protocol))

    assert entry.state is ConfigEntryState.LOADED
    issue = _issue(hass, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "panel": "alpha",
        "version": "0.9.11",
        "required_version": "1.0.0-rc1",
    }
    response = await _hello_response(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        protocol={"min": 4, "max": 4},
    )
    assert not response["success"], response
    assert response["error"]["code"] == "protocol_unsupported"
    assert response["error"]["translation_placeholders"] == {
        "panel_min": "4",
        "panel_max": "4",
        "integration_min": "5",
        "integration_max": "5",
    }

    coordinator = entry.runtime_data.coordinator
    # An outage says nothing about the app.
    with _panel(CannotConnectError()):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert _issue(hass, issue_id) is not None

    with _panel(CURRENT):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert _issue(hass, issue_id) is None
    with _panel(OLDER_APP):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert _issue(hass, issue_id) is not None

    # Updated: the panel says hello on protocol 5 before the next poll.
    response = await _hello_response(hass, hass_ws_client, hass_read_only_access_token)
    assert response["success"], response
    await hass.async_block_till_done()
    assert _issue(hass, issue_id) is None

    # End the entry while its simulated panel still answers, before the
    # WebSocket fixture closes and dispatches its final session change.
    with _panel(CURRENT):
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_a_panel_on_the_old_app_id_gets_only_its_refusal(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """The refusal says what to install first, so it alone asks for the install."""
    entry = _entry(hass, hass_read_only_user.id)

    await _set_up(hass, entry, replace(OLDER_APP, package=None))

    assert entry.state is ConfigEntryState.LOADED
    assert _issue(hass, f"old_app_needs_090_{entry.entry_id}") is not None
    assert _issue(hass, f"move_to_new_app_{entry.entry_id}") is None
    assert _issue(hass, f"panel_app_too_old_{entry.entry_id}") is None


async def test_removing_the_entry_withdraws_its_repair(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """A removed panel leaves no Repair behind."""
    entry = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, entry, OLDER_APP)
    assert _issue(hass, f"panel_app_too_old_{entry.entry_id}") is not None

    await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, f"panel_app_too_old_{entry.entry_id}") is None

"""HA owner aliases reach the panel through its ordinary settings command."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import PanelDevice
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_availability import _load
from .test_management import MANAGED, Panel, _connect


def _device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    return device


async def _rename(panel: Panel, name: str, outcome: str = "applied") -> None:
    request = await panel.manage()
    assert request["op"] == "settings"
    assert request["settings"] == {"friendly_name": name}
    fields = {} if outcome == "applied" else {"code": "failed"}
    assert (await panel.answer(request, outcome, **fields))["success"]


async def test_registry_rename_delivers_only_friendly_name_and_preserves_identity(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    entities = dict(er.async_get(hass).entities)
    identity = (entry.unique_id, dict(entry.data), device.identifiers)

    registry.async_update_device(device.id, name_by_user="Living room")
    await _rename(panel, "Living room")
    await hass.async_block_till_done()

    assert (
        entry.unique_id,
        dict(entry.data),
        _device(hass, entry).identifiers,
    ) == identity
    assert dict(er.async_get(hass).entities) == entities
    assert _device(hass, entry).id == device.id
    assert _device(hass, entry).name_by_user == "Living room"


async def test_offline_alias_reconciles_latest_on_connect_and_load(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    registry.async_update_device(device.id, name_by_user="First")
    registry.async_update_device(device.id, name_by_user="Latest")
    await hass.async_block_till_done()
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    await _rename(panel, "Latest")
    await hass.async_block_till_done()
    await panel.client.close()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    # An alias already saved before setup is also reconciled on the next session.
    from .test_transport import HEALTH, STATUS

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        panel = await _connect(
            hass, hass_ws_client, hass_read_only_access_token, MANAGED
        )
        await _rename(panel, "Latest")


async def test_rapid_renames_serialize_and_coalesce_to_latest(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    coordinator = entry.runtime_data.coordinator
    coordinator.async_set_updated_data(
        replace(
            coordinator.data,
            status=replace(
                coordinator.data.status,
                panel_assistant_device=PanelDevice(name="First"),
            ),
        )
    )
    registry.async_update_device(device.id, name_by_user="First")
    first = await panel.manage()
    assert first["settings"] == {"friendly_name": "First"}
    registry.async_update_device(device.id, name_by_user="Intermediate")
    registry.async_update_device(device.id, name_by_user="Last")
    await hass.async_block_till_done()
    assert len(async_get_sessions(hass).get(entry.entry_id).pending) == 1
    await panel.answer(first, "applied")
    await _rename(panel, "Last")
    await hass.async_block_till_done()
    # The poll still carries its old name; the last applied write must win.
    registry.async_update_device(device.id, name_by_user="First")
    await _rename(panel, "First")


@pytest.mark.parametrize("alias", [None, "", "   "])
async def test_clearing_alias_withdraws_offline_intent_without_fallback_rename(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    alias: str | None,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    registry.async_update_device(device.id, name_by_user="Pending")
    registry.async_update_device(device.id, name_by_user=alias)
    await hass.async_block_till_done()
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
        await hass.async_block_till_done()
        send.assert_not_called()
    assert _device(hass, entry).name == device.name


async def test_refusal_keeps_alias_without_poll_loop_and_retries_on_reconnect(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    device = _device(hass, entry)
    dr.async_get(hass).async_update_device(device.id, name_by_user="Wanted")
    await _rename(panel, "Wanted", "failed")
    await hass.async_block_till_done()
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        entry.runtime_data.coordinator.async_update_listeners()
        await hass.async_block_till_done()
        send.assert_not_called()
    assert _device(hass, entry).name_by_user == "Wanted"
    await panel.client.close()
    await hass.async_block_till_done()
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    await _rename(panel, "Wanted")


async def test_projected_and_other_device_changes_never_echo_a_rename(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    other = MockConfigEntry(domain=DOMAIN, title="other")
    other.add_to_hass(hass)
    foreign = registry.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={(DOMAIN, other.entry_id)}
    )
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        registry.async_update_device(device.id, name="Reported", model="New model")
        registry.async_update_device(foreign.id, name_by_user="Other alias")
        await hass.async_block_till_done()
        send.assert_not_called()


async def test_unload_cancels_inflight_and_refuses_later_registry_changes(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    registry.async_update_device(device.id, name_by_user="First")
    first = await panel.manage()
    assert first["settings"] == {"friendly_name": "First"}
    registry.async_update_device(device.id, name_by_user="Never sent")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        registry.async_update_device(device.id, name_by_user="After unload")
        await hass.async_block_till_done()
        send.assert_not_called()
    assert not async_get_sessions(hass).get(entry.entry_id)


@pytest.mark.parametrize("blocker", ["identity", "management"])
async def test_untrusted_or_ungranted_session_never_receives_owner_name(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    blocker: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    capabilities = MANAGED if blocker == "identity" else []
    await _connect(hass, hass_ws_client, hass_read_only_access_token, capabilities)
    entry.runtime_data.coordinator.identity_mismatch = blocker == "identity"
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        dr.async_get(hass).async_update_device(
            _device(hass, entry).id, name_by_user="Owner intent"
        )
        await hass.async_block_till_done()
        send.assert_not_called()


async def test_replacement_session_sends_latest_alias_after_old_command_ends(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    old = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    registry.async_update_device(device.id, name_by_user="Old")
    assert (await old.manage())["settings"] == {"friendly_name": "Old"}
    registry.async_update_device(device.id, name_by_user="Latest")
    replacement = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, MANAGED
    )
    await _rename(replacement, "Latest")


async def test_clear_during_delivery_withdraws_pending_alias_without_reverting_panel(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    registry = dr.async_get(hass)
    device = _device(hass, entry)
    registry.async_update_device(device.id, name_by_user="In flight")
    request = await panel.manage()
    assert request["settings"] == {"friendly_name": "In flight"}
    registry.async_update_device(device.id, name_by_user="Pending")
    registry.async_update_device(device.id, name_by_user=None)
    await hass.async_block_till_done()
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        await panel.answer(request, "applied")
        await hass.async_block_till_done()
        send.assert_not_called()
    assert _device(hass, entry).name_by_user is None

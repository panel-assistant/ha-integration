"""Discovery and explicit adoption preserve one panel's identity and setup."""

import json
from dataclasses import replace
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError, HaPaneldClient
from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    SOURCE_USB_INSTALL,
)
from custom_components.panel_assistant.identity import (
    CONF_INSTALL_IDENTITY,
    CONF_LEGACY_DID,
)
from custom_components.panel_assistant.panel_move import (
    claim_panel_operation,
    release_panel_operation,
)

from .config_flow_helpers import (
    DISCOVERY_HEALTH,
    _connect_found,
    _start_step,
    _zeroconf_info,
)
from .config_flow_helpers import install_network_pin as install_network_pin
from .test_availability import STORED, _health_by_host, _load, _poll
from .test_migration_targets import (
    _companion,
    _discovered,
    _host_entry,
    _network_device_seen,
)
from .test_migration_targets import adb_open as adb_open
from .test_transport import DID, OTHER_DID, STATUS, _hello, _send
from .test_usb_setup_handover import endpoint as endpoint

_NEW = "192.168.1.23"
_LINK = "c" * 64
_REINSTALLED = replace(
    DISCOVERY_HEALTH,
    discovery_id=OTHER_DID,
    installation_identity=True,
    legacy_discovery_id=_LINK,
)
_REAL_SETUP_STATE = HaPaneldClient.async_get_setup_state
_REAL_SETUP_COMPLETE = HaPaneldClient.async_get_setup_complete


async def _original(hass, user_id):
    entry = await _load(hass, user_id, unique_id=DID)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_INSTALL_IDENTITY: True, CONF_LEGACY_DID: _LINK}
    )
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert not entry.runtime_data.coordinator.last_update_success
    return entry


@pytest.mark.parametrize("route", ["discovery", "manual", "hello"])
async def test_reinstalled_panel_keeps_original_entry_and_history(
    hass, hass_read_only_user, hass_ws_client, hass_read_only_access_token, route
):
    """Every adoption caller retains history and retires the discovery card."""
    pending = None
    if route == "hello":
        # Discovery can arrive before the existing entry has loaded its link.
        with patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=_REINSTALLED)
        ):
            pending = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf_info(discovery_id=OTHER_DID, host=_NEW),
            )
        assert pending["type"] is FlowResultType.FORM
    original = await _original(hass, hass_read_only_user.id)
    registry = er.async_get(hass)
    entities = {
        item.id: (item.entity_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, original.entry_id)
    }
    address = _NEW + ":9999" if route == "manual" else _NEW
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            _health_by_host(
                {
                    STORED: CannotConnectError(),
                    _NEW: _REINSTALLED,
                    "127.0.0.1": CannotConnectError(),
                }
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        if route == "manual":
            form = await _start_step(hass, "add_panel")
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: address}
            )
            if result["type"] is FlowResultType.MENU:
                await _connect_found(hass, result)
        else:
            result = pending or await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf_info(discovery_id=OTHER_DID, host=_NEW),
            )
            if route == "hello":
                client = await hass_ws_client(hass, hass_read_only_access_token)
                await _send(client, _hello(did=OTHER_DID, addresses=[_NEW]))
            else:
                # A known offline reinstall rejoins at discovery, without an
                # owner having to find and confirm another discovery card.
                assert result["type"] is FlowResultType.ABORT
                assert result["reason"] == "already_configured"
                assert original.unique_id == OTHER_DID
                assert original.data[CONF_ADDRESS] == _NEW
        await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == [original]
    assert original.unique_id == OTHER_DID
    assert original.data[CONF_ADDRESS] == address
    assert original.data[CONF_LEGACY_DID] == _LINK
    assert original.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert {
        item.id: (item.entity_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, original.entry_id)
    } == entities


@pytest.mark.parametrize("hold", ["connected", "reachable", "operation"])
async def test_reinstall_discovery_preserves_live_or_busy_original(
    hass, hass_read_only_user, hass_ws_client, hass_read_only_access_token, hold
):
    """A live clone stays addable; a busy offline reinstall creates no duplicate."""
    original = await _original(hass, hass_read_only_user.id)
    old_health = replace(_REINSTALLED, discovery_id=DID)
    if hold == "operation":
        assert claim_panel_operation(hass, original.entry_id)
        old_answer = CannotConnectError()
    else:
        await _poll(hass, original, {STORED: old_health})
        old_answer = old_health
        if hold == "connected":
            client = await hass_ws_client(hass, hass_read_only_access_token)
            response = await _send(client, _hello())
            assert response["success"]
    try:
        with (
            patch.object(
                HaPaneldClient,
                "async_get_health",
                _health_by_host({STORED: old_answer, _NEW: _REINSTALLED}),
            ),
            patch.object(
                HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf_info(discovery_id=OTHER_DID, host=_NEW),
            )
            if hold == "operation":
                assert result["type"] is FlowResultType.ABORT
            else:
                assert result["type"] is FlowResultType.FORM
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {}
                )
                await hass.async_block_till_done()
                assert result["type"] is FlowResultType.CREATE_ENTRY
        entries = hass.config_entries.async_entries(DOMAIN)
        assert original in entries
        assert len(entries) == (1 if hold == "operation" else 2)
        assert original.unique_id == DID
        assert original.data[CONF_ADDRESS] == STORED
        assert original.data[CONF_LEGACY_DID] == _LINK
    finally:
        if hold == "operation":
            release_panel_operation(hass, original.entry_id)


@pytest.mark.parametrize("with_card", [False, True])
async def test_manual_add_replaces_ignore_only_when_finished(hass, with_card):
    """Explicit adoption supersedes Ignore, while abandoning the flow keeps it."""
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=DISCOVERY_HEALTH)
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        if with_card:
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf_info(),
            )
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IGNORE},
            data={"unique_id": DISCOVERY_HEALTH.discovery_id, "title": "Ignored panel"},
        )
        ignored = result["result"]
        user = await hass.auth.async_create_user("Panel account")
        from custom_components.panel_assistant.transport_binding import (
            async_record_binding_request,
        )

        async_record_binding_request(hass, DISCOVERY_HEALTH.discovery_id, user.id)
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: _NEW + ":9999"}
        )
        confirm = await _connect_found(hass, found)
        assert confirm["type"] is FlowResultType.MENU
        assert confirm["step_id"] == "confirm_user"
        assert hass.config_entries.async_entries(DOMAIN) == [ignored]
        hass.config_entries.flow.async_abort(confirm["flow_id"])
        assert hass.config_entries.async_entries(DOMAIN) == [ignored]
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: _NEW + ":9999"}
        )
        confirm = await _connect_found(hass, found)
        result = await hass.config_entries.flow.async_configure(
            confirm["flow_id"], {"next_step_id": "bind_user"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert hass.config_entries.async_get_entry(ignored.entry_id) is None
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        assert entries[0].source == config_entries.SOURCE_USER
        assert entries[0].unique_id == DISCOVERY_HEALTH.discovery_id
        assert entries[0].data[CONF_TRANSPORT_USER_ID] == user.id


@pytest.mark.parametrize("vendor_first", [True, False])
@pytest.mark.parametrize("friendly_name", ["Shelly HA Panel", "\n"])
async def test_later_app_announcement_titles_the_existing_vendor_card(
    hass, vendor_first, friendly_name
):
    """A valid later TXT name improves one card without a status request."""
    health = replace(DISCOVERY_HEALTH, panel_id="shelly_ha_panel")
    address = ip_address(_NEW)
    vendor = ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=80,
        hostname="ShellyWallDisplay-00A90B9D900A.local.",
        type="_shelly._tcp.local.",
        name="ShellyWallDisplay-00A90B9D900A._shelly._tcp.local.",
        properties={},
    )
    app = _zeroconf_info(friendly_name=friendly_name)
    announcements = [vendor, app] if vendor_first else [app, vendor]
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=health)
        ),
        patch.object(
            HaPaneldClient,
            "async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
    ):
        first = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=announcements[0],
        )
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=announcements[1],
        )
        progress = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert len(progress) == 1
        expected = (
            "Shelly HA Panel" if friendly_name == "Shelly HA Panel" else health.panel_id
        )
        assert progress[0]["context"]["title_placeholders"]["name"] == expected
        result = await hass.config_entries.flow.async_configure(first["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == expected


@pytest.mark.parametrize("route", ["dhcp", "stale", "manual", "nonstandard"])
async def test_known_hostname_panel_is_not_offered_as_unconnected(
    hass, adb_open, route
):
    """Registry aliases stay hidden; explicit aliases repair the original entry."""
    health = replace(DISCOVERY_HEALTH, installation_identity=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Panel",
        unique_id=health.discovery_id,
        data={CONF_ADDRESS: STORED, CONF_INSTALL_IDENTITY: True},
    )
    _companion(hass, "Panel tablet", _NEW, "panel")
    adb_open.open.add(_NEW)
    if route == "stale":
        await _network_device_seen(hass)
        card = _discovered(hass)["Panel tablet"]
    entry.add_to_hass(hass)
    with patch.object(
        HaPaneldClient, "async_get_health", AsyncMock(return_value=health)
    ):
        if route == "dhcp":
            await _network_device_seen(hass)
            assert not _discovered(hass)
            assert entry.data[CONF_ADDRESS] == STORED
        elif route == "stale":
            result = await hass.config_entries.flow.async_configure(card["flow_id"], {})
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "already_configured"
        else:
            form = await _start_step(hass, "add_panel")
            address = _NEW + ":9999" if route == "nonstandard" else _NEW
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: address}
            )
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "already_configured"
            assert entry.data[CONF_ADDRESS] == address
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


@pytest.mark.parametrize(
    "complete,needs_user", [(False, False), (False, True), (False, None), (True, None)]
)
async def test_found_panel_setup_menu_asks_only_for_user_steps(
    hass, monkeypatch, complete, needs_user
):
    """Render proof does not open the wizard; old responses retain their meaning."""
    document = {"complete": complete}
    if needs_user is not None:
        document["needs_user"] = needs_user
    monkeypatch.setattr(HaPaneldClient, "async_get_setup_state", _REAL_SETUP_STATE)
    monkeypatch.setattr(
        HaPaneldClient, "async_get_setup_complete", _REAL_SETUP_COMPLETE
    )
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=DISCOVERY_HEALTH)
        ),
        patch.object(
            HaPaneldClient,
            "_async_get_bounded",
            AsyncMock(return_value=json.dumps(document).encode()),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: _NEW + ":9999"}
        )
    needs_wizard = not complete if needs_user is None else needs_user
    assert result["step_id"] == (
        "found_unconfigured" if needs_wizard else "found_panel"
    )
    assert ("panel_setup" in result["menu_options"]) is needs_wizard


@pytest.mark.parametrize("stored", [STORED, _NEW])
async def test_unknown_discovery_probe_failure_keeps_add_panel_usable(
    hass, adb_open, stored
):
    """A failed alias classifier does not break opening Add panel."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=DISCOVERY_HEALTH.discovery_id,
        data={CONF_ADDRESS: stored, CONF_INSTALL_IDENTITY: True},
    ).add_to_hass(hass)
    _companion(hass, "Panel tablet", _NEW, "panel")
    adb_open.open.add(_NEW)
    with patch.object(
        HaPaneldClient,
        "async_get_health",
        AsyncMock(side_effect=RuntimeError("broken panel response")),
    ):
        form = await _start_step(hass, "add_panel")
        await hass.async_block_till_done()
    assert form["step_id"] == "add_panel"
    assert not _discovered(hass)


async def test_replacing_legacy_ignore_closes_other_channel_card(hass, adb_open):
    """Core's Ignore takeover skips its creation hook, but no stale card survives."""
    with (
        patch.object(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=DISCOVERY_HEALTH)
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.async_unload_entry",
            AsyncMock(return_value=True),
        ),
    ):
        ignored = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IGNORE},
            data={"unique_id": DISCOVERY_HEALTH.discovery_id, "title": "Old Ignore"},
        )
        assert ignored["result"].data == {}
        _host_entry(hass, "fully_kiosk", "Registry panel", _NEW)
        adb_open.open.add(_NEW)
        await _network_device_seen(hass)
        assert set(_discovered(hass)) == {"Registry panel"}
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USB_INSTALL}, data={CONF_ADDRESS: _NEW}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        remaining = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        await hass.config_entries.async_unload(result["result"].entry_id)
        assert not remaining


async def test_enriched_discovery_clash_persists_both_qualified_names(hass):
    """Retitling another pending card also changes the name its entry will keep."""
    beta = replace(DISCOVERY_HEALTH, panel_id="beta", discovery_id="f" * 64)
    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({_NEW: DISCOVERY_HEALTH, "192.168.1.24": beta}),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
    ):
        alpha = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        other = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(
                discovery_id=beta.discovery_id,
                host="192.168.1.24",
                friendly_name="Shared name",
            ),
        )
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(friendly_name="Shared name"),
        )
        for form, panel_id in [(alpha, "alpha"), (other, "beta")]:
            result = await hass.config_entries.flow.async_configure(form["flow_id"], {})
            assert result["title"] == f"Shared name ({panel_id})"


@pytest.mark.parametrize("route", ["manual", "usb"])
@pytest.mark.parametrize("hold", ["connected", "reachable"])
async def test_explicit_add_of_live_clone_preserves_original_history(
    hass,
    hass_read_only_user,
    hass_ws_client,
    hass_read_only_access_token,
    endpoint,
    route,
    hold,
):
    """An explicit add does not make a shared legacy hint own a live original."""
    from custom_components.panel_assistant.client import PanelSetupState

    from .test_usb_setup_handover import _Panel, _post

    original = await _original(hass, hass_read_only_user.id)
    old_health = replace(_REINSTALLED, discovery_id=DID)
    await _poll(hass, original, {STORED: old_health})
    if hold == "connected":
        client = await hass_ws_client(hass, hass_read_only_access_token)
        assert (await _send(client, _hello()))["success"]
    registry = er.async_get(hass)
    entities = {
        item.id: (item.entity_id, item.device_id, item.unique_id)
        for item in er.async_entries_for_config_entry(registry, original.entry_id)
    }
    with (
        patch.object(
            HaPaneldClient,
            "async_get_health",
            _health_by_host({STORED: old_health, _NEW: _REINSTALLED}),
        ),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
        patch(
            "custom_components.panel_assistant.browser_delivery.async_read_recommended_sizing",
            AsyncMock(return_value=None),
        ),
    ):
        if route == "manual":
            form = await _start_step(hass, "add_panel")
            found = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: _NEW + ":9999"}
            )
            assert found["type"] is FlowResultType.MENU
            result = await _connect_found(hass, found)
            assert result["type"] is FlowResultType.CREATE_ENTRY
        else:
            status, body = await _post(
                endpoint,
                _Panel(PanelSetupState(complete=False, accepts_handover=True)),
                {"address": _NEW},
            )
            assert status == 200
            assert body["registration"] == "added"
        await hass.async_block_till_done()
    assert original.unique_id == DID
    assert original.data[CONF_ADDRESS] == STORED
    assert original.data[CONF_LEGACY_DID] == _LINK
    assert original.data[CONF_TRANSPORT_USER_ID] == hass_read_only_user.id
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    assert {
        item.id: (item.entity_id, item.device_id, item.unique_id)
        for item in er.async_entries_for_config_entry(registry, original.entry_id)
    } == entities

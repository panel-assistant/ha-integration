"""A removed app can be installed again without discarding its HA history."""

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import device_setup
from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import (
    CONF_DEVICE_SETUP_OWED,
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
)
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_receipt import InstallPhase

from . import config_flow_helpers
from .config_flow_helpers import RELEASE, _choose_version, _probe, _start_step
from .test_config_flow import CREDENTIAL, HEALTH, _final_proof, _manager_for, _receipt
from .test_transport import WsClientFactory, _hello, _send
from .test_upgrade_repairs import _panel

install_release_catalog = config_flow_helpers.install_release_catalog
install_network_pin = config_flow_helpers.install_network_pin
OLD = "a" * 64
NEW = "b" * 64
LINK = "c" * 64
CLEAN = _probe(
    "install_candidate",
    model="WF1589T",
    serial="serial-123",
    primary_abi="arm64-v8a",
    android_sdk=31,
)


def _record_passes(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The device setup pass, as a stand-in that finishes on its first attempt."""
    passes = AsyncMock(
        return_value={"did": NEW, "package": "p", "version_code": 1, "root": False}
    )
    monkeypatch.setattr(device_setup, "async_attempt", passes)
    return passes


async def _keep_loaded_panel(hass: HomeAssistant, user_id: str) -> tuple[Any, Any, Any]:
    """Keep the real entry, device and renamed entity after the app went away."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="My panel",
        unique_id=OLD,
        data={
            CONF_ADDRESS: "panel.local",
            "installation_identity": True,
            "legacy_did": LINK,
            CONF_TRANSPORT_USER_ID: user_id,
        },
    )
    entry.add_to_hass(hass)
    old_health = replace(
        HEALTH,
        discovery_id=OLD,
        legacy_discovery_id=LINK,
        installation_identity=True,
    )
    with _panel(old_health):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, OLD)},
        name="My panel",
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{OLD}_wifi",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_panel_wifi",
    )
    er.async_get(hass).async_update_entity(entity.entity_id, name="My Wi-Fi")
    # Removal from Home Assistant now always deletes the entry, so a kept entry
    # is one whose app went away outside it (for example Android Settings).
    assert entry.state is config_entries.ConfigEntryState.LOADED
    return entry, device, entity


@pytest.mark.parametrize("route", ["address", "discovery", "poll_before_finalization"])
async def test_kept_panel_installs_into_its_original_entry(
    hass: HomeAssistant,
    route: str,
    hass_read_only_user: Any,
    hass_admin_user: Any,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A kept panel reinstalled under another account binds that account.

    The entry stays loaded through the keep step, as production leaves it, and
    the reinstalled panel's own hello during setup is what binds it. The
    reinstall then runs the device setup pass once.
    """
    passes = _record_passes(monkeypatch)
    entry, device, entity = await _keep_loaded_panel(hass, hass_read_only_user.id)
    # Outside a reinstall, another account asking for the kept panel is still
    # a takeover for an administrator to confirm.
    issue = f"panel_user_mismatch_{entry.entry_id}"
    client = await hass_ws_client(hass, hass_access_token)
    response = await _send(client, _hello(did=OLD))
    assert response["error"]["code"] == "panel_user_mismatch"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_read_only_user.id
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = InstallExecutor(hass, manager)
    health = replace(
        HEALTH,
        version=receipt.artifact.version_name,
        discovery_id=NEW,
        legacy_discovery_id=LINK,
        installation_identity=True,
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CLEAN),
        ),
        patch(
            "custom_components.panel_assistant.discovery.async_probe_install_target",
            AsyncMock(return_value=CLEAN),
        ),
        patch(
            "custom_components.panel_assistant.discovery._async_adb_answers",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch.object(executor, "async_ensure_job", AsyncMock()),
        patch.object(executor, "async_wait", AsyncMock(return_value=receipt)),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        if route != "discovery":
            form = await _start_step(hass, "add_panel")
            choose = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            )
        else:
            card = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data={
                    "key": "migration:vendor",
                    "address": "panel.local",
                    "host": "panel.local",
                    "name": "My panel",
                    "source": "vendor",
                    "panel_only": True,
                },
            )
            assert card["type"] is FlowResultType.FORM
            choose = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        assert choose["type"] is FlowResultType.FORM
        confirm = await _choose_version(hass, choose)
        # The install offer says the panel rejoins its kept entry (F3-26).
        assert confirm["step_id"] == "confirm_reinstall_candidate"
        assert confirm["description_placeholders"]["entry_title"] == entry.title
        progress = await hass.config_entries.flow.async_configure(
            confirm["flow_id"], {}
        )
        assert progress["type"] is FlowResultType.SHOW_PROGRESS
        assert progress["step_id"] == "install_progress"
        manager.async_create_or_join.assert_awaited_once()
        manager.async_get.return_value = replace(
            receipt, phase=InstallPhase.HEALTHY_UNCLAIMED
        )
        await hass.async_block_till_done()
        if route == "poll_before_finalization":
            with _panel(health):
                await entry.runtime_data.coordinator.async_refresh()
            await hass.async_block_till_done()
            client = await hass_ws_client(hass, hass_access_token)
            response = await _send(client, _hello(did=NEW))
            assert response["success"], response
            assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_admin_user.id
        with _final_proof(manager, health=AsyncMock(return_value=health)):
            result = await hass.config_entries.flow.async_configure(progress["flow_id"])
            if result.get("type") is FlowResultType.SHOW_PROGRESS_DONE:
                result = await hass.config_entries.flow.async_configure(
                    progress["flow_id"]
                )
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "panel_setup"
            assert result["description_placeholders"]["setup_url"] == (
                "http://192.168.1.23:8888/setup"
            )
            assert not executor.is_finalizer_active(receipt.job_id)
            if route == "poll_before_finalization":
                assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_admin_user.id
            # The reinstalled panel signs in as another account and says hello.
            client = await hass_ws_client(hass, hass_access_token)
            response = await _send(client, _hello(did=NEW))
            assert response["success"], response
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {}
            )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reinstall_successful"
        assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_admin_user.id
    await hass.async_block_till_done(wait_background_tasks=True)
    assert passes.await_count == 1
    assert passes.await_args.args[1] is entry
    assert CONF_DEVICE_SETUP_OWED not in entry.data
    # The flow ending as the pass starts raises no Repair for it.
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, device_setup.device_setup_issue_id(entry.entry_id)
        )
        is None
    )
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.unique_id == NEW
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
    preserved = er.async_get(hass).async_get(entity.entity_id)
    assert preserved is not None
    assert preserved.id == entity.id
    assert preserved.unique_id == f"{NEW}_wifi"
    assert preserved.name == "My Wi-Fi"
    assert preserved.device_id == device.id
    assert dr.async_get(hass).async_get(device.id) is not None
    assert (
        manager.async_transition.await_args.kwargs["consumed_entry_id"]
        == entry.entry_id
    )
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_a_reinstall_closed_before_setup_finishes_leaves_the_repair(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the setup form sends nothing; the owed pass waits for its Repair."""
    passes = _record_passes(monkeypatch)
    entry, _device, _entity = await _keep_loaded_panel(hass, hass_read_only_user.id)
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = InstallExecutor(hass, manager)
    health = replace(
        HEALTH,
        version=receipt.artifact.version_name,
        discovery_id=NEW,
        legacy_discovery_id=LINK,
        installation_identity=True,
    )
    flow = "custom_components.panel_assistant.config_flow"
    with (
        patch(
            f"{flow}.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(f"{flow}.async_probe_install_target", AsyncMock(return_value=CLEAN)),
        patch(f"{flow}.async_resolve_install_choice", AsyncMock(return_value=RELEASE)),
        patch(f"{flow}.async_get_install_job_manager", AsyncMock(return_value=manager)),
        patch(f"{flow}.async_get_install_executor", AsyncMock(return_value=executor)),
        patch(f"{flow}.async_get_adb_credential", AsyncMock(return_value=CREDENTIAL)),
        patch(
            f"{flow}.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch.object(executor, "async_ensure_job", AsyncMock()),
        patch.object(executor, "async_wait", AsyncMock(return_value=receipt)),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        confirm = await _choose_version(hass, choose)
        progress = await hass.config_entries.flow.async_configure(
            confirm["flow_id"], {}
        )
        manager.async_get.return_value = replace(
            receipt, phase=InstallPhase.HEALTHY_UNCLAIMED
        )
        await hass.async_block_till_done()
        with _final_proof(manager, health=AsyncMock(return_value=health)):
            result = await hass.config_entries.flow.async_configure(progress["flow_id"])
            if result.get("type") is FlowResultType.SHOW_PROGRESS_DONE:
                result = await hass.config_entries.flow.async_configure(
                    progress["flow_id"]
                )
            assert result["step_id"] == "panel_setup"
            # The owner closes the dialog before the panel's setup is done.
            hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done(wait_background_tasks=True)
    passes.assert_not_awaited()
    assert entry.data[CONF_DEVICE_SETUP_OWED] == device_setup.OWED_INSTALL
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, device_setup.device_setup_issue_id(entry.entry_id)
    )
    assert issue is not None and issue.is_fixable


@pytest.mark.parametrize("route", ["usb", "poll"])
async def test_kept_panel_reinstall_binds_its_new_account(
    hass: HomeAssistant,
    route: str,
    hass_read_only_user: Any,
    hass_admin_user: Any,
    hass_ws_client: WsClientFactory,
    hass_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """USB registration and the loaded entry's poll share reinstall admission.

    Only the USB install runs the device setup pass; the poll never does.
    """
    from custom_components.panel_assistant.config_flow import SOURCE_USB_INSTALL

    passes = _record_passes(monkeypatch)
    entry, device, entity = await _keep_loaded_panel(hass, hass_read_only_user.id)
    client = await hass_ws_client(hass, hass_access_token)
    issue = f"panel_user_mismatch_{entry.entry_id}"
    response = await _send(client, _hello(did=OLD))
    assert response["error"]["code"] == "panel_user_mismatch"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_read_only_user.id
    health = replace(
        HEALTH, discovery_id=NEW, legacy_discovery_id=LINK, installation_identity=True
    )
    with (
        _panel(health),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        if route == "usb":
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_USB_INSTALL},
                data={CONF_ADDRESS: "panel.local"},
            )
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "already_configured"
        else:
            await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert entry.unique_id == NEW
        response = await _send(client, _hello(did=NEW))
        assert response["success"], response
        assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_admin_user.id
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
        # Repeated accepted health must not forget the newly signed-in account.
        await entry.runtime_data.coordinator.async_refresh()
        assert entry.data.get(CONF_TRANSPORT_USER_ID) == hass_admin_user.id
        await hass.async_block_till_done(wait_background_tasks=True)
    assert passes.await_count == (1 if route == "usb" else 0)
    assert CONF_DEVICE_SETUP_OWED not in entry.data
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    preserved = er.async_get(hass).async_get(entity.entity_id)
    assert preserved is not None
    assert (preserved.id, preserved.unique_id, preserved.name, preserved.device_id) == (
        entity.id,
        f"{NEW}_wifi",
        "My Wi-Fi",
        device.id,
    )
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.parametrize("link", [None, "d" * 64])
async def test_reinstall_does_not_give_an_unlinked_panel_the_kept_history(
    hass: HomeAssistant,
    link: str | None,
) -> None:
    """Verified installation does not make the endpoint an identity authority."""
    from .test_config_flow import _direct_result_flow

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OLD,
        data={
            CONF_ADDRESS: "panel.local",
            "installation_identity": True,
            "legacy_did": LINK,
            CONF_TRANSPORT_USER_ID: "kept-panel-user",
        },
    )
    entry.add_to_hass(hass)
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{OLD}_wifi",
        config_entry=entry,
    )
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = InstallExecutor(hass, manager)
    health = replace(
        HEALTH,
        version=receipt.artifact.version_name,
        discovery_id=NEW,
        legacy_discovery_id=link,
        installation_identity=True,
    )
    with _final_proof(manager, health=AsyncMock(return_value=health)):
        result = await _direct_result_flow(
            hass, receipt, executor
        ).async_step_install_result()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reinstall_identity_mismatch"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.unique_id == OLD
    assert er.async_get(hass).async_get(entity.entity_id) == entity
    assert entry.data.get(CONF_TRANSPORT_USER_ID) == "kept-panel-user"
    manager.async_transition.assert_not_awaited()
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_running_kept_address_is_not_offered_as_a_new_install(
    hass: HomeAssistant,
) -> None:
    """A healthy configured panel stays added when an unsigned ADB probe needs auth."""
    MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"}).add_to_hass(hass)
    with (
        patch(
            "custom_components.panel_assistant.discovery.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.discovery.async_probe_install_target",
            AsyncMock(return_value=_probe("adb_unauthorized")),
        ) as probe,
        patch(
            "custom_components.panel_assistant.discovery._async_adb_answers",
            AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={
                "key": "migration:vendor",
                "address": "panel.local",
                "host": "panel.local",
                "name": "My panel",
                "source": "vendor",
                "panel_only": True,
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    probe.assert_not_awaited()

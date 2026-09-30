"""Existing-panel ADB authorization through Home Assistant options."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelAddress, PanelHealth
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.failure_repair import (
    adb_authorization_issue_id,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)


async def test_existing_panel_options_authorize_adb_after_physical_approval(
    hass: HomeAssistant,
) -> None:
    """Consent precedes key creation; approval refreshes the cached update route."""
    health = PanelHealth(
        version="0.9.9", panel_id="jenna", build="998", config_hash="abcd1234"
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test display",
        data={CONF_ADDRESS: "panel.local"},
        options={"authority": "native"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=health))
    )
    signer = object()
    pin = PinnedPanelTarget(
        original=PanelAddress(host="panel.local", port=8888),
        pinned=PanelAddress(host="192.168.1.23", port=8888),
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(return_value=signer),
        ) as get_signer,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(
                side_effect=[
                    InstallTargetProbe(state=InstallTargetState.ADB_UNAUTHORIZED),
                    InstallTargetProbe(state=InstallTargetState.INSTALLED),
                ]
            ),
        ) as probe,
        patch.object(
            hass.config_entries, "async_reload", AsyncMock(return_value=True)
        ) as reload,
    ):
        opened = await hass.config_entries.options.async_init(entry.entry_id)
        assert opened["type"] is FlowResultType.MENU
        assert "authorize_adb" in opened["menu_options"]
        consent = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "authorize_adb"}
        )
        assert consent["type"] is FlowResultType.FORM
        get_signer.assert_not_awaited()
        probe.assert_not_awaited()

        pending = await hass.config_entries.options.async_configure(
            opened["flow_id"], {}
        )
        assert pending["step_id"] == "authorize_adb"
        assert pending["errors"] == {"base": "adb_still_unauthorized"}
        get_signer.assert_awaited_once()
        assert probe.await_args.args[1] is signer
        reload.assert_not_awaited()

        done = await hass.config_entries.options.async_configure(opened["flow_id"], {})

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"authority": "native"}
    reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("observed_did", ["a" * 64, "b" * 64])
async def test_existing_panel_adb_consent_refuses_changed_http_identity(
    hass: HomeAssistant,
    observed_did: str,
) -> None:
    """A changed address never receives the persistent ADB key."""
    original = PanelHealth(
        version="0.9.9",
        panel_id="jenna",
        build="998",
        config_hash="abcd1234",
        discovery_id=observed_did,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test display",
        unique_id="a" * 64,
        data={CONF_ADDRESS: "192.168.1.23"},
        options={"authority": "native"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=original))
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(
                return_value=PanelHealth(
                    version="0.9.9",
                    panel_id="jenna",
                    build="998",
                    config_hash="abcd1234",
                    discovery_id="b" * 64,
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(),
        ) as get_signer,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(),
        ) as probe,
    ):
        opened = await hass.config_entries.options.async_init(entry.entry_id)
        consent = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "authorize_adb"}
        )
        refused = await hass.config_entries.options.async_configure(
            consent["flow_id"], {}
        )
    assert refused["step_id"] == "authorize_adb"
    assert refused["errors"] == {"base": "panel_identity_changed"}
    get_signer.assert_not_awaited()
    probe.assert_not_awaited()


async def test_adb_authorization_repair_retries_after_physical_approval(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """A named Repair enters the existing authorization operation directly."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    health = PanelHealth(
        version="0.9.9", panel_id="jenna", build="998", config_hash="abcd1234"
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Jenna", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=health))
    )
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Jenna",
    )
    dr.async_get(hass).async_update_device(device.id, name_by_user="Office display")
    issue_id = adb_authorization_issue_id(entry.entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="adb_update_authorization",
        translation_placeholders={"panel": "Jenna"},
        data={"entry_id": entry.entry_id},
    )
    pin = PinnedPanelTarget(
        original=PanelAddress(host="panel.local", port=8888),
        pinned=PanelAddress(host="192.168.1.23", port=8888),
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(return_value=object()),
        ) as get_signer,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(
                side_effect=[
                    InstallTargetProbe(state=InstallTargetState.ADB_UNAUTHORIZED),
                    InstallTargetProbe(state=InstallTargetState.INSTALLED),
                ]
            ),
        ) as probe,
        patch.object(
            hass.config_entries, "async_reload", AsyncMock(return_value=True)
        ) as reload,
    ):
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        assert response.status == 200
        opened = await response.json()
        assert opened["type"] == "form"
        assert opened["step_id"] == "authorize_adb"
        assert opened["description_placeholders"] == {"panel": "Office display"}
        get_signer.assert_not_awaited()
        probe.assert_not_awaited()

        response = await admin.post(
            f"/api/repairs/issues/fix/{opened['flow_id']}", json={}
        )
        pending = await response.json()
        assert pending["errors"] == {"base": "adb_still_unauthorized"}
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        reload.assert_not_awaited()

        response = await admin.post(
            f"/api/repairs/issues/fix/{opened['flow_id']}", json={}
        )
        done = await response.json()
    assert done["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert get_signer.await_count == 2
    reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize("entry_did", ["a" * 64, None])
async def test_adb_authorization_repair_refuses_changed_panel(
    hass: HomeAssistant, hass_client: Any, entry_did: str | None
) -> None:
    """A Repair cannot offer HA's key to a changed address target."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    expected = PanelHealth(
        version="0.9.9",
        panel_id="jenna",
        build="998",
        config_hash="abcd1234",
        discovery_id="a" * 64,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Jenna",
        unique_id=entry_did,
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=expected))
    )
    issue_id = adb_authorization_issue_id(entry.entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="adb_update_authorization",
        translation_placeholders={"panel": "Jenna"},
        data={"entry_id": entry.entry_id},
    )
    pin = PinnedPanelTarget(
        original=PanelAddress(host="panel.local", port=8888),
        pinned=PanelAddress(host="192.168.1.23", port=8888),
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(return_value=pin),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(
                return_value=PanelHealth(
                    version="0.9.9",
                    panel_id="jenna",
                    build="998",
                    config_hash="abcd1234",
                    discovery_id="b" * 64,
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(),
        ) as get_signer,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(),
        ) as probe,
    ):
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        opened = await response.json()
        response = await admin.post(
            f"/api/repairs/issues/fix/{opened['flow_id']}", json={}
        )
        refused = await response.json()
    assert refused["errors"] == {"base": "panel_identity_changed"}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    get_signer.assert_not_awaited()
    probe.assert_not_awaited()


async def test_failed_update_repair_can_authorize_without_clearing_failure(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """Recovery of ADB trust keeps the failed update visible until it succeeds."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Jenna", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="installer_failure_adb_authorization",
        translation_placeholders={"panel": "Jenna"},
        data={"key": issue_id, "entry_id": entry.entry_id},
    )
    with patch(
        "custom_components.panel_assistant.repairs.async_authorize_existing_panel_adb",
        AsyncMock(return_value=None),
    ) as authorize:
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        menu = await response.json()
        assert "authorize_adb" in menu["menu_options"]
        assert menu["description_placeholders"] == {"panel": "Jenna"}
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}",
            json={"next_step_id": "authorize_adb"},
        )
        assert (await response.json())["step_id"] == "authorize_adb"
        authorize.assert_not_awaited()
        response = await admin.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        back = await response.json()
    assert back["type"] == "menu"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update"
    assert "authorize_adb" not in back["menu_options"]
    authorize.assert_awaited_once_with(hass, entry)

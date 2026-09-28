"""Existing-panel ADB authorization through Home Assistant options."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelAddress, PanelHealth
from custom_components.panel_assistant.const import DOMAIN
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


async def test_existing_panel_adb_consent_refuses_changed_http_identity(
    hass: HomeAssistant,
) -> None:
    """A changed address never receives the persistent ADB key."""
    original = PanelHealth(
        version="0.9.9",
        panel_id="jenna",
        build="998",
        config_hash="abcd1234",
        discovery_id="a" * 64,
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

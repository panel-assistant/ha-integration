"""Existing-panel steps in Home Assistant options: ADB and account binding."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelAddress, PanelHealth
from custom_components.panel_assistant.const import (
    DOMAIN,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.repairs_failure import (
    adb_authorization_issue_id,
)

from .install_recovery_text import assert_names_recovery_page


async def _after_authorization_progress(
    hass: HomeAssistant, result: Any, admin: Any = None
) -> Any:
    """The frontend refetches the next screen after a completed wait."""
    assert result["type"] == "progress"
    await hass.async_block_till_done()
    if admin is not None:
        response = await admin.post(
            f"/api/repairs/issues/fix/{result['flow_id']}", json={}
        )
        return await response.json()
    return await hass.config_entries.options.async_configure(result["flow_id"], {})


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
        protocol=(5, 5),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test display",
        unique_id="a" * 64,
        data={CONF_ADDRESS: "192.168.1.23"},
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
                    protocol=(5, 5),
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
        refused = await _after_authorization_progress(hass, refused)
    assert refused["step_id"] == "authorize_adb"
    assert refused["errors"] == {"base": "panel_identity_changed"}
    get_signer.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.parametrize("entry_did", ["a" * 64, None, "b" * 64])
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
        protocol=(5, 5),
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
            "custom_components.panel_assistant.install_network.async_pin_install_target",
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
                    protocol=(5, 5),
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
        refused = await _after_authorization_progress(hass, refused, admin)
    assert refused["errors"] == {"base": "panel_identity_changed"}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    get_signer.assert_not_awaited()
    probe.assert_not_awaited()


async def test_ambiguous_panel_state_in_options_and_repair_names_the_recovery_page(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """The shared authorize_adb step names the recovery page wherever it is shown."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Jenna", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(
            data=SimpleNamespace(
                health=PanelHealth(
                    version="0.9.9",
                    panel_id="jenna",
                    build="998",
                    config_hash="abcd1234",
                    protocol=(5, 5),
                )
            )
        )
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
    with patch(
        "custom_components.panel_assistant.config_flow.async_authorize_existing_panel_adb",
        AsyncMock(return_value="retained_or_ambiguous"),
    ):
        opened = await hass.config_entries.options.async_init(entry.entry_id)
        consent = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "authorize_adb"}
        )
        refused = await hass.config_entries.options.async_configure(
            consent["flow_id"], {}
        )
        refused = await _after_authorization_progress(hass, refused)
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        repair = await response.json()
        response = await admin.post(
            f"/api/repairs/issues/fix/{repair['flow_id']}", json={}
        )
        repaired = await response.json()
        repaired = await _after_authorization_progress(hass, repaired, admin)

    assert refused["errors"] == {"base": "retained_or_ambiguous"}
    await assert_names_recovery_page(
        hass, "options.error.retained_or_ambiguous", refused
    )
    assert repaired["errors"] == {"base": "retained_or_ambiguous"}
    await assert_names_recovery_page(
        hass,
        "issues.adb_update_authorization.fix_flow.error.retained_or_ambiguous",
        repaired,
    )

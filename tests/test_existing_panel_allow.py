"""Existing-panel callers wait for one remembered Allow on the panel."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
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
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.repairs_failure import (
    adb_authorization_issue_id,
    panel_failure_issue_id,
)


class AskingPanel:
    def __init__(self) -> None:
        self.trusted = False
        self.remembers = True
        self.taps: asyncio.Queue[bool] = asyncio.Queue()
        self.offers: list[float] = []
        self.cancelled = False

    async def probe(
        self,
        _address: Any,
        _signer: Any = None,
        *,
        authorize: bool = False,
        approval_seconds: float = 5.0,
    ) -> InstallTargetProbe:
        if self.trusted:
            return InstallTargetProbe(state=InstallTargetState.INSTALLED)
        if not authorize:
            return InstallTargetProbe(state=InstallTargetState.ADB_UNAUTHORIZED)
        self.offers.append(approval_seconds)
        try:
            allowed = await self.taps.get()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if not allowed:
            return InstallTargetProbe(state=InstallTargetState.ADB_UNAUTHORIZED)
        self.trusted = self.remembers
        return InstallTargetProbe(state=InstallTargetState.INSTALLED)


@pytest.fixture(params=["options", "authorization_repair", "failure_repair"])
async def caller(
    hass: HomeAssistant, hass_client: Any, request: pytest.FixtureRequest
) -> AsyncIterator[Any]:
    """Use real Options management and Repairs HTTP; fake only panel I/O."""
    route = request.param
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    health = PanelHealth(
        version="0.9.9",
        panel_id="jenna",
        build="998",
        config_hash="abcd1234",
        protocol=(5, 5),
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Jenna", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=health))
    )
    issue_id = (
        panel_failure_issue_id(f"update:{entry.entry_id}")
        if route == "failure_repair"
        else adb_authorization_issue_id(entry.entry_id)
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="installer_failure_update"
        if route == "failure_repair"
        else "adb_update_authorization",
        translation_placeholders={"panel": "Jenna"},
        data={"entry_id": entry.entry_id, "key": issue_id},
    )
    pin = PinnedPanelTarget(
        original=PanelAddress(host="panel.local", port=8888),
        pinned=PanelAddress(host="192.168.1.23", port=8888),
    )
    panel = AskingPanel()
    admin = await hass_client()
    async with AsyncExitStack() as stack:
        for target, replacement in {
            (
                "custom_components.panel_assistant.install_network."
                "async_pin_install_target"
            ): AsyncMock(return_value=pin),
            (
                "custom_components.panel_assistant.config_flow."
                "async_revalidate_install_target"
            ): AsyncMock(return_value=pin),
            (
                "custom_components.panel_assistant.config_flow."
                "HaPaneldClient.async_get_health"
            ): AsyncMock(return_value=health),
            (
                "custom_components.panel_assistant.config_flow.async_get_adb_signer"
            ): AsyncMock(return_value=object()),
            (
                "custom_components.panel_assistant.config_flow.async_probe_install_target"
            ): panel.probe,
        }.items():
            stack.enter_context(patch(target, replacement))
        reload = stack.enter_context(
            patch.object(
                hass.config_entries, "async_reload", AsyncMock(return_value=True)
            )
        )
        install = stack.enter_context(
            patch(
                "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
                AsyncMock(),
            )
        )
        if route == "options":
            opened = await hass.config_entries.options.async_init(entry.entry_id)
            flow_id = opened["flow_id"]

            async def configure(data: Any = None) -> Any:
                return await hass.config_entries.options.async_configure(flow_id, data)

            consent = await configure({"next_step_id": "authorize_adb"})
        else:
            response = await admin.post(
                "/api/repairs/issues/fix",
                json={"handler": DOMAIN, "issue_id": issue_id},
            )
            opened = await response.json()
            flow_id = opened["flow_id"]

            async def configure(data: Any = None) -> Any:
                response = await admin.post(
                    f"/api/repairs/issues/fix/{flow_id}", json=data
                )
                assert response.status == 200
                return await response.json()

            consent = (
                await configure({"next_step_id": "authorize_adb"})
                if route == "failure_repair"
                else opened
            )
        assert consent["type"] == "form"
        assert panel.offers == []
        yield SimpleNamespace(
            route=route,
            panel=panel,
            configure=configure,
            reload=reload,
            install=install,
            entry=entry,
            issue_id=issue_id,
            flow_id=flow_id,
        )


@pytest.mark.parametrize("outcome", ["remembered", "one_time", "timeout"])
async def test_existing_panel_wait_ends_without_counting_forwarded_input(
    hass: HomeAssistant, caller: Any, outcome: str
) -> None:
    """One Allow finishes, or presents retry; input cannot retry/install/clear."""
    caller.panel.remembers = outcome != "one_time"
    # Queueing the tap also makes this test fail promptly on the old blocking form.
    caller.panel.taps.put_nowait(outcome != "timeout")
    waiting = await caller.configure({})
    assert waiting["type"] == "progress"
    await hass.async_block_till_done()
    result = await caller.configure({"next_step_id": "retry"})
    assert len(caller.panel.offers) == 1
    assert caller.panel.offers[0] >= 60
    caller.install.assert_not_awaited()
    if outcome == "remembered":
        assert result["type"] == (
            "menu" if caller.route == "failure_repair" else "create_entry"
        )
        caller.reload.assert_awaited_once_with(caller.entry.entry_id)
        if caller.route == "failure_repair":
            assert (
                ir.async_get(hass).async_get_issue(DOMAIN, caller.issue_id) is not None
            )
            assert "retry" in result["menu_options"]
        assert caller.entry.options == {}
    else:
        assert result["type"] == "form"
        assert result["errors"] == {"base": "adb_still_unauthorized"}
        caller.reload.assert_not_awaited()
        assert ir.async_get(hass).async_get_issue(DOMAIN, caller.issue_id) is not None
        caller.panel.remembers = True
        caller.panel.taps.put_nowait(True)
        retrying = await caller.configure({})
        assert retrying["type"] == "progress"
        await hass.async_block_till_done()
        done = await caller.configure({})
        assert done["type"] == (
            "menu" if caller.route == "failure_repair" else "create_entry"
        )
        assert len(caller.panel.offers) == 2


async def test_existing_panel_known_key_does_not_offer_again(
    hass: HomeAssistant, caller: Any
) -> None:
    caller.panel.trusted = True
    waiting = await caller.configure({})
    assert waiting["type"] == "progress"
    await hass.async_block_till_done()
    done = await caller.configure({})
    assert done["type"] == (
        "menu" if caller.route == "failure_repair" else "create_entry"
    )
    assert caller.panel.offers == []
    caller.reload.assert_awaited_once_with(caller.entry.entry_id)
    caller.install.assert_not_awaited()


async def test_existing_panel_closing_the_dialog_cancels_the_offer(
    hass: HomeAssistant, caller: Any
) -> None:
    waiting = await asyncio.wait_for(caller.configure({}), 1)
    assert waiting["type"] == "progress"
    await asyncio.sleep(0)
    if caller.route == "options":
        hass.config_entries.options.async_abort(caller.flow_id)
    else:
        from homeassistant.components.repairs import repairs_flow_manager

        manager = repairs_flow_manager(hass)
        assert manager is not None
        manager.async_abort(caller.flow_id)
    await hass.async_block_till_done()
    assert caller.panel.cancelled
    caller.reload.assert_not_awaited()
    caller.install.assert_not_awaited()

"""Owners see the panel cause and a usable next step through the real Repair."""

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.update import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.const import DOMAIN, update_unique_id
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_refresh_update_failure_name,
    async_restore_update_failure,
)

from .test_update_finished_proofs import (
    _native,
    _press_install,
    _session,
    added,  # noqa: F401 - fixture must resolve when its module is also collected
)
from .test_webview import observation

pytest_plugins = ["tests.test_voice", "tests.test_update_finished_proofs"]


async def _retry(
    hass: HomeAssistant, client: Any, menu: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}", json={"next_step_id": "retry"}
    )
    result = await response.json()
    for _ in range(3):
        if result["type"] not in {"progress", "progress_done"}:
            break
        await hass.async_block_till_done()
        response = await client.post(
            f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
        )
        result = await response.json()
    return result


@pytest.mark.parametrize(
    "state,reason,route,advice,evidence_mode",
    [
        (*case, "normal")
        for case in [
            ("deferred", "install_unavailable", "eligible", "no permitted route"),
            ("deferred", "interrupted", "eligible", "connection and available storage"),
            (
                "deferred",
                "staging_failed",
                "eligible",
                "connection and available storage",
            ),
            (
                "deferred",
                "operation_failed",
                "eligible",
                "connection and available storage",
            ),
            ("rejected", "install_refused", "eligible", "Android refused"),
            ("rejected", "artifact_mismatch", "eligible", "did not match"),
            ("submitted_unknown", "reply_lost", "eligible", "could not be confirmed"),
            (
                "submitted_unknown",
                "readback_unavailable",
                "eligible",
                "could not be confirmed",
            ),
        ]
    ]
    + [
        (
            "deferred",
            "operation_failed",
            "eligible",
            "status could not be read",
            "unavailable",
        ),
        (
            "deferred",
            "operation_failed",
            "eligible",
            "now reports that WebView is current",
            "current",
        ),
    ],
)
async def test_terminal_webview_repair_explains_the_next_action_and_keeps_retry_cause(
    hass: HomeAssistant,
    hass_client: Any,
    added: list[Any],  # noqa: F811 - request the imported pytest fixture
    state: str,
    reason: str,
    route: str,
    advice: str,
    evidence_mode: str,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entity, entry, issue_id = _native(hass)
    assert await async_setup_component(hass, "update", {})
    entity._attr_unique_id = update_unique_id(entry.entry_id)
    entity.async_write_ha_state = Entity.async_write_ha_state.__get__(entity)
    component = hass.data[DATA_COMPONENT]
    platform = EntityPlatform(
        hass=hass,
        logger=component.logger,
        domain="update",
        platform_name=DOMAIN,
        platform=None,
        scan_interval=component.scan_interval,
        entity_namespace=None,
    )
    component._platforms[(DOMAIN, None, entry.entry_id)] = platform
    platform.config_entry = entry
    await platform.async_add_entities([entity])
    added.append(entity)
    entity_id = entity.entity_id
    assert entity_id is not None
    terminal = observation(state, eligible=route == "eligible")
    terminal["reason"] = route
    terminal["transaction"]["reason"] = reason
    operations: list[str] = []

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        operations.append(operation)
        if len(operations) == 3:
            if evidence_mode == "unavailable":
                raise TimeoutError
            if evidence_mode == "current":
                return observation(eligible=False)
        return terminal

    async def open_repair(client: Any) -> dict[str, Any]:
        response = await client.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        assert response.status == 200
        return await response.json()

    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as native,
        patch(
            "custom_components.panel_assistant.repairs_failure.async_get_sessions"
        ) as evidence,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
        patch(
            "custom_components.panel_assistant.repairs_failure.async_observe_management",
            manage,
        ),
    ):
        native.return_value.get.return_value = _session(entry)
        evidence.return_value.get.return_value = _session(entry)
        with pytest.raises(HomeAssistantError):
            await _press_install(hass, entity_id, entry, manage)
        entity.async_write_ha_state()
        client = await hass_client()
        menu = await open_repair(client)
        assert advice in menu["description_placeholders"]["webview_guidance"]
        assert (
            "/go/update-webview?"
            in menu["description_placeholders"]["webview_guide_url"]
        )
        assert operations.count("webview.install") == (1 if route == "eligible" else 0)

        # The next failure differs: show its fresh cause rather than the first one.
        terminal["transaction"]["reason"] = "install_refused"
        terminal["transaction"]["state"] = "rejected"
        result = await _retry(hass, client, menu)
        assert result["step_id"] == "retry_error"
        assert (
            "Android refused" if route == "eligible" else "no permitted route"
        ) in result["description_placeholders"]["webview_guidance"]
        saved = (await async_failure_events(hass, issue_id))[-1]
        assert saved["kind"] == "retry_hold"
        assert saved["webview"]["transaction_reason"] == "install_refused"
        assert operations.count("webview.install") == (2 if route == "eligible" else 0)
        entity._delivery.async_deliver_build.assert_awaited_once()

        # Registry loss/restart and a panel-name refresh must retain WebView wording.
        async_refresh_update_failure_name(hass, entry.entry_id)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).translation_key == (
            "installer_failure_update_webview"
        )
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        await async_restore_update_failure(hass, entry.entry_id)
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None
        assert issue.translation_key == "installer_failure_update_webview"
        reopened = await open_repair(client)
        assert (
            result["description_placeholders"]["webview_guidance"]
            == reopened["description_placeholders"]["webview_guidance"]
        )
        response = await client.post(
            f"/api/repairs/issues/fix/{reopened['flow_id']}",
            json={"next_step_id": "support_report"},
        )
        report = (await response.json())["description_placeholders"]["report"]
        assert "events[2].webview.transaction_reason: install_refused" in report

        if reason == "install_unavailable":
            # A safety hold can replace the issue under a still-open WebView
            # dialog; it still needs every placeholder and must not install.
            open_menu = await open_repair(client)
            er.async_get(hass).async_remove(entity_id)
            before_hold = list(operations)
            hold = await _retry(hass, client, open_menu)
            assert hold["errors"]["base"] == "retry_update_unavailable"
            assert (
                "status could not be read"
                in hold["description_placeholders"]["webview_guidance"]
            )
            assert operations == before_hold

"""Provider work belongs to an owner-started app bundle and its one retry Repair."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    update_unique_id,
)
from custom_components.panel_assistant.repairs_failure import (
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.repairs_webview import (
    async_reconcile_webview_issue,
)
from custom_components.panel_assistant.status import parse_status_response

from .test_permission_repairs import _panel

pytest_plugins = ["tests.test_voice"]


def observation(state: str | None = None, *, eligible: bool = True) -> dict[str, Any]:
    """The panel's bounded additive native snapshot."""
    return {
        "schema": 1,
        "supported": True,
        "eligible": eligible,
        "reason": "current" if not eligible else None,
        "candidate": {"sha256": "c" * 64},
        "transaction": {"state": state, "request_id": "prior", "reason": None}
        if state
        else None,
    }


def status(value: dict[str, Any]) -> Any:
    return parse_status_response(
        json.dumps({"warnings": [], "capabilities": [], "webview": value})
    )


@pytest.mark.parametrize("provider_current", [False, True])
@pytest.mark.parametrize("receipt", ["submitted_unknown", "downloading"])
async def test_explicit_retry_recovers_uncertain_receipt(
    hass: HomeAssistant,
    hass_client: Any,
    receipt: str | None,
    provider_current: bool,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    entity = er.async_get(hass).async_get_or_create(
        Platform.UPDATE,
        DOMAIN,
        update_unique_id(entry.entry_id),
        config_entry=entry,
        suggested_object_id="panel_app",
    )
    hass.states.async_set(
        entity.entity_id,
        "off",
        {
            "installed_version": "0.9.10 build 102",
            "latest_version": "0.9.10 build 102",
        },
    )
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10 build 102",
        RuntimeError("provider interrupted"),
        webview_pending=True,
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    operations = []
    request_ids = []
    session = SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )

    reads = 0

    async def manage(_hass: Any, _session: Any, op: str, **fields: Any) -> Any:
        nonlocal reads
        operations.append(op)
        if op == "webview.observe":
            reads += 1
            if receipt == "downloading" and reads > 1:
                return observation("installed", eligible=False)
            return observation(receipt, eligible=not provider_current)
        request_ids.append(fields["args"]["request_id"])
        assert fields["args"]["request_id"] != "prior"
        assert fields["args"]["artifact_sha256"] == "c" * 64
        return observation("installed", eligible=False)

    service = AsyncMock()
    hass.services.async_register("update", "install", service)
    client = await hass_client()
    with (
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(observation(receipt))),
        ),
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        sessions.return_value.get.return_value = session
        response = await client.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        flow = await response.json()
        assert flow["type"] == "menu" and operations == []
        response = await client.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={"next_step_id": "retry"}
        )
        done = await response.json()
        if done["type"] == "progress":
            await hass.async_block_till_done()
            response = await client.post(
                f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
            )
            done = await response.json()
        if done["type"] == "progress_done":
            response = await client.post(
                f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
            )
            done = await response.json()
        service.assert_not_awaited()
    assert operations == (
        ["webview.observe", "webview.observe"]
        if receipt == "downloading"
        else ["webview.observe"]
        if provider_current
        else ["webview.observe", "webview.install"]
    )
    assert len(request_ids) == (
        0 if provider_current or receipt == "downloading" else 1
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert done["type"] == "create_entry"
    if provider_current:
        for _ in range(2):
            await async_reconcile_webview_issue(
                hass, entry, status(observation("submitted_unknown", eligible=False))
            )
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None

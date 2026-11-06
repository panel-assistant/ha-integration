"""Coordinator admission of current and malformed WebView observations."""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.status import parse_status_response

from .test_permission_repairs import HEALTH, _panel
from .test_webview import observation


async def _refresh(
    hass: HomeAssistant, document: dict[str, Any], *, pending: bool = False
) -> tuple[Any, str]:
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    if pending:
        await async_record_update_failure(
            hass,
            entry.entry_id,
            "Panel",
            HEALTH.version,
            RuntimeError("WebView update unfinished"),
            webview_pending=True,
        )

    async def read_status() -> Any:
        # Parse the wire document when the coordinator actually requests it.
        return parse_status_response(
            json.dumps({"warnings": [], "capabilities": [], "webview": document})
        )

    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client, "async_get_status", AsyncMock(side_effect=read_status)
        ) as get_status,
        patch.object(
            coordinator.client,
            "async_get_version_code",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch.object(
            coordinator.client, "async_install_webview", AsyncMock()
        ) as install,
        patch(
            "custom_components.panel_assistant.repairs_webview.async_manage",
            AsyncMock(),
        ) as manage,
    ):
        await coordinator.async_refresh()
    get_status.assert_awaited_once()
    install.assert_not_awaited()
    manage.assert_not_awaited()
    return coordinator.data, issue_id


@pytest.mark.parametrize("state", ["deferred", "rejected"])
@pytest.mark.parametrize("pending", [False, True])
async def test_current_webview_ignores_old_attention_receipt(
    hass: HomeAssistant, state: str, pending: bool
) -> None:
    """A current candidate ends an old receipt without reopening its Repair."""
    snapshot, issue_id = await _refresh(
        hass, observation(state, eligible=False), pending=pending
    )
    assert snapshot.status_error is None
    assert snapshot.status is not None
    assert snapshot.status.webview.reason == "current"
    assert snapshot.status.webview.transaction_state == state
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_unknown_transaction_state_invalidates_poll_without_repair(
    hass: HomeAssistant,
) -> None:
    """An unrecognized wire state fails status admission during the refresh."""
    snapshot, issue_id = await _refresh(hass, observation("bogus"))
    assert snapshot.status_error == "invalid_response"
    assert snapshot.status is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []

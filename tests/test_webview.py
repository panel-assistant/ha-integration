"""Provider updates remain optional at install and explicit after pairing."""

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_webview import (
    async_reconcile_webview_issue,
)
from custom_components.panel_assistant.status import parse_status_response
from custom_components.panel_assistant.transport_binding import async_unbind_user
from custom_components.panel_assistant.webview import async_provision_webview

from .test_permission_repairs import HEALTH, _panel

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


@pytest.mark.parametrize(
    "state", ["installed", "deferred", "rejected", "submitted_unknown"]
)
async def test_resumed_installer_never_replays_a_provider_transaction(
    state: str,
) -> None:
    client = SimpleNamespace(
        async_get_webview=AsyncMock(return_value=observation(state)),
        async_install_webview=AsyncMock(),
    )
    await async_provision_webview(client, "same-install")
    client.async_install_webview.assert_not_awaited()


async def test_a_rootless_panel_gets_a_guide_repair_after_pairing_without_a_request(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    unavailable = observation(eligible=False)
    unavailable["reason"] = "no_install_route"
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(
                return_value=parse_status_response(
                    json.dumps(
                        {
                            "warnings": [],
                            "capabilities": [],
                            "webview": unavailable,
                        }
                    )
                )
            ),
        ),
    ):
        await coordinator.async_refresh()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"panel_webview_{entry.entry_id}")
        is not None
    )


async def test_polling_only_raises_repair_after_binding_and_never_installs(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)
    status = parse_status_response(
        json.dumps(
            {
                "warnings": [],
                "capabilities": [],
                "webview": observation("deferred"),
            }
        )
    )
    issue_id = f"panel_webview_{entry.entry_id}"
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client, "async_get_status", AsyncMock(return_value=status)
        ),
        patch(
            "custom_components.panel_assistant.repairs_webview.async_manage",
            AsyncMock(),
        ) as manage,
    ):
        await coordinator.async_refresh()
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
        )
        await coordinator.async_refresh()
        issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert issue is not None and issue.is_persistent and issue.is_fixable
        await coordinator.async_refresh()
        manage.assert_not_awaited()
    # An owner may resolve a safely failed attempt outside Panel Assistant.
    # Current/newer provider evidence clears it; an ambiguous submission does not.
    for state, retained in [("submitted_unknown", True), ("deferred", False)]:
        current = parse_status_response(
            json.dumps(
                {
                    "warnings": [],
                    "capabilities": [],
                    "webview": observation(state, eligible=False),
                }
            )
        )
        with (
            patch.object(
                coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
            ),
            patch.object(
                coordinator.client, "async_get_status", AsyncMock(return_value=current)
            ),
        ):
            await coordinator.async_refresh()
        assert (
            ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        ) == retained


@pytest.mark.parametrize("retire", [None, "remove", "unbind", "identity"])
async def test_repair_one_press_has_progress_then_observation_only(
    hass: HomeAssistant,
    hass_client: Any,
    retire: str | None,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    entry, _ = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    async_reconcile_webview_issue(
        hass,
        entry,
        parse_status_response(
            json.dumps(
                {
                    "warnings": [],
                    "capabilities": [],
                    "webview": observation(),
                }
            )
        ),
    )
    admin = await hass_client()
    session = SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )
    operations = []
    current_session = [session]
    read_started, release_read = asyncio.Event(), asyncio.Event()
    reconnecting = asyncio.Event()

    async def manage(
        _hass: Any, _session: Any, op: str, **fields: Any
    ) -> dict[str, Any]:
        operations.append(op)
        if len(operations) == 1:
            return observation()
        if op == "webview.install":
            assert fields["artifact_sha256"] == "c" * 64
            return observation("installing")
        if len(operations) == 3:
            # Updating the provider can end the native process/session. The
            # accepted operation remains panel-owned while it reconnects.
            current_session[0] = None
            reconnecting.set()
            raise CannotConnectError
        read_started.set()
        await release_read.wait()
        assert _session is current_session[0]
        return observation("installed", eligible=False)

    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        sessions.return_value.get.side_effect = lambda _: current_session[0]
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={"handler": DOMAIN, "issue_id": f"panel_webview_{entry.entry_id}"},
        )
        flow = await response.json()
        assert flow["type"] == "form" and operations == []
        response = await admin.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
        )
        progress = await response.json()
        assert progress["type"] == "progress"
        assert progress["step_id"] == "webview_progress"
        with suppress(TimeoutError):
            await asyncio.wait_for(reconnecting.wait(), 3)
        assert reconnecting.is_set(), (
            "an admitted install must keep observing through provider rebind"
        )
        if retire == "remove":
            await hass.config_entries.async_remove(entry.entry_id)
        elif retire == "unbind":
            async_unbind_user(hass, entry)
            assert (
                ir.async_get(hass).async_get_issue(
                    DOMAIN, f"panel_webview_{entry.entry_id}"
                )
                is None
            )
        elif retire == "identity":
            current_session[0] = SimpleNamespace(
                did="b" * 64, user_id="owner", capabilities={"management"}
            )
        if retire is None:
            current_session[0] = SimpleNamespace(
                did=entry.unique_id, user_id="owner", capabilities={"management"}
            )
            await read_started.wait()
        release_read.set()
        finished = False
        with suppress(TimeoutError):
            await asyncio.wait_for(hass.async_block_till_done(), 3)
            finished = True
        assert finished, "definitive identity retirement must end progress promptly"
        # Core's progress continuation, not another install form submission.
        response = await admin.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
        )
        result = await response.json()
        assert operations == [
            "webview.observe",
            "webview.install",
            "webview.observe",
        ] + (["webview.observe"] if retire is None else [])
        if retire == "remove":
            assert result["type"] == "abort" and result["reason"] == "entry_removed"
        elif retire in ("unbind", "identity"):
            assert result["type"] == "form" and result["errors"] == {
                "base": "webview_unavailable"
            }
        else:
            assert result["type"] == "create_entry"

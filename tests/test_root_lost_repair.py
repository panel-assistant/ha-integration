"""Lost root becomes an actionable Repair without ever rebooting a locked panel."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import InvalidResponseError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)
from custom_components.panel_assistant.status import parse_status_response
from tests.test_repair_links import assert_repair_links

from .test_management import HEALTH_LINE, MANAGED, Panel, _http_blocked
from .test_native import HEALTH as NATIVE_HEALTH
from .test_native import _setup
from .test_permission_repairs import HEALTH, _panel
from .test_transport import HELLO, _receive
from .test_transport_commands import _connect

pytest_plugins = ["tests.test_voice"]


def _status(**fields: Any) -> Any:
    return parse_status_response(
        json.dumps({"warnings": [], "capabilities": [], **fields})
    )


def _issue(hass: HomeAssistant, entry: Any) -> Any:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"panel_root_lost_{entry.entry_id}"
    )


@pytest.mark.parametrize("field", ["root_lost_since_boot", "screen_lock_secure"])
@pytest.mark.parametrize("value", [0, 1, "false", [], {}])
def test_restart_safety_observations_refuse_non_boolean_values(
    field: str, value: Any
) -> None:
    with pytest.raises(InvalidResponseError):
        _status(**{field: value})


async def test_poll_raises_one_root_warning_and_clears_only_on_explicit_recovery(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)
    read = AsyncMock(return_value=_status())
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(coordinator.client, "async_get_status", read),
    ):
        # Old and never-rooted apps remain quiet.
        for fields in ({}, {"root_lost_since_boot": False}):
            read.return_value = _status(**fields)
            await coordinator.async_refresh()
            assert _issue(hass, entry) is None
        read.return_value = _status(root_lost_since_boot=True)
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        issue = _issue(hass, entry)
        assert issue is not None
        assert issue.is_fixable and issue.severity is ir.IssueSeverity.WARNING
        assert_repair_links(issue)
        assert issue.translation_placeholders["panel"] == "Kitchen display"
        assert sum(domain == DOMAIN for domain, _ in ir.async_get(hass).issues) == 1
        # An old app or unreadable response cannot claim root came back.
        read.return_value = _status()
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None
        read.return_value = _status(root_lost_since_boot=False)
        await coordinator.async_refresh()
        assert _issue(hass, entry) is None


async def test_session_snapshot_also_raises_the_root_repair(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry = await _setup(hass, hass_read_only_user.id)
    with _http_blocked():
        await entry.runtime_data.coordinator.async_refresh()
        assert not entry.runtime_data.coordinator.last_update_success
        ws = await hass_ws_client(hass, hass_read_only_access_token)
        await ws.send_json_auto_id({**HELLO, "capabilities": MANAGED})
        hello = await _receive(ws)
        assert hello["success"]
        panel = Panel(ws, hello)
        request = await panel.manage()
        assert request["op"] == "snapshot"
        await panel.answer(
            request,
            "applied",
            result={
                "health": HEALTH_LINE,
                "status": json.dumps(
                    {"warnings": [], "capabilities": [], "root_lost_since_boot": True}
                ),
            },
        )
        await hass.async_block_till_done()
    assert _issue(hass, entry) is not None


@pytest.mark.parametrize(
    ("initial", "after", "outcome"),
    [
        (False, {"root_lost_since_boot": True, "screen_lock_secure": False}, "reboot"),
        (
            False,
            {"root_lost_since_boot": True, "screen_lock_secure": True},
            "restart_unavailable",
        ),
        (False, {"root_lost_since_boot": True}, "restart_unavailable"),
        (
            False,
            {"root_lost_since_boot": False, "screen_lock_secure": False},
            "recovered",
        ),
        (False, {"screen_lock_secure": False}, "root_unreadable"),
        (False, "unreadable", "root_unreadable"),
        (True, None, "restart_unavailable"),
        (None, None, "restart_unavailable"),
        (False, "busy", "panel_busy"),
        (False, "failed", "restart_failed"),
    ],
)
async def test_repair_rechecks_loss_and_lock_before_the_actual_restart_command(
    hass: HomeAssistant,
    hass_client: Any,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    initial: bool | None,
    after: Any,
    outcome: str,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await _setup(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    read = AsyncMock(
        return_value=_status(root_lost_since_boot=True, screen_lock_secure=initial)
    )
    with (
        patch.object(coordinator.client, "async_get_status", read),
        patch.object(
            coordinator.client,
            "async_get_health",
            AsyncMock(return_value=NATIVE_HEALTH),
        ),
    ):
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={"handler": DOMAIN, "issue_id": f"panel_root_lost_{entry.entry_id}"},
        )
        form = await response.json()
        if initial is not False:
            assert form["type"] == "abort"
            assert form["reason"] == outcome
        else:
            assert form["type"] == "form"
            assert form["step_id"] == "confirm_restart"
            await panel.nothing_sent()
            if after == "unreadable":
                read.side_effect = InvalidResponseError
            elif after == "busy":
                assert claim_panel_operation(hass, entry.entry_id)
            elif after != "failed":
                read.return_value = _status(**after)
            submit = asyncio.ensure_future(
                admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
            )
            if outcome in ("reboot", "restart_failed"):
                command = await panel.command()
                assert command["channel"] == "reboot"
                assert command["value"] is None
                if outcome == "restart_failed":
                    await panel.answer(command["command_id"], "failed", code="failed")
                else:
                    await panel.answer(command["command_id"], "applied")
            response = await submit
            result = await response.json()
            if outcome == "recovered":
                assert result["type"] == "create_entry"
            else:
                assert result["type"] == "abort"
                assert result["reason"] == (
                    "restart_started" if outcome == "reboot" else outcome
                )
            if outcome == "recovered":
                assert _issue(hass, entry) is None
            else:
                assert _issue(hass, entry) is not None
            if after == "busy":
                release_panel_operation(hass, entry.entry_id)
        assert claim_panel_operation(hass, entry.entry_id)
        release_panel_operation(hass, entry.entry_id)
        await panel.nothing_sent()


@pytest.mark.parametrize(
    "language",
    ["en", "cs", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "uk", "zh-Hans"],
)
async def test_root_repair_explains_restart_and_lock_safety_in_every_locale(
    hass: HomeAssistant, language: str
) -> None:
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.panel_root_lost"
    assert f"{prefix}.title" in strings
    assert "{panel}" in strings[f"{prefix}.title"]
    assert "{panel}" in strings[f"{prefix}.fix_flow.step.confirm_restart.description"]
    for reason in (
        "restart_unavailable",
        "root_unreadable",
        "panel_busy",
        "restart_failed",
        "restart_started",
        "entry_removed",
    ):
        assert strings[f"{prefix}.fix_flow.abort.{reason}"]

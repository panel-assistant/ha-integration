"""How the WebView leg of an owner-started app update ends, seen from its caller."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import ROUTE_STAGED

from .test_permission_repairs import HEALTH, _panel
from .test_update_failure_repairs import _entity
from .test_webview import observation, status

STRINGS = "custom_components/panel_assistant/strings.json"


def _native(hass: HomeAssistant) -> tuple[Any, Any, MockConfigEntry, str]:
    entity, client = _entity(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Panel",
        unique_id="a" * 64,
        data={CONF_ADDRESS: "panel.local", CONF_TRANSPORT_USER_ID: "owner"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)
    return entity, client, entry, panel_failure_issue_id(f"update:{entry.entry_id}")


def _session(entry: MockConfigEntry) -> Any:
    return SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )


async def _install(
    hass: HomeAssistant,
    entity: Any,
    entry: MockConfigEntry,
    manage: Callable[..., Any],
    *,
    session: Callable[[], Any] | None = None,
) -> None:
    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        if session is None:
            sessions.return_value.get.return_value = _session(entry)
        else:
            sessions.return_value.get.side_effect = lambda _id: session()
        await entity.async_install(None, backup=False)


async def test_failed_observation_repair_clears_once_the_panel_runs_the_target(
    hass: HomeAssistant,
) -> None:
    """Nothing was submitted, so reaching the target build ends the Repair."""
    entity, client, entry, issue_id = _native(hass)
    client.async_get_status.side_effect = ConnectionError("unreachable")

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        assert operation == "webview.observe"
        raise TimeoutError

    with pytest.raises(HomeAssistantError):
        await _install(hass, entity, entry, manage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    # The coordinator's health poll after the panel reconnected on the new build.
    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_current_code=102
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_submitted_webview_install_keeps_its_repair_through_the_poll(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("submitted_unknown")

    with pytest.raises(HomeAssistantError):
        await _install(hass, entity, entry, manage)
    assert (await async_failure_events(hass, issue_id))[-1]["webview_pending"] is True
    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_current_code=102
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize("fails", [False, True])
async def test_running_update_shows_no_failure_repair(
    hass: HomeAssistant, fails: bool
) -> None:
    entity, _client, entry, issue_id = _native(hass)
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))
    started, release = asyncio.Event(), asyncio.Event()

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await release.wait()
        if fails:
            raise RuntimeError("delivery failed")
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        return observation(eligible=False)

    task = asyncio.create_task(_install(hass, entity, entry, manage))
    await started.wait()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    release.set()
    if fails:
        with pytest.raises(RuntimeError):
            await task
    else:
        await task
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None) == fails
    if not fails:
        # Nothing is left for a later startup to turn into a Repair.
        assert await async_failure_events(hass, issue_id) == []


async def test_unpublished_webview_download_leaves_the_app_update_complete(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        document = observation("deferred")
        document["transaction"]["reason"] = "download_failed"
        return document

    await _install(hass, entity, entry, manage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []
    assert any(
        record.levelname == "WARNING" and "c" * 64 in record.getMessage()
        for record in caplog.records
    )


async def _poll(hass: HomeAssistant, document: dict[str, Any], *, pending: bool) -> str:
    """One health poll of a native panel that reports this WebView document."""
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
            RuntimeError("The app is installed; its WebView update is unfinished"),
            webview_pending=True,
        )
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(document)),
        ),
        patch.object(
            coordinator.client,
            "async_get_version_code",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        await coordinator.async_refresh()
    return issue_id


@pytest.mark.parametrize("state", [None, "installed"])
async def test_poll_seeing_the_webview_current_clears_its_pending_repair(
    hass: HomeAssistant, state: str | None
) -> None:
    issue_id = await _poll(hass, observation(state, eligible=False), pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_poll_keeps_the_repair_while_the_webview_is_still_offered(
    hass: HomeAssistant,
) -> None:
    issue_id = await _poll(hass, observation("deferred"), pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_running_webview_install_after_setup_raises_no_repair(
    hass: HomeAssistant,
) -> None:
    issue_id = await _poll(hass, observation("downloading"), pending=False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_unfinished_webview_names_the_webview_and_the_installed_app(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("rejected")

    with pytest.raises(HomeAssistantError) as raised:
        await _install(hass, entity, entry, manage)
    assert raised.value.translation_key == "update_webview_unfinished"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update_webview"
    assert "issue=installer_failure_update" in (issue.learn_more_url or "")
    strings = json.loads(
        (Path(__file__).parents[1] / STRINGS).read_text(encoding="utf-8")
    )
    message = strings["exceptions"]["update_webview_unfinished"]["message"]
    text = strings["issues"]["installer_failure_update_webview"]
    for value in (
        message,
        text["title"],
        text["fix_flow"]["step"]["init"]["description"],
    ):
        assert "WebView" in value
    assert (
        "installed" in message
        and "installed" in text["fix_flow"]["step"]["init"]["description"]
    )


async def test_recovered_webview_attempt_names_the_webview_after_a_restart(
    hass: HomeAssistant,
) -> None:
    from custom_components.panel_assistant.repairs_failure import (
        async_restore_update_failure,
    )

    issue_id = await _poll(hass, observation("deferred"), pending=False)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update_webview"
    assert "issue=installer_failure_update" in (issue.learn_more_url or "")
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    # Home Assistant starts again with only the saved report.
    await async_restore_update_failure(hass, entry_id)
    restored = registry.async_get_issue(DOMAIN, issue_id)
    assert restored is not None
    assert restored.translation_key == "installer_failure_update_webview"


async def test_plain_update_failure_keeps_the_update_wording(
    hass: HomeAssistant,
) -> None:
    _, _, entry, issue_id = _native(hass)
    await async_record_update_failure(
        hass, entry.entry_id, "Panel", "0.9.10", RuntimeError("delivery failed")
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_key == "installer_failure_update"

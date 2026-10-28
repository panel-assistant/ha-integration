"""How the WebView leg of an owner-started app update ends, seen from its caller."""

from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import ROUTE_STAGED

from .test_update_failure_repairs import _entity
from .test_webview import observation


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

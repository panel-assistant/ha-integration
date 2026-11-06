"""A panel update that finishes, or finishes later, leaves no failure Repair.

Both cases drive the owner's press of Install through Home Assistant's own
``update.install`` service on the real update entity.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.update import DATA_COMPONENT
from homeassistant.components.update import DOMAIN as UPDATE_DOMAIN
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    normalize_address,
)
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
)
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import ROUTE_STAGED

from .test_permission_repairs import HEALTH
from .test_update_failure_repairs import _entity
from .test_webview import observation, status


def _native(hass: HomeAssistant) -> tuple[Any, MockConfigEntry, str]:
    """A natively paired panel offered 0.9.10 build 102 over its session.

    The minimum of tests/test_webview_update_outcomes.py's setup, copied so the
    file also runs on trees that predate that module.
    """
    entity, _client = _entity(hass)
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
    return entity, entry, panel_failure_issue_id(f"update:{entry.entry_id}")


def _session(entry: MockConfigEntry) -> Any:
    return SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )


@pytest.fixture
async def added() -> AsyncIterator[list[Any]]:
    """Entities added to Home Assistant, removed again so no poll timer lingers."""
    entities: list[Any] = []
    yield entities
    for entity in entities:
        await entity.async_remove()


async def _with_service(hass: HomeAssistant, entity: Any, added: list[Any]) -> str:
    """Register the real update entity with Home Assistant's update component."""
    assert await async_setup_component(hass, UPDATE_DOMAIN, {})
    await hass.data[DATA_COMPONENT].async_add_entities([entity])
    added.append(entity)
    assert entity.entity_id is not None
    return entity.entity_id


async def _press_install(
    hass: HomeAssistant, entity_id: str, entry: Any, manage: Any
) -> None:
    """The owner presses Install on the panel's update entity."""
    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        sessions.return_value.get.return_value = _session(entry)
        await hass.services.async_call(
            UPDATE_DOMAIN,
            "install",
            {"entity_id": entity_id, "backup": False},
            blocking=True,
        )


async def test_running_update_shows_no_repair_and_finishes_clean(
    hass: HomeAssistant, added: list[Any]
) -> None:
    """The 2026-10-29 incident: a long delivery is not a failure."""
    entity, entry, issue_id = _native(hass)
    entity_id = await _with_service(hass, entity, added)
    started, release = asyncio.Event(), asyncio.Event()

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await release.wait()
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)
    sent: list[str] = []

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        sent.append(operation)
        # The panel's WebView is already current: nothing to install.
        return observation(eligible=False)

    task = asyncio.create_task(_press_install(hass, entity_id, entry, manage))
    await started.wait()
    # The unfinished-bundle record is saved before the app restarts ...
    saved = await async_failure_events(hass, issue_id)
    assert saved and saved[-1]["webview_pending"] is True
    # ... but the running update is not shown to the owner as a failure.
    # Home Assistant refuses a second Install while this is not False.
    assert entity.in_progress is not False
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    release.set()
    await task
    assert sent == ["webview.observe"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_webview_finishing_after_the_wait_clears_its_repair_on_the_poll(
    hass: HomeAssistant, added: list[Any]
) -> None:
    """The WebView install outlived the update's wait, then finished on its own."""
    entity, entry, issue_id = _native(hass)
    entity_id = await _with_service(hass, entity, added)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        # Submitted, and still unresolved when the update stopped waiting.
        return observation("submitted_unknown")

    with pytest.raises(HomeAssistantError):
        await _press_install(hass, entity_id, entry, manage)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert (await async_failure_events(hass, issue_id))[-1]["webview_pending"] is True

    # A later ordinary health poll: the target build, WebView current, nothing open.
    poller = HaPaneldDataUpdateCoordinator(
        hass, HaPaneldClient(object(), normalize_address("panel.local")), entry.entry_id
    )
    health = replace(HEALTH, version="0.9.10", build="102", version_code=102)
    with (
        patch.object(poller.client, "async_get_health", AsyncMock(return_value=health)),
        patch.object(
            poller.client,
            "async_get_status",
            AsyncMock(return_value=status(observation(eligible=False))),
        ),
        patch.object(
            poller.client,
            "async_get_version_code",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        await poller.async_refresh()
    assert poller.last_update_success
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []

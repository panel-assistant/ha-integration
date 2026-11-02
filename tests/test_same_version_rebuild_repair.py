"""A verified host rebuild clears the update Repair before any later poll."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import ROUTE_ADB, ROUTE_STAGED

from .test_update_failure_repairs import _entity


@pytest.mark.parametrize(
    "newer_failure", [False, True], ids=["completed", "newer-failure"]
)
@pytest.mark.parametrize("route", [ROUTE_STAGED, ROUTE_ADB])
@pytest.mark.parametrize("paired", [False, True], ids=["unpaired", "paired"])
@pytest.mark.parametrize("same_version", [True, False], ids=["rebuild", "version-bump"])
async def test_verified_host_update_clears_repair_before_poll(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    paired: bool,
    same_version: bool,
    newer_failure: bool,
) -> None:
    """An owner-started successful delivery clears both the issue and its events."""
    entity, _client = _entity(hass)
    current = entity.coordinator.data
    entity.coordinator.data = replace(
        current,
        health=replace(
            current.health,
            version="0.9.10" if same_version else "0.9.9",
            version_code=101,
        ),
    )
    entity._installed_code = 101
    entity._code_key = (entity.coordinator.data.health.version, "101")
    data = {CONF_ADDRESS: "panel.local"}
    if paired:
        data[CONF_TRANSPORT_USER_ID] = "owner"
    entry = MockConfigEntry(domain=DOMAIN, title="Panel", data=data)
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(route, object(), object()))
    monkeypatch.setattr(update, "async_update_webview", AsyncMock(return_value=None))
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    target = "0.9.10 build 102" if same_version else "0.9.10"
    assert entity.latest_version == target
    await async_record_update_failure(
        hass, entry.entry_id, entry.title, target, HomeAssistantError("Install failed")
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    delivered: list[tuple[str, int]] = []

    async def delivery(_hass: Any, artifact: Any, *_args: Any) -> None:
        delivered.append((artifact.version, artifact.descriptor.version_code))
        snapshot = entity.coordinator.data
        entity.coordinator.data = replace(
            snapshot,
            health=replace(snapshot.health, version="0.9.10", version_code=102),
        )
        if newer_failure:
            # A newer failed target recorded during delivery must survive this
            # older build's verified completion.
            await async_record_update_failure(
                hass,
                entry.entry_id,
                entry.title,
                "0.9.10 build 103",
                HomeAssistantError("Newer install failed"),
            )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)
    entity._delivery.async_deliver_adb = AsyncMock(side_effect=delivery)
    await entity.async_install(None, backup=False)

    assert delivered == [("0.9.10", 102)]
    entity.coordinator.async_request_refresh.assert_not_awaited()
    events = await async_failure_events(hass, issue_id)
    if newer_failure:
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        assert events[-1]["target_version"] == "0.9.10 build 103"
    else:
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        assert events == []

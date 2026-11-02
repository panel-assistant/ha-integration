"""The bundled update follows active WebView work and bounds lost observations."""

import asyncio
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
from custom_components.panel_assistant.repairs_failure import panel_failure_issue_id
from custom_components.panel_assistant.update_route import ROUTE_STAGED

from .test_update_failure_repairs import _entity
from .test_webview import observation


@pytest.mark.parametrize("contact", ["healthy", "lost", "recovered"])
async def test_update_waits_for_active_webview_but_bounds_lost_contact(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, contact: str
) -> None:
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
    entity._async_install_route = AsyncMock(
        return_value=(ROUTE_STAGED, object(), object())
    )

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)
    operations: list[str] = []
    elapsed = 0.0
    loop = asyncio.get_running_loop()
    real_time, real_sleep = loop.time, asyncio.sleep

    async def advance(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds
        assert elapsed < 300, "Lost contact never reached its reconnect bound"
        # Advance the actual event-loop deadline clock, then let its scheduled
        # timeout callbacks run. No wall-clock wait or replaced timeout logic.
        await real_sleep(0)

    async def manage(_hass: Any, _session: Any, op: str, **fields: Any) -> Any:
        operations.append(op)
        if op == "webview.install":
            assert fields["artifact_sha256"] == "c" * 64
            return observation("downloading")
        if len(operations) == 1:
            return observation()
        if contact == "lost" or (contact == "recovered" and elapsed < 80):
            raise ConnectionError("temporarily disconnected")
        if elapsed < 90:
            return observation("downloading")
        if elapsed < 170:
            return observation("verified")
        if elapsed < 190:
            return observation("installing")
        return observation("installed", eligible=False)

    session = SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )
    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
        monkeypatch.context() as clock,
    ):
        sessions.return_value.get.return_value = session
        clock.setattr(loop, "time", lambda: real_time() + elapsed)
        clock.setattr(asyncio, "sleep", advance)
        if contact == "lost":
            with pytest.raises(HomeAssistantError, match="webview_deferred"):
                await entity.async_install(None, backup=False)
        else:
            await entity.async_install(None, backup=False)
    assert operations.count("webview.install") == 1
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None) == (
        contact == "lost"
    )
    assert 120 <= elapsed < 130 if contact == "lost" else elapsed >= 190

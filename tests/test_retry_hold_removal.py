"""A Retry whose panel is removed while it runs must not leave a Repair behind."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    update_failure_entry_id,
)
from tests.test_failure_repair_flow import repairs_ready  # noqa: F401
from tests.test_retry_during_running_update import (  # noqa: F401
    ISSUE_ID,
    _panel,
    platforms,
)


@pytest.mark.parametrize("removed", [True, False])
async def test_retry_hold_is_not_recorded_for_a_panel_removed_mid_retry(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],  # noqa: F811
    removed: bool,
) -> None:
    _entity, client, staging, release = await _panel(hass, monkeypatch, platforms)

    async def stage(*_args: object, **_kwargs: object) -> None:
        staging.set()
        await release.wait()
        raise HomeAssistantError("disk full")

    client.async_stage_apk = AsyncMock(side_effect=stage)
    await async_record_update_failure(
        hass, "entry-id", "Kitchen", "0.9.10", RuntimeError("download timed out")
    )

    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
    )
    flow_id = (await response.json())["flow_id"]
    response = await admin.post(
        f"/api/repairs/issues/fix/{flow_id}", json={"next_step_id": "retry"}
    )
    assert (await response.json())["type"] == "progress"
    # Try again is now inside the update entity's install.
    await asyncio.wait_for(staging.wait(), 5)
    if removed:
        await hass.config_entries.async_remove("entry-id")
        assert hass.config_entries.async_get_entry("entry-id") is None
        assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    release.set()
    result: dict[str, Any] = {"type": "progress"}
    async with asyncio.timeout(5):
        while result["type"] == "progress":
            await hass.async_block_till_done()
            response = await admin.post(f"/api/repairs/issues/fix/{flow_id}", json={})
            result = await response.json()
    await hass.async_block_till_done()

    assert client.async_stage_apk.await_count == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    events = await async_failure_events(hass, ISSUE_ID)
    if removed:
        assert issue is None
        assert events == []
    else:
        assert result["errors"] == {"base": "retry_failed"}
        assert issue is not None
        assert issue.translation_key == "installer_failure_retry_hold"
        holds = [event for event in events if event["kind"] == "retry_hold"]
        assert [hold["entry_id"] for hold in holds] == ["entry-id"]
        # The hold alone names its panel, so removing the panel later clears it.
        assert update_failure_entry_id(ISSUE_ID, holds) == "entry-id"

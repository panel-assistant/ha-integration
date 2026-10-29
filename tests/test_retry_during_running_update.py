"""Try again on an update Repair explains a running update instead of failing."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.update import DATA_COMPONENT
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant import update_progress, update_staging
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import PanelHealth, StagedApk
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)
from custom_components.panel_assistant.release import _RELEASE_SIGNER_CERTIFICATE_SHA256
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.status import PanelStatus
from tests.test_failure_repair_flow import repairs_ready  # noqa: F401
from tests.test_update_failure_repairs import _entity

ISSUE_ID = panel_failure_issue_id("update:entry-id")


@pytest.fixture
async def platforms(hass: HomeAssistant) -> Any:
    """Remove the attached entities so no coordinator poll outlives a test."""
    attached: list[EntityPlatform] = []
    yield attached
    for platform in attached:
        await platform.async_reset()


async def _panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],
    *,
    transport: bool = False,
) -> tuple[Any, Any, asyncio.Event, asyncio.Event]:
    """Attach the real update entity to Home Assistant's update component."""
    data = {CONF_ADDRESS: "kitchen.local"}
    if transport:
        data[CONF_TRANSPORT_USER_ID] = "transport-user"
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", title="Kitchen", data=data
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        panel_update, "async_update_webview", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(update_staging, "async_store_panel_backup", AsyncMock())
    monkeypatch.setattr(update_progress, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 0)
    monkeypatch.setattr(update_progress, "_RESTART_HEALTH_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(update_progress, "_UPDATE_RECHECK_SECONDS", 0.001)
    entity, client = _entity(hass, feed=True)
    del entity.async_write_ha_state
    staging = asyncio.Event()
    release = asyncio.Event()

    async def stage(*_args: object, **_kwargs: object) -> StagedApk:
        staging.set()
        await release.wait()
        return StagedApk(
            token="token-1",
            package=PACKAGE_ID,
            version="0.9.10",
            signer=_RELEASE_SIGNER_CERTIFICATE_SHA256,
        )

    client.async_stage_apk = AsyncMock(side_effect=stage)

    async def new_health() -> None:
        entity.coordinator.data = PanelSnapshot(
            health=PanelHealth(
                version="0.9.10",
                panel_id="alpha",
                build="102",
                config_hash="abcd",
                package=PACKAGE_ID,
                protocol=(5, 5),
                version_code=102,
            ),
            status=PanelStatus(warning_count=0, capability_count=0),
            status_error=None,
        )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=new_health)
    assert await async_setup_component(hass, "update", {})
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="update",
        platform_name=DOMAIN,
        platform=None,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    platform.config_entry = entry
    platforms.append(platform)
    await platform.async_add_entities([entity])
    assert hass.data[DATA_COMPONENT].get_entity(entity.entity_id) is entity
    await hass.async_block_till_done()
    return entity, client, staging, release


async def _start_install(
    hass: HomeAssistant, entity: Any, staging: asyncio.Event
) -> asyncio.Task[Any]:
    """Start an owner update through the update service and hold it open."""
    task = hass.async_create_task(
        hass.services.async_call(
            "update", "install", {"entity_id": entity.entity_id}, blocking=True
        )
    )
    await asyncio.wait_for(staging.wait(), 5)
    assert entity.in_progress
    return task


async def _try_again(hass_client: Any, hass: HomeAssistant) -> dict[str, Any]:
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
    )
    menu = await response.json()
    assert menu["type"] == "menu"
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}", json={"next_step_id": "retry"}
    )
    result = await response.json()
    if result["type"] == "progress":
        for _ in range(50):
            await asyncio.sleep(0.01)
            response = await admin.post(
                f"/api/repairs/issues/fix/{menu['flow_id']}", json={}
            )
            result = await response.json()
            if result["type"] != "progress":
                break
    return result


def _explains_running_update(result: dict[str, Any]) -> None:
    assert result["type"] == "form"
    assert result["step_id"] == "retry_error"
    assert result["errors"] == {"base": "retry_update_running"}


async def test_try_again_while_update_runs_explains_and_repair_closes_on_success(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],
) -> None:
    entity, client, staging, release = await _panel(hass, monkeypatch, platforms)
    install = await _start_install(hass, entity, staging)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    assert hass.states.get(entity.entity_id).attributes["in_progress"]
    before = await async_failure_events(hass, ISSUE_ID)

    _explains_running_update(await _try_again(hass_client, hass))
    _explains_running_update(await _try_again(hass_client, hass))

    assert client.async_stage_apk.await_count == 1
    assert await async_failure_events(hass, ISSUE_ID) == before

    release.set()
    await asyncio.wait_for(install, 5)
    await hass.async_block_till_done()
    client.async_commit_apk.assert_awaited_once_with("token-1")
    assert client.async_stage_apk.await_count == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_install_refused_as_already_running_explains(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],
) -> None:
    """The state read before Try again lags an install that has just begun."""
    entity, client, staging, release = await _panel(hass, monkeypatch, platforms)
    install = await _start_install(hass, entity, staging)
    stale = hass.states.get(entity.entity_id)
    hass.states.async_set(
        entity.entity_id,
        stale.state,
        {**stale.attributes, "in_progress": False, "latest_version": "0.9.11"},
    )
    before = await async_failure_events(hass, ISSUE_ID)

    _explains_running_update(await _try_again(hass_client, hass))

    assert client.async_stage_apk.await_count == 1
    assert await async_failure_events(hass, ISSUE_ID) == before
    release.set()
    await asyncio.wait_for(install, 5)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_install_refused_as_panel_busy_explains(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],
) -> None:
    """Another operation holds the panel, so the entity's install says update_busy."""
    entity, client, _staging, _release = await _panel(hass, monkeypatch, platforms)
    await async_record_update_failure(
        hass, "entry-id", "Kitchen", "0.9.10", RuntimeError("download timed out")
    )
    assert not hass.states.get(entity.entity_id).attributes["in_progress"]
    before = await async_failure_events(hass, ISSUE_ID)
    assert claim_panel_operation(hass, "entry-id")
    try:
        _explains_running_update(await _try_again(hass_client, hass))
    finally:
        release_panel_operation(hass, "entry-id")

    client.async_stage_apk.assert_not_awaited()
    assert await async_failure_events(hass, ISSUE_ID) == before
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None


async def test_webview_retry_while_panel_is_held_explains(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],
) -> None:
    """The app landed; only the WebView half is left, and the panel is held."""
    entity, client, _staging, _release = await _panel(
        hass, monkeypatch, platforms, transport=True
    )
    installed = hass.states.get(entity.entity_id).attributes["installed_version"]
    await async_record_update_failure(
        hass,
        "entry-id",
        "Kitchen",
        installed,
        RuntimeError("The combined app and WebView update is unfinished"),
        webview_pending=True,
    )
    before = await async_failure_events(hass, ISSUE_ID)
    assert claim_panel_operation(hass, "entry-id")
    try:
        _explains_running_update(await _try_again(hass_client, hass))
    finally:
        release_panel_operation(hass, "entry-id")

    client.async_stage_apk.assert_not_awaited()
    assert await async_failure_events(hass, ISSUE_ID) == before

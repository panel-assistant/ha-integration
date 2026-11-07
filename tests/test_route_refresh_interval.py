"""Background admission stays fresh without opening ADB on every health poll."""

from datetime import timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from tests.test_no_update_route_behaviour import _entry, _issue
from tests.test_update_adb_route import route as route


async def test_health_burst_reuses_route_until_timer_checks_for_lost_adb(
    route, hass, freezer
):
    entry = _entry(hass)
    entity = route.entity
    entity.entity_id = "update.panel"
    entity.platform = SimpleNamespace(platform_name="update", config_entry=None)
    entity.coordinator.update_interval = None
    entity._update_coordinator.update_interval = None
    entity._offer.feed.update_interval = None
    await entity.async_internal_added_to_hass()
    await entity.async_added_to_hass()
    try:
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entity.latest_version != entity.installed_version
        assert route.probe_target.await_count == 1
        route.probe_target.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNAUTHORIZED
        )
        for _ in range(25):
            entity._handle_coordinator_update()
            await hass.async_block_till_done(wait_background_tasks=True)
        assert route.probe_target.await_count == 1
        assert _issue(hass, entry) is None

        freezer.tick(timedelta(minutes=10, seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        assert route.probe_target.await_count > 1
        assert _issue(hass, entry) is not None
        assert entity.latest_version == entity.installed_version
    finally:
        await entity.async_remove()


async def test_reconnect_checks_route_before_interval_expires(route, hass):
    _entry(hass)
    entity = route.entity
    entity.entity_id = "update.panel"
    entity.platform = SimpleNamespace(platform_name="update", config_entry=None)
    entity.coordinator.update_interval = None
    entity._update_coordinator.update_interval = None
    entity._offer.feed.update_interval = None
    await entity.async_internal_added_to_hass()
    await entity.async_added_to_hass()
    try:
        await hass.async_block_till_done(wait_background_tasks=True)
        assert route.probe_target.await_count == 1
        entity.coordinator.reachable = False
        entity._handle_coordinator_update()
        await hass.async_block_till_done(wait_background_tasks=True)
        route.probe_target.reset_mock()
        route.probe_target.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNAUTHORIZED
        )
        entity.coordinator.reachable = True
        entity._handle_coordinator_update()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert route.probe_target.await_count > 0
        assert entity.latest_version == entity.installed_version
    finally:
        await entity.async_remove()


async def test_changed_panel_address_checks_route_before_interval_expires(route, hass):
    from custom_components.panel_assistant.client import normalize_address

    entity = route.entity
    entity._schedule_route_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entity.latest_version != entity.installed_version
    route.probe_target.reset_mock()
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    entity.coordinator.client.address = normalize_address("192.168.1.11")
    entity._handle_coordinator_update()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert route.probe_target.await_count > 0
    assert entity.latest_version == entity.installed_version


async def test_failed_route_checks_are_rate_limited(route, hass):
    route.probe_target.side_effect = RuntimeError("probe failed")
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    # One route attempt tries the selected target and its legacy fallback.
    assert route.probe_target.await_count == 2
    for _ in range(25):
        route.entity._handle_coordinator_update()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert route.probe_target.await_count == 2


async def test_reconnect_during_route_check_is_checked_when_it_finishes(
    route, hass, monkeypatch
):
    """A reconnect observed during an older check needs a fresh answer now."""
    import asyncio
    from unittest.mock import AsyncMock

    from custom_components.panel_assistant import update
    from custom_components.panel_assistant.update_route import ROUTE_ADB

    _entry(hass)
    entity = route.entity
    entity.entity_id = "update.panel"
    entity.platform = SimpleNamespace(platform_name="update", config_entry=None)
    entity.coordinator.update_interval = None
    entity._update_coordinator.update_interval = None
    entity._offer.feed.update_interval = None
    entity.coordinator.reachable = False
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = []

    async def install_route():
        attempts.append(entity.coordinator.reachable)
        if len(attempts) == 1:
            started.set()
            await release.wait()
            return None, None, None
        return ROUTE_ADB, None, None

    monkeypatch.setattr(entity, "_async_install_route", install_route)
    monkeypatch.setattr(
        update, "async_observe_update_route", AsyncMock(return_value=False)
    )
    await entity.async_internal_added_to_hass()
    await entity.async_added_to_hass()
    try:
        await started.wait()
        entity.coordinator.reachable = True
        entity._handle_coordinator_update()
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert attempts == [False, True]
        assert entity.latest_version != entity.installed_version
    finally:
        release.set()
        await entity.async_remove()
        await hass.async_block_till_done(wait_background_tasks=True)

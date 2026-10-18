"""Panel update results preserve failure reasons without guessing completion."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant import update_progress
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import PanelInstallStatus
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.update_coordinator import PanelUpdateSnapshot

from .test_feed_update import _entity as feed_entity
from .test_feed_update import _restart_into, delivery  # noqa: F401
from .test_update_failure_repairs import _entity as download_entity

FAILURES = (
    (
        "update not installed: cancelled on the panel or refused by its package "
        "verifier (verifier detail)"
    ),
    "update not confirmed on the panel in time",
    "install failed: insufficient storage (no space)",
    "install blocked on the panel (verifier policy)",
    "install failed (incompatible package)",
)


@pytest.fixture
def update_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Advance only the update wait's clock, leaving Core timers alone."""
    clock = SimpleNamespace(seconds=0.0)
    monkeypatch.setattr(update_progress, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 180)
    monkeypatch.setattr(update_progress, "_RESTART_HEALTH_GRACE_SECONDS", 60)

    async def advance(seconds: float) -> None:
        clock.seconds += seconds

    monkeypatch.setattr(
        update_progress,
        "asyncio",
        SimpleNamespace(
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.seconds),
            sleep=advance,
        ),
    )
    return clock


@pytest.mark.parametrize("reason", FAILURES)
async def test_download_terminal_failure_preserves_reason_immediately(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    update_clock: SimpleNamespace,
    reason: str,
) -> None:
    MockConfigEntry(domain=DOMAIN, entry_id="entry-id", title="Test panel").add_to_hass(
        hass
    )
    entity, client = download_entity(hass)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(
            running=False, component="ha-paneld", message=reason
        ),
        error=None,
    )
    record = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", record)

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, backup=False)

    assert caught.value.translation_key == "update_refused_by_panel"
    assert caught.value.translation_placeholders == {
        "panel": "Test panel",
        "reason": reason,
    }
    assert reason in str(record.await_args.args[4])
    assert update_clock.seconds == 0
    entity._update_coordinator.async_request_refresh.assert_awaited_once()
    client.async_start_panel_update.assert_awaited_once()
    assert entity.in_progress is False


@pytest.mark.usefixtures("delivery")
@pytest.mark.parametrize("route", ["staged", "download"])
@pytest.mark.parametrize("result", ["running", "empty", "success", "unrelated"])
async def test_nonfailure_result_keeps_verifying_the_committed_target(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    update_clock: SimpleNamespace,
    route: str,
    result: str,
) -> None:
    entity, client = (
        feed_entity(hass, package=PACKAGE_ID)
        if route == "staged"
        else download_entity(hass)
    )
    component = "APK" if route == "staged" else "ha-paneld"
    status = PanelInstallStatus(
        running=result == "running",
        component="Backup" if result == "unrelated" else component,
        message=(
            FAILURES[0]
            if result in ("running", "unrelated")
            else "OK"
            if route == "staged" and result == "success"
            else "updating Panel Assistant -> 0.9.10"
            if result == "success"
            else ""
        ),
    )
    client.async_get_panel_install_status.return_value = status
    entity._update_coordinator.data = PanelUpdateSnapshot(operation=None, error=None)
    entity._update_coordinator.async_request_refresh = AsyncMock()

    async def start(_tag: str) -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=status, error=None
        )

    client.async_start_panel_update.side_effect = start
    record = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", record)

    async def refresh() -> None:
        if update_clock.seconds < 2:
            return
        if route == "staged":
            _restart_into(entity, client, 772, package=PACKAGE_ID)
            await entity.coordinator.async_request_refresh()
        else:
            snapshot = entity.coordinator.data
            entity.coordinator.data = PanelSnapshot(
                health=replace(
                    snapshot.health, version="0.9.10", build="102", version_code=102
                ),
                status=snapshot.status,
                status_error=None,
            )
            entity.coordinator.last_update_success = True

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)
    await entity.async_install(None, backup=False)
    assert update_clock.seconds >= 2
    record.assert_not_awaited()
    assert entity.installed_version.partition(" build ")[0] == (
        "0.9.7-rc4" if route == "staged" else "0.9.10"
    )


@pytest.mark.parametrize("reason", ["", "OK", FAILURES[0]])
async def test_targetless_finished_result_stays_unknown_without_failure_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    update_clock: SimpleNamespace,
    reason: str,
) -> None:
    entity, client = download_entity(hass, recovered=True)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(
            running=False, component="ha-paneld", message=reason
        ),
        error=None,
    )
    monkeypatch.setattr(update_progress, "_TERMINAL_STATUS_GRACE_SECONDS", 0)
    record = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", record)
    await entity._run_observer(None, recovered=True)
    record.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert entity.in_progress is False

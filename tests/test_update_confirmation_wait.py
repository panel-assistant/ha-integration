"""Accepted updates wait for confirmation once, within a finite deadline."""

from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant import update_progress
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import PanelInstallStatus
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update_coordinator import PanelUpdateSnapshot

from .test_feed_update import _entity as feed_entity
from .test_feed_update import delivery  # noqa: F401


@pytest.fixture
def confirmation_clock(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> SimpleNamespace:
    """Advance only wait time after the existing delivery fixture is ready."""
    request.getfixturevalue("delivery")
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


def _waiting_update(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    clock: SimpleNamespace,
    operation: Callable[[float], PanelInstallStatus | None],
    *,
    installed_at: float | None = None,
    installed_code: int | None = None,
) -> SimpleNamespace:
    """Drive real Install with controllable operation and accepted health reads."""
    entity, client = feed_entity(hass, package=PACKAGE_ID)
    record = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", record)

    async def install_status() -> PanelInstallStatus | None:
        return operation(clock.seconds)

    client.async_get_panel_install_status.side_effect = install_status

    async def refresh_operation() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=operation(clock.seconds), error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_operation
    )

    async def refresh_health() -> None:
        # Also kills an accidentally renewable allowance without spinning forever.
        assert clock.seconds < 2000, "confirmation wait kept renewing its deadline"
        if installed_at is not None and clock.seconds >= installed_at:
            snapshot = entity.coordinator.data
            entity.coordinator.data = replace(
                snapshot,
                health=replace(
                    snapshot.health,
                    version="0.9.7-rc4",
                    build="2000",
                    version_code=(
                        installed_code if installed_code is not None else 772
                    ),
                ),
            )
            entity.coordinator.last_update_success = True

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
    return SimpleNamespace(entity=entity, client=client, record=record)


def _confirmation() -> PanelInstallStatus:
    return PanelInstallStatus(running=True, component="APK", awaiting_confirmation=True)


@pytest.mark.usefixtures("confirmation_clock")
@pytest.mark.parametrize("prompt_at,tap_at", [(0, 200), (0, 270)])
async def test_late_confirmation_completes_without_a_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    prompt_at: int,
    tap_at: int,
) -> None:
    clock = request.getfixturevalue("confirmation_clock")
    wait = _waiting_update(
        hass,
        monkeypatch,
        clock,
        lambda now: _confirmation() if now >= prompt_at else None,
        installed_at=tap_at,
    )
    await wait.entity.async_install(None, backup=False)
    assert tap_at <= clock.seconds <= tap_at + 2
    wait.record.assert_not_awaited()
    wait.client.async_commit_apk.assert_awaited_once()


@pytest.mark.usefixtures("confirmation_clock")
@pytest.mark.parametrize("interruption", [None, "missing", "not_awaiting"])
async def test_confirmation_allowance_is_fixed_at_its_first_observation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    interruption: str | None,
) -> None:
    clock = request.getfixturevalue("confirmation_clock")
    prompt_at = 10

    def operation(now: float) -> PanelInstallStatus | None:
        if now < prompt_at:
            return None
        if int(now - prompt_at) % 10 >= 4:
            if interruption == "missing":
                return None
            if interruption == "not_awaiting":
                return replace(_confirmation(), awaiting_confirmation=False)
        return _confirmation()

    wait = _waiting_update(hass, monkeypatch, clock, operation)
    with pytest.raises(HomeAssistantError) as caught:
        await wait.entity.async_install(None, backup=False)
    assert caught.value.translation_key == "update_not_complete"
    # Five minutes for the panel prompt, three to install, one to return.
    budget = prompt_at + 540
    assert budget <= clock.seconds <= budget + 2
    wait.record.assert_awaited_once()


@pytest.mark.usefixtures("confirmation_clock")
@pytest.mark.parametrize("state", ["unrelated", "finished", "not_awaiting"])
async def test_only_a_running_matching_confirmation_extends_the_wait(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    state: str,
) -> None:
    clock = request.getfixturevalue("confirmation_clock")
    operation = replace(
        _confirmation(),
        component="Backup" if state == "unrelated" else "APK",
        running=state != "finished",
        awaiting_confirmation=state != "not_awaiting",
    )
    wait = _waiting_update(hass, monkeypatch, clock, lambda _now: operation)
    with pytest.raises(HomeAssistantError) as caught:
        await wait.entity.async_install(None, backup=False)
    assert caught.value.translation_key == "update_not_complete"
    assert 240 <= clock.seconds <= 242
    wait.record.assert_awaited_once()


@pytest.mark.usefixtures("confirmation_clock")
@pytest.mark.parametrize("unproven", ["code", "home"])
async def test_extended_wait_still_requires_the_exact_build_and_home(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    unproven: str,
) -> None:
    clock = request.getfixturevalue("confirmation_clock")
    wait = _waiting_update(
        hass,
        monkeypatch,
        clock,
        lambda _now: _confirmation(),
        installed_at=270,
        installed_code=771 if unproven == "code" else 772,
    )
    if unproven == "home":
        wait.client.async_get_status.return_value = PanelStatus(
            warning_count=0,
            capability_count=0,
            home_ui={"state": "starting", "reason": "dashboard", "evidence": ""},
        )
    with pytest.raises(HomeAssistantError) as caught:
        await wait.entity.async_install(None, backup=False)
    assert caught.value.translation_key == "update_not_complete"
    assert 540 <= clock.seconds <= 542
    wait.record.assert_awaited_once()


@pytest.mark.usefixtures("confirmation_clock")
async def test_panel_confirmation_timeout_ends_the_extended_wait_immediately(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    clock = request.getfixturevalue("confirmation_clock")
    prompt_at, finished_at = 0, 300
    reason = "update not confirmed on the panel in time"

    def operation(now: float) -> PanelInstallStatus | None:
        if now < prompt_at:
            return None
        if now >= finished_at:
            return replace(_confirmation(), running=False, message=reason)
        return _confirmation()

    wait = _waiting_update(hass, monkeypatch, clock, operation)
    with pytest.raises(HomeAssistantError) as caught:
        await wait.entity.async_install(None, backup=False)
    assert caught.value.translation_key == "update_refused_by_panel"
    assert caught.value.translation_placeholders["reason"] == reason
    assert finished_at <= clock.seconds <= finished_at + 2
    assert reason in str(wait.record.await_args.args[4])
    wait.record.assert_awaited_once()

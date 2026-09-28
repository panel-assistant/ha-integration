"""Accepted update failures remain repairable until a verified update succeeds."""

from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from yarl import URL

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.app_identity import LEGACY_PACKAGE_ID
from custom_components.panel_assistant.build_feed import (
    BuildDownloadError,
    BuildFeed,
    FeedBuild,
    feed_release_artifact,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelHealth,
    PanelInstallStatus,
    StagedApk,
    UpdateBusyError,
    UpdateRejectedError,
)
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import BuildFeedCoordinator
from custom_components.panel_assistant.release import _RELEASE_SIGNER_CERTIFICATE_SHA256
from custom_components.panel_assistant.status import PanelCachedUpdate, PanelStatus
from custom_components.panel_assistant.transport import async_get_sessions
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)


@pytest.fixture
def repairs(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Observe the repair API while leaving the update entity real."""
    calls = SimpleNamespace(record=AsyncMock(), clear=AsyncMock())
    monkeypatch.setattr(
        panel_update, "async_record_update_failure", calls.record, raising=False
    )
    monkeypatch.setattr(
        panel_update, "async_clear_update_failure", calls.clear, raising=False
    )
    return calls


def _entity(
    hass: HomeAssistant,
    *,
    recovered: bool = False,
    feed: bool = False,
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    client = SimpleNamespace(
        configuration_url="http://panel.local:8888",
        async_start_panel_update=AsyncMock(),
        async_get_panel_install_status=AsyncMock(),
        async_backup_panel=AsyncMock(return_value=b"backup"),
        async_stage_apk=AsyncMock(
            return_value=StagedApk(
                token="token-1",
                package=LEGACY_PACKAGE_ID,
                version="0.9.10",
                signer=_RELEASE_SIGNER_CERTIFICATE_SHA256,
            )
        ),
        async_commit_apk=AsyncMock(),
        async_discard_apk=AsyncMock(),
        async_get_version_code=AsyncMock(return_value=("0.9.10", 102)),
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = PanelSnapshot(
        health=PanelHealth(
            version="0.9.9", panel_id="alpha", build="101", config_hash="abcd"
        ),
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            panel_assistant_update=PanelCachedUpdate("0.9.9", "0.9.10", "v0.9.10"),
        ),
        status_error=None,
    )
    health.async_request_refresh = AsyncMock()
    updates = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    updates.data = PanelUpdateSnapshot(
        operation=(
            PanelInstallStatus(running=True, component="ha-paneld")
            if recovered
            else None
        ),
        error=None,
    )
    updates.async_request_refresh = AsyncMock()
    coordinator = None
    if feed:
        build = FeedBuild(
            version_code=102,
            version_name="0.9.10",
            apk_url=URL("https://feed.example/apk"),
            apk_sha256="a" * 64,
            apk_size=3,
            commit="0" * 40,
            database_compatibility="hapaneld-db:v1:ha-paneld.db:11:14",
            min_sdk=26,
            published="2026-09-28T00:00:00Z",
            package_id=LEGACY_PACKAGE_ID,
        )
        coordinator = BuildFeedCoordinator(hass, URL("https://feed.example/feed"))
        coordinator.data = BuildFeed(channel="maintainer", builds=(build,))
        coordinator.last_update_success = True
        coordinator._verified_newest[LEGACY_PACKAGE_ID] = (build, b"apk")
    entity = HaPaneldUpdateEntity(
        "entry-id", health, updates, coordinator, title="Test panel"
    )
    entity.hass = hass
    entity.entity_id = "update.kitchen_panel"
    entity.async_write_ha_state = MagicMock()
    if feed:
        entity._installed_code = 101
        entity._code_key = ("0.9.9", "101")
    return entity, client


async def test_accepted_panel_update_failure_creates_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(running=False, component="ha-paneld"),
        error=None,
    )
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)

    with pytest.raises(HomeAssistantError, match="did not complete"):
        await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once_with("v0.9.10")
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[:4] == (
        hass,
        "entry-id",
        "Test panel",
        "0.9.10",
    )
    assert isinstance(repairs.record.await_args.args[4], HomeAssistantError)
    assert "did not complete" in str(repairs.record.await_args.args[4])
    assert repairs.record.await_args.kwargs["artifact"] is None
    repairs.clear.assert_not_awaited()


async def test_recovered_terminal_without_target_is_unknown_without_requeue(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, recovered=True)
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())

    statuses = iter(
        [
            PanelInstallStatus(running=True, component="ha-paneld"),
            PanelInstallStatus(running=False, component="ha-paneld"),
        ]
    )

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=next(statuses), error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )

    # The recovered observer is the production entry point after entity reload.
    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await observer

    repairs.record.assert_not_awaited()
    assert entity._update_coordinator.async_request_refresh.await_count == 2
    repairs.clear.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_recovered_stalled_update_creates_repair_without_requeue(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, recovered=True)
    entity.coordinator.last_update_success = False
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.01)
    real_sleep = panel_update.asyncio.sleep

    async def wait_past_deadline(_seconds: float) -> None:
        await real_sleep(0.02)

    monkeypatch.setattr(panel_update.asyncio, "sleep", wait_past_deadline)
    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None

    with pytest.raises(HomeAssistantError, match="did not return"):
        await observer

    async_get_sessions(hass).clear_restart_notice("entry-id")
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[3] is None
    assert isinstance(repairs.record.await_args.args[4], HomeAssistantError)
    client.async_start_panel_update.assert_not_awaited()


async def test_recovered_update_accepts_newer_health_when_offer_advanced(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, recovered=True)
    snapshot = entity.coordinator.data
    entity.coordinator.data = PanelSnapshot(
        health=snapshot.health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            panel_assistant_update=PanelCachedUpdate("0.9.9", "0.9.11", "v0.9.11"),
        ),
        status_error=None,
    )
    refreshes = 0

    async def refresh_health() -> None:
        nonlocal refreshes
        refreshes += 1
        if refreshes == 2:
            entity.coordinator.data = PanelSnapshot(
                health=PanelHealth(
                    version="0.9.10",
                    panel_id="alpha",
                    build="102",
                    config_hash="abcd",
                ),
                status=PanelStatus(warning_count=0, capability_count=0),
                status_error=None,
            )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
    statuses = iter(
        [
            PanelInstallStatus(running=True, component="ha-paneld"),
            PanelInstallStatus(running=False, component="ha-paneld"),
        ]
    )

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=next(statuses), error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())
    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await observer

    assert refreshes == 2
    assert entity.installed_version == "0.9.10"
    repairs.record.assert_not_awaited()
    repairs.clear.assert_awaited_once_with(hass, "entry-id")
    client.async_start_panel_update.assert_not_awaited()


async def test_recovered_feed_update_accepts_higher_code_with_same_name(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, recovered=True, feed=True)
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot,
        health=replace(snapshot.health, version="0.9.10", version_code=101),
    )
    refreshes = 0

    async def refresh_health() -> None:
        nonlocal refreshes
        refreshes += 1
        if refreshes == 2:
            current = entity.coordinator.data
            entity.coordinator.data = replace(
                current,
                health=replace(current.health, build="102", version_code=102),
            )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
    statuses = iter(
        [
            PanelInstallStatus(running=True, component="ha-paneld"),
            PanelInstallStatus(running=False, component="ha-paneld"),
        ]
    )

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=next(statuses), error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())

    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await observer

    assert refreshes == 2
    repairs.record.assert_not_awaited()
    repairs.clear.assert_awaited_once_with(hass, "entry-id")
    client.async_start_panel_update.assert_not_awaited()


@pytest.mark.parametrize("feed", [False, True])
async def test_recovered_update_already_advanced_is_not_reported_failed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
    feed: bool,
) -> None:
    entity, client = _entity(hass, recovered=True, feed=feed)
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot,
        health=replace(snapshot.health, version="0.9.10", version_code=102),
    )
    statuses = iter(
        [
            PanelInstallStatus(running=True, component="ha-paneld"),
            PanelInstallStatus(running=False, component="ha-paneld"),
        ]
    )

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=next(statuses), error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())

    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await observer

    repairs.record.assert_not_awaited()
    repairs.clear.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_recovered_terminal_before_running_is_unknown(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, recovered=True)
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=PanelInstallStatus(running=False, component="ha-paneld"),
            error=None,
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None

    await observer

    entity._update_coordinator.async_request_refresh.assert_awaited_once()
    repairs.record.assert_not_awaited()
    repairs.clear.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_verified_panel_success_clears_previous_repair(
    hass: HomeAssistant, repairs: SimpleNamespace
) -> None:
    entity, client = _entity(hass)

    async def new_health() -> None:
        entity.coordinator.data = PanelSnapshot(
            health=PanelHealth(
                version="0.9.10", panel_id="alpha", build="102", config_hash="abcd"
            ),
            status=PanelStatus(warning_count=0, capability_count=0),
            status_error=None,
        )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=new_health)

    await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once()
    repairs.clear.assert_awaited_once_with(hass, "entry-id")
    repairs.record.assert_not_awaited()


async def test_invalid_request_does_not_create_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass)

    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entity.async_install("0.9.11", backup=False)

    client.async_start_panel_update.assert_not_awaited()
    repairs.record.assert_not_awaited()
    repairs.clear.assert_not_awaited()

    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(running=False, component="ha-paneld"),
        error=None,
    )
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0)

    with pytest.raises(HomeAssistantError, match="did not complete"):
        await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once_with("v0.9.10")
    repairs.record.assert_awaited_once()


@pytest.mark.parametrize("refusal", [UpdateRejectedError, CannotConnectError])
async def test_valid_panel_request_refusal_creates_repair(
    hass: HomeAssistant,
    repairs: SimpleNamespace,
    refusal: type[Exception],
) -> None:
    entity, client = _entity(hass)
    client.async_start_panel_update.side_effect = refusal

    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once_with("v0.9.10")
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[:4] == (
        hass,
        "entry-id",
        "Test panel",
        "0.9.10",
    )
    repairs.clear.assert_not_awaited()


@pytest.mark.parametrize("failure", ["backup", "download", "stage", "verification"])
async def test_valid_feed_update_failure_before_commit_creates_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
    failure: str,
) -> None:
    entity, client = _entity(hass, feed=True)
    selected = entity._feed.data.builds[0]
    if failure == "download":
        # An explicitly requested build outside the verified offer is still
        # downloaded and checked on demand, and its failure creates a repair.
        selected = replace(selected, version_code=103, apk_sha256="b" * 64)
        entity._feed.data = BuildFeed(
            channel="maintainer", builds=(selected, *entity._feed.data.builds)
        )
    backup = AsyncMock(
        side_effect=OSError("backup unavailable") if failure == "backup" else None
    )
    download = AsyncMock(
        side_effect=BuildDownloadError("download unavailable")
        if failure == "download"
        else None,
        return_value=b"apk",
    )
    monkeypatch.setattr(panel_update, "async_store_panel_backup", backup)
    monkeypatch.setattr(panel_update, "async_get_clientsession", lambda _hass: object())
    monkeypatch.setattr(panel_update, "async_download_build", download)
    if failure == "stage":

        async def refresh_feed_during_backup(*_args: object) -> None:
            entity._feed.data = BuildFeed(
                channel="maintainer",
                builds=(replace(selected, version_code=103, apk_sha256="b" * 64),),
            )

        backup.side_effect = refresh_feed_during_backup
        client.async_stage_apk.side_effect = UpdateBusyError
    elif failure == "verification":
        client.async_stage_apk.return_value = StagedApk(
            token="token-1",
            package=LEGACY_PACKAGE_ID,
            version="0.9.10",
            signer="0" * 64,
        )

    with pytest.raises(HomeAssistantError):
        await entity.async_install(
            "103" if failure == "download" else None, backup=False
        )

    backup.assert_awaited_once()
    if failure == "download":
        download.assert_awaited_once()
    else:
        download.assert_not_awaited()
    if failure in ("stage", "verification"):
        client.async_stage_apk.assert_awaited_once_with(b"apk")
    client.async_commit_apk.assert_not_awaited()
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[:4] == (
        hass,
        "entry-id",
        "Test panel",
        selected.label,
    )
    assert isinstance(repairs.record.await_args.args[4], HomeAssistantError)
    frozen = repairs.record.await_args.kwargs["artifact"]
    assert frozen == asdict(feed_release_artifact(selected))
    assert frozen["sha256"] == selected.apk_sha256
    assert frozen["version"] == selected.version_name
    assert frozen["descriptor"]["package_id"] == selected.package_id
    repairs.clear.assert_not_awaited()


async def test_accepted_staged_update_failure_creates_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    entity, client = _entity(hass, feed=True)
    monkeypatch.setattr(panel_update, "async_store_panel_backup", AsyncMock())
    monkeypatch.setattr(panel_update, "async_get_clientsession", lambda _hass: object())
    monkeypatch.setattr(
        panel_update, "async_download_build", AsyncMock(return_value=b"apk")
    )
    monkeypatch.setattr(panel_update, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 0)
    monkeypatch.setattr(panel_update, "_RESTART_HEALTH_GRACE_SECONDS", 0)

    with pytest.raises(HomeAssistantError, match="did not return"):
        await entity.async_install(None, backup=False)

    client.async_commit_apk.assert_awaited_once_with("token-1")
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[:4] == (
        hass,
        "entry-id",
        "Test panel",
        "0.9.10 build 102",
    )
    assert isinstance(repairs.record.await_args.args[4], HomeAssistantError)
    repairs.clear.assert_not_awaited()

"""Accepted update failures remain repairable until a verified update succeeds."""

from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    LEGACY_PACKAGE_ID,
)
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
from custom_components.panel_assistant.failure_repair import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.feed_coordinator import (
    DATA_BUILD_FEED,
    BuildFeedCoordinator,
    StableReleaseCoordinator,
)
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
        panel_update,
        "async_clear_update_failure_if_installed",
        calls.clear,
        raising=False,
    )
    return calls


def _entity(
    hass: HomeAssistant,
    *,
    recovered: bool = False,
    feed: bool = False,
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    client = SimpleNamespace(
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
        async_get_status=AsyncMock(
            return_value=PanelStatus(
                warning_count=0,
                capability_count=0,
                home_ui={"state": "ready", "reason": "dashboard", "evidence": "x"},
            )
        ),
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = PanelSnapshot(
        health=PanelHealth(
            version="0.9.9", panel_id="alpha", build="101", config_hash="abcd"
        ),
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="api",
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
        launch_component=LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0],
        protocol_min=3,
        protocol_max=3,
    )
    if feed:
        coordinator = BuildFeedCoordinator(hass, URL("https://feed.example/feed"))
        coordinator.data = BuildFeed(channel="maintainer", builds=(build,))
        coordinator.last_update_success = True
        coordinator._verified_apks[build.apk_sha256] = b"apk"
    host_artifact = replace(feed_release_artifact(build), tag="v0.9.10")
    host = StableReleaseCoordinator(hass)
    host._candidates[host_artifact.tag, LEGACY_PACKAGE_ID] = host_artifact
    entity = HaPaneldUpdateEntity(
        "entry-id",
        health,
        updates,
        coordinator,
        title="Test panel",
        release=host,
    )
    if not feed:
        # The repair tests observe the admitted panel-download route; the LAN
        # suite independently exercises authenticated bytes, backup and staging.
        entity._async_deliver_build = AsyncMock(return_value=False)
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
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)

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

    with pytest.raises(HomeAssistantError, match="did not complete"):
        await observer

    async_get_sessions(hass).clear_restart_notice("entry-id")
    repairs.record.assert_awaited_once()
    assert repairs.record.await_args.args[3] is None
    assert isinstance(repairs.record.await_args.args[4], HomeAssistantError)
    assert repairs.record.await_args.kwargs["observed_before"] == ("0.9.9", None)
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
    repairs.clear.assert_awaited_once_with(
        hass, "entry-id", "0.9.10", None, verified_success=True
    )
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
    repairs.clear.assert_awaited_once_with(
        hass, "entry-id", "0.9.10", 102, verified_success=True
    )
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
    repairs.clear.assert_awaited_once_with(
        hass, "entry-id", "0.9.10", None, verified_success=True
    )
    repairs.record.assert_not_awaited()


@pytest.mark.parametrize(
    ("target", "earlier", "reached", "clears", "observed_before"),
    [
        ("0.9.10", ("0.9.9", None), ("0.9.10", None), True, None),
        ("0.9.10", ("0.9.9", None), ("0.9.11", None), True, None),
        ("0.9.10", ("0.9.9", None), ("0.9.11-rc1", 103), True, None),
        ("0.9.7-rc4 build 102", ("0.9.7-rc4", 101), ("0.9.7-rc4", 102), True, None),
        ("0.9.7-rc4 build 102", ("0.9.7-rc4", 101), ("0.9.7-rc4", 103), True, None),
        ("0.9.7-rc4 build 102", ("0.9.7-rc4", 101), ("0.9.7-rc4", None), False, None),
        (None, ("0.9.9", None), ("0.9.11", 103), False, None),
        (None, ("0.9.7-rc4", 101), ("0.9.7-rc4", 102), True, ("0.9.7-rc4", 101)),
    ],
)
async def test_accepted_health_poll_clears_repair_when_failed_build_is_reached(
    hass: HomeAssistant,
    target: str | None,
    earlier: tuple[str, int | None],
    reached: tuple[str, int | None],
    clears: bool,
    observed_before: tuple[str, int | None] | None,
) -> None:
    entry = MockConfigEntry(
        domain="panel_assistant",
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        target,
        RuntimeError("interrupted rollout"),
        observed_before=observed_before,
    )
    hass.data.pop("panel_assistant.failure_repair_store", None)
    client = SimpleNamespace(
        address="panel.local",
        async_get_health=AsyncMock(),
        async_get_status=AsyncMock(return_value=PanelStatus(0, 0)),
        async_get_version_code=AsyncMock(),
    )

    async def read_code() -> tuple[str, int]:
        health = client.async_get_health.return_value
        if health.version_code is None:
            raise CannotConnectError
        return health.version, health.version_code

    client.async_get_version_code.side_effect = read_code
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)  # type: ignore[arg-type]

    client.async_get_health.return_value = PanelHealth(
        version=earlier[0],
        version_code=earlier[1],
        panel_id="alpha",
        build="earlier",
        config_hash="abcd",
    )
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is not None
    assert (await async_failure_events(hass, issue_id))[-1][
        "reason"
    ] == "interrupted rollout"

    client.async_get_health.return_value = PanelHealth(
        version=reached[0],
        version_code=reached[1],
        panel_id="alpha",
        build="reached",
        config_hash="abcd",
    )
    await coordinator.async_refresh()
    assert (
        ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is None
    ) is clears
    if clears:
        assert await async_failure_events(hass, issue_id) == []
    else:
        assert (await async_failure_events(hass, issue_id))[-1][
            "reason"
        ] == "interrupted rollout"


@pytest.mark.parametrize(
    (
        "installed_code",
        "diag",
        "verified",
        "feed_ok",
        "package",
        "observed_before",
        "clears",
    ),
    [
        pytest.param(
            102,
            ("0.9.10", 102),
            True,
            True,
            LEGACY_PACKAGE_ID,
            None,
            True,
            id="current",
        ),
        pytest.param(
            103, ("0.9.10", 103), True, True, LEGACY_PACKAGE_ID, None, True, id="newer"
        ),
        pytest.param(
            101, ("0.9.10", 101), True, True, LEGACY_PACKAGE_ID, None, False, id="below"
        ),
        pytest.param(
            102,
            ("0.9.10", 101),
            True,
            True,
            LEGACY_PACKAGE_ID,
            None,
            False,
            id="diag-below",
        ),
        pytest.param(
            102,
            ("0.9.9", 102),
            True,
            True,
            LEGACY_PACKAGE_ID,
            None,
            False,
            id="diag-name-mismatch",
        ),
        pytest.param(
            102, None, True, True, LEGACY_PACKAGE_ID, None, False, id="diag-unavailable"
        ),
        pytest.param(
            102,
            ("0.9.10", 102),
            False,
            True,
            LEGACY_PACKAGE_ID,
            None,
            False,
            id="unverified",
        ),
        pytest.param(
            102,
            ("0.9.10", 102),
            True,
            False,
            LEGACY_PACKAGE_ID,
            None,
            False,
            id="stale-feed",
        ),
        pytest.param(
            102,
            ("0.9.10", 102),
            True,
            True,
            "io.panelassistant.android",
            None,
            False,
            id="other-package",
        ),
        pytest.param(
            None, None, True, True, LEGACY_PACKAGE_ID, None, False, id="unknown-code"
        ),
        pytest.param(
            102,
            ("0.9.10", 102),
            True,
            True,
            LEGACY_PACKAGE_ID,
            ("0.9.10", 102),
            False,
            id="fresh-failure-no-advance",
        ),
    ],
)
async def test_older_targetless_repair_clears_only_at_verified_current_feed_build(
    hass: HomeAssistant,
    installed_code: int | None,
    diag: tuple[str, int] | None,
    verified: bool,
    feed_ok: bool,
    package: str,
    observed_before: tuple[str, int] | None,
    clears: bool,
) -> None:
    entry = MockConfigEntry(
        domain="panel_assistant",
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        None,
        RuntimeError("interrupted rollout"),
        observed_before=observed_before,
    )
    hass.data.pop("panel_assistant.failure_repair_store", None)

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
        launch_component=LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0],
        protocol_min=3,
        protocol_max=3,
    )
    feed = BuildFeedCoordinator(hass, URL("https://feed.example/feed"))
    feed.data = BuildFeed(channel="maintainer", builds=(build,))
    feed.last_update_success = feed_ok
    if verified:
        feed._verified_apks[build.apk_sha256] = b"apk"
    hass.data.setdefault("panel_assistant", {})[DATA_BUILD_FEED] = feed

    client = SimpleNamespace(
        address="panel.local",
        async_get_health=AsyncMock(
            return_value=PanelHealth(
                version="0.9.10",
                version_code=installed_code,
                panel_id="alpha",
                build="installed",
                config_hash="abcd",
                package=package,
            )
        ),
        async_get_status=AsyncMock(return_value=PanelStatus(0, 0)),
        async_get_version_code=AsyncMock(
            return_value=diag, side_effect=CannotConnectError if diag is None else None
        ),
    )
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)  # type: ignore[arg-type]
    await coordinator.async_refresh()

    assert (
        ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is None
    ) is clears
    if clears:
        assert await async_failure_events(hass, issue_id) == []
    else:
        assert (await async_failure_events(hass, issue_id))[-1][
            "reason"
        ] == "interrupted rollout"


async def test_feed_failure_repair_uses_diagnostic_code_instead_of_health_label(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain="panel_assistant",
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10 build 102",
        RuntimeError("interrupted rollout"),
    )
    client = SimpleNamespace(
        address="panel.local",
        async_get_health=AsyncMock(
            return_value=PanelHealth(
                version="0.9.10",
                version_code=102,
                panel_id="alpha",
                build="installed",
                config_hash="abcd",
            )
        ),
        async_get_status=AsyncMock(return_value=PanelStatus(0, 0)),
        async_get_version_code=AsyncMock(return_value=("0.9.10", 101)),
    )
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)  # type: ignore[arg-type]

    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is not None
    client.async_get_version_code.return_value = ("0.9.10", 102)
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_verified_update_clears_orphan_repair_without_saved_report(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain="panel_assistant",
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    ir.async_create_issue(
        hass,
        "panel_assistant",
        issue_id,
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="installer_failure_update",
        translation_placeholders={"panel": entry.title},
        data={"key": issue_id},
    )

    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_success=True
    )
    assert ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is None


async def test_installing_older_named_feed_build_keeps_newer_failure_repair(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain="panel_assistant",
        title="Test panel",
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10 build 103",
        RuntimeError("newer update failed"),
    )
    entity, _client = _entity(hass, feed=True)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(
        return_value=(panel_update.ROUTE_PANEL, None, None)
    )
    entity._async_deliver_build = AsyncMock()

    await entity.async_install("0.9.10 build 102", backup=False)

    entity._async_deliver_build.assert_awaited_once()
    assert ir.async_get(hass).async_get_issue("panel_assistant", issue_id) is not None
    assert (await async_failure_events(hass, issue_id))[-1][
        "reason"
    ] == "newer update failed"


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
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)

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

    with pytest.raises(HomeAssistantError, match="did not complete"):
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


@pytest.mark.parametrize(
    "versions", [("0.9.9-rc1", "0.9.9-rc2"), ("0.9.9-rc9", "0.9.9-rc10")]
)
async def test_recovered_prerelease_advance_completes_without_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
    versions: tuple[str, str],
) -> None:
    """A running update found by PA can return on a later release candidate."""
    entity, client = _entity(hass, recovered=True)
    before, after = versions
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot, health=replace(snapshot.health, version=before, version_code=1050)
    )
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)

    async def refresh_health() -> None:
        entity.coordinator.data = replace(
            snapshot, health=replace(snapshot.health, version=after, version_code=1068)
        )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)

    async def refresh_status() -> None:
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=None, error=None
        )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    entity._resume_running_operation()
    await entity._observer_task

    assert entity.installed_version == after
    assert not entity.in_progress
    repairs.record.assert_not_awaited()
    repairs.clear.assert_awaited_once_with(
        hass, "entry-id", after, 1068, verified_success=True
    )
    client.async_start_panel_update.assert_not_awaited()


async def test_slow_panel_return_after_terminal_slot_still_completes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    """A stopped progress slot cannot shorten the accepted update's total wait."""
    entity, _ = _entity(hass)
    snapshot = entity.coordinator.data
    monkeypatch.setattr(panel_update, "_TERMINAL_STATUS_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(running=False, component="ha-paneld"), error=None
    )
    refreshes = 0

    async def refresh_health() -> None:
        nonlocal refreshes
        refreshes += 1
        entity.coordinator.last_update_success = refreshes >= 4
        if refreshes >= 4:
            entity.coordinator.data = replace(
                snapshot, health=replace(snapshot.health, version="0.9.10")
            )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
    await entity.async_install(None, backup=False)

    async_get_sessions(hass).clear_restart_notice("entry-id")
    assert entity.installed_version == "0.9.10"
    assert not entity.in_progress
    repairs.record.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["not_started", "dashboard_missing"])
async def test_reachable_panel_unproved_update_reports_incomplete(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
    outcome: str,
) -> None:
    """A panel answering health never becomes a claim that it did not return."""
    entity, _ = _entity(hass)
    entity._update_coordinator.data = PanelUpdateSnapshot(operation=None, error=None)
    if outcome == "dashboard_missing":
        snapshot = entity.coordinator.data

        async def refresh_health() -> None:
            entity.coordinator.data = replace(
                snapshot, health=replace(snapshot.health, version="0.9.10")
            )

        entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
        entity.coordinator.client.async_get_status = AsyncMock(
            return_value=PanelStatus(warning_count=0, capability_count=0)
        )
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)

    with pytest.raises(HomeAssistantError, match="update did not complete") as error:
        await entity.async_install(None, backup=False)

    assert error.value.translation_key == "update_not_complete"
    assert not entity.in_progress
    repairs.record.assert_awaited_once()


async def test_recovered_disappeared_operation_ends_unknown_without_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    repairs: SimpleNamespace,
) -> None:
    """A past running sample with no durable target is not a failed install."""
    entity, client = _entity(hass, recovered=True)
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(panel_update, "_UPDATE_RECHECK_SECONDS", 0.01)
    refreshes = 0

    async def refresh_status() -> None:
        nonlocal refreshes
        refreshes += 1
        if refreshes > 1:
            entity._update_coordinator.data = PanelUpdateSnapshot(
                operation=None, error=None
            )

    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_status
    )
    entity._resume_running_operation()
    await entity._observer_task

    assert not entity.in_progress
    repairs.record.assert_not_awaited()
    repairs.clear.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()

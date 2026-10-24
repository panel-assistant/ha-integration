"""Configured-panel update projection tests."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientConnectorError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant import update_progress, update_route
from custom_components.panel_assistant.adb_credentials import AdbCredentialError
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelHealth,
    PanelInstallStatus,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.release import ReleaseArtifact
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)

HEALTH = PanelHealth(
    version="0.9.9",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    package=PACKAGE_ID,
    protocol=(5, 5),
)


def _connection_refused() -> CannotConnectError:
    """The real client wraps a failed connection before sending the request."""
    error = CannotConnectError()
    error.__cause__ = ClientConnectorError(
        SimpleNamespace(host="panel.local", port=8888, ssl=False),
        OSError(111, "Connection refused"),
    )
    return error


def _assert_translated(error: HomeAssistantError, key: str) -> None:
    assert error.translation_domain == DOMAIN
    assert error.translation_key == key


def _entity(
    hass: HomeAssistant,
    *,
    version: str = "0.9.9",
    operation: PanelInstallStatus | None = None,
    update_error: str | None = None,
    install_capability: str | None = "api",
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    client = SimpleNamespace(
        async_get_legacy_install_capability=AsyncMock(return_value=True),
        async_get_panel_install_status=AsyncMock(
            return_value=PanelInstallStatus(running=True, component="APK")
        ),
        async_get_status=AsyncMock(
            return_value=PanelStatus(
                warning_count=0,
                capability_count=0,
                home_ui={
                    "state": "ready",
                    "reason": "dashboard",
                    "evidence": "foreground",
                },
            )
        ),
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = PanelSnapshot(
        health=PanelHealth(
            version=version,
            panel_id=HEALTH.panel_id,
            build=HEALTH.build,
            config_hash=HEALTH.config_hash,
            package=HEALTH.package,
            protocol=(5, 5),
        ),
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability=install_capability,
        ),
        status_error=None,
    )
    updates = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    updates.data = PanelUpdateSnapshot(operation=operation, error=update_error)
    artifact = ReleaseArtifact(
        "v0.9.10",
        "0.9.10",
        "app.apk",
        "https://github.com/app.apk",
        "a" * 64,
        descriptor=SimpleNamespace(package_id=PACKAGE_ID, version_code=1100),
        protocol_min=5,
        protocol_max=5,
    )
    host = StableReleaseCoordinator(hass)
    host._candidates[artifact.tag, PACKAGE_ID] = artifact
    entity = HaPaneldUpdateEntity("entry-id", health, updates, release=host)
    # Signed LAN staging and backup execution are exercised in the LAN suite.
    entity._delivery.async_deliver_build = AsyncMock()
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    return entity, client


def test_update_entity_uses_existing_config_entry_and_offers_only_newer_stable(
    hass: HomeAssistant,
) -> None:
    """The entity has no separate device identity or arbitrary-version selector."""
    entity, _ = _entity(hass)

    assert entity.unique_id == "entry-id_update"
    assert entity.installed_version == "0.9.9"
    assert entity.latest_version == "0.9.10"
    assert entity.version_is_newer(entity.latest_version, entity.installed_version)
    assert entity.device_info["identifiers"] == {("panel_assistant", "entry-id")}


def test_update_entity_hides_a_release_older_than_running_health(
    hass: HomeAssistant,
) -> None:
    entity, _ = _entity(hass, version="0.9.11")
    assert entity.latest_version == entity.installed_version


async def test_panel_without_an_install_route_offers_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A newer signed build is not actionable when the app cannot install it.

    The stand-in panel refuses any start, so a request that reaches it fails
    as a refusal at once instead of waiting for a restart that never comes.
    """
    entity, _ = _entity(hass, install_capability="none")
    monkeypatch.setattr(
        update_route,
        "async_get_durable_adb_credential",
        AsyncMock(side_effect=AdbCredentialError),
    )

    assert entity.latest_version == entity.installed_version
    await entity._async_refresh_route()
    assert (
        "no usable authorized ADB route"
        in entity.extra_state_attributes["update_unavailable_reason"]
    )
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entity.async_install(None, backup=False)


async def test_older_api_panel_keeps_its_existing_update_route(
    hass: HomeAssistant,
) -> None:
    """An older panel's reported privileged-route bit preserves API updates."""
    entity, client = _entity(hass, install_capability=None)
    entity.coordinator.async_request_refresh = AsyncMock()
    entity._update_coordinator.async_request_refresh = AsyncMock()

    assert entity.latest_version == entity.installed_version
    await entity._async_refresh_route()
    assert entity.latest_version == "0.9.10"
    client.async_get_legacy_install_capability.assert_awaited()


async def test_changed_health_invalidates_a_cached_legacy_api_route(
    hass: HomeAssistant,
) -> None:
    """An older panel's proven route counts only for the panel it was proven on."""
    entity, _ = _entity(hass, install_capability=None)
    entity.coordinator.async_request_refresh = AsyncMock()
    await entity._async_refresh_route()
    assert entity.latest_version == "0.9.10"

    data = entity.coordinator.data
    entity.coordinator.data = replace(data, health=replace(data.health, build="1001"))

    assert entity.latest_version == entity.installed_version


def test_unparseable_versions_are_never_newer(hass: HomeAssistant) -> None:
    entity, _ = _entity(hass)

    assert not entity.version_is_newer("garbage", "0.9.9")
    assert not entity.version_is_newer("0.9.10", "garbage")


def test_a_lower_build_of_the_same_release_is_never_newer(
    hass: HomeAssistant,
) -> None:
    entity, _ = _entity(hass)

    assert not entity.version_is_newer("1.0.0 build 100", "1.0.0 build 200")
    assert not entity.version_is_newer("1.0.0 build 200", "1.0.0 build 200")
    assert entity.version_is_newer("1.0.0 build 200", "1.0.0 build 100")


async def test_panel_update_that_starts_while_the_route_is_chosen_is_not_repeated(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core admits the press before the route is read; work found then still wins."""
    entity, _ = _entity(hass)
    failure = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", failure)
    choose_route = entity._async_install_route

    async def route_while_the_panel_starts_updating() -> str:
        route = await choose_route()
        entity._update_coordinator.data = PanelUpdateSnapshot(
            operation=PanelInstallStatus(running=True, component="ha-paneld"),
            error=None,
        )
        return route

    entity._async_install_route = route_while_the_panel_starts_updating
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, backup=False)

    failure.assert_not_awaited()
    assert entity.in_progress is True


@pytest.mark.parametrize(
    "home_state", ["blocked", "unknown", "setup", None, "ready-unrendered"]
)
async def test_matching_update_health_requires_ready_home_proof(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, home_state: str | None
) -> None:
    """A restarted HTTP service cannot finish without the visible HOME proof."""
    entity, client = _entity(hass)
    entity.coordinator.data = PanelSnapshot(
        health=PanelHealth(
            version="0.9.10",
            panel_id="alpha",
            build="1001",
            config_hash="1a2b3c4d",
            package=PACKAGE_ID,
            protocol=(5, 5),
        ),
        status=PanelStatus(warning_count=0, capability_count=0),
        status_error=None,
    )
    entity.coordinator.async_request_refresh = AsyncMock()
    # A built-in dashboard in front but still on a status screen reads ready.
    unrendered = home_state == "ready-unrendered"
    client.async_get_status = AsyncMock(
        return_value=PanelStatus(
            warning_count=0,
            capability_count=0,
            home_ui=(
                {
                    "state": "ready" if unrendered else home_state,
                    "reason": "chooser",
                    "evidence": "resolver",
                }
                if home_state is not None
                else None
            ),
            renderer=(
                {"mode": "builtin", "state": "unobserved", "rendered": False}
                if unrendered
                else None
            ),
        )
    )
    entity.coordinator.reachable = True

    monkeypatch.setattr(update_progress, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 0)
    monkeypatch.setattr(update_progress, "_RESTART_HEALTH_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(update_progress, "_UPDATE_RECHECK_SECONDS", 0.001)
    artifact = ReleaseArtifact("v0.9.10", "0.9.10", "app.apk", "", "")

    with pytest.raises(HomeAssistantError, match="did not complete"):
        await entity._delivery.async_wait_for_build(
            hass, artifact, ("1000", "io.panelassistant.android")
        )
    client.async_get_status.assert_awaited()


@pytest.mark.parametrize("version", ["0.9.9", "0.9.11"])
async def test_update_entity_rejects_versions_outside_the_current_offer(
    hass: HomeAssistant, version: str
) -> None:
    """HA cannot turn the update service into a version or downgrade control surface.

    The stand-in panel refuses any start, so installing the offered build in
    place of the requested one fails as a refusal at once instead of waiting.
    """
    entity, _ = _entity(hass)

    with pytest.raises(HomeAssistantError, match="unavailable") as error:
        await entity.async_install(version, backup=False)

    _assert_translated(error.value, "update_unavailable")


def test_update_timeout_covers_android_download_install_and_restart_bounds() -> None:
    """HA must not time out during the panel's declared transaction bounds."""
    assert update_progress._UPDATE_TIMEOUT_SECONDS == 14 * 60


def test_update_entity_restores_a_panel_owned_operation_after_reload(
    hass: HomeAssistant,
) -> None:
    """A running panel operation stays in progress without entity-local state."""
    entity, _ = _entity(
        hass,
        operation=PanelInstallStatus(running=True, component="ha-paneld"),
    )

    assert entity.in_progress is True


async def test_another_components_running_install_is_not_resumed_as_this_update(
    hass: HomeAssistant,
) -> None:
    """A Companion install found after reload is not this update."""
    entity, _ = _entity(
        hass, operation=PanelInstallStatus(running=True, component="companion")
    )
    entity.coordinator.async_request_refresh = AsyncMock()
    entity._update_coordinator.async_request_refresh = AsyncMock()

    entity._handle_update_coordinator_update()
    try:
        assert entity.in_progress is False
    finally:
        entity._cancel_observer()


async def test_reload_observer_latches_through_transient_and_terminal_status(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reload recovery waits for fresh target health without issuing an install."""
    entity, _ = _entity(
        hass,
        operation=PanelInstallStatus(running=True, component="ha-paneld"),
    )
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    health_refreshes = 0
    guarded_samples: list[bool] = []

    async def refresh_health() -> None:
        nonlocal health_refreshes
        health_refreshes += 1
        guarded_samples.append(entity.in_progress)
        if health_refreshes == 3:
            entity.coordinator.data = PanelSnapshot(
                health=PanelHealth(
                    version="0.9.10",
                    panel_id=HEALTH.panel_id,
                    build=HEALTH.build,
                    config_hash=HEALTH.config_hash,
                    protocol=(5, 5),
                ),
                status=PanelStatus(warning_count=0, capability_count=0),
                status_error=None,
            )

    update_samples = iter(
        [
            PanelUpdateSnapshot(operation=None, error="unavailable"),
            PanelUpdateSnapshot(
                operation=PanelInstallStatus(
                    running=False,
                    component="ha-paneld",
                ),
                error=None,
            ),
            PanelUpdateSnapshot(operation=None, error=None),
        ]
    )

    async def refresh_update() -> None:
        entity._update_coordinator.data = next(update_samples)

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh_health)
    entity._update_coordinator.async_request_refresh = AsyncMock(
        side_effect=refresh_update
    )
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())

    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await observer

    assert guarded_samples == [True, True, True]
    assert entity.installed_version == "0.9.10"
    assert entity.in_progress is False


async def test_unload_cancels_recovered_observer_without_install_retry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entity teardown owns and cancels its sole recovered observer."""
    entity, _ = _entity(
        hass,
        operation=PanelInstallStatus(running=True, component="ha-paneld"),
    )
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    waiting = asyncio.Event()

    async def wait_forever(_hass: HomeAssistant, _expected_version: str) -> None:
        await waiting.wait()

    monkeypatch.setattr(
        entity._progress, "async_wait_for_installed_version", wait_forever
    )

    entity._resume_running_operation()
    observer = entity._observer_task
    assert observer is not None
    await asyncio.sleep(0)
    entity._cancel_observer()
    await asyncio.sleep(0)

    assert observer.cancelled()
    assert entity._observer_task is None


async def test_update_coordinator_keeps_local_operation_faults_out_of_entry_setup(
    hass: HomeAssistant,
) -> None:
    """A missing progress endpoint cannot make an existing status entry unavailable."""
    client = SimpleNamespace(async_get_panel_install_status=AsyncMock())
    client.async_get_panel_install_status.side_effect = CannotConnectError
    coordinator = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]

    snapshot = await coordinator._async_update_data()

    assert snapshot == PanelUpdateSnapshot(operation=None, error="unavailable")


_AWAITING = PanelInstallStatus(
    running=True, component="ha-paneld", awaiting_confirmation=True
)


def test_an_update_waiting_for_a_tap_on_the_panel_says_so(
    hass: HomeAssistant,
) -> None:
    """A rootless panel's own installer waits for its owner to tap Install."""
    entity, _ = _entity(hass, operation=_AWAITING)

    assert entity.in_progress is True
    assert entity.release_summary == "Tap Install on the panel to finish the update."
    assert (entity.extra_state_attributes or {}).get("awaiting_confirmation") is True

    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(running=True, component="ha-paneld"),
        error=None,
    )
    assert entity.release_summary is None
    assert "awaiting_confirmation" not in (entity.extra_state_attributes or {})


def test_an_update_home_assistant_sent_also_says_to_tap(hass: HomeAssistant) -> None:
    """The staged upload runs on the panel as "APK" and waits for the same tap."""
    entity, _ = _entity(
        hass,
        operation=PanelInstallStatus(
            running=True, component="APK", awaiting_confirmation=True
        ),
    )

    assert entity.release_summary == "Tap Install on the panel to finish the update."

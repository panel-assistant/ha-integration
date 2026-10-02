"""Update-entity, setup and backup tests for the signed build feed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import stat
from contextlib import ExitStack
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

import pytest
from aiohttp.web import HTTPBadRequest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import CONFIG_SCHEMA, async_setup
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.adb_credentials import AdbCredentialError
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import (
    BuildDownloadError,
    BuildFeed,
    BuildFeedError,
    FeedBuild,
    feed_release_artifact,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    StagedApk,
    UpdateApprovalRequiredError,
    UploadDisabledError,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import (
    BuildFeedCoordinator,
    async_get_feed_coordinator,
)
from custom_components.panel_assistant.install_adb import InstallOutcome
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.panel_backup import (
    PanelBackupInvalidError,
    async_store_panel_backup,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.release import (
    _RELEASE_SIGNER_CERTIFICATE_SHA256,
)
from custom_components.panel_assistant.status import PanelCachedUpdate, PanelStatus
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)


def _archive(
    *, manifest: bool = True, entries: int = 1, state_error: bool = False
) -> bytes:
    """Build an archive shaped like the panel's own settings backup."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        if manifest:
            archive.writestr(
                "manifest.json",
                '{"state":{"error":"capture-failed"}}'
                if state_error
                else '{"discovery_id":"a"}',
            )
        for index in range(entries):
            archive.writestr(f"payload-{index}.bin", b"state")
    return buffer.getvalue()


BACKUP = _archive()
FEED_URL = URL("https://feed.example/x/maintainer.json")
NAME = "0.9.7-rc4"
APK = b"apk-bytes"
OFFER = PanelCachedUpdate("0.9.9", "0.9.10", "v0.9.10")
FETCH = "custom_components.panel_assistant.feed_coordinator.async_fetch_build_feed"


def _build(code: int, package_id: str = LEGACY_PACKAGE_ID) -> FeedBuild:
    sha = hashlib.sha256(str(code).encode()).hexdigest()
    return FeedBuild(
        version_code=code,
        version_name=NAME,
        apk_url=FEED_URL.join(URL(f"apks/{sha}.apk")),
        apk_sha256=sha,
        apk_size=len(APK),
        commit="0" * 40,
        database_compatibility="hapaneld-db:v1:ha-paneld.db:11:14",
        min_sdk=26,
        package_id=package_id,
        published="2026-09-11T10:00:00Z",
        protocol_min=3,
        protocol_max=3,
    )


def _feed_data(*codes: int) -> BuildFeed:
    return BuildFeed(
        channel="maintainer",
        builds=tuple(_build(code) for code in sorted(codes, reverse=True)),
    )


def _preview(**replacements: str) -> StagedApk:
    values = {
        "token": "tok-1",
        "package": LEGACY_PACKAGE_ID,
        "version": NAME,
        "signer": _RELEASE_SIGNER_CERTIFICATE_SHA256,
    }
    values.update(replacements)
    return StagedApk(**values)


def _health(version: str, build: str = "1000") -> PanelHealth:
    return PanelHealth(
        version=version, panel_id="alpha", build=build, config_hash="1a2b3c4d"
    )


def _entity(
    hass: HomeAssistant,
    *,
    version: str = NAME,
    offer: PanelCachedUpdate | None = None,
    feed: BuildFeed | None = None,
    with_feed: bool = True,
    installed_code: int | None = 771,
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    client = SimpleNamespace(
        configuration_url="http://panel.local:8888",
        async_start_panel_update=AsyncMock(),
        async_get_panel_install_status=AsyncMock(),
        async_get_version_code=AsyncMock(return_value=(version, installed_code)),
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
        async_backup_panel=AsyncMock(return_value=BACKUP),
        async_stage_apk=AsyncMock(return_value=_preview()),
        async_commit_apk=AsyncMock(),
        async_discard_apk=AsyncMock(),
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = PanelSnapshot(
        health=_health(version),
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="api",
            panel_assistant_update=offer,
        ),
        status_error=None,
    )
    # Health never changes unless a test says the app restarted.
    health.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    updates = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    updates.data = PanelUpdateSnapshot(operation=None, error=None)
    coordinator: BuildFeedCoordinator | None = None
    if with_feed:
        coordinator = BuildFeedCoordinator(hass, FEED_URL)
        coordinator.data = feed if feed is not None else _feed_data(770, 771, 772)
        coordinator.last_update_success = True
        for package_id in (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID):
            newest = coordinator.data.newest(package_id)
            if newest is not None:
                coordinator._verified_newest[package_id, True] = (newest, APK)
    entity = HaPaneldUpdateEntity("entry-id", health, updates, coordinator)
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    if installed_code is not None:
        entity._installed_code = installed_code
        entity._code_key = (version, "1000")
    return entity, client


def _assert_translated(error: HomeAssistantError, key: str) -> None:
    assert error.translation_domain == DOMAIN
    assert error.translation_key == key


@pytest.fixture
def delivery(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """Keep backups in a temporary config dir and stub the APK download."""
    hass.config.config_dir = str(tmp_path)
    session = object()
    download = AsyncMock(return_value=APK)
    monkeypatch.setattr(panel_update, "async_get_clientsession", lambda _hass: session)
    monkeypatch.setattr(panel_update, "async_download_build", download)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())
    # With sleep stubbed, an unexpected wait would spin to its real deadline;
    # keep that to about a second so a regression fails fast instead of hanging.
    monkeypatch.setattr(panel_update, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 0)
    monkeypatch.setattr(panel_update, "_RESTART_HEALTH_GRACE_SECONDS", 1)
    return SimpleNamespace(
        session=session,
        download=download,
        backups=tmp_path / DOMAIN / "backups",
    )


def _restart_into(
    entity: HaPaneldUpdateEntity,
    client: SimpleNamespace,
    code: int,
    calls: list[Any] | None = None,
    package: str | None = None,
) -> None:
    """Make the next health refresh show a restarted app reporting `code`."""

    async def refresh() -> None:
        if calls is not None:
            calls.append("refresh")
        entity.coordinator.data = PanelSnapshot(
            health=PanelHealth(
                version=NAME,
                panel_id="alpha",
                build="2000",
                config_hash="1a2b3c4d",
                package=package,
            ),
            status=PanelStatus(
                warning_count=0, capability_count=0, install_capability="api"
            ),
            status_error=None,
        )

    async def diag() -> tuple[str, int]:
        if calls is not None:
            calls.append("diag")
        return NAME, code

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)
    client.async_get_version_code = AsyncMock(side_effect=diag)


# --- presentation ------------------------------------------------------------


def test_without_a_feed_behaviour_is_unchanged(hass: HomeAssistant) -> None:
    """No feed: stable versions, no build numbers, no specific-version selector."""
    feed_entity, _ = _entity(hass)
    stable, client = _entity(
        hass, version="0.9.9", offer=OFFER, with_feed=False, installed_code=None
    )

    assert feed_entity.supported_features & UpdateEntityFeature.SPECIFIC_VERSION
    assert not stable.supported_features & UpdateEntityFeature.SPECIFIC_VERSION
    assert stable._feed_mode() is None
    assert stable.installed_version == "0.9.9"
    assert stable.latest_version == stable.installed_version
    assert stable.version_is_newer("0.9.10", "0.9.9") is True
    stable._refresh_installed_code()
    assert stable._code_task is None
    client.async_get_version_code.assert_not_awaited()


async def test_feed_with_unknown_installed_code_falls_back_to_stable(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A panel-cached offer cannot prove range compatibility while diagnostics load."""
    entity, client = _entity(hass, version="0.9.9", offer=OFFER, installed_code=None)

    assert entity._feed_mode() is None
    assert entity.installed_version == "0.9.9"
    assert entity.latest_version == entity.installed_version
    assert entity.version_is_newer("0.9.10", "0.9.9") is True

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install("0.9.7-rc4 build 772", backup=False)

    _assert_translated(error.value, "update_unavailable")
    client.async_backup_panel.assert_not_awaited()
    delivery.download.assert_not_awaited()


def test_failed_feed_read_falls_back_to_stable(hass: HomeAssistant) -> None:
    """A feed that could not be authenticated offers nothing."""
    entity, _ = _entity(hass, version="0.9.9", offer=OFFER)
    assert entity._feed is not None
    entity._feed.last_update_success = False

    assert entity._feed_mode() is None
    assert entity.installed_version == "0.9.9"
    assert entity.latest_version == entity.installed_version


def test_feed_mode_names_builds_and_offers_the_newest(hass: HomeAssistant) -> None:
    """Installed 771 with a 770-772 feed offers 772 by build number."""
    entity, _ = _entity(hass)

    assert entity.supported_features & UpdateEntityFeature.SPECIFIC_VERSION
    assert entity.installed_version == "0.9.7-rc4 build 771"
    assert entity.latest_version == "0.9.7-rc4 build 772"
    assert entity.version_is_newer(entity.latest_version, entity.installed_version)
    assert not entity.version_is_newer("0.9.7-rc4 build 770", entity.installed_version)


def test_feed_mode_offers_nothing_when_installed_is_newest(
    hass: HomeAssistant,
) -> None:
    """No build newer than the running one means no update."""
    entity, _ = _entity(hass, installed_code=772)

    assert entity.latest_version == entity.installed_version == "0.9.7-rc4 build 772"


async def test_feed_offer_is_withheld_when_no_install_route_works(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified build is still withheld from a panel that cannot install it."""
    entity, client = _entity(hass)
    entity.coordinator.data = PanelSnapshot(
        health=entity.coordinator.data.health,
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="none"
        ),
        status_error=None,
    )
    monkeypatch.setattr(
        panel_update,
        "async_get_durable_adb_credential",
        AsyncMock(side_effect=AdbCredentialError),
    )
    client.address = normalize_address("192.168.1.10")
    monkeypatch.setattr(
        panel_update,
        "async_pin_install_target",
        AsyncMock(side_effect=OSError("panel unreachable")),
    )

    assert entity.latest_version == entity.installed_version
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entity.async_install(None, False)
    client.async_stage_apk.assert_not_awaited()


# --- install -----------------------------------------------------------------


@pytest.mark.parametrize(
    "package,expected_code",
    [(None, 773), (SUCCESSOR_PACKAGE_ID, 772)],
)
async def test_update_install_selects_the_installed_app_before_backup(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    package: str | None,
    expected_code: int,
) -> None:
    """Each panel takes its signed APK even when the other app shares a code."""
    feed = BuildFeed(
        "maintainer", (_build(773), _build(772), _build(772, SUCCESSOR_PACKAGE_ID))
    )
    entity, client = _entity(hass, feed=feed)
    health = entity.coordinator.data.health
    entity.coordinator.data = PanelSnapshot(
        health=PanelHealth(
            version=health.version,
            panel_id=health.panel_id,
            build=health.build,
            config_hash=health.config_hash,
            package=package,
        ),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
        status_error=None,
    )
    client.async_stage_apk.return_value = _preview(package=package or LEGACY_PACKAGE_ID)
    expected_apk = f"apk-for-{expected_code}-{package or LEGACY_PACKAGE_ID}".encode()
    assert entity._feed is not None
    selected_build = entity._feed.verified_newest(package)
    assert selected_build is not None
    entity._feed._verified_newest[selected_build.package_id, True] = (
        selected_build,
        expected_apk,
    )
    _restart_into(entity, client, expected_code, package=package)

    assert entity.latest_version == f"0.9.7-rc4 build {expected_code}"
    await entity.async_install(None, False)

    client.async_backup_panel.assert_awaited_once()
    client.async_stage_apk.assert_awaited_once_with(expected_apk)
    delivery.download.assert_not_awaited()
    backups = list(delivery.backups.glob("entry-id-*.zip"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == BACKUP
    receipt = json.loads(backups[0].with_name(f"{backups[0].name}.json").read_text())
    assert receipt["sha256"] == hashlib.sha256(BACKUP).hexdigest()
    client.async_commit_apk.assert_awaited_once()


@pytest.mark.parametrize("target_installed", [True, False])
async def test_rootless_panel_uses_its_existing_authorized_adb_route(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    target_installed: bool,
) -> None:
    """Only a matching installed package permits the authorized ADB offer."""
    entity, client = _entity(hass)
    client.address = normalize_address("192.168.1.10")
    entity.coordinator.data = PanelSnapshot(
        health=entity.coordinator.data.health,
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="none"
        ),
        status_error=None,
    )
    pinned = PinnedPanelTarget(client.address, client.address)
    credential = SimpleNamespace(signer=object(), generation_id="held-key")
    probe = InstallTargetProbe(
        state=InstallTargetState.MIGRATION_CANDIDATE,
        serial="serial-a",
        model="model-a",
        primary_abi="arm64-v8a",
        android_sdk=30,
    )
    monkeypatch.setattr(
        panel_update,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=credential),
    )
    monkeypatch.setattr(
        panel_update, "async_pin_install_target", AsyncMock(return_value=pinned)
    )
    monkeypatch.setattr(
        panel_update, "async_revalidate_install_target", AsyncMock(return_value=pinned)
    )
    monkeypatch.setattr(
        panel_update, "async_probe_install_target", AsyncMock(return_value=probe)
    )
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            target_installed=target_installed, root_mode="rootless"
        )
    )
    monkeypatch.setattr(panel_update, "async_preflight_install", preflight)
    launch = AsyncMock(return_value=panel_update.LaunchOutcome.STARTED)
    monkeypatch.setattr(panel_update, "async_launch_installed_app", launch)
    pinned_client = SimpleNamespace(
        async_get_health=AsyncMock(return_value=entity.coordinator.data.health)
    )
    monkeypatch.setattr(panel_update, "HaPaneldClient", lambda *_args: pinned_client)

    def install(
        _target: Any, _signer: Any, _descriptor: Any, path: Path
    ) -> InstallOutcome:
        assert path.read_bytes() == APK
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        return InstallOutcome.INSTALLED

    adb_install = AsyncMock(side_effect=install)
    monkeypatch.setattr(panel_update, "async_update_installed_apk", adb_install)
    _restart_into(entity, client, 772)

    await entity._async_refresh_route()
    entity._schedule_route_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    if not target_installed:
        assert entity.latest_version == entity.installed_version
        adb_install.assert_not_awaited()
        client.async_backup_panel.assert_not_awaited()
        return
    assert entity.latest_version == "0.9.7-rc4 build 772"
    await entity.async_install(None, False)

    adb_install.assert_awaited_once()
    launch.assert_awaited_once()
    preflight.assert_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert entity.extra_state_attributes == {
        "update_route": "installed_by_home_assistant_adb"
    }


async def test_same_number_from_another_app_does_not_verify_the_install(
    hass: HomeAssistant, delivery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restarted legacy app cannot prove a successor installation."""
    feed = BuildFeed("maintainer", (_build(772, SUCCESSOR_PACKAGE_ID),))
    entity, client = _entity(hass, feed=feed)
    health = entity.coordinator.data.health
    entity.coordinator.data = PanelSnapshot(
        health=PanelHealth(
            version=health.version,
            panel_id=health.panel_id,
            build=health.build,
            config_hash=health.config_hash,
            package=SUCCESSOR_PACKAGE_ID,
        ),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
        status_error=None,
    )
    client.async_stage_apk.return_value = _preview(package=SUCCESSOR_PACKAGE_ID)
    _restart_into(entity, client, 772, package=LEGACY_PACKAGE_ID)
    monkeypatch.setattr(panel_update, "_RESTART_HEALTH_GRACE_SECONDS", 0.01)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "update_did_not_return")
    client.async_commit_apk.assert_awaited_once()


async def test_install_delivers_the_newest_build_in_order(
    hass: HomeAssistant, delivery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backup, store, stage verified bytes, commit, then wait for exactly 772."""
    entity, client = _entity(hass)
    calls: list[Any] = []
    stored: list[Path] = []

    async def backup() -> bytes:
        calls.append("backup")
        return BACKUP

    async def store(*args: Any) -> object:
        calls.append("store")
        receipt = await async_store_panel_backup(*args)
        stored.append(receipt.path)
        return receipt

    async def stage(apk: bytes) -> StagedApk:
        assert len(stored) == 1
        assert stored[0].read_bytes() == BACKUP
        receipt_path = stored[0].with_name(f"{stored[0].name}.json")
        receipt = json.loads(receipt_path.read_text())
        assert receipt.pop("taken_at").endswith("Z")
        assert receipt == {
            "archive": stored[0].name,
            "size": len(BACKUP),
            "sha256": hashlib.sha256(BACKUP).hexdigest(),
            "entries": 2,
        }
        calls.append(("stage", apk))
        return _preview()

    async def commit(token: str) -> None:
        calls.append(("commit", token))

    client.async_backup_panel = AsyncMock(side_effect=backup)
    client.async_stage_apk = AsyncMock(side_effect=stage)
    client.async_commit_apk = AsyncMock(side_effect=commit)
    monkeypatch.setattr(panel_update, "async_store_panel_backup", store)
    _restart_into(entity, client, 772, calls)

    await entity.async_install(None, False)

    assert calls == [
        "backup",
        "store",
        ("stage", APK),
        ("commit", "tok-1"),
        "refresh",
        "diag",
    ]
    delivery.download.assert_not_awaited()
    assert len(stored) == 1
    assert stored[0].parent == Path(hass.config.path(DOMAIN, "backups"))
    assert stored[0].name.startswith("entry-id-")
    assert stored[0].name.endswith("-vc771.zip")
    assert stored[0].read_bytes() == BACKUP
    assert entity._installed_code == 772
    assert entity.installed_version == "0.9.7-rc4 build 772"
    assert entity.in_progress is False
    client.async_discard_apk.assert_not_awaited()


async def test_install_a_specific_older_build(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A named build installs even when it is older than the running one."""
    entity, client = _entity(hass)
    _restart_into(entity, client, 770)

    await entity.async_install("770", False)

    assert delivery.download.await_args.args[1].descriptor.version_code == 770
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    assert entity._installed_code == 770
    assert entity.in_progress is False


async def test_install_refreshes_the_feed_to_find_a_newly_published_build(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A build missing from the cached feed is looked up once more after a refresh."""
    entity, client = _entity(hass)
    feed = entity._feed
    assert feed is not None

    async def publish() -> None:
        feed.data = _feed_data(770, 771, 772, 773)

    feed.async_refresh = AsyncMock(side_effect=publish)  # type: ignore[method-assign]
    _restart_into(entity, client, 773)

    await entity.async_install("0.9.7-rc4 build 773", False)

    feed.async_refresh.assert_awaited_once()
    assert delivery.download.await_args.args[1].descriptor.version_code == 773
    assert entity._installed_code == 773


async def test_install_refuses_a_build_still_missing_after_one_refresh(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """An unknown build refreshes the feed exactly once, then is unavailable."""
    entity, client = _entity(hass)
    feed = entity._feed
    assert feed is not None
    feed.async_refresh = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install("999", False)

    _assert_translated(error.value, "update_unavailable")
    feed.async_refresh.assert_awaited_once()
    client.async_backup_panel.assert_not_awaited()
    client.async_get_version_code.assert_not_awaited()
    delivery.download.assert_not_awaited()
    assert entity.in_progress is False


async def test_install_refuses_a_missing_build_when_the_refresh_fails(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A failed refresh never turns stale or absent data into an install."""
    entity, client = _entity(hass)
    feed = entity._feed
    assert feed is not None

    async def fail() -> None:
        feed.data = _feed_data(770, 771, 772, 773)
        feed.last_update_success = False

    feed.async_refresh = AsyncMock(side_effect=fail)  # type: ignore[method-assign]

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install("773", False)

    _assert_translated(error.value, "update_unavailable")
    client.async_backup_panel.assert_not_awaited()


@pytest.mark.parametrize(
    ("version", "backup"),
    [
        pytest.param("771", False, id="already-installed"),
        pytest.param("0.9.7-rc4 build 771", False, id="already-installed-label"),
        pytest.param(None, True, id="backup-requested"),
        pytest.param("772", True, id="backup-requested-specific"),
    ],
)
async def test_install_refuses_without_contacting_anything(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    version: str | None,
    backup: bool,
) -> None:
    """The installed build and HA-side backups are never offered."""
    entity, client = _entity(hass)
    feed = entity._feed
    assert feed is not None
    feed.async_refresh = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(version, backup)

    _assert_translated(error.value, "update_unavailable")
    feed.async_refresh.assert_not_awaited()
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_get_version_code.assert_not_awaited()
    delivery.download.assert_not_awaited()
    assert not delivery.backups.exists()


@pytest.mark.parametrize(
    "preview",
    [
        pytest.param(_preview(signer="0" * 64), id="wrong-signer"),
        pytest.param(_preview(package="io.github.other"), id="wrong-package"),
        pytest.param(_preview(version="0.9.7-rc3"), id="wrong-version"),
    ],
)
async def test_preview_mismatch_discards_and_never_commits(
    hass: HomeAssistant, delivery: SimpleNamespace, preview: StagedApk
) -> None:
    """The panel's own reading of the upload must match the signed build."""
    entity, client = _entity(hass)
    client.async_stage_apk = AsyncMock(return_value=preview)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "staged_app_mismatch")
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()
    assert entity.in_progress is False


async def test_identity_change_during_upload_refuses_commit(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A panel that changed app identity after selection cannot take the staged APK."""
    entity, client = _entity(hass)

    async def stage(_apk: bytes) -> StagedApk:
        snapshot = entity.coordinator.data
        assert snapshot is not None
        health = snapshot.health
        entity.coordinator.data = PanelSnapshot(
            health=PanelHealth(
                version=health.version,
                panel_id=health.panel_id,
                build=health.build,
                config_hash=health.config_hash,
                package=SUCCESSOR_PACKAGE_ID,
            ),
            status=snapshot.status,
            status_error=snapshot.status_error,
        )
        return _preview()

    client.async_stage_apk = AsyncMock(side_effect=stage)
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "panel_changed_during_update")
    client.async_backup_panel.assert_awaited_once()
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()


@pytest.mark.parametrize("package", [None, SUCCESSOR_PACKAGE_ID])
async def test_a_build_for_another_app_id_is_refused_before_backup(
    hass: HomeAssistant, delivery: SimpleNamespace, package: str | None
) -> None:
    """A legacy build is never installed beside a panel running the successor."""
    feed = _feed_data(772)
    if package is None:
        feed = replace(
            feed, builds=(replace(feed.builds[0], package_id=SUCCESSOR_PACKAGE_ID),)
        )
    entity, client = _entity(hass, feed=feed)
    health = entity.coordinator.data.health
    entity.coordinator.data = PanelSnapshot(
        health=PanelHealth(
            version=health.version,
            panel_id=health.panel_id,
            build=health.build,
            config_hash=health.config_hash,
            package=package,
        ),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
        status_error=None,
    )

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "update_unavailable")
    client.async_backup_panel.assert_not_awaited()
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


@pytest.mark.parametrize("package", [None, SUCCESSOR_PACKAGE_ID])
async def test_delivery_refuses_an_identity_change_before_backup(
    hass: HomeAssistant, delivery: SimpleNamespace, package: str | None
) -> None:
    """The shared delivery seam refuses a selection from before a handover."""
    entity, client = _entity(hass)
    selected = feed_release_artifact(
        _build(
            772,
            package_id=SUCCESSOR_PACKAGE_ID if package is None else LEGACY_PACKAGE_ID,
        )
    )
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot, health=replace(snapshot.health, package=package)
    )
    with pytest.raises(HomeAssistantError) as error:
        await entity._async_deliver_build(selected)
    _assert_translated(error.value, "panel_changed_during_update")
    client.async_backup_panel.assert_not_awaited()
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()


async def test_download_failure_never_stages(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """Bytes that do not match the signed feed never reach the panel."""
    entity, client = _entity(hass)
    delivery.download.side_effect = BuildFeedError

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install("770", False)

    _assert_translated(error.value, "build_verification_failed")
    client.async_backup_panel.assert_awaited_once()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    assert entity.in_progress is False


async def test_backup_failure_never_downloads(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """No backup, no change: the download is never started."""
    entity, client = _entity(hass)
    client.async_backup_panel = AsyncMock(side_effect=CannotConnectError)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "panel_backup_failed")
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    assert entity.in_progress is False


async def test_backup_store_failure_never_downloads(
    hass: HomeAssistant, delivery: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup Home Assistant could not keep counts as no backup."""
    entity, _ = _entity(hass)
    monkeypatch.setattr(
        panel_update, "async_store_panel_backup", AsyncMock(side_effect=OSError)
    )

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "panel_backup_failed")
    delivery.download.assert_not_awaited()


async def test_backup_needing_approval_asks_for_it(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A Hardened panel's approval request is surfaced, not treated as a failure."""
    entity, client = _entity(hass)
    client.async_backup_panel = AsyncMock(side_effect=UpdateApprovalRequiredError)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "update_approval_required")
    delivery.download.assert_not_awaited()


async def test_upload_disabled_is_reported(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A panel that refuses uploads says so."""
    entity, client = _entity(hass)
    client.async_stage_apk = AsyncMock(side_effect=UploadDisabledError)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "upload_disabled")
    client.async_commit_apk.assert_not_awaited()
    assert entity.in_progress is False


async def test_stage_503_refusal_reports_one_named_actionable_error(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    hass_client: Any,
    hass_ws_client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A feed stage refusal stays translated and clean through both HA APIs."""
    entity, client = _entity(hass)
    entity._title = "Example panel"
    staging = HaPaneldClient(MagicMock(), normalize_address("panel.local"))
    staging._async_post_bounded = AsyncMock(return_value=(503, b""))  # type: ignore[method-assign]
    client.async_stage_apk = staging.async_stage_apk

    with pytest.raises(ServiceValidationError) as error:
        await entity.async_install(None, False)

    assert "Example panel" in str(error.value)
    assert "Install tab" in str(error.value)
    assert isinstance(error.value, HTTPBadRequest)
    _assert_translated(error.value, "update_rejected")
    assert error.value.translation_placeholders == {"panel": "Example panel"}
    staging._async_post_bounded.assert_awaited_once()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert entity.in_progress is False

    async def install_service(_call: Any) -> None:
        await entity.async_install(None, False)

    hass.services.async_register("test_refusal", "install", install_service)
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "api", {})
    assert await async_setup_component(hass, "websocket_api", {})
    caplog.clear()
    api = await hass_client()
    rest = await api.post("/api/services/test_refusal/install", json={})
    assert rest.status == 400
    assert "Example panel" in await rest.text()

    websocket = await hass_ws_client(hass)
    await websocket.send_json_auto_id(
        {"type": "call_service", "domain": "test_refusal", "service": "install"}
    )
    response = await websocket.receive_json()
    assert response["error"]["code"] == "service_validation_error"
    assert response["error"]["translation_domain"] == DOMAIN
    assert response["error"]["translation_key"] == "update_rejected"
    assert response["error"]["translation_placeholders"] == {"panel": "Example panel"}
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "Example panel" in errors[0].message
    assert errors[0].exc_info is None


async def test_wait_refuses_a_restart_into_a_different_build(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A restarted app with the wrong build number is not a completed update."""
    entity, client = _entity(hass)
    _restart_into(entity, client, 771)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "update_not_complete")
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    assert entity._installed_code == 771
    assert entity.in_progress is False


async def test_manual_update_also_rereads_the_feed(hass: HomeAssistant) -> None:
    """Asking the entity to update refreshes panel health, then the feed."""
    entity, _ = _entity(hass)
    feed = entity._feed
    assert feed is not None
    order: list[str] = []
    entity.coordinator.async_request_refresh = AsyncMock(
        side_effect=lambda: order.append("health")
    )
    feed.async_refresh = AsyncMock(side_effect=lambda: order.append("feed"))  # type: ignore[method-assign]

    await entity.async_update()

    assert order == ["health", "feed"]


# --- reading the running build number ---------------------------------------


async def test_refresh_reads_diag_once_per_install(hass: HomeAssistant) -> None:
    """The build number is read once per (version, build) and not on every poll."""
    entity, client = _entity(hass, installed_code=None)
    client.async_get_version_code = AsyncMock(return_value=(NAME, 771))

    entity._refresh_installed_code()
    entity._refresh_installed_code()
    assert entity._code_task is not None
    await entity._code_task
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    assert client.async_get_version_code.await_count == 1
    assert entity._installed_code == 771
    assert entity._code_key == (NAME, "1000")

    entity.coordinator.data = PanelSnapshot(
        health=_health(NAME, build="2000"),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
        status_error=None,
    )
    client.async_get_version_code.return_value = (NAME, 772)
    entity._handle_coordinator_update()
    await entity._code_task

    assert client.async_get_version_code.await_count == 2
    assert entity._installed_code == 772


async def test_refresh_ignores_a_diag_for_another_version(
    hass: HomeAssistant,
) -> None:
    """A build number read across a version change is not latched, and the
    same app is not asked again on every poll."""
    entity, client = _entity(hass, installed_code=None)
    client.async_get_version_code = AsyncMock(return_value=("0.9.7-rc3", 707))

    entity._refresh_installed_code()
    assert entity._code_task is not None
    await entity._code_task

    health = entity.coordinator.data.health
    assert entity._installed_code is None
    assert entity._code_key == (health.version, health.build)
    entity._refresh_installed_code()
    await asyncio.sleep(0)
    assert client.async_get_version_code.await_count == 1


# --- coordinator and YAML ----------------------------------------------------


async def test_feed_coordinator_turns_a_bad_feed_into_a_failed_read(
    hass: HomeAssistant,
) -> None:
    """An unauthenticated feed marks the read failed rather than raising."""
    coordinator = BuildFeedCoordinator(hass, FEED_URL)

    with patch(FETCH, AsyncMock(side_effect=BuildFeedError)) as fetch:
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert fetch.await_args.args[1] == FEED_URL


def _patched_setup(fetch: AsyncMock) -> Any:
    return patch.multiple(
        "custom_components.panel_assistant",
        async_register_browser_delivery=MagicMock(),
        async_register_browser_panel=AsyncMock(),
    ), patch(FETCH, fetch)


async def test_yaml_build_feed_creates_one_coordinator(hass: HomeAssistant) -> None:
    """The one YAML key stores a feed coordinator and reads the feed once."""
    assert await async_setup_component(hass, "http", {})
    config = {DOMAIN: {"build_feed": "https://x/maintainer.json"}}
    assert CONFIG_SCHEMA(config) == config
    fetch = AsyncMock(return_value=_feed_data(772))
    download = AsyncMock(return_value=APK)
    browser, feed = _patched_setup(fetch)

    with (
        browser,
        feed,
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            download,
        ),
    ):
        assert await async_setup(hass, config)
        await hass.async_block_till_done(wait_background_tasks=True)

    coordinator = hass.data[DOMAIN]["build_feed"]
    assert isinstance(coordinator, BuildFeedCoordinator)
    assert async_get_feed_coordinator(hass) is coordinator
    assert coordinator.feed_url == URL("https://x/maintainer.json")
    fetch.assert_awaited_once()
    download.assert_awaited_once()
    assert fetch.await_args.args[1] == URL("https://x/maintainer.json")
    assert coordinator.data == _feed_data(772)


async def test_yaml_invalid_build_feed_is_logged_and_ignored(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A plaintext feed URL is refused loudly and nothing is fetched."""
    assert await async_setup_component(hass, "http", {})
    fetch = AsyncMock()
    browser, feed = _patched_setup(fetch)

    with browser, feed, caplog.at_level(logging.ERROR):
        assert await async_setup(
            hass, {DOMAIN: {"build_feed": "http://x/maintainer.json"}}
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert "build_feed" not in hass.data.get(DOMAIN, {})
    assert async_get_feed_coordinator(hass) is None
    assert any(
        record.levelno == logging.ERROR and "build_feed" in record.getMessage()
        for record in caplog.records
    )
    fetch.assert_not_awaited()


@pytest.mark.parametrize("config", [{}, {DOMAIN: {}}])
async def test_no_yaml_means_no_feed(
    hass: HomeAssistant, config: dict[str, Any]
) -> None:
    """Without the key there is no coordinator and no fetch."""
    assert await async_setup_component(hass, "http", {})
    fetch = AsyncMock()
    browser, feed = _patched_setup(fetch)

    with browser, feed:
        assert await async_setup(hass, config)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert async_get_feed_coordinator(hass) is None
    fetch.assert_not_awaited()


def _setup_patches(version_code: AsyncMock) -> Any:
    executor = SimpleNamespace(
        async_reconcile_entry=AsyncMock(),
    )
    client = "custom_components.panel_assistant.client.HaPaneldClient"
    return (
        patch(f"{client}.async_get_health", AsyncMock(return_value=_health(NAME))),
        patch(
            f"{client}.async_get_status",
            AsyncMock(
                return_value=PanelStatus(
                    warning_count=0, capability_count=0, install_capability="api"
                )
            ),
        ),
        patch(f"{client}.async_get_version_code", version_code),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    )


def _update_state(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    update = next(
        item
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry.entry_id and item.domain == "update"
    )
    return hass.states.get(update.entity_id)


async def test_stable_setup_never_reads_a_feed(hass: HomeAssistant) -> None:
    """A panel set up without YAML never fetches a feed or reads diagnostics."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"})
    entry.add_to_hass(hass)
    version_code = AsyncMock(return_value=(NAME, 771))
    patches = _setup_patches(version_code)
    fetch = AsyncMock()

    with ExitStack() as stack:
        for context in (patch(FETCH, fetch), *patches):
            stack.enter_context(context)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        state = _update_state(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)

    fetch.assert_not_awaited()
    version_code.assert_not_awaited()
    assert state.attributes["installed_version"] == NAME
    assert not (
        state.attributes["supported_features"] & UpdateEntityFeature.SPECIFIC_VERSION
    )


async def test_yaml_feed_reaches_the_panel_update_entity(hass: HomeAssistant) -> None:
    """With the YAML feed, a set-up panel shows and offers builds by number."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"})
    entry.add_to_hass(hass)
    version_code = AsyncMock(return_value=(NAME, 771))
    patches = _setup_patches(version_code)
    fetch = AsyncMock(return_value=_feed_data(770, 771, 772))
    download = AsyncMock(return_value=APK)

    with ExitStack() as stack:
        for context in (
            patch(FETCH, fetch),
            patch(
                "custom_components.panel_assistant.feed_coordinator.async_download_build",
                download,
            ),
            *patches,
        ):
            stack.enter_context(context)
        assert await async_setup_component(
            hass, DOMAIN, {DOMAIN: {"build_feed": str(FEED_URL)}}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        state = _update_state(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)

    fetch.assert_awaited()
    download.assert_awaited_once()
    version_code.assert_awaited_once()
    assert state.attributes["installed_version"] == "0.9.7-rc4 build 771"
    assert state.attributes["latest_version"] == "0.9.7-rc4 build 772"
    assert state.state == "on"
    assert state.attributes["supported_features"] & (
        UpdateEntityFeature.SPECIFIC_VERSION
    )


@pytest.mark.parametrize("error", [BuildDownloadError, BuildFeedError])
async def test_yaml_feed_does_not_offer_an_unverified_build(
    hass: HomeAssistant, error: type[BuildFeedError]
) -> None:
    """A missing or mismatched APK makes a signed feed entry unofferable."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"})
    entry.add_to_hass(hass)
    version_code = AsyncMock(return_value=(NAME, 771))
    fetch = AsyncMock(return_value=_feed_data(770, 771, 772))
    download = AsyncMock(side_effect=error)

    with ExitStack() as stack:
        for context in (
            patch(FETCH, fetch),
            patch(
                "custom_components.panel_assistant.feed_coordinator.async_download_build",
                download,
                create=True,
            ),
            *_setup_patches(version_code),
        ):
            stack.enter_context(context)
        assert await async_setup_component(
            hass, DOMAIN, {DOMAIN: {"build_feed": str(FEED_URL)}}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        state = _update_state(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)

    assert state.attributes["installed_version"] == "0.9.7-rc4 build 771"
    assert state.attributes["latest_version"] == state.attributes["installed_version"]
    assert state.state == "off"
    download.assert_awaited_once()


async def test_feed_offer_uses_the_verified_apk_after_its_origin_disappears(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """The install uses the bytes checked for the offer, not a second fetch."""
    entity, client = _entity(hass)
    assert entity._feed is not None
    entity._feed._verified_newest.clear()
    fetch = AsyncMock(return_value=_feed_data(770, 771, 772))
    verified_download = AsyncMock(return_value=APK)
    with (
        patch(FETCH, fetch),
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            verified_download,
        ),
    ):
        await entity._feed.async_refresh()
        verified_download.side_effect = BuildDownloadError
        await entity._feed.async_refresh()

    assert entity.latest_version == "0.9.7-rc4 build 772"
    delivery.download.side_effect = BuildDownloadError
    _restart_into(entity, client, 772)
    await entity.async_install(None, False)

    verified_download.assert_awaited_once()
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_awaited_once_with(APK)
    client.async_commit_apk.assert_awaited_once()


async def test_new_unverifiable_feed_head_withdraws_the_old_offer(
    hass: HomeAssistant,
) -> None:
    """A cached older APK cannot keep an obsolete offer alive."""
    entity, _ = _entity(hass)
    assert entity.latest_version == "0.9.7-rc4 build 772"
    assert entity._feed is not None
    with (
        patch(FETCH, AsyncMock(return_value=_feed_data(770, 771, 773))),
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            AsyncMock(side_effect=BuildDownloadError),
        ),
    ):
        await entity._feed.async_refresh()

    assert entity.latest_version == entity.installed_version


async def test_unverified_feed_head_refuses_unversioned_install(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """The service's default target is exactly the build eligible for offer."""
    entity, client = _entity(hass)
    assert entity._feed is not None
    entity._feed._verified_newest.clear()
    delivery.download.side_effect = BuildDownloadError

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, False)

    _assert_translated(error.value, "update_unavailable")
    client.async_backup_panel.assert_not_awaited()
    delivery.download.assert_not_awaited()


# --- backup store ------------------------------------------------------------


async def test_backup_store_keeps_the_newest_five_private_and_atomic(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Five newest per panel survive, files are 0600, no partial file remains."""
    hass.config.config_dir = str(tmp_path)
    directory = tmp_path / DOMAIN / "backups"
    directory.mkdir(parents=True)
    older = [
        directory / f"entry-id-2000010{day}T000000Z-vc700.zip" for day in range(1, 6)
    ]
    for path in older:
        path.write_bytes(b"old")
    other = directory / "other-entry-20000101T000000Z-vc1.zip"
    other.write_bytes(b"other")

    receipt = await async_store_panel_backup(hass, "entry-id", 771, BACKUP)
    path = receipt.path

    assert path.parent == directory
    assert path.name.endswith("-vc771.zip")
    assert path.read_bytes() == BACKUP
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    kept = sorted(p.name for p in directory.glob("entry-id-*.zip"))
    assert kept == sorted([p.name for p in older[1:]] + [path.name])
    assert not older[0].exists()
    assert other.exists()
    assert not [p for p in directory.iterdir() if p.name.endswith(".partial")]


async def test_backup_store_creates_a_private_directory(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The first backup creates an owner-only directory; unknown codes are named."""
    hass.config.config_dir = str(tmp_path)

    receipt = await async_store_panel_backup(hass, "entry-id", None, BACKUP)
    path = receipt.path

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.name.endswith("-vcunknown.zip")
    assert sorted(p.name for p in path.parent.iterdir()) == sorted(
        [path.name, f"{path.name}.json"]
    )


async def test_a_backup_is_proved_readable_before_it_is_kept(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Existence is not proof: the receipt records what was actually checked."""
    hass.config.config_dir = str(tmp_path)

    receipt = await async_store_panel_backup(hass, "entry-id", 771, BACKUP)

    assert receipt.path.read_bytes() == BACKUP
    assert receipt.size == len(BACKUP)
    assert receipt.sha256 == hashlib.sha256(BACKUP).hexdigest()
    assert receipt.entries == 2
    assert receipt.taken_at.endswith("Z")
    written = json.loads(
        (receipt.path.parent / f"{receipt.path.name}.json").read_text()
    )
    assert written == {
        "archive": receipt.path.name,
        "size": len(BACKUP),
        "sha256": hashlib.sha256(BACKUP).hexdigest(),
        "entries": 2,
        "taken_at": receipt.taken_at,
    }


@pytest.mark.parametrize(
    ("data", "why"),
    [
        pytest.param(b"", "empty", id="empty"),
        pytest.param(b"not a zip at all", "not an archive", id="not-a-zip"),
        pytest.param(BACKUP[: len(BACKUP) // 2], "truncated", id="truncated"),
        pytest.param(_archive(manifest=False), "no manifest", id="no-manifest"),
    ],
)
async def test_an_unusable_backup_is_never_written_or_counted(
    hass: HomeAssistant, tmp_path: Path, data: bytes, why: str
) -> None:
    """A copy can fail, truncate or arrive empty while still producing a file."""
    hass.config.config_dir = str(tmp_path)

    with pytest.raises(PanelBackupInvalidError):
        await async_store_panel_backup(hass, "entry-id", 771, data)

    directory = tmp_path / DOMAIN / "backups"
    assert not directory.exists() or not list(directory.iterdir()), why


async def test_a_manifest_with_no_payload_is_still_a_backup(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A panel with no file-backed state sends a manifest and nothing else."""
    hass.config.config_dir = str(tmp_path)

    receipt = await async_store_panel_backup(hass, "entry-id", 771, _archive(entries=0))

    assert receipt.entries == 1
    assert receipt.path.exists()


async def test_an_unreadable_backup_stops_the_update_before_anything_downloads(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
) -> None:
    """The app that holds the only copy of these settings is not replaced."""
    entity, client = _entity(hass)
    client.async_backup_panel = AsyncMock(return_value=b"not a zip at all")

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, False)

    _assert_translated(caught.value, "panel_backup_failed")
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


async def test_a_panel_state_capture_error_stops_the_update_before_download(
    hass: HomeAssistant, delivery: SimpleNamespace
) -> None:
    """A readable ZIP with failed app-state capture cannot guard an update."""
    entity, client = _entity(hass)
    client.async_backup_panel = AsyncMock(return_value=_archive(state_error=True))

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, False)

    _assert_translated(caught.value, "panel_backup_failed")
    delivery.download.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


async def test_feed_skips_unknown_and_incompatible_heads_before_download(
    hass: HomeAssistant,
) -> None:
    """An unsupported newest build cannot hide the next compatible update."""
    feed = BuildFeed(
        "maintainer",
        (
            replace(_build(775), protocol_min=None, protocol_max=None),
            replace(_build(774), protocol_min=4, protocol_max=4),
            _build(772),
        ),
    )
    entity, _ = _entity(hass, feed=feed)
    assert entity._feed is not None
    entity._feed._verified_newest.clear()
    download = AsyncMock(return_value=APK)
    with (
        patch(FETCH, AsyncMock(return_value=feed)),
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            download,
        ),
    ):
        await entity._feed.async_refresh()

    assert entity.latest_version == _build(772).label
    assert [
        call.args[1].descriptor.version_code for call in download.await_args_list
    ] == [772]


@pytest.mark.parametrize(
    ("pa_version", "opt_in", "expected_code"),
    [
        ("0.7.0", False, 772),
        ("0.7.0", True, 773),
        ("0.7.0-rc3", False, 773),
    ],
)
async def test_feed_channel_follows_loaded_pa_and_entry_opt_in(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    pa_version: str,
    opt_in: bool,
    expected_code: int,
) -> None:
    from custom_components.panel_assistant import update_policy
    from custom_components.panel_assistant.const import CONF_PRERELEASE_PANEL_BUILDS

    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        options={CONF_PRERELEASE_PANEL_BUILDS: opt_in},
    )
    entry.add_to_hass(hass)
    final = replace(_build(772), version_name="0.9.10")
    rc = replace(_build(773), version_name="0.9.11-rc1")
    feed = BuildFeed("maintainer", (rc, final))
    entity, _ = _entity(hass, version="0.9.9", feed=feed)
    assert entity._feed is not None
    entity._feed._verified_newest.clear()
    with (
        patch(FETCH, AsyncMock(return_value=feed)),
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            AsyncMock(return_value=APK),
        ),
    ):
        await entity._feed.async_refresh()
    assert entity.latest_version == feed.find(expected_code, LEGACY_PACKAGE_ID).label


async def test_prerelease_channel_includes_newer_final_promotion(
    hass: HomeAssistant,
) -> None:
    final = replace(_build(772), version_name="0.9.7")
    feed = BuildFeed("maintainer", (final,))
    entity, _ = _entity(hass, feed=feed)
    assert entity.latest_version == final.label
    assert entity.version_is_newer("0.9.7", "0.9.7-rc4")


async def test_shared_feed_keeps_both_channels_for_both_app_identities(
    hass: HomeAssistant,
) -> None:
    builds = tuple(
        replace(_build(code, package), version_name=name)
        for package in (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID)
        for code, name in ((773, "0.9.11-rc1"), (772, "0.9.10"))
    )
    coordinator = BuildFeedCoordinator(hass, FEED_URL)
    download = AsyncMock(return_value=APK)
    with (
        patch(FETCH, AsyncMock(return_value=BuildFeed("maintainer", builds))),
        patch(
            "custom_components.panel_assistant.feed_coordinator.async_download_build",
            download,
        ),
    ):
        await coordinator.async_refresh()
    for package in (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID):
        stable = coordinator.verified_newest(package, allow_prerelease=False)
        testing = coordinator.verified_newest(package, allow_prerelease=True)
        assert stable is not None and stable.version_code == 772
        assert testing is not None and testing.version_code == 773
    # Each exact byte hash is fetched once even when both policy choices share it.
    assert download.await_count == 2


@pytest.mark.parametrize(
    ("candidate_version", "candidate_code", "minimum", "maximum", "pa_version"),
    [
        ("1.1.0", 773, None, None, "0.7.0-rc3"),
        ("1.1.0", 773, 4, 4, "0.7.0-rc3"),
        ("1.1.0-rc1", 773, 3, 3, "0.7.0"),
        ("1.0.0", 770, 3, 3, "0.7.0-rc3"),
        ("0.9.9", 773, 3, 3, "0.7.0-rc3"),
    ],
    ids=["unknown", "incompatible", "stable-channel", "older-code", "older-version"],
)
async def test_explicit_feed_install_refuses_unadmitted_candidate_before_backup(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    candidate_version: str,
    candidate_code: int,
    minimum: int | None,
    maximum: int | None,
    pa_version: str,
) -> None:
    from custom_components.panel_assistant import update_policy

    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    candidate = replace(
        _build(candidate_code),
        version_name=candidate_version,
        protocol_min=minimum,
        protocol_max=maximum,
    )
    entity, client = _entity(
        hass, version="1.0.0", feed=BuildFeed("maintainer", (candidate,))
    )
    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(str(candidate_code), False)
    assert caught.value.translation_key == "update_unavailable"
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    delivery.download.assert_not_awaited()


async def test_changed_running_pa_policy_discards_staged_prerelease_before_commit(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.panel_assistant import update_policy

    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0-rc3")
    entity, client = _entity(hass)

    async def stage(_apk: bytes) -> StagedApk:
        monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0")
        return _preview()

    client.async_stage_apk.side_effect = stage
    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, False)
    assert caught.value.translation_key == "update_unavailable"
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()


async def test_post_one_release_uses_native_version_code_without_feed_diagnostics(
    hass: HomeAssistant,
    delivery: SimpleNamespace,
) -> None:
    """A newer version label cannot permit a lower Android build number."""
    candidate = feed_release_artifact(replace(_build(772), version_name="1.1.0"))
    entity, client = _entity(
        hass, version="1.0.0", with_feed=False, installed_code=None
    )
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot,
        health=replace(snapshot.health, version_code=800),
    )
    entity._release = SimpleNamespace(artifact_for=lambda *_args, **_kwargs: candidate)
    assert entity.latest_version == entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, False)
    assert caught.value.translation_key == "update_unavailable"
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    delivery.download.assert_not_awaited()

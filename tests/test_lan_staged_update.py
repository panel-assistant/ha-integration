"""A stable update travels from Home Assistant to the panel over the LAN.

GitHub is faked at the HTTP boundary and the release is signed with a disposable
key, so the real resolver, signature checks and download run unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from zipfile import ZipFile

import pytest
from aiohttp import ClientConnectorError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from multidict import CIMultiDict
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from yarl import URL

from custom_components.panel_assistant import feed_coordinator, release
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import BuildDownloadError
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    NotABridgeError,
    PanelHealth,
    StagedApk,
    StagingUnavailableError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.status import PanelCachedUpdate, PanelStatus
from custom_components.panel_assistant.transport import async_get_sessions
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)

TAG = "v0.9.10"
VERSION = "0.9.10"
CODE = 780
APK = b"the exact signed release apk" * 64
ROOT = f"https://github.com/panel-assistant/android/releases/download/{TAG}"
APK_NAME = f"ha-paneld-{TAG}-manual-setup-required.apk"
DESCRIPTOR_NAME = f"ha-paneld-{TAG}-install.json"
# GitHub answers an asset URL with a redirect to its asset host.
ASSET_HOST_URL = "https://release-assets.githubusercontent.com/asset/1?sig=x"
OFFER = PanelCachedUpdate("0.9.9", VERSION, TAG)
SIGNER = release._RELEASE_SIGNER_CERTIFICATE_SHA256


class _Content:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, _limit: int) -> AsyncIterator[bytes]:
        yield self._body


@dataclass
class _Response:
    status: int
    body: bytes
    url: URL
    headers: CIMultiDict[str] = field(default_factory=CIMultiDict)
    history: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        self.content = _Content(self.body)

    @property
    def content_length(self) -> int:
        return len(self.body)

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class _GitHub:
    """Serve one signed release exactly as GitHub lays it out."""

    def __init__(
        self,
        key: rsa.RSAPrivateKey,
        *,
        apk: bytes = APK,
        signed_apk: bytes = APK,
        package_id: str = LEGACY_PACKAGE_ID,
        asset_status: int = 200,
        bridge: bytes | None = None,
        signed_bridge: bytes | None = None,
    ) -> None:
        apk_name = release.release_apk_name(TAG, package_id)
        sha = hashlib.sha256(signed_apk).hexdigest()
        checksum = f"{sha}  {apk_name}\n".encode()
        descriptor = (
            json.dumps(
                {
                    "schema": "io.github.maxlyth.hapaneld.install.v1",
                    "releaseTag": TAG,
                    "versionName": VERSION,
                    "versionCode": CODE,
                    "apkName": apk_name,
                    "apkSize": len(signed_apk),
                    "apkSha256": sha,
                    "packageId": package_id,
                    "signerCertificateSha256": SIGNER,
                    "minSdk": 26,
                    "supportedAbis": ["arm64-v8a", "armeabi-v7a"],
                    "databaseCompatibility": "hapaneld-db:v1:ha-paneld.db:11:14",
                    "launchComponent": release.launch_component_for(package_id),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        names = [
            apk_name,
            f"{apk_name}.sha256",
            f"{apk_name}.sha256.sig",
            DESCRIPTOR_NAME,
            f"{DESCRIPTOR_NAME}.sig",
        ]
        document = {
            "tag_name": TAG,
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": name, "browser_download_url": f"{ROOT}/{name}"}
                for name in names
            ],
        }
        bodies = {
            str(release._LATEST_RELEASE_URL): json.dumps(document).encode(),
            f"{ROOT}/{apk_name}.sha256": checksum,
            f"{ROOT}/{apk_name}.sha256.sig": _sign(key, checksum),
            f"{ROOT}/{DESCRIPTOR_NAME}": descriptor,
            f"{ROOT}/{DESCRIPTOR_NAME}.sig": _sign(key, descriptor),
            ASSET_HOST_URL: apk,
        }
        if bridge is not None:
            bridge_sha = hashlib.sha256(
                signed_bridge if signed_bridge is not None else bridge
            ).hexdigest()
            bridge_checksum = f"{bridge_sha}  {APK_NAME}\n".encode()
            for name, body in {
                APK_NAME: bridge,
                f"{APK_NAME}.sha256": bridge_checksum,
                f"{APK_NAME}.sha256.sig": _sign(key, bridge_checksum),
            }.items():
                document["assets"].append(
                    {"name": name, "browser_download_url": f"{ROOT}/{name}"}
                )
                bodies[f"{ROOT}/{name}"] = body
            bodies[str(release._LATEST_RELEASE_URL)] = json.dumps(document).encode()
            bodies[f"{release.ANDROID_RELEASES_API}/tags/{TAG}"] = json.dumps(
                document
            ).encode()
        self._responses = {
            url: _Response(
                asset_status if url == ASSET_HOST_URL else 200, body, URL(url)
            )
            for url, body in bodies.items()
        }
        self._responses[f"{ROOT}/{apk_name}"] = _Response(
            302,
            b"",
            URL(f"{ROOT}/{apk_name}"),
            CIMultiDict({"Location": ASSET_HOST_URL}),
        )
        self.requests: list[str] = []

    def get(self, url: URL, **_kwargs: Any) -> _Response:
        self.requests.append(str(url))
        return self._responses[str(url)]

    @property
    def apk_downloaded(self) -> bool:
        return ASSET_HOST_URL in self.requests


def _sign(key: rsa.RSAPrivateKey, payload: bytes) -> bytes:
    return key.sign(payload, padding.PKCS1v15(), hashes.SHA256())


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    """A disposable release key; production verification code is unchanged."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def backups_in_tmp(hass: HomeAssistant, tmp_path: Any) -> None:
    """Keep the pre-update panel backups out of the repository."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture
def trust(monkeypatch: pytest.MonkeyPatch, key: rsa.RSAPrivateKey) -> None:
    """Make the disposable key the one release key this test trusts."""
    monkeypatch.setattr(
        release,
        "_RELEASE_PUBLIC_KEY_PEM",
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def _preview(**replacements: str) -> StagedApk:
    values = {
        "token": "tok-1",
        "package": LEGACY_PACKAGE_ID,
        "version": VERSION,
        "signer": SIGNER,
    }
    values.update(replacements)
    return StagedApk(**values)


def _snapshot(version: str, build: str, package: str | None) -> PanelSnapshot:
    return PanelSnapshot(
        health=PanelHealth(
            version=version,
            panel_id="alpha",
            build=build,
            config_hash="1a2b3c4d",
            package=package,
            installation_identity=True,
        ),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
        status_error=None,
    )


async def _entity(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    github: _GitHub,
    *,
    offer: PanelCachedUpdate | None = None,
    package: str | None = None,
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    """A panel on 0.9.9 whose app restarts into 0.9.10 once an install starts."""
    monkeypatch.setattr(feed_coordinator, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(panel_update, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())
    client = SimpleNamespace(
        configuration_url="http://panel.local:8888",
        async_backup_panel=AsyncMock(return_value=_backup()),
        async_stage_apk=AsyncMock(return_value=_preview()),
        async_commit_apk=AsyncMock(),
        async_discard_apk=AsyncMock(),
        async_start_panel_update=AsyncMock(),
        async_get_panel_install_status=AsyncMock(),
        async_get_version_code=AsyncMock(return_value=(VERSION, CODE)),
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
        async_offer_installed_successor=AsyncMock(),
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = _snapshot("0.9.9", "1000", package)
    health.data = PanelSnapshot(
        health=health.data.health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="api",
            panel_assistant_update=offer,
        ),
        status_error=None,
    )
    started = False

    async def install_started(*_args: Any) -> None:
        nonlocal started
        started = True

    async def refresh() -> None:
        if started:
            health.data = _snapshot(VERSION, "2000", package)

    client.async_commit_apk.side_effect = install_started
    client.async_start_panel_update.side_effect = install_started
    health.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]
    updates = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    updates.data = PanelUpdateSnapshot(operation=None, error=None)
    updates.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    stable = StableReleaseCoordinator(hass)
    await stable.async_refresh()
    entity = HaPaneldUpdateEntity("entry-id", health, updates, None, release=stable)
    entity.hass = hass
    entity.entity_id = "update.panel_ha_paneld"
    entity.async_write_ha_state = MagicMock()
    return entity, client


def _backup() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", '{"discovery_id":"a"}')
        archive.writestr("payload.bin", b"state")
    return buffer.getvalue()


def _assert_translated(error: HomeAssistantError, key: str) -> None:
    assert error.translation_domain == DOMAIN
    assert error.translation_key == key


# --- the panel without internet ------------------------------------------------


async def test_a_panel_without_internet_is_offered_the_release_home_assistant_found(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """The panel offers nothing of its own; Home Assistant's release is offered."""
    entity, _ = await _entity(hass, monkeypatch, _GitHub(key))

    assert entity.installed_version == "0.9.9"
    assert entity.latest_version == VERSION


async def test_adb_only_panel_offers_host_release_despite_newer_panel_offer(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """An unreachable panel-only offer cannot hide an installable ADB release."""
    entity, client = await _entity(
        hass,
        monkeypatch,
        _GitHub(key),
        offer=PanelCachedUpdate("0.9.9", "0.9.11", "v0.9.11"),
    )
    snapshot = entity.coordinator.data
    entity.coordinator.data = PanelSnapshot(
        health=snapshot.health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="none",
            panel_assistant_update=snapshot.status.panel_assistant_update,
        ),
        status_error=None,
    )
    entity._adb_ready_key = entity._route_key()
    monkeypatch.setattr(
        entity,
        "_async_install_route",
        AsyncMock(return_value=(panel_update.ROUTE_ADB, object(), object())),
    )
    delivered = AsyncMock()
    monkeypatch.setattr(entity, "_async_deliver_adb", delivered)

    assert entity.latest_version == VERSION
    await entity.async_install(None, False)

    delivered.assert_awaited_once()
    assert delivered.await_args.args[0].tag == TAG
    client.async_start_panel_update.assert_not_awaited()


@pytest.mark.parametrize("offer", [None, OFFER], ids=["no-internet", "internet"])
async def test_a_stable_update_is_staged_by_home_assistant_not_fetched_by_the_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    offer: PanelCachedUpdate | None,
) -> None:
    """Download here, stage the exact bytes, commit; never ask the panel to fetch."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github, offer=offer)

    await entity.async_install(None, backup=False)

    assert github.apk_downloaded
    client.async_stage_apk.assert_awaited_once_with(APK)
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    client.async_start_panel_update.assert_not_awaited()
    assert entity.extra_state_attributes == {"update_route": "staged_by_home_assistant"}
    assert entity.installed_version == VERSION
    assert entity.in_progress is False


async def test_staged_commit_retries_only_the_unstarted_call(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A refused connection retries the same staged token without restaging."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    install_started = client.async_commit_apk.side_effect
    connect_error = CannotConnectError()
    connect_error.__cause__ = ClientConnectorError(
        SimpleNamespace(host="panel.local", port=8888, ssl=False),
        OSError(111, "Connection refused"),
    )
    attempts = 0

    async def commit(token: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise connect_error
        await install_started(token)

    client.async_commit_apk.side_effect = commit

    await entity.async_install(None, backup=False)

    client.async_stage_apk.assert_awaited_once_with(APK)
    assert client.async_commit_apk.await_args_list == [call("tok-1"), call("tok-1")]
    client.async_start_panel_update.assert_not_awaited()
    assert entity.installed_version == VERSION
    assert entity.in_progress is False


@pytest.mark.parametrize("route", ["staged", "panel"])
async def test_accepted_update_projects_restart_when_panel_disappears(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    route: str,
) -> None:
    """An accepted install makes the absent panel Restarting until it returns."""
    entity, client = await _entity(
        hass, monkeypatch, _GitHub(key), offer=OFFER, package=LEGACY_PACKAGE_ID
    )
    if route == "panel":
        client.async_stage_apk.side_effect = StagingUnavailableError
    entity.coordinator.last_update_success = True
    waiting_for_return = asyncio.Event()
    allow_return = asyncio.Event()
    refreshes = 0

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        if refreshes == 1:
            entity.coordinator.last_update_success = False
        else:
            waiting_for_return.set()
            await allow_return.wait()
            entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
            entity.coordinator.last_update_success = True

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]
    install = asyncio.create_task(entity.async_install(None, backup=False))
    sessions = async_get_sessions(hass)
    try:
        await asyncio.wait_for(waiting_for_return.wait(), 5)
        if route == "staged":
            client.async_commit_apk.assert_awaited_once_with("tok-1")
        else:
            client.async_start_panel_update.assert_awaited_once_with(TAG)
        notice = sessions.restart_notice("entry-id")
        assert notice is not None
        assert (notice.scope, notice.reason) == ("app", "update")
    finally:
        allow_return.set()
        await install
        sessions.clear_restart_notice("entry-id")


@pytest.mark.parametrize("route", ["update", "panel-download", "bridge-handover"])
async def test_an_update_reads_as_one_steady_sequence_through_its_restart(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    route: str,
) -> None:
    """Never Unavailable, a bar that only rises, and the new version only at the end."""
    move = route == "bridge-handover"
    # A wait that can never succeed fails in a second, not fourteen minutes.
    monkeypatch.setattr(panel_update, "_UPDATE_TIMEOUT_SECONDS", 1)
    github = (
        _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
        if move
        else _GitHub(key)
    )
    entity, client = await _entity(hass, monkeypatch, github)
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, None, False)
    )
    start = entity.installed_version
    shown: list[dict[str, Any]] = []

    def write() -> None:
        shown.append({"available": entity.available, **entity.state_attributes})

    entity.async_write_ha_state = write  # type: ignore[method-assign]
    coordinator = entity.coordinator
    coordinator.last_update_success = True
    restarted: PanelSnapshot | None = None
    answering = absent = accepted = 0

    async def stage(_apk: bytes, **kwargs: Any) -> StagedApk:
        migrating = kwargs.get("migration_sha256") is not None
        return _preview(
            package=SUCCESSOR_PACKAGE_ID if migrating else LEGACY_PACKAGE_ID
        )

    async def accept(*_args: Any) -> None:
        nonlocal restarted, absent, answering, accepted
        staged = client.async_stage_apk.await_args
        migrating = staged is not None and staged.kwargs.get("migration_sha256")
        restarted = _snapshot(
            VERSION,
            "3000" if migrating else "2000",
            SUCCESSOR_PACKAGE_ID if migrating else LEGACY_PACKAGE_ID,
        )
        # The old app answers while it installs the new one, then restarts.
        answering, absent, accepted = 1, 3, len(shown)

    async def refresh() -> None:
        # Each poll ends in the coordinator's listener write, as in Core.
        nonlocal absent, restarted, answering
        if answering:
            answering -= 1
        elif absent:
            absent -= 1
            coordinator.last_update_success = False
        else:
            # A panel that answers again ends its restart notice inside the
            # poll, and the session-change listener writes before the poll
            # is marked successful, as seen on a real panel.
            async_get_sessions(hass).clear_restart_notice("entry-id")
            entity._handle_coordinator_update()
            coordinator.last_update_success = True
            if restarted is not None:
                coordinator.data, restarted = restarted, None
        entity._handle_coordinator_update()

    ready = client.async_get_status.return_value
    builtin = {"mode": "builtin", "state": "unobserved", "rendered": False}
    unrendered = replace(ready, renderer=builtin)
    rendered = replace(ready, renderer={**builtin, "state": "rendered"})

    async def status(**_kwargs: Any) -> PanelStatus:
        # The new app's dashboard reads ready from its first status screen,
        # before the Home Assistant frontend connects.
        return unrendered if client.async_get_status.await_count % 2 else rendered

    client.async_get_status = AsyncMock(side_effect=status)
    client.async_stage_apk.side_effect = (
        StagingUnavailableError if route == "panel-download" else stage
    )
    client.async_commit_apk.side_effect = accept
    client.async_start_panel_update.side_effect = accept
    coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]
    try:
        await entity.async_install(None, backup=False)
    finally:
        async_get_sessions(hass).clear_restart_notice("entry-id")

    working = [state for state in shown if state["in_progress"]]
    assert any(not state["available"] for state in shown) is False
    restarting = [
        state["update_percentage"] for state in working if state["release_summary"]
    ]
    assert restarting
    # The last restart is the one into the target; a bridge's comes earlier.
    assert 50 <= restarting[-1] <= 75
    assert (restarting[0] < 50) is move
    progress = [state["update_percentage"] for state in working]
    assert None not in progress
    assert progress == sorted(progress)
    # Installing, restarting and loading the dashboard each get a share of
    # the bar, rather than the first half passing before the install starts.
    installing = [
        state["update_percentage"]
        for state in shown[accepted:]
        if state["in_progress"] and not state["release_summary"]
    ]
    assert any(20 <= value < 50 for value in installing) is not move
    assert progress[-1] >= 80
    assert {state["installed_version"] for state in working} == {start}
    assert {state["latest_version"] for state in working} == {VERSION}
    assert shown[-1]["in_progress"] is False
    assert shown[-1]["installed_version"] == VERSION
    assert client.async_stage_apk.await_count == (2 if move else 1)
    assert client.async_start_panel_update.await_count == (route == "panel-download")
    assert (
        client.async_get_status.await_count
        == {
            "update": 2,
            "panel-download": 2,
            "bridge-handover": 4,
        }[route]
    )
    if move:
        assert coordinator.data.health.package == SUCCESSOR_PACKAGE_ID


@pytest.mark.parametrize("refusal", [UploadDisabledError, StagingUnavailableError])
async def test_a_panel_that_cannot_take_an_upload_downloads_the_release_itself(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    refusal: type[Exception],
) -> None:
    """Only a panel that takes no upload at all falls back to its own download."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key), offer=OFFER)
    client.async_stage_apk.side_effect = refusal

    await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once_with(TAG)
    client.async_commit_apk.assert_not_awaited()
    assert entity.extra_state_attributes == {"update_route": "downloaded_by_panel"}
    assert entity.installed_version == VERSION


async def test_a_panel_already_past_the_release_is_offered_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """The release found here is never sent to a panel already past it."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot("0.9.11", "2000", None)

    assert entity.latest_version == entity.installed_version == "0.9.11"
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "update_unavailable")
    assert not github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()


async def test_a_newer_offer_from_the_panel_is_left_to_the_panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """When the panel already knows a newer release, that one is installed."""
    newer = PanelCachedUpdate("0.9.9", "0.9.11", "v0.9.11")
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github, offer=newer)

    client.async_start_panel_update.side_effect = UpdateBusyError

    assert entity.latest_version == "0.9.11"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, backup=False)

    client.async_start_panel_update.assert_awaited_once_with("v0.9.11")
    assert not github.apk_downloaded


async def test_refreshing_the_update_reads_the_latest_release_again(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A release published since the last check is found on a manual refresh."""
    github = _GitHub(key)
    entity, _ = await _entity(hass, monkeypatch, github)
    github.requests.clear()

    await entity.async_update()

    assert str(release._LATEST_RELEASE_URL) in github.requests


async def test_a_release_home_assistant_cannot_download_is_left_to_the_panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A download that fetched nothing checked nothing; the panel fetches it."""
    github = _GitHub(key, asset_status=503)
    entity, client = await _entity(hass, monkeypatch, github, offer=OFFER)

    await entity.async_install(None, backup=False)

    assert github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()
    client.async_start_panel_update.assert_awaited_once_with(TAG)
    assert entity.extra_state_attributes == {"update_route": "downloaded_by_panel"}


@pytest.mark.parametrize("offer", [None, PanelCachedUpdate("0.8.5", VERSION, TAG)])
async def test_a_panel_older_than_lan_updates_is_never_offered_what_it_cannot_take(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    offer: PanelCachedUpdate | None,
) -> None:
    """Before 0.8.6 a panel has no backup or upload; only its own offer stands."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github, offer=offer)
    entity.coordinator.data = PanelSnapshot(
        health=_snapshot("0.8.5", "1000", None).health,
        status=entity.coordinator.data.status,
        status_error=None,
    )
    client.async_start_panel_update.side_effect = UpdateBusyError

    if offer is None:
        assert entity.latest_version == entity.installed_version == "0.8.5"
        with pytest.raises(HomeAssistantError) as error:
            await entity.async_install(None, backup=False)
        _assert_translated(error.value, "update_unavailable")
        client.async_start_panel_update.assert_not_awaited()
    else:
        assert entity.latest_version == VERSION
        with pytest.raises(HomeAssistantError):
            await entity.async_install(None, backup=False)
        client.async_start_panel_update.assert_awaited_once_with(TAG)
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    assert not github.apk_downloaded


# --- verification is never a way into the other route --------------------------


async def test_dual_release_leaves_successor_updates_unchanged(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """An already-migrated panel receives only its usual signed successor APK."""
    bridge = b"signed bridge bytes"
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=bridge)
    entity, client = await _entity(
        hass, monkeypatch, github, package=SUCCESSOR_PACKAGE_ID
    )
    client.async_stage_apk.return_value = _preview(package=SUCCESSOR_PACKAGE_ID)

    assert entity.latest_version == VERSION
    await entity.async_install(None, backup=False)

    client.async_stage_apk.assert_awaited_once_with(APK)
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    client.async_start_panel_update.assert_not_awaited()
    assert entity.installed_version == VERSION


async def test_bridge_bytes_must_match_their_own_signed_checksum(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """A bad bridge never reaches upload, install or panel-download fallback."""
    github = _GitHub(
        key,
        package_id=SUCCESSOR_PACKAGE_ID,
        bridge=b"tampered bridge",
        signed_bridge=b"authentic bridge",
    )
    entity, client = await _entity(hass, monkeypatch, github)
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "release_verification_failed")
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


@pytest.mark.parametrize("resume_bridge", [False, True, "old_identity"])
@pytest.mark.parametrize("package", [None, LEGACY_PACKAGE_ID])
async def test_offline_move_delivers_both_verified_identities(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    resume_bridge: bool | str,
    package: str | None,
) -> None:
    """One update action completes the move, including a retry after bridge install."""
    bridge = b"bridge for offline move"
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=bridge)
    entity, client = await _entity(hass, monkeypatch, github, package=package)
    if resume_bridge:
        entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
        if resume_bridge == "old_identity":
            entity.coordinator.data = replace(
                entity.coordinator.data,
                health=replace(
                    entity.coordinator.data.health,
                    build="1500",
                    installation_identity=False,
                ),
            )
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, None, False)
    )
    stages = []

    async def stage(apk: bytes, **kwargs: Any) -> StagedApk:
        migrating = kwargs.get("migration_sha256") is not None
        assert apk == (APK if migrating else bridge)
        if migrating:
            assert entity.coordinator.data.health.version == VERSION
            client.async_get_successor_capability.assert_awaited()
        stages.append(SUCCESSOR_PACKAGE_ID if migrating else LEGACY_PACKAGE_ID)
        return _preview(package=stages[-1], token=f"token-{len(stages)}")

    async def commit(_token: str) -> None:
        # A sealed pair shares a build stamp; identity must prove the handover.
        entity.coordinator.data = _snapshot(VERSION, "2000", stages[-1])

    client.async_stage_apk.side_effect = stage
    client.async_commit_apk.side_effect = commit
    entity.coordinator.async_request_refresh = AsyncMock()

    if resume_bridge is True:
        await entity._async_refresh_route()
    assert entity.state == "on"
    await entity.async_install(None, backup=False)

    expected = [] if resume_bridge is True else [call(bridge)]
    expected.append(call(APK, migration_sha256=hashlib.sha256(APK).hexdigest()))
    assert client.async_stage_apk.await_args_list == expected
    assert entity.coordinator.data.health.package == SUCCESSOR_PACKAGE_ID
    client.async_start_panel_update.assert_not_awaited()
    assert entity.state == "off"


@pytest.mark.parametrize("installed_code", [CODE, CODE + 1])
async def test_retry_reuses_trusted_installed_successor_without_restaging(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    installed_code: int,
) -> None:
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, installed_code, False)
    )
    client.async_get_version_code.return_value = (VERSION, installed_code)

    async def resume() -> None:
        entity.coordinator.data = _snapshot(VERSION, "2000", SUCCESSOR_PACKAGE_ID)

    client.async_offer_installed_successor.side_effect = resume
    entity.coordinator.async_request_refresh = AsyncMock()

    await entity.async_install(None, backup=False)

    client.async_offer_installed_successor.assert_awaited_once()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert not github.apk_downloaded
    assert entity.coordinator.data.health.package == SUCCESSOR_PACKAGE_ID


async def test_retry_refuses_untrusted_installed_successor(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, None, True)
    )

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "successor_untrusted")
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_offer_installed_successor.assert_not_awaited()
    assert not github.apk_downloaded


async def test_not_a_bridge_refusal_withholds_handover_without_a_problem_report(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """A real capability 404 must never advertise a handover or blame signatures."""
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    response = MagicMock()
    response.status = 404
    session = MagicMock()
    session.get.return_value.__aenter__.return_value = response
    client.async_get_successor_capability = HaPaneldClient(
        session, normalize_address("panel.local")
    ).async_get_successor_capability

    recorded = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", recorded)

    await entity._async_refresh_route()
    assert entity.state == "off"
    assert entity.latest_version == entity.installed_version
    # Nothing newer exists and nothing is lost, so the owner is shown no problem.
    assert (
        panel_update.ROUTE_UNAVAILABLE_ATTRIBUTE
        not in entity._attr_extra_state_attributes
    )
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "bridge_not_ready")
    recorded.assert_not_awaited()
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()

    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, None, False)
    )
    entity._schedule_route_refresh()
    assert entity._route_task is not None
    await entity._route_task
    assert entity.state == "on"
    assert entity.latest_version == VERSION


async def test_installed_successor_handover_refusal_names_panel_action(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    entity, client = await _entity(
        hass,
        monkeypatch,
        _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge"),
    )
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, CODE, False)
    )
    client.async_offer_installed_successor.side_effect = UpdateRejectedError

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "bridge_handover_refused")
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


async def test_missing_release_download_is_not_called_a_bad_signature(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    monkeypatch.setattr(
        panel_update, "async_download_build", AsyncMock(side_effect=BuildDownloadError)
    )
    artifact = entity._host_release()
    assert artifact is not None

    with pytest.raises(HomeAssistantError) as error:
        await entity._async_deliver_build(artifact)
    _assert_translated(error.value, "release_download_failed")
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


@pytest.mark.parametrize(
    "capability",
    [
        None,
        (LEGACY_PACKAGE_ID, VERSION, None, False),
        (SUCCESSOR_PACKAGE_ID, "0.9.11", None, False),
    ],
)
async def test_successor_is_never_sent_without_matching_bridge_capability(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    capability: tuple[str, str, int | None, bool] | None,
) -> None:
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    client.async_get_successor_capability = AsyncMock(return_value=capability)
    if capability is None:
        client.async_get_successor_capability.side_effect = CannotConnectError
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(
        error.value,
        "bridge_capability_unavailable"
        if capability is None
        else "bridge_details_changed",
    )
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert not github.apk_downloaded


@pytest.mark.parametrize(
    "defect",
    ["checksum", "package", "signer", "version", "identity-race", "version-race"],
)
async def test_successor_verification_refusal_never_installs_or_downloads_on_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    defect: str,
) -> None:
    github = _GitHub(
        key,
        package_id=SUCCESSOR_PACKAGE_ID,
        bridge=b"bridge",
        apk=b"tampered successor" if defect == "checksum" else APK,
    )
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, VERSION, None, False)
    )
    replacements = {"package": SUCCESSOR_PACKAGE_ID}
    if defect in ("package", "signer", "version"):
        replacements[defect] = {
            "package": LEGACY_PACKAGE_ID,
            "signer": "0" * 64,
            "version": "0.9.11",
        }[defect]
    preview = _preview(**replacements)

    async def stage(_apk: bytes, **_kwargs: Any) -> StagedApk:
        if defect == "identity-race":
            entity.coordinator.data = _snapshot(VERSION, "2000", SUCCESSOR_PACKAGE_ID)
        if defect == "version-race":
            entity.coordinator.data = _snapshot("0.9.11", "3000", LEGACY_PACKAGE_ID)
        return preview

    client.async_stage_apk.side_effect = stage
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(
        error.value,
        "release_verification_failed"
        if defect == "checksum"
        else "panel_changed_during_update"
        if defect.endswith("race")
        else "staged_app_mismatch",
    )
    if defect == "checksum":
        client.async_stage_apk.assert_not_awaited()
    else:
        client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_a_bridge_that_already_handed_over_is_not_reinstalled(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)

    async def refresh() -> None:
        entity.coordinator.data = _snapshot(VERSION, "2000", SUCCESSOR_PACKAGE_ID)

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)
    await entity.async_install(None, backup=False)
    client.async_stage_apk.assert_awaited_once_with(b"bridge")
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    client.async_start_panel_update.assert_not_awaited()
    client.async_get_version_code.assert_awaited_once()
    assert entity.coordinator.data.health.package == SUCCESSOR_PACKAGE_ID


async def test_a_bridge_update_that_cannot_hand_over_yet_is_still_a_success(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """The newer bridge installed; the move to the new app waits without a repair."""
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    client.async_get_successor_capability = AsyncMock(side_effect=NotABridgeError)
    recorded = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", recorded)

    await entity.async_install(None, backup=False)
    client.async_stage_apk.assert_awaited_once_with(b"bridge")
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    recorded.assert_not_awaited()
    assert entity.coordinator.data.health.version == VERSION


@pytest.mark.parametrize(
    ("refusal", "translation", "recorded_failure"),
    [
        (NotABridgeError, "bridge_not_ready", False),
        (CannotConnectError, "bridge_capability_unavailable", True),
    ],
    ids=["not-ready", "unreachable"],
)
async def test_a_handover_refused_after_admission_opens_a_repair_only_for_a_fault(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    refusal: type[Exception],
    translation: str,
    recorded_failure: bool,
) -> None:
    """Ready when the press was admitted, refused once the install asks again."""
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", LEGACY_PACKAGE_ID)
    asked = 0

    async def capability() -> tuple[str, str, int | None, bool]:
        nonlocal asked
        asked += 1
        if asked > 1:
            raise refusal
        return (SUCCESSOR_PACKAGE_ID, VERSION, None, False)

    client.async_get_successor_capability = AsyncMock(side_effect=capability)
    recorded = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", recorded)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, translation)
    assert asked > 1
    assert recorded.await_count == (1 if recorded_failure else 0)
    client.async_commit_apk.assert_not_awaited()


async def test_bridge_label_does_not_hide_a_newer_panel_offer(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = PanelSnapshot(
        health=_snapshot(VERSION, "2000", LEGACY_PACKAGE_ID).health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="api",
            panel_assistant_update=PanelCachedUpdate(VERSION, "0.9.11", "v0.9.11"),
        ),
        status_error=None,
    )
    client.async_get_successor_capability = AsyncMock(side_effect=CannotConnectError)
    assert entity.installed_version == f"{VERSION} (bridge)"
    assert entity.latest_version == "0.9.11"
    assert entity.state == "on"

    async def panel_updated(_tag: str) -> None:
        entity.coordinator.data = _snapshot("0.9.11", "3000", LEGACY_PACKAGE_ID)

    client.async_start_panel_update.side_effect = panel_updated
    await entity.async_install("0.9.11", backup=False)
    client.async_start_panel_update.assert_awaited_once_with("v0.9.11")
    client.async_get_successor_capability.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()


@pytest.mark.parametrize(
    "preview",
    [
        _preview(package=SUCCESSOR_PACKAGE_ID),
        _preview(signer="0" * 64),
        _preview(version="0.9.11"),
    ],
    ids=["package", "signer", "version"],
)
async def test_bridge_stage_must_pass_the_same_identity_checks(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    preview: StagedApk,
) -> None:
    """A signed checksum never replaces the panel's APK identity verification."""
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"bridge")
    entity, client = await _entity(hass, monkeypatch, github)
    client.async_stage_apk.return_value = preview
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "staged_app_mismatch")
    client.async_stage_apk.assert_awaited_once_with(b"bridge")
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_identity_change_during_upload_refuses_commit(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """A concurrent handover cannot commit the old package onto the successor."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))

    async def stage(_apk: bytes) -> StagedApk:
        entity.coordinator.data = _snapshot("0.9.9", "2000", SUCCESSOR_PACKAGE_ID)
        return _preview()

    client.async_stage_apk.side_effect = stage
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "panel_changed_during_update")
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()


async def test_an_apk_that_does_not_match_its_signed_hash_is_refused_on_both_routes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """Tampered bytes are neither staged nor handed to the panel to fetch."""
    github = _GitHub(key, apk=APK[:-1] + b"X")
    entity, client = await _entity(hass, monkeypatch, github, offer=OFFER)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "release_verification_failed")
    assert github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    assert entity.in_progress is False


@pytest.mark.parametrize(
    "preview",
    [
        _preview(signer="0" * 64),
        _preview(package=SUCCESSOR_PACKAGE_ID),
        _preview(version="0.9.11"),
    ],
    ids=["other-signer", "other-package", "other-version"],
)
async def test_a_staged_app_that_is_not_the_signed_release_is_discarded_on_both_routes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    preview: StagedApk,
) -> None:
    """The panel's own reading of the staged file must be the signed release."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key), offer=OFFER)
    client.async_stage_apk.return_value = preview

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "staged_app_mismatch")
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_release_metadata_with_a_bad_signature_is_never_offered_or_downloaded(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None
) -> None:
    """Metadata signed by any other key gives Home Assistant nothing to send."""
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    github = _GitHub(impostor)
    entity, client = await _entity(hass, monkeypatch, github)

    assert entity.latest_version == entity.installed_version == "0.9.9"
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "update_unavailable")
    assert not github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "key_name"),
    [
        (UpdateBusyError, "update_busy"),
        (UpdateRejectedError, "update_rejected"),
        (CannotConnectError, "update_not_accepted"),
    ],
)
async def test_a_stage_that_fails_for_any_other_reason_never_falls_back(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    failure: type[Exception],
    key_name: str,
) -> None:
    """A busy, full or interrupted panel is an error, not a reason to switch routes."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key), offer=OFFER)
    client.async_stage_apk.side_effect = failure

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, key_name)
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()


async def test_a_release_for_another_app_id_is_left_to_the_panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A legacy panel never has the successor staged beside it as a second app."""
    github = _GitHub(key, package_id=SUCCESSOR_PACKAGE_ID)
    entity, client = await _entity(hass, monkeypatch, github, offer=OFFER)

    await entity.async_install(None, backup=False)

    assert not github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()
    client.async_start_panel_update.assert_awaited_once_with(TAG)


async def test_the_shared_release_outlives_the_panel_that_first_asked_for_it(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """Removing one panel never stops the release check the others rely on."""
    github = _GitHub(key)
    monkeypatch.setattr(feed_coordinator, "async_get_clientsession", lambda _h: github)
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    token = config_entries.current_entry.set(entry)
    try:
        coordinator = feed_coordinator.async_get_stable_release_coordinator(hass)
    finally:
        config_entries.current_entry.reset(token)
    await hass.async_block_till_done()
    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done()
    remove_listener = coordinator.async_add_listener(lambda: None)
    github.requests.clear()

    async_fire_time_changed(hass, dt_util.utcnow() + feed_coordinator.STABLE_REFRESH)
    await hass.async_block_till_done()

    remove_listener()
    assert str(release._LATEST_RELEASE_URL) in github.requests

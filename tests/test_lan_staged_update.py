"""A stable update travels from Home Assistant to the panel over the LAN.

GitHub is faked at the HTTP boundary and the release is signed with a disposable
key, so the real resolver, signature checks and download run unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call
from zipfile import ZipFile

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from aiohttp import ClientConnectorError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from multidict import CIMultiDict
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    mock_integration,
)
from yarl import URL

from custom_components.panel_assistant import (
    feed_coordinator,
    install_network,
    release,
    update_errors,
    update_panel_install,
    update_progress,
    update_route,
    update_staging,
)
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.adb_credentials import (
    AdbCredential,
    AdbCredentialError,
    AdbCredentialMissingError,
)
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    LEGACY_LAUNCH_COMPONENT,
    LEGACY_PACKAGE_ID,
    PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import BuildDownloadError
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    PanelInstallStatus,
    StagedApk,
    UpdateBusyError,
    UpdateRejectedError,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.repairs_failure import panel_failure_issue_id
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.transport_sessions import async_get_sessions
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)

from .http_fakes import FakeResponse, FakeSession
from .install_adb_fakes import _install_fakes, _preflight_output, _section
from .test_install_adb import signer  # noqa: F401
from .test_install_adb_grants import (
    _panel_value,
    _RoutingFakeDevice,
    _seed_granted_panel,
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
SIGNER = release._RELEASE_SIGNER_CERTIFICATE_SHA256


def _Response(
    status: int, body: bytes, url: URL, headers: CIMultiDict[str] | None = None
) -> FakeResponse:
    """Declare the body's own length, as GitHub does."""
    return FakeResponse(
        status, body, url, headers=headers or CIMultiDict(), declared_length=len(body)
    )


class _GitHub:
    """Serve one signed release exactly as GitHub lays it out."""

    def __init__(
        self,
        key: rsa.RSAPrivateKey,
        *,
        apk: bytes = APK,
        signed_apk: bytes = APK,
        package_id: str = PACKAGE_ID,
        asset_status: int = 200,
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
                    "launchComponent": LAUNCH_COMPONENTS.get(
                        package_id, (LEGACY_LAUNCH_COMPONENT,)
                    )[0],
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
        protocol_name = f"ha-paneld-{TAG}-protocol.json"
        records = [{"apkSha256": sha, "protocolMin": 5, "protocolMax": 5}]
        protocol = (
            json.dumps(
                {
                    "schema": "io.github.maxlyth.hapaneld.protocol.v1",
                    "artifacts": sorted(
                        records, key=lambda record: record["apkSha256"]
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for name, body in {
            protocol_name: protocol,
            f"{protocol_name}.sig": _sign(key, protocol),
        }.items():
            document["assets"].append(
                {"name": name, "browser_download_url": f"{ROOT}/{name}"}
            )
            bodies[f"{ROOT}/{name}"] = body
        bodies[str(release._LATEST_RELEASE_URL)] = json.dumps(document).encode()
        bodies[f"{release.ANDROID_RELEASES_API}/tags/{TAG}"] = json.dumps(
            document
        ).encode()
        bodies[f"{release.ANDROID_RELEASES_API}?per_page=30"] = b"[]"
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

    def get(self, url: URL, **_kwargs: Any) -> FakeResponse:
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
        "package": PACKAGE_ID,
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
            protocol=(5, 5),
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
    package: str | None = PACKAGE_ID,
) -> tuple[HaPaneldUpdateEntity, SimpleNamespace]:
    """A panel on 0.9.9 whose app restarts into 0.9.10 once an install starts."""
    monkeypatch.setattr(feed_coordinator, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(update_staging, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(panel_update.asyncio, "sleep", AsyncMock())
    client = SimpleNamespace(
        async_backup_panel=AsyncMock(return_value=_backup()),
        async_stage_apk=AsyncMock(return_value=_preview()),
        async_commit_apk=AsyncMock(),
        async_discard_apk=AsyncMock(),
        async_get_panel_install_status=AsyncMock(
            return_value=PanelInstallStatus(running=True, component="APK")
        ),
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
    )
    health = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    health.data = _snapshot("0.9.9", "1000", package)
    health.data = PanelSnapshot(
        health=health.data.health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="api",
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


async def test_adb_only_panel_offers_the_host_release(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A panel with no route of its own still gets the release over ADB."""
    entity, _ = await _entity(hass, monkeypatch, _GitHub(key))
    snapshot = entity.coordinator.data
    entity.coordinator.data = PanelSnapshot(
        health=snapshot.health,
        status=PanelStatus(
            warning_count=0,
            capability_count=0,
            install_capability="none",
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
    monkeypatch.setattr(entity._delivery, "async_deliver_adb", delivered)

    assert entity.latest_version == VERSION
    await entity.async_install(None, False)

    delivered.assert_awaited_once()
    assert delivered.await_args.args[1].tag == TAG


async def test_a_stable_update_is_staged_by_home_assistant_not_fetched_by_the_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """Download here, stage the exact bytes, commit; never ask the panel to fetch."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github)

    await entity.async_install(None, backup=False)

    assert github.apk_downloaded
    client.async_stage_apk.assert_awaited_once_with(APK)
    client.async_commit_apk.assert_awaited_once_with("tok-1")
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
    assert entity.installed_version == VERSION
    assert entity.in_progress is False


async def test_a_retried_commit_checks_the_offer_again(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A retry is a fresh command: an offer no longer allowed is not committed."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    connect_error = CannotConnectError()
    connect_error.__cause__ = ClientConnectorError(
        SimpleNamespace(host="panel.local", port=8888, ssl=False),
        OSError(111, "Connection refused"),
    )

    async def commit(_token: str) -> None:
        # The panel moved past the release while the first call went unanswered,
        # and from 1.0 an older release is never installed over a newer one.
        entity.coordinator.data = _snapshot("1.0.1", "3000", PACKAGE_ID)
        raise connect_error

    client.async_commit_apk.side_effect = commit

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "update_unavailable")
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    client.async_discard_apk.assert_awaited_once_with("tok-1")


async def test_accepted_update_projects_restart_when_panel_disappears(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """An accepted install makes the absent panel Restarting until it returns."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key), package=PACKAGE_ID)
    entity.coordinator.reachable = True
    waiting_for_return = asyncio.Event()
    allow_return = asyncio.Event()
    refreshes = 0

    async def refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        if refreshes == 1:
            entity.coordinator.reachable = False
        else:
            waiting_for_return.set()
            await allow_return.wait()
            entity.coordinator.data = _snapshot(VERSION, "2000", PACKAGE_ID)
            entity.coordinator.reachable = True

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]
    install = asyncio.create_task(entity.async_install(None, backup=False))
    sessions = async_get_sessions(hass)
    try:
        await asyncio.wait_for(waiting_for_return.wait(), 5)
        client.async_commit_apk.assert_awaited_once_with("tok-1")
        notice = sessions.restart_notice("entry-id")
        assert notice is not None
        assert (notice.scope, notice.reason) == ("app", "update")
    finally:
        allow_return.set()
        await install
        sessions.clear_restart_notice("entry-id")


async def test_an_update_reads_as_one_steady_sequence_through_its_restart(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """Never Unavailable, a bar that only rises, and the new version only at the end."""
    # A wait that can never succeed fails in a second, not fourteen minutes.
    monkeypatch.setattr(update_progress, "_UPDATE_TIMEOUT_SECONDS", 1)
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    start = entity.installed_version
    shown: list[dict[str, Any]] = []

    def write() -> None:
        shown.append({"available": entity.available, **entity.state_attributes})

    entity.async_write_ha_state = write  # type: ignore[method-assign]
    coordinator = entity.coordinator
    coordinator.reachable = True
    restarted: PanelSnapshot | None = None
    answering = absent = 0
    clock = 0.0
    monkeypatch.setattr(update_progress, "_now", lambda: clock)

    async def accept(*_args: Any) -> None:
        nonlocal restarted, absent, answering
        restarted = _snapshot(VERSION, "2000", PACKAGE_ID)
        # The old app answers while it installs the new one, then restarts.
        answering, absent = 1, 3

    async def refresh() -> None:
        # Each poll ends in the coordinator's listener write, as in Core, and
        # the panel takes ten seconds between polls.
        nonlocal absent, restarted, answering, clock
        clock += 10
        if answering:
            answering -= 1
        elif absent:
            absent -= 1
            coordinator.reachable = False
        else:
            # A panel that answers again ends its restart notice inside the
            # poll, and the session-change listener writes before the poll
            # is marked successful, as seen on a real panel.
            async_get_sessions(hass).clear_restart_notice("entry-id")
            entity._handle_coordinator_update()
            coordinator.reachable = True
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
    client.async_commit_apk.side_effect = accept
    coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]
    try:
        await entity.async_install(None, backup=False)
    finally:
        async_get_sessions(hass).clear_restart_notice("entry-id")

    working = [state for state in shown if state["in_progress"]]
    assert any(not state["available"] for state in shown) is False
    # Core supplies localized progress UI; supplementary English stage prose
    # must not leak into the native dialog.
    assert all(state["release_summary"] is None for state in working)
    progress = [state["update_percentage"] for state in working]
    assert None not in progress
    # The bar follows time: it only rises, never leaps, and keeps the last
    # few percent for the end.
    assert progress == sorted(progress)
    assert max(b - a for a, b in pairwise(progress)) <= 15
    assert progress[-1] <= 95
    assert progress[-1] - progress[0] >= 40
    assert {state["installed_version"] for state in working} == {start}
    assert {state["latest_version"] for state in working} == {VERSION}
    assert shown[-1]["in_progress"] is False
    assert shown[-1]["installed_version"] == VERSION
    assert client.async_stage_apk.await_count == 1
    assert client.async_get_status.await_count == 2


async def test_the_update_bar_redraws_itself_between_polls_until_the_end(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """While the panel installs, Core's own timer moves the bar, then stops."""
    real_sleep = asyncio.sleep
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    clock = 0.0
    monkeypatch.setattr(update_progress, "_now", lambda: clock)
    drawn: list[tuple[int, int | None]] = []

    def write() -> None:
        drawn.append((threading.get_ident(), entity.update_percentage))

    entity.async_write_ha_state = write  # type: ignore[method-assign]
    installing, finish = asyncio.Event(), asyncio.Event()
    accept = client.async_commit_apk.side_effect

    async def commit(token: str) -> None:
        installing.set()
        await finish.wait()
        await accept(token)

    client.async_commit_apk.side_effect = commit
    install = hass.async_create_task(entity.async_install(None, backup=False))

    async def redraws() -> list[tuple[int, int | None]]:
        drawn.clear()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
        for _ in range(20):
            await real_sleep(0.01)
        return list(drawn)

    try:
        await asyncio.wait_for(installing.wait(), 5)
        clock = 30.0
        moving = await redraws()
        assert moving
        assert {thread for thread, _ in moving} == {threading.get_ident()}
        assert all(value is not None and value > 0 for _, value in moving)
    finally:
        finish.set()
        await install
    clock = 300.0
    assert await redraws() == []


def _app_panel(upload_status: int) -> FakeSession:
    """The 1.0 app's own answers: its upload refusal, and no `paneld` component."""
    install = "http://panel.local:8888/api/v1/install"
    reason = b"disabled" if upload_status == 403 else b"no-root"
    return FakeSession(
        {
            f"{install}/apk": FakeResponse(
                upload_status, b'{"ok":false,"error":"' + reason + b'"}'
            ),
            f"{install}/component": FakeResponse(400, b'{"status":"bad-component"}'),
        }
    )


@pytest.mark.parametrize(
    ("asset_status", "upload_status", "translation_key"),
    [
        (200, 403, "upload_disabled"),
        (200, 503, "update_rejected"),
        (503, 403, "release_download_failed"),
    ],
)
async def test_an_update_the_panel_cannot_take_says_why_and_leaves_the_app(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    asset_status: int,
    upload_status: int,
    translation_key: str,
) -> None:
    """A refused upload or a failed download is reported as itself.

    The panel is never asked to fetch the release, a request the app no longer
    takes, and its installed app is left as it was.
    """
    github = _GitHub(key, asset_status=asset_status)
    entity, client = await _entity(hass, monkeypatch, github)
    MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        title="Panel",
        data={CONF_ADDRESS: "192.168.1.30"},
    ).add_to_hass(hass)
    panel = _app_panel(upload_status)
    app = HaPaneldClient(panel, normalize_address("panel.local"))  # type: ignore[arg-type]
    # Every panel request this test does not script goes to the real client.
    scripted = {
        "async_backup_panel",
        "async_commit_apk",
        "async_discard_apk",
        "async_get_panel_install_status",
        "async_get_status",
        "async_get_version_code",
    }
    for name in dir(app):
        if name.startswith("async_") and name not in scripted:
            setattr(client, name, getattr(app, name))

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    assert error.value.translation_key == translation_key
    assert [url for url, _ in panel.requests if url.endswith("/component")] == []
    client.async_commit_apk.assert_not_awaited()
    assert entity.installed_version == "0.9.9"
    assert entity.in_progress is False
    # The owner keeps the reason as a Repair, not only in the action's reply.
    assert ir.async_get(hass).async_get_issue(
        DOMAIN, panel_failure_issue_id("update:entry-id")
    )


async def test_a_panel_already_past_the_release_is_offered_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """The release found here is never sent to a panel already past it."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot("0.9.11", "2000", PACKAGE_ID)

    assert entity.latest_version == entity.installed_version == "0.9.11"
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "update_unavailable")
    assert not github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()


async def test_refreshing_the_update_reads_the_latest_release_again(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """A release published since the last check is found on a manual refresh."""
    github = _GitHub(key)
    entity, _ = await _entity(hass, monkeypatch, github)
    github.requests.clear()

    await entity.async_update()

    assert str(release._LATEST_RELEASE_URL) in github.requests
    assert entity._offer.release is not None
    await entity._offer.release.async_shutdown()


async def test_polling_every_panel_reads_the_shared_release_once(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """Refreshing eight panels every 5 s for a minute, as a rollout waiting for
    a build does, costs one release read, not one per panel or per poll."""
    github = _GitHub(key)
    entity, _ = await _entity(hass, monkeypatch, github)
    release_check = entity._offer.release
    assert release_check is not None
    panels = [entity] + [
        HaPaneldUpdateEntity(
            f"entry-{index}",
            entity.coordinator,
            entity._update_coordinator,
            None,
            release=release_check,
        )
        for index in range(7)
    ]
    for index, panel in enumerate(panels[1:]):
        panel.hass = hass
        panel.entity_id = f"update.panel_{index}_ha_paneld"
        panel.async_write_ha_state = MagicMock()
    github.requests.clear()

    now = dt_util.utcnow()
    for poll in range(12):
        async_fire_time_changed(hass, now + timedelta(seconds=5 * poll))
        await asyncio.gather(*(panel.async_update() for panel in panels))
        await hass.async_block_till_done(wait_background_tasks=True)

    assert github.requests.count(str(release._LATEST_RELEASE_URL)) == 1
    await release_check.async_shutdown()


async def test_a_refused_release_listing_names_githubs_answer(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rate-limited listing logs the status GitHub answered with."""
    github = _GitHub(key)
    entity, _ = await _entity(hass, monkeypatch, github)
    release_check = entity._offer.release
    assert release_check is not None
    github._responses[str(release._LATEST_RELEASE_URL)] = _Response(
        403, b'{"message":"API rate limit exceeded"}', release._LATEST_RELEASE_URL
    )

    await release_check.async_refresh()

    assert not release_check.last_update_success
    assert "could not be authenticated: HTTP 403" in str(release_check.last_exception)
    assert "could not be authenticated: HTTP 403" in caplog.text


async def test_a_panel_older_than_lan_updates_is_never_offered_what_it_cannot_take(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """Before 0.8.6 a panel lacks LAN updates."""
    github = _GitHub(key)
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = PanelSnapshot(
        health=_snapshot("0.8.5", "1000", PACKAGE_ID).health,
        status=entity.coordinator.data.status,
        status_error=None,
    )

    assert entity.latest_version == entity.installed_version == "0.8.5"
    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "update_unavailable")
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    assert not github.apk_downloaded


# --- verification is never a way into the other route --------------------------


def _add_entry(hass: HomeAssistant, entity: HaPaneldUpdateEntity) -> MockConfigEntry:
    """Register the entity's config entry, as setup does."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id", title="Panel")
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    return entry


@pytest.mark.parametrize("package", [None, LEGACY_PACKAGE_ID])
async def test_an_old_app_panel_is_refused_its_update_before_any_delivery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    package: str | None,
) -> None:
    """A panel on the old app is sent nothing, not even the new app."""
    github = _GitHub(key, package_id=PACKAGE_ID)
    entity, client = await _entity(hass, monkeypatch, github)
    entity.coordinator.data = _snapshot(VERSION, "2000", package)
    _add_entry(hass, entity)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "old_app_needs_090")
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    assert not github.apk_downloaded


async def test_an_update_waits_while_another_operation_holds_the_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """A removal or another update changing the app keeps this one out."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    assert claim_panel_operation(hass, entity._entry_id)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    _assert_translated(error.value, "update_busy")
    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()

    # The claim is the other operation's: refusing never releases it.
    assert not claim_panel_operation(hass, entity._entry_id)
    release_panel_operation(hass, entity._entry_id)
    await entity.async_install(None, backup=False)
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    # The update gives its own claim back when it ends.
    assert claim_panel_operation(hass, entity._entry_id)


@pytest.mark.parametrize(
    ("translation_key", "recorded"),
    [("adb_authorization_required", True), ("update_unavailable", False)],
)
async def test_only_a_missing_adb_authorization_is_kept_as_an_update_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    translation_key: str,
    recorded: bool,
) -> None:
    """Another reason no route was found is reported, not kept as a failure."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        title="Panel",
        data={CONF_ADDRESS: "192.168.1.30"},
    )
    entry.add_to_hass(hass)
    refusal = HomeAssistantError(
        "no route", translation_domain=DOMAIN, translation_key=translation_key
    )
    monkeypatch.setattr(entity, "_async_install_route", AsyncMock(side_effect=refusal))

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)
    assert error.value is refusal
    failure = ir.async_get(hass).async_get_issue(
        DOMAIN, panel_failure_issue_id("update:entry-id")
    )
    assert (failure is not None) is recorded
    client.async_backup_panel.assert_not_awaited()


async def test_missing_release_download_is_not_called_a_bad_signature(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    monkeypatch.setattr(
        update_staging,
        "async_download_build",
        AsyncMock(side_effect=BuildDownloadError),
    )
    artifact = entity._offer.host_release()
    assert artifact is not None

    with pytest.raises(HomeAssistantError) as error:
        await entity._delivery.async_deliver_build(hass, artifact)
    _assert_translated(error.value, "release_download_failed")
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


async def test_identity_change_during_upload_refuses_commit(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    """A panel whose app changes during the upload is not committed onto."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))

    async def stage(_apk: bytes) -> StagedApk:
        entity.coordinator.data = _snapshot("0.9.9", "2000", LEGACY_PACKAGE_ID)
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
    entity, client = await _entity(hass, monkeypatch, github)

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "release_verification_failed")
    assert github.apk_downloaded
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    assert entity.in_progress is False


@pytest.mark.parametrize(
    "preview",
    [
        _preview(signer="0" * 64),
        _preview(package=LEGACY_PACKAGE_ID),
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
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    client.async_stage_apk.return_value = preview

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, "staged_app_mismatch")
    client.async_discard_apk.assert_awaited_once_with("tok-1")
    client.async_commit_apk.assert_not_awaited()


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


@pytest.mark.parametrize(
    ("failure", "key_name"),
    [
        (UpdateBusyError, "update_busy"),
        (UpdateRejectedError, "update_rejected"),
        (CannotConnectError, "update_not_accepted"),
    ],
)
async def test_a_stage_that_fails_for_any_other_reason_says_so(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    failure: type[Exception],
    key_name: str,
) -> None:
    """A busy, full or interrupted panel is reported as itself."""
    entity, client = await _entity(hass, monkeypatch, _GitHub(key))
    client.async_stage_apk.side_effect = failure

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_install(None, backup=False)

    _assert_translated(error.value, key_name)
    client.async_commit_apk.assert_not_awaited()


async def test_a_release_for_another_app_id_is_not_offered(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    github = _GitHub(key, package_id=LEGACY_PACKAGE_ID)
    entity, client = await _entity(hass, monkeypatch, github)
    assert entity.latest_version == entity.installed_version
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entity.async_install(None, backup=False)
    client.async_stage_apk.assert_not_awaited()


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
    await hass.async_block_till_done(wait_background_tasks=True)
    await entry._async_process_on_unload(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    remove_listener = coordinator.async_add_listener(lambda: None)
    github.requests.clear()

    async_fire_time_changed(hass, dt_util.utcnow() + feed_coordinator.STABLE_REFRESH)
    await hass.async_block_till_done(wait_background_tasks=True)

    remove_listener()
    assert str(release._LATEST_RELEASE_URL) in github.requests


class _InstalledPanel(_RoutingFakeDevice):
    present = True
    malformed = False
    package_id = LEGACY_PACKAGE_ID
    # The bytes of the installed package's own APK, which a byte proof hashes.
    installed_apk = APK

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        artifact = re.search(r"HAPANELD_ARTIFACT_BEGIN:([0-9a-f]{32})", command)
        if artifact is not None:
            self.commands.append(command)
            nonce, path = artifact.group(1), "/data/app/ha-paneld/base.apk"
            sha = hashlib.sha256(self.installed_apk).hexdigest()
            lines = [f"HAPANELD_ARTIFACT_BEGIN:{nonce}"]
            lines += _section("ARTIFACT", "MODE", nonce, ["81a4"], 0)
            lines += _section(
                "ARTIFACT", "SIZE", nonce, [str(len(self.installed_apk))], 0
            )
            lines += _section("ARTIFACT", "SHA", nonce, [f"{sha}  {path}"], 0)
            lines.append(f"HAPANELD_ARTIFACT_END:{nonce}")
            yield ("\n".join(lines) + "\n").encode()
            return
        match = re.search(r"HAPANELD_PREFLIGHT_BEGIN:([0-9a-f]{32})", command)
        if match is None:
            async for packet in super().streaming_shell(command, **kwargs):
                yield packet
            return
        self.commands.append(command)
        self.shell_kwargs.append(kwargs)
        if self.malformed:
            yield b"unreadable package-manager response\n"
        else:
            yield _preflight_output(
                match.group(1),
                package_lines=["package:/data/app/ha-paneld/base.apk"]
                if self.present and self.package_id == LEGACY_PACKAGE_ID
                else [],
                successor_package_lines=["package:/data/app/panel-assistant/base.apk"]
                if self.present and self.package_id == PACKAGE_ID
                else [],
            )


async def _lan_grant_repair_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    key: Any,
    adb_signer: PythonRSASigner,
    tmp_path: Path,
) -> SimpleNamespace:
    hass.config.config_dir = str(tmp_path)
    github = _GitHub(key, package_id=PACKAGE_ID)
    entity, client = await _entity(hass, monkeypatch, github, package=PACKAGE_ID)
    client.async_stage_apk.return_value = _preview(package=PACKAGE_ID)
    entry = _add_entry(hass, entity)
    mock_integration(
        hass,
        MockModule(
            DOMAIN,
            async_setup_entry=AsyncMock(return_value=True),
            async_unload_entry=AsyncMock(return_value=True),
        ),
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    assert entity.latest_version == VERSION
    client.address = normalize_address("192.168.1.23")
    pinned = PinnedPanelTarget(client.address, client.address)
    credential = AdbCredential(adb_signer, "existing-host-key")
    for feature in ("camera.any", "microphone"):
        (tmp_path / f"feature.android.hardware.{feature}").write_text("true\n")
    devices = [_InstalledPanel(tmp_path), _InstalledPanel(tmp_path)]
    for device in devices:
        device.package_id = PACKAGE_ID
    _install_fakes(monkeypatch, devices)

    async def durable_key(_hass: HomeAssistant) -> AdbCredential:
        # The dashboard and exact target must be proved before ADB repair.
        assert entity.coordinator.data.health.version == VERSION
        assert client.async_commit_apk.await_count == 1
        assert client.async_get_status.await_count > 0
        return credential

    key_load = AsyncMock(side_effect=durable_key)
    monkeypatch.setattr(
        update_panel_install, "async_get_durable_adb_credential", key_load
    )
    create_key = AsyncMock(side_effect=AssertionError("repair created a new key"))
    monkeypatch.setattr(update_route, "async_get_adb_credential", create_key)
    request_authorization = MagicMock(
        side_effect=AssertionError("repair requested authorization")
    )
    monkeypatch.setattr(
        update_errors, "async_request_adb_authorization", request_authorization
    )
    monkeypatch.setattr(
        install_network, "async_pin_install_target", AsyncMock(return_value=pinned)
    )
    monkeypatch.setattr(
        update_route, "async_revalidate_install_target", AsyncMock(return_value=pinned)
    )
    probe = AsyncMock(
        return_value=InstallTargetProbe(
            state=InstallTargetState.INSTALLED,
            serial="SERIAL-1",
            model="Test Panel",
            primary_abi="arm64-v8a",
            android_sdk=34,
        )
    )
    monkeypatch.setattr(update_route, "async_probe_install_target", probe)
    pinned_client = SimpleNamespace(
        async_get_health=AsyncMock(side_effect=lambda: entity.coordinator.data.health)
    )
    monkeypatch.setattr(install_network, "HaPaneldClient", lambda *_args: pinned_client)

    return SimpleNamespace(
        entity=entity,
        client=client,
        devices=devices,
        key_load=key_load,
        probe=probe,
        pinned_client=pinned_client,
        create_key=create_key,
        request_authorization=request_authorization,
        entry=entry,
    )


@pytest.mark.parametrize("held", [False, True])
async def test_a_lan_update_repairs_missing_grants_and_preserves_held_grants(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    held: bool,
) -> None:
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    entity, devices = panel.entity, panel.devices
    package_id = PACKAGE_ID
    if held:
        _seed_granted_panel(tmp_path, package_id)

    await entity.async_install(None, False)
    # The repair runs after the update returns, once the panel answers ADB.
    await hass.async_block_till_done(wait_background_tasks=True)

    for permission in ("CAMERA", "RECORD_AUDIO", "POST_NOTIFICATIONS"):
        assert (
            _panel_value(
                tmp_path, f"permission.{package_id}.android.permission.{permission}"
            )
            == "true"
        )
    assert _panel_value(tmp_path, "accessibility_enabled") == "1"
    for operation in (
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "REQUEST_INSTALL_PACKAGES",
    ):
        assert _panel_value(tmp_path, f"appop.{package_id}.{operation}") == "allow"
    commands = [command for device in devices for command in device.commands]
    assert not any(
        "am start" in command or "pm install" in command for command in commands
    )
    if held:
        assert not (tmp_path / "calls").exists()
    assert entity.installed_version == VERSION
    assert entity.in_progress is False


@pytest.mark.parametrize("silent_probes", [2, 99], ids=["comes-back", "never"])
async def test_the_grant_repair_waits_for_adb_to_answer_after_the_restart(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    silent_probes: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A restarting app re-asserts network ADB, so ADB can go quiet for a while."""
    # The update's own debug logger is how a release check watches the repair.
    caplog.set_level(logging.DEBUG, logger="custom_components.panel_assistant.update")
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    monkeypatch.setattr(update_panel_install, "_PERMISSION_REPAIR_RETRY_SECONDS", 0)
    answering = panel.probe.return_value
    silent = InstallTargetProbe(
        state=InstallTargetState.ADB_UNREACHABLE,
        serial=None,
        model=None,
        primary_abi=None,
        android_sdk=None,
    )
    panel.probe.return_value = None
    panel.probe.side_effect = [silent] * silent_probes + [answering] * 4

    await panel.entity.async_install(None, False)
    await hass.async_block_till_done(wait_background_tasks=True)

    if silent_probes == 2:
        # Quiet twice, then the panel answers and takes its grants.
        assert _panel_value(tmp_path, "accessibility_enabled") == "1"
        assert panel.probe.await_count == 3
        assert any(
            record.name == "custom_components.panel_assistant.update"
            and record.getMessage().startswith("Repaired permissions after updating")
            for record in caplog.records
        )
    else:
        # A panel that never answers is left alone after a bounded wait.
        assert _panel_value(tmp_path, "accessibility_enabled") is None
        assert (
            panel.probe.await_count == update_panel_install._PERMISSION_REPAIR_ATTEMPTS
        )
    assert panel.entity.installed_version == VERSION
    assert panel.entity.in_progress is False


async def test_a_panel_that_changes_while_adb_is_quiet_gets_no_grant_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Each retry checks the panel again before it can write anything."""
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    monkeypatch.setattr(update_panel_install, "_PERMISSION_REPAIR_RETRY_SECONDS", 0)
    answering = panel.probe.return_value
    silent = replace(
        answering,
        state=InstallTargetState.ADB_UNREACHABLE,
        serial=None,
        model=None,
        primary_abi=None,
        android_sdk=None,
    )

    async def probe(*_args: Any) -> InstallTargetProbe:
        if panel.probe.await_count == 1:
            # While ADB is quiet, the panel at this address stops being this one.
            panel.entity.coordinator.identity_mismatch = True
            return silent
        return answering

    panel.probe.return_value = None
    panel.probe.side_effect = probe

    await panel.entity.async_install(None, False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert panel.probe.await_count == 1
    assert _panel_value(tmp_path, "accessibility_enabled") is None


async def test_removing_the_panel_ends_its_waiting_grant_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
) -> None:
    """Once the entry is gone, a repair waiting for ADB writes nothing."""
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    monkeypatch.setattr(update_panel_install, "_PERMISSION_REPAIR_RETRY_SECONDS", 0)
    answering = panel.probe.return_value
    silent = replace(
        answering,
        state=InstallTargetState.ADB_UNREACHABLE,
        serial=None,
        model=None,
        primary_abi=None,
        android_sdk=None,
    )
    waiting = asyncio.Event()
    adb_back = asyncio.Event()

    async def probe(*_args: Any) -> InstallTargetProbe:
        if panel.probe.await_count == 1:
            return silent
        # The repair's next try: ADB answers only after the panel is removed.
        waiting.set()
        await adb_back.wait()
        return answering

    panel.probe.return_value = None
    panel.probe.side_effect = probe

    await panel.entity.async_install(None, False)
    await waiting.wait()
    assert await hass.config_entries.async_unload(panel.entry.entry_id)
    adb_back.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert panel.probe.await_count == 2
    assert _panel_value(tmp_path, "accessibility_enabled") is None


@pytest.mark.parametrize(
    "unavailable",
    [
        "missing-key",
        "invalid-key",
        "unauthorized",
        "unreachable",
        "foreign-http-panel",
        "entry-discovery-mismatch",
        "missing-installed-package",
        "unreadable-preflight",
        "repair-connection-lost",
    ],
)
async def test_an_unavailable_adb_repair_keeps_a_successful_lan_dashboard_running(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    unavailable: str,
) -> None:
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    entity = panel.entity
    if unavailable == "missing-key":
        panel.key_load.side_effect = AdbCredentialMissingError
    elif unavailable == "invalid-key":
        panel.key_load.side_effect = AdbCredentialError
    elif unavailable == "unauthorized":
        panel.probe.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNAUTHORIZED
        )
    elif unavailable == "unreachable":
        panel.probe.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNREACHABLE
        )
    elif unavailable == "foreign-http-panel":
        panel.pinned_client.async_get_health.side_effect = lambda: replace(
            entity.coordinator.data.health, panel_id="another-panel"
        )
    elif unavailable == "entry-discovery-mismatch":
        for module in (panel_update, update_route):
            monkeypatch.setattr(
                module, "entry_discovery_id", lambda _hass, _entry_id: "pa-expected"
            )
        panel.pinned_client.async_get_health.side_effect = lambda: replace(
            entity.coordinator.data.health, discovery_id="pa-other"
        )
    elif unavailable == "missing-installed-package":
        panel.devices[0].present = False
    elif unavailable == "unreadable-preflight":
        panel.devices[0].malformed = True
    else:
        panel.devices[1].connect_error = OSError("ADB disconnected")

    failure = None
    try:
        await entity.async_install(None, False)
    except AdbCredentialError as err:
        failure = err

    assert failure is None, "ADB unavailability must preserve a successful LAN update"
    assert not (tmp_path / "calls").exists()
    assert entity.installed_version == VERSION
    assert entity.in_progress is False
    panel.create_key.assert_not_awaited()
    panel.request_authorization.assert_not_called()
    assert not any(
        "am start" in command for device in panel.devices for command in device.commands
    )


@pytest.mark.parametrize(
    "credential_error", [AdbCredentialMissingError, AdbCredentialError]
)
async def test_an_unusable_adb_key_ends_the_permission_repair_quietly(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    credential_error: type[AdbCredentialError],
) -> None:
    """Most panels never trusted this Home Assistant: no error after an update."""
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    panel.key_load.side_effect = credential_error
    repairs: list[asyncio.Task[Any]] = []
    create_task = panel.entry.async_create_background_task

    def owned_task(*args: Any, **kwargs: Any) -> asyncio.Task[Any]:
        task = create_task(*args, **kwargs)
        repairs.append(task)
        return task

    monkeypatch.setattr(panel.entry, "async_create_background_task", owned_task)

    await panel.entity.async_install(None, False)
    assert len(repairs) == 1
    await asyncio.wait(repairs)

    assert repairs[0].exception() is None, "the repair left an uncaught error"
    panel.key_load.assert_awaited()


async def test_shared_release_selects_compatible_stable_and_prerelease_candidates(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, trust: None, key: Any
) -> None:
    """Each policy ignores an unknown or incompatible head before choosing."""
    from custom_components.panel_assistant import release_catalog

    entity, _ = await _entity(hass, monkeypatch, _GitHub(key))
    coordinator = entity._offer.release
    assert coordinator is not None
    artifact = coordinator.artifact_for(PACKAGE_ID)
    assert artifact is not None
    candidates = [
        replace(
            artifact,
            version="0.9.15",
            tag="v0.9.15",
            protocol_min=None,
            protocol_max=None,
        ),
        replace(
            artifact, version="0.9.14", tag="v0.9.14", protocol_min=6, protocol_max=6
        ),
        replace(artifact, version="0.9.13-rc1", tag="v0.9.13-rc1"),
        replace(artifact, version="0.9.12", tag="v0.9.12"),
    ]
    monkeypatch.setattr(
        release_catalog,
        "async_resolve_update_candidates",
        AsyncMock(
            return_value=[
                SimpleNamespace(artifact=candidate) for candidate in candidates
            ]
        ),
    )
    await coordinator.async_refresh()
    stable = coordinator.artifact_for(PACKAGE_ID, allow_prerelease=False)
    testing = coordinator.artifact_for(PACKAGE_ID, allow_prerelease=True)
    assert stable is not None and stable.version == "0.9.12"
    assert testing is not None and testing.version == "0.9.13-rc1"
    assert entity.latest_version == "0.9.13-rc1"


@pytest.mark.parametrize("withdrawn", ["unloading", "unloaded", "reloaded"])
async def test_a_panel_withdrawn_during_the_update_gets_no_grant_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    withdrawn: str,
) -> None:
    """Unloaded before the update finished, the entry can no longer cancel a repair."""
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    status = panel.client.async_get_status
    proving = asyncio.Event()
    proved = asyncio.Event()

    async def home_proof(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("home_proof"):
            proving.set()
            await proved.wait()
        return await status(*args, **kwargs)

    panel.client.async_get_status = AsyncMock(side_effect=home_proof)
    cleaning_up = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def slow_cleanup() -> None:
        # Another task of the entry that takes a while to stop, so the unload
        # is still in progress, its runtime data still in place, while the
        # update's HOME proof returns.
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning_up.set()
            await cleaned_up.wait()
            raise

    panel.entry.async_create_background_task(hass, slow_cleanup(), "slow cleanup")
    install = asyncio.create_task(panel.entity.async_install(None, False))
    await proving.wait()
    probes = panel.probe.await_count
    unload = asyncio.create_task(hass.config_entries.async_unload(panel.entry.entry_id))
    await cleaning_up.wait()
    if withdrawn == "unloading":
        assert panel.entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS
        proved.set()
        await install
        cleaned_up.set()
        assert await unload
    else:
        cleaned_up.set()
        assert await unload
        if withdrawn == "reloaded":
            # Loaded again, with a coordinator this update entity does not own.
            panel.entry.runtime_data = SimpleNamespace(coordinator=object())
            panel.entry.mock_state(hass, ConfigEntryState.LOADED)
        proved.set()
        await install
    await hass.async_block_till_done(wait_background_tasks=True)

    assert panel.probe.await_count == probes
    assert _panel_value(tmp_path, "accessibility_enabled") is None


@pytest.mark.parametrize(
    "panel_state", ["stopped", "no-adb", "session", "answering", "other-build"]
)
async def test_an_app_that_never_comes_back_after_an_update_is_started_and_reported(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    signer: PythonRSASigner,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    panel_state: str,
) -> None:
    """A panel took an update, crashed on its first start and stayed down.

    Without ADB the failure stands; a panel that still answers, over HTTP or its
    own session, has its app, so nothing is started over it; and a panel whose
    installed APK is not this build's exact bytes is left untouched.
    """
    panel = await _lan_grant_repair_panel(hass, monkeypatch, key, signer, tmp_path)
    entity, client = panel.entity, panel.client
    monkeypatch.setattr(update_progress, "_ANDROID_PACKAGE_INSTALL_MAX_SECONDS", 0)
    monkeypatch.setattr(update_progress, "_RESTART_HEALTH_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(
        update_route,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=AdbCredential(signer, "existing-host-key")),
    )
    if panel_state == "session":
        monkeypatch.setattr(
            HaPaneldDataUpdateCoordinator, "connected", property(lambda _self: True)
        )
    # The permission repair after a successful update takes ADB connections too.
    panel.devices.extend(_InstalledPanel(tmp_path) for _ in range(6))
    if panel_state == "other-build":
        for device in panel.devices:
            device.installed_apk = b"another build of the same app"
    if panel_state == "no-adb":
        panel.probe.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNREACHABLE,
            serial=None,
            model=None,
            primary_abi=None,
            android_sdk=None,
        )

    def launched() -> int:
        return sum(
            "am start" in command
            for device in panel.devices
            for command in device.commands
        )

    async def refresh() -> None:
        # Installed, then nothing answers until something starts the app.
        if client.async_commit_apk.await_count and not launched():
            # An answering panel stays on its old build instead.
            entity.coordinator.reachable = panel_state == "answering"
        elif launched():
            entity.coordinator.data = _snapshot(VERSION, "2000", PACKAGE_ID)
            entity.coordinator.reachable = True

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]

    if panel_state == "stopped":
        await entity.async_install(None, False)
        assert entity.installed_version == VERSION
        assert launched() == 1
        assert any(
            record.levelno == logging.WARNING
            and "did not start again" in record.getMessage()
            for record in caplog.records
        )
    else:
        with pytest.raises(HomeAssistantError) as raised:
            await entity.async_install(None, False)
        _assert_translated(raised.value, "update_not_complete")
        assert launched() == 0
        # Nothing on the panel changed: no grant, HOME or vendor step ran.
        assert not any(
            marker in command
            for device in panel.devices
            for command in device.commands
            for marker in (
                "_PERMISSIONS_BEGIN",
                "_NOTIFICATIONS_BEGIN",
                "_VENDOR_BEGIN",
            )
        )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entity.in_progress is False

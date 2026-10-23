"""A panel Home Assistant cannot reach updates over its own session.

The fake panel below holds a real session with the management grant, answers
the update's requests as the app does, and moves the app and the backup
through the real blob view with its own bound user's credential.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import (
    blob,
    feed_coordinator,
    transport_protocol,
    update_staging,
)
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.app_identity import (
    PACKAGE_ID,
)
from custom_components.panel_assistant.blob import BlobTransferError, async_get_blobs
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    HaPaneldError,
    InvalidResponseError,
    UpdateBusyError,
    parse_health_response,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.repairs_update_route import (
    no_update_route_issue_id,
)
from custom_components.panel_assistant.session_carrier import SessionCarrier
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)
from custom_components.panel_assistant.update_errors import _UpdateRefusalError

from .test_availability import UPDATE_ENTITY, _load, _load_offline
from .test_lan_staged_update import (  # noqa: F401
    APK,
    SIGNER,
    VERSION,
    _backup,
    _GitHub,
    _preview,
    backups_in_tmp,
    key,
    trust,
)
from .test_management import MANAGED, STATUS_BODY, _http_blocked
from .test_transport import DID, HEALTH, HELLO

_real_sleep = asyncio.sleep
_STATUS = {**json.loads(STATUS_BODY), "install_capability": "api"}
_HOME = {"state": "ready", "reason": "dashboard", "evidence": "foreground"}
OLD = ("0.9.9", "1000", 770)
NEW = (VERSION, "2000", 780)


def _health(version: str, build: str, code: int) -> str:
    return (
        f"ha-paneld {version} panel=alpha build={build} cfg=1a2b3c4d did={DID}"
        f" identity=install vc={code} proto=5-5 pkg={PACKAGE_ID}\n"
    )


Answer = tuple[str, str | None, dict[str, Any] | None]


def _same(reference: dict[str, Any]) -> dict[str, Any]:
    return reference


class FakePanel:
    """The app's side of the session: answers manage requests, moves blobs."""

    def __init__(
        self, hass: HomeAssistant, ws_client: Any, http_client: Any, token: str
    ) -> None:
        self.hass = hass
        self._ws_client = ws_client
        self._http_client = http_client
        self._token = token
        self.build = OLD
        self.ops: list[str] = []
        self.fetched: bytes | None = None
        # op -> answer that replaces the app's own; called with the request's args.
        self.answers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.backup = _backup()
        self.status = dict(_STATUS)
        # Lets a test misreport the uploaded backup.
        self.backup_reference: Callable[[dict[str, Any]], dict[str, Any]] = _same
        self._ws: Any = None
        self._serving: asyncio.Task[None] | None = None
        self.http: Any = None

    async def connect(self, build: tuple[str, str, int] = OLD) -> None:
        self.build = build
        if self.http is None:
            self.http = await self._http_client(self._token)
        self._ws = await self._ws_client(self.hass, self._token)
        await self._ws.send_json_auto_id(
            {
                **HELLO,
                "app": {"version": build[0], "version_code": build[2]},
                "capabilities": MANAGED,
            }
        )
        hello = await self._ws.receive_json()
        assert hello["success"], hello
        self.session = hello["result"]["session"]
        self._serving = asyncio.ensure_future(self._serve(self._ws, self.session))

    async def close(self) -> None:
        if self._serving is not None:
            self._serving.cancel()
        await self._ws.close()

    async def _serve(self, ws: Any, session: str) -> None:
        while True:
            message = await ws.receive_json()
            if message.get("type") != "event":
                continue
            event = message["event"]
            if event.get("kind") != "manage" or event["session"] != session:
                continue
            restart = await self._handle(ws, session, event)
            if restart:
                await ws.close()
                await self.connect(NEW)
                return

    async def _reply(
        self, ws: Any, session: str, event: dict[str, Any], answer: Answer
    ) -> None:
        outcome, code, result = answer
        message: dict[str, Any] = {
            "type": "panel_assistant/command_result",
            "session": session,
            "command_id": event["command_id"],
            "outcome": outcome,
        }
        if code is not None:
            message["code"] = code
        if result is not None:
            message["result"] = result
        await ws.send_json_auto_id(message)

    async def _handle(self, ws: Any, session: str, event: dict[str, Any]) -> bool:
        op, args = event["op"], event.get("args", {})
        self.ops.append(op)
        if op in self.answers:
            for answer in await self.answers[op](args):
                await self._reply(ws, session, event, answer)
            return False
        version, build, code = self.build
        if op == "snapshot":
            answer: Answer = (
                "applied",
                None,
                {
                    "health": _health(version, build, code),
                    "status": json.dumps(self.status),
                },
            )
        elif op == "status":
            assert args == {"home_proof": True}
            answer = ("applied", None, {**self.status, "home_ui": _HOME})
        elif op == "backup":
            assert args["allow_plaintext"] == "1"
            assert args["include_companion"] == "false"
            url = f"/api/panel_assistant/blob/{args['blob_token']}"
            response = await self.http.put(url, data=self.backup)
            assert response.status == 200, response.status
            reference = {
                "url": url,
                "size": len(self.backup),
                "sha256": hashlib.sha256(self.backup).hexdigest(),
                "type": "application/octet-stream",
            }
            answer = ("applied", None, {"blob": self.backup_reference(reference)})
        elif op == "install.apk":
            reference = args["blob"]
            response = await self.http.get(reference["url"])
            assert response.status == 200, response.status
            data = await response.read()
            assert len(data) == reference["size"]
            assert hashlib.sha256(data).hexdigest() == reference["sha256"]
            self.fetched = data
            answer = (
                "applied",
                None,
                {
                    "ok": True,
                    "token": "tok-1",
                    "package": PACKAGE_ID,
                    "version": VERSION,
                    "signer": SIGNER,
                },
            )
        elif op == "install.apk.commit":
            assert args == {"token": "tok-1"}
            await self._reply(
                ws, session, event, ("applied", None, {"status": "started"})
            )
            # The app replaces itself, so its session ends.
            return True
        elif op == "install.apk.discard":
            answer = ("applied", None, {"ok": True})
        else:
            answer = ("refused", "unknown_command", None)
        await self._reply(ws, session, event, answer)
        return False


async def _session_panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signing_key: Any,
    hass_ws_client: Any,
    hass_client: Any,
    user: Any,
    token: str,
) -> tuple[HaPaneldUpdateEntity, FakePanel]:
    """A loaded panel on 0.9.9 that only its session reaches, and its update."""
    github = _GitHub(signing_key, package_id=PACKAGE_ID)
    monkeypatch.setattr(feed_coordinator, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(update_staging, "async_get_clientsession", lambda _h: github)
    entry = await _load(hass, user.id)
    coordinator = entry.runtime_data.coordinator
    stable = StableReleaseCoordinator(hass)
    await stable.async_refresh()
    updates = PanelUpdateCoordinator(hass, coordinator.client)
    updates.data = PanelUpdateSnapshot(operation=None, error=None)
    updates.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    entity = HaPaneldUpdateEntity(
        entry.entry_id, coordinator, updates, None, release=stable
    )
    entity.hass = hass
    entity.entity_id = "update.session_panel"
    entity.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    # Every poll reads at once rather than through the debouncer.
    coordinator.async_request_refresh = coordinator.async_refresh  # type: ignore[method-assign]

    async def fast_sleep(*_args: Any) -> None:
        await _real_sleep(0)

    monkeypatch.setattr(panel_update.asyncio, "sleep", fast_sleep)
    panel = FakePanel(hass, hass_ws_client, hass_client, token)
    return entity, panel


@pytest.fixture
async def offline(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,  # noqa: F811
    key: Any,  # noqa: F811
    hass_ws_client: Any,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> Any:
    entity, panel = await _session_panel(
        hass,
        monkeypatch,
        key,
        hass_ws_client,
        hass_client,
        hass_read_only_user,
        hass_read_only_access_token,
    )
    with _http_blocked(), _update_http_blocked():
        await panel.connect()
        await entity.coordinator.async_refresh()
        assert entity.coordinator.last_update_success
        assert not entity.coordinator.reachable
        yield entity, panel
        await panel.close()


_HTTP_UPDATE_CALLS = (
    "async_backup_panel",
    "async_stage_apk",
    "async_commit_apk",
    "async_discard_apk",
    "async_get_panel_install_status",
    "async_get_version_code",
    "async_start_panel_update",
)


def _update_http_blocked() -> Any:
    """No update call reaches the panel's address either."""
    return patch.multiple(
        HaPaneldClient,
        **{
            name: AsyncMock(side_effect=CannotConnectError)
            for name in _HTTP_UPDATE_CALLS
        },
    )


def _backups(hass: HomeAssistant) -> list[Path]:
    directory = Path(hass.config.path(DOMAIN, "backups"))
    return sorted(directory.iterdir()) if directory.exists() else []


async def _install(entity: HaPaneldUpdateEntity) -> None:
    async with asyncio.timeout(20):
        await entity.async_install(None, backup=False)


async def _refused(entity: HaPaneldUpdateEntity) -> HomeAssistantError:
    with pytest.raises(HomeAssistantError) as raised:
        await _install(entity)
    return raised.value


# --- the whole update -----------------------------------------------------------


async def test_an_unreachable_panel_updates_over_its_session(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline
    assert entity.installed_version == "0.9.9"
    assert entity.latest_version == VERSION

    await _install(entity)

    # The exact release bytes Home Assistant verified went to the panel.
    assert panel.fetched == APK
    assert panel.ops.index("backup") < panel.ops.index("install.apk")
    assert panel.ops.index("install.apk") < panel.ops.index("install.apk.commit")
    assert panel.ops[-1] == "status"
    # The backup the panel uploaded is stored with its receipt.
    archives = [path for path in _backups(hass) if path.suffix == ".zip"]
    assert len(archives) == 1
    assert archives[0].read_bytes() == panel.backup
    receipt = json.loads(archives[0].with_name(archives[0].name + ".json").read_text())
    assert receipt["sha256"] == hashlib.sha256(panel.backup).hexdigest()
    assert entity.extra_state_attributes == {
        "update_route": "pulled_from_home_assistant"
    }
    assert entity.installed_version == VERSION
    assert entity.in_progress is False
    # Nothing outlives the update, and nothing was tried over HTTP.
    assert not async_get_blobs(hass)._transfers
    for name in _HTTP_UPDATE_CALLS:
        getattr(HaPaneldClient, name).assert_not_awaited()


async def test_an_address_that_answers_before_the_first_snapshot_raises_nothing(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The stored address answers, but no snapshot has been read yet.

    The entry loaded with no snapshot; the panel connects, its address
    answers and its snapshot read fails, so the entry is reachable with no
    data when the panel reconnects and the update entity is told. It claims
    no installed build until a snapshot brings one.
    """
    entry = await _load_offline(hass, hass_read_only_user.id, DID)
    coordinator = entry.runtime_data.coordinator
    panel = FakePanel(hass, hass_ws_client, hass_client, hass_read_only_access_token)

    async def failed(_args: dict[str, Any]) -> list[Answer]:
        return [("failed", "failed", None)]

    panel.answers["snapshot"] = failed
    with patch.object(
        HaPaneldClient, "async_get_health", AsyncMock(return_value=HEALTH)
    ):
        await panel.connect()
        await hass.async_block_till_done()
        assert coordinator.reachable
        assert coordinator.data is None
        await panel.close()
        await panel.connect()
        await hass.async_block_till_done()

        assert "Unexpected error updating listener" not in caplog.text
        state = hass.states.get(UPDATE_ENTITY)
        assert state.attributes["installed_version"] is None

        del panel.answers["snapshot"]
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(UPDATE_ENTITY).attributes["installed_version"] == OLD[0]
    await panel.close()


async def test_a_panel_that_cannot_install_itself_gets_no_session_route(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline
    panel.status = {**_STATUS, "install_capability": "none"}
    await entity.coordinator.async_refresh()

    error = await _refused(entity)

    assert error.translation_key == "update_unavailable"
    assert "backup" not in panel.ops


async def test_an_oversize_status_is_refused_before_parsing(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline

    async def huge(_args: dict[str, Any]) -> list[Answer]:
        # Valid, but larger than the HTTP read would ever have accepted.
        return [("applied", None, {**_STATUS, "padding": "x" * 70_000})]

    panel.answers["status"] = huge

    with pytest.raises(InvalidResponseError):
        await SessionCarrier(hass, entity._entry_id).async_get_status(home_proof=True)


async def test_a_reachable_managed_panel_still_updates_over_http(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline
    coordinator = entity.coordinator
    started = False

    async def commit(_token: str) -> None:
        nonlocal started
        started = True

    client = MagicMock()
    client.async_backup_panel = AsyncMock(return_value=_backup())
    client.async_stage_apk = AsyncMock(return_value=_preview(package=PACKAGE_ID))
    client.async_commit_apk = AsyncMock(side_effect=commit)
    client.async_get_status = AsyncMock(
        return_value=PanelStatus(warning_count=0, capability_count=0, home_ui=_HOME)
    )
    coordinator.client = client
    coordinator.reachable = True
    old = coordinator.data

    async def refresh() -> None:
        coordinator.reachable = True
        if started:
            coordinator.data = PanelSnapshot(
                health=parse_health_response(_health(*NEW)),
                status=old.status,
                status_error=None,
            )

    coordinator.async_request_refresh = AsyncMock(side_effect=refresh)  # type: ignore[method-assign]

    await _install(entity)

    client.async_stage_apk.assert_awaited_once_with(APK)
    client.async_commit_apk.assert_awaited_once_with("tok-1")
    assert "install.apk" not in panel.ops
    assert "backup" not in panel.ops
    assert entity.extra_state_attributes == {"update_route": "staged_by_home_assistant"}


# --- refusals and errors ----------------------------------------------------------


async def test_a_backup_that_is_not_the_one_named_is_refused_and_not_stored(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline
    panel.backup_reference = lambda ref: {**ref, "sha256": "0" * 64}

    error = await _refused(entity)

    assert error.translation_key == "panel_backup_failed"
    assert _backups(hass) == []
    assert "install.apk" not in panel.ops
    assert not async_get_blobs(hass)._transfers


async def test_a_backup_of_another_size_is_refused(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline
    panel.backup_reference = lambda ref: {**ref, "size": ref["size"] + 1}

    error = await _refused(entity)

    assert error.translation_key == "panel_backup_failed"
    assert _backups(hass) == []


async def test_a_busy_backup_reads_as_busy(hass: HomeAssistant, offline: Any) -> None:
    entity, panel = offline

    async def busy(_args: dict[str, Any]) -> list[Answer]:
        return [("refused", "failed", {"ok": False, "error": "busy"})]

    panel.answers["backup"] = busy

    with pytest.raises(UpdateBusyError):
        await SessionCarrier(hass, entity._entry_id).async_backup_panel()


async def test_a_busy_upload_slot_reads_as_busy(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline

    async def busy(_args: dict[str, Any]) -> list[Answer]:
        return [("refused", "failed", {"ok": False, "error": "upload-busy"})]

    panel.answers["install.apk"] = busy

    error = await _refused(entity)

    assert error.translation_key == "update_busy"
    assert "install.apk.commit" not in panel.ops
    assert not async_get_blobs(hass)._transfers


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("disabled", "UploadDisabledError"),
        ("no-root", "StagingUnavailableError"),
        ("stopping", "StagingUnavailableError"),
        ("blob-mismatch", "UpdateRejectedError"),
    ],
)
async def test_a_refused_stage_maps_like_its_http_status(
    hass: HomeAssistant, offline: Any, error: str, expected: str
) -> None:
    entity, panel = offline

    async def refuse(_args: dict[str, Any]) -> list[Answer]:
        return [("refused", "failed", {"ok": False, "error": error})]

    panel.answers["install.apk"] = refuse

    with pytest.raises(HaPaneldError) as raised:
        await SessionCarrier(hass, entity._entry_id).async_stage_apk(APK)
    assert type(raised.value).__name__ == expected
    # The app the panel never fetched is not kept in memory after the refusal.
    assert not async_get_blobs(hass)._transfers


async def test_a_build_without_the_names_is_refused_not_waited_for(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline

    async def unknown(_args: dict[str, Any]) -> list[Answer]:
        return [("refused", "unknown_command", None)]

    panel.answers["install.apk"] = unknown

    error = await _refused(entity)

    assert isinstance(error, _UpdateRefusalError)
    assert "install.apk.commit" not in panel.ops


async def test_a_commit_waiting_for_approval_asks_for_it(
    hass: HomeAssistant, offline: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    entity, panel = offline
    monkeypatch.setattr(transport_protocol, "COMMAND_TIMEOUT", 0.2)

    async def pending(_args: dict[str, Any]) -> list[Answer]:
        return [("pending_approval", None, None)]

    panel.answers["install.apk.commit"] = pending

    error = await _refused(entity)

    assert error.translation_key == "update_approval_required"


async def test_a_refused_commit_maps_like_its_http_status(
    hass: HomeAssistant, offline: Any
) -> None:
    entity, panel = offline

    async def refuse(_args: dict[str, Any]) -> list[Answer]:
        return [("refused", "failed", {"status": "disabled"})]

    panel.answers["install.apk.commit"] = refuse

    error = await _refused(entity)

    assert error.translation_key == "upload_disabled"


# --- the repair ------------------------------------------------------------------


async def test_a_session_managed_panel_has_an_update_route_while_unreachable(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    no_release: None,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    coordinator = entry.runtime_data.coordinator
    issue_id = no_update_route_issue_id(entry.entry_id)
    panel = FakePanel(hass, hass_ws_client, hass_client, hass_read_only_access_token)
    with _http_blocked():
        # The address stops answering, and the route checks that started settle.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        # Raised while the panel had no route of its own.
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="no_update_route",
        )
        await panel.connect()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        await panel.close()

    assert not entry.runtime_data.coordinator.reachable
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


# --- the blob view ---------------------------------------------------------------


@pytest.fixture
def no_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """Know of no release, so setting up a panel never reaches GitHub."""
    monkeypatch.setattr(
        StableReleaseCoordinator, "_async_update_data", AsyncMock(return_value=None)
    )


@pytest.fixture
async def bound(hass: HomeAssistant, hass_read_only_user: Any, no_release: None) -> str:
    entry = await _load(hass, hass_read_only_user.id)
    return entry.entry_id


async def test_a_download_is_served_once_to_its_panel_only(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    blobs = async_get_blobs(hass)
    _token, reference = blobs.download(bound, b"app bytes", "application/x-test")
    admin = await hass_client()
    panel = await hass_client(hass_read_only_access_token)

    # Another user, even an administrator, is told nothing and uses nothing up.
    assert (await admin.get(reference["url"])).status == 404
    first = await panel.get(reference["url"])
    assert first.status == 200
    assert await first.read() == b"app bytes"
    assert reference["sha256"] == hashlib.sha256(b"app bytes").hexdigest()
    assert (await panel.get(reference["url"])).status == 404


async def test_an_upload_is_received_once_from_its_panel_only(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    blobs = async_get_blobs(hass)
    token, received = blobs.upload(bound)
    url = f"/api/panel_assistant/blob/{token}"
    admin = await hass_client()
    panel = await hass_client(hass_read_only_access_token)

    assert (await admin.put(url, data=b"other")).status == 404
    assert not received.done()
    answer = await panel.put(url, data=b"backup")
    assert answer.status == 200
    assert await answer.json() == {
        "size": 6,
        "sha256": hashlib.sha256(b"backup").hexdigest(),
    }
    assert received.result() == b"backup"
    assert (await panel.put(url, data=b"again")).status == 404


async def test_an_expired_token_is_refused(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = async_get_blobs(hass)
    _token, reference = blobs.download(bound, b"app", "application/x-test")
    upload, _received = blobs.upload(bound)
    panel = await hass_client(hass_read_only_access_token)
    now = blob._now()
    monkeypatch.setattr(blob, "_now", lambda: now + blob.BLOB_LIFETIME_SECONDS + 1)

    assert (await panel.get(reference["url"])).status == 404
    assert (
        await panel.put(f"/api/panel_assistant/blob/{upload}", data=b"x")
    ).status == 404


async def test_a_token_answers_only_its_own_direction(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    blobs = async_get_blobs(hass)
    _token, reference = blobs.download(bound, b"app", "application/x-test")
    upload, received = blobs.upload(bound)
    upload_url = f"/api/panel_assistant/blob/{upload}"
    panel = await hass_client(hass_read_only_access_token)

    assert (await panel.put(reference["url"], data=b"x")).status == 404
    assert (await panel.get(upload_url)).status == 404
    # Neither wrong use spent the token.
    assert (await panel.get(reference["url"])).status == 200
    assert (await panel.put(upload_url, data=b"y")).status == 200
    assert received.result() == b"y"


async def test_an_oversize_upload_is_refused_and_fails_its_wait(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    blobs = async_get_blobs(hass)
    token, received = blobs.upload(bound, max_bytes=8)
    panel = await hass_client(hass_read_only_access_token)

    answer = await panel.put(f"/api/panel_assistant/blob/{token}", data=b"123456789")

    assert answer.status == 413
    assert isinstance(received.exception(), BlobTransferError)


async def test_a_streamed_upload_past_the_cap_is_refused(
    hass: HomeAssistant,
    bound: str,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    blobs = async_get_blobs(hass)
    token, received = blobs.upload(bound, max_bytes=8)
    panel = await hass_client(hass_read_only_access_token)

    async def chunks() -> Any:
        # No declared length, so only the reading loop can stop it.
        yield b"12345"
        yield b"6789"

    answer = await panel.put(f"/api/panel_assistant/blob/{token}", data=chunks())

    assert answer.status == 413
    assert isinstance(received.exception(), BlobTransferError)

"""A panel that may reach only Home Assistant still gets its WebView update."""

import asyncio
import hashlib
import os
import threading
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.webview_supply import (
    WebViewSupply,
    async_get_webview_supply,
    async_offer_webview,
)

from .loop_io_guard import LoopIoGuard
from .test_webview import observation
from .test_webview_update_outcomes import _install, _native

PAYLOAD = b"webview apk bytes " * 8192
SHA = hashlib.sha256(PAYLOAD).hexdigest()
URL = f"https://panel-assistant.io/downloads/webview/{SHA}.apk"


def _offered(state: str | None = None, *, accepts: bool = True) -> dict[str, Any]:
    document = observation(state)
    document["candidate"] = {"sha256": SHA, "size": len(PAYLOAD)}
    if accepts:
        document["accepts_blob"] = True
    return document


def _installed() -> dict[str, Any]:
    document = _offered("installed")
    document.update(eligible=False, reason="current")
    return document


def _failed(cause: str, supplied: str | None = None) -> dict[str, Any]:
    document = _offered("deferred")
    document["transaction"].update(reason="download_failed", cause=cause)
    if supplied is not None:
        document["transaction"]["supplied"] = supplied
    return document


def _connector_error(kind: type[aiohttp.ClientConnectorError]) -> Exception:
    key = SimpleNamespace(ssl=None, host="panel-assistant.io", port=443)
    return kind(key, OSError(101, "Network is unreachable"))  # type: ignore[arg-type]


@pytest.fixture
async def panel(hass: HomeAssistant, hass_read_only_user: Any) -> Any:
    """The update entity of a panel bound to its own Home Assistant user."""
    assert await async_setup_component(hass, "http", {})
    entity, _client, entry, issue_id = _native(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: hass_read_only_user.id}
    )
    session = SimpleNamespace(
        did=entry.unique_id,
        user_id=hass_read_only_user.id,
        capabilities={"management"},
    )
    return SimpleNamespace(
        entity=entity, entry=entry, issue_id=issue_id, session=lambda: session
    )


def _listing(directory: str) -> list[str]:
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


async def _files(hass: HomeAssistant) -> list[str]:
    return _listing((await async_get_webview_supply(hass)).directory)


async def test_a_panel_without_internet_installs_the_file_home_assistant_downloads(
    hass: HomeAssistant,
    panel: Any,
    aioclient_mock: Any,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    aioclient_mock.get(URL, content=PAYLOAD)
    reader = await hass_client(hass_read_only_access_token)
    administrator = await hass_client()
    sent: list[dict[str, Any]] = []
    # What each reader got from the blob: (status, body).
    reads: list[tuple[str, int, bytes]] = []

    async def manage(_hass: Any, _session: Any, operation: str, **kwargs: Any) -> Any:
        if operation == "webview.observe":
            return _offered() if not sent else _installed()
        sent.append(kwargs["args"])
        if (url := kwargs["args"].get("blob", {}).get("url")) is not None:
            # Only the panel's own user may fetch it, and only once.
            for who, client in (("admin", administrator), ("panel", reader)) * 2:
                response = await client.get(url)
                reads.append((who, response.status, await response.read()))
        return _installed()

    await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    assert len(sent) == 1
    assert sent[0]["artifact_sha256"] == SHA
    blob = sent[0].get("blob")
    assert blob is not None
    assert (blob["sha256"], blob["size"]) == (SHA, len(PAYLOAD))
    assert [(who, status) for who, status, _ in reads] == [
        ("admin", 404),
        ("panel", 200),
        ("admin", 404),
        ("panel", 404),
    ]
    assert reads[1][2] == PAYLOAD
    assert aioclient_mock.call_count == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, panel.issue_id) is None


async def test_a_panel_app_that_cannot_take_the_file_is_not_sent_one(
    hass: HomeAssistant, panel: Any, aioclient_mock: Any
) -> None:
    sent: list[dict[str, Any]] = []

    async def manage(_hass: Any, _session: Any, operation: str, **kwargs: Any) -> Any:
        if operation == "webview.observe":
            return _offered(accepts=False)
        sent.append(kwargs["args"])
        return _installed()

    await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    assert len(sent) == 1
    assert "blob" not in sent[0]
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    ("served", "reason"),
    [
        ({"exc": "unreachable"}, "panel-assistant.io could not be reached"),
        ({"exc": "unresolved"}, "the name panel-assistant.io could not be resolved"),
        ({"status": 503}, "panel-assistant.io answered HTTP 503"),
        # The right size, the wrong bytes: only the hash can tell.
        ({"content": bytes(len(PAYLOAD))}, "the file did not match its SHA-256"),
    ],
    ids=["unreachable", "unresolved", "http_503", "mismatch"],
)
async def test_when_neither_can_download_the_repair_and_log_say_why(
    hass: HomeAssistant,
    panel: Any,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    served: dict[str, Any],
    reason: str,
) -> None:
    if "exc" in served:
        kind = (
            aiohttp.ClientConnectorDNSError
            if served["exc"] == "unresolved"
            else aiohttp.ClientConnectorError
        )
        aioclient_mock.get(URL, exc=_connector_error(kind))
    else:
        aioclient_mock.get(URL, **served)
    sent: list[dict[str, Any]] = []

    async def manage(_hass: Any, _session: Any, operation: str, **kwargs: Any) -> Any:
        if operation == "webview.observe":
            return _offered()
        sent.append(kwargs["args"])
        return _failed("unreachable")

    with pytest.raises(Exception, match="WebView"):
        await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    # The panel was still asked, and tried panel-assistant.io itself.
    assert len(sent) == 1
    assert "blob" not in sent[0]
    assert ir.async_get(hass).async_get_issue(DOMAIN, panel.issue_id) is not None
    [warning] = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and SHA in record.getMessage()
    ]
    assert f"Home Assistant could not download it either: {reason}" in warning
    # Nothing half-written or unverified is kept.
    assert await _files(hass) == []


async def test_an_unpublished_file_is_not_a_repair_when_home_assistant_sees_404_too(
    hass: HomeAssistant,
    panel: Any,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.get(URL, status=404)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return _offered()
        return _failed("not_found")

    await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    assert ir.async_get(hass).async_get_issue(DOMAIN, panel.issue_id) is None
    assert any(
        record.levelname == "WARNING" and "not published" in record.getMessage()
        for record in caplog.records
    )


async def test_a_panel_that_cannot_take_home_assistants_copy_says_so(
    hass: HomeAssistant,
    panel: Any,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.get(URL, content=PAYLOAD)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return _offered()
        return _failed("unreachable", supplied="unavailable")

    with pytest.raises(Exception, match="WebView"):
        await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    assert any(
        "could not take Home Assistant's copy (unavailable)" in record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING"
    )


async def test_panels_updating_together_or_in_turn_download_the_file_once(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(URL, content=PAYLOAD)
    references: list[dict[str, Any]] = []

    async def offer(entry_id: str) -> None:
        async with async_offer_webview(hass, entry_id, SHA, len(PAYLOAD)) as reference:
            references.append(reference)

    await asyncio.gather(offer("one"), offer("two"))
    await offer("three")
    assert aioclient_mock.call_count == 1
    # Each panel gets its own one-use token for the same verified file.
    assert len({reference["url"] for reference in references}) == 3
    assert {reference["sha256"] for reference in references} == {SHA}
    assert await _files(hass) == [f"{SHA}.apk"]


async def test_the_file_is_held_without_tempfile_work_on_the_event_loop(
    hass: HomeAssistant, aioclient_mock: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding the temporary directory writes a probe file, so not on the loop."""
    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(URL, content=PAYLOAD)
    guard = LoopIoGuard()
    guard.install_tempfile(monkeypatch)
    async with async_offer_webview(hass, "one", SHA, len(PAYLOAD)):
        pass
    assert guard.on_loop == []
    assert await _files(hass) == [f"{SHA}.apk"]


async def test_the_file_is_deleted_an_hour_after_its_last_use(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(URL, content=PAYLOAD)
    async with async_offer_webview(hass, "one", SHA, len(PAYLOAD)):
        pass
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=59))
    # The removal runs in the executor, outside any tracked task.
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await _files(hass) == [f"{SHA}.apk"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=61))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await _files(hass) == []


async def test_a_size_beyond_any_webview_is_never_downloaded(
    hass: HomeAssistant, panel: Any, aioclient_mock: Any
) -> None:
    sent: list[dict[str, Any]] = []

    async def manage(_hass: Any, _session: Any, operation: str, **kwargs: Any) -> Any:
        if operation == "webview.observe":
            document = _offered()
            document["candidate"]["size"] = 600 * 1024 * 1024
            return document
        sent.append(kwargs["args"])
        return _installed()

    with patch(
        "custom_components.panel_assistant.webview_supply.shutil.disk_usage"
    ) as usage:
        usage.return_value = SimpleNamespace(free=10**12)
        await _install(hass, panel.entity, panel.entry, manage, session=panel.session)
    assert "blob" not in sent[0]
    assert aioclient_mock.call_count == 0


async def test_a_panel_that_leases_the_file_as_it_expires_keeps_it(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """The removal of an expired copy never deletes the file a new lease holds."""
    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(URL, content=PAYLOAD)
    async with async_offer_webview(hass, "one", SHA, len(PAYLOAD)):
        pass
    leased = threading.Event()
    remove = WebViewSupply._remove

    def held_back(supply: WebViewSupply, sha256: str) -> None:
        # Hold the expired copy's removal until the next panel holds the file.
        leased.wait(timeout=0.5)
        remove(supply, sha256)

    with patch.object(WebViewSupply, "_remove", held_back):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=61))
        await asyncio.sleep(0)
        async with async_offer_webview(hass, "two", SHA, len(PAYLOAD)):
            leased.set()
            await hass.async_block_till_done(wait_background_tasks=True)
            assert await _files(hass) == [f"{SHA}.apk"]


async def test_a_file_already_held_is_served_on_a_nearly_full_disk(
    hass: HomeAssistant, aioclient_mock: Any
) -> None:
    """Only a new download needs the room; the verified copy is reused."""
    assert await async_setup_component(hass, "http", {})
    aioclient_mock.get(URL, content=PAYLOAD)
    async with async_offer_webview(hass, "one", SHA, len(PAYLOAD)):
        pass
    with patch(
        "custom_components.panel_assistant.webview_supply.shutil.disk_usage"
    ) as usage:
        usage.return_value = SimpleNamespace(free=0)
        async with async_offer_webview(hass, "two", SHA, len(PAYLOAD)) as reference:
            assert reference["sha256"] == SHA
    assert aioclient_mock.call_count == 1

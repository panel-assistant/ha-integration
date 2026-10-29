"""The update entity discovers project snapshots before using the GitHub API."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from multidict import CIMultiDict
from yarl import URL

from custom_components.panel_assistant import release_catalog as catalog
from custom_components.panel_assistant.release import ReleaseResolutionError

from . import test_update_candidate_selection as selection
from .http_fakes import FakeContent, FakeResponse

key = selection.key
trust = selection.trust
real_release_reader = selection.real_release_reader

SITE_URL = URL("https://panel-assistant.io/releases/v1.json")


def _snapshot(github: Any, *, latest: bool = True) -> bytes:
    rows = json.loads(github._responses[str(catalog._RECENT_RELEASES_URL)].body)
    return json.dumps(
        {"schema": 1, "latest": rows[0] if latest and rows else None, "releases": rows}
    ).encode()


def _serve(github: Any, body: bytes, *, url: URL = SITE_URL) -> None:
    github._responses[str(url)] = FakeResponse(200, body, url)


def _api_requests(github: Any) -> list[str]:
    return [url for url in github.requests if URL(url).host == "api.github.com"]


@pytest.mark.parametrize("latest", [True, False], ids=["latest-stable", "recent-only"])
async def test_site_snapshot_offers_authenticated_update_without_github_api(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    latest: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    github, _ = selection._public_pool(
        key, monkeypatch, [("1.2.0", 950, (6, 6)), ("1.1.0", 900, (5, 5))]
    )
    _serve(github, _snapshot(github, latest=latest))
    entity, _ = await selection._panel(hass, monkeypatch, github, with_feed=False)
    assert entity.latest_version == "1.1.0"
    assert entity.state == "on"
    assert github.requests[0] == str(SITE_URL)
    assert not _api_requests(github)
    assert not [r for r in caplog.records if str(SITE_URL) in r.getMessage()]
    # The selected row traversed the existing signed descriptor/protocol checks.
    assert any(url.endswith("v1.1.0-install.json.sig") for url in github.requests)
    assert any(url.endswith("v1.1.0-protocol.json.sig") for url in github.requests)


@pytest.mark.parametrize(
    "failure",
    ["404", "timeout", "invalid-json", "schema", "null-row", "too-many", "duplicate"],
)
async def test_unreadable_site_snapshot_falls_back_and_offers_update(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    body = _snapshot(github)
    if failure == "invalid-json":
        body = b"<html>not json</html>"
    elif failure == "schema":
        body = body.replace(b'"schema": 1', b'"schema": 2')
    elif failure == "null-row":
        body = b'{"schema":1,"latest":null,"releases":[null]}'
    elif failure == "too-many":
        row = json.loads(body)["latest"]
        body = json.dumps({"schema": 1, "latest": row, "releases": [row] * 31}).encode()
    elif failure == "duplicate":
        body = b'{"schema":1,"schema":1,"latest":null,"releases":[]}'
    _serve(github, body)
    if failure == "404":
        github._responses[str(SITE_URL)] = FakeResponse(404, b"", SITE_URL)
    elif failure == "timeout":
        github._responses[str(SITE_URL)].content = FakeContent(error=TimeoutError())
    entity, _ = await selection._panel(hass, monkeypatch, github, with_feed=False)
    assert entity.latest_version == "1.1.0"
    assert entity.state == "on"
    assert github.requests[0] == str(SITE_URL)
    assert _api_requests(github)[:2] == [
        str(catalog._LATEST_RELEASE_URL),
        str(catalog._RECENT_RELEASES_URL),
    ]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(SITE_URL) in message
    assert "using GitHub" in message
    reason = {
        "404": "HTTP 404",
        "timeout": "TimeoutError",
        "invalid-json": "JSON",
        "schema": "schema",
    }.get(failure)
    if reason is not None:
        assert reason in message


async def test_old_readable_snapshot_never_downgrades_or_uses_api(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    _serve(github, _snapshot(github))
    entity, client = await selection._panel(
        hass, monkeypatch, github, installed=("1.2.0", 950), with_feed=False
    )
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, False)
    client.async_stage_apk.assert_not_awaited()
    assert not _api_requests(github)


async def test_tampered_snapshot_release_is_rejected_without_github_rescue(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    _serve(github, _snapshot(github))
    signature = next(url for url in github._responses if url.endswith(".sha256.sig"))
    github._responses[signature] = FakeResponse(200, b"forged", URL(signature))
    entity, client = await selection._panel(hass, monkeypatch, github, with_feed=False)
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, False)
    client.async_stage_apk.assert_not_awaited()
    assert signature in github.requests
    assert not _api_requests(github)


@pytest.mark.parametrize(
    "target",
    [
        "https://future-provider.example/releases/v1.json",
        "http://future-provider.example/v1.json",
    ],
)
async def test_catalogue_redirect_requires_https_and_keeps_asset_verification(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    target: str,
) -> None:
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    _serve(github, _snapshot(github), url=URL(target))
    github._responses[str(SITE_URL)] = FakeResponse(
        302, b"", SITE_URL, headers=CIMultiDict({"Location": target})
    )
    entity, _ = await selection._panel(hass, monkeypatch, github, with_feed=False)
    assert entity.latest_version == "1.1.0"
    assert entity.state == "on"
    assert github.requests[0] == str(SITE_URL)
    if target.startswith("https:"):
        assert target in github.requests
        assert not _api_requests(github)
    else:
        assert target not in github.requests
        assert _api_requests(github)


async def test_explicit_selection_authenticates_snapshot_row_without_api(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:

    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    _serve(github, _snapshot(github))
    await selection._panel(hass, monkeypatch, github, with_feed=False)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: github)
    bundle = await catalog.async_resolve_install_bundle_choice(hass, "v1.1.0")
    assert bundle.artifact.tag == "v1.1.0"
    assert bundle.artifact.descriptor.version_code == 900
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_resolve_install_bundle_choice(hass, "v1.9.0")
    assert not _api_requests(github)


async def test_both_sources_fail_warning_retains_site_reason(
    hass, monkeypatch, trust, key, caplog
):
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    github._responses[str(SITE_URL)] = FakeResponse(404, b"", SITE_URL)
    github._responses[str(catalog._LATEST_RELEASE_URL)] = FakeResponse(
        403, b"", catalog._LATEST_RELEASE_URL
    )
    await selection._panel(hass, monkeypatch, github, with_feed=False)
    failures = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "recent releases could not be authenticated" in r.getMessage()
    ]
    assert len(failures) == 1
    assert str(SITE_URL) in failures[0]
    assert "HTTP 404" in failures[0]
    assert "HTTP 403" in failures[0]


async def test_explicit_selection_warns_when_site_is_unreadable(
    hass, monkeypatch, trust, key, caplog
):
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    github._responses[str(SITE_URL)] = FakeResponse(404, b"", SITE_URL)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: github)
    bundle = await catalog.async_resolve_install_bundle_choice(hass, "v1.1.0")
    assert bundle.artifact.tag == "v1.1.0"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(SITE_URL) in warnings[0].getMessage()
    assert "HTTP 404" in warnings[0].getMessage()
    assert "using GitHub" in warnings[0].getMessage()


async def test_explicit_selection_both_sources_fail_retains_site_reason(
    hass, monkeypatch, trust, key, caplog
):
    github, _ = selection._public_pool(key, monkeypatch, [("1.1.0", 900, (5, 5))])
    github._responses[str(SITE_URL)] = FakeResponse(404, b"", SITE_URL)
    tag_url = URL(f"{catalog.ANDROID_RELEASES_API}/tags/v1.1.0")
    github._responses[str(tag_url)] = FakeResponse(403, b"", tag_url)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: github)
    with pytest.raises(ReleaseResolutionError) as failure:
        await catalog.async_resolve_install_bundle_choice(hass, "v1.1.0")
    assert str(SITE_URL) in str(failure.value)
    assert "HTTP 404" in str(failure.value)
    assert "HTTP 403" in str(failure.value)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

"""Offer and install the newest eligible authenticated APK across both sources."""

# --- candidate offers from authenticated sources ---

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import feed_coordinator, release, update_policy
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.const import CONF_PRERELEASE_PANEL_BUILDS, DOMAIN
from custom_components.panel_assistant.feed_coordinator import BuildFeedCoordinator

from . import test_build_feed as feed_http
from . import test_lan_staged_update as lan

key = lan.key
trust = lan.trust

_REAL_RELEASE_UPDATE = feed_coordinator.StableReleaseCoordinator._async_update_data
# version, Android code, authenticated protocol range
Candidate = tuple[str, int, tuple[int, int] | None]


@pytest.fixture(autouse=True)
def real_release_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt into the signed HTTP fixture instead of conftest's network suppression."""
    monkeypatch.setattr(
        feed_coordinator.StableReleaseCoordinator,
        "_async_update_data",
        _REAL_RELEASE_UPDATE,
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0")


def _public_pool(
    key: Any, monkeypatch: pytest.MonkeyPatch, candidates: list[Candidate]
) -> tuple[lan._GitHub, dict[tuple[str, int], bytes]]:
    responses: dict[str, lan._Response] = {}
    documents = []
    apks = {}
    fixture = lan._GitHub(key, package_id=SUCCESSOR_PACKAGE_ID)
    for version, code, protocol_range in candidates:
        tag = f"v{version}"
        root = f"https://github.com/panel-assistant/android/releases/download/{tag}"
        apk = f"public APK {version} build {code}".encode()
        with monkeypatch.context() as scoped:
            for name, value in {
                "TAG": tag,
                "VERSION": version,
                "CODE": code,
                "ROOT": root,
                "DESCRIPTOR_NAME": f"ha-paneld-{tag}-install.json",
                "ASSET_HOST_URL": f"https://release-assets.githubusercontent.com/{tag}",
            }.items():
                scoped.setattr(lan, name, value)
            fixture = lan._GitHub(
                key, apk=apk, signed_apk=apk, package_id=SUCCESSOR_PACKAGE_ID
            )
        document = json.loads(fixture._responses[str(release._LATEST_RELEASE_URL)].body)
        document["prerelease"] = "-" in version
        protocol_url = f"{root}/ha-paneld-{tag}-protocol.json"
        if protocol_range is not None:
            protocol = json.loads(fixture._responses[protocol_url].body)
            record = protocol["artifacts"][0]
            record["protocolMin"], record["protocolMax"] = protocol_range
            body = feed_http._canonical(protocol)
            fixture._responses[protocol_url] = lan._Response(
                200, body, URL(protocol_url)
            )
            fixture._responses[f"{protocol_url}.sig"] = lan._Response(
                200, lan._sign(key, body), URL(f"{protocol_url}.sig")
            )
        else:
            document["assets"] = [
                asset
                for asset in document["assets"]
                if "-protocol.json" not in asset["name"]
            ]
        fixture._responses[f"{release.ANDROID_RELEASES_API}/tags/{tag}"] = (
            lan._Response(
                200,
                json.dumps(document).encode(),
                URL(f"{release.ANDROID_RELEASES_API}/tags/{tag}"),
            )
        )
        documents.append(document)
        responses.update(fixture._responses)
        apks[version, code] = apk
    latest = release._LATEST_RELEASE_URL
    recent = URL(f"{release.ANDROID_RELEASES_API}?per_page=30")
    responses[str(latest)] = lan._Response(
        200 if documents else 404,
        json.dumps(documents[0] if documents else {}).encode(),
        latest,
    )
    responses[str(recent)] = lan._Response(200, json.dumps(documents).encode(), recent)
    fixture._responses = responses
    return fixture, apks


def _add_feed(
    github: lan._GitHub,
    key: Any,
    candidates: list[Candidate],
    *,
    broken: str | None = None,
    package_id: str = SUCCESSOR_PACKAGE_ID,
) -> dict[tuple[str, int], bytes]:
    entries = []
    ranges = []
    apks = {}
    for index, (version, code, protocol_range) in enumerate(candidates):
        apk = f"feed APK {version} build {code}".encode()
        sha = hashlib.sha256(apk).hexdigest()
        entry = feed_http._build_entry(
            code,
            versionName=version,
            packageId=package_id,
            launchComponent=LAUNCH_COMPONENTS[package_id][0],
            apkSha256=sha,
            apkPath=f"apks/{sha}.apk",
            apkSize=len(apk),
        )
        entries.append(entry)
        if protocol_range is not None:
            ranges.append(
                {
                    "apkSha256": sha,
                    "protocolMin": protocol_range[0],
                    "protocolMax": protocol_range[1],
                }
            )
        url = feed_http.FEED_URL.join(URL(entry["apkPath"]))
        github._responses[str(url)] = lan._Response(
            404 if index == 0 and broken == "missing" else 200,
            b"wrong APK bytes" if index == 0 and broken == "hash" else apk,
            url,
        )
        apks[version, code] = apk
    body = feed_http._canonical(feed_http._feed(entries))
    protocol = feed_http._canonical(
        {
            "schema": "io.github.maxlyth.hapaneld.protocol.v1",
            "artifacts": sorted(ranges, key=lambda record: record["apkSha256"]),
        }
    )
    for url, payload in (
        (feed_http.FEED_URL, body),
        (feed_http.SIGNATURE_URL, lan._sign(key, body)),
        (feed_http.PROTOCOL_URL, protocol),
        (feed_http.PROTOCOL_SIGNATURE_URL, lan._sign(key, protocol)),
    ):
        github._responses[str(url)] = lan._Response(200, payload, url)
    return apks


async def _panel(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    github: lan._GitHub,
    *,
    installed: tuple[str, int] = ("1.0.0", 800),
    with_feed: bool = True,
    prerelease: bool = False,
) -> tuple[Any, Any]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        options={CONF_PRERELEASE_PANEL_BUILDS: prerelease},
    )
    entry.add_to_hass(hass)
    entity, client = await lan._entity(
        hass, monkeypatch, github, package=SUCCESSOR_PACKAGE_ID
    )
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot,
        health=replace(
            snapshot.health, version=installed[0], version_code=installed[1]
        ),
    )
    entity._installed_code = installed[1]
    entity._code_key = (installed[0], snapshot.health.build)
    if with_feed:
        entity._feed = BuildFeedCoordinator(hass, feed_http.FEED_URL)
        await entity._feed.async_refresh()
    # This is the existing callback used when feed/release/health snapshots change.
    entity._handle_offer_refresh()
    await hass.async_block_till_done()
    return entity, client


@pytest.mark.parametrize(
    ("installed", "candidates", "expected"),
    [
        (
            ("1.0.0", 800),
            [("1.1.0", 900, (3, 3)), ("1.2.0", 850, (3, 3))],
            ("1.1.0", 900),
        ),
        (
            ("1.0.0", 800),
            [("1.2.0", 850, (3, 3)), ("1.2.0", 900, (3, 3))],
            ("1.2.0", 900),
        ),
        (
            ("1.0.0", 875),
            [("1.2.0", 850, (3, 3)), ("1.1.0", 900, (3, 3))],
            ("1.1.0", 900),
        ),
        (
            ("1.0.0", 800),
            [("1.2.0", 950, None), ("1.1.0", 900, (3, 3))],
            ("1.1.0", 900),
        ),
        (
            ("1.0.0", 800),
            [("1.2.0", 950, (5, 5)), ("1.1.0", 900, (3, 3))],
            ("1.1.0", 900),
        ),
    ],
    ids=[
        "code-before-semantic",
        "same-semantic-code-tie",
        "installed-code-before-rank",
        "unknown-range",
        "incompatible-range",
    ],
)
async def test_feed_offer_filters_eligibility_before_code_ranking(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    installed: tuple[str, int],
    candidates: list[Candidate],
    expected: tuple[str, int],
) -> None:
    github, _ = _public_pool(key, monkeypatch, [])
    _add_feed(github, key, candidates)
    entity, _ = await _panel(hass, monkeypatch, github, installed=installed)
    assert entity.latest_version == f"{expected[0]} build {expected[1]}"
    assert entity.state == "on"


@pytest.mark.parametrize(
    ("installed", "candidates", "expected"),
    [
        (("1.0.0", 875), [("1.2.0", 850, (3, 3)), ("1.1.0", 900, (3, 3))], "1.1.0"),
        (("1.1.0", 800), [("1.1.0", 900, (3, 3))], "1.1.0 build 900"),
    ],
    ids=["installed-code-before-rank", "same-semantic-higher-code"],
)
async def test_public_release_offers_newest_eligible_build(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    installed: tuple[str, int],
    candidates: list[Candidate],
    expected: str,
) -> None:
    github, _ = _public_pool(key, monkeypatch, candidates)
    entity, _ = await _panel(
        hass, monkeypatch, github, installed=installed, with_feed=False
    )
    assert entity.latest_version == expected
    assert entity.state == "on"


# The 0.9.11 line was briefly numbered 1.0.0-rc1 (builds 1117 to 1176), then
# renumbered. Some panels still run those builds.
RENUMBERED = ("1.0.0-rc1", 1168)
RENAMED_NEXT = ("0.9.11-rc1", 1199, (3, 3))


async def test_feed_build_with_higher_code_upgrades_a_renumbered_line(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github, _ = _public_pool(key, monkeypatch, [])
    apks = _add_feed(github, key, [(*RENUMBERED, (3, 3)), RENAMED_NEXT])
    entity, client = await _panel(
        hass, monkeypatch, github, installed=RENUMBERED, prerelease=True
    )
    assert entity.latest_version == "0.9.11-rc1 build 1199"
    assert entity.state == "on"
    _restart_for_install(entity, client, RENAMED_NEXT[0], RENAMED_NEXT[1])
    await entity.async_install(None, False)
    client.async_stage_apk.assert_awaited_once_with(apks[RENAMED_NEXT[:2]])
    client.async_commit_apk.assert_awaited_once_with("tok-1")


@pytest.mark.parametrize(
    "installed",
    [("0.9.11-rc1", 1193), ("1.0.0-rc2", 1180)],
    ids=["pre-1.0-installed", "post-1.0-installed"],
)
async def test_feed_build_with_lower_code_is_never_offered_or_installed(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    installed: tuple[str, int],
) -> None:
    github, _ = _public_pool(key, monkeypatch, [])
    _add_feed(github, key, [(*RENUMBERED, (3, 3))])
    entity, client = await _panel(
        hass, monkeypatch, github, installed=installed, prerelease=True
    )
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, False)
    if installed[0].startswith("1."):
        # Asked for by number, a post-1.0 panel still refuses the older build.
        with pytest.raises(HomeAssistantError):
            await entity.async_install(str(RENUMBERED[1]), False)
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


async def test_public_release_keeps_name_order_on_a_renumbered_line(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github, _ = _public_pool(key, monkeypatch, [RENAMED_NEXT])
    entity, client = await _panel(
        hass,
        monkeypatch,
        github,
        installed=RENUMBERED,
        with_feed=False,
        prerelease=True,
    )
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, False)
    client.async_stage_apk.assert_not_awaited()


def _restart_for_install(entity: Any, client: Any, version: str, code: int) -> None:
    client.async_stage_apk.return_value = lan._preview(
        package=SUCCESSOR_PACKAGE_ID, version=version
    )
    client.async_get_version_code.return_value = (version, code)

    async def restarted() -> None:
        snapshot = entity.coordinator.data
        entity.coordinator.data = replace(
            snapshot,
            health=replace(
                snapshot.health, version=version, version_code=code, build="2000"
            ),
        )

    entity.coordinator.async_request_refresh = AsyncMock(side_effect=restarted)


@pytest.mark.parametrize(
    ("newest_source", "installed_code", "newest"),
    [
        pytest.param("feed", 800, ("1.2.0", 850, (3, 3)), id="feed"),
        pytest.param("public", 800, ("1.2.0", 850, (3, 3)), id="public"),
        pytest.param(
            "feed", 900, ("1.1.0", 900, (3, 3)), id="same-code-newer-semantic"
        ),
    ],
)
async def test_default_install_uses_the_newest_offer_from_either_source(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    newest_source: str,
    installed_code: int,
    newest: Candidate,
) -> None:
    older = ("1.0.1" if installed_code == 900 else "1.1.0", 900, (3, 3))
    public = newest if newest_source == "public" else older
    feed = newest if newest_source == "feed" else older
    github, public_apks = _public_pool(key, monkeypatch, [public])
    feed_apks = _add_feed(github, key, [feed])
    entity, client = await _panel(
        hass, monkeypatch, github, installed=("1.0.0", installed_code)
    )
    expected_label = (
        f"{newest[0]} build {newest[1]}" if newest_source == "feed" else newest[0]
    )
    assert entity.latest_version == expected_label
    assert entity.state == "on"
    _restart_for_install(entity, client, newest[0], newest[1])
    await entity.async_install(None, False)
    expected_apk = (feed_apks if newest_source == "feed" else public_apks)[newest[:2]]
    client.async_stage_apk.assert_awaited_once_with(expected_apk)
    client.async_commit_apk.assert_awaited_once_with("tok-1")


@pytest.mark.parametrize("prerelease", [False, True], ids=["stable", "opt-in"])
async def test_channel_filters_both_sources_before_ranking(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    prerelease: bool,
) -> None:
    github, _ = _public_pool(
        key, monkeypatch, [("1.3.0-rc1", 950, (3, 3)), ("1.1.0", 900, (3, 3))]
    )
    _add_feed(github, key, [("1.2.0-rc1", 940, (3, 3)), ("1.2.0", 930, (3, 3))])
    entity, _ = await _panel(hass, monkeypatch, github, prerelease=prerelease)
    assert entity.latest_version.split(" build ")[0] == (
        "1.3.0-rc1" if prerelease else "1.2.0"
    )
    assert entity.state == "on"


@pytest.mark.parametrize("broken", ["missing", "hash"])
async def test_selected_feed_apk_failure_withholds_offer_without_falling_back(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    broken: str,
) -> None:
    github, _ = _public_pool(key, monkeypatch, [("1.1.0", 900, (3, 3))])
    apks = _add_feed(
        github, key, [("1.3.0", 950, (3, 3)), ("1.2.0", 930, (3, 3))], broken=broken
    )
    entity, client = await _panel(hass, monkeypatch, github)
    selected_sha = hashlib.sha256(apks["1.3.0", 950]).hexdigest()
    selected_url = feed_http.FEED_URL.join(URL(f"apks/{selected_sha}.apk"))
    assert str(selected_url) in github.requests
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"
    with pytest.raises(HomeAssistantError):
        await entity.async_install(None, False)
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()


@pytest.mark.parametrize("incompatible_source", ["feed", "public"])
async def test_incompatible_semantic_head_cannot_hide_other_source(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    incompatible_source: str,
) -> None:
    bad, good = ("1.3.0", 950, (5, 5)), ("1.2.0", 900, (3, 3))
    public = bad if incompatible_source == "public" else good
    feed = bad if incompatible_source == "feed" else good
    github, _ = _public_pool(key, monkeypatch, [public])
    _add_feed(github, key, [feed])
    entity, _ = await _panel(hass, monkeypatch, github)
    assert entity.latest_version.split(" build ")[0] == good[0]
    assert entity.state == "on"


async def test_equal_rank_sources_preserve_the_verified_feed_artifact(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    candidate = ("1.2.0", 900, (3, 3))
    github, public_apks = _public_pool(key, monkeypatch, [candidate])
    feed_apks = _add_feed(github, key, [candidate])
    entity, client = await _panel(hass, monkeypatch, github)
    assert entity.latest_version == "1.2.0 build 900"
    assert entity.state == "on"
    feed_apk = feed_apks[candidate[:2]]
    assert feed_apk != public_apks[candidate[:2]]
    sha = hashlib.sha256(feed_apk).hexdigest()
    url = feed_http.FEED_URL.join(URL(f"apks/{sha}.apk"))
    github._responses[str(url)] = lan._Response(404, b"", url)
    _restart_for_install(entity, client, candidate[0], candidate[1])
    await entity.async_install(None, False)
    client.async_stage_apk.assert_awaited_once_with(feed_apk)
    client.async_commit_apk.assert_awaited_once_with("tok-1")


async def test_configured_feed_does_not_hide_an_installed_bridge_handover(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
) -> None:
    github = lan._GitHub(key, package_id=SUCCESSOR_PACKAGE_ID, bridge=b"signed bridge")
    _add_feed(
        github, key, [(lan.VERSION, lan.CODE, (3, 3))], package_id=LEGACY_PACKAGE_ID
    )
    entity, client = await lan._entity(
        hass, monkeypatch, github, package=LEGACY_PACKAGE_ID
    )
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot,
        health=replace(snapshot.health, version=lan.VERSION, version_code=lan.CODE),
    )
    entity._installed_code = lan.CODE
    entity._code_key = (lan.VERSION, snapshot.health.build)
    entity._feed = BuildFeedCoordinator(hass, feed_http.FEED_URL)
    await entity._feed.async_refresh()
    client.async_get_successor_capability = AsyncMock(
        return_value=(SUCCESSOR_PACKAGE_ID, lan.VERSION, lan.CODE, False)
    )
    lan._move_entry(hass, entity)
    entity._handle_offer_refresh()
    await hass.async_block_till_done()
    assert entity.installed_version == f"{lan.VERSION} (bridge)"
    assert entity.latest_version == lan.VERSION
    assert entity.state == "on"
    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_install(None, False)
    assert caught.value.translation_key == "move_with_repair"
    assert lan._move_repair(hass) is not None
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    client.async_start_panel_update.assert_not_awaited()
    client.async_offer_installed_successor.assert_not_awaited()
    snapshot = entity.coordinator.data
    entity.coordinator.data = replace(
        snapshot, health=replace(snapshot.health, package=SUCCESSOR_PACKAGE_ID)
    )
    entity._handle_offer_refresh()
    await hass.async_block_till_done()
    assert entity.state == "off"
    assert "(bridge)" not in entity.installed_version


@pytest.mark.parametrize("verification", ["cached", "in-flight"])
async def test_withdrawn_feed_candidate_cannot_return_verified_bytes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    trust: None,
    key: Any,
    verification: str,
) -> None:
    """Refresh revokes completed and unfinished demand verification alike."""
    head, demanded = ("1.2.0", 950, (3, 3)), ("1.1.0", 900, (3, 3))
    github, _ = _public_pool(key, monkeypatch, [])
    apks = _add_feed(github, key, [head, demanded])
    monkeypatch.setattr(feed_coordinator, "async_get_clientsession", lambda _h: github)
    coordinator = BuildFeedCoordinator(hass, feed_http.FEED_URL)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    build = coordinator.data.find(demanded[1], SUCCESSOR_PACKAGE_ID)
    assert build is not None
    assert coordinator.verified_apk(build) is None
    target_apk = apks[demanded[:2]]
    target_url = build.apk_url
    entered, finish = asyncio.Event(), asyncio.Event()

    if verification == "cached":
        assert await coordinator.async_verify_build(build) == target_apk
        assert coordinator.verified_apk(build) == target_apk
        task = None
    else:

        class PausedDownload:
            async def iter_chunked(self, _limit: int):
                entered.set()
                await finish.wait()
                yield target_apk

        github._responses[str(target_url)].content = PausedDownload()
        task = hass.async_create_task(
            coordinator.async_verify_build(build), "verify feed candidate"
        )
        async with asyncio.timeout(1):
            await entered.wait()

    _add_feed(github, key, [head])
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.verified_apk(build) is None
    if task is not None:
        finish.set()
        assert await task is None
        assert coordinator.verified_apk(build) is None
    else:
        # Reintroducing metadata cannot resurrect revoked cached bytes when
        # the origin now cannot supply the exact artifact.
        _add_feed(github, key, [head, demanded])
        github._responses[str(target_url)] = lan._Response(404, b"", target_url)
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.verified_apk(build) is None
        assert await coordinator.async_verify_build(build) is None
    assert str(target_url) in github.requests

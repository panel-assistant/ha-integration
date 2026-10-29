"""Discovery includes only complete, supported release choices."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.panel_assistant import release_catalog as catalog
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.release import ReleaseResolutionError

from .http_fakes import FakeResponse, FakeSession

# Captured before conftest stubs the release refresh for unrelated tests.
_REAL_STABLE_UPDATE = StableReleaseCoordinator._async_update_data


def document(tag="v1.2.3", *, prerelease=False):
    apk = f"panel-assistant-{tag}-manual-setup-required.apk"
    descriptor = f"ha-paneld-{tag}-install.json"
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": prerelease,
        "body": "Untrusted release prose",
        "assets": [
            {
                "name": name,
                "browser_download_url": (
                    f"https://github.com/panel-assistant/android/releases/download/{tag}/{name}"
                ),
            }
            for name in [
                apk,
                apk + ".sha256",
                apk + ".sha256.sig",
                descriptor,
                descriptor + ".sig",
            ]
        ],
    }


def session(latest, recent):
    return FakeSession(
        {
            str(url): FakeResponse(200, json.dumps(value).encode(), url)
            for url, value in [
                (catalog._LATEST_RELEASE_URL, latest),
                (catalog._RECENT_RELEASES_URL, recent),
            ]
        }
    )


@pytest.fixture
def discovery_authentication(monkeypatch):
    """Isolate discovery from signatures, covered by the signed fixtures below."""

    async def authenticate(session, tag, *, release_body=None):
        return SimpleNamespace(artifact=SimpleNamespace(tag=tag, version=tag[1:]))

    monkeypatch.setattr(catalog, "_async_release_choice", authenticate)


async def test_recent_stable_and_exact_rc_candidates_are_discovered(
    discovery_authentication,
):
    rc = document("v1.3.0-rc2", prerelease=True)
    client = session(document(), [rc, document("v1.0.0"), rc])
    choices = await catalog.async_resolve_update_candidates(client)
    assert [choice.artifact.tag for choice in choices] == [
        "v1.3.0-rc2",
        "v1.2.3",
        "v1.0.0",
    ]
    assert [url for url, _ in client.requests] == [
        str(catalog._SITE_RELEASES_URL),
        str(catalog._LATEST_RELEASE_URL),
        str(catalog._RECENT_RELEASES_URL),
    ]
    for _, options in client.requests:
        assert options["allow_redirects"] is False
        assert "Authorization" not in options["headers"]
        assert options["timeout"].total == 10


async def test_total_choice_limit_includes_latest_stable(discovery_authentication):
    recent = [document(f"v1.3.0-rc{n}", prerelease=True) for n in range(30, 0, -1)]
    choices = await catalog.async_resolve_update_candidates(session(document(), recent))
    assert len(choices) == 30
    assert {choice.artifact.tag for choice in choices} == {
        "v1.2.3",
        *(f"v1.3.0-rc{n}" for n in range(30, 1, -1)),
    }


@pytest.mark.parametrize("missing", range(5))
async def test_every_install_asset_is_required(missing, discovery_authentication):
    latest = document()
    latest["assets"].pop(missing)
    rc = document("v1.3.0-rc2", prerelease=True)
    choices = await catalog.async_resolve_update_candidates(session(latest, [rc]))
    assert [choice.artifact.tag for choice in choices] == ["v1.3.0-rc2"]


async def test_latest_cannot_substitute_rc_and_bad_rows_are_ignored(
    discovery_authentication,
):
    rc = document("v1.3.0-rc2", prerelease=True)
    choices = await catalog.async_resolve_update_candidates(session(rc, [None, rc]))
    assert [choice.artifact.tag for choice in choices] == ["v1.3.0-rc2"]


@pytest.mark.parametrize(
    "change", ["missing", "url", "duplicate", "draft", "flag", "tag"]
)
async def test_bad_choices_are_omitted(change, discovery_authentication):
    candidate = document("v1.3.0-rc2", prerelease=True)
    if change == "missing":
        candidate["assets"] = candidate["assets"][:3]
    elif change == "url":
        candidate["assets"][0]["browser_download_url"] = "https://example.com/a.apk"
    elif change == "duplicate":
        candidate["assets"].append(candidate["assets"][0])
    elif change == "draft":
        candidate["draft"] = True
    elif change == "flag":
        candidate["prerelease"] = False
    else:
        candidate["tag_name"] = "v1.3.0-beta1"
    latest = document()
    latest["assets"] = []
    assert (
        await catalog.async_resolve_update_candidates(session(latest, [candidate]))
        == []
    )


@pytest.mark.parametrize("recent", [{}, [None] * 31])
async def test_invalid_catalog_shape_or_count_fails(recent):
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_resolve_update_candidates(session(document(), recent))


@pytest.mark.parametrize(
    "body,status",
    [
        (b"x" * (catalog._MAX_CATALOG_BYTES + 1), 200),
        (b"[]", 403),
        (b"[]", 302),
        (b'{"duplicate":1,"duplicate":2}', 200),
        (b"[NaN]", 200),
        (b"\xff", 200),
    ],
)
async def test_bounded_and_unambiguous_response(body, status):
    client = session(document(), [])
    client.responses[str(catalog._RECENT_RELEASES_URL)] = FakeResponse(
        status, body, catalog._RECENT_RELEASES_URL
    )
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_resolve_update_candidates(client)


def authenticated_session(signing_key, rows):
    """Serve real signed release bytes through the production HTTP seam."""
    from yarl import URL

    from .test_release import _canonical_descriptor, _descriptor_document, _signature

    responses = {}
    docs = []
    for index, (tag, protocol_range) in enumerate(rows, 1):
        doc = document(tag, prerelease="-" in tag)
        digest = f"{index:064x}"
        apk = doc["assets"][0]["name"]
        root = f"https://github.com/panel-assistant/android/releases/download/{tag}"
        descriptor = _canonical_descriptor(
            _descriptor_document(
                releaseTag=tag,
                versionName=tag[1:],
                apkName=apk,
                apkSha256=digest,
                versionCode=700 + index,
            )
        )
        checksum = f"{digest}  {apk}\n".encode()
        protocol_name = f"ha-paneld-{tag}-protocol.json"
        if protocol_range is not None:
            body = (
                json.dumps(
                    {
                        "schema": "io.github.maxlyth.hapaneld.protocol.v1",
                        "artifacts": [
                            {
                                "apkSha256": digest,
                                "protocolMin": protocol_range[0],
                                "protocolMax": protocol_range[1],
                            }
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
            for suffix, data in [("", body), (".sig", _signature(signing_key, body))]:
                name = protocol_name + suffix
                doc["assets"].append(
                    {"name": name, "browser_download_url": f"{root}/{name}"}
                )
                url = URL(f"{root}/{name}")
                responses[str(url)] = FakeResponse(200, data, url)
        for name, data in [
            (apk + ".sha256", checksum),
            (apk + ".sha256.sig", _signature(signing_key, checksum)),
            (f"ha-paneld-{tag}-install.json", descriptor),
            (f"ha-paneld-{tag}-install.json.sig", _signature(signing_key, descriptor)),
        ]:
            url = URL(f"{root}/{name}")
            responses[str(url)] = FakeResponse(200, data, url)
        url = URL(f"{catalog.ANDROID_RELEASES_API}/tags/{tag}")
        responses[str(url)] = FakeResponse(200, json.dumps(doc).encode(), url)
        docs.append(doc)
    responses[str(catalog._LATEST_RELEASE_URL)] = FakeResponse(
        200,
        json.dumps(next(d for d in docs if not d["prerelease"])).encode(),
        catalog._LATEST_RELEASE_URL,
    )
    responses[str(catalog._RECENT_RELEASES_URL)] = FakeResponse(
        200, json.dumps(docs).encode(), catalog._RECENT_RELEASES_URL
    )
    return FakeSession(responses)


@pytest.fixture
def release_key(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa

    from .test_release import _install_test_key

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _install_test_key(monkeypatch, key)
    return key


@pytest.mark.parametrize(
    "pa_version,expected",
    [
        ("1.0.0", "v1.2.0"),
        ("1.0.0-rc2", "v1.3.0-rc2"),
    ],
)
async def test_default_install_follows_running_pa_and_skips_incompatible_head(
    hass, monkeypatch, release_key, pa_version, expected
):
    from custom_components.panel_assistant import update_policy

    client = authenticated_session(
        release_key,
        [
            ("v1.4.0", (6, 6)),
            ("v1.3.0-rc2", (5, 5)),
            ("v1.2.0", (5, 5)),
        ],
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    result = await catalog.async_resolve_install_choice(hass, None)
    assert result.tag == expected
    assert not any(url.endswith(".apk") for url, _ in client.requests)


_PREVIEW_CHAIN = [
    "v0.9.0",
    "v1.0.0-preview1",
    "v1.0.0-preview2",
    "v1.0.0-rc1",
    "v1.0.0",
]


@pytest.mark.parametrize("published", range(1, len(_PREVIEW_CHAIN) + 1))
async def test_prerelease_channel_offers_each_preview_then_rc_then_final(
    hass, monkeypatch, release_key, published
):
    """0.9.0 < 1.0.0-preview1 < 1.0.0-preview2 < 1.0.0-rc1 < 1.0.0, in any order."""
    from custom_components.panel_assistant import update_policy

    tags = _PREVIEW_CHAIN[:published]
    client = authenticated_session(release_key, [(tag, (5, 5)) for tag in tags[::-1]])
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0-preview1")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    assert (await catalog.async_resolve_install_choice(hass, None)).tag == tags[-1]


async def test_stable_channel_never_offers_a_preview(hass, monkeypatch, release_key):
    from custom_components.panel_assistant import update_policy

    client = authenticated_session(
        release_key, [("v1.0.0-preview1", (5, 5)), ("v0.9.0", (5, 5))]
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.9.0")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    assert (await catalog.async_resolve_install_choice(hass, None)).tag == "v0.9.0"


@pytest.mark.parametrize("protocol_range", [None, (6, 6)])
async def test_explicit_install_cannot_bypass_candidate_compatibility(
    hass, monkeypatch, release_key, protocol_range
):
    client = authenticated_session(release_key, [("v1.4.0", protocol_range)])
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_resolve_install_bundle_choice(hass, "v1.4.0")


async def test_prerelease_default_accepts_newer_final_promotion(
    hass, monkeypatch, release_key
):
    from custom_components.panel_assistant import update_policy

    client = authenticated_session(
        release_key, [("v1.3.0", (5, 5)), ("v1.3.0-rc10", (5, 5))]
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0-rc1")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    assert (await catalog.async_resolve_install_choice(hass, None)).tag == "v1.3.0"


@pytest.mark.parametrize(
    "pa_version,version", [("1.0.0", "1.2.0"), ("1.0.0-rc2", "1.2.0-rc3")]
)
async def test_feed_only_recommendation_resolves_the_same_eligible_build(
    hass, monkeypatch, pa_version, version
):
    from dataclasses import replace

    from custom_components.panel_assistant import update_policy

    from .test_feed_install_paths import _build, _install_feed

    build = replace(_build(), version_name=version)
    _install_feed(hass, build)
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    monkeypatch.setattr(
        catalog,
        "async_resolve_update_candidates",
        AsyncMock(side_effect=ReleaseResolutionError),
    )
    choices = await catalog.async_list_install_choices(hass)
    assert choices[0]["tag"] == "build-772-successor"
    assert choices[0]["prerelease"] == ("-" in version)
    assert (await catalog.async_resolve_install_choice(hass, None)).tag == choices[0][
        "tag"
    ]


async def test_equal_version_default_uses_newest_authenticated_build(
    hass, monkeypatch, release_key
):
    from dataclasses import replace

    from custom_components.panel_assistant import update_policy

    from .test_feed_install_paths import _build, _install_feed

    _install_feed(hass, replace(_build(), version_name="1.2.0"))
    client = authenticated_session(release_key, [("v1.2.0", (5, 5))])
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    choices = await catalog.async_list_install_choices(hass)
    assert choices[0]["tag"] == "build-772-successor"
    artifact = await catalog.async_resolve_install_choice(hass, None)
    assert artifact.tag == choices[0]["tag"]
    assert artifact.descriptor.version_code == 772


@pytest.mark.parametrize(
    "pa_version,expected",
    [("1.0.0", ["v1.2.0"]), ("1.0.0-rc1", ["build-774-successor", "v1.2.0"])],
)
async def test_install_list_offers_newest_compatible_choice_per_pa_channel(
    hass, monkeypatch, release_key, pa_version, expected
):
    from dataclasses import replace

    from custom_components.panel_assistant import update_policy

    from .test_feed_install_paths import _build, _install_feed

    _install_feed(
        hass,
        replace(_build(774), version_name="1.3.0-rc2"),
        replace(_build(773), version_name="1.3.0-rc2"),
        replace(_build(772), version_name="1.1.0"),
        replace(_build(775), version_name="1.4.0", protocol_min=6, protocol_max=6),
    )
    client = authenticated_session(
        release_key,
        [
            ("v1.4.0", (6, 6)),
            ("v1.3.0-rc1", (5, 5)),
            ("v1.2.0", (5, 5)),
            ("v1.1.0", (5, 5)),
        ],
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    choices = await catalog.async_list_install_choices(hass)
    assert [choice["tag"] for choice in choices] == expected
    assert (await catalog.async_resolve_install_choice(hass, None)).tag == expected[0]
    assert not any(url.endswith(".apk") for url, _ in client.requests)


@pytest.mark.parametrize("github_available", [False, True])
async def test_install_recommendation_follows_feed_code_when_names_regress(
    hass, monkeypatch, release_key, github_available
):
    from dataclasses import replace

    from custom_components.panel_assistant import update_policy

    from .test_feed_install_paths import _build, _install_feed

    _install_feed(
        hass,
        replace(_build(1246), version_name="1.0.0-rc1"),
        replace(_build(1670), version_name="1.0.0-preview1"),
        replace(
            _build(1671),
            version_name="1.0.0-preview1",
            protocol_min=6,
            protocol_max=6,
        ),
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0-preview1")
    if github_available:
        client = authenticated_session(release_key, [("v0.9.11", (5, 5))])
        monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    else:
        monkeypatch.setattr(
            catalog,
            "async_resolve_update_candidates",
            AsyncMock(side_effect=ReleaseResolutionError),
        )

    choices = await catalog.async_list_install_choices(hass)
    assert [choice["tag"] for choice in choices] == (
        ["build-1670-successor", "v0.9.11"]
        if github_available
        else ["build-1670-successor"]
    )
    recommended = await catalog.async_resolve_install_choice(hass, None)
    assert recommended.tag == choices[0]["tag"]
    assert recommended.descriptor.version_code == 1670
    # Removing an older build from the menu never changes an exact saved choice.
    assert (
        await catalog.async_resolve_install_choice(hass, "build-1246-successor")
    ).tag == ("build-1246-successor")


async def test_install_recommendation_keeps_github_name_order_over_feed_code(
    hass, monkeypatch, release_key
):
    from dataclasses import replace

    from custom_components.panel_assistant import update_policy

    from .test_feed_install_paths import _build, _install_feed

    _install_feed(
        hass,
        replace(_build(1246), version_name="1.0.0-rc1"),
        replace(_build(1670), version_name="1.0.0-preview1"),
    )
    # The newer GitHub name has the lower code, including compared with the feed.
    client = authenticated_session(
        release_key, [("v1.1.0", (5, 5)), ("v1.0.1", (5, 5))]
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0-preview1")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)

    choices = await catalog.async_list_install_choices(hass)
    assert [choice["tag"] for choice in choices] == ["v1.1.0", "build-1670-successor"]
    recommended = await catalog.async_resolve_install_choice(hass, None)
    assert recommended.tag == choices[0]["tag"]
    assert recommended.descriptor.version_code == 701


def _fail_tags(client, tags, status=403):
    from yarl import URL

    for tag in tags:
        url = URL(f"{catalog.ANDROID_RELEASES_API}/tags/{tag}")
        client.responses[str(url)] = FakeResponse(status, b"", url)


@pytest.mark.parametrize("site", [FakeResponse(404, b""), FakeResponse(200, b"{")])
async def test_site_list_failure_is_logged_before_the_github_fallback(
    release_key, caplog, site
):
    import logging

    client = authenticated_session(release_key, [("v1.2.0", (5, 5))])
    site.url = catalog._SITE_RELEASES_URL
    client.responses[str(catalog._SITE_RELEASES_URL)] = site
    caplog.set_level(logging.DEBUG, logger=catalog.__name__)
    candidates = await catalog.async_resolve_update_candidates(client)
    assert [bundle.artifact.tag for bundle in candidates] == ["v1.2.0"]
    assert str(catalog._SITE_RELEASES_URL) in caplog.text
    if site.status == 404:
        assert "HTTP 404" in caplog.text


def _unreachable_tags(client, tags):
    from aiohttp import ClientConnectionError
    from yarl import URL

    urls = {str(URL(f"{catalog.ANDROID_RELEASES_API}/tags/{tag}")) for tag in tags}
    get = client.get

    def unreachable(url, **kwargs):
        if str(url) in urls:
            raise ClientConnectionError("unreachable")
        return get(url, **kwargs)

    client.get = unreachable


@pytest.mark.parametrize("failure", ["status", "connection"])
async def test_every_release_download_failing_is_a_failed_check(release_key, failure):
    client = authenticated_session(
        release_key, [("v1.2.0", (5, 5)), ("v1.1.0", (5, 5))]
    )
    fail = _fail_tags if failure == "status" else _unreachable_tags
    fail(client, ["v1.2.0", "v1.1.0"])
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_resolve_update_candidates(client)


async def test_one_failed_release_leaves_the_other_offered(release_key):
    client = authenticated_session(
        release_key, [("v1.2.0", (5, 5)), ("v1.1.0", (5, 5))]
    )
    _fail_tags(client, ["v1.2.0"])
    candidates = await catalog.async_resolve_update_candidates(client)
    assert [bundle.artifact.tag for bundle in candidates] == ["v1.1.0"]


async def test_every_release_rejected_is_an_empty_successful_check(
    release_key, monkeypatch
):
    from cryptography.hazmat.primitives.asymmetric import rsa

    client = authenticated_session(
        release_key, [("v1.2.0", (5, 5)), ("v1.1.0", (5, 5))]
    )
    from .test_release import _install_test_key

    _install_test_key(
        monkeypatch, rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    assert await catalog.async_resolve_update_candidates(client) == []


async def test_stable_coordinator_keeps_its_offer_when_every_download_fails(
    hass, release_key, caplog, monkeypatch
):
    import logging
    from unittest.mock import patch

    from custom_components.panel_assistant import feed_coordinator, update_policy

    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0")

    client = authenticated_session(release_key, [("v1.1.0", (5, 5))])
    coordinator = feed_coordinator.StableReleaseCoordinator(hass)
    caplog.set_level(logging.WARNING)
    with (
        patch.object(
            feed_coordinator.StableReleaseCoordinator,
            "_async_update_data",
            _REAL_STABLE_UPDATE,
        ),
        patch.object(feed_coordinator, "async_get_clientsession", lambda _: client),
    ):
        await coordinator.async_refresh()
        offered = coordinator.data
        assert offered is not None and offered.tag == "v1.1.0"
        _fail_tags(client, ["v1.1.0"])
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.data is offered
    assert "could not be authenticated" in caplog.text


async def test_install_choices_without_feed_fail_when_every_download_fails(
    hass, release_key, monkeypatch
):
    client = authenticated_session(release_key, [("v1.1.0", (5, 5))])
    _fail_tags(client, ["v1.1.0"])
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_list_install_choices(hass)

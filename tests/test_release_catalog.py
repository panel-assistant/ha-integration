"""Discovery includes only complete, supported release choices."""

import json
from unittest.mock import AsyncMock

import pytest

from custom_components.panel_assistant import release_catalog as catalog
from custom_components.panel_assistant.release import ReleaseResolutionError

from .test_release import _FakeResponse, _FakeSession


def document(tag="v1.2.3", *, prerelease=False):
    apk = f"ha-paneld-{tag}-manual-setup-required.apk"
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
    return _FakeSession(
        {
            str(url): _FakeResponse(200, json.dumps(value).encode(), url)
            for url, value in [
                (catalog._LATEST_RELEASE_URL, latest),
                (catalog._RECENT_RELEASES_URL, recent),
            ]
        }
    )


async def test_recent_stable_and_exact_rc_candidates_are_discovered():
    rc = document("v1.3.0-rc2", prerelease=True)
    client = session(document(), [rc, document("v1.0.0"), rc])
    assert await catalog.async_list_install_releases(client) == [
        {"tag": "v1.2.3", "prerelease": False},
        {"tag": "v1.3.0-rc2", "prerelease": True},
        {"tag": "v1.0.0", "prerelease": False},
    ]
    assert len(client.requests) == 2
    for _, options in client.requests:
        assert options["allow_redirects"] is False
        assert "Authorization" not in options["headers"]
        assert options["timeout"].total == 10


async def test_total_choice_limit_includes_latest_stable():
    recent = [document(f"v1.3.0-rc{n}", prerelease=True) for n in range(30, 0, -1)]
    choices = await catalog.async_list_install_releases(session(document(), recent))
    assert len(choices) == 30
    assert choices[0] == {"tag": "v1.2.3", "prerelease": False}
    assert choices[-1] == {"tag": "v1.3.0-rc2", "prerelease": True}


@pytest.mark.parametrize("missing", range(5))
async def test_every_install_asset_is_required(missing):
    latest = document()
    latest["assets"].pop(missing)
    rc = document("v1.3.0-rc2", prerelease=True)
    assert await catalog.async_list_install_releases(session(latest, [rc])) == [
        {"tag": "v1.3.0-rc2", "prerelease": True}
    ]


async def test_latest_cannot_substitute_rc_and_bad_rows_are_ignored():
    rc = document("v1.3.0-rc2", prerelease=True)
    assert await catalog.async_list_install_releases(session(rc, [None, rc])) == [
        {"tag": "v1.3.0-rc2", "prerelease": True}
    ]


@pytest.mark.parametrize(
    "change", ["missing", "url", "duplicate", "draft", "flag", "tag"]
)
async def test_bad_choices_are_omitted(change):
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
    assert await catalog.async_list_install_releases(session(latest, [candidate])) == []


@pytest.mark.parametrize("recent", [{}, [None] * 31])
async def test_invalid_catalog_shape_or_count_fails(recent):
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_list_install_releases(session(document(), recent))


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
    client._responses[str(catalog._RECENT_RELEASES_URL)] = _FakeResponse(
        status, body, catalog._RECENT_RELEASES_URL
    )
    with pytest.raises(ReleaseResolutionError):
        await catalog.async_list_install_releases(client)


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
                responses[str(url)] = _FakeResponse(200, data, url)
        for name, data in [
            (apk + ".sha256", checksum),
            (apk + ".sha256.sig", _signature(signing_key, checksum)),
            (f"ha-paneld-{tag}-install.json", descriptor),
            (f"ha-paneld-{tag}-install.json.sig", _signature(signing_key, descriptor)),
        ]:
            url = URL(f"{root}/{name}")
            responses[str(url)] = _FakeResponse(200, data, url)
        url = URL(f"{catalog.ANDROID_RELEASES_API}/tags/{tag}")
        responses[str(url)] = _FakeResponse(200, json.dumps(doc).encode(), url)
        docs.append(doc)
    responses[str(catalog._LATEST_RELEASE_URL)] = _FakeResponse(
        200,
        json.dumps(next(d for d in docs if not d["prerelease"])).encode(),
        catalog._LATEST_RELEASE_URL,
    )
    responses[str(catalog._RECENT_RELEASES_URL)] = _FakeResponse(
        200, json.dumps(docs).encode(), catalog._RECENT_RELEASES_URL
    )
    return _FakeSession(responses)


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
            ("v1.4.0", (4, 4)),
            ("v1.3.0-rc2", (3, 3)),
            ("v1.2.0", (3, 3)),
        ],
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", pa_version)
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    result = await catalog.async_resolve_install_choice(hass, None)
    assert result.tag == expected
    assert not any(url.endswith(".apk") for url, _ in client.requests)


@pytest.mark.parametrize("protocol_range", [None, (4, 4)])
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
        release_key, [("v1.3.0", (3, 3)), ("v1.3.0-rc10", (3, 3))]
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
    assert choices[0]["tag"] == "build-772"
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
    client = authenticated_session(release_key, [("v1.2.0", (3, 3))])
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0")
    monkeypatch.setattr(catalog, "async_get_clientsession", lambda _: client)
    choices = await catalog.async_list_install_choices(hass)
    assert choices[0]["tag"] == "build-772"
    artifact = await catalog.async_resolve_install_choice(hass, None)
    assert artifact.tag == choices[0]["tag"]
    assert artifact.descriptor.version_code == 772

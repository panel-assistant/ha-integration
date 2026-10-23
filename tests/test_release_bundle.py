"""Installation authenticates and retains the exact published release proofs."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
from yarl import URL

from custom_components.panel_assistant import browser_release_cache as cache_module
from custom_components.panel_assistant import release, release_catalog, update_policy
from custom_components.panel_assistant.browser_release_cache import (
    BrowserReleaseCache,
    BrowserReleaseCacheError,
    BrowserReleaseCacheErrorCode,
)
from custom_components.panel_assistant.install_artifacts import InstallArtifact

from .http_fakes import FakeResponse
from .test_release import _canonical_descriptor, _install_test_key, _signature
from .test_release import signing_key as signing_key
from .test_release_catalog import authenticated_session


@pytest.fixture(params=[None, "v1.2.3", "v1.3.0-rc2"], ids=["default", "stable", "rc"])
def signed_choice(request, hass, monkeypatch, signing_key):
    _install_test_key(monkeypatch, signing_key)
    selected = request.param
    tag = selected or "v1.2.3"
    rows = [("v1.2.3", (5, 5))]
    if selected == "v1.3.0-rc2":
        rows.append((selected, (5, 5)))
    session = authenticated_session(signing_key, rows)
    monkeypatch.setattr(release_catalog, "async_get_clientsession", lambda _: session)
    monkeypatch.setattr(
        release_catalog, "_async_current_feed", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0")
    return session, selected, tag


async def test_install_choice_preserves_original_signed_bytes(hass, signed_choice):
    session, selected, tag = signed_choice
    bundle = await release_catalog.async_resolve_install_bundle_choice(hass, selected)
    assert isinstance(bundle, release.InstallReleaseBundle)
    assert bundle.artifact.tag == tag
    root = bundle.artifact.apk_url.rsplit("/", 1)[0]
    urls = [
        bundle.artifact.apk_url + ".sha256",
        bundle.artifact.apk_url + ".sha256.sig",
        f"{root}/ha-paneld-{tag}-install.json",
        f"{root}/ha-paneld-{tag}-install.json.sig",
    ]
    assert [
        bundle.metadata.checksum,
        bundle.metadata.checksum_signature,
        bundle.metadata.descriptor_bytes,
        bundle.metadata.descriptor_signature,
    ] == [session.responses[url].body for url in urls]
    assert bundle.artifact.apk_url not in [url for url, _ in session.requests]
    with pytest.raises(FrozenInstanceError):
        bundle.metadata.checksum = b"replacement"


@pytest.mark.parametrize("caller", ["network", "usb"])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "checksum_signature",
        "descriptor_signature",
        "descriptor_hash",
        "descriptor_tag",
        "protocol_hash",
        "protocol_range",
        "missing_descriptor",
    ],
)
async def test_install_choices_authenticate_before_download(
    hass, monkeypatch, signing_key, signed_choice, caller, fault
):
    session, selected, tag = signed_choice
    root = f"https://github.com/panel-assistant/android/releases/download/{tag}"
    descriptor_url = f"{root}/ha-paneld-{tag}-install.json"
    protocol_url = f"{root}/ha-paneld-{tag}-protocol.json"
    apk_url = f"{root}/panel-assistant-{tag}-manual-setup-required.apk"
    if fault is not None:
        if fault.endswith("signature"):
            url = (
                apk_url + ".sha256.sig"
                if fault == "checksum_signature"
                else descriptor_url + ".sig"
            )
            session.responses[url] = FakeResponse(
                200, _signature(signing_key, b"other bytes"), URL(url)
            )
        elif fault == "missing_descriptor":
            url = f"{release_catalog.ANDROID_RELEASES_API}/tags/{tag}"
            document = json.loads(session.responses[url].body)
            document["assets"] = [
                a for a in document["assets"] if "-install.json" not in a["name"]
            ]
            session.responses[url] = FakeResponse(
                200, json.dumps(document).encode(), URL(url)
            )
        else:
            url = descriptor_url if fault.startswith("descriptor") else protocol_url
            document = json.loads(session.responses[url].body)
            if fault == "descriptor_hash":
                document["apkSha256"] = "f" * 64
            elif fault == "descriptor_tag":
                document["releaseTag"] = "v9.9.9"
            elif fault == "protocol_hash":
                document["artifacts"][0]["apkSha256"] = "f" * 64
            else:
                document["artifacts"][0]["protocolMin"] = 6
                document["artifacts"][0]["protocolMax"] = 6
            body = _canonical_descriptor(document)
            session.responses[url] = FakeResponse(200, body, URL(url))
            session.responses[url + ".sig"] = FakeResponse(
                200, _signature(signing_key, body), URL(url + ".sig")
            )
    download = AsyncMock(
        return_value=InstallArtifact("owned", "/unused/owned", 12345, "0" * 64)
    )
    monkeypatch.setattr(cache_module, "async_download_browser_artifact", download)
    monkeypatch.setattr(cache_module, "async_reconcile_browser_artifacts", AsyncMock())
    monkeypatch.setattr(cache_module, "async_cleanup_browser_artifact", AsyncMock())
    cache = BrowserReleaseCache(hass, session)
    try:
        if fault is not None:
            if caller == "network":
                with pytest.raises(release.ReleaseResolutionError):
                    await release_catalog.async_resolve_install_bundle_choice(
                        hass, selected
                    )
            else:
                with pytest.raises(BrowserReleaseCacheError) as error:
                    await cache.async_prepare("admin", rc_tag=selected)
                assert error.value.code is BrowserReleaseCacheErrorCode.PREPARE_FAILED
            download.assert_not_awaited()
        elif caller == "network":
            bundle = await release_catalog.async_resolve_install_bundle_choice(
                hass, selected
            )
            assert bundle.artifact.tag == tag
            download.assert_not_awaited()
        else:
            record = await cache.async_prepare("admin", rc_tag=selected)
            assert record.bundle.artifact.tag == tag
            download.assert_awaited_once()
            assert download.await_args.args[2] == record.bundle.artifact
    finally:
        await cache.async_close()
    assert apk_url not in [url for url, _ in session.requests]


@pytest.mark.parametrize(
    "tag", ["", "../latest", "v1.2.3-rc0", "v1.2.3-rc03", "v1.2.3+other"]
)
async def test_invalid_install_choice_is_refused_before_http(hass, monkeypatch, tag):
    from .http_fakes import FakeSession

    session = FakeSession({})
    monkeypatch.setattr(release_catalog, "async_get_clientsession", lambda _: session)
    with pytest.raises(release.ReleaseResolutionError):
        await release_catalog.async_resolve_install_bundle_choice(hass, tag)
    assert session.requests == []


async def test_invalid_newest_release_does_not_hide_authenticated_older_default(
    hass, monkeypatch, signing_key
):
    _install_test_key(monkeypatch, signing_key)
    session = authenticated_session(
        signing_key, [("v1.4.0", (5, 5)), ("v1.2.3", (5, 5))]
    )
    url = "https://github.com/panel-assistant/android/releases/download/v1.4.0/panel-assistant-v1.4.0-manual-setup-required.apk.sha256.sig"
    session.responses[url] = FakeResponse(
        200, _signature(signing_key, b"other bytes"), URL(url)
    )
    monkeypatch.setattr(release_catalog, "async_get_clientsession", lambda _: session)
    monkeypatch.setattr(
        release_catalog, "_async_current_feed", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "1.0.0")
    assert (
        await release_catalog.async_resolve_install_choice(hass, None)
    ).tag == "v1.2.3"
    with pytest.raises(release.ReleaseResolutionError):
        await release_catalog.async_resolve_install_choice(hass, "v1.4.0")

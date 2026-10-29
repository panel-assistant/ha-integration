"""Feed builds travel both install paths through one list and one resolver."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from yarl import URL

from custom_components.panel_assistant import (
    install_artifacts,
    install_executor,
    install_jobs,
    release_catalog,
)
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    LEGACY_PACKAGE_ID,
    PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import (
    BuildFeed,
    FeedBuild,
    FeedInstallBundle,
    feed_release_artifact,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.feed_coordinator import (
    DATA_BUILD_FEED,
    BuildFeedCoordinator,
)
from custom_components.panel_assistant.install_artifacts import ArtifactCustodyError
from custom_components.panel_assistant.install_plan import (
    InstallPlanError,
    build_install_plan,
)
from custom_components.panel_assistant.release import (
    ReleaseResolutionError,
    artifact_identity_matches,
)
from tests.test_install_jobs import (
    Clock,
    emulate_home_assistant_store_file,
)
from tests.test_install_plan import CREDENTIAL_ID, pinned_target, probe

__all__ = ["emulate_home_assistant_store_file"]

FEED_URL = URL("https://builds.example/maintainer.json")
SHA = hashlib.sha256(b"772").hexdigest()


def _build(code: int = 772, sha: str = SHA, package_id: str = PACKAGE_ID) -> FeedBuild:
    return FeedBuild(
        version_code=code,
        version_name="0.9.7-rc4",
        apk_url=FEED_URL.join(URL(f"apks/{sha}.apk")),
        apk_sha256=sha,
        apk_size=14,
        commit="0" * 40,
        database_compatibility="hapaneld-db:v1:ha-paneld.db:11:14",
        min_sdk=26,
        published="2026-09-11T10:00:00Z",
        package_id=package_id,
        launch_component=LAUNCH_COMPONENTS[package_id][0],
        protocol_min=5,
        protocol_max=5,
    )


def _install_feed(hass: HomeAssistant, *builds: FeedBuild) -> BuildFeedCoordinator:
    coordinator = BuildFeedCoordinator(hass, FEED_URL)
    feed = BuildFeed("maintainer", tuple(builds), raw=b"feed\n", signature=b"s" * 256)
    coordinator.async_refresh = AsyncMock()  # type: ignore[method-assign]
    coordinator.data = feed
    coordinator.reachable = True
    hass.data.setdefault(DOMAIN, {})[DATA_BUILD_FEED] = coordinator
    return coordinator


# --- one identity rule ---------------------------------------------------------


def test_identity_rule_binds_each_kind_of_artifact() -> None:
    """A GitHub tag binds name and file; a feed tag binds code and content hash."""
    assert artifact_identity_matches(
        "v0.9.7-rc3",
        "0.9.7-rc3",
        707,
        "panel-assistant-v0.9.7-rc3-manual-setup-required.apk",
        SHA,
    )
    # A receipt an earlier release stored about the old app still reads back.
    assert artifact_identity_matches(
        "v0.9.7-rc3",
        "0.9.7-rc3",
        707,
        "ha-paneld-v0.9.7-rc3-manual-setup-required.apk",
        SHA,
        LEGACY_PACKAGE_ID,
    )
    assert artifact_identity_matches(
        "build-772-successor", "0.9.7-rc4", 772, f"{SHA}.apk", SHA
    )
    for tag, name, code, apk in (
        ("build-772-successor", "0.9.7-rc4", 771, f"{SHA}.apk"),  # not the tag's
        ("build-772-successor", "0.9.7-rc4", 772, "other.apk"),  # not addressed
        ("build-772-successor", "bad name!", 772, f"{SHA}.apk"),
        ("build-0772-successor", "0.9.7-rc4", 772, f"{SHA}.apk"),
        ("build-772", "0.9.7-rc4", 772, f"{SHA}.apk"),  # the old app's tag
        (
            "v0.9.7-rc3",
            "0.9.7-rc4",
            707,
            "panel-assistant-v0.9.7-rc3-manual-setup-required.apk",
        ),
        # The old app's name does not bind an artifact of the app.
        (
            "v0.9.7-rc3",
            "0.9.7-rc3",
            707,
            "ha-paneld-v0.9.7-rc3-manual-setup-required.apk",
        ),
        ("v0.9.7-rc3", "0.9.7-rc3", 707, f"{SHA}.apk"),
    ):
        assert not artifact_identity_matches(tag, name, code, apk, SHA), (tag, apk)


# --- one list, one resolver ------------------------------------------------------


async def test_both_paths_offer_only_the_newest_compatible_choice_per_channel(
    hass: HomeAssistant,
) -> None:
    _install_feed(hass, _build(772), _build(771, hashlib.sha256(b"771").hexdigest()))
    github = [{"tag": "v0.9.7-rc3", "prerelease": True}]
    with patch.object(
        release_catalog,
        "async_resolve_update_candidates",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    artifact=SimpleNamespace(
                        tag=choice["tag"],
                        version=choice["tag"][1:],
                        protocol_min=5,
                        protocol_max=5,
                        descriptor=SimpleNamespace(version_code=770),
                    )
                )
                for choice in github
            ]
        ),
    ):
        choices = await release_catalog.async_list_install_choices(hass)
    assert choices == [
        {
            "tag": "build-772-successor",
            "prerelease": True,
            "name": "0.9.7-rc4 build 772",
        },
    ]


async def test_catalog_resolves_only_the_apps_build_tag(
    hass: HomeAssistant,
) -> None:
    """A bare build tag names an old-app build, which is never resolved."""
    _install_feed(hass, _build(772))
    with patch.object(
        release_catalog, "async_resolve_update_candidates", AsyncMock(return_value=[])
    ):
        choices = await release_catalog.async_list_install_choices(hass)
        assert [choice["tag"] for choice in choices] == ["build-772-successor"]
        assert (
            await release_catalog.async_resolve_install_choice(hass, None)
        ).tag == choices[0]["tag"]
    artifact = await release_catalog.async_resolve_install_choice(
        hass, "build-772-successor"
    )
    assert artifact.descriptor.package_id == PACKAGE_ID
    assert artifact.sha256 == SHA
    with pytest.raises(ReleaseResolutionError):
        await release_catalog.async_resolve_install_choice(hass, "build-772")


async def test_github_outage_still_offers_feed_builds_but_not_nothing(
    hass: HomeAssistant,
) -> None:
    outage = AsyncMock(side_effect=ReleaseResolutionError)
    with patch.object(release_catalog, "async_resolve_update_candidates", outage):
        with pytest.raises(ReleaseResolutionError):
            await release_catalog.async_list_install_choices(hass)
        _install_feed(hass, _build())
        choices = await release_catalog.async_list_install_choices(hass)
    assert [choice["tag"] for choice in choices] == ["build-772-successor"]


async def test_without_a_feed_the_list_is_github_only(hass: HomeAssistant) -> None:
    github = [{"tag": "v0.9.6", "prerelease": False}]
    with patch.object(
        release_catalog,
        "async_resolve_update_candidates",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    artifact=SimpleNamespace(
                        tag=choice["tag"],
                        version=choice["tag"][1:],
                        protocol_min=5,
                        protocol_max=5,
                        descriptor=SimpleNamespace(version_code=770),
                    )
                )
                for choice in github
            ]
        ),
    ):
        assert await release_catalog.async_list_install_choices(hass) == github


async def test_one_resolver_names_the_exact_feed_build(hass: HomeAssistant) -> None:
    coordinator = _install_feed(hass, _build())
    artifact = await release_catalog.async_resolve_install_choice(
        hass, "build-772-successor"
    )
    assert artifact.tag == "build-772-successor"
    assert artifact.apk_name == f"{SHA}.apk"
    assert artifact.apk_url == str(FEED_URL.join(URL(f"apks/{SHA}.apk")))
    assert artifact.descriptor is not None
    assert artifact.descriptor.version_code == 772
    coordinator.async_refresh.assert_awaited()  # a fresh read, not a stale list
    with pytest.raises(ReleaseResolutionError):
        await release_catalog.async_resolve_install_choice(hass, "build-999-successor")
    with pytest.raises(ReleaseResolutionError):
        await release_catalog.async_resolve_install_choice(hass, "latest")


async def test_browser_bundle_carries_the_exact_signed_feed(
    hass: HomeAssistant,
) -> None:
    _install_feed(hass, _build())
    bundle = await release_catalog.async_resolve_install_bundle_choice(
        hass, "build-772-successor"
    )
    assert isinstance(bundle, FeedInstallBundle)
    assert bundle.feed == b"feed\n"
    assert bundle.feed_signature == b"s" * 256
    assert bundle.artifact.tag == "build-772-successor"


# --- the network path accepts it end to end ----------------------------------------


def test_install_plan_refuses_a_feed_build_when_another_was_chosen() -> None:
    release = feed_release_artifact(_build())
    for selected in (None, "build-772-successor"):
        plan = build_install_plan(
            pinned_target(), probe(), release, CREDENTIAL_ID, expected_rc_tag=selected
        )
        assert plan.artifact.release_tag == "build-772-successor"
        assert plan.artifact.version_code == 772
        assert plan.artifact.package_id == PACKAGE_ID
    for expected in ("build-771-successor", "build-772", "v0.9.7-rc3"):
        with pytest.raises(InstallPlanError):
            build_install_plan(
                pinned_target(),
                probe(),
                release,
                CREDENTIAL_ID,
                expected_rc_tag=expected,
            )


@pytest.mark.parametrize("tamper", [None, "release_tag", "version_code"])
async def test_a_feed_build_survives_the_durable_receipt(
    hass: HomeAssistant, tamper: str | None
) -> None:
    plan = build_install_plan(
        pinned_target(),
        probe(),
        feed_release_artifact(_build()),
        CREDENTIAL_ID,
        expected_rc_tag="build-772-successor",
    )
    receipt, created = await install_jobs.InstallJobManager(
        hass, now=Clock()
    ).async_create_or_join(
        plan.target, plan.artifact, plan.plan_sha256, plan.adb_credential_id
    )
    assert created
    if tamper is not None:
        store = Store(
            hass, 1, f"{DOMAIN}.install_jobs", private=True, atomic_writes=True
        )
        document = await store.async_load()
        document["jobs"][0]["artifact"][tamper] = (
            "build-772" if tamper == "release_tag" else 771
        )
        await store.async_save(document)
    restarted = install_jobs.InstallJobManager(hass, now=Clock())
    if tamper is not None:
        with pytest.raises(install_jobs.InstallJobStoreError):
            await restarted.async_get(receipt.job_id)
    else:
        restored = await restarted.async_get(receipt.job_id)
        assert restored == receipt
        assert restored.artifact == plan.artifact
        assert restored.artifact.release_tag == "build-772-successor"
        assert restored.artifact.apk_sha256 == SHA
        assert restored.artifact.package_id == PACKAGE_ID


def test_custody_fetches_only_content_addressed_apks_from_the_feed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_artifacts, "_FEED_DOWNLOAD_HOSTS", set())
    good = f"https://builds.example/apks/{SHA}.apk"
    with pytest.raises(ArtifactCustodyError):
        install_artifacts._trusted_download_url(good)  # host not registered yet
    install_artifacts.register_feed_download_host("builds.example")
    assert str(install_artifacts._trusted_download_url(good)) == good
    for refused in (
        "https://builds.example/maintainer.json",
        f"https://builds.example/apks/{SHA}.apk.exe",
        "https://builds.example/apks/notahash.apk",
        f"http://builds.example/apks/{SHA}.apk",
        f"https://other.example/apks/{SHA}.apk",
    ):
        with pytest.raises(ArtifactCustodyError):
            install_artifacts._trusted_download_url(refused)


def test_executor_rebuilds_a_feed_url_and_never_guesses_one() -> None:
    artifact = build_install_plan(
        pinned_target(),
        probe(),
        feed_release_artifact(_build()),
        CREDENTIAL_ID,
        expected_rc_tag="build-772-successor",
    ).artifact
    receipt = SimpleNamespace(artifact=artifact)
    assert install_executor._apk_url(receipt, FEED_URL) == (  # type: ignore[arg-type]
        f"https://builds.example/apks/{SHA}.apk"
    )
    assert install_executor._apk_url(receipt, None) == ""  # type: ignore[arg-type]

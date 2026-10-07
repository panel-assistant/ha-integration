"""Discover bounded install choices; selections still require verification."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .app_identity import SUCCESSOR_PACKAGE_ID
from .build_feed import BuildFeed, FeedBuild, FeedInstallBundle, feed_release_artifact
from .client import _version_key
from .const import ANDROID_RELEASES_API
from .feed_coordinator import async_get_feed_coordinator
from .release import (
    _API_HEADERS,
    _LATEST_RELEASE_URL,
    _MAX_RELEASE_RESPONSE_BYTES,
    InstallReleaseBundle,
    ReleaseArtifact,
    ReleaseResolutionError,
    _async_fetch_bounded,
    _async_resolve_release,
    _parse_release_metadata,
    feed_build_code,
    feed_build_package,
    feed_build_tag,
    is_rc_release_tag,
    release_descriptor_name,
    strict_json_hooks,
)
from .update_policy import build_allowed, prereleases_allowed

_RECENT_RELEASES_URL = URL(f"{ANDROID_RELEASES_API}?per_page=30")
_MAX_RECENT_RELEASES = 30
_MAX_CATALOG_BYTES = 1024 * 1024


def _decode(body: bytes) -> Any:
    try:
        return json.loads(
            body.decode("utf-8"),
            **strict_json_hooks(ReleaseResolutionError),
        )
    except (UnicodeError, ValueError, RecursionError) as err:
        raise ReleaseResolutionError from err


def _choice(document: Any, *, prerelease: bool) -> dict[str, str | bool] | None:
    if not isinstance(document, dict):
        return None
    tag = document.get("tag_name")
    if prerelease and not is_rc_release_tag(tag):
        return None
    try:
        tag, _, assets = _parse_release_metadata(
            json.dumps(document).encode(),
            expected_rc_tag=tag if prerelease else None,
        )
    except ReleaseResolutionError:
        return None
    descriptor = release_descriptor_name(tag)
    if descriptor not in assets or f"{descriptor}.sig" not in assets:
        return None
    return {"tag": tag, "prerelease": prerelease}


async def async_list_install_releases(
    session: ClientSession,
) -> list[dict[str, str | bool]]:
    """List latest stable and recent RCs with complete installation assets.

    Metadata discovery neither authenticates assets nor opts into testing.
    Invalid/incomplete releases are omitted; transport or catalogue failures
    raise ReleaseResolutionError, rather than reporting an empty catalogue.
    """
    try:
        async with asyncio.timeout(20):
            latest = _decode(
                await _async_fetch_bounded(
                    session,
                    _LATEST_RELEASE_URL,
                    _MAX_RELEASE_RESPONSE_BYTES,
                    allow_release_redirects=False,
                    headers=_API_HEADERS,
                )
            )
            recent = _decode(
                await _async_fetch_bounded(
                    session,
                    _RECENT_RELEASES_URL,
                    _MAX_CATALOG_BYTES,
                    allow_release_redirects=False,
                    headers=_API_HEADERS,
                )
            )
    except TimeoutError as err:
        raise ReleaseResolutionError from err
    if (
        not isinstance(latest, dict)
        or not isinstance(recent, list)
        or len(recent) > _MAX_RECENT_RELEASES
    ):
        raise ReleaseResolutionError
    choices = []
    stable = _choice(latest, prerelease=False)
    if stable is not None:
        choices.append(stable)
    seen = {choice["tag"] for choice in choices}
    for document in recent:
        choice = _choice(
            document,
            prerelease=isinstance(document, dict)
            and document.get("prerelease") is True,
        )
        if choice is not None and choice["tag"] not in seen:
            choices.append(choice)
            seen.add(choice["tag"])
            if len(choices) == _MAX_RECENT_RELEASES:
                break
    return choices


async def _async_release_choice(
    session: ClientSession, tag: str, *, include_bridge: bool = False
) -> tuple[InstallReleaseBundle, ReleaseArtifact | None]:
    """Authenticate an exact published tag and its hash-bound protocol range."""
    # Validate before constructing a URL; unknown strings are never URL paths.
    from .release import is_install_release_tag

    if not is_install_release_tag(tag):
        raise ReleaseResolutionError
    artifact, metadata, bridge = await _async_resolve_release(
        session,
        URL(f"{ANDROID_RELEASES_API}/tags/{tag}"),
        expected_rc_tag=tag if is_rc_release_tag(tag) else None,
        include_bridge=include_bridge,
    )
    if artifact.tag != tag or metadata is None:
        raise ReleaseResolutionError
    return InstallReleaseBundle(artifact=artifact, metadata=metadata), bridge


async def async_resolve_update_candidates(
    session: ClientSession,
) -> list[tuple[InstallReleaseBundle, ReleaseArtifact | None]]:
    """Authenticate the bounded recent catalogue, including stable promotions.

    A missing or incompatible head must not conceal a compatible older build.
    No APK is downloaded here. Failed individual releases are simply ineligible.
    """
    choices = await async_list_install_releases(session)

    async def resolve(
        tag: str,
    ) -> tuple[InstallReleaseBundle, ReleaseArtifact | None] | None:
        try:
            return await _async_release_choice(session, tag, include_bridge=True)
        except ReleaseResolutionError:
            return None

    async with asyncio.timeout(60):
        resolved = await asyncio.gather(*(resolve(str(c["tag"])) for c in choices))
    return sorted(
        (pair for pair in resolved if pair is not None),
        key=lambda pair: (
            _version_key(pair[0].artifact.version) or ((0, 0, 0), False, ())
        ),
        reverse=True,
    )


def _allowed(artifact: ReleaseArtifact, *, allow_prerelease: bool) -> bool:
    return build_allowed(
        artifact.version,
        artifact.protocol_min,
        artifact.protocol_max,
        allow_prerelease=allow_prerelease,
    )


async def _async_current_feed(hass: HomeAssistant) -> BuildFeed | None:
    """Read the configured build feed afresh, or None when there is none."""
    coordinator = async_get_feed_coordinator(hass)
    if coordinator is None:
        return None
    await coordinator.async_refresh()
    if not coordinator.last_update_success or coordinator.data is None:
        return None
    return coordinator.data


async def async_list_install_choices(
    hass: HomeAssistant,
) -> list[dict[str, str | bool]]:
    """The one version list every install path offers.

    Newest compatible versions come first on the running PA channel. Explicit
    prerelease choices follow on stable PA. Feed builds retain exact version-code
    identities; either source can remain available when the other is unavailable.
    """
    feed = await _async_current_feed(hass)
    builds: list[dict[str, str | bool]] = [
        {
            "tag": feed_build_tag(build.version_code, build.package_id),
            "prerelease": "-" in build.version_name,
            "name": (
                f"{build.label} (Panel Assistant)"
                if build.package_id == SUCCESSOR_PACKAGE_ID
                else build.label
            ),
        }
        for build in (feed.builds if feed is not None else ())
        if _allowed(feed_release_artifact(build), allow_prerelease=True)
    ]
    try:
        candidates = await async_resolve_update_candidates(
            async_get_clientsession(hass)
        )
        releases: list[dict[str, str | bool]] = [
            {
                "tag": bundle.artifact.tag,
                "prerelease": is_rc_release_tag(bundle.artifact.tag),
            }
            for bundle, _bridge in candidates
            if _allowed(bundle.artifact, allow_prerelease=True)
        ]
    except ReleaseResolutionError, TimeoutError:
        if not builds:
            raise ReleaseResolutionError from None
        releases = []
    versions = (
        {bundle.artifact.tag: bundle.artifact.version for bundle, _bridge in candidates}
        if releases
        else {}
    )
    version_codes = (
        {
            bundle.artifact.tag: bundle.artifact.descriptor.version_code
            for bundle, _bridge in candidates
            if bundle.artifact.descriptor is not None
        }
        if releases
        else {}
    )
    if feed is not None:
        versions.update(
            {
                feed_build_tag(b.version_code, b.package_id): b.version_name
                for b in feed.builds
            }
        )
        version_codes.update(
            {
                feed_build_tag(b.version_code, b.package_id): b.version_code
                for b in feed.builds
            }
        )
    choices = sorted(
        [*releases, *builds],
        key=lambda choice: (
            _version_key(versions[str(choice["tag"])]) or ((0, 0, 0), False, ()),
            version_codes.get(str(choice["tag"]), 0),
        ),
        reverse=True,
    )
    # Explicit prerelease choices are PA-managed per-panel opt-ins. The default
    # remains the newest compatible release on the running PA's own channel.
    allowed_pre = prereleases_allowed()
    return sorted(
        choices,
        key=lambda choice: bool(choice["prerelease"]) and not allowed_pre,
    )


async def async_resolve_feed_choice(
    hass: HomeAssistant, tag: str
) -> tuple[BuildFeed, FeedBuild]:
    """Find exactly the feed build a choice names, in a freshly read feed."""
    code = feed_build_code(tag)
    feed = await _async_current_feed(hass) if code is not None else None
    package_id = feed_build_package(tag)
    build = (
        feed.find(code, package_id) if feed is not None and code is not None else None
    )
    if (
        feed is None
        or build is None
        or not _allowed(feed_release_artifact(build), allow_prerelease=True)
    ):
        raise ReleaseResolutionError
    return feed, build


async def async_resolve_install_choice(
    hass: HomeAssistant, tag: str | None
) -> ReleaseArtifact:
    """Resolve the PA-channel default or one explicit compatible panel choice."""
    return (await async_resolve_install_bundle_choice(hass, tag)).artifact


async def async_resolve_install_bundle_choice(
    hass: HomeAssistant, tag: str | None
) -> InstallReleaseBundle | FeedInstallBundle:
    """Authenticate every selection before any browser or ADB installation."""
    if tag is not None and feed_build_code(tag) is not None:
        feed, build = await async_resolve_feed_choice(hass, tag)
        return FeedInstallBundle(
            artifact=feed_release_artifact(build),
            feed=feed.raw,
            feed_signature=feed.signature,
        )
    session = async_get_clientsession(hass)
    if tag is not None:
        bundle, _bridge = await _async_release_choice(session, tag)
        if not _allowed(bundle.artifact, allow_prerelease=True):
            raise ReleaseResolutionError
        return bundle
    current_feed = await _async_current_feed(hass)
    eligible_feed = [
        build
        for build in (current_feed.builds if current_feed is not None else ())
        if _allowed(
            feed_release_artifact(build), allow_prerelease=prereleases_allowed()
        )
    ]
    try:
        candidates = await async_resolve_update_candidates(session)
    except ReleaseResolutionError, TimeoutError:
        if not eligible_feed:
            raise ReleaseResolutionError from None
        candidates = []
    eligible: list[InstallReleaseBundle | FeedInstallBundle] = [
        bundle
        for bundle, _bridge in candidates
        if _allowed(bundle.artifact, allow_prerelease=prereleases_allowed())
    ]
    if current_feed is not None:
        eligible.extend(
            FeedInstallBundle(
                artifact=feed_release_artifact(build),
                feed=current_feed.raw,
                feed_signature=current_feed.signature,
            )
            for build in eligible_feed
        )
    if not eligible:
        raise ReleaseResolutionError
    return max(
        eligible,
        key=lambda bundle: (
            _version_key(bundle.artifact.version) or ((0, 0, 0), False, ()),
            bundle.artifact.descriptor.version_code
            if bundle.artifact.descriptor
            else 0,
        ),
    )

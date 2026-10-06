"""Poll the signed release sources every panel on this Home Assistant shares."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from yarl import URL

from .app_identity import ACCEPTED_PACKAGE_IDS, LEGACY_PACKAGE_ID
from .build_feed import (
    BuildFeed,
    BuildFeedError,
    FeedBuild,
    async_download_build,
    async_fetch_build_feed,
    feed_release_artifact,
)
from .client import _version_key
from .const import DOMAIN
from .release import (
    ReleaseArtifact,
    ReleaseResolutionError,
)
from .update_policy import build_allowed, prereleases_allowed

_LOGGER = logging.getLogger(__name__)
DATA_BUILD_FEED = "build_feed"
CONF_BUILD_FEED = "build_feed"
FEED_REFRESH = timedelta(minutes=15)
DATA_STABLE_RELEASE = "stable_release"
STABLE_REFRESH = timedelta(hours=6)


class BuildFeedCoordinator(DataUpdateCoordinator[BuildFeed]):
    """One authenticated feed shared by every panel on this Home Assistant."""

    def __init__(self, hass: HomeAssistant, feed_url: URL) -> None:
        """Remember exactly which feed this Home Assistant was pointed at."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN} build feed",
            update_interval=FEED_REFRESH,
        )
        self.feed_url = feed_url
        self._verified_apks: dict[str, bytes] = {}

    def verified_newest(
        self, package_id: str | None, *, allow_prerelease: bool | None = None
    ) -> FeedBuild | None:
        """Return only a newest build whose exact APK is already verified here."""
        feed: BuildFeed | None = self.data
        if feed is None:
            return None
        if allow_prerelease is None:
            allow_prerelease = prereleases_allowed()
        build = max(
            (
                candidate
                for candidate in feed.builds
                if candidate.package_id == (package_id or LEGACY_PACKAGE_ID)
                and build_allowed(
                    candidate.version_name,
                    candidate.protocol_min,
                    candidate.protocol_max,
                    allow_prerelease=allow_prerelease,
                )
            ),
            key=lambda candidate: (
                _version_key(candidate.version_name) or ((0, 0, 0), False, ()),
                candidate.version_code,
            ),
            default=None,
        )
        return (
            build
            if build is not None and self.verified_apk(build) is not None
            else None
        )

    def verified_apk(self, build: FeedBuild) -> bytes | None:
        """Keep the verified offer's bytes available for its install attempt."""
        if self.data is not None and build in self.data.builds:
            apk = self._verified_apks.get(build.apk_sha256)
            if apk is not None and len(apk) == build.apk_size:
                return apk
        return None

    async def async_verify_build(self, build: FeedBuild) -> bytes | None:
        """Verify a selected authenticated candidate before offering its APK."""
        if self.data is None or build not in self.data.builds:
            return None
        apk = self.verified_apk(build)
        if apk is not None:
            return apk
        try:
            apk = await async_download_build(
                async_get_clientsession(self.hass), feed_release_artifact(build)
            )
        except BuildFeedError:
            _LOGGER.warning(
                "Signed build %s for %s has no verifiable APK; not offering it",
                build.version_code,
                build.package_id,
            )
            return None
        if self.data is None or build not in self.data.builds:
            return None
        self._verified_apks[build.apk_sha256] = apk
        return apk

    async def _async_update_data(self) -> BuildFeed:
        try:
            session = async_get_clientsession(self.hass)
            feed = await async_fetch_build_feed(session, self.feed_url)
        except BuildFeedError as err:
            raise UpdateFailed("The build feed could not be authenticated") from err
        current_hashes = {build.apk_sha256 for build in feed.builds}
        cached = {
            sha256: apk
            for sha256, apk in self._verified_apks.items()
            if sha256 in current_hashes
        }
        for package_id in ACCEPTED_PACKAGE_IDS:
            for allow_prerelease in (False, True):
                build = max(
                    (
                        candidate
                        for candidate in feed.builds
                        if candidate.package_id == package_id
                        and build_allowed(
                            candidate.version_name,
                            candidate.protocol_min,
                            candidate.protocol_max,
                            allow_prerelease=allow_prerelease,
                        )
                    ),
                    key=lambda candidate: (
                        _version_key(candidate.version_name) or ((0, 0, 0), False, ()),
                        candidate.version_code,
                    ),
                    default=None,
                )
                if build is None:
                    continue
                apk = cached.get(build.apk_sha256)
                if apk is None or len(apk) != build.apk_size:
                    try:
                        apk = await async_download_build(
                            session, feed_release_artifact(build)
                        )
                    except BuildFeedError:
                        _LOGGER.warning(
                            "Signed build %s for %s has no verifiable APK; "
                            "not offering it",
                            build.version_code,
                            package_id,
                        )
                        continue
                    cached[build.apk_sha256] = apk
        self._verified_apks = cached
        return feed


def async_get_feed_coordinator(hass: HomeAssistant) -> BuildFeedCoordinator | None:
    """Return the feed coordinator, or None when no feed is configured."""
    coordinator = hass.data.get(DOMAIN, {}).get(DATA_BUILD_FEED)
    return coordinator if isinstance(coordinator, BuildFeedCoordinator) else None


class StableReleaseCoordinator(DataUpdateCoordinator[ReleaseArtifact | None]):
    """Compatible recent releases, authenticated here rather than on the panel.

    A panel that cannot reach GitHub never learns of a release itself, so Home
    Assistant finds it, and sends the exact signed APK over the LAN.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Share one bounded release catalogue across every configured panel."""
        super().__init__(
            hass,
            logger=_LOGGER,
            # Shared by every panel, so it belongs to none: bound to the entry
            # that happened to create it, it would stop when that entry unloads.
            config_entry=None,
            name=f"{DOMAIN} stable release",
            update_interval=STABLE_REFRESH,
        )
        self._candidates: dict[tuple[str, str], ReleaseArtifact] = {}

    def candidates_for(self, package_id: str) -> tuple[ReleaseArtifact, ...]:
        """Return every authenticated candidate for installed-state selection."""
        return tuple(
            artifact
            for (_, candidate_package), artifact in self._candidates.items()
            if candidate_package == package_id
        )

    def artifact_for(
        self,
        package_id: str,
        *,
        allow_prerelease: bool | None = None,
        tag: str | None = None,
    ) -> ReleaseArtifact | None:
        """Return the newest authenticated compatible APK for this policy."""
        if allow_prerelease is None:
            allow_prerelease = prereleases_allowed()
        if tag is not None:
            candidate = self._candidates.get((tag, package_id))
            return (
                candidate
                if candidate is not None
                and build_allowed(
                    candidate.version,
                    candidate.protocol_min,
                    candidate.protocol_max,
                    allow_prerelease=allow_prerelease,
                )
                else None
            )
        return max(
            (
                candidate
                for candidate in self.candidates_for(package_id)
                if build_allowed(
                    candidate.version,
                    candidate.protocol_min,
                    candidate.protocol_max,
                    allow_prerelease=allow_prerelease,
                )
            ),
            key=lambda candidate: (
                _version_key(candidate.version) or ((0, 0, 0), False, ()),
                candidate.descriptor.version_code
                if candidate.descriptor is not None
                else 0,
            ),
            default=None,
        )

    async def _async_update_data(self) -> ReleaseArtifact | None:
        # The catalogue also uses this module to access a configured feed.
        from .release_catalog import async_resolve_update_candidates

        try:
            candidates = await async_resolve_update_candidates(
                async_get_clientsession(self.hass)
            )
        except ReleaseResolutionError as err:
            _LOGGER.warning(
                "The recent releases could not be authenticated", exc_info=True
            )
            raise UpdateFailed(
                "The recent releases could not be authenticated"
            ) from err
        authenticated: dict[tuple[str, str], ReleaseArtifact] = {}
        for bundle, bridge in candidates:
            for artifact in (bundle.artifact, bridge):
                if artifact is None:
                    continue
                package_id = (
                    artifact.descriptor.package_id
                    if artifact.descriptor is not None
                    else LEGACY_PACKAGE_ID
                )
                authenticated[artifact.tag, package_id] = artifact
        self._candidates = authenticated
        return self.artifact_for(ACCEPTED_PACKAGE_IDS[-1]) or self.artifact_for(
            LEGACY_PACKAGE_ID
        )


def async_get_stable_release_coordinator(
    hass: HomeAssistant,
) -> StableReleaseCoordinator:
    """Return the one stable-release coordinator, reading it once when created."""
    data = hass.data.setdefault(DOMAIN, {})
    coordinator = data.get(DATA_STABLE_RELEASE)
    if not isinstance(coordinator, StableReleaseCoordinator):
        coordinator = data[DATA_STABLE_RELEASE] = StableReleaseCoordinator(hass)
        hass.async_create_background_task(
            coordinator.async_refresh(), f"{DOMAIN} first stable release read"
        )
    return coordinator

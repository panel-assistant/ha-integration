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
from .client import is_version_at_least
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
        self._verified_newest: dict[tuple[str, bool], tuple[FeedBuild, bytes]] = {}

    def verified_newest(
        self, package_id: str | None, *, allow_prerelease: bool | None = None
    ) -> FeedBuild | None:
        """Return only a newest build whose exact APK is already verified here."""
        verified = self._verified_newest.get(
            (
                package_id or LEGACY_PACKAGE_ID,
                prereleases_allowed() if allow_prerelease is None else allow_prerelease,
            )
        )
        return verified[0] if verified is not None else None

    def verified_apk(self, build: FeedBuild) -> bytes | None:
        """Keep the verified offer's bytes available for its install attempt."""
        return next(
            (
                apk
                for selected, apk in self._verified_newest.values()
                if selected == build
            ),
            None,
        )

    async def _async_update_data(self) -> BuildFeed:
        try:
            session = async_get_clientsession(self.hass)
            feed = await async_fetch_build_feed(session, self.feed_url)
        except BuildFeedError as err:
            raise UpdateFailed("The build feed could not be authenticated") from err
        verified: dict[tuple[str, bool], tuple[FeedBuild, bytes]] = {}
        cached = {
            build.apk_sha256: apk for build, apk in self._verified_newest.values()
        }
        for package_id in ACCEPTED_PACKAGE_IDS:
            for allow_prerelease in (False, True):
                build = next(
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
                    None,
                )
                if build is None:
                    continue
                apk = cached.get(build.apk_sha256)
                if apk is None:
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
                verified[package_id, allow_prerelease] = (build, apk)
        self._verified_newest = verified
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
        self._artifacts: dict[tuple[str, bool], ReleaseArtifact] = {}
        self._candidates: dict[tuple[str, str], ReleaseArtifact] = {}

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
        return self._artifacts.get(
            (
                package_id,
                prereleases_allowed() if allow_prerelease is None else allow_prerelease,
            )
        )

    async def _async_update_data(self) -> ReleaseArtifact | None:
        # The catalogue also uses this module to access a configured feed.
        from .release_catalog import async_resolve_update_candidates

        try:
            candidates = await async_resolve_update_candidates(
                async_get_clientsession(self.hass)
            )
        except ReleaseResolutionError as err:
            raise UpdateFailed(
                "The recent releases could not be authenticated"
            ) from err
        selected: dict[tuple[str, bool], ReleaseArtifact] = {}
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
                for allow_prerelease in (False, True):
                    if not build_allowed(
                        artifact.version,
                        artifact.protocol_min,
                        artifact.protocol_max,
                        allow_prerelease=allow_prerelease,
                    ):
                        continue
                    previous = selected.get((package_id, allow_prerelease))
                    if previous is None or (
                        artifact.version != previous.version
                        and is_version_at_least(artifact.version, previous.version)
                        is True
                    ):
                        selected[package_id, allow_prerelease] = artifact
        self._artifacts = selected
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

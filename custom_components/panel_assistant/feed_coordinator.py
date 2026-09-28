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
from .const import DOMAIN
from .release import (
    ReleaseArtifact,
    ReleaseResolutionError,
    async_resolve_stable_bundle_and_bridge,
)

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
        self._verified_newest: dict[str, tuple[FeedBuild, bytes]] = {}

    def verified_newest(self, package_id: str | None) -> FeedBuild | None:
        """Return only a newest build whose exact APK is already verified here."""
        verified = self._verified_newest.get(package_id or LEGACY_PACKAGE_ID)
        return verified[0] if verified is not None else None

    def verified_apk(self, build: FeedBuild) -> bytes | None:
        """Keep the verified offer's bytes available for its install attempt."""
        verified = self._verified_newest.get(build.package_id)
        return verified[1] if verified is not None and verified[0] == build else None

    async def _async_update_data(self) -> BuildFeed:
        try:
            session = async_get_clientsession(self.hass)
            feed = await async_fetch_build_feed(session, self.feed_url)
        except BuildFeedError as err:
            raise UpdateFailed("The build feed could not be authenticated") from err
        verified: dict[str, tuple[FeedBuild, bytes]] = {}
        for package_id in ACCEPTED_PACKAGE_IDS:
            build = feed.newest(package_id)
            if build is None:
                continue
            previous = self._verified_newest.get(package_id)
            if previous is not None and previous[0] == build:
                verified[package_id] = previous
                continue
            try:
                apk = await async_download_build(session, feed_release_artifact(build))
            except BuildFeedError:
                _LOGGER.warning(
                    "Signed build %s for %s has no verifiable APK; not offering it",
                    build.version_code,
                    package_id,
                )
                continue
            verified[package_id] = (build, apk)
        self._verified_newest = verified
        return feed


def async_get_feed_coordinator(hass: HomeAssistant) -> BuildFeedCoordinator | None:
    """Return the feed coordinator, or None when no feed is configured."""
    coordinator = hass.data.get(DOMAIN, {}).get(DATA_BUILD_FEED)
    return coordinator if isinstance(coordinator, BuildFeedCoordinator) else None


class StableReleaseCoordinator(DataUpdateCoordinator[ReleaseArtifact]):
    """The latest stable release, authenticated here rather than on the panel.

    A panel that cannot reach GitHub never learns of a release itself, so Home
    Assistant finds it, and sends the exact signed APK over the LAN.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Read GitHub only as often as a stable release could plausibly appear."""
        super().__init__(
            hass,
            logger=_LOGGER,
            # Shared by every panel, so it belongs to none: bound to the entry
            # that happened to create it, it would stop when that entry unloads.
            config_entry=None,
            name=f"{DOMAIN} stable release",
            update_interval=STABLE_REFRESH,
        )
        self._bridge: ReleaseArtifact | None = None

    def artifact_for(self, package_id: str) -> ReleaseArtifact | None:
        """Return the authenticated APK for the package this panel runs."""
        artifact: ReleaseArtifact | None = self.data
        if artifact is None:
            return None
        if (
            artifact.descriptor is not None
            and package_id == artifact.descriptor.package_id
        ):
            return artifact
        if package_id == LEGACY_PACKAGE_ID:
            bridge = self._bridge
            return bridge if bridge is not None and bridge.tag == artifact.tag else None
        return None

    async def _async_update_data(self) -> ReleaseArtifact:
        try:
            bundle, bridge = await async_resolve_stable_bundle_and_bridge(
                async_get_clientsession(self.hass)
            )
        except ReleaseResolutionError as err:
            raise UpdateFailed(
                "The latest stable release could not be authenticated"
            ) from err
        self._bridge = bridge
        return bundle.artifact


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

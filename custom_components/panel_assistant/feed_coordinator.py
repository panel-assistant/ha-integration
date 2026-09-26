"""Poll the signed release sources every panel on this Home Assistant shares."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from yarl import URL

from .build_feed import BuildFeed, BuildFeedError, async_fetch_build_feed
from .const import DOMAIN
from .release import (
    ReleaseArtifact,
    ReleaseResolutionError,
    async_resolve_install_bundle,
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

    async def _async_update_data(self) -> BuildFeed:
        try:
            return await async_fetch_build_feed(
                async_get_clientsession(self.hass), self.feed_url
            )
        except BuildFeedError as err:
            raise UpdateFailed("The build feed could not be authenticated") from err


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

    async def _async_update_data(self) -> ReleaseArtifact:
        try:
            bundle = await async_resolve_install_bundle(
                async_get_clientsession(self.hass)
            )
        except ReleaseResolutionError as err:
            raise UpdateFailed(
                "The latest stable release could not be authenticated"
            ) from err
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

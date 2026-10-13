"""Which build a panel is offered, and whether it may still be installed.

The newest eligible build across the signed feed and the releases Home
Assistant authenticated, checked against the panel's update policy and its
running build. Every update route installs only what this admits, and admits it
again right before each command that changes the panel.
"""

from __future__ import annotations

from collections.abc import Callable

from .app_identity import LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, reports_package
from .build_feed import BuildFeed, FeedBuild, build_label, feed_release_artifact
from .client import (
    HaPaneldError,
    NotABridgeError,
    _version_key,
    is_newer_stable_version,
)
from .coordinator import HaPaneldDataUpdateCoordinator, PanelSnapshot
from .feed_coordinator import (
    BuildFeedCoordinator,
    StableReleaseCoordinator,
    newest_feed_build,
)
from .release import ReleaseArtifact, is_feed_build_tag
from .status import PanelCachedUpdate
from .update_errors import _update_error
from .update_policy import build_allowed, feed_build_newer, version_allowed

# The first ha-paneld release that can be backed up and sent an app over the LAN.
# Older panels cannot use this route to receive an authenticated replacement.
_FIRST_LAN_UPDATE_VERSION = "0.8.6"


class UpdateOffer:
    """The build offered to one panel, from the authenticated sources."""

    def __init__(
        self,
        coordinator: HaPaneldDataUpdateCoordinator,
        feed: BuildFeedCoordinator | None,
        release: StableReleaseCoordinator | None,
        installed_code: Callable[[], int | None],
        allow_prerelease: Callable[[], bool],
    ) -> None:
        self._coordinator = coordinator
        # A signed build feed, when configured. Internal builds
        # share one version name, so they are told apart by build number.
        self.feed = feed
        # The compatible release as Home Assistant itself authenticated it,
        # so a panel without internet access is offered and sent it too.
        self.release = release
        # The running build number as the entity last read it.
        self._installed_code = installed_code
        self._allow_prerelease = allow_prerelease

    def allowed(self, artifact: ReleaseArtifact) -> bool:
        """Recheck policy and prevent a post-1.0 downgrade before delivery."""
        if not build_allowed(
            artifact.version,
            artifact.protocol_min,
            artifact.protocol_max,
            allow_prerelease=self._allow_prerelease(),
        ):
            return False
        snapshot: PanelSnapshot | None = self._coordinator.data
        if snapshot is None:
            return False
        return version_allowed(
            artifact.version,
            artifact.descriptor.version_code
            if artifact.descriptor is not None
            else None,
            snapshot.health.version,
            snapshot.health.version_code
            if snapshot.health.version_code is not None
            else self._installed_code(),
            feed=is_feed_build_tag(artifact.tag),
        )

    async def async_admit(self, artifact: ReleaseArtifact) -> None:
        """Check current consent for this exact candidate before a fresh command."""
        if not self.allowed(artifact):
            raise _update_error(
                "update_unavailable", "The requested update is unavailable"
            )

    def host_release(self) -> ReleaseArtifact | None:
        """Choose the newest eligible artifact across the authenticated sources."""
        snapshot: PanelSnapshot | None = self._coordinator.data
        if snapshot is None or is_newer_stable_version(
            _FIRST_LAN_UPDATE_VERSION, snapshot.health.version
        ):
            return None
        package_id = snapshot.health.package or LEGACY_PACKAGE_ID
        installed_key = _version_key(snapshot.health.version)
        if installed_key is None:
            return None
        installed_code = snapshot.health.version_code or self._installed_code()
        eligible = []
        if self.feed is not None and self.feed.last_update_success and self.feed.data:
            # The feed's own order: versionCode first, whatever the name.
            feed_offer = newest_feed_build(
                build
                for build in self.feed.data.builds
                if build.package_id == package_id
                and feed_build_newer(
                    build.version_name,
                    build.version_code,
                    snapshot.health.version,
                    installed_code,
                )
                and self.allowed(feed_release_artifact(build))
            )
            if feed_offer is not None:
                eligible.append(feed_release_artifact(feed_offer))
        candidates = (
            self.release.candidates_for(package_id) if self.release is not None else ()
        )
        for artifact in candidates:
            if not self.allowed(artifact):
                continue
            code = artifact.descriptor.version_code if artifact.descriptor else None
            candidate_key = _version_key(artifact.version)
            if candidate_key is None:
                continue
            if (
                candidate_key > installed_key
                or (
                    artifact.version == snapshot.health.version
                    and code is not None
                    and installed_code is not None
                    and code > installed_code
                )
                # An installed bridge still owes its one-way handover.
                or (
                    artifact.descriptor is None
                    and artifact.version == snapshot.health.version
                )
            ):
                eligible.append(artifact)
        return max(
            eligible,
            key=lambda artifact: (
                _version_key(artifact.version),
                artifact.descriptor.version_code if artifact.descriptor else 0,
            ),
            default=None,
        )

    def label(self, artifact: ReleaseArtifact) -> str:
        """Keep same-version rebuilds distinguishable in Home Assistant."""
        if artifact.descriptor is not None and (
            is_feed_build_tag(artifact.tag)
            or artifact.version == self._coordinator.data.health.version
        ):
            return build_label(artifact.version, artifact.descriptor.version_code)
        return artifact.version

    def bridge_handover_due(self) -> bool:
        snapshot: PanelSnapshot | None = self._coordinator.data
        release = self.host_release()
        return bool(
            snapshot is not None
            and self.feed_mode() is None
            and release is not None
            and release.descriptor is None
            and snapshot.health.version == release.version
            and snapshot.health.installation_identity
            and reports_package(snapshot.health.package, LEGACY_PACKAGE_ID)
        )

    async def async_check_successor(
        self, artifact: ReleaseArtifact, snapshot: PanelSnapshot
    ) -> int | None:
        """Use the panel's handover answer for both offer and install admission."""
        descriptor = artifact.descriptor
        if descriptor is None or descriptor.package_id != SUCCESSOR_PACKAGE_ID:
            raise _update_error(
                "release_pair_unavailable",
                "The matching new-app release is unavailable",
            )
        if (
            not snapshot.health.installation_identity
            or snapshot.health.version != artifact.version
        ):
            raise _update_error(
                "bridge_details_changed", "The panel's handover details changed"
            )
        try:
            capability = await self._coordinator.client.async_get_successor_capability()
        except NotABridgeError as err:
            raise _update_error(
                "bridge_not_ready",
                "The panel cannot move to the new app yet; "
                "it is offered again once it can",
            ) from err
        except HaPaneldError as err:
            raise _update_error(
                "bridge_capability_unavailable",
                "The panel's handover capability could not be checked",
            ) from err
        if capability[3]:
            raise _update_error(
                "successor_untrusted",
                "The installed new app is not trusted for handover",
            )
        if capability[:2] != (descriptor.package_id, artifact.version):
            raise _update_error(
                "bridge_details_changed", "The panel's handover details changed"
            )
        return capability[2]

    def stable_target(self) -> PanelCachedUpdate | None:
        """Project only the host's authenticated compatible release."""
        release = self.host_release()
        if release is None:
            return None
        return PanelCachedUpdate(
            self._coordinator.data.health.version,
            self.label(release),
            release.tag,
        )

    def feed_mode(self) -> BuildFeed | None:
        """Use feed delivery only when the chosen artifact comes from the feed."""
        artifact = self.host_release()
        return (
            self.feed.data
            if self.feed is not None
            and artifact is not None
            and is_feed_build_tag(artifact.tag)
            else None
        )

    def selected_feed_build(self, artifact: ReleaseArtifact | None) -> FeedBuild | None:
        """Find the exact signed feed record chosen for this panel."""
        if (
            artifact is None
            or not is_feed_build_tag(artifact.tag)
            or self.feed is None
            or self.feed.data is None
        ):
            return None
        return next(
            (
                build
                for build in self.feed.data.builds
                if feed_release_artifact(build) == artifact
            ),
            None,
        )

    def feed_package(self) -> str | None:
        """Choose the package the panel actually runs, including old reports."""
        snapshot: PanelSnapshot | None = self._coordinator.data
        if snapshot is None:
            return None
        return snapshot.health.package or LEGACY_PACKAGE_ID

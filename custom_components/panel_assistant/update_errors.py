"""What a failed or refused panel update tells its owner.

Every update stage raises through these, so one failure reads the same whichever
route carried the update; the update entity records them as Repairs.
"""

from __future__ import annotations

import logging

from aiohttp.web import HTTPBadRequest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .client import UpdateRejectedError
from .const import DOMAIN
from .release import ReleaseArtifact, is_feed_build_tag
from .repairs_failure import async_request_adb_authorization

# Every update stage logs as the update entity, so one logger setting covers the
# whole update path.
UPDATE_LOGGER = logging.getLogger(f"{__package__}.update")


class _UpdateRefusalError(ServiceValidationError, HTTPBadRequest):
    """Report an expected refusal cleanly through both HA service transports."""

    def __init__(self, panel: str, reason: str | None = None) -> None:
        if reason:
            message = f"{panel} refused the update: {reason}"
            key, placeholders = (
                "update_refused_by_panel",
                {"panel": panel, "reason": reason},
            )
        else:
            message = (
                f"{panel} refused the update request. "
                "Check its Install tab, then try again."
            )
            key, placeholders = "update_rejected", {"panel": panel}
        HTTPBadRequest.__init__(self, text=message)
        self._message = message
        self.translation_domain = DOMAIN
        self.translation_key = key
        self.translation_placeholders = placeholders


def refusal_error(panel: str, refusal: UpdateRejectedError) -> _UpdateRefusalError:
    """The panel's own reason, when its install result gave one."""
    reason = str(refusal)
    if reason.startswith("refused ("):
        reason = reason.removeprefix("refused (").removesuffix(")")
    return _UpdateRefusalError(panel, reason or None)


def _update_error(translation_key: str, fallback: str) -> HomeAssistantError:
    """Return a localized update-action error with a useful log fallback."""
    return HomeAssistantError(
        fallback,
        translation_domain=DOMAIN,
        translation_key=translation_key,
    )


def _verification_error(artifact: ReleaseArtifact) -> HomeAssistantError:
    """Name what failed to verify: a feed build, or a GitHub release."""
    if is_feed_build_tag(artifact.tag):
        return _update_error(
            "build_verification_failed",
            "The build did not match the signed build feed",
        )
    return _update_error(
        "release_verification_failed", "The release did not match its signature"
    )


def adb_authorization_error(
    hass: HomeAssistant, entry_id: str, panel: str
) -> HomeAssistantError:
    """Raise the authorization Repair and say what the owner must approve."""
    async_request_adb_authorization(hass, entry_id)
    return HomeAssistantError(
        f"ADB authorization is missing for {panel}. "
        "Open its Home Assistant Repair to authorize updates, then approve "
        "Allow debugging on that panel when asked.",
        translation_domain=DOMAIN,
        translation_key="adb_authorization_required",
        translation_placeholders={"panel": panel},
    )


def route_unavailable_reason(error: Exception | None, *, has_adb_artifact: bool) -> str:
    """Why a panel without an update route cannot be updated now."""
    return (
        str(error)
        if isinstance(error, HomeAssistantError)
        else "Panel Assistant has no verified signed build for this ADB update."
        if not has_adb_artifact
        else "The panel cannot install this update itself, and Panel "
        "Assistant has no usable authorized ADB route."
    )

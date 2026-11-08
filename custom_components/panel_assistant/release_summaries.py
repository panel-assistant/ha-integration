"""Short panel app release summaries for Home Assistant's update dialog.

The project website publishes each app release's short summary beside its release
list. They are display text only: nothing here chooses, trusts or installs a build.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .const import DOMAIN
from .release import ReleaseResolutionError, _async_fetch_bounded

_LOGGER = logging.getLogger(__name__)

SUMMARIES_URL = URL("https://panel-assistant.io/releases/summaries-v1.json")
_MAX_BYTES = 256 * 1024
_MAX_SUMMARY = 2048
_REFRESH_SECONDS = 3600
_RETRY_SECONDS = 300
_CACHE = f"{DOMAIN}_release_summaries"


def full_notes_url(version: str) -> str:
    """The website's full notes for one panel app version."""
    return str(
        URL("https://panel-assistant.io/go/release-notes").with_query(
            v=version, component="panel-app"
        )
    )


def parse_summaries(body: bytes) -> dict[str, str]:
    """Keep only well-formed, bounded tag-to-summary pairs."""
    try:
        document = json.loads(body.decode("utf-8"))
    except UnicodeError, ValueError, RecursionError:
        return {}
    if not isinstance(document, dict) or document.get("schema") != 1:
        return {}
    summaries = document.get("summaries")
    if not isinstance(summaries, dict):
        return {}
    return {
        tag: text
        for tag, text in summaries.items()
        if isinstance(tag, str)
        and isinstance(text, str)
        and text.strip()
        and len(text.encode()) <= _MAX_SUMMARY
    }


async def async_release_summary(hass: HomeAssistant, tag: str) -> str | None:
    """The summary for one app release tag, read at most once an hour for all panels."""
    cache: dict[str, Any] = hass.data.setdefault(_CACHE, {"at": None, "summaries": {}})
    now = time.monotonic()
    if cache["at"] is None or now >= cache["at"]:
        try:
            body = await _async_fetch_bounded(
                async_get_clientsession(hass),
                SUMMARIES_URL,
                _MAX_BYTES,
                allow_release_redirects=False,
                allow_catalog_redirects=True,
                headers={"Accept": "application/json"},
            )
            cache["summaries"] = parse_summaries(body)
            cache["at"] = now + _REFRESH_SECONDS
        except (ReleaseResolutionError, TimeoutError) as err:
            _LOGGER.debug("Release summaries unavailable: %s", err)
            cache["at"] = now + _RETRY_SECONDS
    summaries: dict[str, str] = cache["summaries"]
    return summaries.get(tag)


async def async_release_notes(hass: HomeAssistant, tag: str, version: str) -> str:
    """The summary when there is one, and always the link to the full notes."""
    summary = await async_release_summary(hass, tag)
    if summary is not None:
        if "panel-assistant.io/go/release-notes" in summary:
            return summary
        return f"{summary}\n\n[Full release notes]({full_notes_url(version)})"
    return f"[Full release notes]({full_notes_url(version)})"

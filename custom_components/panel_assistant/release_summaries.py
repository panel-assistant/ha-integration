"""Short panel app release summaries for Home Assistant's update dialog.

The project website publishes each app release's short summary beside its release
list. They are display text only: nothing here chooses, trusts or installs a build.
The last good copy is kept in storage, so the dialog still shows what changed when
the website cannot be reached.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from yarl import URL

from .client import _version_key
from .const import DOMAIN
from .release import ReleaseResolutionError, _async_fetch_bounded

_LOGGER = logging.getLogger(__name__)

SUMMARIES_URL = URL("https://panel-assistant.io/releases/summaries-v1.json")
_MAX_BYTES = 256 * 1024
_MAX_SUMMARY = 2048
# A panel that skipped releases sees this many of them; the link covers the rest.
_MAX_RELEASES = 3
_REFRESH_SECONDS = 3600
_RETRY_SECONDS = 300
_CACHE = f"{DOMAIN}_release_summaries"
_STORE_KEY = f"{DOMAIN}.release_summaries"


def full_notes_url(version: str) -> str:
    """The website's full notes for one panel app version."""
    return str(
        URL("https://panel-assistant.io/go/release-notes").with_query(
            v=version, component="panel-app"
        )
    )


def _valid_summaries(document: Any) -> dict[str, str] | None:
    """Bounded tag-to-summary pairs, or None for a document of another shape."""
    if not isinstance(document, dict) or document.get("schema") != 1:
        return None
    summaries = document.get("summaries")
    if not isinstance(summaries, dict):
        return None
    return {
        tag: text
        for tag, text in summaries.items()
        if isinstance(tag, str)
        and isinstance(text, str)
        and text.strip()
        and len(text.encode()) <= _MAX_SUMMARY
    }


def parse_summaries(body: bytes) -> dict[str, str] | None:
    """Keep only well-formed, bounded tag-to-summary pairs."""
    try:
        return _valid_summaries(json.loads(body.decode("utf-8")))
    except UnicodeError, ValueError, RecursionError:
        return None


async def _async_summaries(hass: HomeAssistant) -> dict[str, str]:
    """Every published summary, read at most once an hour for all panels."""
    cache: dict[str, Any] = hass.data.get(_CACHE) or hass.data.setdefault(
        _CACHE,
        {"at": None, "summaries": None, "store": Store(hass, 1, _STORE_KEY)},
    )
    if cache["summaries"] is None:
        # First use since Home Assistant started: begin from the last good read.
        cache["summaries"] = _valid_summaries(await cache["store"].async_load()) or {}
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
        except (ReleaseResolutionError, TimeoutError) as err:
            _LOGGER.debug("Release summaries unavailable: %s", err)
            cache["at"] = now + _RETRY_SECONDS
        else:
            cache["at"] = now + _REFRESH_SECONDS
            summaries = parse_summaries(body)
            if summaries is None:
                _LOGGER.debug("Release summaries unreadable; keeping the last copy")
            elif summaries != cache["summaries"]:
                cache["summaries"] = summaries
                await cache["store"].async_save({"schema": 1, "summaries": summaries})
    current: dict[str, str] = cache["summaries"]
    return current


def _releases_between(
    summaries: dict[str, str], installed: str | None, offered: str
) -> list[tuple[str, str]]:
    """The offered release and the stable ones a panel skipped, newest first."""
    newest = _version_key(offered)
    oldest = _version_key(installed) if installed is not None else None
    picked = []
    for tag, text in summaries.items():
        version = tag.removeprefix("v")
        key = _version_key(version)
        if version == offered or (
            key is not None
            and newest is not None
            and oldest is not None
            and oldest < key < newest
            # A stable release's summary already covers its own candidates.
            and key[1]
        ):
            picked.append((key or newest, version, text))
    picked.sort(key=lambda item: item[0] or (), reverse=True)
    return [(version, text) for _, version, text in picked[:_MAX_RELEASES]]


def _latest_release_before(summaries: dict[str, str], offered: str) -> str | None:
    """The newest published version older than a test build, release or candidate."""
    newest = _version_key(offered)
    if newest is None:
        return None
    earlier = [
        (key, version)
        for version in (tag.removeprefix("v") for tag in summaries)
        if (key := _version_key(version)) is not None and key < newest
    ]
    return max(earlier)[1] if earlier else None


def _test_build_note(offered: str, build: int) -> str:
    """Say what a feed build is when its version has no published notes yet."""
    release = offered.partition("-")[0] if _version_key(offered) else None
    if release is None:
        return (
            f"This is a test build of the panel app (build {build}), made before "
            "its version is released. Its changes will be described in the "
            "release notes."
        )
    return (
        f"This is a test build of the panel app (build {build}), made before "
        f"version {release} is released. Its changes will be described in the "
        f"{release} release notes."
    )


async def async_release_notes(
    hass: HomeAssistant,
    offered: str,
    installed: str | None = None,
    *,
    test_build: int | None = None,
) -> str:
    """What changed since the installed version, always with the full notes link.

    A test build (a feed build, passed as its version code) whose version has no
    published summary says what it is, and links the notes of the newest release
    before it, because the website has nothing under the test build's version.
    """
    releases = _releases_between(await _async_summaries(hass), installed, offered)
    if test_build is not None and offered not in (v for v, _ in releases):
        notes = "\n\n".join(
            [_test_build_note(offered, test_build)]
            + [f"## {version}\n\n{text}" for version, text in releases]
        )
        # The website has no notes for test builds: link the newest release before it.
        earlier = _latest_release_before(await _async_summaries(hass), offered)
        if earlier is None:
            page = URL("https://panel-assistant.io/go/release-notes").with_query(
                component="panel-app"
            )
            return f"{notes}\n\n[Release notes]({page})"
        return (
            f"{notes}\n\n[Release notes for {earlier}, the last release]"
            f"({full_notes_url(earlier)})"
        )
    if len(releases) == 1:
        notes = releases[0][1]
    else:
        notes = "\n\n".join(f"## {version}\n\n{text}" for version, text in releases)
    link = full_notes_url(offered)
    if link in notes:
        return notes
    more = f"[Full release notes]({link})"
    return f"{notes}\n\n{more}" if notes else more

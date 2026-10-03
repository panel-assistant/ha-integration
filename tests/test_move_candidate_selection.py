"""Fresh identity moves install the newest eligible authenticated source bytes."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.panel_assistant import panel_move
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.feed_coordinator import (
    DATA_BUILD_FEED,
    DATA_STABLE_RELEASE,
)
from custom_components.panel_assistant.install_adb import AdbRootMode, InstallOutcome

from . import test_update_candidate_selection as candidates

key = candidates.key
trust = candidates.trust
real_release_reader = candidates.real_release_reader


@pytest.mark.parametrize(
    ("feed", "public", "installed", "expected_source", "expected"),
    [
        (
            [("1.1.0", 900, (3, 3))],
            [("1.2.0", 950, (3, 3))],
            ("1.0.0", 800),
            "public",
            ("1.2.0", 950),
        ),
        (
            [("1.2.0", 850, (3, 3)), ("1.1.0", 900, (3, 3))],
            [],
            ("1.0.0", 875),
            "feed",
            ("1.1.0", 900),
        ),
        (
            [("1.1.0", 900, (3, 3)), ("1.2.0", 850, (3, 3))],
            [],
            ("1.0.0", 800),
            "feed",
            ("1.2.0", 850),
        ),
        (
            [],
            [("1.2.0", 850, (3, 3)), ("1.1.0", 900, (3, 3))],
            ("1.0.0", 875),
            "public",
            ("1.1.0", 900),
        ),
        (
            [("1.2.0", 900, (3, 3))],
            [("1.2.0", 900, (3, 3))],
            ("1.0.0", 800),
            "feed",
            ("1.2.0", 900),
        ),
        (
            [("1.2.0", 900, None), ("1.1.0", 850, (3, 3))],
            [],
            ("1.0.0", 800),
            "feed",
            ("1.1.0", 850),
        ),
    ],
    ids=[
        "public-newer",
        "feed-code-before-rank",
        "feed-semantic-before-code",
        "public-code-before-rank",
        "equal-rank-feed",
        "unknown-range",
    ],
)
async def test_fresh_move_installs_newest_eligible_source_bytes(
    hass, monkeypatch, trust, key, feed, public, installed, expected_source, expected
):
    github, public_apks = candidates._public_pool(key, monkeypatch, public)
    feed_apks = candidates._add_feed(github, key, feed)
    entity, client = await candidates._panel(
        hass, monkeypatch, github, installed=installed
    )
    entry = hass.config_entries.async_get_entry("entry-id")
    entry.runtime_data = SimpleNamespace(client=client, coordinator=entity.coordinator)
    client.async_get_health = AsyncMock(return_value=entity.coordinator.data.health)
    hass.data.setdefault(DOMAIN, {})[DATA_BUILD_FEED] = entity._feed
    hass.data[DOMAIN][DATA_STABLE_RELEASE] = entity._release
    monkeypatch.setattr(panel_move, "async_get_clientsession", lambda _h: github)
    monkeypatch.setattr(
        panel_move,
        "async_preflight_install",
        AsyncMock(
            return_value=SimpleNamespace(
                migration_candidate=True, root_mode=AdbRootMode.ROOTLESS
            )
        ),
    )
    staged: list[tuple[Any, bytes]] = []
    installed_descriptors = []

    async def stage(_target, _signer, descriptor, _job, path: Path, **_kwargs):
        staged.append((descriptor, await hass.async_add_executor_job(path.read_bytes)))
        return "staged"

    async def install(_target, _signer, descriptor, _job, *, before_install, **_kwargs):
        await before_install()
        installed_descriptors.append(descriptor)
        return InstallOutcome.INSTALLED

    monkeypatch.setattr(panel_move, "async_stage_apk", stage)
    monkeypatch.setattr(panel_move, "async_install_staged_apk", install)
    monkeypatch.setattr(panel_move, "async_cleanup_staged_apk", AsyncMock())
    artifact, _ = await panel_move._async_install_successor(
        hass, entry, "target", "key"
    )
    expected_apk = (feed_apks if expected_source == "feed" else public_apks)[expected]
    assert (artifact.version, artifact.descriptor.version_code) == expected
    assert staged == [(artifact.descriptor, expected_apk)]
    assert installed_descriptors == [artifact.descriptor]


async def test_fresh_move_refuses_unknown_installed_snapshot(
    hass, monkeypatch, trust, key
):
    """A fresh install cannot assume ordering when the panel is unobserved."""
    github, _ = candidates._public_pool(key, monkeypatch, [("1.2.0", 900, (3, 3))])
    entity, client = await candidates._panel(hass, monkeypatch, github, with_feed=False)
    entry = hass.config_entries.async_get_entry("entry-id")
    client.async_get_health = AsyncMock(return_value=entity.coordinator.data.health)
    entry.runtime_data = SimpleNamespace(
        client=client, coordinator=SimpleNamespace(data=None)
    )
    hass.data.setdefault(DOMAIN, {})[DATA_STABLE_RELEASE] = entity._release
    download = AsyncMock(
        side_effect=AssertionError("unobserved panel downloaded an APK")
    )
    monkeypatch.setattr(panel_move, "async_download_build", download)
    with pytest.raises(panel_move.MoveError) as failure:
        await panel_move._async_install_successor(hass, entry, "target", "key")
    assert failure.value.reason == panel_move.REASON_RELEASE_UNAVAILABLE
    download.assert_not_awaited()

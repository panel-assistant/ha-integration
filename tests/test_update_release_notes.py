"""A panel's update dialog shows what changed in the offered app release."""

import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import release_summaries
from custom_components.panel_assistant.client import PanelInstallStatus
from custom_components.panel_assistant.release import ReleaseDownloadError
from custom_components.panel_assistant.update import UpdateEntityFeature

from .test_update import _entity

LINK = "https://panel-assistant.io/go/release-notes?v=0.9.10&component=panel-app"


def _site(monkeypatch: pytest.MonkeyPatch, **kwargs) -> AsyncMock:
    fetch = AsyncMock(**kwargs)
    monkeypatch.setattr(release_summaries, "_async_fetch_bounded", fetch)
    return fetch


def _summaries(**summaries: str) -> bytes:
    return json.dumps({"schema": 1, "summaries": summaries}).encode()


async def test_the_offered_release_summary_is_shown_with_a_link_to_the_full_notes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = _site(
        monkeypatch,
        return_value=_summaries(**{"v0.9.10": "Faster panels.", "v0.9.9": "Old."}),
    )
    entity, _ = _entity(hass)
    other, _ = _entity(hass)

    assert entity.supported_features & UpdateEntityFeature.RELEASE_NOTES
    assert await entity.async_release_notes() == (
        f"Faster panels.\n\n[Full release notes]({LINK})"
    )
    # Every panel shares one read of the summaries.
    assert await other.async_release_notes() == await entity.async_release_notes()
    assert fetch.await_count == 1


async def test_a_summary_that_already_links_the_full_notes_is_shown_as_published(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = f"Faster panels.\n\n[Full release notes]({LINK})"
    _site(monkeypatch, return_value=_summaries(**{"v0.9.10": text}))
    entity, _ = _entity(hass)
    assert await entity.async_release_notes() == text


@pytest.mark.parametrize(
    "site",
    [
        {"side_effect": ReleaseDownloadError("HTTP 404")},
        {"return_value": b"not json"},
        {"return_value": _summaries(**{"v0.9.10": "x" * 2049})},
    ],
)
async def test_without_a_usable_summary_the_dialog_still_links_the_full_notes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, site: dict
) -> None:
    _site(monkeypatch, **site)
    entity, _ = _entity(hass)
    assert await entity.async_release_notes() == f"[Full release notes]({LINK})"


async def test_a_panel_with_nothing_to_offer_shows_no_notes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = _site(monkeypatch, return_value=_summaries())
    entity, _ = _entity(hass, install_capability="none")
    assert await entity.async_release_notes() is None
    assert fetch.await_count == 0


async def test_the_last_summaries_read_still_show_after_a_restart_without_the_website(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, hass_storage: dict
) -> None:
    _site(monkeypatch, return_value=_summaries(**{"v0.9.10": "Faster panels."}))
    entity, _ = _entity(hass)
    await entity.async_release_notes()
    await hass.async_block_till_done()

    # A restart: nothing is kept in memory, and the website cannot be reached.
    hass.data.pop(release_summaries._CACHE)
    _site(monkeypatch, side_effect=ReleaseDownloadError("offline"))
    restarted, _ = _entity(hass)
    assert await restarted.async_release_notes() == (
        f"Faster panels.\n\n[Full release notes]({LINK})"
    )


async def test_a_panel_that_skipped_releases_sees_each_one_newest_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    _site(
        monkeypatch,
        return_value=_summaries(
            **{
                "v0.9.11": "Not offered yet.",
                "v0.9.10": "Faster panels.",
                "v0.9.10-rc1": "A candidate the stable release covers.",
                "v0.9.9": "Quieter logs.",
                "v0.9.8": "Already installed.",
            }
        ),
    )
    entity, _ = _entity(hass, version="0.9.8")
    assert await entity.async_release_notes() == (
        "## 0.9.10\n\nFaster panels.\n\n"
        "## 0.9.9\n\nQuieter logs.\n\n"
        f"[Full release notes]({LINK})"
    )


async def test_an_update_waiting_for_a_tap_says_so_above_what_changed(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    _site(monkeypatch, return_value=_summaries(**{"v0.9.10": "Faster panels."}))
    entity, _ = _entity(
        hass,
        operation=PanelInstallStatus(
            running=True, component="APK", awaiting_confirmation=True
        ),
    )
    # The dialog shows release notes instead of the summary when an entity has them.
    assert await entity.async_release_notes() == (
        "<ha-alert>Tap Install on the panel to finish the update.</ha-alert>\n\n"
        f"Faster panels.\n\n[Full release notes]({LINK})"
    )

"""A panel's update dialog shows what changed in the offered app release."""

import json
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import release_summaries
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

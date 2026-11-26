"""Try again on a refused panel update tells the owner why, and what to do next."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.panel_assistant.client import UpdateRejectedError
from custom_components.panel_assistant.repairs_failure import (
    async_record_update_failure,
)
from tests.test_failure_repair_flow import repairs_ready  # noqa: F401
from tests.test_retry_during_running_update import (  # noqa: F401
    _panel,
    _try_again,
    platforms,
)

_STRINGS = json.loads(
    (
        Path(__file__).parents[1] / "custom_components/panel_assistant/strings.json"
    ).read_text()
)


def _rendered_errors(result: dict[str, Any]) -> dict[str, str]:
    """The retry error as each installer failure Repair's dialog would show it."""
    (key,) = result["errors"].values()
    rendered = {}
    for issue, block in _STRINGS["issues"].items():
        errors = block.get("fix_flow", {}).get("error", {})
        if issue.startswith("installer_failure") and "retry_failed" in errors:
            assert key in errors, f"{issue} has no text for {key}"
            rendered[issue] = errors[key].format(**result["description_placeholders"])
    assert rendered
    return rendered


@pytest.mark.parametrize(
    ("title", "refusal", "expected"),
    [
        (
            "Kitchen",
            UpdateRejectedError(),
            "Kitchen refused the update request. Check its Install tab, "
            "then try again.",
        ),
        (
            "Kitchen",
            UpdateRejectedError("refused (not enough storage)"),
            "Kitchen refused the update: not enough storage",
        ),
        (
            # A panel name quoting a placeholder is shown as the owner wrote it.
            "Kitchen {reason}",
            UpdateRejectedError("refused (low space)"),
            "Kitchen {reason} refused the update: low space",
        ),
    ],
)
async def test_refused_retry_shows_the_panel_reason_and_clear_error(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],  # noqa: F811
    title: str,
    refusal: UpdateRejectedError,
    expected: str,
) -> None:
    entity, client, _staging, _release = await _panel(
        hass, monkeypatch, platforms, title=title
    )
    client.async_stage_apk = AsyncMock(side_effect=refusal)
    await async_record_update_failure(
        hass, "entry-id", title, "0.9.10", RuntimeError("download timed out")
    )

    result = await _try_again(hass_client, hass)

    assert result["type"] == "form"
    assert result["step_id"] == "retry_error"
    assert result["errors"] == {"base": "retry_failed_reason"}
    assert client.async_stage_apk.await_count == 1
    for text in _rendered_errors(result).values():
        assert expected in text
        assert "Clear error" in text
    assert not entity.in_progress


async def test_unexplained_retry_failure_keeps_the_generic_text(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],  # noqa: F811
) -> None:
    """An error with no owner-facing message still reads as a failed retry."""
    _entity, client, _staging, _release = await _panel(hass, monkeypatch, platforms)
    client.async_stage_apk = AsyncMock(side_effect=HomeAssistantError("disk full"))
    await async_record_update_failure(
        hass, "entry-id", "Kitchen", "0.9.10", RuntimeError("download timed out")
    )

    result = await _try_again(hass_client, hass)

    assert result["errors"] == {"base": "retry_failed"}
    assert "retry_reason" not in result["description_placeholders"]


async def test_stable_only_panel_retries_its_offer_without_a_version(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],  # noqa: F811
) -> None:
    """Home Assistant refuses a version for an entity that cannot pick one."""
    entity, _client, _staging, _release = await _panel(
        hass, monkeypatch, platforms, feed=False
    )
    assert UpdateEntityFeature.SPECIFIC_VERSION not in entity.supported_features
    offered = hass.states.get(entity.entity_id).attributes["latest_version"]
    install = AsyncMock()
    monkeypatch.setattr(entity, "async_install", install)
    await async_record_update_failure(
        hass, "entry-id", "Kitchen", offered, RuntimeError("download timed out")
    )

    await _try_again(hass_client, hass)

    # The retry reached the entity, which installs its current offer.
    install.assert_awaited_once_with(None, False)


async def test_retry_mirrors_the_update_progress(
    hass: HomeAssistant,
    repairs_ready: None,  # noqa: F811
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    platforms: list[EntityPlatform],  # noqa: F811
) -> None:
    """The bar follows the update's own percentage, and stops when the retry ends."""
    entity, _client, staging, release = await _panel(hass, monkeypatch, platforms)
    await async_record_update_failure(
        hass, "entry-id", "Kitchen", "0.9.10", RuntimeError("download timed out")
    )
    seen: list[float] = []
    hass.bus.async_listen(
        "data_entry_flow_progress_update",
        callback(lambda event: seen.append(event.data["progress"])),
    )

    def report(percentage: int) -> None:
        state = hass.states.get(entity.entity_id)
        hass.states.async_set(
            entity.entity_id,
            state.state,
            {**state.attributes, "update_percentage": percentage},
        )

    retry = hass.async_create_task(_try_again(hass_client, hass))
    try:
        await asyncio.wait_for(staging.wait(), 5)
        report(25)
        report(60)
        await asyncio.sleep(0)
    finally:
        release.set()
    await asyncio.wait_for(retry, 10)
    await hass.async_block_till_done()

    assert [value for value in seen if value in (0.25, 0.6)] == [0.25, 0.6]
    report(90)
    await hass.async_block_till_done()
    assert 0.9 not in seen

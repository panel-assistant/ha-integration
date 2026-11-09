"""Proximity setup is raised as a Repair until the panel reports it calibrated.

Proximity is optional, so the Repair can always be ignored: Home Assistant shows
its Ignore button only for an issue without a fix flow.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import InvalidResponseError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import parse_status_response
from tests.test_repair_links import assert_repair_links

from .test_permission_repairs import HEALTH, _panel

pytest_plugins = ["tests.test_voice"]


def _status(proximity: Any) -> Any:
    document: dict[str, Any] = {"warnings": [], "capabilities": []}
    if proximity is not None:
        document["proximity"] = proximity
    return parse_status_response(json.dumps(document))


def _issue(hass: HomeAssistant, entry: Any) -> Any:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"panel_proximity_setup_{entry.entry_id}"
    )


async def _poll(hass: HomeAssistant, coordinator: Any, proximity: Any) -> None:
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=_status(proximity)),
        ),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success


@pytest.mark.parametrize(
    "proximity",
    [
        [],
        "ready",
        {"state": "unknown"},
        {"state": "ready", "wake_ready": "yes"},
        {"wake_ready": True},
    ],
)
def test_malformed_proximity_observations_are_refused(proximity: Any) -> None:
    with pytest.raises(InvalidResponseError):
        _status(proximity)


async def test_an_uncalibrated_sensor_raises_one_warning_until_it_is_calibrated(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)

    await _poll(
        hass, coordinator, {"state": "calibration_required", "wake_ready": False}
    )
    await _poll(
        hass, coordinator, {"state": "calibration_required", "wake_ready": False}
    )

    issue = _issue(hass, entry)
    assert issue is not None
    assert not issue.is_fixable and issue.severity is ir.IssueSeverity.WARNING
    assert_repair_links(issue)
    # The Repair links straight to the panel's Presence & wake card in the sidebar.
    assert {
        "panel": "Kitchen display",
        "configure_url": f"/panel-assistant/{entry.entry_id}"
        "/configure/cfg-proximity-learning",
    }.items() <= issue.translation_placeholders.items()
    assert sum(domain == DOMAIN for domain, _ in ir.async_get(hass).issues) == 1

    # Someone part way through setup is neither nagged nor told it is done.
    await _poll(hass, coordinator, {"state": "calibrating", "wake_ready": False})
    assert _issue(hass, entry) is not None

    await _poll(hass, coordinator, {"state": "ready", "wake_ready": True})
    assert _issue(hass, entry) is None


async def test_a_self_calibrating_sensor_clears_the_warning(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, {"state": "calibration_required"})
    assert _issue(hass, entry) is not None

    await _poll(hass, coordinator, {"state": "learning", "wake_ready": False})
    assert _issue(hass, entry) is None


@pytest.mark.parametrize(
    "proximity",
    [
        None,
        {"state": "ready", "wake_ready": True},
        {"state": "source_unavailable", "wake_ready": False},
        {"state": "calibrating"},
        {"state": "learning", "wake_ready": False},
    ],
)
async def test_no_warning_without_a_sensor_that_needs_setup(
    hass: HomeAssistant, proximity: Any
) -> None:
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, proximity)

    assert _issue(hass, entry) is None


async def test_the_owner_can_ignore_the_repair_and_it_stays_ignored(
    hass: HomeAssistant, hass_ws_client: Any
) -> None:
    """Through Home Assistant's Repairs API, as its Repairs page uses it."""
    assert await async_setup_component(hass, "repairs", {})
    entry, coordinator = _panel(hass)
    issue_id = f"panel_proximity_setup_{entry.entry_id}"
    client = await hass_ws_client(hass)

    async def listed() -> Any:
        await client.send_json_auto_id({"type": "repairs/list_issues"})
        message = await client.receive_json()
        assert message["success"]
        return next(
            (
                issue
                for issue in message["result"]["issues"]
                if issue["domain"] == DOMAIN and issue["issue_id"] == issue_id
            ),
            None,
        )

    await _poll(
        hass, coordinator, {"state": "calibration_required", "wake_ready": False}
    )
    issue = await listed()
    # A fixable issue opens its fix flow and has no Ignore button.
    assert issue is not None and issue["is_fixable"] is False
    assert issue["ignored"] is False

    await client.send_json_auto_id(
        {
            "type": "repairs/ignore_issue",
            "domain": DOMAIN,
            "issue_id": issue_id,
            "ignore": True,
        }
    )
    assert (await client.receive_json())["success"]

    # A sensor that is briefly unreadable has not been set up either: the
    # ignored Repair is kept rather than raised afresh when it reads again.
    for proximity in (
        {"state": "source_unavailable", "wake_ready": False},
        {"state": "calibration_required", "wake_ready": False},
    ):
        await _poll(hass, coordinator, proximity)
        issue = await listed()
        assert issue is not None and issue["ignored"] is True

    # A panel that no longer asks for setup (presence off, Wake on wave off)
    # sends no proximity state, and the Repair goes.
    await _poll(hass, coordinator, None)
    assert await listed() is None


async def test_a_briefly_unreadable_sensor_keeps_an_open_repair(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, {"state": "calibration_required"})
    created = _issue(hass, entry).created
    await _poll(hass, coordinator, {"state": "source_unavailable"})
    await _poll(hass, coordinator, {"state": "calibration_required"})

    assert _issue(hass, entry).created == created


@pytest.mark.parametrize(
    "language",
    ["en", "cs", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "uk", "zh-Hans"],
)
async def test_proximity_repair_has_translated_guidance(
    hass: HomeAssistant, language: str
) -> None:
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.panel_proximity_setup"
    assert "{panel}" in strings[f"{prefix}.title"]
    description = strings[f"{prefix}.description"]
    assert "{panel}" in description
    assert "]({configure_url})" in description
    assert not any(key.startswith(f"{prefix}.fix_flow") for key in strings)

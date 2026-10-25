"""Proximity setup is raised as a Repair until the panel reports it calibrated."""

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
        {"state": "learning"},
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
    assert issue.is_fixable and issue.severity is ir.IssueSeverity.WARNING
    assert_repair_links(issue)
    assert {
        "panel": "Kitchen display"
    }.items() <= issue.translation_placeholders.items()
    assert sum(domain == DOMAIN for domain, _ in ir.async_get(hass).issues) == 1

    # Someone part way through setup is neither nagged nor told it is done.
    await _poll(hass, coordinator, {"state": "calibrating", "wake_ready": False})
    assert _issue(hass, entry) is not None

    await _poll(hass, coordinator, {"state": "ready", "wake_ready": True})
    assert _issue(hass, entry) is None


@pytest.mark.parametrize(
    "proximity",
    [
        None,
        {"state": "ready", "wake_ready": True},
        {"state": "source_unavailable", "wake_ready": False},
        {"state": "calibrating"},
    ],
)
async def test_no_warning_without_a_sensor_that_needs_setup(
    hass: HomeAssistant, proximity: Any
) -> None:
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, proximity)

    assert _issue(hass, entry) is None


@pytest.mark.parametrize(
    ("after", "outcome"),
    [
        ({"state": "ready", "wake_ready": True}, None),
        ({"state": "calibration_required"}, "proximity_not_calibrated"),
        ({"state": "calibrating"}, "proximity_calibrating"),
        ("unreadable", "proximity_unreadable"),
    ],
)
async def test_the_repair_completes_only_on_a_fresh_calibrated_read(
    hass: HomeAssistant, hass_client: Any, after: Any, outcome: str | None
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    entry, coordinator = _panel(hass)
    status = AsyncMock(return_value=_status({"state": "calibration_required"}))
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(coordinator.client, "async_get_status", status),
    ):
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={
                "handler": DOMAIN,
                "issue_id": f"panel_proximity_setup_{entry.entry_id}",
            },
        )
        flow = await response.json()
        assert flow["type"] == "form"
        assert flow["step_id"] == "set_up_proximity"
        # The step links straight to the panel's Presence & wake card in the sidebar.
        assert flow["description_placeholders"] == {
            "panel": "Kitchen display",
            "configure_url": f"/panel-assistant/{entry.entry_id}"
            "/configure/cfg-proximity-learning",
        }
        if after == "unreadable":
            status.side_effect = InvalidResponseError
        else:
            status.return_value = _status(after)
        response = await admin.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
        )
        result = await response.json()
    if outcome is None:
        assert result["type"] == "create_entry"
        assert _issue(hass, entry) is None
    else:
        assert result["type"] == "form"
        assert result["errors"] == {"base": outcome}


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
    description = strings[f"{prefix}.fix_flow.step.set_up_proximity.description"]
    assert "{panel}" in description
    assert "]({configure_url})" in description
    for error in (
        "proximity_not_calibrated",
        "proximity_calibrating",
        "proximity_unreadable",
    ):
        assert strings[f"{prefix}.fix_flow.error.{error}"]

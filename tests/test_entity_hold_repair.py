"""A dashboard released unfiltered from an entity hold is logged at once, and
raises a Repair only when it stays unfiltered."""

import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import parse_status_response
from tests.test_repair_links import assert_repair_links

from .test_permission_repairs import HEALTH, _panel

pytest_plugins = ["tests.test_voice"]


def _status(**hold: Any) -> Any:
    renderer: dict[str, Any] = {
        "mode": "builtin",
        "state": "rendered",
        "rendered": True,
    }
    renderer.update(hold)
    return parse_status_response(
        json.dumps({"warnings": [], "capabilities": [], "renderer": renderer})
    )


def _issue(hass: HomeAssistant, entry: Any) -> Any:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"dashboard_unfiltered_{entry.entry_id}"
    )


async def _poll(hass: HomeAssistant, coordinator: Any, status: Any) -> None:
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client, "async_get_status", AsyncMock(return_value=status)
        ),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success


RELEASED = {
    "entity_hold": "scan_failed",
    "entity_hold_state": "released",
    "entity_hold_age_ms": 95_000,
}


async def test_a_short_release_is_a_log_line_not_a_repair(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel that filters again on its own within the half hour needs nothing."""
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, _status(**RELEASED))
    freezer.tick(timedelta(minutes=29))
    await _poll(hass, coordinator, _status(**RELEASED))

    assert _issue(hass, entry) is None
    logged = [
        record
        for record in caplog.records
        if "without entity filtering" in record.getMessage()
    ]
    assert len(logged) == 1 and logged[0].levelname == "WARNING"
    assert "Kitchen display" in logged[0].getMessage()

    # Filtered again; a later release starts its own half hour.
    await _poll(hass, coordinator, _status(entity_hold=None, entity_hold_state=None))
    freezer.tick(timedelta(minutes=5))
    await _poll(hass, coordinator, _status(**RELEASED))
    assert _issue(hass, entry) is None


async def test_a_released_hold_raises_one_warning_until_the_panel_filters_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry, coordinator = _panel(hass)

    # Still holding: the panel is working inside its time limit, nothing to say yet.
    await _poll(
        hass,
        coordinator,
        _status(
            entity_hold="learning",
            entity_hold_state="holding",
            entity_hold_age_ms=20_000,
        ),
    )
    assert _issue(hass, entry) is None

    released = _status(**RELEASED)
    await _poll(hass, coordinator, released)
    assert _issue(hass, entry) is None
    freezer.tick(timedelta(minutes=30))
    await _poll(hass, coordinator, released)

    issue = _issue(hass, entry)
    assert issue is not None
    assert not issue.is_fixable and issue.severity is ir.IssueSeverity.WARNING
    assert_repair_links(issue)
    assert issue.translation_placeholders["panel"] == "Kitchen display"
    assert issue.translation_placeholders["entities_url"].endswith(
        f"/{entry.entry_id}/entities"
    )
    assert sum(domain == DOMAIN for domain, _ in ir.async_get(hass).issues) == 1

    # The hold cleared and the dashboard was rebuilt filtered.
    await _poll(hass, coordinator, _status(entity_hold=None, entity_hold_state=None))
    assert _issue(hass, entry) is None


async def test_an_app_that_reports_no_hold_raises_nothing(hass: HomeAssistant) -> None:
    entry, coordinator = _panel(hass)

    await _poll(hass, coordinator, _status())

    assert _issue(hass, entry) is None

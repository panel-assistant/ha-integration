"""An app update's own restart is not reported as a panel health error."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant.client import CannotConnectError

from .test_permission_repairs import HEALTH, _panel
from .test_update_failure_repairs import _entity

COORDINATOR_LOGGER = "custom_components.panel_assistant.coordinator"


@pytest.mark.parametrize("updating", [True, False])
async def test_restart_during_a_driven_update_logs_no_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, updating: bool
) -> None:
    _entry, coordinator = _panel(hass)
    entity, _client = _entity(hass)
    # The update entity's progress drives this panel's coordinator.
    entity._progress._coordinator = coordinator
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    if updating:
        entity._progress.begin(hass, "0.9.9", "0.9.10")
    caplog.clear()
    with (
        caplog.at_level(logging.DEBUG, COORDINATOR_LOGGER),
        patch.object(
            coordinator.client,
            "async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        # The app restarts into the new build and cannot answer for a while.
        await coordinator.async_refresh()
    entity._progress.end()
    assert not coordinator.last_update_success
    errors = [
        record
        for record in caplog.records
        if record.name == COORDINATOR_LOGGER and record.levelno >= logging.ERROR
    ]
    assert bool(errors) != updating
    # After the update ends, failures are reported again.
    assert not coordinator.expected_restart

"""Coordinator errors identify the affected panel and authentication cause."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import HaPaneldDataUpdateCoordinator
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.release import ReleaseResolutionError

# The shared fixture suppresses background GitHub reads. These diagnostic tests
# explicitly exercise the production release refresh with a local failing resolver.
_release_update = StableReleaseCoordinator._async_update_data


async def test_health_poll_failure_log_identifies_panel(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Polling failures remain attributable when several panels are configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example display",
        data={"address": "192.0.2.1"},
    )
    entry.add_to_hass(hass)
    client = MagicMock(
        async_get_health=AsyncMock(side_effect=CannotConnectError("offline"))
    )
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert any(
        "Example display" in record.getMessage()
        and "Error fetching" in record.getMessage()
        for record in caplog.records
    )


async def test_release_authentication_failure_log_includes_cause(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed signed release lookup preserves its actionable cause in logs."""
    caplog.set_level(logging.WARNING)
    coordinator = StableReleaseCoordinator(hass)
    detail = "Release signature does not match"

    async def cannot_authenticate(_session: object) -> None:
        try:
            raise ValueError(detail)
        except ValueError as err:
            raise ReleaseResolutionError from err

    with (
        patch.object(StableReleaseCoordinator, "_async_update_data", _release_update),
        patch(
            "custom_components.panel_assistant.release_catalog."
            "async_resolve_update_candidates",
            side_effect=cannot_authenticate,
        ),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert detail in caplog.text


async def test_diagnostics_download_for_unloaded_entry_is_reduced_report(
    hass: HomeAssistant, hass_client
) -> None:
    """A download while the entry is not loaded answers a reduced report, not 500."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "diagnostics", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example display",
        data={"address": "192.0.2.1"},
    )
    entry.add_to_hass(hass)
    client = await hass_client()

    response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")

    assert response.status == 200
    data = (await response.json())["data"]
    assert data["state"] == "not_loaded"
    assert data["entry"]["address"] == "**REDACTED**"
    assert data["health"] is None
    assert data["status"] is None

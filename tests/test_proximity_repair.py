"""Proximity setup is raised as a Repair until the panel reports it calibrated."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import InvalidResponseError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import parse_status_response
from tests.test_repair_links import assert_repair_links

from .test_permission_repairs import HEALTH, _panel
from .test_upgrade_repairs import CURRENT, _entry
from .test_upgrade_repairs import _panel as _upgrade_panel

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


@pytest.mark.parametrize("reloaded", [True, False])
async def test_only_the_current_runtime_owner_can_finish_the_repair(
    hass: HomeAssistant, hass_client: Any, hass_read_only_user: Any, reloaded: bool
) -> None:
    """A read awaited across a reload of the same entry belongs to the old runtime.

    The flow awaits its coordinator's refresh directly, so Home Assistant's
    reload neither cancels that read nor stops it publishing its result. Only
    the coordinator that now owns the entry may say the sensor is calibrated.
    """
    assert await async_setup_component(hass, "repairs", {})
    entry = _entry(hass, hass_read_only_user.id)
    uncalibrated = _status({"state": "calibration_required", "wake_ready": False})
    calibrated = _status({"state": "ready", "wake_ready": True})
    held = asyncio.Event()
    release = asyncio.Event()
    reads: list[str] = []

    async def status(self: Any, **kwargs: Any) -> Any:
        reads.append("read")
        if len(reads) == 2:
            # The Repair's own read: hold it at the panel until released.
            held.set()
            await release.wait()
            return calibrated
        return uncalibrated

    with (
        _upgrade_panel(CURRENT),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert _issue(hass, entry) is not None
        retired = entry.runtime_data.coordinator
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={
                "handler": DOMAIN,
                "issue_id": f"panel_proximity_setup_{entry.entry_id}",
            },
        )
        flow = await response.json()
        submit = asyncio.ensure_future(
            admin.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
        )
        await asyncio.wait_for(held.wait(), 5)
        if reloaded:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert hass.config_entries.async_get_entry(entry.entry_id) is entry
            assert entry.runtime_data.coordinator is not retired
        release.set()
        result = await (await submit).json()
        # The retired read did complete, and successfully: nothing stopped it.
        assert retired.last_update_success
        assert retired.data.status.proximity == {"state": "ready", "wake_ready": True}

    if reloaded:
        assert result["type"] == "form"
        assert result["errors"] == {"base": "proximity_unreadable"}
    else:
        assert result["type"] == "create_entry"
        assert _issue(hass, entry) is None
    assert await hass.config_entries.async_unload(entry.entry_id)

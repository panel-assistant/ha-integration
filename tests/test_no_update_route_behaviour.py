"""The repair must follow the route, not a remembered answer about it.

These three were proved by review rather than by the unit tests that shipped
with the first attempt. Each one is the difference between a repair that works
and a repair that is merely present: the first is the whole feature, and the
other two leave a panel accused of something that is no longer true.
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.adb_credentials import AdbCredentialMissingError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update_route_repair import (
    no_update_route_issue_id,
)

pytest_plugins = ["tests.test_update_adb_route"]


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", data={}, title="Study display"
    )
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, no_update_route_issue_id(entry.entry_id)
    )


def _up_to_date(route):
    route.entity._installed_code = 772
    route.entity.coordinator.data = replace(
        route.entity.coordinator.data,
        health=replace(route.entity.coordinator.data.health, version_code=772),
    )


async def test_a_lost_route_is_noticed_although_nothing_else_changed(
    route, hass: HomeAssistant
) -> None:
    """Losing ADB moves nothing the route key is made of, so the key cannot gate it.

    A panel loses its authorized route when something is done on the panel -- a
    firmware update, a reset turning developer options off. Its identity, build,
    address and artifact are all unchanged, so a check that trusts the previous
    answer for an unchanged key never looks again, and the repair never appears.
    """
    entry = _entry(hass)
    await route.entity._async_refresh_route()
    assert route.entity._has_install_route()

    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "no_update_route"
    assert issue.translation_placeholders == {"panel": "Study display"}
    assert issue.severity is ir.IssueSeverity.WARNING
    assert "panel-cannot-update" in issue.learn_more_url
    # Raw details remain available to support without being interpolated into
    # the user's translated Repair.
    assert route.entity.extra_state_attributes["update_unavailable_reason"]


async def test_a_panel_that_can_install_for_itself_again_is_let_off(
    route, hass: HomeAssistant
) -> None:
    """Regaining the privileged route returns early, and must still withdraw."""
    entry = _entry(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None

    route.entity.coordinator.data = replace(
        route.entity.coordinator.data,
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="api"
        ),
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()

    assert route.entity._has_install_route()
    assert _issue(hass, entry) is None


async def test_removing_the_panel_takes_its_repair_with_it(
    route, hass: HomeAssistant
) -> None:
    """The issue is persistent, so nothing else would ever clear it."""
    entry = _entry(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None

    assert await hass.config_entries.async_remove(entry.entry_id)

    assert _issue(hass, entry) is None


async def test_an_unreachable_panel_is_not_accused_of_losing_its_route(
    route, hass: HomeAssistant
) -> None:
    """A failed poll answers "no route" without ever reaching the panel.

    Ordinary network loss and a panel restart both land here, so reconciling on
    that answer would raise a repair saying authorization is unusable every time
    the link blipped. Silence is not evidence.
    """
    entry = _entry(hass)
    await route.entity._async_refresh_route()
    assert route.entity._has_install_route()

    route.entity.coordinator.last_update_success = False
    route.entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


async def test_an_established_repair_outlives_an_offline_spell(
    route, hass: HomeAssistant
) -> None:
    """The panel really has no route; going quiet must not retract that."""
    entry = _entry(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None

    route.entity.coordinator.last_update_success = False
    route.entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert _issue(hass, entry) is not None


@pytest.mark.parametrize("has_offer", [False, True])
async def test_recovered_adb_clears_repair_without_a_pending_update(
    route, hass, has_offer
):
    entry = _entry(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is not None

    route.probe_target.return_value = route.probe
    if not has_offer:
        _up_to_date(route)
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None
    if not has_offer:
        assert route.entity.latest_version == route.entity.installed_version


async def test_healthy_adb_without_offer_does_not_raise_repair(route, hass):
    entry = _entry(hass)
    _up_to_date(route)
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is None
    route.probe_target.assert_awaited()
    assert route.entity.latest_version == route.entity.installed_version


async def test_adb_loss_is_reported_without_a_pending_update(route, hass):
    entry = _entry(hass)
    _up_to_date(route)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert _issue(hass, entry) is not None


async def test_build_verification_failure_is_not_route_loss(route, hass):
    entry = _entry(hass)
    route.entity._feed.async_verify_build = AsyncMock(
        side_effect=RuntimeError("feed unavailable")
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    route.probe_target.assert_awaited()
    assert _issue(hass, entry) is None


@pytest.mark.parametrize("existing_issue", [False, True])
async def test_wrong_http_identity_preserves_route_issue_state(
    route, hass, existing_issue
):
    entry = _entry(hass)
    if existing_issue:
        route.probe_target.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNAUTHORIZED
        )
        await route.entity._async_refresh_route()
        assert _issue(hass, entry) is not None
    route.probe_target.return_value = route.probe
    route.pinned_health.return_value = replace(
        route.entity.coordinator.data.health, panel_id="another-panel"
    )
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert (_issue(hass, entry) is not None) == existing_issue


async def test_open_adb_without_offer_does_not_create_credentials(
    route, hass, monkeypatch
):
    entry = _entry(hass)
    _up_to_date(route)
    route.get_credential.side_effect = AdbCredentialMissingError()
    create_key = AsyncMock(
        side_effect=AssertionError("passive observation must not create credentials")
    )
    monkeypatch.setattr(panel_update, "async_get_adb_credential", create_key)
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    route.probe_target.assert_awaited_once_with(route.pinned.pinned)
    create_key.assert_not_awaited()
    assert _issue(hass, entry) is None
    assert route.entity.latest_version == route.entity.installed_version


async def test_package_changed_during_probe_cannot_clear_repair(route, hass):
    entry = _entry(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None
    _up_to_date(route)
    route.probe_target.return_value = route.probe

    async def package_changed(*_args):
        route.entity.coordinator.data = replace(
            route.entity.coordinator.data,
            health=replace(route.entity.coordinator.data.health, package="another.app"),
        )
        return route.pinned

    route.revalidate.side_effect = package_changed
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None


@pytest.mark.parametrize("existing_issue", [False, True])
@pytest.mark.parametrize(
    "state", [InstallTargetState.RETAINED_OR_AMBIGUOUS, InstallTargetState.INSTALLED]
)
async def test_unreadable_adb_app_facts_preserve_issue(
    route, hass, existing_issue, state
):
    entry = _entry(hass)
    if existing_issue:
        route.probe_target.return_value = InstallTargetProbe(
            state=InstallTargetState.ADB_UNAUTHORIZED
        )
        await route.entity._async_refresh_route()
        assert _issue(hass, entry) is not None
    _up_to_date(route)
    route.probe_target.return_value = InstallTargetProbe(state=state)
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()
    assert (_issue(hass, entry) is not None) == existing_issue


async def test_a_route_check_that_raises_waits_for_the_next_poll(route, hass):
    """A failed check is not repeated at once: that spun the event loop."""
    _up_to_date(route)
    route.probe_target.side_effect = RuntimeError("unexpected")

    route.entity._schedule_route_refresh()
    async with asyncio.timeout(5):
        await hass.async_block_till_done()

    assert route.probe_target.await_count == 1

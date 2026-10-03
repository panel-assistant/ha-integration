"""The repair must follow the route, not a remembered answer about it.

These three were proved by review rather than by the unit tests that shipped
with the first attempt. Each one is the difference between a repair that works
and a repair that is merely present: the first is the whole feature, and the
other two leave a panel accused of something that is no longer true.
"""

from dataclasses import replace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update_route_repair import (
    no_update_route_issue_id,
)

pytest_plugins = ["tests.test_update_adb_route"]


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-id", data={})
    entry.add_to_hass(hass)
    return entry


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, no_update_route_issue_id(entry.entry_id)
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

    route.entity._async_install_route = AsyncMock(return_value=(None, None, None))
    route.entity._schedule_route_refresh()
    await hass.async_block_till_done()

    assert _issue(hass, entry) is not None


async def test_a_panel_that_can_install_for_itself_again_is_let_off(
    route, hass: HomeAssistant
) -> None:
    """Regaining the privileged route returns early, and must still withdraw."""
    entry = _entry(hass)
    route.entity._async_install_route = AsyncMock(return_value=(None, None, None))
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
    route.entity._async_install_route = AsyncMock(return_value=(None, None, None))
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
    route.entity._async_install_route = AsyncMock(return_value=(None, None, None))
    await route.entity._async_refresh_route()
    assert _issue(hass, entry) is not None

    route.entity.coordinator.last_update_success = False
    route.entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert _issue(hass, entry) is not None

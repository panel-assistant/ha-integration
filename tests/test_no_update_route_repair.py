"""A panel that cannot be updated says so, and the link it offers leaks nothing."""

from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.status import (
    PanelCachedUpdate,
    PanelDevice,
    PanelStatus,
)
from custom_components.panel_assistant.update_route_repair import (
    HELP_TOPIC,
    ISSUE_NO_UPDATE_ROUTE,
    async_reconcile_update_route_issue,
    help_parameters,
    no_update_route_issue_id,
)


def _status(**overrides) -> PanelStatus:
    """Build a status shaped like the one a real panel sends."""
    fields = {
        "warning_count": 0,
        "capability_count": 0,
        "install_capability": "none",
        "panel_assistant_update": PanelCachedUpdate(
            current_version="0.9.9-rc3", target_version="0.9.9-rc3", tag="v0.9.9-rc3"
        ),
        "panel_assistant_device": PanelDevice(
            name="Study display",
            manufacturer="Shelly",
            model="Jenna",
            hw_version="rk30board",
            area="Study",
        ),
    }
    fields.update(overrides)
    return PanelStatus(**fields)


def test_help_parameters_describe_the_hardware() -> None:
    parameters = help_parameters(_status())
    assert parameters["make"] == "Shelly"
    assert parameters["model"] == "Jenna"
    assert parameters["hw"] == "rk30board"
    assert parameters["app"] == "0.9.9-rc3"
    # Separates "never had a route" from "route withdrawn" for the page.
    assert parameters["cap"] == "none"


def test_help_parameters_never_carry_an_identity() -> None:
    """The browser opens this against a public site, so nothing identifying goes."""
    parameters = help_parameters(_status())
    leaked = {"Study display", "Study"}
    assert leaked.isdisjoint(set(parameters.values()))
    assert not {"name", "area"} & set(parameters)


def test_help_parameters_survive_a_panel_that_reports_nothing() -> None:
    """Older panels send no device block, so the page must still be reachable."""
    assert help_parameters(None) == {}
    assert "make" not in help_parameters(_status(panel_assistant_device=None))


@pytest.mark.asyncio
async def test_issue_is_raised_while_no_route_exists(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Study display")
    entry.add_to_hass(hass)
    async_reconcile_update_route_issue(
        hass, entry, has_route=False, status=_status(), reason="no usable route"
    )
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, no_update_route_issue_id(entry.entry_id))
    assert issue is not None
    assert issue.translation_key == ISSUE_NO_UPDATE_ROUTE
    assert issue.severity is ir.IssueSeverity.WARNING
    # It carries no instructions of its own: the remedy differs per device.
    assert issue.learn_more_url is not None
    # The call site must spell the topic literally for the repository gate, so
    # hold that literal and the exported constant together here.
    assert HELP_TOPIC == "panel-cannot-update"
    assert HELP_TOPIC in issue.learn_more_url


@pytest.mark.asyncio
async def test_learn_more_url_routes_on_what_the_panel_reported(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Study display")
    entry.add_to_hass(hass)
    async_reconcile_update_route_issue(
        hass, entry, has_route=False, status=_status(), reason="no usable route"
    )
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, no_update_route_issue_id(entry.entry_id))
    query = parse_qs(urlparse(issue.learn_more_url).query)
    assert query["model"] == ["Jenna"]
    assert query["cap"] == ["none"]
    # help_url stamps the integration itself so an older install can be routed.
    assert "v" in query and "build" in query
    assert "Study" not in issue.learn_more_url


@pytest.mark.asyncio
async def test_issue_is_withdrawn_once_a_route_returns(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Study display")
    entry.add_to_hass(hass)
    registry = ir.async_get(hass)
    async_reconcile_update_route_issue(
        hass, entry, has_route=False, status=_status(), reason="no usable route"
    )
    assert registry.async_get_issue(DOMAIN, no_update_route_issue_id(entry.entry_id))
    async_reconcile_update_route_issue(
        hass, entry, has_route=True, status=_status(install_capability="api")
    )
    assert (
        registry.async_get_issue(DOMAIN, no_update_route_issue_id(entry.entry_id))
        is None
    )


@pytest.mark.parametrize(
    "language", ["en", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"]
)
async def test_the_issue_renders_panel_and_reason(hass, language):
    from homeassistant.helpers.translation import async_get_translations
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Study display")
    entry.add_to_hass(hass)
    async_reconcile_update_route_issue(
        hass, entry, has_route=False, status=_status(), reason="no usable route"
    )
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, no_update_route_issue_id(entry.entry_id)
    )
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.{issue.translation_key}"
    title = strings[f"{prefix}.title"].format(**issue.translation_placeholders)
    description = strings[f"{prefix}.description"].format(
        **issue.translation_placeholders
    )
    assert "Study display" in title
    assert "Study display" in description
    assert "no usable route" in description

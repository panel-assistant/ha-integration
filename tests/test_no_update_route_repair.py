"""A panel that cannot be updated says so, and the link it offers leaks nothing."""

from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.repairs_update_route import (
    async_reconcile_update_route_issue,
    no_update_route_issue_id,
)
from custom_components.panel_assistant.status import (
    PanelDevice,
    PanelStatus,
)
from tests.test_update_adb_route import route as route


def _status(**overrides) -> PanelStatus:
    """Build a status shaped like the one a real panel sends."""
    fields = {
        "warning_count": 0,
        "capability_count": 0,
        "install_capability": "none",
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


@pytest.mark.parametrize("reported", ["hardware", "no_device", "no_status"])
async def test_route_loss_offers_a_hardware_help_url_without_panel_identity(
    hass: HomeAssistant, route, reported: str
) -> None:
    """The entity's Repair sends hardware facts, never identity, to the public site."""
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", data={}, title="Study display"
    )
    entry.add_to_hass(hass)
    status = (
        None
        if reported == "no_status"
        else _status(panel_assistant_device=None)
        if reported == "no_device"
        else _status()
    )
    route.entity.coordinator.data = replace(
        route.entity.coordinator.data, status=status
    )
    route.client.async_get_legacy_install_capability = AsyncMock(return_value=False)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )

    await route.entity._async_refresh_route()

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, no_update_route_issue_id(entry.entry_id)
    )
    assert issue is not None
    query = parse_qs(urlparse(issue.learn_more_url).query)
    if reported == "hardware":
        assert {key: query.get(key) for key in ("make", "model", "hw", "cap")} == {
            "make": ["Shelly"],
            "model": ["Jenna"],
            "hw": ["rk30board"],
            "cap": ["none"],
        }
    else:
        assert not {"make", "model", "hw"} & query.keys()
    # The integration version routes older installs without identifying the panel.
    assert "v" in query and "build" in query
    assert not {"name", "area", "app"} & query.keys()
    assert "Study" not in issue.learn_more_url


@pytest.mark.parametrize(
    "language",
    ["en", "cs", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "uk", "zh-Hans"],
)
async def test_the_issue_renders_localized_guidance_for_the_named_panel(hass, language):
    from homeassistant.helpers.translation import async_get_translations
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Study display")
    entry.add_to_hass(hass)
    async_reconcile_update_route_issue(hass, entry, has_route=False, status=_status())
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
    assert {"panel": "Study display"}.items() <= issue.translation_placeholders.items()

"""Admission of panel builds follows the running integration and native range."""

from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.update_policy import (
    build_allowed,
    policy_for,
    prereleases_allowed,
)


@pytest.mark.parametrize("pa_version", ["1.0.0", "1.0.0-rc1"])
@pytest.mark.parametrize("override", [False, True])
def test_channel_defaults_to_running_pa_with_per_panel_opt_in(
    pa_version: str, override: bool
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"prerelease_panel_builds": override}
    )
    with patch(
        "custom_components.panel_assistant.update_policy.INTEGRATION_VERSION",
        pa_version,
    ):
        assert prereleases_allowed() is ("-" in pa_version)
        assert prereleases_allowed(entry) is (override or "-" in pa_version)
        assert policy_for(entry) == {
            "protocolMin": 1,
            "protocolMax": 3,
            "prerelease": override or "-" in pa_version,
        }


@pytest.mark.parametrize(
    "version,allow,expected",
    [
        ("1.0.0", False, True),
        ("1.0.0", True, True),
        ("1.0.0-rc1", False, False),
        ("1.0.0-rc1", True, True),
        ("dev-1234", False, False),
        ("dev-1234", True, False),
        ("", True, False),
        ("1.0.0\n", True, False),
        ("../1.0.0", True, False),
        ("a" * 65, True, False),
        ("1.0.0-rc1.", True, False),
        ("1.0.0-rc..1", True, False),
        ("1.0.0-01", True, False),
        ("1.0.0-rc1-2", True, True),
        (None, True, False),
        (123, True, False),
    ],
)
def test_build_channel_admission(version: str, allow: bool, expected: bool) -> None:
    assert build_allowed(version, 3, 3, allow_prerelease=allow) is expected


@pytest.mark.parametrize(
    "low,high,expected",
    [
        (1, 1, True),
        (3, 3, True),
        (2, 4, True),
        (4, 5, False),
        (None, None, False),
        (1, None, False),
        (None, 3, False),
        (0, 3, False),
        (-1, 3, False),
        (3, 1, False),
        (True, 3, False),
        (1, False, False),
        (1.0, 3, False),
        (1, "3", False),
        (1, 2**31, False),
        (2**31, 2**31, False),
    ],
)
def test_only_bounded_ordered_native_ranges_are_admitted(
    low: int | None, high: int | None, expected: bool
) -> None:
    assert build_allowed("1.0.0", low, high, allow_prerelease=True) is expected


@pytest.mark.parametrize("override", ["true", 1, None, False])
def test_prerelease_opt_in_must_be_explicit_boolean(override: object) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, options={"prerelease_panel_builds": override}
    )
    with patch(
        "custom_components.panel_assistant.update_policy.INTEGRATION_VERSION", "1.0.0"
    ):
        assert not prereleases_allowed(entry)


def test_unproven_pa_channel_never_widens_admission() -> None:
    entry = MockConfigEntry(domain=DOMAIN, options={"prerelease_panel_builds": True})
    with patch(
        "custom_components.panel_assistant.update_policy.INTEGRATION_VERSION", "unknown"
    ):
        assert not prereleases_allowed(entry)


async def test_offline_panel_options_default_off_and_remain_per_panel(
    hass: HomeAssistant,
) -> None:
    """The option is available without native entities and never edits another panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a" * 64,
        data={CONF_ADDRESS: "panel.local"},
        options={"authority": "native"},
    )
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="b" * 64,
        data={CONF_ADDRESS: "other.local"},
    )
    entry.add_to_hass(hass)
    other.add_to_hass(hass)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    assert "updates" in menu["menu_options"]
    form = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"next_step_id": "updates"}
    )
    assert form["type"] is FlowResultType.FORM
    assert form["data_schema"]({}) == {"prerelease_panel_builds": False}
    saved = await hass.config_entries.options.async_configure(
        menu["flow_id"], {"prerelease_panel_builds": True}
    )
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"authority": "native", "prerelease_panel_builds": True}
    assert entry.data == {CONF_ADDRESS: "panel.local"}
    assert entry.unique_id == "a" * 64
    assert dict(other.options) == {}

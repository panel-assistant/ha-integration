"""The one device card every platform shares, and the identity it must not adopt."""

from __future__ import annotations

from dataclasses import replace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.device import (
    async_refresh_panel_device,
    panel_device_info,
)
from custom_components.panel_assistant.status import PanelDevice, PanelStatus

HEALTH = PanelHealth(
    version="0.9.7-rc4",
    panel_id="alpha_panel",
    build="1788979164237",
    config_hash="7ac44093",
)


def _snapshot(
    device: PanelDevice | None,
    *,
    with_status: bool = True,
    health: PanelHealth = HEALTH,
) -> PanelSnapshot:
    status = (
        PanelStatus(warning_count=0, capability_count=0, panel_assistant_device=device)
        if with_status
        else None
    )
    return PanelSnapshot(health=health, status=status, status_error=None)


def test_reported_facts_fill_every_card_field() -> None:
    """A panel that states its hardware should leave no blank on the device page."""
    info = panel_device_info(
        "entry-1",
        _snapshot(
            PanelDevice(
                name="Alpha panel",
                manufacturer="Acme",
                model="AP-1",
                hw_version="Android 14 · TQ3A",
                area="Study",
            )
        ),
    )

    assert info["name"] == "Alpha panel"
    assert info["manufacturer"] == "Acme"
    assert info["model"] == "AP-1"
    assert info["suggested_area"] == "Study"
    assert info["sw_version"] == "0.9.7-rc4"
    assert info["configuration_url"] == "homeassistant://panel-assistant/entry-1"


def test_the_card_never_adopts_an_identity_the_mqtt_bridge_owns() -> None:
    """Sharing an identifier or a MAC merges the devices and splits authority."""
    info = panel_device_info(
        "entry-1",
        _snapshot(PanelDevice(name="Alpha panel", manufacturer="Acme")),
    )

    assert info["identifiers"] == {(DOMAIN, "entry-1")}
    assert "connections" not in info
    assert "serial_number" not in info


def test_a_silent_panel_still_produces_a_usable_card() -> None:
    """Panels below the release that added the projection must not blank the page."""
    for snapshot in (_snapshot(None), _snapshot(None, with_status=False)):
        info = panel_device_info("entry-1", snapshot)

        assert info["name"] == "alpha_panel"
        assert info["sw_version"] == "0.9.7-rc4"
        # Unstated, so left out: the registry keeps what the panel last said
        # rather than being told the product is this integration's app.
        assert "model" not in info
        assert "manufacturer" not in info
        assert "suggested_area" not in info


def test_a_partial_report_fills_only_what_the_panel_stated() -> None:
    """A field the panel dropped stays absent instead of arriving empty."""
    info = panel_device_info(
        "entry-1", _snapshot(PanelDevice(manufacturer="Acme", area="Study"))
    )

    assert info["manufacturer"] == "Acme"
    assert info["suggested_area"] == "Study"
    assert info["name"] == "alpha_panel"
    assert "model" not in info


def test_the_card_never_shows_the_android_release_as_its_hardware() -> None:
    """The Android release and build told a user nothing, so the line is cleared.

    Cleared rather than left out, so a card an earlier version registered loses
    it, and an older panel that still sends it is not shown it.
    """
    for snapshot in (
        _snapshot(
            PanelDevice(model="Wall Display X2i", hw_version="Android 11 · RD2A")
        ),
        _snapshot(None),
        None,
    ):
        info = panel_device_info("entry-1", snapshot, "alpha")

        assert "hw_version" in info
        assert info["hw_version"] is None


def test_the_profile_model_the_panel_reports_is_the_card_model() -> None:
    """The panel names the product from its profile; the card shows exactly that."""
    info = panel_device_info(
        "entry-1",
        _snapshot(PanelDevice(manufacturer="Shelly", model="Wall Display X2i")),
    )

    assert info["manufacturer"] == "Shelly"
    assert info["model"] == "Wall Display X2i"


def test_the_firmware_line_preserves_the_exact_version_and_build() -> None:
    """A release candidate is rebuilt many times, so the version alone is ambiguous."""
    info = panel_device_info(
        "entry-1", _snapshot(None, health=replace(HEALTH, version_code=812))
    )

    assert info["sw_version"] == "0.9.7-rc4 (812)"


def test_the_session_names_the_build_even_when_the_address_is_silent() -> None:
    """A connected panel declared its version and build; the card uses them."""
    for snapshot in (None, _snapshot(None, health=replace(HEALTH, version_code=812))):
        info = panel_device_info(
            "entry-1", snapshot, "alpha", app_build=("0.9.8-rc2", 904)
        )

        assert info["sw_version"] == "0.9.8-rc2 (904)"


def test_an_unknown_version_leaves_the_registered_one_alone() -> None:
    """Nobody has answered yet, so there is nothing to replace the last version with."""
    info = panel_device_info("entry-1", None, "alpha")

    assert "sw_version" not in info
    assert info["name"] == "alpha"


async def test_a_registered_card_is_brought_up_to_date(hass: HomeAssistant) -> None:
    """The card a panel registered earlier learns the session's build and loses
    the Android line, while the product it named is kept."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Shelly",
        model="Wall Display X2i",
        sw_version="0.9.8-rc1",
        hw_version="Android 11 · RD2A.211001.002 release-keys",
        configuration_url="http://192.168.1.23:8888/",
    )

    async_refresh_panel_device(
        hass,
        entry.entry_id,
        panel_device_info(entry.entry_id, None, "alpha", ("0.9.8-rc2", 904)),
    )

    device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert (
        device.configuration_url == f"homeassistant://panel-assistant/{entry.entry_id}"
    )
    assert device.sw_version == "0.9.8-rc2 (904)"
    assert device.hw_version is None
    assert device.manufacturer == "Shelly"
    assert device.model == "Wall Display X2i"


async def test_refreshing_never_creates_a_card(hass: HomeAssistant) -> None:
    """Only the entities register the device; a refresh before them is a no-op."""
    async_refresh_panel_device(
        hass, "entry-1", panel_device_info("entry-1", None, "alpha", ("0.9.8", 1))
    )

    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, "entry-1"), "entry-1"
        )
        is None
    )


@pytest.mark.parametrize("reported_area", ["null", " NuLl "])
async def test_literal_null_report_does_not_create_an_area(
    hass: HomeAssistant, reported_area: str
) -> None:
    """A stored panel value of `null` cannot seed a new HA area."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    info = panel_device_info(entry.entry_id, _snapshot(PanelDevice(area=reported_area)))

    assert "suggested_area" not in info
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **info
    )
    assert device.area_id is None
    assert not ar.async_get(hass).areas


@pytest.mark.parametrize("area_name", ["null", " NuLl "])
async def test_existing_literal_null_assignment_is_cleared_once(
    hass: HomeAssistant, area_name: str
) -> None:
    """Repair our device assignment while preserving all area records and real moves."""
    entry = MockConfigEntry(domain=DOMAIN, title="alpha")
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    accidental = areas.async_get_or_create(area_name)
    study = areas.async_get_or_create("Study")
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
    )
    registry.async_update_device(device.id, area_id=accidental.id)
    info = panel_device_info(entry.entry_id, _snapshot(PanelDevice(area="null")))

    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id is None
    assert areas.async_get_area(accidental.id) == accidental
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id is None

    registry.async_update_device(device.id, area_id=study.id)
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id == study.id

    registry.async_update_device(device.id, area_id="unavailable_area")
    async_refresh_panel_device(hass, entry.entry_id, info)
    assert registry.async_get(device.id).area_id == "unavailable_area"

"""The add flow owes the device setup pass only to an install it finished.

Each test drives the real config flow against the real receipt store and the
real executor, as test_install_unfinished.py does; the pass itself is a
recording stand-in here (test_deferred_admin.py and
test_device_setup_trigger.py run the real one).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_ZEROCONF
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import device_setup, install_executor
from custom_components.panel_assistant.const import (
    CONF_DEVICE_SETUP,
    CONF_DEVICE_SETUP_OWED,
    DOMAIN,
    SOURCE_USB_INSTALL,
)
from custom_components.panel_assistant.install_jobs import InstallPhase

from .config_flow_helpers import _zeroconf_info
from .install_executor_harness import Harness, seed_phase
from .test_install_unfinished import (  # noqa: F401
    FLOW,
    PANEL_DID,
    SEEDED,
    SEEDED_IP,
    SEEDED_IPV6,
    _add,
    _app_answers,
    _choose,
    _executor_health_names_the_panel,
    emulate_home_assistant_store_file,
    harness,
    install_release_catalog,
    pins,
)

RECORD = {"did": PANEL_DID, "package": "p", "version_code": 1, "root": False}
SETUP = "custom_components.panel_assistant.async_setup_entry"


@pytest.fixture
def passes(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The pass each started run makes; it finishes on its first attempt."""
    attempt = AsyncMock(return_value=RECORD)
    monkeypatch.setattr(device_setup, "async_attempt", attempt)
    # Entry setup is a stand-in here, so there is no coordinator to hand it.
    monkeypatch.setattr(device_setup, "_coordinator", lambda _entry: object())
    return attempt


def _assert_one_pass(passes: AsyncMock, entry: Any) -> None:
    assert passes.await_count == 1
    assert passes.await_args.args[1] is entry
    assert passes.await_args.kwargs == {"vendor": True}
    assert entry.data[CONF_DEVICE_SETUP] == RECORD
    assert CONF_DEVICE_SETUP_OWED not in entry.data


async def test_finishing_an_install_runs_the_pass_once_and_records_it(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    passes: AsyncMock,
) -> None:
    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    with (
        _app_answers({SEEDED_IP: PANEL_DID, SEEDED_IPV6: PANEL_DID}),
        patch(SETUP, return_value=True),
    ):
        menu = await _add(hass, f"[{SEEDED_IPV6}]")
        result = await _choose(hass, menu, "resume_install")
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    _assert_one_pass(passes, entry)


async def test_an_install_that_became_an_existing_entry_runs_the_pass_for_it(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    passes: AsyncMock,
) -> None:
    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    _executor_health_names_the_panel(monkeypatch)
    await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    added = MockConfigEntry(
        domain=DOMAIN,
        title="panel",
        unique_id=PANEL_DID,
        data={CONF_ADDRESS: f"[{SEEDED_IPV6}]", "installation_identity": True},
    )
    added.add_to_hass(hass)
    with patch(SETUP, return_value=True):
        result = await _add(hass, SEEDED.address)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    _assert_one_pass(passes, added)


async def test_the_usb_installer_runs_the_pass_for_the_panel_it_adds(
    hass: HomeAssistant, passes: AsyncMock
) -> None:
    with (
        _app_answers({SEEDED_IP: PANEL_DID, SEEDED_IPV6: PANEL_DID}),
        patch(f"{FLOW}.HaPaneldClient.async_get_status", AsyncMock()),
        patch(SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USB_INSTALL},
            data={CONF_ADDRESS: f"[{SEEDED_IPV6}]"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    _assert_one_pass(passes, result["result"])


async def test_the_usb_installer_runs_the_pass_for_a_panel_it_adds_over_ignore(
    hass: HomeAssistant, passes: AsyncMock
) -> None:
    """Core creates that entry without async_on_create_entry; setup starts it."""
    MockConfigEntry(
        domain=DOMAIN, source=SOURCE_IGNORE, unique_id=PANEL_DID, data={}
    ).add_to_hass(hass)

    async def setup_entry(hass: HomeAssistant, entry: Any) -> bool:
        # The part of the real entry setup that concerns the pass.
        device_setup.async_reconcile(hass, entry)
        return True

    with (
        _app_answers({SEEDED_IP: PANEL_DID, SEEDED_IPV6: PANEL_DID}),
        patch(f"{FLOW}.HaPaneldClient.async_get_status", AsyncMock()),
        patch(SETUP, side_effect=setup_entry),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USB_INSTALL},
            data={CONF_ADDRESS: f"[{SEEDED_IPV6}]"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    _assert_one_pass(passes, result["result"])


async def test_adding_a_panel_whose_app_was_already_installed_runs_no_pass(
    hass: HomeAssistant, passes: AsyncMock
) -> None:
    """The discovery card promises that no panel settings will change."""
    with (
        _app_answers({SEEDED_IP: PANEL_DID}),
        patch(f"{FLOW}.HaPaneldClient.async_get_status", AsyncMock()),
        patch(SETUP, return_value=True),
    ):
        card = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_ZEROCONF},
            data=_zeroconf_info(discovery_id=PANEL_DID, host=SEEDED_IP),
        )
        assert card["step_id"] == "confirm_discovery"
        result = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    passes.assert_not_awaited()
    assert CONF_DEVICE_SETUP_OWED not in entry.data
    assert CONF_DEVICE_SETUP not in entry.data

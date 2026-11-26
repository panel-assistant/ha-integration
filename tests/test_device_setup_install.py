"""The add flow owes the device setup pass only to an install it finished.

Each test drives the real config flow against the real receipt store and the
real executor, as test_install_unfinished.py does; the pass itself is a
recording stand-in here (test_deferred_admin.py and
test_device_setup_trigger.py run the real one).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_ZEROCONF,
    ConfigEntryState,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import device_setup, install_executor
from custom_components.panel_assistant.client import PanelSetupState
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
from .test_migration_targets import (  # noqa: F401
    _add_integration_opened,
    _companion,
    _discovered,
    adb_open,
)
from .test_transport import WsClientFactory, _hello, _send

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
    assert passes.await_args.kwargs == {}
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


@pytest.mark.parametrize("ignored", [False, True])
async def test_companion_install_keeps_the_verified_identity_when_health_is_unreachable(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    passes: AsyncMock,
    adb_open: SimpleNamespace,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    ignored: bool,
) -> None:
    """The panel can connect before Core can poll its stored install address."""
    if ignored:
        MockConfigEntry(
            domain=DOMAIN, source=SOURCE_IGNORE, unique_id=PANEL_DID, data={}
        ).add_to_hass(hass)
    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    handover = AsyncMock()
    monkeypatch.setattr(f"{FLOW}.async_offer_ha_url", handover)
    _executor_health_names_the_panel(monkeypatch)
    receipt, manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    _companion(hass, "Wall screen", SEEDED_IP, "wall-screen")
    adb_open.open.add(SEEDED_IP)
    await _add_integration_opened(hass)
    card = _discovered(hass)["Wall screen"]
    assert card["context"]["unique_id"].startswith("migration:")

    # Finalization reaches the app at the pinned IP. The first entry poll gets
    # no answer at the name the install used, so it cannot fill in the identity.
    with _app_answers({SEEDED_IP: PANEL_DID}):
        menu = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        result = await _choose(hass, menu, "resume_install")
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.CREATE_ENTRY
        (entry,) = hass.config_entries.async_entries(DOMAIN)
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.coordinator.data is None
        assert (
            await manager.async_get(receipt.job_id)
        ).consumed_entry_id == entry.entry_id
        (_, onboarding) = result["next_flow"]
        assert (
            hass.config_entries.options.async_get(onboarding)["step_id"] == "onboarding"
        )
        early = await hass.config_entries.options.async_configure(onboarding, {})
        assert early["errors"] == {"base": "panel_not_connected"}

        client = await hass_ws_client(hass, hass_read_only_access_token)
        wrong = await _send(client, _hello(did="e" * 64, addresses=[SEEDED_IP]))
        assert wrong["error"]["code"] == "unknown_panel"
        response = await _send(client, _hello(did=PANEL_DID, addresses=[SEEDED_IP]))
        assert response["success"], (entry.unique_id, response)
        finished = await hass.config_entries.options.async_configure(onboarding, {})
        assert finished["type"] is FlowResultType.ABORT
        assert finished["reason"] == "panel_connected"
        await client.close()

    assert entry.unique_id == PANEL_DID
    _assert_one_pass(passes, entry)


async def test_aborted_companion_install_preserves_the_ignored_panel(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    passes: AsyncMock,
    adb_open: SimpleNamespace,  # noqa: F811
) -> None:
    """A different entry can claim the install address during setup verification."""
    ignored = MockConfigEntry(
        domain=DOMAIN, source=SOURCE_IGNORE, unique_id=PANEL_DID, data={}
    )
    ignored.add_to_hass(hass)
    competing = MockConfigEntry(
        domain=DOMAIN, unique_id="e" * 64, data={CONF_ADDRESS: SEEDED.address}
    )

    async def setup_state(_client: object) -> PanelSetupState:
        competing.add_to_hass(hass)
        return PanelSetupState(complete=True)

    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    monkeypatch.setattr(f"{FLOW}.HaPaneldClient.async_get_setup_state", setup_state)
    _executor_health_names_the_panel(monkeypatch)
    receipt, manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    _companion(hass, "Wall screen", SEEDED_IP, "wall-screen")
    adb_open.open.add(SEEDED_IP)
    await _add_integration_opened(hass)
    card = _discovered(hass)["Wall screen"]

    with _app_answers({SEEDED_IP: PANEL_DID}):
        menu = await hass.config_entries.flow.async_configure(card["flow_id"], {})
        result = await _choose(hass, menu, "resume_install")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_get_entry(ignored.entry_id) is ignored
    assert hass.config_entries.async_get_entry(competing.entry_id) is competing
    persisted = await manager.async_get(receipt.job_id)
    assert persisted.phase is InstallPhase.HEALTHY_UNCLAIMED
    executor = await install_executor.async_get_install_executor(hass)
    assert not executor.is_finalizer_active(receipt.job_id)
    passes.assert_not_awaited()

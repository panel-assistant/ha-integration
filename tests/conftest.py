"""Shared pytest fixtures for ha-paneld."""

from collections.abc import Generator
from shutil import copytree, ignore_patterns
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import get_test_config_dir

from custom_components.panel_assistant import (
    config_flow,
    feed_coordinator,
    update_policy,
)
from custom_components.panel_assistant.client import (
    HaPaneldClient,
    PanelInstallStatus,
    PanelSetupState,
)


@pytest.fixture
def hass_config_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Give every test its own config directory.

    The fixture default is one directory inside the installed test package,
    shared by every process, so parallel workers deleted each other's
    `.storage` files. Stored state left there by earlier runs is not copied.
    """
    path = tmp_path_factory.mktemp("hass_config")
    copytree(
        get_test_config_dir(),
        path,
        symlinks=True,
        dirs_exist_ok=True,
        ignore=ignore_patterns(".storage"),
    )
    return str(path)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Allow Home Assistant to load the integration under test."""
    yield


@pytest.fixture(autouse=True)
def _prerelease_panel_assistant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as a pre-release Panel Assistant unless a test says otherwise.

    The update fixtures offer pre-release panel builds, which a stable Panel
    Assistant admits only on a panel's opt-in. Pinning the channel here keeps the
    suite independent of the release being numbered; the stable channel has its
    own tests that set the version explicitly.
    """
    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.0.0-test")


@pytest.fixture(autouse=True)
def _no_stable_release_from_github(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Generator[None]:
    """Know of no stable release unless a test serves one.

    Every panel set up would otherwise open a real socket to GitHub. The LAN
    update tests serve a signed release over a fake GitHub instead.
    """
    if request.path.name != "test_lan_staged_update.py" and request.node.name != (
        "test_coordinator_routes_by_package_and_clears_old_bridge"
    ):
        monkeypatch.setattr(
            feed_coordinator.StableReleaseCoordinator,
            "_async_update_data",
            AsyncMock(return_value=None),
        )
    yield


@pytest.fixture(autouse=True)
def _stub_panel_update_operation_in_lifecycle_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Generator[None]:
    """Keep lifecycle tests on their declared local panel fixtures."""
    # Both of these exercise the real client contract rather than a flow that
    # merely needs a panel to answer, so stubbing it out would test the stub.
    if request.path.name not in {"test_client.py", "test_ha_url.py"}:
        monkeypatch.setattr(
            HaPaneldClient,
            "async_get_panel_install_status",
            AsyncMock(return_value=PanelInstallStatus(running=False, component="")),
        )
        # A found panel reports its setup as done unless a test says otherwise.
        monkeypatch.setattr(
            HaPaneldClient, "async_get_setup_complete", AsyncMock(return_value=True)
        )
        # The same default in the richer form the handover path reads: setup done,
        # so nothing is handed over. Stubbed for the same reason as its sibling —
        # otherwise every install and adoption test would open a real socket to
        # ask a panel that is not there. The handover's own tests override it.
        monkeypatch.setattr(
            HaPaneldClient,
            "async_get_setup_state",
            AsyncMock(return_value=PanelSetupState(complete=True)),
        )
        # Reachability is answered locally; only its own tests open that path.
        monkeypatch.setattr(
            config_flow, "_async_host_answers", AsyncMock(return_value=True)
        )
    yield


#: Tests of the update route itself, which serve or mock the panel's answer.
_UPDATE_ROUTE_TESTS = frozenset(
    {
        "test_client.py",
        "test_feed_update.py",
        "test_install_executor.py",
        "test_lan_staged_update.py",
        "test_no_update_route_behaviour.py",
        "test_no_update_route_repair.py",
        "test_update.py",
        "test_update_adb_route.py",
        "test_update_candidate_selection.py",
        "test_update_failure_repairs.py",
    }
)


@pytest.fixture(autouse=True)
def _panel_installs_its_own_updates(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Generator[None]:
    """Answer the update entity's route check locally.

    Every poll of a loaded panel asks whether it can install its own updates;
    unanswered, that opens a real socket, and then an ADB probe, to a panel
    that is not there.
    """
    if request.path.name not in _UPDATE_ROUTE_TESTS:
        monkeypatch.setattr(
            HaPaneldClient,
            "async_get_legacy_install_capability",
            AsyncMock(return_value=True),
        )
    yield


@pytest.fixture
def mqtt_era_panel() -> Generator[None]:
    """The panel runs an ha-paneld from before the MQTT withdrawal.

    Only such a panel stays on MQTT: Panel Assistant moves a newer one to its
    own connection as soon as it learns its version.
    """
    from dataclasses import replace
    from unittest.mock import patch

    from . import test_cutover, test_native, test_transport

    HEALTH = test_transport.HEALTH
    app = {"version": "0.9.7", "version_code": 790}
    with (
        patch.object(test_native, "HEALTH", replace(HEALTH, version="0.9.7")),
        patch.object(test_transport, "HEALTH", replace(HEALTH, version="0.9.7")),
        patch.dict(test_cutover._HELLO_TAIL, {"app": app}),
        patch.dict(test_transport.HELLO, {"app": app}),
    ):
        yield

"""ADB the panel app keeps off for this Home Assistant while no session runs.

The app renews its lease for as long as it runs, so a panel that still answers
on its own port without a session will not open ADB by waiting. Each flow that
needs ADB then stops at once and names the lease and the ways back; a panel
whose app is gone, or reports it no longer renews, still waits for the lapse.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import (
    config_flow,
    local_adb,
    removal,
    update_panel_install,
)
from custom_components.panel_assistant.adb_protocol import (
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.release import ReleaseArtifact
from custom_components.panel_assistant.status import parse_status_response
from custom_components.panel_assistant.transport_sessions import (
    PanelSession,
    async_get_sessions,
)

from .test_removal import FakePanel, _open_form, panel  # noqa: F401

DID = "a" * 64
IDENTITY = ("SERIAL-1", "Test Panel", "arm64-v8a", 34)

ABSENT: dict[str, Any] = {}
MANAGED = {"network_adb": {"managed": True, "owner_switch_wait_s": None}}
SWITCH_NOW = {"network_adb": {"managed": True, "owner_switch_wait_s": 0}}
SWITCH_LATER = {"network_adb": {"managed": True, "owner_switch_wait_s": 230}}
NOT_RENEWING = {"network_adb": {"managed": False, "owner_switch_wait_s": None}}


class AppPort:
    """The app's own port: what it reports, and every read made of it."""

    def __init__(self, report: dict[str, Any] | None) -> None:
        self.report = report
        self.reads: list[Any] = []

    def client(self, _session: Any, address: Any) -> Any:
        async def status(**_kwargs: Any) -> Any:
            self.reads.append(address)
            if self.report is None:
                raise CannotConnectError
            return parse_status_response(
                json.dumps({"warnings": [], "capabilities": [], **self.report})
            )

        return SimpleNamespace(async_get_status=status)


def _app_port(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any] | None):
    port = AppPort(report)
    # Older trees never read the app's port here; absent, nothing is patched.
    monkeypatch.setattr(local_adb, "HaPaneldClient", port.client, raising=False)
    return port


def _lost_session(hass: HomeAssistant, entry: Any) -> None:
    """A session that granted local ADB, then lost its connection."""
    sessions = async_get_sessions(hass)
    session = PanelSession(
        entry.entry_id,
        DID,
        "lost-session",
        MagicMock(),
        1,
        "user",
        5,
        "1.0.2",
        1,
        "c" * 64,
        frozenset(),
        {},
        dt_util.utcnow(),
        local_adb_identity=IDENTITY,
    )
    sessions.open(session)
    sessions.close(session, "connection_lost")
    assert sessions.get(entry.entry_id) is None


# Removal -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("history", "report", "error", "placeholders"),
    [
        (True, ABSENT, "removal_adb_lease", {}),
        (True, MANAGED, "removal_adb_lease", {}),
        (False, SWITCH_NOW, "removal_adb_lease_switch_now", {}),
        (False, SWITCH_LATER, "removal_adb_lease_switch", {"minutes": "4"}),
    ],
    ids=["released-app", "managed", "switch-now", "switch-later"],
)
async def test_removal_without_a_session_names_the_lease_and_the_ways_back(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    history: bool,
    report: dict[str, Any],
    error: str,
    placeholders: dict[str, str],
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    if history:
        _lost_session(hass, entry)
    port = _app_port(monkeypatch, report)
    monkeypatch.setattr(
        removal,
        "async_probe_install_target",
        AsyncMock(return_value=InstallTargetProbe(InstallTargetState.ADB_UNREACHABLE)),
    )
    form = await _open_form(hass, entry)

    with caplog.at_level(logging.WARNING):
        result = await hass.config_entries.options.async_configure(form["flow_id"], {})

    assert result["errors"] == {"base": error}
    for name, value in placeholders.items():
        assert result["description_placeholders"][name] == value
    assert [address.port for address in port.reads] == [8888]
    assert "keeps off while this Home Assistant manages it" in caplog.text
    assert fake.installed and fake.hand_back_requests == 0


@pytest.mark.parametrize(
    ("history", "report"),
    [(True, None), (True, NOT_RENEWING), (False, ABSENT)],
    ids=["app-gone", "not-renewing", "never-managed"],
)
async def test_removal_keeps_the_generic_cause_where_adb_is_not_our_lease(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    history: bool,
    report: dict[str, Any] | None,
) -> None:
    _fake, server = panel
    entry = _entry(hass, server)
    if history:
        _lost_session(hass, entry)
    _app_port(monkeypatch, report)
    monkeypatch.setattr(
        removal,
        "async_probe_install_target",
        AsyncMock(return_value=InstallTargetProbe(InstallTargetState.ADB_UNREACHABLE)),
    )
    form = await _open_form(hass, entry)

    result = await hass.config_entries.options.async_configure(form["flow_id"], {})

    assert result["errors"] == {"base": "removal_adb_unreachable"}


def _entry(hass: HomeAssistant, server: TestServer) -> Any:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test display",
        unique_id=DID,
        data={CONF_ADDRESS: f"127.0.0.1:{server.port}"},
    )
    entry.add_to_hass(hass)
    return entry


# Permission repair after an update -----------------------------------------


def _artifact() -> ReleaseArtifact:
    return ReleaseArtifact(
        "v1.0.0",
        "1.0.0",
        "app.apk",
        "",
        "",
        descriptor=SimpleNamespace(
            package_id="io.panelassistant.android", version_code=1100
        ),
    )


@pytest.mark.parametrize(
    ("report", "attempts"),
    [(MANAGED, 1), (ABSENT, 12), (NOT_RENEWING, 12), (None, 12)],
    # A restarted app that does not report the lease may still reconnect.
    ids=["managed", "released-app", "not-renewing", "app-gone"],
)
async def test_permission_repair_stops_retrying_adb_the_app_keeps_off(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, Any] | None,
    attempts: int,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DID, data={CONF_ADDRESS: "192.168.1.30"}
    )
    entry.add_to_hass(hass)
    _lost_session(hass, entry)
    port = _app_port(monkeypatch, report)
    artifact = _artifact()
    coordinator = SimpleNamespace(
        _entry=lambda: entry,
        available=True,
        identity_mismatch=False,
        data=SimpleNamespace(
            health=SimpleNamespace(
                version=artifact.version, package="io.panelassistant.android"
            )
        ),
        client=SimpleNamespace(address=SimpleNamespace(host="192.168.1.30", port=8888)),
    )
    monkeypatch.setattr(update_panel_install, "_PERMISSION_REPAIR_RETRY_SECONDS", 0)
    monkeypatch.setattr(
        update_panel_install,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=SimpleNamespace(signer=object())),
    )
    admit = AsyncMock(
        side_effect=InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
    )

    await update_panel_install.async_repair_update_permissions(
        hass, coordinator, artifact, admit_adb_target=admit, panel_name=lambda: "p"
    )

    assert admit.await_count == attempts
    assert port.reads


# Allowing updates from a Repair or the options -------------------------------


@pytest.mark.parametrize(
    ("report", "error"),
    [
        (SWITCH_LATER, "adb_lease_switch"),
        (ABSENT, "adb_lease"),
        (NOT_RENEWING, "adb_unreachable"),
        (None, "adb_unreachable"),
    ],
    ids=["switch-later", "released-app", "not-renewing", "app-gone"],
)
async def test_allowing_updates_names_the_lease_instead_of_turning_adb_on(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, Any] | None,
    error: str,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DID, data={CONF_ADDRESS: "192.168.1.30"}
    )
    entry.add_to_hass(hass)
    _lost_session(hass, entry)
    port = _app_port(monkeypatch, report)
    health = config_flow.PanelHealth.__new__(config_flow.PanelHealth)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(data=SimpleNamespace(health=health))
    )
    pinned = SimpleNamespace(host="192.168.1.30", port=8888)
    monkeypatch.setattr(
        config_flow,
        "async_pin_entry_target",
        AsyncMock(return_value=SimpleNamespace(pinned=pinned)),
    )
    monkeypatch.setattr(config_flow, "async_revalidate_install_target", AsyncMock())
    monkeypatch.setattr(config_flow, "async_get_adb_signer", AsyncMock())
    monkeypatch.setattr(
        config_flow,
        "async_wait_for_adb_allow",
        AsyncMock(return_value=InstallTargetProbe(InstallTargetState.ADB_UNREACHABLE)),
    )

    assert await config_flow.async_authorize_existing_panel_adb(hass, entry) == error
    assert port.reads == [pinned]

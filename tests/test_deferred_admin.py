"""Deferred admin activation through normal coordinator refresh and Android shell."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import (
    adb_credentials,
    adb_protocol,
    provisioning,
)
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.client import (
    HaPaneldClient,
    PanelHealth,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import HaPaneldDataUpdateCoordinator
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelStatus

from .install_adb_fakes import _identity_root_output

DID = "d" * 64
PACKAGE = "io.panelassistant.android"


@pytest.fixture
async def panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Any:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Panel", data={CONF_ADDRESS: "192.168.1.23"}, unique_id=DID
    )
    entry.add_to_hass(hass)
    state = SimpleNamespace(
        health=PanelHealth(
            "1.0.0",
            "panel",
            "1",
            "cfg",
            discovery_id=DID,
            package=PACKAGE,
            version_code=1,
        ),
        connections=0,
        closes=0,
        refused=False,
        serial="SERIAL-1",
        now=100.0,
        active_install=False,
        change_entry=False,
    )

    async def health(_client: HaPaneldClient) -> PanelHealth:
        return state.health

    monkeypatch.setattr(HaPaneldClient, "async_get_health", health)
    monkeypatch.setattr(
        HaPaneldClient, "async_get_status", AsyncMock(return_value=PanelStatus(0, 0))
    )
    monkeypatch.setattr(
        adb_credentials,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=AdbCredential(object.__new__(PythonRSASigner), "key")),
    )

    async def probe(*_args: Any) -> InstallTargetProbe:
        if state.change_entry:
            hass.config_entries.async_update_entry(
                entry, data={CONF_ADDRESS: "192.168.1.24"}
            )
        return InstallTargetProbe(
            InstallTargetState.INSTALLED, "Test Panel", "SERIAL-1", "arm64-v8a", 34
        )

    monkeypatch.setattr(provisioning, "async_probe_install_target", probe)
    from custom_components.panel_assistant import install_jobs

    monkeypatch.setattr(
        install_jobs,
        "async_get_install_job_manager",
        AsyncMock(
            return_value=SimpleNamespace(
                async_find_active=AsyncMock(
                    side_effect=lambda *_args: (
                        object() if state.active_install else None
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(
        "custom_components.panel_assistant.coordinator.monotonic", lambda: state.now
    )

    class Device:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def connect(self, **_kwargs: Any) -> bool:
            state.connections += 1
            return True

        async def close(self) -> None:
            state.closes += 1

        async def streaming_shell(self, command: str, **_kwargs: Any) -> Any:
            match = re.search(r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", command)
            assert match is not None
            if "HAPANELD_POSTURE_BEGIN" in command:
                yield _identity_root_output(match[1], serial=state.serial)
                return
            program = (
                r"""
dumpsys() {
  if [ -f "$STATE/admin" ]; then
    echo '    io.panelassistant.android/.platform.PanelDeviceAdminReceiver:'
  fi
}
dpm() { echo "$*" >> "$STATE/calls"; [ "$REFUSE" = 1 ] || touch "$STATE/admin"; }
"""
                + command
            )
            process = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                program,
                env={
                    **os.environ,
                    "STATE": str(tmp_path),
                    "REFUSE": str(int(state.refused)),
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            output, error = await process.communicate()
            assert process.returncode == 0, error
            yield output

    monkeypatch.setattr(adb_protocol, "AdbDeviceAsync", Device)
    client = HaPaneldClient(MagicMock(), normalize_address(entry.data[CONF_ADDRESS]))
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)
    return SimpleNamespace(
        state=state, entry=entry, coordinator=coordinator, directory=tmp_path
    )


async def test_setup_completion_activates_admin_once_and_closes_adb(panel: Any) -> None:
    await panel.coordinator.async_refresh()
    assert panel.state.connections == 0
    panel.state.health = replace(panel.state.health, setup_complete=True)
    await panel.coordinator.async_refresh()
    await panel.coordinator.async_refresh()
    assert panel.coordinator.last_update_success
    assert (panel.directory / "admin").exists()
    assert (panel.directory / "calls").read_text().splitlines() == [
        f"set-active-admin {PACKAGE}/{PACKAGE}.platform.PanelDeviceAdminReceiver"
    ]
    assert panel.state.connections == panel.state.closes == 1


async def test_existing_admin_needs_readback_without_another_claim(panel: Any) -> None:
    (panel.directory / "admin").touch()
    panel.state.health = replace(panel.state.health, setup_complete=True)
    await panel.coordinator.async_refresh()
    await panel.coordinator.async_refresh()
    assert not (panel.directory / "calls").exists()
    assert panel.state.connections == panel.state.closes == 1


async def test_admin_refusal_retries_are_spaced_and_connections_close(
    panel: Any,
) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    panel.state.refused = True
    for _ in range(5):
        await panel.coordinator.async_refresh()
    assert panel.state.connections == 1
    for _ in range(5):
        panel.state.now += 301
        await panel.coordinator.async_refresh()
    assert panel.state.connections == panel.state.closes == 6
    assert not (panel.directory / "admin").exists()
    assert panel.coordinator.last_update_success


async def test_transient_admin_refusal_converges_on_later_poll(panel: Any) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    panel.state.refused = True
    await panel.coordinator.async_refresh()
    panel.state.refused = False
    panel.state.now += 301
    await panel.coordinator.async_refresh()
    await panel.coordinator.async_refresh()
    assert (panel.directory / "admin").exists()
    assert panel.state.connections == panel.state.closes == 2


@pytest.mark.parametrize(
    "guard", ["panel_busy", "install_busy", "changed_entry", "wrong_serial"]
)
async def test_admin_never_races_another_panel_operation_or_changed_identity(
    panel: Any, hass: HomeAssistant, guard: str
) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    if guard == "panel_busy":
        assert claim_panel_operation(hass, panel.entry.entry_id)
    elif guard == "install_busy":
        panel.state.active_install = True
    elif guard == "changed_entry":
        panel.state.change_entry = True
    else:
        panel.state.serial = "OTHER-PANEL"
    try:
        await panel.coordinator.async_refresh()
    finally:
        release_panel_operation(hass, panel.entry.entry_id)
    assert not (panel.directory / "admin").exists()
    assert not (panel.directory / "calls").exists()
    assert panel.coordinator.last_update_success

"""Shelly recovery through the coordinator's real session-change callback."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import DeviceAuthError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import adb_protocol, vendor_recovery
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.client import (
    CannotConnectError,
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
from custom_components.panel_assistant.status import PanelDevice, PanelStatus
from custom_components.panel_assistant.transport_sessions import (
    PanelSession,
    async_get_sessions,
)

from .install_adb_fakes import _identity_root_output, _single_output

DID = "d" * 64
PACKAGE = "io.panelassistant.android"
HOME = f"{PACKAGE}/.DashboardActivity"
BOOT = "fb44645c-76bf-4550-a94e-b510ef9e8c67"


@pytest.fixture
async def panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Any:
    """A healthy configured panel and authenticated ADB, with real shell parsers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Panel",
        data={CONF_ADDRESS: "192.168.1.23"},
        unique_id=DID,
    )
    entry.add_to_hass(hass)
    health = PanelHealth(
        "1.0.0", "panel", "1", "cfg", discovery_id=DID, package=PACKAGE, version_code=1
    )
    status = PanelStatus(
        0,
        0,
        panel_assistant_device=PanelDevice(
            manufacturer="Shelly", model="Wall Display X2i"
        ),
    )
    state = SimpleNamespace(
        online=True,
        stopped=False,
        home=HOME,
        boot=BOOT,
        now=100.0,
        vendor_pid=1371,
        serial="SERIAL-1",
        credential="host-key",
        authorized=True,
        commands=[],
        homes=0,
        logs=[
            "  90.000  475  500 I ActivityManager: "
            "Start proc 1371:cloud.shelly.stargate/1000 for broadcast {native}"
        ],
        block=None,
        drop_now=102.0,
        stop_time=101.0,
        active_install=False,
        reading=asyncio.Event(),
        final_home=None,
        final_stop=None,
        malformed=False,
        real_format=False,
    )

    async def get_health(_client: HaPaneldClient) -> PanelHealth:
        if not state.online:
            raise CannotConnectError
        return health

    monkeypatch.setattr(HaPaneldClient, "async_get_health", get_health)
    monkeypatch.setattr(
        HaPaneldClient, "async_get_status", AsyncMock(return_value=status)
    )
    signer = object.__new__(PythonRSASigner)

    async def credential(_hass: HomeAssistant) -> AdbCredential:
        return AdbCredential(signer, state.credential)

    monkeypatch.setattr(vendor_recovery, "async_get_durable_adb_credential", credential)
    monkeypatch.setattr(
        vendor_recovery,
        "async_probe_install_target",
        AsyncMock(
            return_value=InstallTargetProbe(
                InstallTargetState.INSTALLED, "Test Panel", "SERIAL-1", "arm64-v8a", 34
            )
        ),
    )
    monkeypatch.setattr(vendor_recovery, "_GRACE_SECONDS", 0)
    monkeypatch.setattr(
        vendor_recovery,
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

    async def shell(command: str) -> bytes:
        # Execute production observation/navigation programs against platform stubs.
        bins = tmp_path / "bin"
        bins.mkdir(exist_ok=True)
        effects = tmp_path / "home-effects"
        users = (
            [
                "    User 0: ceDataInode=1957 installed=true hidden=false "
                "suspended=false distractionFlags=0 stopped=false notLaunched=false "
                "enabled=0 instant=false virtual=false",
                "    User 0:",
                "  User 0:",
            ]
            if state.real_format
            else ["  User 0: installed=true stopped=false"]
        )
        users = [
            line.replace("stopped=false", "stopped=" + str(state.stopped).lower())
            for line in users
        ]
        scripts = {
            "cat": "echo " + shlex.quote(state.boot),
            "cut": "echo " + str(state.now),
            "pidof": "echo " + str(state.vendor_pid),
            "cmd": "printf '%s\\n' " + shlex.quote(state.final_home or state.home),
            "dumpsys": "printf '%s\\n' "
            + " ".join(shlex.quote(line) for line in users),
            "logcat": "printf '%s\\n' "
            + " ".join(
                shlex.quote(line.replace("101.000", "101.087"))
                for line in [
                    *state.logs,
                    *([state.final_stop] if state.final_stop else []),
                ]
            ),
            "input": "echo HOME >> " + shlex.quote(str(effects)),
        }
        for name, script in scripts.items():
            path = bins / name
            path.write_text("#!/bin/sh\n" + script + "\n")
            path.chmod(0o700)
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            env={**os.environ, "PATH": str(bins) + ":/usr/bin:/bin"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await process.communicate()
        assert not error
        state.homes = len(effects.read_text().splitlines()) if effects.exists() else 0
        if state.homes:
            state.stopped = False
            state.online = True
        return output

    class Device:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def connect(self, **kwargs: Any) -> bool:
            if not state.authorized:
                kwargs["auth_callback"](self)
                raise DeviceAuthError
            return True

        async def close(self) -> None:
            pass

        async def streaming_shell(self, command: str, **_kwargs: Any) -> Any:
            state.commands.append(command)
            nonce = re.search(r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", command)[1]
            if "HAPANELD_POSTURE_BEGIN" in command:
                yield _identity_root_output(nonce, serial=state.serial)
            elif "HAPANELD_RECOVERY_BEGIN" in command:
                if state.block is not None and "logcat" in command:
                    state.reading.set()
                    await state.block.wait()
                if state.real_format:
                    yield await shell(command)
                    return
                lines = [
                    state.boot,
                    f"{state.now:.2f}",
                    state.home,
                    str(state.vendor_pid),
                    f"  User 0: installed=true stopped={str(state.stopped).lower()}",
                ]
                if "logcat" in command:
                    lines.extend(state.logs)
                    lines.append(f"{state.now:.2f}")
                yield _single_output(
                    "RECOVERY", nonce, [] if state.malformed else lines, 0
                )
            else:
                assert "input keyevent 3" in command
                yield await shell(command)

    monkeypatch.setattr(adb_protocol, "AdbDeviceAsync", Device)
    client = HaPaneldClient(MagicMock(), normalize_address(entry.data[CONF_ADDRESS]))
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)
    entry.runtime_data = SimpleNamespace(
        coordinator=coordinator,
        update_coordinator=SimpleNamespace(data=SimpleNamespace(operation=None)),
    )
    connection = SimpleNamespace(subscriptions={}, send_message=MagicMock())
    session = PanelSession(
        entry.entry_id,
        DID,
        "session-one",
        connection,
        1,
        "user",
        5,
        "1.0.0",
        1,
        "c" * 64,
        frozenset(),
        {},
        dt_util.utcnow(),
    )
    stop = coordinator.async_follow_session()
    sessions = async_get_sessions(hass)
    sessions.open(session)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    async def shutdown(actor: int = 1371) -> None:
        state.online = False
        state.stopped = True
        state.now = state.drop_now
        state.logs.append(
            f"  {state.stop_time:.3f}  475  1207 I ActivityManager: "
            f"Force stopping {PACKAGE} appid=10115 user=0: from pid {actor}"
        )
        sessions.close(session, "test socket ended")
        await hass.async_block_till_done()

    yield SimpleNamespace(
        state=state,
        coordinator=coordinator,
        entry=entry,
        session=session,
        sessions=sessions,
        shutdown=shutdown,
        stop=stop,
        health=health,
    )
    stop()
    sessions.clear_restart_notice(entry.entry_id)
    release_panel_operation(hass, entry.entry_id)
    await coordinator.async_shutdown()
    await hass.async_block_till_done()


@pytest.mark.parametrize("vendor_present", [True, False])
async def test_vendor_shutdown_returns_home_once_and_health_answers(
    panel: Any, vendor_present: bool
) -> None:
    if not vendor_present:
        panel.state.vendor_pid = 0
    await panel.shutdown()
    assert panel.state.homes == 1
    assert panel.coordinator.reachable
    assert not panel.state.stopped
    assert not any(
        "pm grant" in c or "set-home-activity" in c or " install " in c
        for c in panel.state.commands
    )
    # Repeating state changes after the witness was consumed never sends HOME again.
    panel.sessions.close_entry(panel.entry.entry_id, "repeat")
    await panel.coordinator.async_refresh()
    assert panel.state.homes == 1


@pytest.mark.parametrize(
    "reason",
    [
        "settings",
        "wrong_home",
        "healthy",
        "address",
        "did",
        "credential",
        "unload",
        "restart",
        "operation",
        "busy",
        "no_adb",
        "boot",
        "old",
        "malformed",
        "old_logs",
        "first_install",
        "wrong_serial",
        "final_home",
        "final_settings_stop",
        "unknown_actor",
        "recycled_actor",
        "truncated",
    ],
)
async def test_only_a_current_vendor_stop_is_recovered(
    panel: Any, hass: HomeAssistant, reason: str
) -> None:
    state = panel.state
    if reason == "wrong_home":
        state.home = f"{PACKAGE}/.AdminLauncherActivity"
    elif reason == "address":
        hass.config_entries.async_update_entry(
            panel.entry, data={CONF_ADDRESS: "192.168.1.24"}
        )
    elif reason == "did":
        hass.config_entries.async_update_entry(panel.entry, unique_id="e" * 64)
    elif reason == "credential":
        state.credential = "replacement"
    elif reason == "unload":
        panel.stop()
    elif reason == "restart":
        panel.sessions.set_restart_notice(panel.entry.entry_id, "app", "settings", 5000)
    elif reason == "operation":
        panel.entry.runtime_data.update_coordinator.data.operation = SimpleNamespace(
            running=True
        )
    elif reason == "busy":
        assert claim_panel_operation(hass, panel.entry.entry_id)
    elif reason == "no_adb":
        state.authorized = False
    elif reason == "boot":
        state.boot = "fb44645c-76bf-4550-a94e-b510ef9e8c68"
    elif reason == "truncated":
        state.logs = []
    elif reason == "recycled_actor":
        state.logs.append(
            "  100.500  475  500 I ActivityManager: "
            "Start proc 1371:com.android.settings/1000 for top {Settings}"
        )
    elif reason == "malformed":
        state.malformed = True
    elif reason == "old":
        state.drop_now = 120.0
    elif reason == "old_logs":
        state.stop_time = 99.0
    elif reason == "first_install":
        state.active_install = True
    elif reason == "wrong_serial":
        state.serial = "OTHER-PANEL"
    elif reason == "settings":
        state.logs.append(
            "  100.500  475  500 I ActivityManager: "
            "Start proc 1234:com.android.settings/1000 for top {Settings}"
        )
    elif reason == "final_home":
        state.final_home = f"{PACKAGE}/.AdminLauncherActivity"
    elif reason == "final_settings_stop":
        state.final_stop = (
            f"  102.000  475  1207 I ActivityManager: Force stopping {PACKAGE} "
            "appid=10115 user=0: from pid 1234"
        )
    if reason == "healthy":
        state.online = True
        panel.sessions.close(panel.session, "socket only")
        await hass.async_block_till_done()
    else:
        await panel.shutdown(
            actor=1234 if reason in {"settings", "unknown_actor"} else 1371
        )
    assert state.homes == 0


@pytest.mark.parametrize(
    "change", ["address", "did", "credential", "unload", "reconnect"]
)
async def test_state_change_during_adb_read_retires_navigation(
    panel: Any, hass: HomeAssistant, change: str
) -> None:
    state = panel.state
    state.block = asyncio.Event()
    state.online = False
    state.stopped = True
    state.now = 102.0
    state.logs.append(
        f"  101.000  475  1207 I ActivityManager: Force stopping {PACKAGE} "
        "appid=10115 user=0: from pid 1371"
    )
    panel.sessions.close(panel.session, "socket ended")
    await asyncio.wait_for(state.reading.wait(), 1)
    if change == "address":
        hass.config_entries.async_update_entry(
            panel.entry, data={CONF_ADDRESS: "192.168.1.24"}
        )
    elif change == "did":
        hass.config_entries.async_update_entry(panel.entry, unique_id="e" * 64)
    elif change == "credential":
        state.credential = "replacement"
    elif change == "unload":
        panel.stop()
    else:
        panel.sessions.open(replace(panel.session, token="session-two", closed_at=None))
    state.block.set()
    await hass.async_block_till_done()
    assert state.homes == 0


async def test_vendor_shutdown_with_real_android_user_sections_and_old_negative_log(
    panel: Any,
) -> None:
    # Rootless Android 11 emits several User 0 headings, and historical logcat
    # records can become negative after the boot clock was corrected.
    panel.state.real_format = True
    panel.state.logs.insert(
        0,
        " -2851.244   475   500 I ActivityManager: "
        "Start proc 618:com.android.bluetooth/1002 for service "
        "{com.android.bluetooth/com.android.bluetooth.btservice.AdapterService}",
    )
    await panel.shutdown()
    assert panel.state.homes == 1
    assert panel.coordinator.reachable

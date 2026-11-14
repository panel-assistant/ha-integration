"""When the device setup pass runs: only inside an operation the owner started.

The device admin claim, the vendor setup and the pending home-screen claim
change panel settings. The poll, a panel's session connecting and entry setup
after a Home Assistant restart must send none of them, whatever the entry
records; an install, an update and a confirmed Repair run the pass once and
record it. The shell stand-in is the one test_deferred_admin.py uses, and the
same recorder sees the commands in both directions.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import (
    adb_credentials,
    adb_protocol,
    device_setup,
    local_adb,
)
from custom_components.panel_assistant.adb_credentials import AdbCredentialError
from custom_components.panel_assistant.adb_protocol import AdbInstallTarget
from custom_components.panel_assistant.const import (
    CONF_DEVICE_SETUP,
    CONF_DEVICE_SETUP_OWED,
    DEVICE_SETUP_ADOPT,
    DOMAIN,
)
from custom_components.panel_assistant.repairs import async_create_fix_flow

from .install_adb_fakes import (
    _identity_root_output,
    _identity_sections,
    _single_output,
)
from .test_deferred_admin import DID, PACKAGE, panel  # noqa: F401
from .test_management import MANAGED, STATUS_BODY, _connect
from .test_transport import WsClientFactory, _receive

# What changes a panel setting, as the pass's programs spell it.
STATE_CHANGING = re.compile(
    r"dpm |settings put|pm grant|appops set|set-home-activity|input (tap|keyevent)"
    r"|HAPANELD_(VENDOR|PERMISSIONS|NOTIFICATIONS)_BEGIN"
)
RECORDED = {"did": DID, "package": PACKAGE, "version_code": 1, "root": False}
# The entry and panel each trigger meets: no record (a panel added without an
# install), an entry adopted from before the record existed, a recorded build
# whose home-screen request is pending again, and a build updated outside
# Home Assistant since its record.
STATES = ["no_record", "adopted", "home_pending", "updated_elsewhere"]


def _changing(commands: list[str]) -> list[str]:
    return [command for command in commands if STATE_CHANGING.search(command)]


@pytest.fixture
def sent(panel: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:  # noqa: F811
    """Every shell command the stand-in panel receives, on any route."""
    commands: list[str] = []
    device = adb_protocol.AdbDeviceAsync
    original = device.streaming_shell

    async def recording(self: Any, command: str, **kwargs: Any) -> Any:
        commands.append(command)
        async for chunk in original(self, command, **kwargs):
            yield chunk

    monkeypatch.setattr(device, "streaming_shell", recording)
    return commands


def _prepare(panel: Any, state: str) -> None:  # noqa: F811
    """Put the entry and the panel into one of STATES, setup finished."""
    data: dict[str, Any] = {
        CONF_ADDRESS: panel.entry.data[CONF_ADDRESS],
        "installation_identity": True,
    }
    health = replace(
        panel.state.health,
        setup_complete=True,
        installation_identity=True,
        protocol=(5, 5),
    )
    if state == "adopted":
        data[CONF_DEVICE_SETUP] = DEVICE_SETUP_ADOPT
        health = replace(health, home_pending=True)
    elif state == "home_pending":
        data[CONF_DEVICE_SETUP] = RECORDED
        health = replace(health, home_pending=True)
    elif state == "updated_elsewhere":
        data[CONF_DEVICE_SETUP] = RECORDED
        health = replace(health, version="1.0.1", version_code=2)
    panel.hass.config_entries.async_update_entry(
        panel.entry, data=data, minor_version=3
    )
    panel.state.health = health
    # A vendor launcher holds HOME, so a HOME claim would have work to do.
    (panel.directory / "home").write_text("com.vendor.launcher/.Home\n")


@pytest.mark.parametrize("state", STATES)
async def test_polls_send_no_setup_command(
    panel: Any,  # noqa: F811
    sent: list[str],
    state: str,
) -> None:
    _prepare(panel, state)
    for _ in range(3):
        # Past any retry spacing the poll ever kept.
        panel.state.now += 3601
        await panel.coordinator.async_refresh()
        assert panel.coordinator.last_update_success
    assert _changing(sent) == []
    assert panel.state.connections == 0
    assert not (panel.directory / "calls").exists()
    assert CONF_DEVICE_SETUP_OWED not in panel.entry.data


async def _set_up(hass: HomeAssistant, entry: Any) -> None:
    """Entry setup as after a Home Assistant restart, from the stored entry."""
    executor = SimpleNamespace(async_reconcile_entry=AsyncMock(return_value=False))
    with (
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("state", STATES)
async def test_entry_setup_after_a_restart_sends_no_setup_command(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    sent: list[str],
    state: str,
) -> None:
    _prepare(panel, state)
    await _set_up(hass, panel.entry)
    coordinator = panel.entry.runtime_data.coordinator
    for _ in range(2):
        panel.state.now += 3601
        await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _changing(sent) == []
    assert panel.state.connections == 0
    assert not (panel.directory / "calls").exists()
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, device_setup.device_setup_issue_id(panel.entry.entry_id)
        )
        is None
    )
    await hass.config_entries.async_unload(panel.entry.entry_id)


async def test_a_pass_a_restart_interrupted_becomes_a_repair_not_a_command(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    sent: list[str],
) -> None:
    """An install owed the pass when Home Assistant stopped: the owner confirms."""
    _prepare(panel, "no_record")
    hass.config_entries.async_update_entry(
        panel.entry,
        data={**panel.entry.data, CONF_DEVICE_SETUP_OWED: device_setup.OWED_INSTALL},
    )
    await _set_up(hass, panel.entry)
    panel.state.now += 3601
    await panel.entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sent == []
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, device_setup.device_setup_issue_id(panel.entry.entry_id)
    )
    assert issue is not None and issue.is_fixable
    assert issue.translation_key == "device_setup_unfinished"
    await hass.config_entries.async_unload(panel.entry.entry_id)


SESSION_HEALTH = (
    f"ha-paneld 1.0.0 panel=alpha build=2000 cfg=1a2b3c4d did={DID}"
    f" identity=install vc={{vc}} proto=5-5 pkg={PACKAGE} setup_complete=1"
    " home_pending={pending}\n"
)


def _shell_answer(command: str) -> str | None:
    """Answer the read-only steps a local route makes; refuse everything else."""
    match = re.search(r"HAPANELD_(\w+?)_BEGIN:([a-f0-9]{32})", command)
    if match is None:
        return None
    prefix, nonce = match[1], match[2]
    if prefix == "ID":
        return (
            "\n".join(
                [
                    f"HAPANELD_ID_BEGIN:{nonce}",
                    *_identity_sections("ID", nonce),
                    f"HAPANELD_ID_END:{nonce}",
                ]
            )
            + "\n"
        )
    if prefix == "POSTURE":
        return _identity_root_output(nonce, uid="0").decode()
    if prefix == "PACKAGE":
        # Read-only: the app is installed, so a pass would go on to its grants.
        return _single_output(
            "PACKAGE", nonce, ["package:/data/app/panel/base.apk"], 0
        ).decode()
    return None


@pytest.mark.parametrize("state", STATES)
async def test_a_new_local_session_sends_no_setup_command(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    panel: Any,  # noqa: F811
    sent: list[str],
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """The panel connects with its local ADB channel; polls then run over it."""
    import base64

    _prepare(panel, state)
    # No network route at all, so only the session's own channel could be used.
    monkeypatch.setattr(
        adb_credentials,
        "async_get_durable_adb_credential",
        AsyncMock(side_effect=AdbCredentialError),
    )
    await _set_up(hass, panel.entry)
    session_shells: list[str] = []
    snapshots: list[str] = []
    connected = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, [*MANAGED, "local_adb"]
    )
    health = panel.state.health

    async def respond() -> None:
        while True:
            event = (await _receive(connected.client))["event"]
            if event.get("kind") != "manage":
                continue
            op = event["op"]
            if op == "snapshot":
                snapshots.append(op)
                line = SESSION_HEALTH.format(
                    vc=health.version_code, pending=int(health.home_pending)
                )
                result: dict[str, Any] = {"health": line, "status": STATUS_BODY}
            elif op == "adb.ready":
                result = {"ready": True, "managed": True, "recovery_hold": False}
            elif op == "adb.shell" and (
                answer := _shell_answer(event["args"]["command"])
            ):
                session_shells.append(event["args"]["command"])
                result = {
                    "stdout_b64": base64.b64encode(answer.encode()).decode(),
                    "exit_code": 0,
                    "timed_out": False,
                    "truncated": False,
                }
            else:
                if op == "adb.shell":
                    session_shells.append(event["args"]["command"])
                await connected.answer(event, "refused", code="unknown_command")
                continue
            await connected.answer(event, "applied", result=result)

    responder = asyncio.create_task(respond())
    coordinator = panel.entry.runtime_data.coordinator
    try:
        await hass.async_block_till_done()
        for _ in range(3):
            panel.state.now += 3601
            await asyncio.wait_for(coordinator.async_refresh(), 10)
        await hass.async_block_till_done(wait_background_tasks=True)
    finally:
        responder.cancel()
    # The polls did run over the panel's session.
    assert len(snapshots) >= 3
    assert coordinator.connected
    assert _changing(session_shells) == []
    assert _changing(sent) == []
    assert panel.state.connections == 0
    await hass.config_entries.async_unload(panel.entry.entry_id)


async def test_the_poll_keeps_only_a_rooted_panels_recovery_restart(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    sent: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded rooted pass gets its ADB recovery started once per session."""
    _prepare(panel, "no_record")
    hass.config_entries.async_update_entry(
        panel.entry,
        data={**panel.entry.data, CONF_DEVICE_SETUP: {**RECORDED, "root": True}},
    )
    sessions = [object(), object()]
    current = [sessions[0]]
    monkeypatch.setattr(local_adb, "session_for", lambda *_args: current[0])
    recovery = AsyncMock(return_value=True)
    monkeypatch.setattr(local_adb, "async_ensure_recovery", recovery)
    for _ in range(3):
        panel.state.now += 3601
        await panel.coordinator.async_refresh()
    assert recovery.await_count == 1
    current[0] = sessions[1]
    panel.state.now += 3601
    await panel.coordinator.async_refresh()
    assert recovery.await_count == 2
    assert sent == []


def _owed(entry: Any) -> str | None:
    return entry.data.get(CONF_DEVICE_SETUP_OWED)


def _issue(hass: HomeAssistant, entry: Any) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, device_setup.device_setup_issue_id(entry.entry_id)
    )


async def test_an_operations_pass_runs_once_and_records_the_build(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    sent: list[str],
) -> None:
    """What an install or update starts: one admin claim, one vendor pass."""
    _prepare(panel, "no_record")
    panel.state.health = replace(panel.state.health, home_pending=False)
    (panel.directory / "home").write_text(f"{PACKAGE}/.DashboardActivity\n")
    panel.entry.runtime_data = SimpleNamespace(coordinator=panel.coordinator)
    # Entry setup has read the panel once, as it does before any operation.
    await panel.coordinator.async_refresh()
    device_setup.async_request(hass, panel.entry, device_setup.OWED_INSTALL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (panel.directory / "calls").read_text().splitlines() == [
        f"set-active-admin {PACKAGE}/{PACKAGE}.platform.PanelDeviceAdminReceiver"
    ]
    assert panel.state.vendor_passes == 1
    assert panel.entry.data[CONF_DEVICE_SETUP] == RECORDED
    assert _owed(panel.entry) is None
    assert _issue(hass, panel.entry) is None
    # Later polls never run it again, even with the admin taken away.
    (panel.directory / "admin").unlink()
    count = len(sent)
    for _ in range(2):
        panel.state.now += 3601
        await panel.coordinator.async_refresh()
    assert len(sent) == count


async def test_a_panel_never_ready_gets_one_repair_after_the_bounded_wait(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home never taken: the pass stops at its wait and asks the owner."""
    monkeypatch.setattr(device_setup, "SETUP_RETRY_SECONDS", 0)
    _prepare(panel, "no_record")
    panel.state.refused = True
    panel.entry.runtime_data = SimpleNamespace(coordinator=panel.coordinator)
    # Entry setup has read the panel once, as it does before any operation.
    await panel.coordinator.async_refresh()
    device_setup.async_request(hass, panel.entry, device_setup.OWED_INSTALL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert panel.state.connections == device_setup.SETUP_ATTEMPTS
    assert _owed(panel.entry) == device_setup.OWED_INSTALL
    issue = _issue(hass, panel.entry)
    assert issue is not None and issue.is_fixable
    # Nothing runs in the background after that.
    panel.state.now += 3601
    await panel.coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert panel.state.connections == device_setup.SETUP_ATTEMPTS


async def test_an_update_asking_while_a_pass_runs_gets_a_pass_of_its_own(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
) -> None:
    _prepare(panel, "no_record")
    (panel.directory / "home").write_text(f"{PACKAGE}/.DashboardActivity\n")
    panel.entry.runtime_data = SimpleNamespace(coordinator=panel.coordinator)
    # Entry setup has read the panel once, as it does before any operation.
    await panel.coordinator.async_refresh()
    panel.state.pause_at = "connect"
    device_setup.async_request(hass, panel.entry, device_setup.OWED_INSTALL)
    async with asyncio.timeout(5):
        await panel.state.paused.wait()
    # The update finished and asks while the install's pass is mid-way.
    device_setup.async_mark_owed(hass, panel.entry, device_setup.OWED_UPDATE)
    device_setup.async_start(hass, panel.entry)
    panel.state.pause_at = None
    panel.state.resume.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert panel.state.connections == 2
    assert _owed(panel.entry) is None
    assert panel.entry.data[CONF_DEVICE_SETUP] == RECORDED


@pytest.mark.parametrize("ready", [True, False], ids=["ready", "not_ready"])
async def test_the_repair_runs_the_pass_on_confirm_and_says_when_not_ready(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
    sent: list[str],
    ready: bool,
) -> None:
    _prepare(panel, "no_record")
    (panel.directory / "home").write_text(f"{PACKAGE}/.DashboardActivity\n")
    hass.config_entries.async_update_entry(
        panel.entry,
        data={**panel.entry.data, CONF_DEVICE_SETUP_OWED: device_setup.OWED_INSTALL},
    )
    panel.entry.runtime_data = SimpleNamespace(coordinator=panel.coordinator)
    # Entry setup has read the panel once, as it does before any operation.
    await panel.coordinator.async_refresh()
    device_setup.async_raise_issue(hass, panel.entry)
    issue_id = device_setup.device_setup_issue_id(panel.entry.entry_id)
    flow = await async_create_fix_flow(
        hass, issue_id, {"entry_id": panel.entry.entry_id}
    )
    flow.hass = hass
    flow.issue_id = issue_id
    flow.handler = DOMAIN
    flow.flow_id = "fix"
    form = await flow.async_step_init()
    # Opening the Repair sends nothing.
    assert form["step_id"] == "confirm_setup"
    assert sent == []
    if not ready:
        panel.state.health = replace(panel.state.health, setup_complete=False)
    result = await flow.async_step_confirm_setup({})
    if ready:
        assert result["type"] == "create_entry"
        assert _changing(sent) != []
        assert (panel.directory / "calls").read_text().splitlines() == [
            f"set-active-admin {PACKAGE}/{PACKAGE}.platform.PanelDeviceAdminReceiver"
        ]
        assert panel.state.vendor_passes == 1
        assert panel.entry.data[CONF_DEVICE_SETUP] == RECORDED
        assert _owed(panel.entry) is None
        assert _issue(hass, panel.entry) is None
    else:
        assert result["type"] == "form"
        assert result["errors"] == {"base": "panel_not_ready"}
        assert sent == []
        assert _owed(panel.entry) == device_setup.OWED_INSTALL


async def test_an_update_pass_leaves_the_vendor_setup_to_the_update(
    hass: HomeAssistant,
    panel: Any,  # noqa: F811
) -> None:
    """The update's own grant repair applied it; the pass claims the admin only."""
    _prepare(panel, "no_record")
    (panel.directory / "home").write_text(f"{PACKAGE}/.DashboardActivity\n")
    panel.entry.runtime_data = SimpleNamespace(coordinator=panel.coordinator)
    # Entry setup has read the panel once, as it does before any operation.
    await panel.coordinator.async_refresh()
    device_setup.async_request(hass, panel.entry, device_setup.OWED_UPDATE)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (panel.directory / "admin").exists()
    assert panel.state.vendor_passes == 0
    assert _owed(panel.entry) is None


async def test_local_route_targets_are_never_requested_by_the_poll(
    panel: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with a live local channel, the poll does not open it for setup."""
    _prepare(panel, "no_record")
    target = AdbInstallTarget(
        panel.coordinator.client.address,
        "SERIAL-1",
        "Test Panel",
        "arm64-v8a",
        34,
        local_shell=adb_protocol.AdbDeviceAsync(None),
    )
    request = AsyncMock(return_value=target)
    monkeypatch.setattr(local_adb, "async_target", request)
    monkeypatch.setattr(local_adb, "session_for", MagicMock(return_value=object()))
    for _ in range(2):
        panel.state.now += 3601
        await panel.coordinator.async_refresh()
    request.assert_not_awaited()
    assert not (panel.directory / "admin").exists()

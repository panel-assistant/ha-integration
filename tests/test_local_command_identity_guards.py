"""Local privileged callers refuse changed panel and session identities."""

import asyncio
import base64
import contextlib
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import local_adb, update_route
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    InstallAdbError,
    InstallAdbErrorCode,
    LaunchOutcome,
)
from custom_components.panel_assistant.client import normalize_address
from custom_components.panel_assistant.release import ReleaseArtifact
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .install_adb_fakes import _identity_root_output, _identity_sections
from .test_native import _report, _setup
from .test_transport import WsClientFactory, _receive, _send
from .test_transport_commands import Panel, _connect


@pytest.fixture
async def local_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> tuple[Any, Panel]:
    entry = await _setup(hass, hass_read_only_user.id)
    panel = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        capabilities=["management", "local_adb"],
        sync=False,
    )
    return entry, panel


async def _shell_answer(panel: Panel, event: dict[str, Any], body: bytes) -> None:
    await panel.answer(
        event["command_id"],
        "applied",
        result={
            "stdout_b64": base64.b64encode(body).decode(),
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
        },
    )


async def _refused_without_command(task: asyncio.Task[Any], panel: Panel) -> None:
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(panel.nothing_sent(), 1)
        with pytest.raises(InstallAdbError) as error:
            await asyncio.wait_for(task, 1)
        assert error.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_shell_refuses_entry_whose_device_identity_changed(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
) -> None:
    entry, panel = local_panel
    # First prove this authenticated socket can execute the caller's command.
    task = asyncio.create_task(local_adb.async_shell(hass, entry, "echo owner", 1000))
    event = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
    assert event["op"] == "adb.shell"
    await _shell_answer(panel, event, b"owner\n")
    assert await task == b"owner\n"
    hass.config_entries.async_update_entry(entry, unique_id="another-device")
    await _refused_without_command(
        asyncio.create_task(local_adb.async_shell(hass, entry, "echo refused", 1000)),
        panel,
    )


async def test_shell_refuses_retained_session_after_real_reconnect(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    entry, previous = local_panel
    old = async_get_sessions(hass).get(entry.entry_id)
    panel = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        capabilities=["management", "local_adb"],
        sync=False,
    )
    ended = (await asyncio.wait_for(_receive(previous.client), 1))["event"]
    assert ended["kind"] == "session_closed"
    task = asyncio.create_task(
        local_adb.async_shell(hass, entry, "echo refused", 1000, session=old)
    )
    try:
        await asyncio.sleep(0)
        # A report response is a barrier on the old socket: no command may precede it.
        response = await asyncio.wait_for(
            _send(previous.client, _report(previous.token, "delta", [])), 1
        )
        assert response["type"] == "result"
        assert response["success"] is False
        await _refused_without_command(task, panel)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    # The replacement socket remains usable after refusing the stale caller.
    task = asyncio.create_task(local_adb.async_shell(hass, entry, "echo new", 1000))
    event = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
    await _shell_answer(panel, event, b"new\n")
    assert await task == b"new\n"


async def _prove_target(
    hass: HomeAssistant, entry: Any, panel: Panel, serial: str
) -> Any:
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    ready = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
    assert ready["op"] == "adb.ready"
    await panel.answer(
        ready["command_id"],
        "applied",
        result={"ready": True, "managed": True, "recovery_hold": False},
    )
    for prefix in ("ID", "POSTURE"):
        event = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
        assert event["op"] == "adb.shell"
        nonce = re.search(
            r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", event["args"]["command"]
        )[1]
        body = (
            _identity_root_output(nonce, serial=serial, uid="0")
            if prefix == "POSTURE"
            else (
                "\n".join(
                    [
                        f"HAPANELD_ID_BEGIN:{nonce}",
                        *_identity_sections("ID", nonce, serial=serial),
                        f"HAPANELD_ID_END:{nonce}",
                    ]
                )
                + "\n"
            ).encode()
        )
        await _shell_answer(panel, event, body)
    return await task


async def test_target_refuses_changed_hardware_without_replacing_original_proof(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
) -> None:
    entry, panel = local_panel
    target = await _prove_target(hass, entry, panel, "SERIAL-1")
    assert target.serial == "SERIAL-1"
    with pytest.raises(InstallAdbError) as error:
        await _prove_target(hass, entry, panel, "SERIAL-2")
    assert error.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert (await _prove_target(hass, entry, panel, "SERIAL-1")).serial == "SERIAL-1"


async def test_target_refuses_entry_identity_change_while_awaiting_ready(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
) -> None:
    entry, panel = local_panel
    # async_target holds the session it started with; the identity probe it sends
    # after adb.ready must re-check that session against the entry as it is now.
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    ready = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
    assert ready["op"] == "adb.ready"
    hass.config_entries.async_update_entry(entry, unique_id="another-device")
    await panel.answer(
        ready["command_id"],
        "applied",
        result={"ready": True, "managed": True, "recovery_hold": False},
    )
    await _refused_without_command(task, panel)


@pytest.mark.parametrize(
    "replacement", [False, True], ids=["same-hardware", "replacement-hardware"]
)
async def test_update_relaunch_rejects_recovery_on_other_hardware_before_preflight(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
    monkeypatch: pytest.MonkeyPatch,
    replacement: bool,
) -> None:
    entry, panel = local_panel
    await _prove_target(hass, entry, panel, "SERIAL-1")
    sessions = async_get_sessions(hass)
    sessions.close(sessions.get(entry.entry_id), "connection_lost")
    coordinator = entry.runtime_data.coordinator
    coordinator.reachable = False
    target = AdbInstallTarget(
        normalize_address("panel.local"),
        "SERIAL-2" if replacement else "SERIAL-1",
        "Test Panel",
        "arm64-v8a",
        34,
    )
    monkeypatch.setattr(
        update_route,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=SimpleNamespace(signer=object())),
    )
    monkeypatch.setattr(
        update_route, "async_pin_install_target", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        update_route, "async_probe_adb_target", AsyncMock(return_value=target)
    )
    preflight = AsyncMock(
        return_value=SimpleNamespace(target_installed=True, root_mode="root")
    )
    launch = AsyncMock(return_value=LaunchOutcome.STARTED)
    monkeypatch.setattr(update_route, "async_preflight_install", preflight)
    monkeypatch.setattr(
        update_route, "async_installed_artifact_size", AsyncMock(return_value=123)
    )
    monkeypatch.setattr(update_route, "async_launch_installed_app", launch)
    artifact = ReleaseArtifact(
        "v1.0.0",
        "1.0.0",
        "app.apk",
        "",
        "",
        descriptor=SimpleNamespace(
            package_id="io.panelassistant.android", version_code=1100
        ),
    )
    assert await update_route.async_relaunch_stopped_app(
        hass, coordinator, artifact, lambda: "panel"
    ) is (not replacement)
    if replacement:
        preflight.assert_not_awaited()
        launch.assert_not_awaited()
    else:
        preflight.assert_awaited_once()
        launch.assert_awaited_once()


@pytest.mark.parametrize(
    "result",
    [{"enabled": True}, {"enabled": False}, {"enabled": True, "extra": True}],
    ids=["enabled", "disabled", "extra-field"],
)
async def test_recovery_requires_exact_enabled_acknowledgement(
    hass: HomeAssistant,
    local_panel: tuple[Any, Panel],
    result: dict[str, Any],
) -> None:
    entry, panel = local_panel
    task = asyncio.create_task(local_adb.async_recover(hass, entry))
    event = (await asyncio.wait_for(_receive(panel.client), 1))["event"]
    assert event["op"] == "adb.recover"
    await panel.answer(event["command_id"], "applied", result=result)
    if result == {"enabled": True}:
        await task
    else:
        with pytest.raises(InstallAdbError):
            await task
    await panel.nothing_sent()

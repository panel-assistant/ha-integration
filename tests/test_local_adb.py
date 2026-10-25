"""Privileged panel commands retain their bytes on the authenticated session."""

import asyncio
import base64
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.panel_assistant.adb_protocol import InstallAdbError

from .test_native import _setup
from .test_transport import WsClientFactory, _receive
from .test_transport_commands import _connect


async def _panel(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, user: Any, token: str
) -> Any:
    entry = await _setup(hass, user.id)
    panel = await _connect(
        hass,
        hass_ws_client,
        token,
        capabilities=["management", "local_adb"],
        sync=False,
    )
    return entry, panel


@pytest.mark.parametrize("management", [True, False])
async def test_local_channel_requires_management_grant(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    management: bool,
) -> None:
    await _setup(hass, hass_read_only_user.id)
    panel = await _connect(
        hass,
        hass_ws_client,
        hass_read_only_access_token,
        capabilities=["local_adb"] + (["management"] if management else []),
        sync=False,
    )
    assert ("local_adb" in panel.result["capabilities"]) is management


@pytest.mark.parametrize(
    "answer",
    [
        {"exit_code": 0, "timed_out": False, "truncated": False},
        {"exit_code": 1, "timed_out": False, "truncated": False},
        {"exit_code": None, "timed_out": True, "truncated": False},
        {"exit_code": 0, "timed_out": False, "truncated": True},
        {"exit_code": True, "timed_out": False, "truncated": False},
    ],
)
async def test_shell_preserves_bytes_and_rejects_incomplete_execution(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    answer: dict[str, Any],
) -> None:
    from custom_components.panel_assistant import local_adb

    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    task = asyncio.create_task(local_adb.async_shell(hass, entry, "echo fixture", 1000))
    event = (await _receive(panel.client))["event"]
    assert event["kind"] == "manage"
    assert event["op"] == "adb.shell"
    assert event["args"] == {"command": "echo fixture", "timeout_ms": 1000}
    output = b"framed\r\n\xff\x00"
    await panel.answer(
        event["command_id"],
        "applied",
        result={
            "stdout_b64": base64.b64encode(output).decode(),
            **answer,
        },
    )
    if answer == {"exit_code": 0, "timed_out": False, "truncated": False}:
        assert await task == output
    else:
        with pytest.raises(InstallAdbError):
            await task


async def test_selected_local_shell_never_retries_over_network_after_disconnect(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    from custom_components.panel_assistant import local_adb

    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    with patch("custom_components.panel_assistant.adb_protocol.AdbDeviceAsync") as tcp:
        task = asyncio.create_task(
            local_adb.async_shell(hass, entry, "echo fixture", 1000)
        )
        event = (await _receive(panel.client))["event"]
        assert event["op"] == "adb.shell"
        await panel.client.close()
        with pytest.raises((InstallAdbError, HomeAssistantError)):
            await task
        tcp.assert_not_called()


@pytest.mark.parametrize("ready_diagnostic", [None, False, True])
@pytest.mark.parametrize("bootstrap_needed", [False, True])
async def test_local_target_proves_readiness_and_physical_identity_on_the_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_needed: bool,
    ready_diagnostic: bool | None,
) -> None:
    import re

    from custom_components.panel_assistant import install_adb, local_adb
    from custom_components.panel_assistant.client import normalize_address
    from custom_components.panel_assistant.transport_sessions import async_get_sessions

    from .install_adb_fakes import _identity_root_output, _identity_sections

    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    if bootstrap_needed:
        from unittest.mock import AsyncMock

        from adb_shell.auth.sign_pythonrsa import PythonRSASigner

        from custom_components.panel_assistant import (
            adb_credentials,
            install_network,
            local_adb_bootstrap,
            provisioning,
        )
        from custom_components.panel_assistant.adb_credentials import (
            AdbCredential,
        )
        from custom_components.panel_assistant.install_network import PinnedPanelTarget

        address = normalize_address("192.168.1.23")
        pinned = PinnedPanelTarget(address, address)
        monkeypatch.setattr(
            install_network, "async_pin_entry_target", AsyncMock(return_value=pinned)
        )
        monkeypatch.setattr(
            install_network,
            "async_revalidate_install_target",
            AsyncMock(return_value=pinned),
        )
        monkeypatch.setattr(
            provisioning,
            "async_probe_install_target",
            AsyncMock(
                return_value=provisioning.InstallTargetProbe(
                    provisioning.InstallTargetState.INSTALLED,
                    "Test Panel",
                    "SERIAL-1",
                    "arm64-v8a",
                    34,
                )
            ),
        )
        monkeypatch.setattr(
            adb_credentials,
            "async_get_durable_adb_credential",
            AsyncMock(
                return_value=AdbCredential(
                    object.__new__(PythonRSASigner), "original-key"
                )
            ),
        )
        bootstrap = AsyncMock(return_value=True)
        monkeypatch.setattr(local_adb_bootstrap, "async_bootstrap", bootstrap)
    diagnostic = (
        {} if ready_diagnostic is None else {"security_unverified": ready_diagnostic}
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    ready = (await _receive(panel.client))["event"]
    assert ready["op"] == "adb.ready"
    await panel.answer(
        ready["command_id"],
        "applied",
        result={
            "ready": not bootstrap_needed,
            "managed": not bootstrap_needed,
            "recovery_hold": False,
            **diagnostic,
        },
    )
    if bootstrap_needed:
        prepare = (await _receive(panel.client))["event"]
        assert prepare["op"] == "adb.prepare"
        sha = "a" * 64
        asset = (
            f"/data/user/0/io.panelassistant.android/cache/adb-recovery/helper-{sha}"
        )
        await panel.answer(
            prepare["command_id"],
            "applied",
            result={"path": asset, "sha256": sha, "build_id": "b" * 64},
        )
        ready = (await _receive(panel.client))["event"]
        assert ready["op"] == "adb.ready"
        assert bootstrap.await_count == 1
        assert bootstrap.call_args.args[0].serial == "SERIAL-1"
        assert bootstrap.call_args.args[2:] == (asset, sha)
        assert bootstrap.call_args.kwargs["before_mutation"]()
        await panel.answer(
            ready["command_id"],
            "applied",
            result={
                "ready": True,
                "managed": True,
                "recovery_hold": False,
                **diagnostic,
            },
        )
    for prefix in ("ID", "POSTURE"):
        event = (await _receive(panel.client))["event"]
        assert event["op"] == "adb.shell"
        nonce = re.search(
            r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", event["args"]["command"]
        )[1]
        body = (
            _identity_root_output(nonce, uid="0")
            if prefix == "POSTURE"
            else (
                "\n".join(
                    [
                        f"HAPANELD_ID_BEGIN:{nonce}",
                        *_identity_sections("ID", nonce),
                        f"HAPANELD_ID_END:{nonce}",
                    ]
                )
                + "\n"
            ).encode()
        )
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
    target = await task
    assert target.serial == "SERIAL-1"
    assert local_adb.recovery_wait(hass, entry) == 315
    # The ordinary display-restoration production seam uses the attached
    # carrier; no ADB key or outbound network connection is needed.
    restore = asyncio.create_task(
        install_adb.async_restore_display_sizing(target, None)
    )
    for prefix in ("POSTURE", "DISPLAY_RESTORE"):
        event = (await _receive(panel.client))["event"]
        assert event["op"] == "adb.shell"
        nonce = re.search(
            r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", event["args"]["command"]
        )[1]
        body = (
            _identity_root_output(nonce, uid="0")
            if prefix == "POSTURE"
            else (
                f"HAPANELD_DISPLAY_RESTORE_BEGIN:{nonce}\nrestored\n"
                f"HAPANELD_DISPLAY_RESTORE_END:{nonce}:0\n"
            ).encode()
        )
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
    assert await restore == "restored"
    assert async_get_sessions(hass).get(entry.entry_id) is not None


async def test_hardened_local_command_waits_for_approval_and_its_final_result(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.panel_assistant import local_adb, transport_sessions

    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    original = transport_sessions.async_manage_outcome

    async def short_response(
        hass: Any, session: Any, op: str, wait_seconds: float, **fields: Any
    ) -> Any:
        return await original(hass, session, op, 0.02, **fields)

    monkeypatch.setattr(local_adb, "async_manage_outcome", short_response)
    monkeypatch.setattr(local_adb, "LOCAL_ADB_APPROVAL_SECONDS", 0.2, raising=False)
    task = asyncio.create_task(
        local_adb.async_shell(hass, entry, "echo approved", 1000)
    )
    event = (await _receive(panel.client))["event"]
    await panel.answer(event["command_id"], "pending_approval")
    await asyncio.sleep(0.05)
    assert not task.done(), (
        "pending approval retains the request beyond its response budget"
    )
    await panel.answer(
        event["command_id"],
        "applied",
        result={
            "stdout_b64": base64.b64encode(b"approved\n").decode(),
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
        },
    )
    assert await task == b"approved\n"


async def test_disconnected_pending_hello_does_not_open_phantom_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from custom_components.panel_assistant import install_jobs
    from custom_components.panel_assistant.transport_sessions import async_get_sessions

    from .test_transport_commands import _hello

    entry = await _setup(hass, hass_read_only_user.id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def listing() -> list[Any]:
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(
        install_jobs,
        "async_get_install_job_manager",
        AsyncMock(return_value=SimpleNamespace(async_list=listing)),
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_hello(["management", "local_adb"]))
    await asyncio.wait_for(entered.wait(), 1)
    await client.close()
    await asyncio.sleep(0.05)
    release.set()
    await hass.async_block_till_done()
    assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize("diagnostic", ["false", 0, 1, None, {}, []])
async def test_ready_refuses_malformed_security_diagnostic_before_shell_execution(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    diagnostic: Any,
) -> None:
    from custom_components.panel_assistant import local_adb
    from custom_components.panel_assistant.client import normalize_address

    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    ready = (await _receive(panel.client))["event"]
    assert ready["op"] == "adb.ready"
    await panel.answer(
        ready["command_id"],
        "applied",
        result={
            "ready": True,
            "managed": True,
            "recovery_hold": False,
            "security_unverified": diagnostic,
        },
    )
    await panel.nothing_sent()
    assert task.done(), "malformed readiness is refused before any shell request"
    with pytest.raises(InstallAdbError):
        await task

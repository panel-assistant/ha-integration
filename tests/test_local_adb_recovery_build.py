"""A stale recovery build is replaced before selecting authenticated local I/O."""

import asyncio
import base64
import re
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    adb_credentials,
    install_network,
    local_adb,
    local_adb_bootstrap,
    provisioning,
)
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.adb_protocol import InstallAdbError
from custom_components.panel_assistant.client import normalize_address
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .install_adb_fakes import _identity_root_output, _identity_sections
from .test_local_adb import _panel
from .test_transport import _receive

BUILD = "b" * 64
OLD_BUILD = "c" * 64
SHA = "a" * 64
PATH = f"/data/user/0/io.panelassistant.android/cache/adb-recovery/helper-{SHA}"


def _ready(build: Any = BUILD) -> dict[str, Any]:
    return {
        "ready": True,
        "managed": True,
        "recovery_hold": False,
        "build_id": build,
    }


async def _event(panel: Any, task: asyncio.Task[Any]) -> dict[str, Any]:
    receiving = asyncio.create_task(_receive(panel.client))
    done, _ = await asyncio.wait({receiving, task}, return_when=asyncio.FIRST_COMPLETED)
    if task in done and receiving not in done:
        receiving.cancel()
        await task
        pytest.fail("The target returned before the required protocol request")
    return (await receiving)["event"]


async def _answer_identity(panel: Any, task: asyncio.Task[Any]) -> None:
    for prefix in ("ID", "POSTURE"):
        event = await _event(panel, task)
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


@pytest.mark.parametrize(
    "scenario",
    [
        "matching",
        "stale",
        "bootstrap-fails",
        "recover-refused",
        "wrong-build",
        "identity-changed",
        "legacy",
    ],
)
async def test_recovery_build_controls_selection(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    address = normalize_address("panel.local")
    pinned = PinnedPanelTarget(address, address)
    pin = AsyncMock(return_value=pinned)
    revalidate = AsyncMock(return_value=pinned)
    bootstrap = AsyncMock(return_value=scenario != "bootstrap-fails")
    monkeypatch.setattr(install_network, "async_pin_entry_target", pin)
    monkeypatch.setattr(install_network, "async_revalidate_install_target", revalidate)
    monkeypatch.setattr(local_adb_bootstrap, "async_bootstrap", bootstrap)
    monkeypatch.setattr(
        adb_credentials,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=AdbCredential(object(), "original-key")),
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
    if scenario == "identity-changed":
        async_get_sessions(hass).get(entry.entry_id).local_adb_identity = (
            "OTHER-SERIAL",
            "Test Panel",
            "arm64-v8a",
            34,
        )
    # Keep the wrong-build failure bounded without waiting through ten polls.
    monkeypatch.setattr(local_adb.asyncio, "sleep", AsyncMock())
    task = asyncio.create_task(local_adb.async_target(hass, entry, address))
    event = await _event(panel, task)
    assert event["op"] == "adb.ready"
    ready = _ready(BUILD if scenario == "matching" else OLD_BUILD)
    if scenario == "legacy":
        ready.pop("build_id")
    await panel.answer(event["command_id"], "applied", result=ready)
    if scenario != "legacy":
        event = await _event(panel, task)
        assert event["op"] == "adb.prepare"
        await panel.answer(
            event["command_id"],
            "applied",
            result={"path": PATH, "sha256": SHA, "build_id": BUILD},
        )
    if scenario not in ("matching", "legacy"):
        event = await _event(panel, task)
        assert event["op"] == "adb.recover"
        await panel.answer(
            event["command_id"],
            "applied",
            result={"enabled": scenario != "recover-refused"},
        )
        if scenario == "recover-refused":
            with pytest.raises(InstallAdbError):
                await task
            bootstrap.assert_not_awaited()
            return
        if scenario == "identity-changed":
            with pytest.raises(InstallAdbError):
                await task
            bootstrap.assert_not_awaited()
            return
        if scenario == "bootstrap-fails":
            assert await task is None
            assert bootstrap.await_count == 1
            return
        for _ in range(10 if scenario == "wrong-build" else 1):
            event = await _event(panel, task)
            assert event["op"] == "adb.ready"
            await panel.answer(
                event["command_id"],
                "applied",
                result=_ready(OLD_BUILD if scenario == "wrong-build" else BUILD),
            )
        if scenario == "wrong-build":
            with pytest.raises(InstallAdbError):
                await task
            return
    await _answer_identity(panel, task)
    target = await task
    assert target.serial == "SERIAL-1"
    if scenario == "stale":
        assert bootstrap.await_count == 1
        assert bootstrap.call_args.args[0].serial == "SERIAL-1"
        assert bootstrap.call_args.args[2:] == (PATH, SHA)
        assert bootstrap.call_args.kwargs["before_mutation"]()
        pin.assert_awaited_once()
        revalidate.assert_awaited_once()
    else:
        bootstrap.assert_not_awaited()
        pin.assert_not_awaited()


@pytest.mark.parametrize(
    "stage,value",
    [
        ("ready", None),
        ("ready", True),
        ("ready", "B" * 64),
        ("ready", "short"),
        ("prepare", "short"),
        ("prepare", None),
    ],
)
async def test_invalid_recovery_build_is_refused(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    value: Any,
) -> None:
    entry, panel = await _panel(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
    )
    bootstrap = AsyncMock()
    monkeypatch.setattr(local_adb_bootstrap, "async_bootstrap", bootstrap)
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    event = await _event(panel, task)
    assert event["op"] == "adb.ready"
    await panel.answer(
        event["command_id"],
        "applied",
        result=_ready(value if stage == "ready" else OLD_BUILD),
    )
    if stage == "prepare":
        event = await _event(panel, task)
        assert event["op"] == "adb.prepare"
        await panel.answer(
            event["command_id"],
            "applied",
            result={"path": PATH, "sha256": SHA, "build_id": value},
        )
    with pytest.raises(InstallAdbError):
        await task
    bootstrap.assert_not_awaited()

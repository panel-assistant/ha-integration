"""The background admin pass over the local command channel never waits on approvals.

In Hardened mode every local shell needs its own on-panel approval. The poll
and the panel-operation claim must not wait for one, and the pass must not ask
again faster than its back-off.
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import coordinator as coordinator_module
from custom_components.panel_assistant.client import HaPaneldClient
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)

from .install_adb_fakes import _identity_root_output, _identity_sections
from .test_availability import _load
from .test_management import MANAGED, STATUS_BODY, _connect
from .test_transport import DID, HEALTH, WsClientFactory, _receive

PACKAGE = "io.panelassistant.android"
HEALTH_LINE = (
    f"ha-paneld 1.0.0 panel=alpha build=2000 cfg=1a2b3c4d did={DID}"
    f" identity=install vc=1200 proto=5-5 pkg={PACKAGE} setup_complete=1\n"
)


def _shell_result(body: bytes) -> dict[str, Any]:
    return {
        "stdout_b64": base64.b64encode(body).decode(),
        "exit_code": 0,
        "timed_out": False,
        "truncated": False,
    }


def _answer_shell(command: str) -> bytes:
    nonce = re.search(r"HAPANELD_(\w+?)_BEGIN:([a-f0-9]{32})", command)
    assert nonce is not None
    prefix, value = nonce[1], nonce[2]
    if prefix == "ID":
        return (
            "\n".join(
                [
                    f"HAPANELD_ID_BEGIN:{value}",
                    *_identity_sections("ID", value),
                    f"HAPANELD_ID_END:{value}",
                ]
            )
            + "\n"
        ).encode()
    if prefix == "POSTURE":
        return _identity_root_output(value, uid="0")
    assert prefix == "ADMIN", command
    return (
        f"HAPANELD_ADMIN_BEGIN:{value}\nactive\nhome:other/.Home\n"
        f"HAPANELD_ADMIN_END:{value}:0\n"
    ).encode()


async def _panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user: Any,
    token: str,
    approve: bool,
) -> Any:
    health = replace(HEALTH, discovery_id=DID, package=PACKAGE, setup_complete=True)
    with patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=health),
        async_get_status=AsyncMock(return_value=None),
    ):
        entry = await _load(hass, user.id, unique_id=DID)
    panel = await _connect(hass, hass_ws_client, token, [*MANAGED, "local_adb"])
    shells: list[str] = []

    async def respond() -> None:
        while True:
            event = (await _receive(panel.client))["event"]
            if event.get("kind") != "manage":
                continue
            op = event["op"]
            if op == "snapshot":
                result = {"health": HEALTH_LINE, "status": STATUS_BODY}
            elif op == "adb.ready":
                result = {"ready": True, "managed": True, "recovery_hold": False}
            elif op == "adb.shell":
                shells.append(event["args"]["command"])
                if not approve:
                    await panel.answer(event, "pending_approval")
                    continue  # Nobody stands at the panel.
                result = _shell_result(_answer_shell(event["args"]["command"]))
            else:
                await panel.answer(event, "refused", code="unknown_command")
                continue
            await panel.answer(event, "applied", result=result)

    responder = asyncio.create_task(respond())
    return entry, shells, responder


async def test_background_admin_pass_never_waits_on_panel_approval(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: now[0])
    with patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=HEALTH),
        async_get_status=AsyncMock(return_value=None),
    ):
        entry, shells, responder = await _panel(
            hass,
            hass_ws_client,
            hass_read_only_user,
            hass_read_only_access_token,
            approve=False,
        )
        coordinator = entry.runtime_data.coordinator
        try:
            # The poll budget, well inside the 45 s network deadline.
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert coordinator.last_update_success
            assert coordinator.data.health.setup_complete
            assert len(shells) == 1
            # An owner action is not refused as busy while the prompt is shown.
            assert claim_panel_operation(hass, entry.entry_id)
            release_panel_operation(hass, entry.entry_id)

            # Later polls inside the back-off send no new approval request,
            # even after the ordinary five-minute retry spacing.
            for _ in range(3):
                now[0] += 301
                await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == 1
            # Past the back-off, exactly one more request.
            now[0] += 3600
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == 2
            # And the next back-off is longer than the last.
            now[0] += 3601
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == 2
        finally:
            responder.cancel()


async def test_background_admin_pass_completes_when_the_panel_answers(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    with patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=HEALTH),
        async_get_status=AsyncMock(return_value=None),
    ):
        entry, shells, responder = await _panel(
            hass,
            hass_ws_client,
            hass_read_only_user,
            hass_read_only_access_token,
            approve=True,
        )
        coordinator = entry.runtime_data.coordinator
        try:
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert coordinator.last_update_success
            assert any("HAPANELD_ADMIN_BEGIN" in shell for shell in shells)
            assert coordinator._admin_done
            count = len(shells)
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == count
        finally:
            responder.cancel()

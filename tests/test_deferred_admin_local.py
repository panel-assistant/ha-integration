"""The device setup pass over the local command channel and on-panel approvals.

In Hardened mode every local shell needs its own on-panel approval. An
operation's unattended pass never waits for one and asks once, then leaves the
Repair; the poll never asks at all.
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
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant import device_setup
from custom_components.panel_assistant.client import HaPaneldClient
from custom_components.panel_assistant.const import CONF_DEVICE_SETUP_OWED, DOMAIN
from custom_components.panel_assistant.device_setup import (
    SetupApprovalPending,
    async_attempt,
)
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


async def test_unattended_setup_pass_never_waits_on_panel_approval(
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
            approve=False,
        )
        coordinator = entry.runtime_data.coordinator
        try:
            # Well inside the 45 s network deadline: the prompt is not waited on.
            with pytest.raises(SetupApprovalPending):
                await asyncio.wait_for(
                    async_attempt(hass, entry, coordinator, refresh=True), 5
                )
            assert coordinator.last_update_success
            assert coordinator.data.health.setup_complete
            assert len(shells) == 1
            # An owner action is not refused as busy while the prompt is shown.
            assert claim_panel_operation(hass, entry.entry_id)
            release_panel_operation(hass, entry.entry_id)
            # Polls never ask again.
            for _ in range(3):
                await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == 1
        finally:
            responder.cancel()


async def test_an_operations_pass_stops_at_one_prompt_and_raises_the_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardened mode needs someone at the panel; the owner is asked once."""
    monkeypatch.setattr(device_setup, "SETUP_RETRY_SECONDS", 0)
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
        try:
            # A poll over the session reads the finished setup first.
            await asyncio.wait_for(entry.runtime_data.coordinator.async_refresh(), 5)
            device_setup.async_request(hass, entry, device_setup.OWED_UPDATE)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert len(shells) == 1
            issue = ir.async_get(hass).async_get_issue(
                DOMAIN, device_setup.device_setup_issue_id(entry.entry_id)
            )
            assert issue is not None and issue.is_fixable
            assert entry.data[CONF_DEVICE_SETUP_OWED] == device_setup.OWED_UPDATE
        finally:
            responder.cancel()


async def test_setup_pass_completes_when_the_panel_answers(
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
            record = await asyncio.wait_for(
                async_attempt(hass, entry, coordinator, refresh=True), 5
            )
            assert record is not None and record["did"] == DID
            assert any("HAPANELD_ADMIN_BEGIN" in shell for shell in shells)
            count = len(shells)
            await asyncio.wait_for(coordinator.async_refresh(), 5)
            assert len(shells) == count
        finally:
            responder.cancel()

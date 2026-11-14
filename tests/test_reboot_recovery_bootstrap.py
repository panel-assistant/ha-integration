"""A panel that comes back from a reboot gets its ADB recovery daemon again.

On a rooted panel whose app has no working su, only Panel Assistant's network
ADB route can start the recovery daemon that closes ADB. The device setup pass
ran once, inside the install, so nothing else would start it after a reboot:
the poll restarts it once per new session when that pass saw root.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    adb_credentials,
    device_setup,
    install_network,
    provisioning,
)
from custom_components.panel_assistant import adb_protocol as adb
from custom_components.panel_assistant import coordinator as coordinator_module
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.client import HaPaneldClient
from custom_components.panel_assistant.const import CONF_DEVICE_SETUP
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .install_adb_fakes import _identity_root_output, _identity_sections, _section
from .test_availability import _load
from .test_management import MANAGED, STATUS_BODY, _connect
from .test_transport import DID, HEALTH, WsClientFactory, _receive

PACKAGE = "io.panelassistant.android"
HEALTH_LINE = (
    f"ha-paneld 1.0.0 panel=alpha build=2000 cfg=1a2b3c4d did={DID}"
    f" identity=install vc=1200 proto=5-5 pkg={PACKAGE} setup_complete=1\n"
)
PINNED = "192.168.1.50"
SHA = "a" * 64
BUILD = "b" * 64
PREPARED = {
    "path": f"/data/user/0/{PACKAGE}/cache/adb-recovery/helper-{SHA}",
    "sha256": SHA,
    "build_id": BUILD,
}


def _shell_result(body: bytes) -> dict[str, Any]:
    return {
        "stdout_b64": base64.b64encode(body).decode(),
        "exit_code": 0,
        "timed_out": False,
        "truncated": False,
    }


def _answer_shell(command: str, route: str = "adbd") -> bytes:
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
        # The ADB route's own posture: root adbd (uid 0), a shell with a
        # working su, or neither. The app itself has no su.
        return _identity_root_output(
            value,
            uid="0" if route == "adbd" else "2000",
            su_lines=["present" if route == "su" else "absent"],
        )
    if prefix == "DELEGATE":
        return (
            "\n".join(
                [
                    f"HAPANELD_DELEGATE_BEGIN:{value}",
                    f"HAPANELD_SU_BEGIN:{value}",
                    *_section("SU", "UID", value, ["0"], 0),
                    f"HAPANELD_SU_END:{value}",
                    f"HAPANELD_DELEGATE_END:{value}:0",
                ]
            )
            + "\n"
        ).encode()
    if prefix == "BOOTSTRAP":
        return (
            f"HAPANELD_BOOTSTRAP_BEGIN:{value}\nlaunched\n"
            f"HAPANELD_BOOTSTRAP_END:{value}:0\n"
        ).encode()
    assert prefix == "ADMIN", command
    return (
        f"HAPANELD_ADMIN_BEGIN:{value}\nactive\nhome:other/.Home\n"
        f"HAPANELD_ADMIN_END:{value}:0\n"
    ).encode()


class _Panel:
    """One booted panel: its daemon state, its network ADB and its sessions."""

    def __init__(self, daemon_running: bool, route: str = "adbd") -> None:
        self.daemon_running = daemon_running
        self.route = route
        self.requests: list[str] = []
        self.bootstraps: list[str] = []
        self.network_hosts: list[str] = []
        self.failed_launches = 0

    async def serve(self, panel: Any) -> None:
        while True:
            event = (await _receive(panel.client))["event"]
            if event.get("kind") != "manage":
                continue
            op = event["op"]
            self.requests.append(op)
            if op == "snapshot":
                result: dict[str, Any] = {"health": HEALTH_LINE, "status": STATUS_BODY}
            elif op == "adb.ready":
                result = {
                    "ready": self.daemon_running,
                    "managed": True,
                    "recovery_hold": False,
                    "build_id": BUILD if self.daemon_running else None,
                }
            elif op == "adb.prepare":
                result = PREPARED
            elif op == "adb.shell":
                result = _shell_result(
                    _answer_shell(event["args"]["command"], self.route)
                )
            else:
                await panel.answer(event, "refused", code="unknown_command")
                continue
            await panel.answer(event, "applied", result=result)

    def network_device(self, host: str) -> Any:
        state = self

        class _NetworkAdb:
            async def streaming_shell(
                self, command: str, **_kwargs: Any
            ) -> AsyncIterator[bytes]:
                body = _answer_shell(command, state.route)
                if b"BOOTSTRAP_BEGIN" in body:
                    assert SHA in command
                    assert command.startswith("su 0 ") is (state.route == "su")
                    state.bootstraps.append(host)
                    if state.failed_launches:
                        state.failed_launches -= 1
                        body = body.replace(b"\nlaunched\n", b"\n").replace(
                            b":0\n", b":1\n"
                        )
                    else:
                        state.daemon_running = True
                yield body

            async def close(self) -> None:
                return None

        self.network_hosts.append(host)
        return _NetworkAdb()


@pytest.fixture
def network_adb(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The panel's original network ADB route, reached only by its pinned address."""
    state: dict[str, Any] = {"panel": None, "pinned_did": DID}
    original_connect = adb._async_connect

    async def connect(target: Any, signer: Any, *, authorize: bool = True) -> Any:
        if target.local_shell is not None:
            return await original_connect(target, signer, authorize=authorize)
        return state["panel"].network_device(target.address.host)

    async def resolve(_hass: Any, _address: Any) -> frozenset[Any]:
        return frozenset({ipaddress.ip_address(PINNED)})

    async def health(self: HaPaneldClient) -> Any:
        if self.address.host == PINNED:
            # The identity the pinned address itself answers with.
            return replace(HEALTH, discovery_id=state["pinned_did"])
        return HEALTH

    monkeypatch.setattr(adb, "_async_connect", connect)
    monkeypatch.setattr(install_network, "_async_resolve", resolve)
    monkeypatch.setattr(HaPaneldClient, "async_get_health", health)
    monkeypatch.setattr(
        HaPaneldClient, "async_get_status", AsyncMock(return_value=None)
    )
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
    return state


async def _rebooted_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user: Any,
    token: str,
    network_adb: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    daemon_after_boot: bool,
    route: str = "adbd",
) -> tuple[Any, _Panel, list[float], list[asyncio.Task[None]]]:
    """Finish the install's setup pass, then reboot the panel into a fresh session."""
    now = [1000.0]
    monkeypatch.setattr(coordinator_module, "monotonic", lambda: now[0])
    health = replace(HEALTH, discovery_id=DID, package=PACKAGE, setup_complete=True)
    with patch.multiple(
        HaPaneldClient,
        async_get_health=AsyncMock(return_value=health),
        async_get_status=AsyncMock(return_value=None),
    ):
        entry = await _load(hass, user.id, unique_id=DID)
    # A rootless panel never had a daemon; its pass finishes over network ADB.
    before = _Panel(daemon_running=route != "rootless", route=route)
    network_adb["panel"] = before
    first = await _connect(hass, hass_ws_client, token, [*MANAGED, "local_adb"])
    tasks = [asyncio.create_task(before.serve(first))]
    # A poll over the session reads the finished setup; then the install the
    # owner started runs the device setup pass once.
    await asyncio.wait_for(entry.runtime_data.coordinator.async_refresh(), 5)
    monkeypatch.setattr(device_setup, "SETUP_RETRY_SECONDS", 0)
    device_setup.async_request(hass, entry, device_setup.OWED_INSTALL)
    await asyncio.wait_for(hass.async_block_till_done(wait_background_tasks=True), 10)
    assert entry.data[CONF_DEVICE_SETUP]["root"] is (route != "rootless")
    assert before.bootstraps == []

    # The reboot: the old session ends and the panel says hello again with the
    # same discovery id, package, version code and address.
    first_session = async_get_sessions(hass).get(entry.entry_id)
    await first.client.close()
    await hass.async_block_till_done()
    rebooted = _Panel(daemon_running=daemon_after_boot, route=route)
    network_adb["panel"] = rebooted
    second = await _connect(hass, hass_ws_client, token, [*MANAGED, "local_adb"])
    tasks.append(asyncio.create_task(rebooted.serve(second)))
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session is not first_session
    assert session.local_adb_identity is None
    return entry, rebooted, now, tasks


async def _polls(coordinator: Any, now: list[float], count: int) -> None:
    for _ in range(count):
        now[0] += 301
        await asyncio.wait_for(coordinator.async_refresh(), 5)
        assert coordinator.last_update_success


@pytest.mark.parametrize("route", ["adbd", "su"])
async def test_rebooted_panel_without_daemon_gets_one_pinned_bootstrap(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
    route: str,
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=False,
        route=route,
    )
    try:
        await _polls(entry.runtime_data.coordinator, now, 3)
        # One bootstrap, over network ADB at the address pinned to this panel.
        assert panel.network_hosts == [PINNED]
        assert panel.bootstraps == [PINNED]
        adb_requests = [op for op in panel.requests if op.startswith("adb.")]
        assert adb_requests == ["adb.ready", "adb.prepare", "adb.ready"]
        assert panel.daemon_running
    finally:
        for task in tasks:
            task.cancel()


async def test_rebooted_panel_with_running_daemon_gets_no_bootstrap(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=True,
    )
    try:
        await _polls(entry.runtime_data.coordinator, now, 3)
        assert panel.bootstraps == []
        assert panel.network_hosts == []
        assert [op for op in panel.requests if op.startswith("adb.")] == ["adb.ready"]
    finally:
        for task in tasks:
            task.cancel()


async def test_rebooted_panel_bootstrap_is_pinned_to_this_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=False,
    )
    # Another panel now answers at the address the entry stores.
    network_adb["pinned_did"] = "e" * 64
    try:
        await _polls(entry.runtime_data.coordinator, now, 2)
        assert panel.bootstraps == []
        assert panel.network_hosts == []
        assert not panel.daemon_running
        assert [op for op in panel.requests if op.startswith("adb.")] == [
            "adb.ready",
            "adb.ready",
        ]
    finally:
        for task in tasks:
            task.cancel()


async def test_failing_rebooted_bootstrap_is_spaced(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=False,
    )
    coordinator = entry.runtime_data.coordinator
    probe = AsyncMock(
        return_value=provisioning.InstallTargetProbe(
            provisioning.InstallTargetState.ADB_UNREACHABLE
        )
    )
    monkeypatch.setattr(provisioning, "async_probe_install_target", probe)
    try:
        await _polls(coordinator, now, 1)
        assert probe.await_count == 1
        # Polls inside the retry spacing do not try again.
        for _ in range(3):
            now[0] += 60
            await asyncio.wait_for(coordinator.async_refresh(), 5)
        assert probe.await_count == 1
        # Past it, exactly one more attempt.
        await _polls(coordinator, now, 1)
        assert probe.await_count == 2
        assert panel.bootstraps == []
        assert [op for op in panel.requests if op.startswith("adb.")] == [
            "adb.ready",
            "adb.ready",
        ]
    finally:
        for task in tasks:
            task.cancel()


async def test_failed_rebooted_bootstrap_does_not_rerun_the_admin_pass(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=False,
    )
    panel.failed_launches = 1
    try:
        await _polls(entry.runtime_data.coordinator, now, 3)
        # The launch fails once and succeeds on the spaced retry. The finished
        # admin pass is never repeated, so no shell (and no approval prompt on
        # a Hardened panel) is sent over the local channel.
        assert panel.bootstraps == [PINNED, PINNED]
        assert panel.daemon_running
        assert [op for op in panel.requests if op.startswith("adb.")] == [
            "adb.ready",
            "adb.prepare",
            "adb.ready",
            "adb.prepare",
            "adb.ready",
        ]
    finally:
        for task in tasks:
            task.cancel()


async def test_pass_finished_on_a_rootless_route_never_restarts_recovery(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
    network_adb: dict[str, Any],
) -> None:
    entry, panel, now, tasks = await _rebooted_panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        network_adb,
        monkeypatch,
        daemon_after_boot=False,
        route="rootless",
    )
    try:
        # Several polls, each past the five-minute retry spacing.
        await _polls(entry.runtime_data.coordinator, now, 4)
        # No route here can start a daemon: nothing is staged or connected.
        assert panel.network_hosts == []
        assert [op for op in panel.requests if op.startswith("adb.")] == []
    finally:
        for task in tasks:
            task.cancel()

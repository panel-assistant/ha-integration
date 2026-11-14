"""The device setup pass on fresh health, through the Android shell stand-in.

These tests drive the pass itself (device_setup.async_attempt), which only an
install, an update or a confirmed Repair starts; test_device_setup_trigger.py
proves that the poll, a session and a restart never do.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
import subprocess
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
from custom_components.panel_assistant.device_setup import async_attempt
from custom_components.panel_assistant.home_chooser import CHOOSER_DUMP
from custom_components.panel_assistant.panel_operations import (
    claim_panel_operation,
    release_panel_operation,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelStatus

from .install_adb_fakes import _identity_root_output, _single_output
from .test_install_adb_grants import _run_on_stand_in_panel

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
        vendor_passes=0,
        vendor_drop=False,
        pause_at=None,
        paused=asyncio.Event(),
        resume=asyncio.Event(),
    )

    async def pause_at(boundary: str) -> None:
        if state.pause_at == boundary:
            state.paused.set()
            await state.resume.wait()

    async def health(_client: HaPaneldClient) -> PanelHealth:
        if (tmp_path / "home_ack").exists():
            state.health = replace(state.health, home_pending=False)
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
                async_list=AsyncMock(return_value=[]),
                async_find_active=AsyncMock(
                    side_effect=lambda *_args: (
                        object() if state.active_install else None
                    )
                ),
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
            await pause_at("connect")
            return True

        async def close(self) -> None:
            state.closes += 1

        async def streaming_shell(self, command: str, **_kwargs: Any) -> Any:
            match = re.search(r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", command)
            assert match is not None
            if "HAPANELD_POSTURE_BEGIN" in command:
                yield _identity_root_output(match[1], serial=state.serial)
                return
            if "HAPANELD_PACKAGE_BEGIN" in command:
                await pause_at("package")
                yield _single_output(
                    "PACKAGE", match[1], ["package:/data/app/panel/base.apk"], 0
                )
                return
            if (
                "HAPANELD_PERMISSIONS_BEGIN" in command
                or "HAPANELD_NOTIFICATIONS_BEGIN" in command
            ):
                if "HAPANELD_NOTIFICATIONS_BEGIN" in command:
                    await pause_at("notifications")
                yield await asyncio.to_thread(
                    _run_on_stand_in_panel,
                    command.replace(CHOOSER_DUMP, str(tmp_path / "chooser-dump.xml")),
                    tmp_path,
                )
                return
            if "HAPANELD_VENDOR_BEGIN" in command:
                state.vendor_passes += 1
                if state.vendor_drop:
                    raise TimeoutError
                yield _single_output("VENDOR", match[1], ["device:other"], 0)
                return
            program = (
                r"""
cmd() { if [ -f "$STATE/home" ]; then cat "$STATE/home"; fi; }
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
        state=state,
        entry=entry,
        coordinator=coordinator,
        directory=tmp_path,
        hass=hass,
    )


async def _pass(panel: Any, coordinator: Any = None) -> dict[str, Any] | None:
    """One device setup pass, as an install, update or Repair runs it."""
    return await async_attempt(
        panel.hass, panel.entry, coordinator or panel.coordinator, refresh=True
    )


async def test_setup_completion_activates_admin_once_and_closes_adb(panel: Any) -> None:
    assert await _pass(panel) is None
    assert panel.state.connections == 0
    panel.state.health = replace(panel.state.health, setup_complete=True)
    assert await _pass(panel) == {
        "did": DID,
        "package": PACKAGE,
        "version_code": 1,
        "root": False,
    }
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
    assert await _pass(panel) is not None
    await panel.coordinator.async_refresh()
    assert not (panel.directory / "calls").exists()
    assert panel.state.connections == panel.state.closes == 1


async def test_admin_refusal_leaves_the_pass_unfinished_and_connections_close(
    panel: Any,
) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    panel.state.refused = True
    for _ in range(3):
        assert await _pass(panel) is None
    # Polls in between open nothing.
    await panel.coordinator.async_refresh()
    assert panel.state.connections == panel.state.closes == 3
    assert not (panel.directory / "admin").exists()
    assert panel.coordinator.last_update_success


async def test_transient_admin_refusal_converges_on_the_next_attempt(
    panel: Any,
) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    panel.state.refused = True
    assert await _pass(panel) is None
    panel.state.refused = False
    assert await _pass(panel) is not None
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
        assert await _pass(panel) is None
    finally:
        release_panel_operation(hass, panel.entry.entry_id)
    assert not (panel.directory / "admin").exists()
    assert not (panel.directory / "calls").exists()
    assert panel.coordinator.last_update_success


@pytest.mark.parametrize("native_home", [False, True], ids=["shell", "native"])
async def test_completion_pending_home_claims_once_and_handback_survives_ha_reload(
    panel: Any, hass: HomeAssistant, native_home: bool
) -> None:
    if native_home:
        (panel.directory / "native.home").touch()
    previous = "com.vendor.launcher/.Home"
    (panel.directory / "home").write_text(previous + "\n")
    (panel.directory / "incomplete.setup").touch()
    panel.state.health = replace(panel.state.health, home_pending=True)
    await _pass(panel)
    assert panel.state.connections == 0
    assert (panel.directory / "home").read_text().strip() == previous

    (panel.directory / "incomplete.setup").unlink()
    panel.state.health = replace(panel.state.health, setup_complete=True)
    await _pass(panel)
    assert panel.coordinator.last_update_success
    assert (
        panel.directory / "home"
    ).read_text().strip() == f"{PACKAGE}/.DashboardActivity"
    assert (panel.directory / "home_ack").exists()
    assert panel.state.vendor_passes == 0  # Generic hardware needs no vendor recipe.
    await panel.coordinator.async_refresh()
    connections = panel.state.connections
    assert panel.state.closes == connections
    # Native handback has cleared pending intent. A fresh HA coordinator must
    # not turn that explicit choice back into another takeover, and the entry
    # records that this build's setup pass is done, so it opens no ADB at all.
    (panel.directory / "home").write_text(previous + "\n")
    reloaded = HaPaneldDataUpdateCoordinator(
        hass, panel.coordinator.client, panel.entry.entry_id
    )
    await reloaded.async_refresh()
    await reloaded.async_refresh()
    assert reloaded.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert panel.state.vendor_passes == 0
    assert panel.state.connections == panel.state.closes == connections


@pytest.mark.parametrize("boundary", ["connect", "package", "notifications"])
async def test_delayed_setup_completion_preserves_later_owner_home_handback(
    panel: Any, hass: HomeAssistant, boundary: str
) -> None:
    """Run the coordinator and its shell; Android and Configure are stand-ins."""
    previous = "com.vendor.launcher/.Home"
    own = f"{PACKAGE}/.DashboardActivity"
    (panel.directory / "home").write_text(previous + "\n")
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    panel.state.pause_at = boundary
    refresh = asyncio.create_task(_pass(panel))
    try:
        async with asyncio.timeout(5):
            await panel.state.paused.wait()
        # The real coordinator is already acting on completed/pending health.
        # Simulate native completion followed by successful Configure handback
        # while its ADB operation is suspended. Native/controller behavior is
        # covered in the app tests; these writes are not native execution proof.
        assert panel.state.connections == 1
        (panel.directory / "home").write_text(own + "\n")
        (panel.directory / "home_ack").touch()
        panel.state.health = replace(panel.state.health, home_pending=False)
        (panel.directory / "home").write_text(previous + "\n")
    finally:
        panel.state.resume.set()
        await refresh

    assert panel.coordinator.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert (panel.directory / "admin").exists()
    assert panel.state.vendor_passes == 0
    assert panel.state.connections == panel.state.closes == 1
    assert not any(
        line.startswith("cmd package set-home-activity")
        for line in (panel.directory / "calls").read_text().splitlines()
    )

    # Polling and HA reload preserve the owner's choice after the delayed work
    # returns, as well as at the instant it resumes: the finished pass is
    # recorded, so neither opens ADB again.
    await panel.coordinator.async_refresh()
    reloaded = HaPaneldDataUpdateCoordinator(
        hass, panel.coordinator.client, panel.entry.entry_id
    )
    await reloaded.async_refresh()
    assert reloaded.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert panel.state.vendor_passes == 0
    assert panel.state.connections == panel.state.closes == 1


async def test_owner_handback_after_native_claim_receipt_prevents_shell_reclaim(
    panel: Any,
) -> None:
    """A successful native receipt must not authorize a later shell takeover."""
    previous = "com.vendor.launcher/.Home"
    (panel.directory / "home").write_text(previous + "\n")
    (panel.directory / "native.home").touch()
    (panel.directory / "prop.ro.product.device").write_text("omni\n")
    (panel.directory / "handback.after.home.authority").write_text(previous + "\n")
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )

    await _pass(panel)

    assert (panel.directory / "handback.done").exists()
    assert panel.coordinator.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert panel.state.vendor_passes == 0
    assert panel.state.connections == panel.state.closes == 1
    assert not any(
        line.startswith("cmd package set-home-activity")
        for line in (panel.directory / "calls").read_text().splitlines()
    )


@pytest.mark.parametrize("receiver", ["legacy.receiver", "receiver.absent"])
async def test_deferred_setup_never_uses_legacy_home_authority(
    panel: Any, receiver: str
) -> None:
    previous = "com.vendor.launcher/.Home"
    (panel.directory / "home").write_text(previous + "\n")
    (panel.directory / receiver).touch()
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    await _pass(panel)
    assert panel.coordinator.last_update_success
    assert (panel.directory / "admin").exists()
    assert (panel.directory / "home").read_text().strip() == previous
    assert not (panel.directory / "home_ack").exists()


async def test_rootless_setup_rechecks_pending_after_shell_offer(panel: Any) -> None:
    previous = "com.vendor.launcher/.Home"
    (panel.directory / "home").write_text(previous + "\n")
    (panel.directory / "handback.after.shell.offer").write_text(previous + "\n")
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    await _pass(panel)
    assert (panel.directory / "handback.done").exists()
    assert panel.coordinator.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert not any(
        line.startswith("cmd package set-home-activity")
        for line in (panel.directory / "calls").read_text().splitlines()
    )


@pytest.mark.parametrize("handback", [False, True], ids=["still_pending", "revoked"])
async def test_rootless_setup_chooser_uses_current_pending_intent(
    panel: Any, handback: bool
) -> None:
    previous = "com.vendor.launcher/.Home"
    own = f"{PACKAGE}/.DashboardActivity"
    chooser = Path(__file__).parent / "fixtures" / "portal_home_chooser.xml"
    (panel.directory / "home").write_text(
        "android/com.android.internal.app.ResolverActivity\n"
    )
    (panel.directory / "refuse.home").touch()
    (panel.directory / "chooser.xml").write_text(chooser.read_text())
    (panel.directory / "chooser.tap").write_text("817 623\n")
    (panel.directory / "chooser.home").write_text(own + "\n")
    if handback:
        (panel.directory / "handback.during.chooser").write_text(previous + "\n")
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    await _pass(panel)
    assert panel.coordinator.last_update_success
    assert (panel.directory / "chooser.shown").exists()
    assert (panel.directory / "home").read_text().strip() == (
        previous if handback else own
    )
    taps = [
        line
        for line in (panel.directory / "calls").read_text().splitlines()
        if line.startswith("input tap")
    ]
    assert taps == ([] if handback else ["input tap 817 623"])


@pytest.mark.parametrize("boundary", ["after.shell.offer", "during.chooser"])
async def test_rootless_setup_rechecks_native_recovery_before_mutation(
    panel: Any, boundary: str
) -> None:
    """A recovered native route withdraws shell authority while still pending."""
    previous = "com.vendor.launcher/.Home"
    own = f"{PACKAGE}/.DashboardActivity"
    if boundary == "during.chooser":
        previous = "android/com.android.internal.app.ResolverActivity"
        chooser = Path(__file__).parent / "fixtures" / "portal_home_chooser.xml"
        (panel.directory / "refuse.home").touch()
        (panel.directory / "chooser.xml").write_text(chooser.read_text())
        (panel.directory / "chooser.tap").write_text("817 623\n")
        (panel.directory / "chooser.home").write_text(own + "\n")
    (panel.directory / "home").write_text(previous + "\n")
    (panel.directory / f"native.recover.{boundary}").touch()
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )

    await _pass(panel)

    assert panel.coordinator.last_update_success
    assert (panel.directory / "native.home").exists()
    assert (panel.directory / "home").read_text().strip() == previous
    assert not (panel.directory / "home_ack").exists()
    mutations = [
        line
        for line in (panel.directory / "calls").read_text().splitlines()
        if line.startswith(("cmd package set-home-activity", "input tap"))
    ]
    # The refused direct attempt precedes recovery during chooser observation.
    assert mutations == (
        [f"cmd package set-home-activity {PACKAGE}/{PACKAGE}.DashboardActivity"]
        if boundary == "during.chooser"
        else []
    )
    assert panel.state.connections == panel.state.closes == 1

    await _pass(panel)
    assert panel.coordinator.last_update_success
    assert (panel.directory / "home").read_text().strip() == own
    assert (panel.directory / "home_ack").exists()
    assert panel.state.connections == panel.state.closes == 2


def _try_handback_on_kernel_peer(directory: Path, component: str) -> bool:
    """A separate OS peer for the native protocol; actual JNI has app tests.

    The kernel exclusion is real. Android HOME and Configure remain stand-ins,
    so this is PA caller proof, not an installed native handback claim.
    """
    result = subprocess.run(
        [
            "/bin/sh",
            "-c",
            'exec 8< "$STATE/home-lock" || exit 74; '
            "flock -xn 8 || exit 75; "
            'printf "%s\\n" "$1" > "$STATE/home"',
            "handback-peer",
            component,
        ],
        env={"PATH": os.environ["PATH"], "STATE": str(directory)},
        capture_output=True,
        timeout=5,
    )
    assert result.returncode in (0, 75), result.stderr
    return result.returncode == 0


@pytest.mark.parametrize("boundary", ["set-home", "chooser-tap"])
async def test_rootless_final_mutation_excludes_handback_until_shell_finishes(
    panel: Any, hass: HomeAssistant, boundary: str
) -> None:
    """Real coordinator/shell/flock; recovered native and HOME are OS peers."""
    previous = "com.vendor.launcher/.Home"
    own = f"{PACKAGE}/.DashboardActivity"
    (panel.directory / "home").write_text(previous + "\n")
    if boundary == "chooser-tap":
        chooser = Path(__file__).parent / "fixtures" / "portal_home_chooser.xml"
        (panel.directory / "home").write_text(
            "android/com.android.internal.app.ResolverActivity\n"
        )
        (panel.directory / "refuse.home").touch()
        (panel.directory / "chooser.xml").write_text(chooser.read_text())
        (panel.directory / "chooser.tap").write_text("817 623\n")
        (panel.directory / "chooser.home").write_text(own + "\n")
    (panel.directory / f"pause.{boundary}").touch()
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    refresh = asyncio.create_task(_pass(panel))
    try:
        async with asyncio.timeout(20):
            # This barrier is published by an OS shell, not an asyncio task.
            while not (panel.directory / f"paused.{boundary}").exists():  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        # Capability recovers after the real final eligibility check. Native
        # setup acknowledges its claim, then Configure durably clears intent.
        (panel.directory / "native.home").touch()
        (panel.directory / "home").write_text(own + "\n")
        (panel.directory / "home_ack").touch()
        panel.state.health = replace(panel.state.health, home_pending=False)
        assert not await asyncio.to_thread(
            _try_handback_on_kernel_peer, panel.directory, previous
        ), "handback reported success while an admitted shell can still mutate"
        assert (panel.directory / "home").read_text().strip() == own
        # cmd passes stdin into Android's Binder shellCommand transaction. Its
        # descriptor must refer to the lock inode, not merely the parent shell.
        identity = (panel.directory / "home-lock").stat()
        assert (panel.directory / "home.stdin").read_text().strip() == (
            f"{identity.st_dev}:{identity.st_ino}"
        )
    finally:
        (panel.directory / f"resume.{boundary}").touch()
        await refresh

    assert panel.coordinator.last_update_success
    assert panel.state.connections == panel.state.closes == 1
    assert await asyncio.to_thread(
        _try_handback_on_kernel_peer, panel.directory, previous
    ), "owner retry must succeed after the old shell exits"
    assert (panel.directory / "home").read_text().strip() == previous
    await panel.coordinator.async_refresh()
    reloaded = HaPaneldDataUpdateCoordinator(
        hass, panel.coordinator.client, panel.entry.entry_id
    )
    await reloaded.async_refresh()
    assert reloaded.last_update_success
    assert (panel.directory / "home").read_text().strip() == previous
    assert panel.state.connections == panel.state.closes


@pytest.mark.parametrize(
    "unavailable",
    ["busy", "flock.unavailable", "lock.identity.mismatch", "legacy.lock.receiver"],
)
async def test_rootless_setup_defers_without_shared_exclusion_and_recovers(
    panel: Any, unavailable: str
) -> None:
    previous = "com.vendor.launcher/.Home"
    (panel.directory / "home").write_text(previous + "\n")
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=True
    )
    lock_directory = panel.directory / "home-lock"
    lock_directory.mkdir()
    descriptor = os.open(lock_directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if unavailable == "busy":
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            (panel.directory / unavailable).touch()
        await _pass(panel)
        assert panel.coordinator.last_update_success
        assert (panel.directory / "home").read_text().strip() == previous
        assert not (panel.directory / "home_ack").exists()
        assert (panel.directory / "admin").exists()
        assert panel.state.connections == panel.state.closes == 1
        assert not any(
            line.startswith(("cmd package set-home-activity", "input tap"))
            for line in (panel.directory / "calls").read_text().splitlines()
        )
    finally:
        os.close(descriptor)
        if unavailable != "busy":
            (panel.directory / unavailable).unlink()

    await _pass(panel)
    assert panel.coordinator.last_update_success
    assert (panel.directory / "home").read_text().strip() == (
        f"{PACKAGE}/.DashboardActivity"
    )
    assert (panel.directory / "home_ack").exists()
    assert panel.state.connections == panel.state.closes == 2


async def test_native_completed_home_receives_vendor_pass_without_reclaiming_home(
    panel: Any,
) -> None:
    panel.state.health = replace(
        panel.state.health, setup_complete=True, home_pending=False
    )
    home = f"{PACKAGE}/.DashboardActivity"
    (panel.directory / "home").write_text(home + "\n")
    await _pass(panel)
    await panel.coordinator.async_refresh()
    assert panel.coordinator.last_update_success
    assert panel.state.vendor_passes == 1
    assert (panel.directory / "home").read_text().strip() == home
    assert panel.state.connections == panel.state.closes == 1


async def test_vendor_transport_drop_retries_then_finishes_completed_setup(
    panel: Any,
) -> None:
    panel.state.health = replace(panel.state.health, setup_complete=True)
    (panel.directory / "home").write_text(f"{PACKAGE}/.DashboardActivity\n")
    panel.state.vendor_drop = True
    # Admin readback alone cannot finish a pass whose vendor I/O failed.
    assert await _pass(panel) is None
    assert panel.coordinator.last_update_success
    assert panel.state.vendor_passes == 1
    panel.state.vendor_drop = False
    # The poll never retries it; the operation's next attempt does.
    await panel.coordinator.async_refresh()
    assert panel.state.vendor_passes == 1
    assert await _pass(panel) is not None
    assert panel.coordinator.last_update_success
    assert panel.state.vendor_passes == 2
    assert panel.state.connections == panel.state.closes == 2
    assert (panel.directory / "calls").read_text().splitlines() == [
        f"set-active-admin {PACKAGE}/{PACKAGE}.platform.PanelDeviceAdminReceiver"
    ]


async def test_completed_setup_uses_local_shell_without_network_adb(
    panel: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary commissioning caller works while port 5555 is closed."""
    from custom_components.panel_assistant import local_adb
    from custom_components.panel_assistant.adb_protocol import AdbInstallTarget

    device = adb_protocol.AdbDeviceAsync(None)
    target = AdbInstallTarget(
        panel.coordinator.client.address,
        "SERIAL-1",
        "Test Panel",
        "arm64-v8a",
        34,
        local_shell=device,
    )
    monkeypatch.setattr(local_adb, "async_target", AsyncMock(return_value=target))
    monkeypatch.setattr(
        adb_credentials,
        "async_get_durable_adb_credential",
        AsyncMock(
            side_effect=AssertionError(
                "local commissioning must not require an ADB key"
            )
        ),
    )
    panel.state.health = replace(panel.state.health, setup_complete=True)
    await _pass(panel)
    assert (panel.directory / "admin").exists()
    assert panel.state.connections == 0

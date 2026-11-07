"""Slow and stalled FileSync copies through the real in-place update caller."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import AdbConnectionError, AdbTimeoutError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_adb import async_update_installed_apk
from custom_components.panel_assistant.release import InstallDescriptor
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)

from .install_adb_fakes import (
    NONCES,
    FakeDevice,
    _allow_install,
    _cleanup_output,
    _identity_root_output,
    _install_update_fake,
    _installed_package_output,
    _preflight_output,
    _remote_output,
    _single_output,
    _write_private_apk,
    descriptor,
    signer,
    target,
)

__all__ = ["descriptor", "signer", "target"]


@pytest.fixture
async def advance(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:
    """Advance the real loop's deadlines without waiting minutes per copy."""
    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0.0

    def tick(seconds: float) -> None:
        nonlocal offset
        offset += seconds

    monkeypatch.setattr(loop, "time", lambda: clock() + offset)
    return tick


class StreamingDevice(FakeDevice):
    """Read the actual caller-owned file and accept chunks at a measured rate."""

    def __init__(
        self,
        descriptor: InstallDescriptor,
        advance: Callable[[float], None],
        *,
        failure: str | None = None,
        chunk: int = 175 * 1024,
    ) -> None:
        installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
        outputs = [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
        ]
        if failure is None:
            remote = {"size": descriptor.apk_size, "sha256": descriptor.apk_sha256}
            outputs += [
                _remote_output(NONCES[3], **remote),
                _preflight_output(NONCES[4], **installed),
                _installed_package_output(NONCES[5]),
                _remote_output(NONCES[6], **remote),
                _single_output("INSTALL", NONCES[7], ["Success"], 0),
                _cleanup_output(NONCES[8]),
            ]
        else:
            outputs += [
                _identity_root_output(NONCES[3]),
                _cleanup_output(NONCES[4]),
            ]
        super().__init__(outputs)
        self.advance = advance
        self.failure = failure
        self.chunk = chunk
        self.received = bytearray()
        self.copy_seconds = 0.0

    async def tick(self, seconds: float) -> None:
        self.copy_seconds += seconds
        self.advance(seconds)
        # Let due timeout handles cancel the task before another progress report.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def push(self, *args: Any, **kwargs: Any) -> None:
        self.pushes.append((args, kwargs))
        body = await asyncio.to_thread(Path(args[0]).read_bytes)
        for offset in range(0, len(body), self.chunk):
            await self.tick(5)
            data = body[offset : offset + self.chunk]
            self.received.extend(data)
            callback = kwargs.get("progress_callback")
            if callback is not None:
                # adb-shell 0.4.4 sends chunk lengths, including the last short one.
                await callback(args[1], len(data), len(body))
            if self.failure == "no_progress":
                await self.tick(31)
                while True:
                    await self.tick(5)
            if self.failure == "packet_timeout":
                raise AdbTimeoutError("packet read timed out")
            if self.failure == "connection_closed":
                raise AdbConnectionError("peer closed connection")


def apk_fixture(
    tmp_path: Path, descriptor: InstallDescriptor
) -> tuple[Path, InstallDescriptor]:
    body = b"A" * 15_550_090
    apk = tmp_path / "release.apk"
    _write_private_apk(apk, body)
    return apk, replace(
        descriptor, apk_size=len(body), apk_sha256=hashlib.sha256(body).hexdigest()
    )


async def test_steady_slow_update_finishes_beyond_fixed_copy_and_update_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    advance: Callable[[float], None],
) -> None:
    apk, descriptor = apk_fixture(tmp_path, descriptor)
    device = StreamingDevice(descriptor, advance)
    _install_update_fake(monkeypatch, device)
    outcome = await async_update_installed_apk(
        target, signer, descriptor, apk, before_install=_allow_install
    )
    assert outcome is InstallOutcome.INSTALLED
    assert device.copy_seconds > 420  # Also exceeds the old 180-second stage budget.
    assert device.received == apk.read_bytes()
    assert any("pm install -r " in command for command in device.commands)
    assert "rm -f " in device.commands[-1]
    assert device.closed


@pytest.mark.parametrize(
    "reason", ["no_progress", "packet_timeout", "connection_closed"]
)
async def test_stalled_update_names_transfer_cause_and_never_installs(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    advance: Callable[[float], None],
    caplog: pytest.LogCaptureFixture,
    reason: str,
) -> None:
    apk, descriptor = apk_fixture(tmp_path, descriptor)
    device = StreamingDevice(descriptor, advance, failure=reason)
    _install_update_fake(monkeypatch, device)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.STAGE_AMBIGUOUS
    assert reason in str(caught.value)
    assert caught.value.__cause__ is not None
    assert reason in caplog.text
    assert "179200/15550090 bytes" in caplog.text
    assert all("pm install" not in command for command in device.commands)
    assert "rm -f " in device.commands[-1]
    assert device.closed
    entry = MockConfigEntry(
        domain=DOMAIN, title="Kitchen", data={CONF_ADDRESS: "panel.local"}
    )
    entry.add_to_hass(hass)
    # The update service wraps this exact caller failure before recording it.
    error = HomeAssistantError("The panel update did not complete")
    error.__cause__ = caught.value
    await async_record_update_failure(hass, entry.entry_id, entry.title, "1.0.0", error)
    events = await async_failure_events(
        hass, panel_failure_issue_id(f"update:{entry.entry_id}")
    )
    assert "transfer" in events[-1]
    assert events[-1]["transfer"]["reason"] == reason
    assert events[-1]["transfer"]["bytes_sent"] == len(device.received)
    assert reason in events[-1]["exception"]


async def test_trickling_update_cannot_extend_size_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    advance: Callable[[float], None],
) -> None:
    apk, descriptor = apk_fixture(tmp_path, descriptor)
    # It reports progress every five seconds forever, but cannot finish in budget.
    device = StreamingDevice(descriptor, advance, failure="ceiling", chunk=1024)
    _install_update_fake(monkeypatch, device)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert "stage_timeout" in str(caught.value)
    assert 1100 < device.copy_seconds < 1150
    assert all("pm install" not in command for command in device.commands)
    assert "rm -f " in device.commands[-1]
    assert device.closed

"""A fake panel for the ADB install primitives: its framed answers and device.

Shared by every test that drives ``install_adb`` through its real command
builders and parsers: the fixtures, the canned output of each framed command,
and a device that replays those outputs in order while recording what it was
sent.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from custom_components.panel_assistant import install_adb
from custom_components.panel_assistant.install_adb import (
    StagedApk,
)

NONCES = tuple(f"{number:032x}" for number in range(1, 20))
JOB_ID = "a" * 32
REMOTE_PATH = f"/data/local/tmp/ha-paneld-install-{JOB_ID}.apk"
APK_BYTES = b"signed apk fixture"
APK_SHA256 = hashlib.sha256(APK_BYTES).hexdigest()


async def _allow_install() -> None:
    """Admit the already-selected fixture in the transport-only tests."""


def _staged() -> StagedApk:
    return StagedApk(
        job_id=JOB_ID,
        remote_path=REMOTE_PATH,
        apk_size=len(APK_BYTES),
        apk_sha256=APK_SHA256,
    )


def _write_private_apk(path: Path, body: bytes = APK_BYTES) -> None:
    path.write_bytes(body)
    path.chmod(0o600)


def _section(
    prefix: str, name: str, nonce: str, lines: list[str], status: int
) -> list[str]:
    return [
        f"HAPANELD_{prefix}_{name}_BEGIN:{nonce}",
        *lines,
        f"HAPANELD_{prefix}_{name}_END:{nonce}:{status}",
    ]


def _identity_sections(
    prefix: str,
    nonce: str,
    *,
    model: str = "Test Panel",
    serial: str = "SERIAL-1",
    abi: str = "arm64-v8a",
    sdk: int = 34,
) -> list[str]:
    values = (("MODEL", model), ("SERIAL", serial), ("ABI", abi), ("SDK", str(sdk)))
    lines: list[str] = []
    for name, value in values:
        lines.extend(_section(prefix, name, nonce, [value], 0))
    return lines


def _preflight_output(
    nonce: str,
    *,
    model: str = "Test Panel",
    serial: str = "SERIAL-1",
    abi: str = "arm64-v8a",
    sdk: int = 34,
    uid: str = "2000",
    secure: str = "1",
    debuggable: str = "0",
    su_lines: list[str] | None = None,
    su_status: int = 0,
    package_lines: list[str] | None = None,
    retained_lines: list[str] | None = None,
    successor_package_lines: list[str] | None = None,
    successor_retained_lines: list[str] | None = None,
    unreadable_base: int | None = None,
    residue_index: int | None = None,
) -> bytes:
    prefix = "PREFLIGHT"
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    lines.extend(
        _identity_sections(prefix, nonce, model=model, serial=serial, abi=abi, sdk=sdk)
    )
    lines.extend(_section(prefix, "UID", nonce, [uid], 0))
    lines.extend(_section(prefix, "SECURE", nonce, [secure], 0))
    lines.extend(_section(prefix, "DEBUGGABLE", nonce, [debuggable], 0))
    lines.extend(
        _section(
            prefix,
            "SU",
            nonce,
            ["absent"] if su_lines is None else su_lines,
            su_status,
        )
    )
    lines.extend(
        _section(
            prefix,
            "LIVE",
            nonce,
            ["package:/system/framework/framework-res.apk"],
            0,
        )
    )
    # Every accepted application id is observed in its own pair of sections,
    # legacy first, exactly as the command emits them.
    for index, (paths, retained) in enumerate(
        (
            (package_lines, retained_lines),
            (successor_package_lines, successor_retained_lines),
        )
    ):
        lines.extend(_section(prefix, f"PACKAGE{index}", nonce, paths or [], 1))
        lines.extend(_section(prefix, f"RETAINED{index}", nonce, retained or [], 0))
    for index in range(len(install_adb._ROOT_DATA_BASES)):
        state = "unreadable" if index == unreadable_base else "readable"
        lines.extend(_section(prefix, f"BASE{index}", nonce, [state], 0))
    for index in range(len(install_adb._RESIDUE_PATHS)):
        state = "present" if index == residue_index else "absent"
        lines.extend(_section(prefix, f"RESIDUE{index}", nonce, [state], 0))
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _identity_root_output(
    nonce: str,
    *,
    serial: str = "SERIAL-1",
    model: str = "Test Panel",
    abi: str = "arm64-v8a",
    sdk: int = 34,
    uid: str = "2000",
    secure: str = "1",
    debuggable: str = "0",
    su_lines: list[str] | None = None,
    su_status: int = 0,
) -> bytes:
    prefix = "POSTURE"
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    lines.extend(
        _identity_sections(prefix, nonce, serial=serial, model=model, abi=abi, sdk=sdk)
    )
    lines.extend(_section(prefix, "UID", nonce, [uid], 0))
    lines.extend(_section(prefix, "SECURE", nonce, [secure], 0))
    lines.extend(_section(prefix, "DEBUGGABLE", nonce, [debuggable], 0))
    lines.extend(
        _section(
            prefix,
            "SU",
            nonce,
            ["absent"] if su_lines is None else su_lines,
            su_status,
        )
    )
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _single_output(prefix: str, nonce: str, lines: list[str], status: int) -> bytes:
    return (
        "\n".join(
            [
                f"HAPANELD_{prefix}_BEGIN:{nonce}",
                *lines,
                f"HAPANELD_{prefix}_END:{nonce}:{status}",
            ]
        )
        + "\n"
    ).encode()


_NOTIFICATIONS_GRANTED_LINE = (
    "      android.permission.POST_NOTIFICATIONS: granted=true, "
    "flags=[ USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED]"
)


def _notifications_output(nonce: str, *, granted: bool = True) -> bytes:
    line = _NOTIFICATIONS_GRANTED_LINE.replace(
        "granted=true", "granted=true" if granted else "granted=false"
    )
    return _single_output("NOTIFICATIONS", nonce, [line], 0)


_ACCESSIBILITY_SERVICES = {
    "io.github.maxlyth.hapaneld": (
        "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService"
    ),
    "io.panelassistant.android": (
        "io.panelassistant.android/"
        "io.github.maxlyth.hapaneld.input.PanelAccessibilityService"
    ),
}


def _permissions_output(
    nonce: str, package_id: str = "io.github.maxlyth.hapaneld"
) -> bytes:
    """A fresh panel's readback after every grant took."""
    return _single_output(
        "PERMISSIONS",
        nonce,
        [
            "null",
            _ACCESSIBILITY_SERVICES[package_id],
            "1",
            "WRITE_SETTINGS: allow",
            "SYSTEM_ALERT_WINDOW: allow\nREQUEST_INSTALL_PACKAGES: allow\n"
            "unsupported\nunsupported\n"
            "home_chooser:kept\nsecure_settings:granted\nhome:"
            f"{install_adb.HOME_COMPONENTS[install_adb.LAUNCH_COMPONENTS[package_id][0]]}"
            "\ndevice:generic",
        ],
        0,
    )


def _remote_output(
    nonce: str,
    *,
    size: int = len(APK_BYTES),
    sha256: str = APK_SHA256,
    mode: str = "81a4",
) -> bytes:
    prefix = "ARTIFACT"
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    lines.extend(_section(prefix, "CHMOD", nonce, [], 0))
    lines.extend(_section(prefix, "MODE", nonce, [mode], 0))
    lines.extend(_section(prefix, "SIZE", nonce, [str(size)], 0))
    lines.extend(_section(prefix, "SHA", nonce, [f"{sha256}  {REMOTE_PATH}"], 0))
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _cleanup_output(nonce: str) -> bytes:
    prefix = "CLEANUP"
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    lines.extend(_section(prefix, "RM", nonce, [], 0))
    lines.extend(_section(prefix, "STATE", nonce, ["absent"], 0))
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _installed_package_output(nonce: str) -> bytes:
    return _single_output("PACKAGE", nonce, ["package:/data/app/ha-paneld/base.apk"], 0)


def _su_output(
    nonce: str,
    *,
    clean: bool = True,
    uid: str = "0",
    unreadable_base: int | None = None,
    residue_index: int | None = None,
) -> bytes:
    lines = [f"HAPANELD_DELEGATE_BEGIN:{nonce}", f"HAPANELD_SU_BEGIN:{nonce}"]
    lines.extend(_section("SU", "UID", nonce, [uid], 0))
    if clean:
        for index in range(len(install_adb._ROOT_DATA_BASES)):
            value = "unreadable" if index == unreadable_base else "readable"
            lines.extend(_section("SU", f"BASE{index}", nonce, [value], 0))
        for index in range(len(install_adb._RESIDUE_PATHS)):
            value = "present" if index == residue_index else "absent"
            lines.extend(_section("SU", f"RESIDUE{index}", nonce, [value], 0))
    lines.extend([f"HAPANELD_SU_END:{nonce}", f"HAPANELD_DELEGATE_END:{nonce}:0"])
    return ("\n".join(lines) + "\n").encode()


class FakeDevice:
    def __init__(
        self,
        outputs: list[bytes | BaseException],
        *,
        maxdata: object = 64 * 1024,
        connect_error: BaseException | None = None,
        push_error: BaseException | None = None,
        prompt_for_authorization: bool = False,
    ) -> None:
        self.outputs = iter(outputs)
        self._maxdata = maxdata
        self.connect_error = connect_error
        self.push_error = push_error
        self.prompt_for_authorization = prompt_for_authorization
        self.constructor: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self.connect_kwargs: dict[str, Any] | None = None
        self.commands: list[str] = []
        self.shell_kwargs: list[dict[str, Any]] = []
        self.pushes: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.closed = False

    async def connect(self, **kwargs: Any) -> bool:
        self.connect_kwargs = kwargs
        if self.prompt_for_authorization:
            kwargs["auth_callback"](self)
        if self.connect_error is not None:
            raise self.connect_error
        return True

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        self.commands.append(command)
        self.shell_kwargs.append(kwargs)
        output = next(self.outputs)
        if isinstance(output, BaseException):
            raise output
        yield output

    async def push(self, *args: Any, **kwargs: Any) -> None:
        self.pushes.append((args, kwargs))
        if self.push_error is not None:
            raise self.push_error

    async def close(self) -> None:
        self.closed = True


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch, devices: list[FakeDevice]
) -> Iterator[FakeDevice]:
    device_iterator = iter(devices)
    nonces = iter(NONCES)
    monkeypatch.setattr(install_adb, "token_hex", lambda _length: next(nonces))

    def factory(*args: Any, **kwargs: Any) -> FakeDevice:
        device = next(device_iterator)
        device.constructor = (args, kwargs)
        return device

    monkeypatch.setattr(install_adb, "AdbDeviceAsync", factory)
    return device_iterator


def _install_update_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeDevice) -> None:
    _install_fakes(monkeypatch, [fake])
    nonces = iter((JOB_ID, *NONCES))
    monkeypatch.setattr(install_adb, "token_hex", lambda _length: next(nonces))

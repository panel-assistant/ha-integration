"""Launch-time grants a fresh network install makes before the app first starts."""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from custom_components.panel_assistant import install_adb
from custom_components.panel_assistant.app_identity import LEGACY_PACKAGE_ID
from custom_components.panel_assistant.install_adb import (
    AdbInstallTarget,
    AdbRootMode,
    LaunchOutcome,
    async_launch_installed_app,
)
from custom_components.panel_assistant.release import InstallDescriptor

from .test_install_adb import (  # noqa: F401 - fixtures resolve by parameter name
    _ACCESSIBILITY_SERVICES,
    _NOTIFICATIONS_GRANTED_LINE,
    NONCES,
    FakeDevice,
    _identity_root_output,
    _install_fakes,
    _notifications_output,
    _permissions_output,
    _single_output,
    descriptor,
    signer,
    successor_descriptor,
    target,
)

_STAND_IN_PANEL = r"""
settings() {
  if [ "$1" = get ]; then
    if [ -f "$STATE/$3" ]; then cat "$STATE/$3"; else echo null; fi
    if [ -f "$STATE/race.$3" ]; then mv "$STATE/race.$3" "$STATE/$3"; fi
  else
    echo "settings $*" >> "$STATE/calls"
    [ -f "$STATE/refuse.$3" ] || printf '%s\n' "$4" > "$STATE/$3"
  fi
}
appops() {
  if [ "$1" = set ]; then
    echo "appops $*" >> "$STATE/calls"
    [ -f "$STATE/refuse.$3" ] || echo "$4" > "$STATE/appop.$2.$3"
  elif [ -f "$STATE/appop.$2.$3" ]; then
    echo "$3: $(cat "$STATE/appop.$2.$3"); time=+2s12ms ago"
  else
    echo "$3: default"
  fi
}
"""


def _run_on_stand_in_panel(command: str, state: Path) -> bytes:
    return subprocess.run(
        ["/bin/sh", "-c", _STAND_IN_PANEL + command],
        capture_output=True,
        check=True,
        env={"PATH": os.environ["PATH"], "STATE": str(state)},
    ).stdout


def _panel_value(state: Path, name: str) -> str | None:
    path = state / name
    return path.read_text().strip() if path.exists() else None


class _RoutingFakeDevice(FakeDevice):
    """Answers each command by the section it opens, so a test can observe order.

    A fake that answers by position fails a changed sequence with a parse error;
    this one lets the observed command list itself be the assertion.
    """

    def __init__(self, state: Path, *, sdk: int = 34) -> None:
        super().__init__([])
        self.sdk = sdk
        self.state = state

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        self.commands.append(command)
        self.shell_kwargs.append(kwargs)
        match = re.search(r"HAPANELD_([A-Z]+)_BEGIN:([0-9a-f]{32})", command)
        assert match is not None, command
        prefix, nonce = match.groups()
        if prefix == "POSTURE":
            yield _identity_root_output(nonce, sdk=self.sdk)
        elif prefix == "PACKAGE":
            yield _single_output(
                "PACKAGE", nonce, ["package:/data/app/ha-paneld/base.apk"], 0
            )
        elif prefix == "NOTIFICATIONS":
            yield _notifications_output(nonce)
        elif prefix == "PERMISSIONS":
            yield _run_on_stand_in_panel(command, self.state)
        else:
            assert prefix == "LAUNCH", command
            yield _single_output("LAUNCH", nonce, ["Status: ok"], 0)


async def test_launch_grants_notifications_before_the_first_start_from_android_13(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    fake = _RoutingFakeDevice(tmp_path)
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert outcome is LaunchOutcome.STARTED
    grants = [
        index
        for index, command in enumerate(fake.commands)
        if "pm grant io.github.maxlyth.hapaneld android.permission.POST_NOTIFICATIONS"
        in command
    ]
    starts = [
        index for index, command in enumerate(fake.commands) if "am start" in command
    ]
    assert grants == [2]
    assert starts == [4]
    assert "dumpsys package io.github.maxlyth.hapaneld" in fake.commands[2]
    assert "notification permission" not in caplog.text


async def test_launch_below_android_13_grants_nothing(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    fake = _RoutingFakeDevice(tmp_path, sdk=32)
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        replace(target, android_sdk=32),
        signer,
        descriptor,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )

    assert outcome is LaunchOutcome.STARTED
    assert len(fake.commands) == 4
    assert not any("POST_NOTIFICATIONS" in command for command in fake.commands)


@pytest.mark.parametrize(
    "notifications",
    [
        _notifications_output(NONCES[2], granted=False),
        _single_output("NOTIFICATIONS", NONCES[2], [], 1),
        _single_output("NOTIFICATIONS", NONCES[2], ["unexpected"], 0),
        _single_output(
            "NOTIFICATIONS",
            NONCES[2],
            [
                _NOTIFICATIONS_GRANTED_LINE,
                _NOTIFICATIONS_GRANTED_LINE.replace("true", "false"),
            ],
            0,
        ),
        b"garbage\n",
    ],
)
async def test_a_refused_or_unreadable_grant_is_reported_and_never_stops_the_start(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    notifications: bytes,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            notifications,
            _permissions_output(NONCES[3]),
            _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert outcome is LaunchOutcome.STARTED
    assert "am start" in fake.commands[4]
    assert [
        record.levelno
        for record in caplog.records
        if "did not grant io.github.maxlyth.hapaneld the notification permission"
        in record.getMessage()
    ] == [logging.WARNING]


async def test_a_grant_over_a_persons_denial_reads_back_as_granted(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Android 14 keeps the person's flags after a shell grant; granted=true decides.
    overridden = _NOTIFICATIONS_GRANTED_LINE.replace(
        "flags=[ ", "flags=[ USER_SET|USER_FIXED|"
    )
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _single_output("NOTIFICATIONS", NONCES[2], [overridden], 0),
            _permissions_output(NONCES[3]),
            _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert outcome is LaunchOutcome.STARTED
    assert "notification permission" not in caplog.text


def _run_notification_program(flags: str, calls: Path) -> list[str]:
    """Run the real grant program against stand-in `dumpsys` and `pm`.

    `pm` records to a file: the program itself discards the grant's output.
    """
    program = "\n".join(
        (
            "dumpsys() { echo '    android.permission.POST_NOTIFICATIONS';"
            " echo '      android.permission.POST_NOTIFICATIONS: granted=false,"
            f" flags=[ {flags}]'; }}",
            f'pm() {{ echo "pm $*" >> {shlex.quote(str(calls))}; }}',
            f": > {shlex.quote(str(calls))}",
            install_adb._notification_grant_command(
                NONCES[0], "io.github.maxlyth.hapaneld"
            ),
        )
    )
    subprocess.run(["/bin/sh", "-c", program], capture_output=True, check=False)
    return calls.read_text().splitlines()


def test_the_grant_program_grants_over_a_persons_denial(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    grant = "pm grant io.github.maxlyth.hapaneld android.permission.POST_NOTIFICATIONS"
    for flags in (
        "USER_SET|USER_SENSITIVE_WHEN_GRANTED",
        "USER_SET|USER_FIXED",
        "USER_FIXED",
        "USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED",
    ):
        assert _run_notification_program(flags, calls) == [grant], flags


_GRANT_WARNING = "every permission it needs"


async def _launch_on(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    installed: InstallDescriptor,
    state: Path,
) -> _RoutingFakeDevice:
    fake = _RoutingFakeDevice(state)
    _install_fakes(monkeypatch, [fake])
    outcome = await async_launch_installed_app(
        target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
    )
    assert outcome is LaunchOutcome.STARTED
    return fake


@pytest.mark.parametrize("identity", ["legacy", "successor"])
async def test_a_fresh_install_gets_the_usb_installers_grants_before_its_first_start(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    identity: str,
) -> None:
    installed = descriptor if identity == "legacy" else successor_descriptor
    package_id = installed.package_id

    fake = await _launch_on(monkeypatch, signer, target, installed, tmp_path)

    assert _panel_value(tmp_path, f"appop.{package_id}.WRITE_SETTINGS") == "allow"
    assert _panel_value(tmp_path, f"appop.{package_id}.SYSTEM_ALERT_WINDOW") == "allow"
    assert (
        _panel_value(tmp_path, "enabled_accessibility_services")
        == (_ACCESSIBILITY_SERVICES[package_id])
    )
    assert _panel_value(tmp_path, "accessibility_enabled") == "1"
    grants = [i for i, c in enumerate(fake.commands) if "appops set" in c]
    starts = [i for i, c in enumerate(fake.commands) if "am start" in c]
    assert len(grants) == 1
    assert grants[0] < starts[0]
    assert _GRANT_WARNING not in caplog.text


async def test_other_apps_accessibility_services_stay_enabled(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    others = "com.example.reader/.ReaderService:org.other/org.other.a11y.Helper"
    (tmp_path / "enabled_accessibility_services").write_text(others + "\n")

    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == (
        f"{others}:{_ACCESSIBILITY_SERVICES[successor_descriptor.package_id]}"
    )
    assert _GRANT_WARNING not in caplog.text


@pytest.mark.parametrize(
    "refused",
    [
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "enabled_accessibility_services",
        "accessibility_enabled",
    ],
)
async def test_a_readback_without_an_allowed_grant_is_reported_and_still_starts(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    refused: str,
) -> None:
    (tmp_path / f"refuse.{refused}").touch()

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert "am start" in fake.commands[-1]
    assert [
        record.levelno
        for record in caplog.records
        if f"did not grant io.github.maxlyth.hapaneld {_GRANT_WARNING}"
        in record.getMessage()
    ] == [logging.WARNING]


async def test_a_service_list_changed_mid_grant_is_left_alone_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "enabled_accessibility_services").write_text(
        "com.example.reader/.ReaderService\n"
    )
    raced = "com.example.reader/.ReaderService:org.other/.Helper"
    (tmp_path / "race.enabled_accessibility_services").write_text(raced + "\n")

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == raced
    assert _GRANT_WARNING in caplog.text


async def test_a_service_list_that_is_not_components_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "enabled_accessibility_services").write_text("not a component\n")

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert "am start" in fake.commands[-1]
    assert _GRANT_WARNING in caplog.text


@pytest.mark.parametrize(
    "already",
    [
        None,
        "io.github.maxlyth.hapaneld/"
        "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
    ],
)
async def test_a_rerun_writes_no_second_copy_of_the_service(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    already: str | None,
) -> None:
    if already is not None:
        (tmp_path / "enabled_accessibility_services").write_text(
            f"com.example.reader/.ReaderService:{already}\n"
        )
    else:
        await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)
    services = _panel_value(tmp_path, "enabled_accessibility_services")
    (tmp_path / "calls").write_text("")

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == services
    assert "enabled_accessibility_services" not in (tmp_path / "calls").read_text()
    assert _panel_value(tmp_path, f"appop.{LEGACY_PACKAGE_ID}.WRITE_SETTINGS") == (
        "allow"
    )
    assert _GRANT_WARNING not in caplog.text

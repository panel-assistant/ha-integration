"""HOME and the Meta Portal's vendor setup on the network install path.

Each test runs the real shell programs against the stand-in panel, through
the same launch and repair calls an install, an update and Repairs make.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from custom_components.panel_assistant import vendor_provisioning
from custom_components.panel_assistant.install_adb import (
    AdbInstallTarget,
    AdbRootMode,
    LaunchOutcome,
    async_launch_installed_app,
    async_repair_installed_app_permissions,
    async_restore_vendor_setup,
)
from custom_components.panel_assistant.release import InstallDescriptor

from .install_adb_fakes import (
    FakeDevice,
    _identity_root_output,
    _install_fakes,
    _single_output,
)
from .test_install_adb import (  # noqa: F401 - fixtures resolve by parameter name
    descriptor,
    signer,
    target,
)
from .test_install_adb_grants import _run_on_stand_in_panel, _seed_granted_panel

_META_COMPONENTS = vendor_provisioning.META_SCREENSAVER_COMPONENTS
_META_DEFAULT = vendor_provisioning.META_SCREENSAVER_DEFAULT_COMPONENT
_CONTROL_CENTER = vendor_provisioning.META_CONTROL_CENTER
_SETUP_WARNING = "Meta Portal setup"
_HOME_WARNING = "its home screen"


class _PanelDevice(FakeDevice):
    """Runs every program the installer sends on the stand-in panel.

    The restore record's fixed path on the panel is moved into the test's
    state directory; nothing else in the program is changed.
    """

    def __init__(self, state: Path) -> None:
        super().__init__([])
        self.state = state

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        self.commands.append(command)
        self.shell_kwargs.append(kwargs)
        match = re.search(r"HAPANELD_([A-Z_]+?)_BEGIN:([0-9a-f]{32})", command)
        assert match is not None, command
        prefix, nonce = match.groups()
        if prefix == "POSTURE":
            yield _identity_root_output(nonce)
        elif prefix == "PACKAGE":
            yield _single_output(
                "PACKAGE", nonce, ["package:/data/app/ha-paneld/base.apk"], 0
            )
        elif prefix == "LAUNCH":
            yield _single_output("LAUNCH", nonce, ["Status: ok"], 0)
        else:
            assert prefix in (
                "NOTIFICATIONS",
                "PERMISSIONS",
                "VENDOR",
                "VENDOR_RESTORE",
            ), command
            yield _run_on_stand_in_panel(
                command.replace(
                    vendor_provisioning.META_RESTORE_RECORD,
                    str(self.state / "restore-record"),
                ),
                self.state,
            )


def _portal(state: Path, *, components: str = _META_COMPONENTS) -> None:
    """A Portal as Meta ships it: both dreams set, the swipe-up bar allowed."""
    (state / "prop.ro.product.device").write_text("omni\n")
    (state / "screensaver_components").write_text(components + "\n")
    (state / "screensaver_default_component").write_text(_META_DEFAULT + "\n")
    (state / f"package.{_CONTROL_CENTER}").touch()
    (state / f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW").write_text("allow\n")


def _calls(state: Path) -> list[str]:
    path = state / "calls"
    return path.read_text().splitlines() if path.exists() else []


def _value(state: Path, name: str) -> str | None:
    path = state / name
    return path.read_text().rstrip("\n") if path.exists() else None


async def _launch(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    state: Path,
) -> _PanelDevice:
    fake = _PanelDevice(state)
    _install_fakes(monkeypatch, [fake])
    outcome = await async_launch_installed_app(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )
    assert outcome is LaunchOutcome.STARTED
    return fake


async def test_any_panel_is_given_the_app_as_home_and_no_meta_setup(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "feature.android.hardware.microphone").write_text("true\n")

    fake = await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    calls = _calls(tmp_path)
    assert (
        "cmd package set-home-activity io.github.maxlyth.hapaneld/.DashboardActivity"
        in calls
    )
    assert _value(tmp_path, "home") == "io.github.maxlyth.hapaneld/.DashboardActivity"
    assert (
        "pm grant io.github.maxlyth.hapaneld android.permission.RECORD_AUDIO" in calls
    )
    assert not any("HAPANELD_VENDOR" in command for command in fake.commands)
    assert not any("screensaver" in call or _CONTROL_CENTER in call for call in calls)
    assert _HOME_WARNING not in caplog.text
    # HOME is claimed before the app starts, so its first start is as HOME.
    assert "am start" in fake.commands[-1]


async def test_a_portal_gets_the_four_steps_read_back_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    (tmp_path / "feature.android.hardware.microphone").write_text("true\n")

    fake = await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    assert _value(tmp_path, "home") == "io.github.maxlyth.hapaneld/.DashboardActivity"
    assert (
        _value(
            tmp_path,
            "permission.io.github.maxlyth.hapaneld.android.permission.RECORD_AUDIO",
        )
        == "true"
    )
    assert _value(tmp_path, "screensaver_components") == ""
    assert _value(tmp_path, "screensaver_default_component") == ""
    assert _value(tmp_path, f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW") == "deny"
    assert _value(tmp_path, "restore-record") == "\n".join(
        (
            f"screensaver_components={_META_COMPONENTS}",
            f"screensaver_default_component={_META_DEFAULT}",
            "controlcenter_overlay=allow",
        )
    )
    vendor = [i for i, c in enumerate(fake.commands) if "HAPANELD_VENDOR_BEGIN" in c]
    launch = [i for i, c in enumerate(fake.commands) if "HAPANELD_LAUNCH_BEGIN" in c]
    assert len(vendor) == 1 and vendor[0] < launch[0]
    assert _SETUP_WARNING not in caplog.text
    assert _HOME_WARNING not in caplog.text


async def test_a_retry_or_update_on_a_portal_writes_nothing_more(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    # A dream the owner chose, not Meta's default, so a record made again
    # from the blanked values would differ from the first.
    _portal(tmp_path, components="com.example.dreams/.Photos")
    _seed_granted_panel(tmp_path, descriptor.package_id, descriptor.launch_component)
    await _launch(monkeypatch, signer, target, descriptor, tmp_path)
    record = _value(tmp_path, "restore-record")
    assert "screensaver_components=com.example.dreams/.Photos" in record
    (tmp_path / "calls").unlink()

    await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    assert _calls(tmp_path) == []
    # The blanked values are never recorded as Meta's.
    assert _value(tmp_path, "restore-record") == record
    assert _SETUP_WARNING not in caplog.text


async def test_an_update_that_dropped_home_has_it_claimed_again(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    _seed_granted_panel(tmp_path, descriptor.package_id, descriptor.launch_component)
    # Android forgets the preferred HOME when the set of packages changes.
    (tmp_path / "home").unlink()
    _install_fakes(monkeypatch, [_PanelDevice(tmp_path)])

    await async_repair_installed_app_permissions(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert _calls(tmp_path)[0] == (
        "cmd package set-home-activity io.github.maxlyth.hapaneld/.DashboardActivity"
    )
    assert _value(tmp_path, "home") == "io.github.maxlyth.hapaneld/.DashboardActivity"


async def test_a_home_android_does_not_keep_is_reported_and_the_app_still_starts(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "refuse.home").touch()

    await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    assert _HOME_WARNING in caplog.text


async def test_values_someone_already_blanked_are_recorded_as_metas(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _portal(tmp_path, components="")

    await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    assert (
        f"screensaver_components={_META_COMPONENTS}"
        in _value(tmp_path, "restore-record").splitlines()
    )


async def test_a_record_that_cannot_be_written_leaves_meta_untouched(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    # The record's directory is missing, so the write cannot land.
    real = _PanelDevice.streaming_shell

    async def unwritable(
        self: _PanelDevice, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        async for chunk in real(
            self,
            command.replace(
                vendor_provisioning.META_RESTORE_RECORD,
                str(self.state / "missing" / "record"),
            ),
            **kwargs,
        ):
            yield chunk

    monkeypatch.setattr(_PanelDevice, "streaming_shell", unwritable)

    await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    assert _value(tmp_path, "screensaver_components") == _META_COMPONENTS
    assert _value(tmp_path, f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW") == "allow"
    assert _SETUP_WARNING in caplog.text


async def test_hand_back_restores_metas_values_once(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    await _launch(monkeypatch, signer, target, descriptor, tmp_path)

    _install_fakes(monkeypatch, [_PanelDevice(tmp_path), _PanelDevice(tmp_path)])
    assert await async_restore_vendor_setup(target, signer) == "restored"

    assert _value(tmp_path, "screensaver_components") == _META_COMPONENTS
    assert _value(tmp_path, "screensaver_default_component") == _META_DEFAULT
    assert _value(tmp_path, f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW") == "allow"
    assert not (tmp_path / "restore-record").exists()
    # A retry finds nothing left to put back.
    assert await async_restore_vendor_setup(target, signer) == "none"


async def test_hand_back_on_a_panel_never_changed_touches_nothing(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    tmp_path: Path,
) -> None:
    _install_fakes(monkeypatch, [_PanelDevice(tmp_path)])

    assert await async_restore_vendor_setup(target, signer) == "none"
    assert _calls(tmp_path) == []


def test_an_unset_dream_is_restored_as_unset(tmp_path: Path) -> None:
    (tmp_path / "prop.ro.product.device").write_text("aloha\n")
    record = tmp_path / "restore-record"

    def run(program: str) -> bytes:
        return _run_on_stand_in_panel(
            program.replace(vendor_provisioning.META_RESTORE_RECORD, str(record)),
            tmp_path,
        )

    run(vendor_provisioning.vendor_setup_command("0" * 32))
    assert "screensaver_components=null" in record.read_text().splitlines()
    assert _value(tmp_path, "screensaver_components") == ""

    restored = run(vendor_provisioning.vendor_restore_command("1" * 32))

    assert b"\nrestored\n" in restored
    assert not (tmp_path / "screensaver_components").exists()
    assert "settings delete secure screensaver_components" in _calls(tmp_path)


def _setup_then_restore(state: Path, before_restore: str | None = None) -> bytes:
    """Run the setup and then the hand-back program on one stand-in Portal."""
    record = state / "restore-record"

    def run(program: str) -> bytes:
        return _run_on_stand_in_panel(
            program.replace(vendor_provisioning.META_RESTORE_RECORD, str(record)),
            state,
        )

    run(vendor_provisioning.vendor_setup_command("0" * 32))
    assert record.exists()
    if before_restore is not None:
        (state / before_restore).write_text("garbage\n")
    return run(vendor_provisioning.vendor_restore_command("1" * 32))


@pytest.mark.parametrize(
    "fault",
    [
        # Android refuses to put the saved mode back: the bar stays denied.
        "refuse.SYSTEM_ALERT_WINDOW",
        # The mode is put back but cannot be read: nothing proves it.
        "unreadable.SYSTEM_ALERT_WINDOW",
        "read_fail.SYSTEM_ALERT_WINDOW",
    ],
)
def test_an_overlay_not_read_back_as_recorded_keeps_the_record(
    tmp_path: Path, fault: str
) -> None:
    _portal(tmp_path)

    restored = _setup_then_restore(tmp_path, fault)

    assert b"\nunrestored\n" in restored
    assert "controlcenter_overlay=allow" in _value(tmp_path, "restore-record")


def test_an_overlay_read_back_as_another_mode_keeps_the_record(
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    # Android accepts the write but keeps a different mode than the one saved.
    record = tmp_path / "restore-record"
    _run_on_stand_in_panel(
        vendor_provisioning.vendor_setup_command("0" * 32).replace(
            vendor_provisioning.META_RESTORE_RECORD, str(record)
        ),
        tmp_path,
    )
    record.write_text(record.read_text().replace("=allow", "=ignore"))
    (tmp_path / "refuse.SYSTEM_ALERT_WINDOW").touch()
    (tmp_path / f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW").write_text("allow\n")

    restored = _run_on_stand_in_panel(
        vendor_provisioning.vendor_restore_command("1" * 32).replace(
            vendor_provisioning.META_RESTORE_RECORD, str(record)
        ),
        tmp_path,
    )

    assert b"\nunrestored\n" in restored
    assert record.exists()


def test_a_control_centre_recorded_absent_is_restored_while_still_absent(
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    (tmp_path / f"package.{_CONTROL_CENTER}").unlink()

    restored = _setup_then_restore(tmp_path)

    assert b"\nrestored\n" in restored
    assert not (tmp_path / "restore-record").exists()
    assert not any(_CONTROL_CENTER in call for call in _calls(tmp_path))


def test_a_control_centre_recorded_absent_that_appeared_keeps_the_record(
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    (tmp_path / f"package.{_CONTROL_CENTER}").unlink()
    record = tmp_path / "restore-record"
    _run_on_stand_in_panel(
        vendor_provisioning.vendor_setup_command("0" * 32).replace(
            vendor_provisioning.META_RESTORE_RECORD, str(record)
        ),
        tmp_path,
    )
    # Meta's package arrived since, and its mode is no longer the one recorded.
    (tmp_path / f"package.{_CONTROL_CENTER}").touch()
    (tmp_path / f"appop.{_CONTROL_CENTER}.SYSTEM_ALERT_WINDOW").write_text("deny\n")

    restored = _run_on_stand_in_panel(
        vendor_provisioning.vendor_restore_command("1" * 32).replace(
            vendor_provisioning.META_RESTORE_RECORD, str(record)
        ),
        tmp_path,
    )

    assert b"\nunrestored\n" in restored
    assert record.exists()


async def test_hand_back_that_cannot_restore_the_bar_reports_it_and_keeps_the_record(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _portal(tmp_path)
    await _launch(monkeypatch, signer, target, descriptor, tmp_path)
    (tmp_path / "unreadable.SYSTEM_ALERT_WINDOW").write_text("garbage\n")

    _install_fakes(monkeypatch, [_PanelDevice(tmp_path)])
    assert await async_restore_vendor_setup(target, signer) == "unrestored"

    assert (tmp_path / "restore-record").exists()

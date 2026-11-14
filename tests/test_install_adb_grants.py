"""Launch-time grants a fresh network install makes before the app first starts."""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from custom_components.panel_assistant import (
    adb_protocol,
    app_identity,
    home_chooser,
    install_adb,
)
from custom_components.panel_assistant.adb_protocol import (
    ADB_PORT,
    AdbInstallTarget,
    AdbRootMode,
    LaunchOutcome,
    OwnerDenials,
)
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    PACKAGE_ID,
)
from custom_components.panel_assistant.install_adb import async_launch_installed_app
from custom_components.panel_assistant.release import InstallDescriptor
from custom_components.panel_assistant.vendor_provisioning import META_PORTAL_DEVICES

from .install_adb_fakes import (
    _ACCESSIBILITY_SERVICES,
    _NOTIFICATIONS_GRANTED_LINE,
    NONCES,
    FakeDevice,
    _identity_root_output,
    _install_fakes,
    _notifications_output,
    _permissions_output,
    _single_output,
)
from .test_install_adb import (  # noqa: F401 - fixtures resolve by parameter name
    descriptor,
    signer,
    successor_descriptor,
    target,
)

_STAND_IN_PANEL = r"""
home_write_barrier() {
  if [ -f "$STATE/pause.$1" ]; then
    touch "$STATE/paused.$1"
    while [ ! -f "$STATE/resume.$1" ]; do command sleep 0.01; done
  fi
}
flock() {
  [ ! -f "$STATE/flock.unavailable" ] || return 127
  command flock "$@"
}
handback_after_home_authority() {
  handback=${1:-handback.after.home.authority}
  if [ -f "$STATE/$handback" ]; then
    # Simulated native completion followed by a successful owner handback.
    touch "$STATE/home_ack" "$STATE/handback.done"
    cp "$STATE/$handback" "$STATE/home"
  fi
}
am() {
  case "$*" in
    *"operation dashboard_home_policy"*)
      if [ -f "$STATE/home_ack" ] &&
          case "$*" in *"pending_setup true"*) true ;; *) false ;; esac; then
        echo 'Broadcast completed: result=0'
      elif [ -f "$STATE/home.policy" ]; then cat "$STATE/home.policy"
      elif [ -f "$STATE/receiver.absent" ]; then echo 'Broadcast completed: result=0'
      else echo 'Broadcast completed: result=1,' \
        'data="panel-assistant-home-builtin"'; fi ;;
    *"operation restore_previous_home_shell_lock_eligibility"*)
      handback_after_home_authority handback.before.restore.lock
      if [ -f "$STATE/disappear.before.restore.lock" ]; then
        touch "$STATE/receiver.absent"
        rm -f "$STATE/home.policy"
        echo 'Broadcast completed: result=0'; return
      fi
      if [ ! -f "$STATE/revoke.home.restore" ] &&
          { [ ! -f "$STATE/home_ack" ] ||
            case "$*" in *"pending_setup true"*) false ;; *) true ;; esac; }; then
        echo 'Broadcast completed: result=1,' \
          'data="panel-assistant-home-restore-shell-locked"'
      else echo 'Broadcast completed: result=0'; fi ;;
    *"operation acknowledge_retained_home"*)
      touch "$STATE/retained.home_ack" "$STATE/home_ack"
      echo 'Broadcast completed: result=2,' \
        'data="panel-assistant-home-retained"' > "$STATE/home.policy"
      echo 'Broadcast completed: result=1' ;;
    *"operation claim_pending_home"*)
      if [ -f "$STATE/incomplete.setup" ] || [ -f "$STATE/home_ack" ] ||
          [ -f "$STATE/legacy.receiver" ] || [ -f "$STATE/receiver.absent" ]; then
        echo "Broadcast completed: result=0"
      elif [ -f "$STATE/home.policy" ]; then
        case "$(cat "$STATE/home.policy")" in
          *panel-assistant-home-retained*)
            touch "$STATE/home_ack"
            echo 'Broadcast completed: result=1,' \
              'data="panel-assistant-pending-home-retained"' ;;
          *)
            handback_after_home_authority handback.after.restore.offer
            echo 'Broadcast completed: result=3,' \
              'data="panel-assistant-pending-home-restore"' ;;
        esac
      elif [ -f "$STATE/native.home" ]; then
        while [ "$#" -ge 3 ]; do
          if [ "$1 $2" = "--es home_component" ]; then
            pkg=${3%%/*}; cls=${3#*/}
            case "$cls" in "$pkg".*) cls=${cls#"$pkg"} ;; esac
            echo "$pkg/$cls" > "$STATE/home"
            touch "$STATE/home_ack"
            break
          fi
          shift
        done
        handback_after_home_authority
        echo 'Broadcast completed: result=1,' \
          'data="panel-assistant-pending-home-claimed"'
      else
        handback_after_home_authority handback.after.shell.offer
        if [ -f "$STATE/native.recover.after.shell.offer" ]; then
          touch "$STATE/native.home"
        fi
        echo 'Broadcast completed: result=2, data="panel-assistant-pending-home-shell"'
      fi ;;
    *"operation pending_home_shell_lock_eligibility"*)
      identity=
      while [ "$#" -ge 3 ]; do
        if [ "$1 $2" = "--es home_lock_identity" ]; then identity=$3; break; fi
        shift
      done
      if [ -f "$STATE/incomplete.setup" ] || [ -f "$STATE/home_ack" ] ||
          [ -f "$STATE/native.home" ] ||
          [ -f "$STATE/legacy.lock.receiver" ] ||
          [ -f "$STATE/lock.identity.mismatch" ] ||
          [ "$identity" != "$(stat -Lc '%d:%i' "$STATE/home-lock")" ]; then
        echo "Broadcast completed: result=0"
      else
        echo 'Broadcast completed: result=1,' \
          'data="panel-assistant-pending-home-shell-locked"'
      fi ;;
    *"operation pending_home_shell_eligibility"*)
      if [ -f "$STATE/incomplete.setup" ] || [ -f "$STATE/home_ack" ] ||
          [ -f "$STATE/native.home" ]; then
        echo "Broadcast completed: result=0"
      else
        echo 'Broadcast completed: result=1, data="panel-assistant-pending-home-shell"'
      fi ;;
    *"operation setup_eligibility"*)
      if [ -f "$STATE/legacy.receiver" ]; then echo "Broadcast completed: result=1"
      elif [ -f "$STATE/incomplete.setup" ]; then echo "Broadcast completed: result=0"
      else
        handback_after_home_authority
        if [ -f "$STATE/race.home.policy" ]; then
          mv "$STATE/race.home.policy" "$STATE/home.policy"; fi
        echo 'Broadcast completed: result=1, data="panel-assistant-setup-eligible"'
      fi ;;
    *"operation acknowledge_home"*)
      case "$(cat "$STATE/home" 2>/dev/null)" in
        io.panelassistant.android/*) touch "$STATE/home_ack" ;;
      esac
      echo "Broadcast completed: result=1" ;;
  esac
}
settings() {
  if [ "$1" = get ]; then
    if [ -f "$STATE/$3" ]; then cat "$STATE/$3"; else echo null; fi
    if [ -f "$STATE/race.$3" ]; then mv "$STATE/race.$3" "$STATE/$3"; fi
    if [ -f "$STATE/fail_after_first.$3" ]; then
      mv "$STATE/fail_after_first.$3" "$STATE/read_fail.$3"
      return 0
    fi
    [ ! -f "$STATE/read_fail.$3" ]
  else
    echo "settings $*" >> "$STATE/calls"
    if [ "$1" = delete ]; then rm -f "$STATE/$3"
    else [ -f "$STATE/refuse.$3" ] || printf '%s\n' "$4" > "$STATE/$3"; fi
  fi
}
appops() {
  if [ "$1" = set ]; then
    echo "appops $*" >> "$STATE/calls"
    [ -f "$STATE/refuse.$3" ] || echo "$4" > "$STATE/appop.$2.$3"
  elif [ -f "$STATE/unreadable.$3" ]; then
    cat "$STATE/unreadable.$3"
  elif [ -f "$STATE/appop.$2.$3" ]; then
    echo "$3: $(cat "$STATE/appop.$2.$3"); time=+2s12ms ago"
  elif [ -f "$STATE/default.$3" ]; then
    printf 'No operations.\nDefault mode: %s\n' "$(cat "$STATE/default.$3")"
  else
    echo "$3: default"
  fi
  if [ "$1" = get ]; then [ ! -f "$STATE/read_fail.$3" ]; fi
}
cmd() {
  if [ "$1 $2" = "package query-receivers" ]; then
    if [ -f "$STATE/receiver.disabled" ]; then
      case " $* " in *" --query-flags 512 "*) ;;
        *) echo "No receivers found"; return ;; esac
    fi
    if [ -f "$STATE/receiver.query.error" ]; then echo "No receivers found"; return 1
    elif [ -f "$STATE/receiver.absent" ]; then echo "No receivers found"
    else echo io.panelassistant.android/.platform.PreservePreviousHomeReceiver; fi
  elif [ "$1 $2" = "package resolve-activity" ]; then
    echo "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true"
    if [ -f "$STATE/home" ]; then cat "$STATE/home"
    else echo android/com.android.internal.app.ResolverActivity; fi
  elif [ "$1 $2" = "package set-home-activity" ]; then
    stat -Lc '%d:%i' /proc/self/fd/0 > "$STATE/home.stdin"
    home_write_barrier set-home
    echo "cmd $*" >> "$STATE/calls"
    # Android keeps the component and prints its short form back.
    pkg=${3%%/*}; cls=${3#*/}
    case "$cls" in "$pkg".*) cls=${cls#"$pkg"} ;; esac
    [ -f "$STATE/refuse.home" ] || echo "$pkg/$cls" > "$STATE/home"
  fi
}
getprop() {
  if [ -f "$STATE/prop.$1" ]; then cat "$STATE/prop.$1"; else echo generic; fi
}
setprop() {
  echo "setprop $*" >> "$STATE/calls"
  [ ! -f "$STATE/refuse.setprop" ] || return 1
  echo "$2" > "$STATE/prop.$1"
  [ ! -f "$STATE/fail.after.setprop" ] || return 1
}
sleep() { :; }
# HOME opens the chooser in chooser.xml; tapping at chooser.tap presses its
# Always, which makes chooser.home the preferred HOME.
input() {
  echo "input $*" >> "$STATE/calls"
  if [ "$*" = "keyevent 3" ] && [ -f "$STATE/chooser.xml" ]; then
    touch "$STATE/chooser.shown"
  elif [ "$1" = tap ] && [ -f "$STATE/chooser.shown" ] &&
      [ "$2 $3" = "$(cat "$STATE/chooser.tap")" ]; then
    home_write_barrier chooser-tap
    cp "$STATE/chooser.home" "$STATE/home"
  fi
}
uiautomator() {
  if [ -f "$STATE/chooser.shown" ]; then
    cp "$STATE/chooser.xml" "$2"
    if [ -f "$STATE/chooser.home.policy" ]; then
      mv "$STATE/chooser.home.policy" "$STATE/home.policy"; fi
    handback_after_home_authority handback.during.chooser
    if [ -f "$STATE/native.recover.during.chooser" ]; then
      touch "$STATE/native.home"
    fi
  fi
}
pm() {
  if [ "$1" = path ]; then
    if [ -f "$STATE/package.$2" ]; then echo "package:/system/app/$2/$2.apk"; fi
  elif [ "$1" = list ] && [ "$2" = features ]; then
    echo reqGlEsVersion=0x30002
    for f in "$STATE"/feature.android.hardware.*; do
      [ -f "$f" ] || continue
      case "$(cat "$f")" in
        true) echo "feature:${f##*/feature.}" ;;
        false) ;;
        *) return 2 ;;
      esac
    done
    # A read that fails part-way still prints what it read before it failed.
    for f in "$STATE"/read_fail.android.hardware.*; do
      if [ -e "$f" ]; then return 2; fi
    done
  elif [ "$1" = has-feature ]; then
    # Android 8.1, like the Sonoff PX30 panels: no has-feature, only its usage.
    echo "Error: unknown command 'has-feature'"
    return 1
  elif [ "$1" = grant ]; then
    echo "pm $*" >> "$STATE/calls"
    [ -f "$STATE/refuse.$3" ] || echo true > "$STATE/permission.$2.$3"
  fi
}
# Android lists each active admin by its short name, as a Meta Portal printed it.
dpm() {
  echo "dpm $*" >> "$STATE/calls"
  if [ "$1" = set-active-admin ] &&
      [ "${2#*/}" = io.panelassistant.android.platform.PanelDeviceAdminReceiver ]; then
    touch "$STATE/admin.${2%%/*}"
  fi
}
dumpsys() {
  if [ "$1" = device_policy ]; then
    echo "  Enabled Device Admins (User 0, provisioningState: 0):"
    for admin in "$STATE"/admin.*; do
      [ -e "$admin" ] || continue
      echo "    ${admin##*/admin.}/.platform.PanelDeviceAdminReceiver:"
    done
    return 0
  fi
  # A refused read, such as a permission denial, never reaches the package.
  if [ -f "$STATE/unreadable.permissions" ]; then
    cat "$STATE/unreadable.permissions"
    return
  fi
  echo "Package [$2]"
  if [ -f "$STATE/package.unreadable" ]; then return 1; fi
  for permission in POST_NOTIFICATIONS CAMERA RECORD_AUDIO; do
    permission="android.permission.$permission"
    # Android 9 and older list no line for a permission nobody decided until
    # it is granted.
    if [ -f "$STATE/unlisted.$2.$permission" ] &&
        [ ! -f "$STATE/permission.$2.$permission" ]; then
      continue
    fi
    # A line this parser cannot read, printed as it stands.
    if [ -f "$STATE/line.$2.$permission" ]; then
      cat "$STATE/line.$2.$permission"
      continue
    fi
    granted=false
    if [ -f "$STATE/permission.$2.$permission" ]; then
      granted=$(cat "$STATE/permission.$2.$permission")
    fi
    # Android's own defaults, as a real Android 11 panel printed them; a
    # flags file holds a decision such as USER_SET|USER_FIXED.
    flags="USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED"
    if [ -f "$STATE/flags.$2.$permission" ]; then
      flags=$(cat "$STATE/flags.$2.$permission")
    fi
    echo "      $permission: granted=$granted, flags=[ $flags]"
  done
  # An install-time permission, listed as granted only once it is, as Android
  # 11 prints it. Held unless a test says it is missing.
  secure=android.permission.WRITE_SECURE_SETTINGS
  [ -f "$STATE/unrequested.$secure" ] || echo "      $secure"
  if [ ! -f "$STATE/missing.$secure" ] ||
    [ "$(cat "$STATE/permission.$2.$secure" 2>/dev/null)" = true ]; then
    echo "      $secure: granted=true"
  fi
  [ ! -f "$STATE/read_fail.permissions" ]
}
"""


def _run_on_stand_in_panel(command: str, state: Path) -> bytes:
    # Preserve the generated locking program and real host flock. Only Android's
    # immutable directory and command side effects belong to this fixture.
    (state / "home-lock").mkdir(exist_ok=True)
    command = command.replace(
        getattr(adb_protocol, "_HOME_LOCK_DIRECTORY", "/system"),
        shlex.quote(str(state / "home-lock")),
    )
    return subprocess.run(
        ["/bin/sh", "-c", _STAND_IN_PANEL + command],
        capture_output=True,
        check=True,
        timeout=30,
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

    def __init__(
        self, state: Path, *, sdk: int = 34, posture: dict[str, Any] | None = None
    ) -> None:
        super().__init__([])
        self.sdk = sdk
        self.state = state
        self.posture = posture or {}

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        self.commands.append(command)
        self.shell_kwargs.append(kwargs)
        match = re.search(r"HAPANELD_([A-Z_]+?)_BEGIN:([0-9a-f]{32})", command)
        assert match is not None, command
        prefix, nonce = match.groups()
        if prefix == "POSTURE":
            yield _identity_root_output(nonce, sdk=self.sdk, **self.posture)
        elif prefix == "PACKAGE":
            yield _single_output(
                "PACKAGE", nonce, ["package:/data/app/ha-paneld/base.apk"], 0
            )
        elif prefix in ("NOTIFICATIONS", "PERMISSIONS", "VENDOR", "BOOTPORT"):
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
        if "pm grant io.panelassistant.android android.permission.POST_NOTIFICATIONS"
        in command
    ]
    starts = [
        index for index, command in enumerate(fake.commands) if "am start" in command
    ]
    assert grants == [2]
    assert starts == [4]
    assert "dumpsys package io.panelassistant.android" in fake.commands[2]
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
        if "did not grant io.panelassistant.android the notification permission"
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
            adb_protocol._notification_grant_command(
                NONCES[0], "io.panelassistant.android"
            ),
        )
    )
    subprocess.run(["/bin/sh", "-c", program], capture_output=True, check=False)
    return calls.read_text().splitlines()


def test_the_grant_program_keeps_a_persons_denial(tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    grant = "pm grant io.panelassistant.android android.permission.POST_NOTIFICATIONS"
    for flags in (
        "USER_SET|USER_SENSITIVE_WHEN_GRANTED",
        "USER_SET|USER_FIXED",
        "USER_FIXED",
        "POLICY_FIXED",
    ):
        assert _run_notification_program(flags, calls) == [], flags
    # Android's own defaults, as real panels print them: nobody answered.
    for flags in (
        "USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED",
        "REVIEW_REQUIRED",
    ):
        assert _run_notification_program(flags, calls) == [grant], flags


_GRANT_WARNING = "every permission it needs"


@pytest.mark.parametrize("current", [None, "full", "short"])
async def test_post_update_repair_removes_only_the_obsolete_service(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    current: str | None,
) -> None:
    installed = replace(
        successor_descriptor,
        launch_component=LAUNCH_COMPONENTS[successor_descriptor.package_id][-1],
    )
    old = app_identity.ACCESSIBILITY_COMPONENTS[successor_descriptor.launch_component]
    # The class a moved-classes build declares, spelled out rather than read
    # from the table the repair itself uses.
    new = (
        "io.panelassistant.android/"
        "io.panelassistant.android.input.PanelAccessibilityService"
    )
    if current == "short":
        new = installed.package_id + "/.input.PanelAccessibilityService"
    others = [
        "com.example.reader/.ReaderService",
        "org.other/org.other.a11y.Helper",
        # The old app's service is another app's: it stays.
        "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService",
    ]
    _seed_granted_panel(tmp_path, installed.package_id, installed.launch_component)
    before = [others[0], old, *others[1:]] + ([new] if current else [])
    (tmp_path / "enabled_accessibility_services").write_text(":".join(before) + "\n")

    for _ in range(2):
        fake = _RoutingFakeDevice(tmp_path)
        _install_fakes(monkeypatch, [fake])
        await install_adb.async_repair_installed_app_permissions(
            target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
        )
        assert _panel_value(tmp_path, "enabled_accessibility_services") == ":".join(
            [*others, new]
        )
        assert _panel_value(tmp_path, "accessibility_enabled") == "1"
        assert not any("am start" in command for command in fake.commands)
        assert _GRANT_WARNING not in caplog.text
    assert (tmp_path / "calls").read_text().splitlines() == [
        "settings put secure enabled_accessibility_services " + ":".join([*others, new])
    ]


async def test_refused_obsolete_service_cleanup_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    _seed_granted_panel(
        tmp_path, successor_descriptor.package_id, successor_descriptor.launch_component
    )
    installed = replace(
        successor_descriptor,
        launch_component=LAUNCH_COMPONENTS[successor_descriptor.package_id][-1],
    )
    new = app_identity.ACCESSIBILITY_COMPONENTS[installed.launch_component]
    before = _panel_value(tmp_path, "enabled_accessibility_services") + ":" + new
    (tmp_path / "enabled_accessibility_services").write_text(before + "\n")
    _install_fakes(
        monkeypatch, [_RoutingFakeDevice(tmp_path), _RoutingFakeDevice(tmp_path)]
    )

    # An older descriptor still needs the old class and must preserve the list.
    await install_adb.async_repair_installed_app_permissions(
        target, signer, successor_descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )
    assert not (tmp_path / "calls").exists()
    (tmp_path / "refuse.enabled_accessibility_services").touch()
    await install_adb.async_repair_installed_app_permissions(
        target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert _panel_value(tmp_path, "enabled_accessibility_services") == before
    assert _panel_value(tmp_path, "accessibility_enabled") == "1"
    assert _GRANT_WARNING in caplog.text


# The app's two class sets: builds up to 0.9.9 keep the classes in the old
# namespace, later ones moved them. Each set's service is spelled out rather
# than read from the table the grants themselves use.
_CLASS_SETS = {
    "successor": (
        "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity",
        "io.panelassistant.android/"
        "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
    ),
    "new_classes": (
        "io.panelassistant.android/io.panelassistant.android.MainActivity",
        "io.panelassistant.android/"
        "io.panelassistant.android.input.PanelAccessibilityService",
    ),
}


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


@pytest.mark.parametrize("operation", ["install_launch", "update_repair"])
async def test_explicit_install_and_update_repair_home_without_pending_setup(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    operation: str,
) -> None:
    installed = replace(
        successor_descriptor,
        launch_component=LAUNCH_COMPONENTS[successor_descriptor.package_id][-1],
    )
    # Setup has previously finished, and a subsequent handback returned HOME.
    # Explicit supported install/update repair still has broader authority than
    # the automatic coordinator's completion of a pending first setup.
    (tmp_path / "home_ack").touch()
    (tmp_path / "home").write_text("com.vendor.launcher/.Home\n")
    for _ in range(2):
        fake = _RoutingFakeDevice(tmp_path)
        _install_fakes(monkeypatch, [fake])
        if operation == "install_launch":
            result = await async_launch_installed_app(
                target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
            )
            assert result is LaunchOutcome.STARTED
        else:
            await install_adb.async_repair_installed_app_permissions(
                target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
            )
        assert _panel_value(tmp_path, "home") == f"{PACKAGE_ID}/.DashboardActivity"
        assert _panel_value(tmp_path, f"appop.{PACKAGE_ID}.WRITE_SETTINGS") == "allow"
        assert (
            _panel_value(tmp_path, f"appop.{PACKAGE_ID}.SYSTEM_ALERT_WINDOW") == "allow"
        )
    assert (
        sum(
            line.startswith("cmd package set-home-activity")
            for line in (tmp_path / "calls").read_text().splitlines()
        )
        == 1
    )


@pytest.mark.parametrize(
    "operation", ["install_launch", "update_repair", "pending_setup"]
)
@pytest.mark.parametrize(
    "initial_home",
    [
        "com.android.launcher3/.Launcher",
        "io.homeassistant.companion.android/.LauncherAlias",
        f"{PACKAGE_ID}/.DashboardActivity",
    ],
)
async def test_companion_install_and_repair_retain_previous_home(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    initial_home: str,
) -> None:
    previous = "com.android.launcher3/com.android.launcher3.Launcher"
    vendor_setup = AsyncMock(return_value=None)
    monkeypatch.setattr(install_adb, "_async_vendor_setup", vendor_setup)
    (tmp_path / "prop.ro.product.device").write_text(META_PORTAL_DEVICES[0])
    (tmp_path / "home").write_text(initial_home + "\n")
    restoring = initial_home.startswith(PACKAGE_ID)
    policy = (
        f'Broadcast completed: result=3, data="panel-assistant-home-restore:{previous}"'
        if restoring
        else 'Broadcast completed: result=2, data="panel-assistant-home-retained"'
    )
    (tmp_path / "home.policy").write_text(policy + "\n")
    installed = replace(
        successor_descriptor,
        launch_component=LAUNCH_COMPONENTS[successor_descriptor.package_id][-1],
    )
    for _ in range(2):
        fake = _RoutingFakeDevice(tmp_path)
        _install_fakes(monkeypatch, [fake])
        if operation == "pending_setup":
            chooser = await install_adb._async_repair_app_permissions(
                fake,
                target,
                installed.package_id,
                installed.launch_component,
                deferred_setup=True,
            )
            assert chooser == (
                "deferred" if (tmp_path / "completed.pending").exists() else "retained"
            )
            (tmp_path / "completed.pending").touch()
        elif operation == "update_repair":
            await install_adb.async_repair_installed_app_permissions(
                target, signer, installed, expected_root_mode=AdbRootMode.ROOTLESS
            )
        else:
            result = await install_adb.async_start_installed_app(
                target,
                signer,
                installed,
                expected_root_mode=AdbRootMode.ROOTLESS,
            )
            assert result.outcome is LaunchOutcome.STARTED
            assert result.home_chooser == "retained"
        assert _panel_value(tmp_path, "home") == (
            "com.android.launcher3/.Launcher" if restoring else initial_home
        )
        assert _panel_value(tmp_path, f"appop.{PACKAGE_ID}.WRITE_SETTINGS") == "allow"
    calls = (tmp_path / "calls").read_text()
    assert "set-home-activity io.panelassistant.android/" not in calls
    assert calls.count("set-home-activity") == int(restoring)
    assert "its home screen" not in caplog.text
    assert vendor_setup.await_count == (0 if operation == "pending_setup" else 2)


@pytest.mark.parametrize(
    "failure", ["unknown", "malformed", "revoked", "locked", "refused", "disappeared"]
)
async def test_unproved_companion_home_policy_never_claims_builtin_home(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    failure: str,
) -> None:
    current = f"{PACKAGE_ID}/.DashboardActivity"
    previous = "com.android.launcher3/com.android.launcher3.Launcher"
    (tmp_path / "home").write_text(current + "\n")
    policy = (
        f'Broadcast completed: result=3, data="panel-assistant-home-restore:{previous}"'
    )
    if failure == "unknown":
        policy = "Broadcast completed: result=0"
    elif failure == "malformed":
        policy = policy.replace(previous, previous + "; touch unexpected")
    else:
        (
            tmp_path
            / {
                "disappeared": "disappear.before.restore.lock",
                "revoked": "revoke.home.restore",
                "locked": "flock.unavailable",
                "refused": "refuse.home",
            }[failure]
        ).touch()
    (tmp_path / "home.policy").write_text(policy + "\n")
    fake = _RoutingFakeDevice(tmp_path)
    _install_fakes(monkeypatch, [fake])
    result = await install_adb.async_start_installed_app(
        target,
        signer,
        successor_descriptor,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )
    assert result.outcome is LaunchOutcome.STARTED
    assert result.home_chooser == "deferred"
    assert _panel_value(tmp_path, "home") == current
    assert not (tmp_path / "retained.home_ack").exists()
    calls = (tmp_path / "calls").read_text()
    assert "set-home-activity io.panelassistant.android/" not in calls
    assert calls.count("set-home-activity") == int(failure == "refused")


@pytest.mark.parametrize("boundary", ["after.restore.offer", "before.restore.lock"])
async def test_pending_companion_restore_cannot_restart_setup_after_handback(
    monkeypatch: pytest.MonkeyPatch,
    target: AdbInstallTarget,  # noqa: F811
    tmp_path: Path,
    boundary: str,
) -> None:
    vendor_setup = AsyncMock()
    monkeypatch.setattr(install_adb, "_async_vendor_setup", vendor_setup)
    (tmp_path / "prop.ro.product.device").write_text(META_PORTAL_DEVICES[0])
    current = f"{PACKAGE_ID}/.DashboardActivity"
    previous = "com.android.launcher3/com.android.launcher3.Launcher"
    owner_home = "com.vendor.launcher/.Home"
    (tmp_path / "home").write_text(current + "\n")
    (tmp_path / "home.policy").write_text(
        "Broadcast completed: result=3, "
        f'data="panel-assistant-home-restore:{previous}"\n'
    )
    (tmp_path / f"handback.{boundary}").write_text(owner_home + "\n")
    chooser = await install_adb._async_repair_app_permissions(
        _RoutingFakeDevice(tmp_path),
        target,
        PACKAGE_ID,
        LAUNCH_COMPONENTS[PACKAGE_ID][-1],
        deferred_setup=True,
    )
    assert chooser == "deferred"
    assert _panel_value(tmp_path, "home") == owner_home
    assert "set-home-activity" not in (tmp_path / "calls").read_text()
    vendor_setup.assert_not_awaited()


@pytest.mark.parametrize("boundary", ["eligibility", "chooser"])
async def test_explicit_home_claim_rechecks_a_changed_renderer(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    boundary: str,
) -> None:
    current = "com.vendor.launcher/.Home"
    policy_file = "race.home.policy"
    if boundary == "chooser":
        monkeypatch.setattr(
            home_chooser, "CHOOSER_DUMP", str(tmp_path / "chooser-dump")
        )
        current = "android/com.android.internal.app.ResolverActivity"
        policy_file = "chooser.home.policy"
        (tmp_path / "refuse.home").touch()
        (tmp_path / "chooser.xml").write_text(
            (Path(__file__).parent / "fixtures" / "portal_home_chooser.xml").read_text()
        )
        (tmp_path / "chooser.tap").write_text("817 623")
        (tmp_path / "chooser.home").write_text(f"{PACKAGE_ID}/.DashboardActivity\n")
    (tmp_path / "home").write_text(current + "\n")
    (tmp_path / policy_file).write_text(
        'Broadcast completed: result=2, data="panel-assistant-home-retained"\n'
    )
    _install_fakes(monkeypatch, [_RoutingFakeDevice(tmp_path)])
    result = await install_adb.async_start_installed_app(
        target, signer, successor_descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )
    assert result.home_chooser == "deferred"
    assert _panel_value(tmp_path, "home") == current
    calls = (tmp_path / "calls").read_text()
    assert calls.count("set-home-activity") == int(boundary == "chooser")
    assert "input tap" not in calls


@pytest.mark.parametrize("identity", list(_CLASS_SETS))
async def test_a_fresh_install_gets_the_usb_installers_grants_before_its_first_start(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    identity: str,
) -> None:
    launch_component, service = _CLASS_SETS[identity]
    installed = replace(descriptor, launch_component=launch_component)
    package_id = installed.package_id

    fake = await _launch_on(monkeypatch, signer, target, installed, tmp_path)

    assert _panel_value(tmp_path, f"appop.{package_id}.WRITE_SETTINGS") == "allow"
    assert _panel_value(tmp_path, f"appop.{package_id}.SYSTEM_ALERT_WINDOW") == "allow"
    assert (
        _panel_value(tmp_path, f"appop.{package_id}.REQUEST_INSTALL_PACKAGES")
        == "allow"
    )
    assert _panel_value(tmp_path, "enabled_accessibility_services") == service
    assert _panel_value(tmp_path, "accessibility_enabled") == "1"
    grants = [i for i, c in enumerate(fake.commands) if "appops set" in c]
    starts = [i for i, c in enumerate(fake.commands) if "am start" in c]
    assert len(grants) == 1
    assert grants[0] < starts[0]
    assert _GRANT_WARNING not in caplog.text


_SELF_UPDATE_LOST = "cannot install its own update with a tap"


@pytest.mark.parametrize(
    ("refused", "named", "self_update_lost"),
    [
        ("REQUEST_INSTALL_PACKAGES", "install packages", True),
        ("WRITE_SETTINGS", "modify system settings", False),
    ],
)
async def test_the_grant_warning_describes_only_the_missing_grants(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    refused: str,
    named: str,
    self_update_lost: bool,
) -> None:
    (tmp_path / f"refuse.{refused}").touch()

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    [warning] = [
        r.getMessage() for r in caplog.records if _GRANT_WARNING in r.getMessage()
    ]
    assert f"missing: {named};" in warning
    assert (_SELF_UPDATE_LOST in warning) is self_update_lost
    others = {"install packages", "modify system settings", "display over other apps"}
    assert not [other for other in others - {named} if other in warning]


async def test_an_unreadable_grant_readback_says_the_missing_grants_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _notifications_output(NONCES[2], granted=True),
            b"garbage\n",
            _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    await async_launch_installed_app(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    [warning] = [
        r.getMessage() for r in caplog.records if _GRANT_WARNING in r.getMessage()
    ]
    assert "cannot tell which permissions it lacks" in warning


@pytest.mark.parametrize("identity", list(_CLASS_SETS))
async def test_install_leaves_admin_inactive_until_setup_completes(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    identity: str,
) -> None:
    """Fresh installs and retries remain removable in Android Settings."""
    installed = replace(descriptor, launch_component=_CLASS_SETS[identity][0])

    await _launch_on(monkeypatch, signer, target, installed, tmp_path)
    await _launch_on(monkeypatch, signer, target, installed, tmp_path)

    calls = (tmp_path / "calls").read_text().splitlines()
    admin_calls = [call for call in calls if call.startswith("dpm ")]
    assert not (tmp_path / f"admin.{installed.package_id}").exists()
    assert admin_calls == []


def _seed_granted_panel(
    state: Path, package_id: str, launch_component: str | None = None
) -> None:
    launch = launch_component or LAUNCH_COMPONENTS[package_id][0]
    (state / "home").write_text(
        app_identity.EQUIVALENT_HOME_COMPONENTS[launch][0] + "\n"
    )
    (state / "enabled_accessibility_services").write_text(
        f"com.example.reader/.ReaderService:{_ACCESSIBILITY_SERVICES[package_id]}\n"
    )
    (state / "accessibility_enabled").write_text("1\n")
    for operation in (
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "REQUEST_INSTALL_PACKAGES",
    ):
        (state / f"appop.{package_id}.{operation}").write_text("allow\n")
    for feature in ("camera.any", "microphone"):
        (state / f"feature.android.hardware.{feature}").write_text("true\n")
    for permission in ("POST_NOTIFICATIONS", "CAMERA", "RECORD_AUDIO"):
        (state / f"permission.{package_id}.android.permission.{permission}").write_text(
            "true\n"
        )
    if package_id == app_identity.PACKAGE_ID:
        (state / f"admin.{package_id}").touch()


@pytest.mark.parametrize(
    "mode", ["default", "deny", "ignore", "foreground", "errored", "allow"]
)
async def test_android14_default_appops_repair_only_missing_grants(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    (tmp_path / f"appop.{descriptor.package_id}.WRITE_SETTINGS").unlink()
    (tmp_path / "default.WRITE_SETTINGS").write_text(mode + "\n")

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    calls = (
        (tmp_path / "calls").read_text().splitlines()
        if (tmp_path / "calls").exists()
        else []
    )
    assert calls == (
        []
        if mode == "allow"
        else [f"appops set {descriptor.package_id} WRITE_SETTINGS allow"]
    )
    assert _GRANT_WARNING not in caplog.text
    assert (
        _panel_value(tmp_path, "enabled_accessibility_services")
        == "com.example.reader/.ReaderService:"
        + _ACCESSIBILITY_SERVICES[descriptor.package_id]
    )


@pytest.mark.parametrize(
    "mode", ["unknown", "default\nDefault mode: allow", "allow; unexpected"]
)
async def test_android14_unknown_default_appops_preserve_state(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    (tmp_path / f"appop.{descriptor.package_id}.WRITE_SETTINGS").unlink()
    (tmp_path / "default.WRITE_SETTINGS").write_text(mode + "\n")

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert not (tmp_path / "calls").exists()
    assert _GRANT_WARNING in caplog.text


async def test_an_update_preserves_every_existing_grant_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _seed_granted_panel(
        tmp_path, successor_descriptor.package_id, successor_descriptor.launch_component
    )

    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("identity", list(_CLASS_SETS))
async def test_an_update_restores_hardware_permissions_without_enabling_features(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    identity: str,
) -> None:
    installed = replace(descriptor, launch_component=_CLASS_SETS[identity][0])
    for feature in ("camera.any", "microphone"):
        (tmp_path / f"feature.android.hardware.{feature}").write_text("true\n")
    (tmp_path / "camera_enabled").write_text("0\n")
    (tmp_path / "voice_enabled").write_text("0\n")

    await _launch_on(monkeypatch, signer, target, installed, tmp_path)

    for permission in ("CAMERA", "RECORD_AUDIO"):
        assert (
            _panel_value(
                tmp_path,
                f"permission.{installed.package_id}.android.permission.{permission}",
            )
            == "true"
        )
    assert _panel_value(tmp_path, "camera_enabled") == "0"
    assert _panel_value(tmp_path, "voice_enabled") == "0"


@pytest.mark.parametrize(
    "before",
    [
        "not a component",
        "org.other/.Helper:org.other/.Helper",
        "org.other/.Helper\norg.other/.Another",
        ":".join(f"org.other/.Helper{i}" for i in range(65)),
        "org.other/." + "H" * 4096,
    ],
)
async def test_an_unreadable_accessibility_list_is_never_rewritten(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    before: str,
) -> None:
    (tmp_path / "enabled_accessibility_services").write_text(before + "\n")

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == before
    assert (
        "settings put secure enabled_accessibility_services"
        not in (tmp_path / "calls").read_text()
    )
    assert "am start" in fake.commands[-1]


async def test_unreadable_operations_and_hardware_permissions_are_never_repaired(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    for operation in (
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "REQUEST_INSTALL_PACKAGES",
    ):
        (tmp_path / f"unreadable.{operation}").write_text("permission denied\n")
    (tmp_path / "unreadable.permissions").write_text("permission denied\n")
    (tmp_path / "feature.android.hardware.camera.any").write_text("unknown\n")
    (tmp_path / "feature.android.hardware.microphone").write_text("unknown\n")

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert not (tmp_path / "calls").exists()
    assert "am start" in fake.commands[-1]
    assert _GRANT_WARNING in caplog.text


async def test_a_refused_hardware_grant_retries_then_converges_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    permission = "android.permission.CAMERA"
    (tmp_path / "feature.android.hardware.camera.any").write_text("true\n")
    refusal = tmp_path / f"refuse.{permission}"
    refusal.touch()

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)
    assert (
        _panel_value(tmp_path, f"permission.{descriptor.package_id}.{permission}")
        is None
    )
    assert _GRANT_WARNING in caplog.text
    refusal.unlink()
    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)
    assert (
        _panel_value(tmp_path, f"permission.{descriptor.package_id}.{permission}")
        == "true"
    )
    (tmp_path / "calls").write_text("")

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert (tmp_path / "calls").read_text() == ""


@pytest.mark.parametrize(
    ("camera", "microphone"), [(True, False), (False, True), (False, False)]
)
async def test_runtime_repairs_follow_each_actual_hardware_feature(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    camera: bool,
    microphone: bool,
) -> None:
    for feature, present in (("camera.any", camera), ("microphone", microphone)):
        (tmp_path / f"feature.android.hardware.{feature}").write_text(
            str(present).lower() + "\n"
        )

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    for permission, present in (("CAMERA", camera), ("RECORD_AUDIO", microphone)):
        assert _panel_value(
            tmp_path,
            f"permission.{descriptor.package_id}.android.permission.{permission}",
        ) == ("true" if present else None)


@pytest.mark.parametrize(
    "read_fail",
    [
        "permissions",
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "REQUEST_INSTALL_PACKAGES",
        "enabled_accessibility_services",
        "accessibility_enabled",
        "android.hardware.camera.any",
        "android.hardware.microphone",
    ],
)
async def test_a_failed_read_does_not_prove_a_missing_grant(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    read_fail: str,
) -> None:
    package_id = descriptor.package_id
    _seed_granted_panel(tmp_path, package_id)
    (tmp_path / f"read_fail.{read_fail}").touch()
    if read_fail == "permissions":
        for permission in ("POST_NOTIFICATIONS", "CAMERA", "RECORD_AUDIO"):
            (
                tmp_path / f"permission.{package_id}.android.permission.{permission}"
            ).write_text("false\n")
    elif read_fail in (
        "WRITE_SETTINGS",
        "SYSTEM_ALERT_WINDOW",
        "REQUEST_INSTALL_PACKAGES",
    ):
        (tmp_path / f"appop.{package_id}.{read_fail}").write_text("deny\n")
    elif read_fail == "enabled_accessibility_services":
        (tmp_path / read_fail).write_text("null\n")
    elif read_fail == "accessibility_enabled":
        (tmp_path / read_fail).write_text("0\n")
    else:
        permission = "CAMERA" if read_fail.endswith("camera.any") else "RECORD_AUDIO"
        (
            tmp_path / f"permission.{package_id}.android.permission.{permission}"
        ).write_text("false\n")

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert not (tmp_path / "calls").exists()
    assert "am start" in fake.commands[-1]


async def test_a_successor_shorthand_is_preserved_but_does_not_enable_the_service(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    invalid = "io.panelassistant.android/.input.PanelAccessibilityService"
    (tmp_path / "enabled_accessibility_services").write_text(invalid + "\n")

    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == (
        f"{invalid}:{_ACCESSIBILITY_SERVICES[successor_descriptor.package_id]}"
    )


async def test_a_failed_list_recheck_prevents_rewriting_other_services(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    other = "com.example.reader/.ReaderService"
    (tmp_path / "enabled_accessibility_services").write_text(other + "\n")
    (tmp_path / "fail_after_first.enabled_accessibility_services").touch()

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == other
    assert not (tmp_path / "calls").exists()


@pytest.mark.parametrize("unproved", ["unparsed", "failed", "oversized"])
async def test_an_unproved_service_binding_leaves_both_secure_settings_untouched(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    unproved: str,
) -> None:
    own = _ACCESSIBILITY_SERVICES[descriptor.package_id]
    # Every entry of an oversized list is a component, so only its length
    # makes it unproved.
    before = {
        "unparsed": f"unreadable:{own}",
        "failed": own,
        "oversized": f"org.other/.{'H' * 4096}:{own}",
    }[unproved]
    (tmp_path / "enabled_accessibility_services").write_text(before + "\n")
    (tmp_path / "accessibility_enabled").write_text("0\n")
    if unproved == "failed":
        (tmp_path / "read_fail.enabled_accessibility_services").touch()

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert _panel_value(tmp_path, "enabled_accessibility_services") == before
    assert _panel_value(tmp_path, "accessibility_enabled") == "0"
    assert "settings put secure" not in (tmp_path / "calls").read_text()
    assert (
        _panel_value(tmp_path, f"appop.{descriptor.package_id}.WRITE_SETTINGS")
        == "allow"
    )
    assert "am start" in fake.commands[-1]


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
        "REQUEST_INSTALL_PACKAGES",
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
    if refused == "enabled_accessibility_services":
        (tmp_path / "accessibility_enabled").write_text("1\n")

    fake = await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    assert "am start" in fake.commands[-1]
    assert [
        record.levelno
        for record in caplog.records
        if f"did not grant io.panelassistant.android {_GRANT_WARNING}"
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
    (tmp_path / "accessibility_enabled").write_text("1\n")
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
    [None, "io.panelassistant.android/.input.PanelAccessibilityService"],
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
    # The shorthand is the other spelling Android reads back for a build whose
    # classes moved to the app's own package.
    descriptor = replace(descriptor, launch_component=_CLASS_SETS["new_classes"][0])
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
    assert _panel_value(tmp_path, f"appop.{PACKAGE_ID}.WRITE_SETTINGS") == "allow"
    assert _GRANT_WARNING not in caplog.text


_SECURE = "android.permission.WRITE_SECURE_SETTINGS"


async def test_a_missing_secure_settings_grant_is_granted_once_and_then_left(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package = successor_descriptor.package_id
    _seed_granted_panel(tmp_path, package, successor_descriptor.launch_component)
    (tmp_path / f"missing.{_SECURE}").touch()

    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert (tmp_path / "calls").read_text().splitlines() == [
        f"pm grant {package} {_SECURE}"
    ]
    assert _panel_value(tmp_path, f"permission.{package}.{_SECURE}") == "true"
    assert _GRANT_WARNING not in caplog.text

    (tmp_path / "calls").unlink()
    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert not (tmp_path / "calls").exists()


async def test_a_refused_secure_settings_grant_is_reported_and_still_starts(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    package = successor_descriptor.package_id
    _seed_granted_panel(tmp_path, package, successor_descriptor.launch_component)
    (tmp_path / f"missing.{_SECURE}").touch()
    (tmp_path / f"refuse.{_SECURE}").touch()

    fake = await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    assert _GRANT_WARNING in caplog.text
    assert "the screen dims instead of switching off" in caplog.text
    assert any("am start" in command for command in fake.commands)


@pytest.mark.parametrize("reason", ["unrequested", "unreadable"])
async def test_secure_settings_is_never_granted_without_proof_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    successor_descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    reason: str,
) -> None:
    package = successor_descriptor.package_id
    _seed_granted_panel(tmp_path, package, successor_descriptor.launch_component)
    (tmp_path / f"missing.{_SECURE}").touch()
    if reason == "unrequested":
        (tmp_path / f"unrequested.{_SECURE}").touch()
    else:
        (tmp_path / "read_fail.permissions").touch()

    await _launch_on(monkeypatch, signer, target, successor_descriptor, tmp_path)

    calls = tmp_path / "calls"
    assert _SECURE not in (calls.read_text() if calls.exists() else "")


async def test_the_grant_warning_names_the_missing_grant_and_the_panel(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "refuse.SYSTEM_ALERT_WINDOW").touch()

    await _launch_on(monkeypatch, signer, target, descriptor, tmp_path)

    messages = [
        record.getMessage()
        for record in caplog.records
        if _GRANT_WARNING in record.getMessage()
    ]
    assert len(messages) == 1
    assert "(missing: display over other apps; panel " in messages[0]
    assert f"panel {target.address.stored_value})" in messages[0]


_BOOT_PORT = "persist.adb.tcp.port"
_POSTURES: dict[AdbRootMode, dict[str, Any]] = {
    AdbRootMode.ROOTLESS: {},
    AdbRootMode.ROOT_ADBD: {"uid": "0"},
    AdbRootMode.ROOT_SU: {"su_lines": ["present"]},
}


async def _start_on(
    monkeypatch: pytest.MonkeyPatch,
    target: AdbInstallTarget,  # noqa: F811
    signer: PythonRSASigner,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    state: Path,
    root_mode: AdbRootMode,
    *,
    local: bool = False,
) -> list[str]:
    monkeypatch.setattr(install_adb, "_async_prove_su", AsyncMock())
    fake = _RoutingFakeDevice(state, posture=_POSTURES[root_mode])
    _install_fakes(monkeypatch, [fake])
    if local:
        target = replace(target, local_shell=fake)
    launch = await install_adb.async_start_installed_app(
        target, signer, descriptor, expected_root_mode=root_mode
    )
    assert launch.outcome is LaunchOutcome.STARTED
    return fake.commands


def _boot_port_writes(state: Path) -> list[str]:
    calls = state / "calls"
    lines = calls.read_text().splitlines() if calls.exists() else []
    return [line for line in lines if line.startswith("setprop ")]


async def test_a_root_adbd_install_keeps_network_adb_on_its_port_before_the_start(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    # An unset property: toybox getprop prints an empty line.
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("\n")
    caplog.set_level(logging.INFO)
    commands = await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, AdbRootMode.ROOT_ADBD
    )
    writes = [i for i, command in enumerate(commands) if "setprop" in command]
    starts = [i for i, command in enumerate(commands) if "am start" in command]
    assert len(writes) == 1
    assert len(starts) == 1
    assert writes[0] < starts[0]
    assert _boot_port_writes(tmp_path) == [f"setprop {_BOOT_PORT} {ADB_PORT}"]
    assert _panel_value(tmp_path, f"prop.{_BOOT_PORT}") == "5555"
    assert "Kept network ADB enabled after a reboot" in caplog.text
    assert "Could not keep network ADB" not in caplog.text

    # A second start finds the port in place and changes nothing.
    caplog.clear()
    await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, AdbRootMode.ROOT_ADBD
    )
    assert _boot_port_writes(tmp_path) == [f"setprop {_BOOT_PORT} {ADB_PORT}"]
    assert caplog.text.count("network ADB") == 0


async def test_a_root_adbd_install_never_overwrites_an_existing_boot_port(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("5037\n")
    await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, AdbRootMode.ROOT_ADBD
    )
    assert _boot_port_writes(tmp_path) == []
    assert _panel_value(tmp_path, f"prop.{_BOOT_PORT}") == "5037"
    assert "network ADB" not in caplog.text


@pytest.mark.parametrize("root_mode", [AdbRootMode.ROOTLESS, AdbRootMode.ROOT_SU])
async def test_only_root_adbd_installs_touch_the_boot_port(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    root_mode: AdbRootMode,
) -> None:
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("\n")
    commands = await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, root_mode
    )
    assert sum(_BOOT_PORT in command for command in commands) == 0
    assert _boot_port_writes(tmp_path) == []


@pytest.mark.parametrize("failure", ["refuse.setprop", "fail.after.setprop"])
async def test_a_refused_boot_port_write_is_reported_and_never_stops_the_start(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("\n")
    (tmp_path / failure).touch()
    commands = await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, AdbRootMode.ROOT_ADBD
    )
    assert _boot_port_writes(tmp_path) == [f"setprop {_BOOT_PORT} {ADB_PORT}"]
    assert sum("am start" in command for command in commands) == 1
    # This integration's warnings only: under load asyncio adds a slow-callback one.
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name.startswith("custom_components.")
    ]
    assert [r.getMessage() for r in warnings] == [
        "Could not keep network ADB enabled after a reboot on this panel; "
        "the app's helper sets it when it first runs"
    ]


async def test_a_boot_port_that_does_not_read_back_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    # setprop reports success but the property stays empty (a vendor guard).
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("\n")
    monkeypatch.setattr(
        sys.modules[__name__],
        "_STAND_IN_PANEL",
        _STAND_IN_PANEL + 'setprop() { echo "setprop $*" >> "$STATE/calls"; }\n',
    )
    commands = await _start_on(
        monkeypatch, target, signer, descriptor, tmp_path, AdbRootMode.ROOT_ADBD
    )
    assert _boot_port_writes(tmp_path) == [f"setprop {_BOOT_PORT} {ADB_PORT}"]
    assert sum("am start" in command for command in commands) == 1
    assert caplog.text.count("Could not keep network ADB enabled") == 1


async def test_the_apps_own_session_leaves_the_boot_port_to_its_helper(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    (tmp_path / f"prop.{_BOOT_PORT}").write_text("\n")
    commands = await _start_on(
        monkeypatch,
        target,
        signer,
        descriptor,
        tmp_path,
        AdbRootMode.ROOT_ADBD,
        local=True,
    )
    assert sum(_BOOT_PORT in command for command in commands) == 0
    assert sum("am start" in command for command in commands) == 1


# The owner's choices --------------------------------------------------------
#
# An install or update grants only what nobody decided. A denial the owner
# made in Android settings is kept and reported, never turned back on.

#: The owner's Deny, as Android 11 printed it on a Shelly X2i after Settings,
#: Apps, Panel Assistant, Permissions, Microphone, Deny.
_OWNER_DENIAL = "USER_SET|USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED"


async def _launch_reporting(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    installed: InstallDescriptor,
    state: Path,
) -> list[OwnerDenials]:
    reports: list[OwnerDenials] = []
    _install_fakes(monkeypatch, [_RoutingFakeDevice(state, sdk=target.android_sdk)])
    outcome = await async_launch_installed_app(
        target,
        signer,
        installed,
        expected_root_mode=AdbRootMode.ROOTLESS,
        report_denials=reports.append,
    )
    assert outcome is LaunchOutcome.STARTED
    return reports


def _calls(state: Path) -> list[str]:
    calls = state / "calls"
    return calls.read_text().splitlines() if calls.exists() else []


def _deny(state: Path, package_id: str, permission: str, flags: str) -> None:
    permission = f"android.permission.{permission}"
    (state / f"permission.{package_id}.{permission}").write_text("false\n")
    (state / f"flags.{package_id}.{permission}").write_text(flags + "\n")


async def test_a_hardware_denial_nobody_answered_is_granted(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    for permission in ("CAMERA", "RECORD_AUDIO"):
        _deny(
            tmp_path,
            descriptor.package_id,
            permission,
            "USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED",
        )

    reports = await _launch_reporting(monkeypatch, signer, target, descriptor, tmp_path)

    assert sorted(c for c in _calls(tmp_path) if c.startswith("pm grant")) == [
        f"pm grant {descriptor.package_id} android.permission.{permission}"
        for permission in ("CAMERA", "RECORD_AUDIO")
    ]
    assert reports == [OwnerDenials(features=(), complete=True)]


async def test_a_hardware_permission_android_9_never_listed_is_granted(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    # Android 8.1 and 9 (the Sonoff panels, the Portals) print no line for a
    # permission nobody has decided, so a fresh install shows none.
    _seed_granted_panel(tmp_path, descriptor.package_id)
    for permission in ("CAMERA", "RECORD_AUDIO"):
        permission = f"android.permission.{permission}"
        (tmp_path / f"permission.{descriptor.package_id}.{permission}").unlink()
        (tmp_path / f"unlisted.{descriptor.package_id}.{permission}").touch()

    reports = await _launch_reporting(
        monkeypatch, signer, replace(target, android_sdk=28), descriptor, tmp_path
    )

    assert sorted(c for c in _calls(tmp_path) if c.startswith("pm grant")) == [
        f"pm grant {descriptor.package_id} android.permission.{permission}"
        for permission in ("CAMERA", "RECORD_AUDIO")
    ]
    assert reports == [OwnerDenials(features=(), complete=True)]


async def test_a_malformed_line_android_9_prints_is_never_granted(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    # A line that names the permission but is not one this parser reads is
    # unknown, not the absence Android 9 shows for a permission nobody decided.
    _seed_granted_panel(tmp_path, descriptor.package_id)
    permission = "android.permission.RECORD_AUDIO"
    (tmp_path / f"permission.{descriptor.package_id}.{permission}").unlink()
    (tmp_path / f"line.{descriptor.package_id}.{permission}").write_text(
        f"      {permission}: granted = false, flags=[ USER_SET|USER_FIXED]\n"
    )

    await _launch_reporting(
        monkeypatch, signer, replace(target, android_sdk=28), descriptor, tmp_path
    )

    assert f"pm grant {descriptor.package_id} {permission}" not in _calls(tmp_path)


_UNDECIDED_LINE = (
    "      android.permission.RECORD_AUDIO: granted=false,"
    " flags=[ USER_SENSITIVE_WHEN_GRANTED|USER_SENSITIVE_WHEN_DENIED]"
)


@pytest.mark.parametrize(
    ("dump", "status", "granted"),
    [
        # Android 9 and older: a readable dump of the package, no line.
        (["Package [io.panelassistant.android] (1a2b3c):"], 0, True),
        # A failed dump proves nothing, even with the header.
        (["Package [io.panelassistant.android] (1a2b3c):"], 1, False),
        # A dump that does not show this package proves nothing.
        (["Unable to find package: io.panelassistant.android"], 0, False),
        (["Package [io.panelassistant.android.other] (1a2b3c):"], 0, False),
        # A line still decides: the owner's denial is kept ...
        (
            [
                "Package [io.panelassistant.android] (1a2b3c):",
                "      android.permission.RECORD_AUDIO: granted=false,"
                " flags=[ USER_SET|USER_FIXED]",
            ],
            0,
            False,
        ),
        # ... and a denial nobody decided is granted.
        (["Package [io.panelassistant.android] (1a2b3c):", _UNDECIDED_LINE], 0, True),
        # Android 9's requested list names it bare: still no line, granted.
        (
            [
                "Package [io.panelassistant.android] (1a2b3c):",
                "      android.permission.RECORD_AUDIO",
            ],
            0,
            True,
        ),
        # A line naming it that this parser cannot read is unknown, not absent.
        (
            [
                "Package [io.panelassistant.android] (1a2b3c):",
                "      android.permission.RECORD_AUDIO: granted = false,"
                " flags=[ USER_SET|USER_FIXED]",
            ],
            0,
            False,
        ),
        (
            [
                "Package [io.panelassistant.android] (1a2b3c):",
                "      RECORD_AUDIO: granted=false, flags=[ USER_SET|USER_FIXED]",
            ],
            0,
            False,
        ),
    ],
)
def test_a_runtime_permission_is_granted_only_where_nobody_decided_it(
    tmp_path: Path, dump: list[str], status: int, granted: bool
) -> None:
    calls = tmp_path / "calls"
    lines = "; ".join(f"echo {shlex.quote(line)}" for line in dump)
    program = "\n".join(
        (
            f"dumpsys() {{ {lines}; return {status}; }}",
            f'pm() {{ echo "pm $*" >> {shlex.quote(str(calls))}; }}',
            f": > {shlex.quote(str(calls))}",
            adb_protocol._runtime_permission_repair_command(
                "io.panelassistant.android", "android.permission.RECORD_AUDIO"
            ),
        )
    )
    subprocess.run(["/bin/sh", "-c", program], capture_output=True, check=False)
    assert calls.read_text().splitlines() == (
        ["pm grant io.panelassistant.android android.permission.RECORD_AUDIO"]
        if granted
        else []
    )


@pytest.mark.parametrize(
    "flags", ["USER_SET", "USER_FIXED", _OWNER_DENIAL, "POLICY_FIXED"]
)
async def test_the_owners_hardware_denial_is_kept_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    flags: str,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    for permission in ("CAMERA", "RECORD_AUDIO"):
        _deny(tmp_path, descriptor.package_id, permission, flags)

    reports = await _launch_reporting(monkeypatch, signer, target, descriptor, tmp_path)

    assert not any(call.startswith("pm grant") for call in _calls(tmp_path))
    for permission in ("CAMERA", "RECORD_AUDIO"):
        assert (
            _panel_value(
                tmp_path,
                f"permission.{descriptor.package_id}.android.permission.{permission}",
            )
            == "false"
        )
    assert reports == [OwnerDenials(features=("camera", "microphone"), complete=True)]
    # A kept choice is reported as a Repair, not as a grant that failed.
    assert _GRANT_WARNING not in caplog.text


async def test_the_owners_notification_denial_is_kept_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    _deny(tmp_path, descriptor.package_id, "POST_NOTIFICATIONS", _OWNER_DENIAL)

    reports = await _launch_reporting(
        monkeypatch, signer, replace(target, android_sdk=34), descriptor, tmp_path
    )

    assert not any(call.startswith("pm grant") for call in _calls(tmp_path))
    assert reports == [OwnerDenials(features=("notifications",), complete=True)]
    assert "notification permission" not in caplog.text


@pytest.mark.parametrize("mode", ["deny", "ignore"])
async def test_the_owners_app_op_choice_is_kept_and_reported(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mode: str,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    for operation in ("WRITE_SETTINGS", "SYSTEM_ALERT_WINDOW"):
        (tmp_path / f"appop.{descriptor.package_id}.{operation}").write_text(
            mode + "\n"
        )

    reports = await _launch_reporting(monkeypatch, signer, target, descriptor, tmp_path)

    assert not any(call.startswith("appops set") for call in _calls(tmp_path))
    for operation in ("WRITE_SETTINGS", "SYSTEM_ALERT_WINDOW"):
        assert (
            _panel_value(tmp_path, f"appop.{descriptor.package_id}.{operation}") == mode
        )
    assert reports == [
        OwnerDenials(
            features=("modify system settings", "display over other apps"),
            complete=True,
        )
    ]
    assert _GRANT_WARNING not in caplog.text


async def test_an_app_op_nobody_set_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    (tmp_path / f"appop.{descriptor.package_id}.REQUEST_INSTALL_PACKAGES").write_text(
        "default\n"
    )

    reports = await _launch_reporting(monkeypatch, signer, target, descriptor, tmp_path)

    assert _calls(tmp_path) == [
        f"appops set {descriptor.package_id} REQUEST_INSTALL_PACKAGES allow"
    ]
    assert reports == [OwnerDenials(features=(), complete=True)]


# Below Android 13 there is no notification read to fall back on, so the
# permission program's own readback must say it was incomplete.
@pytest.mark.parametrize("sdk", [30, 34])
async def test_an_unread_grant_never_proves_nothing_is_turned_off(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
    sdk: int,
) -> None:
    _seed_granted_panel(tmp_path, descriptor.package_id)
    (tmp_path / "unreadable.permissions").write_text("permission denied\n")

    reports = await _launch_reporting(
        monkeypatch, signer, replace(target, android_sdk=sdk), descriptor, tmp_path
    )

    assert not any(call.startswith("pm grant") for call in _calls(tmp_path))
    assert reports == [OwnerDenials(features=(), complete=False)]

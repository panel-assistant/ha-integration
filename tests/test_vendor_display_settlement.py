"""Execute setup's vendor program against a stateful Android shell boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from custom_components.panel_assistant.app_identity import SUCCESSOR_PACKAGE_ID
from custom_components.panel_assistant.vendor_provisioning import (
    VendorResponseError,
    parse_vendor_display_settlement,
    vendor_display_settlement_command,
)

_NONCE = "a" * 32
_STAND_IN = r"""
pm() {
  [ "$MODE" != probe-error ] || return 1
  [ "$MODE" = other ] || echo package:cloud.shelly.stargate
}
cmd() {
  echo priority=0
  if [ "$MODE" = wrong-home ]; then echo other.app/.Home
  else echo io.panelassistant.android/.DashboardActivity; fi
}
wm() { echo 'Physical density: 240'; echo 'Override density: 216'; }
settings() {
  case "$MODE" in delayed-font|stale-font) echo 0.9 ;; *) echo null ;; esac
}
pidof() { [ ! -f "$STATE/pid" ] || cat "$STATE/pid"; }
input() {
  echo home >> "$STATE/calls"
  echo running > "$STATE/app"
  [ "$MODE" = never-pauses ] || echo paused > "$STATE/activity"
}
date() {
  hp_clock=$(cat "$STATE/clock")
  echo "$((hp_clock + 1))" > "$STATE/clock"
  echo "$hp_clock"
}
sleep() { :; }
timeout() { shift; "$@"; }
am() {
  echo resume >> "$STATE/calls"
  [ "$MODE" != start-error ] || return 1
  echo resumed > "$STATE/activity"
  if [ ! -f "$STATE/pid" ] && [ "$MODE" != cold-exit ]; then
    echo 200 > "$STATE/pid"; mkdir -p "$STATE/proc/200"
  fi
  [ ! -f "$STATE/pending" ] || touch "$STATE/exiting"
  echo 'Status: ok'
}
dumpsys() {
  hp_pid=$(pidof)
  [ -n "$hp_pid" ] || return 0
  if [ -f "$STATE/exiting" ]; then
    echo shutdown >> "$STATE/calls"
    rm -rf "$STATE/proc/$hp_pid" "$STATE/pid" "$STATE/pending" "$STATE/exiting"
    echo stopped > "$STATE/app"
    return 0
  fi
  [ "$MODE" != wrong-pid ] || hp_pid=999
  echo "  ACTIVITY cloud.shelly.stargate/.activities.MainActivity a123 pid=$hp_pid"
  if [ "$MODE" = header-only ] && [ "$(cat "$STATE/activity")" = resumed ]; then
    return 0
  fi
  echo '    Local Activity b123 State:'
  if [ "$(cat "$STATE/activity")" = resumed ]; then
    echo '      mResumed=true mStopped=false mFinished=false'
  else echo '      mResumed=false mStopped=true mFinished=false'; fi
  hp_font=1.0; hp_density=216
  if [ "$MODE" = stale-density ]; then hp_density=217; fi
  if [ "$MODE" = delayed-font ] && [ -f "$STATE/config-delivered" ]; then
    hp_font=0.9
  fi
  echo "      mCurrentConfig={$hp_font en_GB $hp_density"'dpi port}'
  if [ "$MODE" = delayed-font ] && [ ! -f "$STATE/config-delivered" ]; then
    echo configuration >> "$STATE/calls"
    touch "$STATE/pending" "$STATE/config-delivered"
  fi
}
"""


def _run(state: Path, mode: str) -> tuple[list[str], list[str]]:
    program = vendor_display_settlement_command(_NONCE, SUCCESSOR_PACKAGE_ID)
    output = subprocess.run(
        ["/bin/sh", "-c", _STAND_IN + program.replace('"/proc/', '"$STATE/proc/')],
        check=True,
        text=True,
        capture_output=True,
        timeout=5,
        env={"PATH": os.environ["PATH"], "STATE": str(state), "MODE": mode},
    ).stdout.splitlines()
    assert output[0] == f"HAPANELD_VENDOR_DISPLAY_SETTLEMENT_BEGIN:{_NONCE}"
    assert output[-1] == f"HAPANELD_VENDOR_DISPLAY_SETTLEMENT_END:{_NONCE}:0"
    calls = (
        (state / "calls").read_text().splitlines() if (state / "calls").exists() else []
    )
    return output[1:-1], calls


@pytest.fixture
def native_panel(tmp_path: Path) -> Path:
    (tmp_path / "pid").write_text("100\n")
    (tmp_path / "clock").write_text("0\n")
    (tmp_path / "activity").write_text("paused\n")
    (tmp_path / "app").write_text("running\n")
    (tmp_path / "proc/100").mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize("mode", ["pending", "unchanged", "cold"])
def test_native_resume_finishes_before_setup_can_accept(
    native_panel: Path, mode: str
) -> None:
    if mode == "pending":
        (native_panel / "pending").touch()
    if mode == "cold":
        (native_panel / "pid").unlink()
        (native_panel / "proc/100").rmdir()
    lines, calls = _run(native_panel, mode)
    assert parse_vendor_display_settlement(lines) == "settled"
    if mode == "pending":
        assert calls == ["resume", "shutdown", "resume", "home", "resume"]
        assert not (native_panel / "pending").exists()
        assert not (native_panel / "proc/100").exists()
    else:
        assert calls == ["resume", "home", "resume"]
    assert (native_panel / "app").read_text().strip() == "running"


@pytest.mark.parametrize(
    "mode",
    [
        "probe-error",
        "wrong-home",
        "never-pauses",
        "start-error",
        "wrong-pid",
        "header-only",
        "cold-exit",
        "stale-density",
        "stale-font",
    ],
)
def test_no_positive_native_completion_refuses_setup(
    native_panel: Path, mode: str
) -> None:
    if mode == "cold-exit":
        (native_panel / "pid").unlink()
        (native_panel / "proc/100").rmdir()
    lines, calls = _run(native_panel, mode)
    with pytest.raises(VendorResponseError):
        parse_vendor_display_settlement(lines)
    assert lines == ["error"]
    if mode in {"probe-error", "wrong-home"}:
        assert "resume" not in calls
    else:
        assert calls.count("resume") == 1


def test_other_vendors_do_not_change_home_or_start_stargate(native_panel: Path) -> None:
    lines, calls = _run(native_panel, "other")
    assert parse_vendor_display_settlement(lines) == "skipped"
    assert calls == []


def test_interrupted_setup_repeats_after_the_pending_restart(
    native_panel: Path,
) -> None:
    (native_panel / "pending").touch()
    first, _ = _run(native_panel, "pending")
    assert parse_vendor_display_settlement(first) == "settled"
    (native_panel / "clock").write_text("0\n")
    (native_panel / "calls").unlink()
    second, calls = _run(native_panel, "cold")
    assert parse_vendor_display_settlement(second) == "settled"
    assert calls == ["resume", "home", "resume"]


def test_browser_and_network_run_the_same_settlement_program() -> None:
    module = (
        Path(__file__).resolve().parents[1]
        / "custom_components/panel_assistant/frontend/src/vendor-provisioning.mjs"
    )
    script = (
        f"import {{buildVendorDisplaySettlement}} from {json.dumps(module.as_uri())}; "
        "console.log(JSON.stringify(buildVendorDisplaySettlement("
        f"'{_NONCE}', '{SUCCESSOR_PACKAGE_ID}')));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == vendor_display_settlement_command(
        _NONCE, SUCCESSOR_PACKAGE_ID
    )


def test_a_configuration_delivered_after_warmup_is_consumed_by_a_fresh_resume(
    native_panel: Path,
) -> None:
    lines, calls = _run(native_panel, "delayed-font")
    assert lines == ["settled"]
    assert calls == ["resume", "configuration", "home", "resume", "shutdown"]
    assert (native_panel / "app").read_text().strip() == "stopped"
    assert not (native_panel / "pending").exists()
    assert not (native_panel / "proc/100").exists()

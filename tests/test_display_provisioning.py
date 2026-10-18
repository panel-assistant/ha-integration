"""Display and text size at setup: the real programs on a stand-in panel."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from custom_components.panel_assistant import display_provisioning as dp
from custom_components.panel_assistant.adb_protocol import AdbInstallTarget
from custom_components.panel_assistant.install_adb import (
    async_apply_display_sizing,
    async_restore_display_sizing,
)

from .install_adb_fakes import FakeDevice, _identity_root_output, _install_fakes
from .test_install_adb import (  # noqa: F401 - fixtures resolve by parameter name
    signer,
    target,
)

# `wm density` and `settings` as Android 11 answers them, keeping one panel's
# state in files. The factory density is 240.
_STAND_IN = r"""
wm() {
  if [ "$1" != density ]; then return 1; fi
  if [ -f "$STATE/wm_unreadable" ]; then return 1; fi
  if [ -z "$2" ]; then
    echo "Physical density: 240"
    [ ! -f "$STATE/density" ] || echo "Override density: $(cat "$STATE/density")"
  elif [ "$2" = reset ]; then
    echo "wm density reset" >> "$STATE/calls"; rm -f "$STATE/density"
  else
    echo "wm density $2" >> "$STATE/calls"; echo "$2" > "$STATE/density"
  fi
}
settings() {
  if [ "$1" = get ]; then
    if [ -f "$STATE/$3" ]; then cat "$STATE/$3"; else echo null; fi
  elif [ "$1" = delete ]; then
    echo "settings $*" >> "$STATE/calls"; rm -f "$STATE/$3"
  else
    echo "settings $*" >> "$STATE/calls"; printf '%s\n' "$4" > "$STATE/$3"
  fi
}
"""

_X2I = dp.DisplaySizing(216, 1.0)


def _run(program: str, state: Path, record: Path | None = None) -> list[str]:
    program = program.replace(
        dp.DISPLAY_RESTORE_RECORD, str(record or state / "display-record")
    )
    out = subprocess.run(
        ["/bin/sh", "-c", _STAND_IN + program],
        capture_output=True,
        check=True,
        text=True,
        env={"PATH": os.environ["PATH"], "STATE": str(state)},
    ).stdout.splitlines()
    assert out[0].startswith("HAPANELD_DISPLAY") and out[-1].endswith(":0")
    return out[1:-1]


def _calls(state: Path) -> list[str]:
    path = state / "calls"
    return path.read_text().splitlines() if path.exists() else []


def _value(state: Path, name: str) -> str | None:
    path = state / name
    return path.read_text().strip() if path.exists() else None


def test_a_fresh_x2i_gets_216_and_a_second_setup_writes_nothing(
    tmp_path: Path,
) -> None:
    result = dp.parse_display_sizing(
        _run(dp.display_sizing_command("0" * 32, _X2I), tmp_path), _X2I
    )

    assert result.converged
    assert result.record == ("none", "null")
    assert _calls(tmp_path) == ["wm density 216"]
    # Android reads an unset text size as 1, so the recommended 1 is no write.
    assert _value(tmp_path, "font_scale") is None

    (tmp_path / "calls").unlink()
    again = dp.parse_display_sizing(
        _run(dp.display_sizing_command("1" * 32, _X2I), tmp_path), _X2I
    )
    assert again.converged and again.record == ("none", "null")
    assert _calls(tmp_path) == []


def test_a_recommended_text_size_is_written_once(tmp_path: Path) -> None:
    sizing = dp.DisplaySizing(None, 0.9)
    (tmp_path / "font_scale").write_text("1.0\n")

    result = dp.parse_display_sizing(
        _run(dp.display_sizing_command("0" * 32, sizing), tmp_path), sizing
    )

    assert result.converged and result.record == ("none", "1.0")
    assert _calls(tmp_path) == ["settings put system font_scale 0.9"]
    assert _value(tmp_path, "density") is None


def test_nothing_changes_when_the_record_cannot_be_kept(tmp_path: Path) -> None:
    result = dp.parse_display_sizing(
        _run(
            dp.display_sizing_command("0" * 32, _X2I),
            tmp_path,
            tmp_path / "missing" / "record",
        ),
        _X2I,
    )

    assert not result.converged
    assert result.record is None
    assert _calls(tmp_path) == []


def test_an_unreadable_density_is_never_recorded_or_overwritten(
    tmp_path: Path,
) -> None:
    (tmp_path / "wm_unreadable").touch()

    result = dp.parse_display_sizing(
        _run(dp.display_sizing_command("0" * 32, _X2I), tmp_path), _X2I
    )

    assert not result.converged and result.density == "unknown"
    assert _calls(tmp_path) == []
    assert not (tmp_path / "display-record").exists()


@pytest.mark.parametrize(("density", "font_scale"), [(None, None), ("200", "1.15")])
def test_removal_gives_back_what_the_panel_had_once(
    tmp_path: Path, density: str | None, font_scale: str | None
) -> None:
    if density is not None:
        (tmp_path / "density").write_text(density + "\n")
    if font_scale is not None:
        (tmp_path / "font_scale").write_text(font_scale + "\n")
    _run(dp.display_sizing_command("0" * 32, dp.DisplaySizing(216, 0.9)), tmp_path)
    assert _value(tmp_path, "density") == "216"

    restored = _run(dp.display_restore_command("1" * 32), tmp_path)

    assert dp.parse_display_restore(restored) == "restored"
    assert _value(tmp_path, "density") == density
    assert _value(tmp_path, "font_scale") == font_scale
    assert not (tmp_path / "display-record").exists()
    assert (
        dp.parse_display_restore(_run(dp.display_restore_command("2" * 32), tmp_path))
        == "none"
    )


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        (
            {
                "recommended": {"density": 216, "font_scale": 1},
                "privileged_sizing": False,
            },
            dp.DisplaySizing(216, 1.0),
        ),
        (
            {
                "recommended": {"density": None, "font_scale": 0.9},
                "privileged_sizing": False,
            },
            dp.DisplaySizing(None, 0.9),
        ),
        (
            {
                "recommended": {"density": 216, "font_scale": 1},
                "privileged_sizing": True,
            },
            None,
        ),
        ({"recommended": {"density": 216, "font_scale": 1}}, None),
        (
            {
                "recommended": {"density": None, "font_scale": None},
                "privileged_sizing": False,
            },
            None,
        ),
        (
            {
                "recommended": {"density": "216", "font_scale": 1},
                "privileged_sizing": False,
            },
            None,
        ),
        (
            {
                "recommended": {"density": True, "font_scale": 1},
                "privileged_sizing": False,
            },
            None,
        ),
        (
            {
                "recommended": {"density": 216, "font_scale": 3},
                "privileged_sizing": False,
            },
            None,
        ),
        ({"physical_pixels": {"width": 720, "height": 1440}}, None),
    ],
)
def test_only_a_rootless_panels_valid_recommendation_is_applied(
    display: dict[str, Any], expected: dp.DisplaySizing | None
) -> None:
    assert dp.recommended_sizing(display) == expected


def _shell(command: str, state: Path) -> bytes:
    return subprocess.run(
        [
            "/bin/sh",
            "-c",
            _STAND_IN
            + command.replace(dp.DISPLAY_RESTORE_RECORD, str(state / "display-record")),
        ],
        capture_output=True,
        check=True,
        env={"PATH": os.environ["PATH"], "STATE": str(state)},
    ).stdout


class _PanelDevice(FakeDevice):
    """Answers the identity check, then runs the program on the stand-in."""

    def __init__(self, state: Path) -> None:
        super().__init__([])
        self.state = state

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        self.commands.append(command)
        match = re.search(r"HAPANELD_([A-Z_]+?)_BEGIN:([0-9a-f]{32})", command)
        assert match is not None, command
        prefix, nonce = match.groups()
        if prefix == "POSTURE":
            yield _identity_root_output(nonce)
            return
        yield _shell(command, self.state)


async def test_setup_and_removal_run_over_the_panels_adb_session(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    tmp_path: Path,
) -> None:
    _install_fakes(monkeypatch, [_PanelDevice(tmp_path)])
    result = await async_apply_display_sizing(target, signer, _X2I)
    assert result.converged
    assert _value(tmp_path, "density") == "216"

    _install_fakes(monkeypatch, [_PanelDevice(tmp_path)])
    assert await async_restore_display_sizing(target, signer) == "restored"
    assert _value(tmp_path, "density") is None

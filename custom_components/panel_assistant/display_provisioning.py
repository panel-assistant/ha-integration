"""Display size and text size Panel Assistant applies at setup, and hands back.

A device profile in the panel app can recommend a display density and a text
size for its screen; the Shelly X2i's text is too large at its factory density,
for example. The app applies those itself only through a privileged route,
which a rootless panel does not have, but the ADB shell Panel Assistant
installs through can run ``wm density`` and write ``font_scale``. So at setup,
and only then, Panel Assistant reads the recommendation from the panel and
applies it over that shell when the panel says it has no privileged route of
its own. A panel that can size itself keeps doing so, and an owner's later
change is never fought: nothing re-applies it on an update.

As with the vendor setup, the values the panel had are recorded on the panel
once, before anything changes, in a file its shell owns; removal through Panel
Assistant reads it and puts them back. Both install paths (network ADB here,
browser USB in ``display-provisioning.mjs``) send the same program, compared
by value in a test.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

DISPLAY_RESTORE_RECORD: Final = "/data/local/tmp/panel-assistant-display-restore"
#: ``wm density`` refuses values outside what the app's own sizing allows.
DENSITY_RANGE: Final = (80, 640)
FONT_SCALE_RANGE: Final = (0.5, 1.5)

_QUIET: Final = ">/dev/null 2>&1"
_DENSITY = re.compile(r"[0-9]{2,3}", flags=re.ASCII)
_FONT_SCALE = re.compile(r"[0-9]{1,2}(?:\.[0-9]{1,6})?", flags=re.ASCII)
# The override ``wm density`` reports, ``none`` without one, ``unknown`` when
# its answer cannot be read.
_DENSITY_FUNCTION: Final = (
    "hp_density() { hp_w=$(wm density 2>/dev/null) || { echo unknown; return; }; "
    'case "$hp_w" in *"Override density: "*) '
    "hp_w=${hp_w##*Override density: }; hp_w=${hp_w%%[!0-9]*}; "
    'echo "${hp_w:-unknown}" ;; *"Physical density: "*) echo none ;; '
    "*) echo unknown ;; esac; }"
)


@dataclass(frozen=True, slots=True)
class DisplaySizing:
    """The density and text size a panel's profile recommends."""

    density: int | None
    font_scale: float | None


def font_scale_text(value: float) -> str:
    """The spelling both installers write: ``1`` and ``0.9``, never ``1.0``."""
    return f"{value:g}"


def recommended_sizing(display: Mapping[str, object]) -> DisplaySizing | None:
    """What to apply from the panel's ``/api/v1/display``, or None.

    None when the panel sizes itself through a privileged route, recommends
    nothing, or answers in a shape this does not know.
    """
    if display.get("privileged_sizing") is not False:
        return None
    recommended = display.get("recommended")
    if not isinstance(recommended, Mapping):
        return None
    density = recommended.get("density")
    font_scale = recommended.get("font_scale")
    if density is not None and (
        type(density) is not int or not DENSITY_RANGE[0] <= density <= DENSITY_RANGE[1]
    ):
        return None
    if font_scale is not None and (
        type(font_scale) not in (int, float)
        or not FONT_SCALE_RANGE[0] <= font_scale <= FONT_SCALE_RANGE[1]
    ):
        return None
    if density is None and font_scale is None:
        return None
    return DisplaySizing(density, None if font_scale is None else float(font_scale))


async def async_read_recommended_sizing(client: Any) -> DisplaySizing | None:
    """Ask the panel what to apply; None when it cannot say or needs nothing.

    Best-effort: a panel older than the release that reports its
    recommendation, or one that cannot be reached, simply gets no sizing.
    """
    try:
        display = await client.async_get_display()
    except Exception:
        _LOGGER.debug("Panel did not report its display; no sizing applied")
        return None
    return recommended_sizing(display)


def display_sizing_command(nonce: str, sizing: DisplaySizing) -> str:
    """Record the panel's values once, apply the recommendation, read it back.

    Every write is skipped when Android already reads the wanted value, so a
    second setup writes nothing. Nothing changes unless the record reads back.
    """
    path = DISPLAY_RESTORE_RECORD
    record = (
        f"if [ ! -f {path} ]; then hp_d=$(hp_density); "
        'if [ "$hp_d" != unknown ] && hp_f=$(settings get system font_scale); then '
        'printf \'density=%s\\nfont_scale=%s\\n\' "$hp_d" "$hp_f" '
        f"> {path}.part && mv {path}.part {path}; fi; rm -f {path}.part; fi"
    )
    writes = []
    if sizing.density is not None:
        writes.append(
            f'[ "$(hp_density)" = {sizing.density} ] || '
            f"wm density {sizing.density} {_QUIET}"
        )
    if sizing.font_scale is not None:
        scale = font_scale_text(sizing.font_scale)
        # Android reads an unset text size as 1.
        same = f"{scale}|1.0|null" if scale == "1" else scale
        writes.append(
            f'case "$(settings get system font_scale)" in {same}) ;; '
            f"*) settings put system font_scale {scale} {_QUIET} ;; esac"
        )
    return "; ".join(
        (
            f"echo HAPANELD_DISPLAY_BEGIN:{nonce}",
            _DENSITY_FUNCTION,
            record,
            f"if [ -f {path} ]; then "
            'while IFS= read -r hp_line; do echo "record:$hp_line"; '
            f"done < {path}; " + "; ".join(writes) + "; fi",
            'echo "density=$(hp_density)"',
            'echo "font_scale=$(settings get system font_scale)"',
            f"echo HAPANELD_DISPLAY_END:{nonce}:0",
        )
    )


def display_restore_command(nonce: str) -> str:
    """Put back the recorded values, then forget the record once they read back.

    A panel without a record had nothing changed, and is left alone.
    """
    path = DISPLAY_RESTORE_RECORD
    restore = "; ".join(
        (
            "hp_d=; hp_f=",
            'while IFS= read -r hp_line; do case "$hp_line" in '
            "density=*) hp_d=${hp_line#*=} ;; font_scale=*) hp_f=${hp_line#*=} ;; "
            f"esac; done < {path}",
            f'case "$hp_d" in none) wm density reset {_QUIET} ;; '
            f'[0-9]*) wm density "$hp_d" {_QUIET} ;; esac',
            f'if [ "$hp_f" = null ]; then settings delete system font_scale {_QUIET}; '
            'elif [ -n "$hp_f" ]; then '
            f'settings put system font_scale "$hp_f" {_QUIET}; fi',
            'if [ "$(hp_density)" = "$hp_d" ] && '
            '[ "$(settings get system font_scale)" = "$hp_f" ]; then '
            f"rm -f {path}; echo restored; else echo unrestored; fi",
        )
    )
    return "; ".join(
        (
            f"echo HAPANELD_DISPLAY_RESTORE_BEGIN:{nonce}",
            _DENSITY_FUNCTION,
            f"if [ -f {path} ]; then {restore}; else echo none; fi",
            f"echo HAPANELD_DISPLAY_RESTORE_END:{nonce}:0",
        )
    )


class DisplayResponseError(ValueError):
    """The panel's answer was not one of the shapes the program prints."""


@dataclass(frozen=True, slots=True)
class DisplayResult:
    """What the panel read back after the sizing program."""

    #: The panel's density override (``none``) and text size before setup.
    record: tuple[str, str] | None
    density: str
    font_scale: str
    sizing: DisplaySizing

    @property
    def converged(self) -> bool:
        """The recommendation reads back, and the values to undo it are kept."""
        if self.record is None:
            return False
        if self.sizing.density is not None and self.density != str(self.sizing.density):
            return False
        if self.sizing.font_scale is None:
            return True
        scale = font_scale_text(self.sizing.font_scale)
        return self.font_scale == scale or (
            scale == "1" and self.font_scale in ("1.0", "null")
        )


def _density_value(value: str) -> bool:
    return value in ("none", "unknown") or _DENSITY.fullmatch(value) is not None


def _font_value(value: str) -> bool:
    return value == "null" or _FONT_SCALE.fullmatch(value) is not None


def parse_display_sizing(lines: list[str], sizing: DisplaySizing) -> DisplayResult:
    """Read the program's answer; any other shape is refused."""
    records = [line.removeprefix("record:") for line in lines if line[:7] == "record:"]
    rest = [line for line in lines if line[:7] != "record:"]
    if len(rest) != 2 or len(records) not in (0, 2):
        raise DisplayResponseError
    density = rest[0].removeprefix("density=")
    font_scale = rest[1].removeprefix("font_scale=")
    if (
        density == rest[0]
        or font_scale == rest[1]
        or not _density_value(density)
        or not _font_value(font_scale)
    ):
        raise DisplayResponseError
    record: tuple[str, str] | None = None
    if records:
        old_density = records[0].removeprefix("density=")
        old_font = records[1].removeprefix("font_scale=")
        if (
            old_density == records[0]
            or old_font == records[1]
            or old_density == "unknown"
            or not _density_value(old_density)
            or not _font_value(old_font)
        ):
            raise DisplayResponseError
        record = (old_density, old_font)
    return DisplayResult(record, density, font_scale, sizing)


def parse_display_restore(lines: list[str]) -> str:
    """``restored``, ``unrestored``, or ``none`` when nothing was recorded."""
    if len(lines) != 1 or lines[0] not in ("restored", "unrestored", "none"):
        raise DisplayResponseError
    return lines[0]

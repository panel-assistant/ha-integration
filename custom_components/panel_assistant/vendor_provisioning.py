"""Vendor setup Panel Assistant applies during installation, and hands back.

This is Panel Assistant's custom support: the panel- and vendor-specific steps
on its install, update, repair and removal paths live here and in the browser
twin ``vendor-provisioning.mjs``, so generic code holds no vendor names. The
grant step that every install, update and repair runs appends
``VENDOR_PROBE`` and asks ``needs_vendor_setup``; removal runs the restore
program, which acts only on a record a setup left. A device profile cannot
select these steps, because they run over ADB before the app answers and on
panels whose installed build predates any such profile field.

Panels that use it, and why:

- Meta Portal (codenames ``omni`` and ``aloha``): Meta's dream, swipe-up bar
  and package verifier, below.
- Shelly Wall Display (any with Shelly's ``cloud.shelly.stargate`` launcher):
  the launcher's corner overlay, below.

Two vendor recipes. Meta's Portal: Meta's dream service starts on every sleep
whatever ``screensaver_enabled`` says and sends HOME, and Meta's control centre
draws a swipe-up bar over the panel app. Installation blanks both dream
components and denies that bar its overlay; removal puts back what was there
before. Shelly's Wall Displays: Shelly's launcher draws a corner overlay over
every app; installation denies it that overlay, which leaves the launcher
itself installed and reachable from the panel app's launcher button. It is
found by its package, not a codename, so every Wall Display on that firmware
gets it, and removal puts its overlay mode back.

Meta also ships a package verifier (``com.facebook.appverifier``) that rejects
every app Meta did not sign, so the Panel Assistant app could never update
itself through Android's tap-confirmed installer. Meta has ended support, so
installation switches the global ``package_verifier_enable`` off and removal
puts back the value it found. That value has its own record: Portals set up
before it hold the screensaver record, written once, and gain this one on
their next setup.

The values Meta had are recorded on the panel itself before anything changes,
in a file the panel's shell owns. That record is the one place both install
paths (network ADB here, browser USB in ``vendor-provisioning.mjs``) write and
the removal path reads: a USB-installed panel has nothing in Home Assistant to
carry it, and the record outlives the app it was made for. It is written once:
a retry reads the blanked values back, and must never record those as Meta's.
Nothing is changed until the record reads back, so a panel never holds a
change it could not undo.

The programs are shell text sent unchanged by both installers; the browser
module states them again by value and a test compares the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Final

#: Meta's device codenames for the Portals with a screen: ``omni`` is the
#: Android 10 (2nd gen) image, ``aloha`` the Android 9 (1st gen) one. The app's
#: ``meta-portal`` device profile matches the same two.
META_PORTAL_DEVICES: Final = ("omni", "aloha")
#: Meta's own values, recorded in place of a value someone already blanked.
META_SCREENSAVER_COMPONENTS: Final = (
    "com.facebook.alohaapps.launcher/"
    "com.facebook.aloha.app.home.touch.HomeDreamService,"
    "com.facebook.alohaapps.superframe/"
    "com.facebook.alohaapps.superframe.SuperframeDream"
)
META_SCREENSAVER_DEFAULT_COMPONENT: Final = (
    "com.facebook.alohaapps.launcher/com.facebook.aloha.app.home.touch.HomeDreamService"
)
META_CONTROL_CENTER: Final = "com.facebook.alohaapps.controlcenter"
META_RESTORE_RECORD: Final = "/data/local/tmp/panel-assistant-meta-portal-restore"
#: Shelly's launcher, which draws the corner overlay on every Wall Display.
SHELLY_LAUNCHER: Final = "cloud.shelly.stargate"
SHELLY_RESTORE_RECORD: Final = "/data/local/tmp/panel-assistant-shelly-restore"
META_VERIFIER_RECORD: Final = (
    "/data/local/tmp/panel-assistant-meta-portal-verifier-restore"
)
_VERIFIER: Final = "package_verifier_enable"

_SETTINGS: Final = ("screensaver_components", "screensaver_default_component")
_OVERLAY_MODES: Final = ("allow", "ignore", "deny", "default", "foreground")
_RECORD_VALUE = re.compile(r"[\x20-\x7e]{0,4096}", flags=re.ASCII)

# The control centre's overlay mode as one word: absent when Meta's package is
# not there, unknown when Android's answer cannot be read.
_OVERLAY_FUNCTION: Final = (
    "hp_overlay() { "
    f'if [ -z "$(pm path {META_CONTROL_CENTER} 2>/dev/null)" ]; '
    "then echo absent; return; fi; "
    f"hp_mode=$(appops get {META_CONTROL_CENTER} SYSTEM_ALERT_WINDOW 2>/dev/null)"
    " || { echo unknown; return; }; "
    'case "$hp_mode" in "SYSTEM_ALERT_WINDOW: "*) '
    "hp_mode=${hp_mode#SYSTEM_ALERT_WINDOW: }; hp_mode=${hp_mode%%;*} ;; "
    '*"Default mode: "*) hp_mode=${hp_mode##*Default mode: } ;; '
    "*) hp_mode=unknown ;; esac; "
    f'case "$hp_mode" in {"|".join(_OVERLAY_MODES)}) echo "$hp_mode" ;; '
    "*) echo unknown ;; esac; }"
)
_QUIET: Final = ">/dev/null 2>&1"
# The same word for Shelly's launcher.
_SHELLY_OVERLAY_FUNCTION: Final = _OVERLAY_FUNCTION.replace(
    "hp_overlay()", "hp_shelly_overlay()"
).replace(META_CONTROL_CENTER, SHELLY_LAUNCHER)


#: Read by the install grant step after its HOME readback: the device codename
#: and whether Shelly's launcher is installed. ``needs_vendor_setup`` reads the
#: two lines back.
VENDOR_PROBE: Final = (
    'echo "device:$(getprop ro.product.device)"',
    f'if [ -n "$(pm path {SHELLY_LAUNCHER} 2>/dev/null)" ]; '
    "then echo vendor:shelly; else echo vendor:none; fi",
)


def needs_vendor_setup(device: str, vendor: str) -> bool | None:
    """Whether ``VENDOR_PROBE`` found a panel a recipe here serves.

    None when either line is not one the probe prints.
    """
    if not device.startswith("device:") or vendor not in (
        "vendor:shelly",
        "vendor:none",
    ):
        return None
    return device.removeprefix("device:") in META_PORTAL_DEVICES or (
        vendor == "vendor:shelly"
    )


def _read_back() -> tuple[str, ...]:
    return (
        *(
            f"hp_value=$(settings get secure {name}) "
            f'&& echo "{name}=$hp_value" || echo unreadable:{name}'
            for name in _SETTINGS
        ),
        'echo "controlcenter_overlay=$(hp_overlay)"',
        f"hp_value=$(settings get global {_VERIFIER}) "
        f'&& echo "{_VERIFIER}=$hp_value" || echo unreadable:{_VERIFIER}',
    )


def _verifier_setup() -> tuple[str, str, str]:
    """Record Meta's verifier setting once, show the record, then switch it off.

    A value already off is recorded as Meta's default, on, as a blanked dream
    is recorded as Meta's dream.
    """
    path = META_VERIFIER_RECORD
    record = (
        f"if [ ! -f {path} ]; then "
        f"if hp_v=$(settings get global {_VERIFIER}); then "
        '[ "$hp_v" != 0 ] || hp_v=1; '
        f"printf '{_VERIFIER}=%s\\n' \"$hp_v\" "
        f"> {path}.part && mv {path}.part {path}; "
        f"rm -f {path}.part; fi; fi"
    )
    show_record = (
        f"if [ -f {path} ]; then "
        'while IFS= read -r hp_line; do echo "verifier_record:$hp_line"; '
        f"done < {path}; fi"
    )
    apply = (
        f"if [ -f {path} ]; then "
        f'hp_value=$(settings get global {_VERIFIER}) && [ "$hp_value" = 0 ] || '
        f"settings put global {_VERIFIER} 0 {_QUIET}; fi"
    )
    return record, show_record, apply


def _shelly_setup() -> str:
    """Record the launcher's overlay mode once, then deny it, and read it back.

    Only a mode Android actually reports is recorded. When the mode cannot be
    read nothing is recorded and nothing is denied, so removal can always put
    back the launcher's real mode; the next launch or update tries again.
    """
    path = SHELLY_RESTORE_RECORD
    return "; ".join(
        (
            _SHELLY_OVERLAY_FUNCTION,
            "echo vendor:shelly",
            f"if [ ! -f {path} ]; then "
            'hp_o=$(hp_shelly_overlay); case "$hp_o" in '
            "allow|ignore|deny|default|foreground) "
            f"printf 'stargate_overlay=%s\\n' \"$hp_o\" > {path}.part && "
            f"mv {path}.part {path}; rm -f {path}.part ;; esac; fi",
            f"if [ -f {path} ]; then "
            'while IFS= read -r hp_line; do echo "record:$hp_line"; '
            f"done < {path}; "
            'case "$(hp_shelly_overlay)" in absent|deny) ;; '
            f"*) appops set {SHELLY_LAUNCHER} SYSTEM_ALERT_WINDOW deny {_QUIET} ;; "
            "esac; fi",
            'echo "stargate_overlay=$(hp_shelly_overlay)"',
        )
    )


def vendor_setup_command(nonce: str) -> str:
    """Record Meta's values once, then blank the dreams and deny the bar.

    Every write is skipped when Android already reads the wanted value, so a
    retry writes nothing; each value is read back after.
    """
    path = META_RESTORE_RECORD
    record = (
        f"if [ ! -f {path} ]; then "
        "if hp_c=$(settings get secure screensaver_components) && "
        "hp_d=$(settings get secure screensaver_default_component); then "
        f"[ -n \"$hp_c\" ] || hp_c='{META_SCREENSAVER_COMPONENTS}'; "
        f"[ -n \"$hp_d\" ] || hp_d='{META_SCREENSAVER_DEFAULT_COMPONENT}'; "
        'hp_o=$(hp_overlay); case "$hp_o" in deny|unknown) hp_o=allow ;; esac; '
        "printf 'screensaver_components=%s\\nscreensaver_default_component=%s\\n"
        'controlcenter_overlay=%s\\n\' "$hp_c" "$hp_d" "$hp_o" '
        f"> {path}.part && mv {path}.part {path}; "
        f"rm -f {path}.part; fi; fi"
    )
    show_record = (
        f"if [ -f {path} ]; then "
        'while IFS= read -r hp_line; do echo "record:$hp_line"; '
        f"done < {path}; fi"
    )
    apply = (
        f"if [ -f {path} ]; then "
        + "".join(
            f'hp_value=$(settings get secure {name}) && [ -z "$hp_value" ] || '
            f"settings put secure {name} '' {_QUIET}; "
            for name in _SETTINGS
        )
        + 'case "$(hp_overlay)" in absent|deny) ;; '
        f"*) appops set {META_CONTROL_CENTER} SYSTEM_ALERT_WINDOW deny {_QUIET} ;; "
        "esac; fi"
    )
    return "; ".join(
        (
            f"echo HAPANELD_VENDOR_BEGIN:{nonce}",
            f'case "$(getprop ro.product.device)" in '
            f"{'|'.join(META_PORTAL_DEVICES)}) "
            + "; ".join(
                (
                    _OVERLAY_FUNCTION,
                    record,
                    show_record,
                    apply,
                    *_verifier_setup(),
                    *_read_back(),
                )
            )
            + f' ;; *) if [ -n "$(pm path {SHELLY_LAUNCHER} 2>/dev/null)" ]; then '
            + _shelly_setup()
            + "; else echo device:other; fi ;; esac",
            f"echo HAPANELD_VENDOR_END:{nonce}:0",
        )
    )


def vendor_restore_command(nonce: str) -> str:
    """Put back the recorded values, then forget the record once they read back.

    Each record is deleted only when every value it holds, the overlay mode
    included, reads back exactly as recorded; otherwise it stays for the next
    attempt. A panel without either record had nothing changed, and is left
    alone. One line answers for both: ``restored`` only when no record is left.
    """
    path = META_RESTORE_RECORD
    load = (
        'while IFS= read -r hp_line; do case "$hp_line" in '
        + " ".join(
            f"{name}=*) {var}=${{hp_line#*=}} ;;"
            for name, var in (
                ("screensaver_components", "hp_c"),
                ("screensaver_default_component", "hp_d"),
                ("controlcenter_overlay", "hp_o"),
            )
        )
        + f" esac; done < {path}"
    )
    put_back = "; ".join(
        f'if [ "${var}" = null ]; then settings delete secure {name} {_QUIET}; '
        f'else settings put secure {name} "${var}" {_QUIET}; fi'
        for name, var in (
            ("screensaver_components", "hp_c"),
            ("screensaver_default_component", "hp_d"),
        )
    )
    allow = (
        'case "$hp_o" in allow|ignore|default|foreground) '
        f'[ -n "$(pm path {META_CONTROL_CENTER} 2>/dev/null)" ] && '
        f'appops set {META_CONTROL_CENTER} SYSTEM_ALERT_WINDOW "$hp_o" {_QUIET} ;; esac'
    )
    # The overlay must read back as the mode recorded, not merely as something
    # other than deny: an unknown, absent or different answer keeps the record,
    # so a later hand-back can try again. A control centre recorded absent was
    # never denied, so it is restored only while it is still absent.
    verify = (
        "hp_now=$(hp_overlay); "
        'if [ "$(settings get secure screensaver_components)" = "$hp_c" ] && '
        '[ "$(settings get secure screensaver_default_component)" = "$hp_d" ] && '
        'case "$hp_o" in absent|allow|ignore|default|foreground) '
        '[ "$hp_now" = "$hp_o" ] ;; *) false ;; esac; '
        f"then rm -f {path}; else hp_r=unrestored; fi"
    )
    verifier = META_VERIFIER_RECORD
    put_back_verifier = (
        f"if [ -f {verifier} ]; then hp_v=; "
        f'while IFS= read -r hp_line; do case "$hp_line" in '
        f"{_VERIFIER}=*) hp_v=${{hp_line#*=}} ;; esac; done < {verifier}; "
        'if [ -z "$hp_v" ]; then hp_r=unrestored; else '
        f'if [ "$hp_v" = null ]; then settings delete global {_VERIFIER} {_QUIET}; '
        f'else settings put global {_VERIFIER} "$hp_v" {_QUIET}; fi; '
        f'if [ "$(settings get global {_VERIFIER})" = "$hp_v" ]; '
        f"then rm -f {verifier}; else hp_r=unrestored; fi; fi; fi"
    )
    shelly = SHELLY_RESTORE_RECORD
    shelly_restore = "; ".join(
        (
            "hp_o=",
            'while IFS= read -r hp_line; do case "$hp_line" in '
            f"stargate_overlay=*) hp_o=${{hp_line#*=}} ;; esac; done < {shelly}",
            'case "$hp_o" in allow|ignore|deny|default|foreground) '
            f'[ -n "$(pm path {SHELLY_LAUNCHER} 2>/dev/null)" ] && '
            f'appops set {SHELLY_LAUNCHER} SYSTEM_ALERT_WINDOW "$hp_o" {_QUIET} '
            ";; esac",
            'hp_now=$(hp_shelly_overlay); if [ "$hp_now" = absent ] || '
            '[ "$hp_now" = "$hp_o" ]; then '
            f"rm -f {shelly}; echo restored; else echo unrestored; fi",
        )
    )
    return "; ".join(
        (
            f"echo HAPANELD_VENDOR_RESTORE_BEGIN:{nonce}",
            _OVERLAY_FUNCTION,
            _SHELLY_OVERLAY_FUNCTION,
            f"if [ -f {path} ] || [ -f {verifier} ]; then hp_r=restored; "
            f"if [ -f {path} ]; then hp_c=; hp_d=; hp_o=; "
            + "; ".join((load, put_back, allow, verify))
            + "; fi; "
            + put_back_verifier
            + '; echo "$hp_r"; '
            + f"elif [ -f {shelly} ]; then {shelly_restore}; else echo none; fi",
            f"echo HAPANELD_VENDOR_RESTORE_END:{nonce}:0",
        )
    )


@dataclass(frozen=True, slots=True)
class VendorSetup:
    """What a Portal reported after its vendor setup, read in the same shell."""

    #: Logged when the setup did not converge.
    warning: ClassVar[str] = (
        "The panel did not take Panel Assistant's Meta Portal setup: Meta's "
        "screensaver or its swipe-up bar may still show over the panel app"
    )

    #: Meta's values as recorded before the change, or None with no record.
    record: tuple[str, str, str] | None
    screensaver_components: str | None
    screensaver_default_component: str | None
    #: allow, deny, ..., ``absent`` without Meta's package, or ``unknown``.
    control_center_overlay: str
    #: Meta's ``package_verifier_enable`` as recorded, or None with no record.
    verifier_record: str | None
    package_verifier_enable: str | None

    @property
    def converged(self) -> bool:
        """Dreams blank, bar denied, verifier off, and the undo on record."""
        return (
            self.record is not None
            and self.screensaver_components == ""
            and self.screensaver_default_component == ""
            and self.control_center_overlay in ("deny", "absent")
            and self.verifier_record is not None
            and self.package_verifier_enable == "0"
        )


@dataclass(frozen=True, slots=True)
class ShellySetup:
    """What a panel with Shelly's launcher reported after its vendor setup."""

    #: Logged when the setup did not converge.
    warning: ClassVar[str] = (
        "The panel did not take Panel Assistant's Shelly setup: Shelly's "
        "corner overlay may still show over the panel app"
    )

    #: The launcher's overlay mode as recorded before the change, or None.
    record: str | None
    #: allow, deny, ..., ``absent`` without the launcher, or ``unknown``.
    launcher_overlay: str

    @property
    def converged(self) -> bool:
        """Overlay denied, and the mode to undo it on record."""
        return self.record is not None and self.launcher_overlay in ("deny", "absent")


#: Logged when the panel's answer to the setup could not be read.
UNREAD_SETUP_WARNING: Final = (
    "The panel did not take Panel Assistant's vendor setup: on a Meta "
    "Portal, Meta's screensaver or its swipe-up bar may still show over "
    "the panel app; on a Shelly Wall Display, Shelly's corner overlay"
)
#: Logged by removal, with the restore outcome, when a record was not put back.
RESTORE_WARNING: Final = (
    "Could not put back the vendor settings Panel Assistant changed on "
    "this panel (%s): Meta's screensaver and swipe-up bar, or Shelly's "
    "corner overlay, stay off after the app is removed"
)


class VendorResponseError(ValueError):
    """The panel's answer was not one of the shapes the program prints."""


def _parse_shelly_setup(lines: list[str]) -> ShellySetup:
    modes = (*_OVERLAY_MODES, "absent", "unknown")
    records = [line for line in lines if line.startswith("record:")]
    rest = [line for line in lines if not line.startswith("record:")]
    if len(records) > 1 or len(rest) != 1:
        raise VendorResponseError
    overlay = rest[0].removeprefix("stargate_overlay=")
    if overlay == rest[0] or overlay not in modes:
        raise VendorResponseError
    record = None
    if records:
        record = records[0].removeprefix("record:stargate_overlay=")
        if record == records[0] or record not in _OVERLAY_MODES:
            raise VendorResponseError
    return ShellySetup(record, overlay)


def parse_vendor_setup(lines: list[str]) -> VendorSetup | ShellySetup | None:
    """Return the vendor's state, or None for a panel with no vendor setup."""
    if lines == ["device:other"]:
        return None
    if lines[:1] == ["vendor:shelly"]:
        return _parse_shelly_setup(lines[1:])
    records = [line.removeprefix("record:") for line in lines if line[:7] == "record:"]
    verifier_tag = "verifier_record:"
    verifier_records = [
        line.removeprefix(verifier_tag)
        for line in lines
        if line.startswith(verifier_tag)
    ]
    rest = [
        line
        for line in lines
        if line[:7] != "record:" and not line.startswith(verifier_tag)
    ]
    if len(rest) != 4 or len(records) not in (0, 3) or len(verifier_records) > 1:
        raise VendorResponseError
    values: list[str | None] = []
    for name, line in zip((*_SETTINGS, _VERIFIER), (*rest[:2], rest[3]), strict=True):
        if line == f"unreadable:{name}":
            values.append(None)
        elif line.startswith(f"{name}=") and _RECORD_VALUE.fullmatch(line):
            values.append(line.removeprefix(f"{name}="))
        else:
            raise VendorResponseError
    overlay = rest[2].removeprefix("controlcenter_overlay=")
    if overlay == rest[2] or overlay not in (*_OVERLAY_MODES, "absent", "unknown"):
        raise VendorResponseError
    record: tuple[str, str, str] | None = None
    if records:
        keys = (*_SETTINGS, "controlcenter_overlay")
        if not all(
            line.startswith(f"{key}=") and _RECORD_VALUE.fullmatch(line)
            for key, line in zip(keys, records, strict=True)
        ):
            raise VendorResponseError
        first, second, third = (line.split("=", 1)[1] for line in records)
        record = (first, second, third)
    verifier_record: str | None = None
    if verifier_records:
        line = verifier_records[0]
        if not (line.startswith(f"{_VERIFIER}=") and _RECORD_VALUE.fullmatch(line)):
            raise VendorResponseError
        verifier_record = line.removeprefix(f"{_VERIFIER}=")
    return VendorSetup(
        record, values[0], values[1], overlay, verifier_record, values[2]
    )


def parse_vendor_restore(lines: list[str]) -> str:
    """``restored``, ``unrestored``, or ``none`` when nothing was recorded."""
    if len(lines) != 1 or lines[0] not in ("restored", "unrestored", "none"):
        raise VendorResponseError
    return lines[0]

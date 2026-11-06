"""Make the installed build's dashboard the panel's HOME, both install paths.

``cmd package set-home-activity`` is asked first. Some firmware answers
Success and still resolves HOME to Android's chooser (Meta's Portal does): the
command only preselects the app there. On those panels the program presses
HOME, reads the chooser Android shows, and presses its Always button, but only
when the chooser's title names this app. The labels are the app's untranslated
``app_name`` values, so the title is matched in any language; a chooser that
offers another app, or has no Always button, is dismissed and never pressed.

Nothing here decides an install. The program sets ``hp_chooser`` to what it
did, the caller prints it beside the HOME readback, and a HOME that is still not
the dashboard is reported. The browser installer's ``home-chooser.mjs`` builds
the same text, and a test compares the two by value.
"""

from __future__ import annotations

from typing import Final

from .app_identity import (
    EQUIVALENT_HOME_COMPONENTS,
    HOME_COMPONENTS,
    PACKAGE_ID,
)

#: What HOME resolves to now: the last line names one component, or Android's
#: chooser (``android/…ResolverActivity``) when nothing is preferred.
HOME_QUERY: Final = (
    "cmd package resolve-activity --brief "
    "-a android.intent.action.MAIN -c android.intent.category.HOME 2>/dev/null"
    " | tail -n 1"
)
#: The app's ``app_name`` (not translated) by application id. Builds up to
#: 0.9.11 still carry the old name.
APP_LABELS: Final = {PACKAGE_ID: ("Panel Assistant", "ha-paneld")}
#: Where the chooser's window is dumped; removed straight after it is read.
CHOOSER_DUMP: Final = "/data/local/tmp/panel-assistant-home-chooser.xml"
#: Every word the program leaves in ``hp_chooser``.
CHOOSER_OUTCOMES: Final = (
    "deferred",  # First setup has not authorized ownership yet.
    "retained",  # The external renderer keeps its valid Android HOME.
    "kept",  # HOME was already the dashboard; nothing was sent.
    "set",  # set-home-activity took.
    "tapped",  # the chooser offered this app and Always was pressed.
    "other_app",  # the chooser offered another app; nothing was pressed.
    "no_button",  # no chooser with an enabled Always button was read.
    "other_home",  # HOME names another launcher outright; no chooser to press.
)

_QUIET: Final = ">/dev/null 2>&1"
_ALWAYS: Final = 'resource-id="android:id/button_always"'


def home_claim_command(
    launch_component: str,
    *,
    claim_allowed: str | None = None,
    claim_stdin: int | None = None,
) -> str:
    """Claim HOME and set hp_chooser, rechecking optional automatic authority."""
    homes = "|".join(
        f"'{name}'" for name in EQUIVALENT_HOME_COMPONENTS[launch_component]
    )
    dashboard = HOME_COMPONENTS[launch_component]
    labels = "|".join(
        f"*'{label}'*" for label in APP_LABELS[launch_component.partition("/")[0]]
    )
    # mksh closes shell-only descriptors when starting an external command.
    # Explicit stdin redirection retains the lock in each HOME command child.
    stdin = f" <&{claim_stdin}" if claim_stdin is not None else ""
    home_query = HOME_QUERY.replace("cmd ", f"cmd{stdin} ", 1)
    dump = CHOOSER_DUMP
    tap = (
        "input tap $(((hp_l+hp_r)/2)) $(((hp_t+hp_b)/2))"
        f"{stdin} {_QUIET}; hp_chooser=tapped; sleep 1"
    )
    if claim_allowed is not None:
        # Chooser discovery waits on Android. Re-read automatic setup intent
        # immediately before pressing Always, after those waits.
        tap = f"if {claim_allowed}; then {tap}; else hp_chooser=deferred; fi"
    chooser = "; ".join(
        (
            "hp_chooser=no_button",
            f"input keyevent 3{stdin} {_QUIET}",
            "hp_ui=",
            "hp_try=0",
            'while [ "$hp_try" -lt 3 ]; do hp_try=$((hp_try+1)); sleep 1; '
            f"rm -f {dump}; uiautomator dump {dump} {_QUIET}; "
            f"hp_ui=$(tr '>' '\\n' < {dump} 2>/dev/null); rm -f {dump}; "
            f"case \"$hp_ui\" in *'{_ALWAYS}'*) break ;; esac; done",
            "hp_always=$(printf '%s\\n' \"$hp_ui\" | "
            f"grep -F '{_ALWAYS}' | grep -F 'package=\"android\"' | "
            "grep -F 'enabled=\"true\"')",
            "hp_title=$(printf '%s\\n' \"$hp_ui\" | "
            "grep -F 'resource-id=\"android:id/title\"' | "
            "grep -F 'package=\"android\"' | "
            'sed -n \'s/.* text="\\([^"]*\\)".*/\\1/p\')',
            "hp_box=$(printf '%s\\n' \"$hp_always\" | sed -n "
            "'s/.* bounds=\"\\[\\([0-9]\\{1,5\\}\\),\\([0-9]\\{1,5\\}\\)\\]"
            "\\[\\([0-9]\\{1,5\\}\\),\\([0-9]\\{1,5\\}\\)\\]\".*/\\1 \\2 \\3 \\4/p')",
            # One Always button and one title, or nothing is pressed.
            "case \"$hp_always$hp_title$hp_box\" in *'\n'*) hp_always= ;; esac",
            'if [ -n "$hp_always" ] && [ -n "$hp_box" ]; then '
            f'case "$hp_title" in {labels}) '
            "hp_l=${hp_box%% *}; hp_box=${hp_box#* }; hp_t=${hp_box%% *}; "
            "hp_box=${hp_box#* }; hp_r=${hp_box%% *}; hp_b=${hp_box#* }; "
            'if [ "$hp_r" -gt "$hp_l" ] && [ "$hp_b" -gt "$hp_t" ]; then '
            f"{tap}; fi ;; "
            "*) hp_chooser=other_app ;; esac; fi",
            # Always opens the dashboard itself. Otherwise the chooser is
            # dismissed, which returns the panel to the app it showed before;
            # starting the app stays the caller's choice (an update never does).
            'if [ "$hp_chooser" != tapped ]; then '
            'case "$hp_ui" in *\'resource-id="android:id/button_\'*) '
            f"input keyevent 4{stdin} {_QUIET} ;; esac; fi",
        )
    )
    # Android's package shell command receives stdin across Binder. A deferred
    # caller supplies its lock descriptor so remote completion retains custody
    # even if the local cmd process exits before the service finishes.
    claim = (
        f"cmd package set-home-activity {dashboard}{stdin} {_QUIET}; "
        f'case "$({home_query})" in {homes}) hp_chooser=set ;; '
        f"android/*) {chooser} ;; "
        "*) hp_chooser=other_home ;; esac"
    )
    if claim_allowed is not None:
        claim = f"if {claim_allowed}; then {claim}; else hp_chooser=deferred; fi"
    return f'case "$({home_query})" in {homes}) hp_chooser=kept ;; *) {claim} ;; esac'

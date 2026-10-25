"""Write-ahead HOME custody in a clean install's existing staging namespace."""

from __future__ import annotations

from .app_identity import PACKAGE_ID
from .home_chooser import HOME_QUERY

_COMPONENT = "^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z.][A-Za-z0-9_.$]*$"
_PARENTS = " && ".join(
    f"[ ! -L {p} ]" for p in ("/data", "/data/local", "/data/local/tmp")
)


def _owned_file(path: str) -> str:
    return (
        f"{_PARENTS} && [ ! -L {path} ] && [ -f {path} ] && "
        f'[ "$(stat -c %h {path})" = 1 ] && '
        f'{{ [ "$(stat -c %u {path})" = 0 ] || '
        f'[ "$(stat -c %u {path})" = 2000 ]; }} && '
        f'[ "$(stat -c %a {path})" = 600 ] && '
        f'[ "$(stat -c %s {path})" -le 512 ]'
    )


def snapshot_previous_home(remote_apk: str) -> str:
    """Only create a job-owned record; never overwrite one on retry."""
    path = remote_apk + ".previous-home"
    return (
        f"if {_PARENTS}; then if [ -e {path} ] || [ -L {path} ]; "
        f"then {_owned_file(path)}; else hp_previous=$({HOME_QUERY}); "
        'case "$hp_previous" in android/*|com.android.settings/*|'
        "io.panelassistant.android/*|io.github.maxlyth.hapaneld/*) "
        "hp_previous=none ;; *) if ! printf '%s\\n' \"$hp_previous\" | "
        f"grep -Eq '{_COMPONENT}'; then hp_previous=none; fi ;; esac; "
        f"(umask 077; set -C; printf '%s\\n' \"$hp_previous\" > {path}); "
        "fi; else false; fi"
    )


def receiver_absence_check(package_id: str) -> str:
    """Prove the explicit receiver absent, including disabled components.

    Package summaries omit receivers without intent filters. Only a successful
    PackageManager query's exact empty result, plus installed-package evidence,
    admits old apps. Unknown commands, denied reads and other output fail closed.
    """
    receiver = (
        f"{package_id}/io.panelassistant.android.platform.PreservePreviousHomeReceiver"
    )
    return (
        "hp_receivers=$(cmd package query-receivers --query-flags 512 --user 0 "
        f"--brief --components -n {receiver} 2>/dev/null); hp_receivers_status=$?; "
        f"hp_package=$(dumpsys package {package_id} 2>/dev/null); "
        "hp_package_status=$?; "
        '[ "$hp_receivers_status" -eq 0 ] && '
        '[ "$hp_receivers" = "No receivers found" ] && '
        '[ "$hp_package_status" -eq 0 ] && '
        f"printf '%s\\n' \"$hp_package\" | grep -Fq 'Package [{package_id}]'"
    )


def transfer_previous_home(remote_apk: str) -> str:
    """Return success only after the shell record reaches the canonical app store."""
    path = remote_apk + ".previous-home"
    receiver = (
        f"{PACKAGE_ID}/io.panelassistant.android.platform.PreservePreviousHomeReceiver"
    )
    broadcast = f"am broadcast --include-stopped-packages -n {receiver}"
    return (
        f"if [ ! -e {path} ] && [ ! -L {path} ]; then true; "
        f"elif {_owned_file(path)}; then hp_previous=$(cat {path}); "
        'if [ "$hp_previous" = none ]; then hp_extra=; '
        "elif printf '%s\\n' \"$hp_previous\" | "
        f"grep -Eq '{_COMPONENT}'; then hp_extra=--es; "
        "else hp_extra=invalid; fi; "
        'if [ "$hp_extra" != invalid ]; then '
        f'if [ "$hp_extra" = --es ]; then hp_record=$({broadcast} '
        '--es previous_home_component "$hp_previous" 2>/dev/null); '
        f"else hp_record=$({broadcast} 2>/dev/null); fi; "
        'case "$hp_record" in *"Broadcast completed: result=1"*) '
        f"rm {path} ;; "
        # Only a direct receiver query can prove this is an old supported app.
        "*) if " + receiver_absence_check(PACKAGE_ID) + "; "
        f"then rm {path}; else false; fi ;; esac; else false; fi; else false; fi"
    )


def discard_previous_home(remote_apk: str) -> str:
    """Only for a job proven not installed; installed jobs retain retry custody."""
    path = remote_apk + ".previous-home"
    return (
        f"if [ ! -e {path} ] && [ ! -L {path} ]; then true; "
        f"elif {_owned_file(path)}; then rm {path}; else false; fi"
    )

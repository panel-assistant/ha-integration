"""Panel build admission owned by the running Panel Assistant."""

from homeassistant.config_entries import ConfigEntry

from .client import _version_key, is_valid_panel_version
from .const import (
    CONF_PRERELEASE_PANEL_BUILDS,
    INTEGRATION_VERSION,
    MAX_ANDROID_INTEGER,
    PROTOCOL_MAX,
    PROTOCOL_MIN,
)


def prereleases_allowed(entry: ConfigEntry | None = None) -> bool:
    """Follow the running PA release, with an explicit per-panel opt-in."""
    version = _version_key(INTEGRATION_VERSION)
    return version is not None and (
        not version[1]
        or (
            entry is not None
            and entry.options.get(CONF_PRERELEASE_PANEL_BUILDS) is True
        )
    )


def build_allowed(
    version_name: str,
    protocol_min: int | None,
    protocol_max: int | None,
    *,
    allow_prerelease: bool,
) -> bool:
    """Require a proven channel and native range before admitting an APK."""
    if (
        type(version_name) is not str
        or not is_valid_panel_version(version_name)
        or type(protocol_min) is not int
        or type(protocol_max) is not int
        or not 1 <= protocol_min <= protocol_max <= MAX_ANDROID_INTEGER
        or protocol_min > PROTOCOL_MAX
        or protocol_max < PROTOCOL_MIN
    ):
        return False
    version = _version_key(version_name)
    if "-" in version_name:
        prerelease = version_name.partition("-")[2]
        if any(
            not part or (part.isdigit() and len(part) > 1 and part.startswith("0"))
            for part in prerelease.split(".")
        ):
            return False
    return version is not None and (version[1] or allow_prerelease is True)


def version_allowed(
    version_name: str,
    version_code: int | None,
    installed_version: str,
    installed_code: int | None,
) -> bool:
    """Never older than the installed build once it runs 1.0 or later."""
    installed = _version_key(installed_version)
    if installed is None or _version_key(version_name) is None:
        return False
    return installed[0] < (1, 0, 0) or version_not_older(
        version_name, version_code, installed_version, installed_code
    )


def version_not_older(
    version_name: str,
    version_code: int | None,
    installed_version: str,
    installed_code: int | None,
) -> bool:
    """The build is the installed release or a later one, by name and code."""
    installed = _version_key(installed_version)
    candidate = _version_key(version_name)
    return (
        installed is not None
        and candidate is not None
        and candidate >= installed
        and (
            version_code is None
            or installed_code is None
            or version_code >= installed_code
        )
    )


def policy_for(entry: ConfigEntry | None = None) -> dict[str, int | bool]:
    """Advertise this entry's current policy in its authenticated hello grant."""
    return {
        "protocolMin": PROTOCOL_MIN,
        "protocolMax": PROTOCOL_MAX,
        "prerelease": prereleases_allowed(entry),
    }

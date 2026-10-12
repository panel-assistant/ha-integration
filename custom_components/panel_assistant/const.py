"""Constants for the ha-paneld integration."""

import json
from datetime import timedelta
from pathlib import Path

from yarl import URL

DOMAIN = "panel_assistant"
# One definition of this integration's public version: its own manifest.
INTEGRATION_VERSION: str = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)["version"]
# The public version (manifest.json) only changes when something ships. This
# build number tells builds apart in between: it counts the commits that have
# changed this integration.
INTEGRATION_BUILD = 549

# The native transport range this running integration can serve. Protocol 5 is
# the first without MQTT: an app that speaks only 4 or older is refused, told
# that it needs app 1.0.0, the first release speaking 5, and offered it.
PROTOCOL_MIN = 5
PROTOCOL_MAX = 5
REQUIRED_APP_VERSION = "1.0.0"

# Every outward link goes through the site's own redirect rather than a page
# path, so pages can move. The version and build travel with it, so a later
# site can route an older Panel Assistant somewhere that still makes sense.
_HELP_REDIRECT = "https://panel-assistant.io/go/"


def help_url(topic: str, **parameters: str) -> str:
    """Return the site redirect for one help topic, stamped with this version."""
    query = {"v": INTEGRATION_VERSION, "build": str(INTEGRATION_BUILD), **parameters}
    return str(URL(_HELP_REDIRECT + topic).with_query(query))


# One definition of where the panel app is published. The repository moved to
# the organisation in 2026, and GitHub redirects the old path indefinitely, but
# nothing here relies on that redirect.
ANDROID_REPOSITORY = "panel-assistant/android"
ANDROID_RELEASES_API = f"https://api.github.com/repos/{ANDROID_REPOSITORY}/releases"
ANDROID_RELEASE_DOWNLOAD_ROOT = (
    f"https://github.com/{ANDROID_REPOSITORY}/releases/download"
)

DEFAULT_PORT = 8888
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_TIMEOUT_SECONDS = 5
MAX_HEALTH_RESPONSE_BYTES = 512
MAX_STATUS_RESPONSE_BYTES = 64 * 1024
MAX_INSTALL_RESPONSE_BYTES = 1024
MAX_STATUS_WARNINGS = 32
MAX_STATUS_CAPABILITIES = 32
MAX_STATUS_REASON_CODES = 32
MAX_STATUS_WARNING_LENGTH = 2048
MAX_STATUS_TOKEN_LENGTH = 128
MAX_STATUS_FAULT_DETAIL_LENGTH = 40
MAX_STATUS_INTEGER = 2**63 - 1
MIN_ANDROID_INTEGER = -(2**31)
MAX_ANDROID_INTEGER = 2**31 - 1
MAX_ZIGBEE_CPU_PERCENT = 1000

HEALTH_PATH = "/api/v1/health"
STATUS_PATH = "/api/v1/status"
INSTALL_COMPONENT_PATH = "/api/v1/install/component"
INSTALL_STATUS_PATH = "/api/v1/install/status"
APK_STAGE_PATH = "/api/v1/install/apk"
APK_COMMIT_PATH = "/api/v1/install/apk/commit"
APK_DISCARD_PATH = "/api/v1/install/apk/discard"
BACKUP_PATH = "/api/v1/backup"
RESTORE_PATH = "/api/v1/restore"
DIAG_PATH = "/api/v1/diag"
SETUP_PATH = "/api/v1/setup"
DISPLAY_PATH = "/api/v1/display"
# The panel's own settings endpoint. This integration writes exactly one thing to
# it: the Home Assistant address a newly installed or adopted panel should use,
# for the panel to verify from its own network before accepting.
CONFIG_PATH = "/api/v1/config"

# The Home Assistant user a panel's native transport session is bound to. Only an
# administrator's Repairs confirmation records it; the entry's identity does not
# change.
CONF_TRANSPORT_USER_ID = "transport_user_id"
# The entry option that chose who owned a panel's entities while MQTT could.
# Nothing reads it; the entry migration to minor version 2 drops it.
CONF_AUTHORITY = "authority"
# A stable integration may admit prerelease panel builds for this panel only.
CONF_PRERELEASE_PANEL_BUILDS = "prerelease_panel_builds"
# The entry data key holding the record Panel Assistant 0.9.0 and earlier wrote
# when they moved a panel's entities off MQTT: which entities moved, and whether
# the move completed. Only read now.
CONF_CUTOVER = "cutover"
# The entry data key holding the channels the entry's panel has described in a
# hello, with the identity it described them under (see ``transport.py``).
CONF_SUPPORTED_CHANNELS = "supported_channels"


def update_unique_id(entry_id: str) -> str:
    """Return the registry unique ID of an entry's ha-paneld update entity."""
    return f"{entry_id}_update"


#: The config flow source the browser USB installer's handover starts, adding
#: the panel it installed.
SOURCE_USB_INSTALL = "usb_install"

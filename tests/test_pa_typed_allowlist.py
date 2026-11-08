"""The helper's typed-command allowlist matches Panel Assistant's own definitions.

The helper keeps one C copy of these tables (``helper/src/pa_allowlist.h``).
Both repositories compare their copy by value with this shared fixture, so a
change on either side fails until the other side follows.
"""

import json
import os
import shlex
from pathlib import Path
from typing import Any

from custom_components.panel_assistant import (
    adb_protocol,
    app_identity,
    display_provisioning,
    home_chooser,
    install_adb,
    vendor_provisioning,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pa-typed-allowlist.json"


def _by_launch(table: Any) -> dict[str, Any]:
    return {
        launch: list(value) if isinstance(value, tuple) else value
        for launch, value in table.items()
    }


def live_allowlist() -> dict[str, Any]:
    """Build the fixture's layout from the integration's live constants."""
    launches = list(app_identity.LAUNCH_COMPONENTS[app_identity.PACKAGE_ID])
    splash = shlex.split(vendor_provisioning.VENDOR_HOME_COMMAND)[-1]
    return {
        "launch_components": launches,
        "accessibility_components": {
            "current": _by_launch(app_identity.ACCESSIBILITY_COMPONENTS),
            "equivalent": _by_launch(app_identity.EQUIVALENT_ACCESSIBILITY_COMPONENTS),
            "obsolete": _by_launch(app_identity.OBSOLETE_ACCESSIBILITY_COMPONENTS),
        },
        "app_ops": adb_protocol._GRANTED_APP_OPS.split(),
        "runtime_grants": [
            {"feature": feature, "permission": f"android.permission.{permission}"}
            for feature, permission in adb_protocol.RUNTIME_GRANTS
        ],
        "notifications": {
            "permission": adb_protocol._NOTIFICATIONS_PERMISSION,
            "min_sdk": install_adb._NOTIFICATIONS_RUNTIME_SDK,
        },
        "secure_settings_permission": adb_protocol._SECURE_SETTINGS_PERMISSION,
        "home_components": {
            "current": _by_launch(app_identity.HOME_COMPONENTS),
            "equivalent": _by_launch(app_identity.EQUIVALENT_HOME_COMPONENTS),
            "labels": list(home_chooser.APP_LABELS[app_identity.PACKAGE_ID]),
        },
        "device_admin": {
            "receiver": app_identity.DEVICE_ADMIN_RECEIVER,
            "release_receiver": app_identity.RELEASE_DEVICE_ADMIN_RECEIVER,
        },
        "package_ids": {
            "app": app_identity.PACKAGE_ID,
            "legacy": app_identity.LEGACY_PACKAGE_ID,
            "shelly": vendor_provisioning.SHELLY_LAUNCHER,
        },
        "shelly_splash": splash,
        "meta": {
            "devices": list(vendor_provisioning.META_PORTAL_DEVICES),
            "control_center": vendor_provisioning.META_CONTROL_CENTER,
        },
        "restore_settings_keys": {
            # The record lines each restore program reads back.
            "meta": [*vendor_provisioning._SETTINGS, "controlcenter_overlay"],
            "meta_verifier": [vendor_provisioning._VERIFIER],
            "shelly": ["stargate_overlay"],
            "display": ["density", "font_scale"],
        },
        "record_paths": {
            "meta": vendor_provisioning.META_RESTORE_RECORD,
            "meta_verifier": vendor_provisioning.META_VERIFIER_RECORD,
            "shelly": vendor_provisioning.SHELLY_RESTORE_RECORD,
            "display": display_provisioning.DISPLAY_RESTORE_RECORD,
        },
    }


def test_allowlist_fixture_equals_the_live_constants() -> None:
    live = live_allowlist()
    if os.environ.get("PA_WRITE_TYPED_ALLOWLIST") == "1":
        FIXTURE.write_text(
            json.dumps(live, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == live

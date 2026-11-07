"""Owner-reviewed hardware report drafts from non-identifying cached facts."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from .build import INTEGRATION_BUILD
from .const import INTEGRATION_VERSION
from .coordinator import PanelSnapshot

_BEHAVIORS = (
    "dashboard",
    "brightness",
    "sleep-wake",
    "light",
    "proximity",
    "buttons",
    "leds",
    "relays",
    "speaker",
    "microphone",
    "restart",
)


def owner_report_issue_url(snapshot: PanelSnapshot | None) -> str:
    """Prepare a GitHub draft without reading the panel or sending the report.

    Only facts describing a kind of device cross this boundary. Never copy a
    status object, diagnostics, entry data, or owner-assigned names into a report.
    Runtime availability and advertised capabilities cannot prove physical tests.
    """
    status = snapshot.status if snapshot is not None else None
    device = status.panel_assistant_device if status is not None else None
    hardware = status.panel_assistant_hardware if status is not None else None
    health = snapshot.health if snapshot is not None else None
    report = {
        "schemaVersion": 1,
        "model": {
            "manufacturer": device.manufacturer or "unknown" if device else "unknown",
            "name": device.model or "unknown" if device else "unknown",
        },
        "firmware": {
            "build": hardware.get("firmware", "unknown") if hardware else "unknown",
            "android": (
                hardware.get("android_release", "unknown") if hardware else "unknown"
            ),
        },
        "software": {
            "appVersion": health.version if health is not None else "unknown",
            "appBuild": (
                str(health.version_code)
                if health is not None and health.version_code is not None
                else "unknown"
            ),
            "integrationVersion": INTEGRATION_VERSION,
            "integrationBuild": str(INTEGRATION_BUILD),
        },
        # Neither the active profile nor physical test results are in this cache.
        "profile": {"level": "unknown", "id": "unknown", "revision": "unknown"},
        "results": [
            {"behavior": behavior, "outcome": "untested", "notes": ""}
            for behavior in _BEHAVIORS
        ],
        "limitations": [],
    }
    body = (
        "Review this owner hardware report before submitting. Correct any device "
        "facts and fill in the profile you actually used; leave unknown facts as "
        '"unknown". Profile levels are unknown, generic, community or built-in.\n\n'
        "Edit each tested result to works, fails or not-fitted and describe what "
        "you tested in its notes. Leave everything you did not test as untested. "
        "A connected panel does not prove that its hardware controls work. "
        "Include when you tested, required permissions and remaining problems. "
        "Add limitations to the list.\n\n"
        "Check for private information before submitting. This draft contains only "
        "device-type and software facts; do not add addresses, serials, room names "
        "or credentials. Submission does not certify support or automatically "
        "publish evidence on the website.\n\n"
        f"```json\n{json.dumps(report, ensure_ascii=False, indent=2)}\n```\n"
    )
    return "https://github.com/panel-assistant/ha-integration/issues/new?" + urlencode(
        {
            "template": "owner_hardware_report.md",
            "title": "Owner hardware report",
            "body": body,
        }
    )

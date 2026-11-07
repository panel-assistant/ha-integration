"""An owner opens and reviews an allowlisted hardware report through panel setup."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.build import INTEGRATION_BUILD
from custom_components.panel_assistant.client import PanelHealth
from custom_components.panel_assistant.const import DOMAIN, INTEGRATION_VERSION
from custom_components.panel_assistant.coordinator import PanelSnapshot
from custom_components.panel_assistant.status import PanelDevice, PanelStatus


@pytest.mark.parametrize(
    "snapshot_kind", ["complete", "no_hardware", "no_status", "unloaded"]
)
async def test_owner_opens_reviewable_hardware_report_without_panel_operations(
    hass: HomeAssistant, snapshot_kind: str
) -> None:
    """The actual options caller exposes a private-by-construction issue draft."""
    private = "private-owner-room-192.0.2.19"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=private,
        unique_id=private,
        data={"address": private, "transport_user_id": private},
        options={"prerelease_panel_builds": True},
    )
    entry.add_to_hass(hass)
    if snapshot_kind != "unloaded":
        entry.runtime_data = SimpleNamespace(
            coordinator=SimpleNamespace(
                data=PanelSnapshot(
                    health=PanelHealth(
                        version="1.0.0-rc1",
                        version_code=2400,
                        panel_id=private,
                        build=private,
                        config_hash=private,
                        discovery_id=private,
                    ),
                    status=(
                        PanelStatus(
                            warning_count=0,
                            capability_count=99,
                            panel_assistant_device=PanelDevice(
                                manufacturer="Example & Co",
                                model="Panel + / XL",
                                name=private,
                                area=private,
                            ),
                            panel_assistant_hardware={
                                "firmware": "vendor 2.0+release/5",
                                "android_release": "11",
                                "serial_number": private,
                                "network_macs": [private],
                            }
                            if snapshot_kind == "complete"
                            else {},
                            renderer={"state": "ready"},
                        )
                        if snapshot_kind in {"complete", "no_hardware"}
                        else None
                    ),
                    status_error=None,
                )
            )
        )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            new_callable=AsyncMock,
        ) as health_read,
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            new_callable=AsyncMock,
        ) as status_read,
        patch(
            "custom_components.panel_assistant.config_flow.async_read_firmware",
            new_callable=AsyncMock,
        ) as firmware_read,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            new_callable=AsyncMock,
        ) as adb_probe,
        patch(
            "custom_components.panel_assistant.transport_sessions.async_manage",
            new_callable=AsyncMock,
        ) as manage,
    ):
        menu = await hass.config_entries.options.async_init(entry.entry_id)
        assert "owner_report" in menu["menu_options"]
        form = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "owner_report"}
        )
        assert form["type"] == "form"
        assert form["step_id"] == "owner_report"
        url = form["description_placeholders"]["issue_url"]
        parsed = urlsplit(url)
        assert (parsed.scheme, parsed.netloc, parsed.path) == (
            "https",
            "github.com",
            "/panel-assistant/ha-integration/issues/new",
        )
        query = parse_qs(parsed.query)
        assert set(query) == {"template", "title", "body"}
        assert query["template"] == ["owner_hardware_report.md"]
        # Source-text reason: GitHub consumes this published Markdown template.
        template = (
            Path(__file__).parents[1]
            / ".github"
            / "ISSUE_TEMPLATE"
            / query["template"][0]
        )
        assert template.is_file()
        assert template.read_text().startswith("---\nname: Owner hardware report\n")
        assert query["title"] == ["Owner hardware report"]
        body = query["body"][0]
        assert "Review" in body
        assert "untested" in body
        report = json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])
        assert set(report) == {
            "schemaVersion",
            "model",
            "firmware",
            "software",
            "profile",
            "results",
            "limitations",
        }
        assert report["schemaVersion"] == 1
        assert report["profile"] == {
            "level": "unknown",
            "id": "unknown",
            "revision": "unknown",
        }
        assert [row["behavior"] for row in report["results"]] == [
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
        ]
        assert all(
            row["outcome"] == "untested" and row["notes"] == ""
            for row in report["results"]
        )
        assert report["limitations"] == []
        assert report["software"]["integrationVersion"] == INTEGRATION_VERSION
        assert report["software"]["integrationBuild"] == str(INTEGRATION_BUILD)
        if snapshot_kind in {"complete", "no_hardware"}:
            assert report["model"] == {
                "manufacturer": "Example & Co",
                "name": "Panel + / XL",
            }
        if snapshot_kind == "complete":
            assert report["firmware"] == {
                "build": "vendor 2.0+release/5",
                "android": "11",
            }
        elif snapshot_kind != "no_hardware":
            assert report["model"] == {"manufacturer": "unknown", "name": "unknown"}
        if snapshot_kind != "complete":
            assert report["firmware"] == {"build": "unknown", "android": "unknown"}
        assert report["software"]["appVersion"] == (
            "unknown" if snapshot_kind == "unloaded" else "1.0.0-rc1"
        )
        assert report["software"]["appBuild"] == (
            "unknown" if snapshot_kind == "unloaded" else "2400"
        )
        assert private not in str(form)
        assert private not in json.dumps(report)
        finished = await hass.config_entries.options.async_configure(
            form["flow_id"], {}
        )
        assert finished["type"] == "create_entry"
        assert finished["data"] == {"prerelease_panel_builds": True}
        assert dict(entry.options) == {"prerelease_panel_builds": True}
    for operation in (health_read, status_read, firmware_read, adb_probe, manage):
        operation.assert_not_awaited()

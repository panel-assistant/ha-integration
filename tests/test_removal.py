"""Removing the panel app from Panel Assistant's options.

The panel is a fake in two halves that production code reaches through its own
seams: the app's HTTP API answers on a real local server, and every ADB
command Panel Assistant sends runs in a real shell whose ``cmd``, ``pm``,
``am`` and ``getprop`` read and change one package-manager state. So the guard
that keeps HOME on another launcher is the shell text production sends, not a
stand-in for it.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import install_adb, panel_move
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.client import PanelAddress
from custom_components.panel_assistant.const import DOMAIN, help_url
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelDevice, PanelStatus

VENDOR_HOME = "com.eWeLinkControlPanel/.HomeActivity"
FALLBACK_HOME = "com.android.settings/.FallbackHome"
CHOOSER = "android/com.android.internal.app.ResolverActivity"
APP_HOME = f"{SUCCESSOR_PACKAGE_ID}/.DashboardActivity"

# One program stands in for each panel command; it reads and writes the
# package-manager state in $PANEL_STATE and logs every change it makes.
_FAKE_PANEL = r"""
import json, os, sys
path = os.environ["PANEL_STATE"]
state = json.load(open(path))
name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
def save():
    json.dump(state, open(path, "w"))
if name == "cmd" and args[:2] == ["package", "resolve-activity"]:
    print("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true")
    print(state["home"])
elif name == "cmd":
    sys.exit(1)
elif name == "pm" and args[0] == "path":
    if args[1] in state["installed"]:
        print(f"package:/data/app/{args[1]}/base.apk")
    else:
        sys.exit(1)
elif name == "pm" and args[0] == "list":
    if args[-1] in state["installed"] + state["aside"]:
        print(f"package:{args[-1]}")
elif name == "pm" and args[0] == "uninstall":
    package = args[-1]
    if package in state["fail_uninstall"]:
        state["fail_uninstall"].remove(package)
        save()
        print("Failure [DELETE_FAILED_INTERNAL_ERROR]")
        sys.exit(1)
    if package in state["installed"] or package in state["aside"]:
        state["installed"] = [p for p in state["installed"] if p != package]
        state["aside"] = [p for p in state["aside"] if p != package]
        state["events"].append(f"uninstall {package}")
        if state["home"].startswith(package + "/"):
            state["home"] = "com.android.settings/.FallbackHome"
        save()
        print("Success")
    else:
        print("Failure [DELETE_FAILED_INTERNAL_ERROR]")
        sys.exit(1)
elif name == "getprop":
    print(state["firmware"])
elif name in ("am", "dumpsys"):
    pass
else:
    sys.exit(1)
"""


class FakePanel:
    """One panel: its package manager over ADB and its app over HTTP."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.bin = directory / "bin"
        self.bin.mkdir()
        program = directory / "fake_panel.py"
        program.write_text(f"#!{sys.executable}\n" + _FAKE_PANEL)
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        # The program reads the name it was started under to know its command.
        for command in ("cmd", "pm", "am", "getprop", "dumpsys", "timeout"):
            (self.bin / command).symlink_to(program)
        self.state_path = directory / "state.json"
        self.write(
            {
                "installed": [SUCCESSOR_PACKAGE_ID],
                "aside": [],
                "home": APP_HOME,
                "firmware": "1.11.0",
                "fail_uninstall": [],
                "events": [],
            }
        )
        #: How the app answers hand-back-home: "hand", "approval", a 409
        #: code, "unconfirmed" (200 without a launcher) or an HTTP status.
        self.hand_back: str | int = "hand"
        self.hand_back_requests = 0
        #: Raise after the shell ran, as a dropped connection would.
        self.drop_after: str | None = None
        self.adb_authorized = True

    def read(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def write(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state))

    def set(self, **changes: Any) -> None:
        self.write({**self.read(), **changes})

    @property
    def installed(self) -> list[str]:
        state = self.read()
        return sorted(state["installed"] + state["aside"])

    async def shell(self, _device: Any, command: str, **_kwargs: Any) -> bytes:
        result = await asyncio.to_thread(
            subprocess.run,
            ["sh", "-c", command],
            capture_output=True,
            env={
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "PANEL_STATE": str(self.state_path),
            },
            check=False,
        )
        if self.drop_after is not None and self.drop_after in command:
            self.drop_after = None
            raise ConnectionResetError
        return result.stdout

    async def probe(self, *_args: Any, **_kwargs: Any) -> InstallTargetProbe:
        if not self.adb_authorized:
            return InstallTargetProbe(state=InstallTargetState.ADB_UNAUTHORIZED)
        installed = self.read()["installed"]
        if SUCCESSOR_PACKAGE_ID in installed:
            state = InstallTargetState.INSTALLED
        elif LEGACY_PACKAGE_ID in installed:
            state = InstallTargetState.MIGRATION_CANDIDATE
        elif self.read()["aside"]:
            state = InstallTargetState.RETAINED_OR_AMBIGUOUS
        else:
            return InstallTargetProbe(state=InstallTargetState.INSTALL_CANDIDATE)
        return InstallTargetProbe(
            state=state,
            model="px30_evb",
            serial="PANEL1",
            primary_abi="arm64-v8a",
            android_sdk=27,
        )

    async def hand_back_home(self, _request: web.Request) -> web.Response:
        self.hand_back_requests += 1
        if SUCCESSOR_PACKAGE_ID not in self.read()["installed"]:
            # Nothing serves the app's port once it is uninstalled.
            raise ConnectionResetError
        mode = self.hand_back
        if mode == "hand":
            self.set(home=VENDOR_HOME)
            return web.json_response(
                {
                    "ok": True,
                    "restored": ["com.eWeLinkControlPanel"],
                    "adopted": [],
                    "outstanding": [],
                    "home_handed_to": "com.eWeLinkControlPanel",
                }
            )
        if mode == "lie":
            # Claims a launcher took HOME, which the panel's own HOME denies.
            return web.json_response(
                {"ok": True, "outstanding": [], "home_handed_to": "com.example.home"}
            )
        if mode == "unconfirmed":
            return web.json_response(
                {"ok": False, "restored": [], "adopted": [], "outstanding": []}
            )
        if mode == "approval":
            return web.json_response(
                {
                    "ok": False,
                    "error": "approval-required",
                    "approval_id": "a1",
                    "message": "Approve this request physically on the panel.",
                },
                status=202,
            )
        if isinstance(mode, int):
            return web.Response(status=mode)
        return web.json_response({"ok": False, "error": mode}, status=409)


@pytest.fixture
async def panel(
    tmp_path: Path, socket_enabled: None
) -> AsyncIterator[tuple[FakePanel, TestServer]]:
    fake = FakePanel(tmp_path)
    app = web.Application()
    app.router.add_post("/api/v1/hand-back-home", fake.hand_back_home)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    pin = PinnedPanelTarget(
        original=PanelAddress(host="127.0.0.1", port=server.port),
        # ADB refuses anything but a private LAN address; the shell is local.
        pinned=PanelAddress(host="192.168.1.30", port=8888),
    )
    with (
        patch.object(panel_move, "async_get_adb_credential", AsyncMock()),
        patch.object(
            panel_move,
            "async_get_durable_adb_credential",
            AsyncMock(return_value=SimpleNamespace(signer=object())),
        ),
        patch.object(
            panel_move, "async_pin_install_target", AsyncMock(return_value=pin)
        ),
        patch.object(panel_move, "async_probe_install_target", fake.probe),
        patch.object(install_adb, "_async_connect", AsyncMock(return_value=object())),
        patch.object(install_adb, "_async_close", AsyncMock()),
        patch.object(install_adb, "_parse_identity_root", lambda *_args: None),
        patch.object(install_adb, "_async_shell", fake.shell),
    ):
        yield fake, server
    await server.close()


def _entry(hass: HomeAssistant, server: TestServer, status: Any = None) -> Any:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test display",
        data={CONF_ADDRESS: f"127.0.0.1:{server.port}"},
        options={"authority": "native"},
    )
    entry.add_to_hass(hass)
    if status is not None:
        entry.runtime_data = SimpleNamespace(
            coordinator=SimpleNamespace(
                data=SimpleNamespace(health=None, status=status)
            )
        )
    return entry


async def _open_form(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "remove_app" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_app"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "remove_app"
    return result


async def _remove(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    form = await _open_form(hass, entry)
    return await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )


async def test_removal_hands_home_back_then_removes_both_ids(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.set(installed=[LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "remove_app_done"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME
    assert fake.hand_back_requests == 1
    assert sorted(fake.read()["events"]) == sorted(
        [f"uninstall {SUCCESSOR_PACKAGE_ID}", f"uninstall {LEGACY_PACKAGE_ID}"]
    )


async def test_the_owner_can_forget_the_panel_once_its_app_is_gone(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    _fake, server = panel
    entry = _entry(hass, server)
    done = await _remove(hass, entry)

    result = await hass.config_entries.options.async_configure(
        done["flow_id"], {"next_step_id": "remove_app_delete_entry"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "removed_entry_deleted"
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_keeping_the_entry_leaves_it_in_place(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    _fake, server = panel
    entry = _entry(hass, server)
    done = await _remove(hass, entry)

    result = await hass.config_entries.options.async_configure(
        done["flow_id"], {"next_step_id": "remove_app_keep_entry"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "removed_entry_kept"
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_nothing_is_removed_until_the_owner_confirms(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": False}
    )

    assert result["errors"] == {"base": "removal_unconfirmed"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0


async def test_the_risks_link_names_the_panel_make_model_and_firmware(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    status = PanelStatus(
        warning_count=0,
        capability_count=0,
        panel_assistant_device=PanelDevice(
            name="Test display",
            manufacturer="Sonoff",
            model="NSPanel Pro",
            area="Lounge",
        ),
    )
    entry = _entry(hass, server, status)

    form = await _open_form(hass, entry)

    link = URL(form["description_placeholders"]["risks_url"])
    assert str(link).startswith(help_url("removal-risks").split("?")[0])
    assert link.query["make"] == "Sonoff"
    assert link.query["model"] == "NSPanel Pro"
    assert link.query["fw"] == "1.11.0"
    # Nothing that names this panel travels to the public site.
    assert "Lounge" not in str(link)
    assert "Test" not in str(link)
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]


@pytest.mark.parametrize(
    "firmware", ["", "x" * 200, "1.0\u0007"], ids=["empty", "too-long", "control"]
)
async def test_the_risks_link_reads_without_a_firmware_it_cannot_match(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], firmware: str
) -> None:
    fake, server = panel
    fake.set(firmware=firmware)
    entry = _entry(hass, server)

    form = await _open_form(hass, entry)

    link = URL(form["description_placeholders"]["risks_url"])
    assert link.path == "/go/removal-risks"
    assert "fw" not in link.query
    assert "make" not in link.query


async def test_the_risks_link_survives_a_panel_adb_cannot_reach(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.adb_authorized = False
    entry = _entry(hass, server)

    form = await _open_form(hass, entry)

    assert URL(form["description_placeholders"]["risks_url"]).path == (
        "/go/removal-risks"
    )


async def test_hardened_approval_stops_with_nothing_removed_and_a_retry_finishes(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.hand_back = "approval"
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )

    assert result["errors"] == {"base": "removal_approval_required"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.read()["home"] == APP_HOME

    fake.hand_back = "hand"  # approved on the panel; the same request again
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert result["step_id"] == "remove_app_done"
    assert fake.installed == []


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ("no-replacement-home", "removal_no_replacement_home"),
        ("ownership-unreadable", "removal_ownership_unreadable"),
        ("package-state-unknown", "removal_package_state_unknown"),
        ("unconfirmed", "removal_home_not_handed"),
        (404, "removal_hand_back_unsupported"),
    ],
)
async def test_a_refused_hand_back_removes_nothing(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    answer: str | int,
    error: str,
) -> None:
    fake, server = panel
    fake.hand_back = answer
    # HOME already on another launcher, so only the app's own answer stops
    # the removal: the vendor apps it switched off may still be off.
    fake.set(home=VENDOR_HOME)
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["errors"] == {"base": error}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.read()["events"] == []


@pytest.mark.parametrize("home", [APP_HOME, FALLBACK_HOME, CHOOSER])
async def test_the_uninstall_waits_for_another_launcher_on_the_panel_itself(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], home: str
) -> None:
    """A hand-back the panel's HOME does not bear out removes nothing."""
    fake, server = panel
    fake.hand_back = "lie"
    fake.set(home=home)
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["errors"] == {"base": "removal_home_not_handed"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]


async def test_a_run_cut_short_after_one_uninstall_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.set(
        installed=[LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID],
        fail_uninstall=[LEGACY_PACKAGE_ID],
    )
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )

    assert first["errors"] == {"base": "removal_remove_failed"}
    assert fake.installed == [LEGACY_PACKAGE_ID]
    assert fake.read()["home"] == VENDOR_HOME

    # The app that answered HTTP is gone; the retry goes on over ADB alone.
    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME


async def test_a_dropped_connection_during_the_uninstall_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.drop_after = "pm uninstall"
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert first["errors"] == {"base": "removal_remove_failed"}

    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []
    assert fake.hand_back_requests == 1


async def test_a_silent_app_that_still_holds_home_is_not_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.hand_back = 503
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["errors"] == {"base": "removal_app_unreachable"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]


async def test_a_panel_busy_with_an_update_is_left_alone(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    assert panel_move.claim_panel_operation(hass, entry.entry_id)

    result = await _remove(hass, entry)

    assert result["errors"] == {"base": "removal_busy"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0


async def test_an_unauthorized_panel_is_sent_to_authorize_adb_first(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.adb_authorized = False
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["errors"] == {"base": "removal_adb_authorization"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0

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
import os
import stat
import subprocess
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

from custom_components.panel_assistant import (
    display_provisioning,
    install_adb,
    panel_move,
    vendor_provisioning,
)
from custom_components.panel_assistant.adb_protocol import (
    InstallAdbError,
    InstallAdbErrorCode,
)
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

# One shell program stands in for each panel command. Package-manager state is
# a directory: one empty file per package under installed/, aside/,
# fail_uninstall/, admin/ (an active device admin), no_release/ (a build that
# cannot give its admin up) and stopped/ (force-stopped, so only a broadcast
# that includes stopped packages reaches it), and one file each for HOME,
# firmware and the change log.
_FAKE_PANEL = r"""#!/bin/sh
s=$PANEL_STATE
release=io.panelassistant.android.platform.ReleaseDeviceAdminReceiver
case "${0##*/} $1 $2" in
"cmd package resolve-activity")
    echo "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true"
    cat "$s/home" ;;
"cmd package set-home-activity")
    [ -e "$s/installed/${3%%/*}" ] || exit 1
    echo "$3" > "$s/home"; echo "set home $3" >> "$s/events" ;;
"pm path "*)
    [ -e "$s/installed/$2" ] || exit 1
    echo "package:/data/app/$2/base.apk" ;;
"pm list "*)
    for p; do :; done
    if [ -e "$s/installed/$p" ] || [ -e "$s/aside/$p" ]; then echo "package:$p"; fi ;;
"pm uninstall "*)
    for p; do :; done
    if [ -e "$s/admin/$p" ]; then
        echo "uninstall refused $p" >> "$s/events"
        echo "Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]"; exit 1
    fi
    if [ -e "$s/fail_uninstall/$p" ]; then
        rm "$s/fail_uninstall/$p"; echo "Failure [DELETE_FAILED_INTERNAL_ERROR]"; exit 1
    fi
    if [ -e "$s/installed/$p" ] || [ -e "$s/aside/$p" ]; then
        rm -f "$s/installed/$p" "$s/aside/$p"
        # -k keeps the app's data: set aside, not gone.
        [ "$2" != -k ] || touch "$s/aside/$p"
        echo "uninstall $p" >> "$s/events"
        case "$(cat "$s/home")" in
        "$p"/*) echo com.android.settings/.FallbackHome > "$s/home" ;;
        esac
        echo Success
    else
        echo "Failure [DELETE_FAILED_INTERNAL_ERROR]"; exit 1
    fi ;;
"getprop "*) cat "$s/firmware" ;;
"settings get "*)
    if [ -f "$s/setting.$3" ]; then cat "$s/setting.$3"; else echo null; fi ;;
"settings put "*)
    printf '%s\n' "$4" > "$s/setting.$3"; echo "settings put $3" >> "$s/events" ;;
"settings delete "*)
    rm -f "$s/setting.$3"; echo "settings delete $3" >> "$s/events" ;;
"appops get "*)
    if [ -f "$s/appop.$2.$3" ]; then echo "$3: $(cat "$s/appop.$2.$3")"
    else echo "$3: default"; fi ;;
"appops set "*)
    echo "$4" > "$s/appop.$2.$3"; echo "appops set $2 $4" >> "$s/events" ;;
"wm density"*)
    if [ -z "$2" ]; then
        echo "Physical density: 240"
        [ ! -f "$s/density" ] || echo "Override density: $(cat "$s/density")"
    elif [ "$2" = reset ]; then
        rm -f "$s/density"; echo "wm density reset" >> "$s/events"
    else
        echo "$2" > "$s/density"; echo "wm density $2" >> "$s/events"
    fi ;;
"am force-stop"*) touch "$s/stopped/$2" ;;
"am broadcast"*)
    stopped=
    while [ $# -gt 0 ]; do
        [ "$1" != -n ] || c=$2
        [ "$1" != --include-stopped-packages ] || stopped=1
        shift
    done
    p=${c%%/*}
    if [ -e "$s/stopped/$p" ] && [ -z "$stopped" ]; then
        :
    elif [ "${c#*/}" = "$release" ] && [ -e "$s/installed/$p" ] \
        && [ ! -e "$s/no_release/$p" ] && [ -e "$s/admin/$p" ]; then
        echo released > "$s/admin/$p"; echo "release admin $p" >> "$s/events"
    fi ;;
# Android lets go of a released admin only once it has told the app it is
# disabled, which takes time: here, until the shell next sleeps.
"sleep "*)
    for f in "$s"/admin/*; do [ ! -s "$f" ] || rm "$f"; done ;;
"am "*|"dumpsys "*) ;;
*) exit 1 ;;
esac
"""
_LISTS = ("installed", "aside", "fail_uninstall", "admin", "no_release", "stopped")


class FakePanel:
    """One panel: its package manager over ADB and its app over HTTP."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.bin = directory / "bin"
        self.bin.mkdir()
        program = directory / "fake_panel"
        program.write_text(_FAKE_PANEL)
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        # The program reads the name it was started under to know its command.
        for command in (
            "cmd",
            "pm",
            "am",
            "getprop",
            "dumpsys",
            "timeout",
            "settings",
            "appops",
            "wm",
            "sleep",
        ):
            (self.bin / command).symlink_to(program)
        self.state_path = directory / "state"
        for name in _LISTS:
            (self.state_path / name).mkdir(parents=True)
        (self.state_path / "events").touch()
        self.set(installed=[SUCCESSOR_PACKAGE_ID], home=APP_HOME, firmware="1.11.0")
        #: How the app answers hand-back-home: "hand", "approval", a 409
        #: code, "unconfirmed" (200 without a launcher) or an HTTP status.
        self.hand_back: str | int = "hand"
        self.hand_back_requests = 0
        #: Raise after the shell ran, as a dropped connection would.
        self.drop_after: str | None = None
        self.adb_authorized = True

    def read(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            name: sorted(path.name for path in (self.state_path / name).iterdir())
            for name in _LISTS
        }
        state["events"] = (self.state_path / "events").read_text().splitlines()
        for name in ("home", "firmware"):
            state[name] = (self.state_path / name).read_text().strip()
        return state

    def set(self, **changes: Any) -> None:
        for name, value in changes.items():
            if name in _LISTS:
                for path in (self.state_path / name).iterdir():
                    path.unlink()
                for package in value:
                    (self.state_path / name / package).touch()
            else:
                (self.state_path / name).write_text(f"{value}\n")

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


@pytest.mark.parametrize("holder", [SUCCESSOR_PACKAGE_ID, LEGACY_PACKAGE_ID])
async def test_an_app_holding_device_admin_gives_it_up_and_is_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], holder: str
) -> None:
    """Android refuses to uninstall an active device admin; the app is asked."""
    fake, server = panel
    fake.set(installed=[LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID], admin=[holder])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert fake.installed == []
    assert fake.read()["admin"] == []
    events = fake.read()["events"]
    assert events.count(f"release admin {holder}") == 1
    assert events.index(f"release admin {holder}") < events.index(f"uninstall {holder}")


async def test_an_app_that_cannot_give_up_device_admin_is_kept_and_reported(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """A build without the release stays installed after a bounded wait."""
    fake, server = panel
    fake.set(admin=[SUCCESSOR_PACKAGE_ID], no_release=[SUCCESSOR_PACKAGE_ID])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    # The first refusal and ten retries, then the observation reports it.
    assert (
        fake.read()["events"].count(f"uninstall refused {SUCCESSOR_PACKAGE_ID}") == 11
    )


@pytest.mark.parametrize(
    ("step", "holder"),
    [
        (install_adb.MoveStep.REMOVE_SUCCESSOR, SUCCESSOR_PACKAGE_ID),
        (install_adb.MoveStep.SET_ASIDE_LEGACY, LEGACY_PACKAGE_ID),
        (install_adb.MoveStep.RETIRE_LEGACY, LEGACY_PACKAGE_ID),
    ],
)
async def test_repair_and_purge_uninstalls_ask_the_app_to_give_up_device_admin(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    step: install_adb.MoveStep,
    holder: str,
) -> None:
    """Repair and the old-app purge uninstall through these steps."""
    fake, server = panel
    fake.set(
        installed=[LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID],
        admin=[holder],
        home=VENDOR_HOME,
    )
    found = await panel_move.async_app_target(hass, _entry(hass, server))
    assert found is not None

    await install_adb.async_move_step(*found, step)

    assert holder not in fake.read()["installed"]
    # Setting the old app aside keeps its data; the other steps remove it.
    assert (holder in fake.read()["aside"]) is (
        step is install_adb.MoveStep.SET_ASIDE_LEGACY
    )
    assert fake.read()["admin"] == []
    events = fake.read()["events"]
    assert events.count(f"release admin {holder}") == 1
    assert events.index(f"release admin {holder}") < events.index(f"uninstall {holder}")


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

    assert result.get("errors") == {"base": "removal_unconfirmed"}
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
    assert link.query.get("make") == "Sonoff"
    assert link.query.get("model") == "NSPanel Pro"
    assert link.query.get("fw") == "1.11.0"
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

    assert result.get("errors") == {"base": "removal_approval_required"}
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

    assert result.get("errors") == {"base": error}
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

    assert result.get("errors") == {"base": "removal_home_not_handed"}
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

    assert first.get("errors") == {"base": "removal_remove_failed"}
    # The new app stays while the old one is left, so the retry can reach it.
    assert fake.installed == [LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID]
    assert fake.read()["home"] == VENDOR_HOME

    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME


async def test_an_old_app_set_aside_mid_move_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """Only the old app's kept data is left beside the new app."""
    fake, server = panel
    fake.set(
        installed=[SUCCESSOR_PACKAGE_ID],
        aside=[LEGACY_PACKAGE_ID],
        fail_uninstall=[LEGACY_PACKAGE_ID],
    )
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert first.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == [LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID]

    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []


async def test_a_connection_lost_after_the_uninstall_still_ends_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """The panel is read again, so an uninstall that ran is not reported failed."""
    fake, server = panel
    fake.drop_after = "pm uninstall"
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["step_id"] == "remove_app_done"
    assert fake.installed == []


async def test_a_dropped_connection_during_the_uninstall_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """An uninstall that did not run before the drop is reported; a retry runs it."""
    fake, server = panel
    fake.drop_after = "pm uninstall"
    fake.set(fail_uninstall=[SUCCESSOR_PACKAGE_ID])
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert first.get("errors") == {"base": "removal_remove_failed"}

    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []
    # The app was still there, so the retry asks it to hand HOME back again.
    assert fake.hand_back_requests == 2


async def test_a_panel_with_neither_app_left_is_reported_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """An app already gone, by hand or by a run whose answer was lost, is done."""
    fake, server = panel
    fake.set(installed=[])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["step_id"] == "remove_app_done"
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []


async def test_a_silent_app_that_still_holds_home_is_not_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.hand_back = 503
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_app_unreachable"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]


async def test_a_panel_busy_with_an_update_is_left_alone(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    assert panel_move.claim_panel_operation(hass, entry.entry_id)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_busy"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0


async def test_an_unauthorized_panel_is_sent_to_authorize_adb_first(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.adb_authorized = False
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_adb_authorization"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0


@pytest.mark.parametrize(
    ("seam", "code", "error"),
    [
        (
            "_async_connect",
            InstallAdbErrorCode.AUTHORIZATION_REQUIRED,
            "removal_adb_authorization",
        ),
        (
            "_parse_identity_root",
            InstallAdbErrorCode.TARGET_CHANGED,
            "removal_panel_changed",
        ),
    ],
)
async def test_adb_lost_after_the_form_opened_is_reported_as_its_cause(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    seam: str,
    code: InstallAdbErrorCode,
    error: str,
) -> None:
    """Authorization revoked, or another panel at the address, mid-removal."""
    fake, server = panel
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    with patch.object(install_adb, seam, side_effect=InstallAdbError(code)):
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {"confirmed": True}
        )

    assert result.get("errors") == {"base": error}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.hand_back_requests == 0


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ("no-replacement-home", "removal_no_replacement_home"),
        ("ownership-unreadable", "removal_ownership_unreadable"),
        ("package-state-unknown", "removal_package_state_unknown"),
        (404, "removal_hand_back_unsupported"),
    ],
)
async def test_a_refusal_on_a_shelly_panel_is_not_overridden(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    answer: str | int,
    error: str,
) -> None:
    """Only an unconfirmed hand-back makes Stargate HOME over ADB."""
    fake, server = panel
    fake.hand_back = answer
    fake.set(installed=[SUCCESSOR_PACKAGE_ID, vendor_provisioning.SHELLY_LAUNCHER])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": error}
    assert SUCCESSOR_PACKAGE_ID in fake.installed
    assert fake.read()["home"] == APP_HOME
    assert fake.read()["events"] == []


@pytest.mark.parametrize("home", ["", CHOOSER])
async def test_a_silent_app_on_a_panel_without_home_is_not_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], home: str
) -> None:
    fake, server = panel
    fake.hand_back = 503
    fake.set(home=home)
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_app_unreachable"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.read()["events"] == []


async def test_an_old_app_left_without_the_new_app_is_reported_until_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.set(
        installed=[LEGACY_PACKAGE_ID],
        fail_uninstall=[LEGACY_PACKAGE_ID],
        home=VENDOR_HOME,
    )
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert first.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == [LEGACY_PACKAGE_ID]

    retry = await hass.config_entries.options.async_configure(
        form["flow_id"], {"confirmed": True}
    )
    assert retry["step_id"] == "remove_app_done"
    assert fake.installed == []


@pytest.mark.parametrize("command", ["appops set", "wm density"])
async def test_a_restore_that_fails_still_removes_the_app(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], command: str
) -> None:
    """The vendor setup and the display size are put back best effort."""
    fake, server = panel
    fake.drop_after = command
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert fake.drop_after is None  # the restore command ran and dropped
    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["step_id"] == "remove_app_done"
    assert fake.installed == []


async def test_removal_gives_a_portal_its_meta_values_back_before_the_uninstall(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What installation recorded is put back once HOME is Meta's again."""
    fake, server = panel
    control_center = vendor_provisioning.META_CONTROL_CENTER
    record = tmp_path / "meta-record"
    monkeypatch.setattr(vendor_provisioning, "META_RESTORE_RECORD", str(record))
    record.write_text(
        "screensaver_components=meta/.Dream\n"
        "screensaver_default_component=null\n"
        "controlcenter_overlay=allow\n"
    )
    verifier = tmp_path / "verifier-record"
    monkeypatch.setattr(vendor_provisioning, "META_VERIFIER_RECORD", str(verifier))
    verifier.write_text("package_verifier_enable=1\n")
    (fake.state_path / "setting.package_verifier_enable").write_text("0\n")
    fake.set(installed=[SUCCESSOR_PACKAGE_ID, control_center])
    for name in ("screensaver_components", "screensaver_default_component"):
        (fake.state_path / f"setting.{name}").write_text("\n")
    (fake.state_path / f"appop.{control_center}.SYSTEM_ALERT_WINDOW").write_text(
        "deny\n"
    )
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["step_id"] == "remove_app_done"
    assert fake.installed == [control_center]
    events = fake.read()["events"]
    assert events == [
        "settings put screensaver_components",
        "settings delete screensaver_default_component",
        f"appops set {control_center} allow",
        "settings put package_verifier_enable",
        f"uninstall {SUCCESSOR_PACKAGE_ID}",
    ]
    assert (fake.state_path / "setting.screensaver_components").read_text() == (
        "meta/.Dream\n"
    )
    assert not record.exists()
    assert (fake.state_path / "setting.package_verifier_enable").read_text() == "1\n"
    assert not verifier.exists()


async def test_removal_gives_shelly_home_its_overlay_and_the_panel_its_size_back(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app that cannot hand HOME to Stargate leaves it to Panel Assistant.

    As on a rootless Wall Display: the app answers without a launcher, so
    Panel Assistant makes Stargate HOME over ADB, then puts both setup records
    back before the uninstall.
    """
    fake, server = panel
    fake.hand_back = "unconfirmed"
    launcher = vendor_provisioning.SHELLY_LAUNCHER
    shelly = tmp_path / "shelly-record"
    display = tmp_path / "display-record"
    monkeypatch.setattr(vendor_provisioning, "SHELLY_RESTORE_RECORD", str(shelly))
    monkeypatch.setattr(display_provisioning, "DISPLAY_RESTORE_RECORD", str(display))
    shelly.write_text("stargate_overlay=allow\n")
    display.write_text("density=none\nfont_scale=null\n")
    fake.set(installed=[SUCCESSOR_PACKAGE_ID, launcher])
    (fake.state_path / f"appop.{launcher}.SYSTEM_ALERT_WINDOW").write_text("deny\n")
    (fake.state_path / "density").write_text("216\n")
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["step_id"] == "remove_app_done"
    assert fake.installed == [launcher]
    assert fake.read()["home"] == f"{launcher}/.activities.SplashActivity"
    assert fake.read()["events"] == [
        f"set home {launcher}/.activities.SplashActivity",
        f"appops set {launcher} allow",
        "wm density reset",
        "settings delete font_scale",
        f"uninstall {SUCCESSOR_PACKAGE_ID}",
    ]
    assert not (fake.state_path / "density").exists()
    assert not shelly.exists()
    assert not display.exists()


async def test_an_unhanded_home_without_shelly_changes_nothing(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only Shelly's launcher is made HOME over ADB; elsewhere the app keeps it.

    A vendor record waits for the app to go: Meta's dreams stay blank while
    the app is still HOME.
    """
    fake, server = panel
    fake.hand_back = "unconfirmed"
    record = tmp_path / "meta-record"
    monkeypatch.setattr(vendor_provisioning, "META_RESTORE_RECORD", str(record))
    record.write_text(
        "screensaver_components=meta/.Dream\n"
        "screensaver_default_component=null\n"
        "controlcenter_overlay=allow\n"
    )
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_home_not_handed"}
    assert fake.installed == [SUCCESSOR_PACKAGE_ID]
    assert fake.read()["home"] == APP_HOME
    assert fake.read()["events"] == []
    assert record.exists()

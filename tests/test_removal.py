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
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import (
    display_provisioning,
    install_adb,
    removal,
    vendor_provisioning,
)
from custom_components.panel_assistant.adb_protocol import (
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    PACKAGE_ID,
)
from custom_components.panel_assistant.client import (
    HaPaneldClient,
    PanelAddress,
)
from custom_components.panel_assistant.const import DOMAIN, help_url
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.panel_operations import claim_panel_operation
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelDevice, PanelStatus

VENDOR_HOME = "com.eWeLinkControlPanel/.HomeActivity"
FALLBACK_HOME = "com.android.settings/.FallbackHome"
CHOOSER = "android/com.android.internal.app.ResolverActivity"
APP_HOME = f"{PACKAGE_ID}/.DashboardActivity"

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
        cat "$s/release_sleeps" 2>/dev/null > "$s/admin/$p" || echo 1 > "$s/admin/$p"
        echo "release admin $p" >> "$s/events"
    fi ;;
# Android lets go of a released admin only once it has told the app it is
# disabled, which takes time: here, after release_sleeps shell sleeps (one).
"sleep "*)
    for f in "$s"/admin/*; do
        [ -s "$f" ] || continue
        n=$(($(cat "$f") - 1))
        if [ "$n" -le 0 ]; then rm "$f"; else echo "$n" > "$f"; fi
    done ;;
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
        self.set(installed=[PACKAGE_ID], home=APP_HOME, firmware="1.11.0")
        #: How the app answers hand-back-home: "hand", "approval", a 409
        #: code, "unconfirmed" (200 without a launcher) or an HTTP status.
        self.hand_back: str | int = "hand"
        self.hand_back_requests = 0
        #: Raise after the shell ran, as a dropped connection would.
        self.drop_after: str | None = None
        self.adb_authorized = True
        self.did = "a" * 64
        self.serial = "PANEL1"
        self.health_hook: Any = None
        self.health_status = 200
        self.health_package = PACKAGE_ID

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
        if PACKAGE_ID in installed:
            state = InstallTargetState.INSTALLED
        elif LEGACY_PACKAGE_ID in installed:
            state = InstallTargetState.MIGRATION_CANDIDATE
        elif self.read()["aside"]:
            state = InstallTargetState.RETAINED_OR_AMBIGUOUS
        else:
            state = InstallTargetState.INSTALL_CANDIDATE
        return InstallTargetProbe(
            state=state,
            model="px30_evb",
            serial=self.serial,
            primary_abi="arm64-v8a",
            android_sdk=27,
        )

    async def hand_back_home(self, _request: web.Request) -> web.Response:
        self.hand_back_requests += 1
        if PACKAGE_ID not in self.read()["installed"]:
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

    async def health(_request: web.Request) -> web.Response:
        if fake.health_hook is not None:
            await fake.health_hook()
        if not fake.installed or fake.health_status != 200:
            return web.Response(status=fake.health_status if fake.installed else 503)
        return web.Response(
            text=(
                f"ha-paneld 1.0.0 panel=test build=100 cfg=abcd1234 did={fake.did} "
                f"pkg={fake.health_package}"
            )
        )

    app.router.add_get("/api/v1/health", health)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    pin = PinnedPanelTarget(
        original=PanelAddress(host="127.0.0.1", port=server.port),
        # ADB refuses anything but a private LAN address; the shell is local.
        pinned=PanelAddress(host="192.168.1.30", port=8888),
    )
    original_client = HaPaneldClient

    def panel_client(session: Any, address: PanelAddress) -> HaPaneldClient:
        return original_client(session, PanelAddress("127.0.0.1", server.port))

    with (
        patch.object(removal, "HaPaneldClient", side_effect=panel_client),
        patch.object(
            removal,
            "async_get_durable_adb_credential",
            AsyncMock(return_value=SimpleNamespace(signer=object())),
        ),
        patch.object(removal, "async_pin_install_target", AsyncMock(return_value=pin)),
        patch.object(removal, "async_probe_install_target", fake.probe),
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
        unique_id="a" * 64,
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


async def _choose_removal(admin: Any, menu: dict[str, Any]) -> dict[str, Any]:
    """Pick Remove in the Repair's menu, as the owner does."""
    assert menu["type"] == "menu", menu
    assert menu["menu_options"] == ["confirm", "keep_app"]
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}", json={"next_step_id": "confirm"}
    )
    assert response.status == 200
    return await response.json()


async def _remove(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    form = await _open_form(hass, entry)
    return await hass.config_entries.options.async_configure(form["flow_id"], {})


async def test_one_submit_removes_the_app_then_deletes_the_entry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """Submit under the risks link is the one consent; nothing else is asked."""
    fake, server = panel
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)
    assert fake.installed == [PACKAGE_ID], "opening the form removes nothing"
    assert fake.hand_back_requests == 0

    result = await hass.config_entries.options.async_configure(form["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is None


async def test_the_removal_form_names_the_panel_as_home_assistant_shows_it(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """The entry title is the panel's id; the owner knows it by its device name."""
    _fake, server = panel
    entry = _entry(hass, server)
    hass.config_entries.async_update_entry(entry, title="jenna_6c08")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Jenna",
    )
    dr.async_get(hass).async_update_device(device.id, name_by_user="Lounge display")

    form = await _open_form(hass, entry)

    assert form["description_placeholders"]["panel"] == "Lounge display"


async def test_removal_hands_home_back_then_removes_both_ids(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.set(installed=[LEGACY_PACKAGE_ID, PACKAGE_ID])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME
    assert fake.hand_back_requests == 1
    assert sorted(fake.read()["events"]) == sorted(
        [f"uninstall {PACKAGE_ID}", f"uninstall {LEGACY_PACKAGE_ID}"]
    )


@pytest.mark.parametrize("holder", [PACKAGE_ID, LEGACY_PACKAGE_ID])
async def test_an_app_holding_device_admin_gives_it_up_and_is_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], holder: str
) -> None:
    """Android refuses to uninstall an active device admin; the app is asked."""
    fake, server = panel
    fake.set(installed=[LEGACY_PACKAGE_ID, PACKAGE_ID], admin=[holder])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert fake.installed == []
    assert fake.read()["admin"] == []
    events = fake.read()["events"]
    assert events.count(f"release admin {holder}") == 1
    assert events.index(f"release admin {holder}") < events.index(f"uninstall {holder}")


async def test_a_slow_device_admin_release_is_awaited_within_the_budget(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """Android may take most of the ten-second wait to drop a released admin."""
    fake, server = panel
    fake.set(admin=[PACKAGE_ID], release_sleeps=9)
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert fake.installed == []


async def test_an_app_that_cannot_give_up_device_admin_is_kept_and_reported(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """A build without the release stays installed after a bounded wait."""
    fake, server = panel
    fake.set(admin=[PACKAGE_ID], no_release=[PACKAGE_ID])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == [PACKAGE_ID]


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
    assert fake.installed == [PACKAGE_ID]


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

    result = await hass.config_entries.options.async_configure(form["flow_id"], {})

    assert result.get("errors") == {"base": "removal_approval_required"}
    assert fake.installed == [PACKAGE_ID]
    assert fake.read()["home"] == APP_HOME

    fake.hand_back = "hand"  # approved on the panel; the same request again
    result = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert result.get("errors") is None, result.get("errors")
    assert result["reason"] == "removed_entry_deleted"
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
    assert fake.installed == [PACKAGE_ID]
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
    assert fake.installed == [PACKAGE_ID]


async def test_a_run_cut_short_after_one_uninstall_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.set(
        installed=[LEGACY_PACKAGE_ID, PACKAGE_ID],
        fail_uninstall=[LEGACY_PACKAGE_ID],
    )
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(form["flow_id"], {})

    assert first.get("errors") == {"base": "removal_remove_failed"}
    # The new app stays while the old one is left, so the retry can reach it.
    assert fake.installed == [LEGACY_PACKAGE_ID, PACKAGE_ID]
    assert fake.read()["home"] == VENDOR_HOME

    retry = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert retry["reason"] == "removed_entry_deleted"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME


async def test_a_connection_lost_after_the_uninstall_still_ends_removed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """The panel is read again, so an uninstall that ran is not reported failed."""
    fake, server = panel
    fake.drop_after = "pm uninstall"
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == []


async def test_a_dropped_connection_during_the_uninstall_is_finished_by_a_retry(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """An uninstall that did not run before the drop is reported; a retry runs it."""
    fake, server = panel
    fake.drop_after = "pm uninstall"
    fake.set(fail_uninstall=[PACKAGE_ID])
    entry = _entry(hass, server)
    form = await _open_form(hass, entry)

    first = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert first.get("errors") == {"base": "removal_remove_failed"}

    retry = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert retry["reason"] == "removed_entry_deleted"
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
    assert result["reason"] == "removed_entry_deleted"
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
    assert fake.installed == [PACKAGE_ID]


async def test_a_panel_busy_with_an_update_is_left_alone(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    assert claim_panel_operation(hass, entry.entry_id)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_busy"}
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0


async def test_an_unauthorized_panel_is_sent_to_authorize_adb_first(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.adb_authorized = False
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    # With no answering panel Configure has no Allow updates step to name.
    assert result.get("errors") == {"base": "removal_adb_authorization_unavailable"}
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0


@pytest.mark.parametrize(
    ("seam", "code", "error"),
    [
        (
            "_async_connect",
            InstallAdbErrorCode.AUTHORIZATION_REQUIRED,
            "removal_adb_authorization_unavailable",
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
        result = await hass.config_entries.options.async_configure(form["flow_id"], {})

    assert result.get("errors") == {"base": error}
    assert fake.installed == [PACKAGE_ID]
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
    fake.set(installed=[PACKAGE_ID, vendor_provisioning.SHELLY_LAUNCHER])
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": error}
    assert PACKAGE_ID in fake.installed
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
    assert fake.installed == [PACKAGE_ID]
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

    first = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert first.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == [LEGACY_PACKAGE_ID]

    retry = await hass.config_entries.options.async_configure(form["flow_id"], {})
    assert retry["reason"] == "removed_entry_deleted"
    assert fake.installed == []


@pytest.mark.parametrize("command", ["appops set", "wm density"])
async def test_a_restore_that_fails_still_removes_the_app(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], command: str
) -> None:
    """The vendor setup and the display size are put back best effort."""
    fake, server = panel
    fake.drop_after = command
    entry = _entry(hass, server)

    result = None
    failure = None
    try:
        result = await _remove(hass, entry)
    except Exception as err:
        failure = err

    assert failure is None, (
        f"Best-effort restoration must still finish removal: {failure!r}"
    )
    assert result is not None

    assert fake.drop_after is None  # the restore command ran and dropped
    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["reason"] == "removed_entry_deleted"
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
    fake.set(installed=[PACKAGE_ID, control_center])
    for name in ("screensaver_components", "screensaver_default_component"):
        (fake.state_path / f"setting.{name}").write_text("\n")
    (fake.state_path / f"appop.{control_center}.SYSTEM_ALERT_WINDOW").write_text(
        "deny\n"
    )
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == [control_center]
    events = fake.read()["events"]
    # Restores may run in any order, but must finish before terminal uninstall.
    assert events[-1:] == [f"uninstall {PACKAGE_ID}"]
    assert (fake.state_path / "setting.screensaver_components").read_text() == (
        "meta/.Dream\n"
    )
    assert not (fake.state_path / "setting.screensaver_default_component").exists()
    assert (
        fake.state_path / f"appop.{control_center}.SYSTEM_ALERT_WINDOW"
    ).read_text() == "allow\n"
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
    fake.set(installed=[PACKAGE_ID, launcher])
    (fake.state_path / f"appop.{launcher}.SYSTEM_ALERT_WINDOW").write_text("deny\n")
    (fake.state_path / "density").write_text("216\n")
    (fake.state_path / "setting.font_scale").write_text("1.2\n")
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") is None, (result.get("errors"), fake.read())
    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == [launcher]
    assert fake.read()["home"] == f"{launcher}/.activities.SplashActivity"
    # Only the order against uninstall matters, not the order among restores.
    assert fake.read()["events"][-1:] == [f"uninstall {PACKAGE_ID}"]
    assert (
        fake.state_path / f"appop.{launcher}.SYSTEM_ALERT_WINDOW"
    ).read_text() == "allow\n"
    assert not (fake.state_path / "setting.font_scale").exists()
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
    assert fake.installed == [PACKAGE_ID]
    assert fake.read()["home"] == APP_HOME
    assert fake.read()["events"] == []
    assert record.exists()


async def test_device_action_waits_for_owner_confirmation_then_deletes_entry(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    hass_client: Any,
    hass_read_only_user: Any,
) -> None:
    """The device's button opens a Repair; only its Submit changes the panel."""
    from homeassistant.components import persistent_notification
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.device import panel_display_name
    from custom_components.panel_assistant.repairs_removal import removal_issue_id

    from .test_native import _setup

    fake, _server = panel
    fake.did = "d" * 64
    entry = await _setup(hass, hass_read_only_user.id)
    assert await async_setup_component(hass, "repairs", {})
    await hass.async_block_till_done()
    entity = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_remove_from_panel"
    )
    assert entity is not None
    await hass.services.async_call(
        "button", "press", {"entity_id": entity}, blocking=True
    )
    issue_id = removal_issue_id(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.learn_more_url is not None
    assert "/go/removal-risks" in issue.learn_more_url
    assert fake.hand_back_requests == 0
    assert fake.installed == [PACKAGE_ID]
    # Pressing must visibly answer: a notification points at the Repair.
    notification_id = f"{DOMAIN}_remove_from_panel_{entry.entry_id}"
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert notification_id in notices
    assert panel_display_name(hass, entry) in notices[notification_id]["message"]
    assert "/config/repairs" in notices[notification_id]["message"]

    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    assert form.get("step_id") == "confirm"
    assert fake.hand_back_requests == 0
    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    result = await response.json()
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert fake.installed == []
    assert fake.read()["home"] == VENDOR_HOME
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert notification_id not in notices


async def test_removal_notice_survives_reload_and_goes_with_its_entry(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    hass_read_only_user: Any,
) -> None:
    """A reload keeps the press's notice; deleting the entry withdraws it."""
    from homeassistant.components import persistent_notification

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
    )

    from .test_native import _setup

    fake, _server = panel
    fake.did = "d" * 64
    entry = await _setup(hass, hass_read_only_user.id)
    await async_request_removal(hass, entry)
    notification_id = f"{DOMAIN}_remove_from_panel_{entry.entry_id}"
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert notification_id in notices
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert notification_id in notices
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert notification_id not in notices


async def test_options_removal_posts_no_press_notice(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    hass_read_only_user: Any,
) -> None:
    """The options-flow path records the Repair without the press notice."""
    from homeassistant.components import persistent_notification
    from homeassistant.helpers import issue_registry as ir

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    from .test_native import _setup

    fake, _server = panel
    fake.did = "d" * 64
    entry = await _setup(hass, hass_read_only_user.id)
    await async_request_removal(hass, entry, notify=False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, removal_issue_id(entry.entry_id))
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert f"{DOMAIN}_remove_from_panel_{entry.entry_id}" not in notices


async def test_stale_address_never_removes_a_different_enrolled_panel(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    fake.did = "b" * 64

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_panel_changed"}
    assert fake.hand_back_requests == 0
    assert fake.installed == [PACKAGE_ID]
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_retry_after_restart_never_adopts_a_replacement_at_same_address(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    """The original shell identity remains bound after a failed uninstall."""
    fake, server = panel
    entry = _entry(hass, server)
    fake.set(fail_uninstall=[PACKAGE_ID])
    result = await _remove(hass, entry)
    assert result.get("errors") == {"base": "removal_remove_failed"}
    assert fake.hand_back_requests == 1
    # The flow is gone; a newly opened flow must use the entry's saved custody.
    fake.serial = "OTHER_PANEL"

    retry = await _remove(hass, entry)

    assert retry.get("errors") == {"base": "removal_panel_changed"}
    assert fake.hand_back_requests == 1
    assert fake.installed == [PACKAGE_ID]
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_retry_rejects_a_new_installation_on_the_same_hardware(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    fake.set(fail_uninstall=[PACKAGE_ID])
    first = await _remove(hass, entry)
    assert first.get("errors") == {"base": "removal_remove_failed"}
    assert fake.hand_back_requests == 1
    fake.did = "b" * 64

    retry = await _remove(hass, entry)

    assert retry.get("errors") == {"base": "removal_panel_changed"}
    assert fake.hand_back_requests == 1
    assert fake.installed == [PACKAGE_ID]
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None


async def test_deleted_entry_withdraws_an_open_removal_confirmation(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    assert await async_setup_component(hass, "repairs", {})
    await async_request_removal(hass, entry)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": removal_issue_id(entry.entry_id)},
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    assert form.get("step_id") == "confirm"
    assert await hass.config_entries.async_remove(entry.entry_id)

    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []


async def test_entry_deleted_during_identity_read_prevents_panel_changes(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)

    async def remove_entry() -> None:
        assert await hass.config_entries.async_remove(entry.entry_id)

    fake.health_hook = remove_entry
    # The caller must receive the deliberate withdrawal result, not an internal
    # registry exception from continuing with a deleted entry.
    with pytest.raises(Exception) as result:
        await removal.async_remove_app(hass, entry)

    assert isinstance(result.value, removal.RemovalError)
    assert result.value.reason == "panel_changed"
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []


async def test_uninstall_keeps_repair_until_entry_deletion_has_completed(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    """The final restart cut retains a retry when the app has already gone."""
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    assert await async_setup_component(hass, "repairs", {})
    await async_request_removal(hass, entry)
    admin = await hass_client()
    issue_id = removal_issue_id(entry.entry_id)
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    removal_started = asyncio.Event()
    allow_entry_removal = asyncio.Event()
    remove_entry = hass.config_entries.async_remove

    async def paused_remove(entry_id: str) -> bool:
        removal_started.set()
        await allow_entry_removal.wait()
        return await remove_entry(entry_id)

    with patch.object(hass.config_entries, "async_remove", paused_remove):
        submitted = asyncio.create_task(
            admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
        )
        try:
            await asyncio.wait_for(removal_started.wait(), timeout=5)
            assert fake.installed == []
            assert not submitted.done()
            assert hass.config_entries.async_get_entry(entry.entry_id) is not None
            assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        finally:
            allow_entry_removal.set()
        response = await submitted
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_options_uninstall_retains_retry_when_entry_deletion_is_interrupted(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    """An unloaded, app-less entry can still finish the owner's saved request."""
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import removal_issue_id

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    with patch.object(
        hass.config_entries, "async_remove", AsyncMock(return_value=False)
    ):
        result = await _remove(hass, entry)
        await hass.async_block_till_done()
    assert result["reason"] == "removed_entry_deleted"
    assert fake.installed == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None
    issue_id = removal_issue_id(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    assert await async_setup_component(hass, "repairs", {})
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_repair_keeps_request_when_home_assistant_declines_entry_deletion(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    await async_request_removal(hass, entry)
    issue_id = removal_issue_id(entry.entry_id)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    with patch.object(
        hass.config_entries, "async_remove", AsyncMock(return_value=False)
    ):
        response = await admin.post(
            f"/api/repairs/issues/fix/{form['flow_id']}", json={}
        )
    assert response.status == 200
    first = await response.json()
    assert first["type"] == "form"
    assert first.get("errors") == {"base": "removal_remove_failed"}
    assert fake.installed == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is not None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_removal_without_a_held_adb_key_makes_no_panel_change(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    from custom_components.panel_assistant import adb_credentials

    fake, server = panel
    entry = _entry(hass, server)
    credential_path = Path(hass.config.path(".storage/panel_assistant.adb_key"))
    assert not await asyncio.to_thread(credential_path.exists)
    generation_attempts: list[bool] = []

    def unexpected_generation() -> None:
        generation_attempts.append(True)
        raise AssertionError("Removal must use the key already held")

    with (
        patch.object(
            removal,
            "async_get_durable_adb_credential",
            adb_credentials.async_get_durable_adb_credential,
        ),
        patch.object(adb_credentials, "_generate_credential", unexpected_generation),
    ):
        result = await _remove(hass, entry)
    assert generation_attempts == []
    assert result.get("errors") == {"base": "removal_adb_unreachable"}
    assert not await asyncio.to_thread(credential_path.exists)
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []


@pytest.mark.parametrize("recovery", ["verified", "failed", "different-panel"])
async def test_local_removal_proves_surviving_network_before_uninstall(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    monkeypatch: pytest.MonkeyPatch,
    recovery: str,
) -> None:
    from custom_components.panel_assistant import local_adb
    from custom_components.panel_assistant.adb_protocol import AdbInstallTarget

    fake, server = panel
    entry = _entry(hass, server)
    target = AdbInstallTarget(
        PanelAddress("192.168.1.30", 8888),
        "PANEL1",
        "px30_evb",
        "arm64-v8a",
        27,
        local_shell=object(),
    )
    # A failed recovery may leave a listener open; its acknowledgement is
    # still required. Keep this outside the call the mutation removes.
    fake.adb_authorized = recovery == "failed"
    monkeypatch.setattr(local_adb, "async_target", AsyncMock(return_value=target))

    async def recover(*_args: Any) -> None:
        assert PACKAGE_ID in fake.installed, "recovery must precede terminal uninstall"
        assert fake.read()["home"] == VENDOR_HOME
        if recovery == "failed":
            raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
        fake.adb_authorized = True
        if recovery == "different-panel":
            fake.serial = "OTHER"

    monkeypatch.setattr(local_adb, "async_recover", recover)

    async def hand_back(*_args: Any) -> None:
        await fake.hand_back_home(None)

    monkeypatch.setattr(local_adb, "async_hand_back_home", hand_back)
    if recovery == "verified":
        await removal.async_remove_app(hass, entry)
        assert fake.installed == []
    else:
        with pytest.raises(removal.RemovalError):
            await removal.async_remove_app(hass, entry)
        assert fake.installed == [PACKAGE_ID]


@pytest.mark.parametrize("cut", ["observe", "handback", "display", "network-observe"])
async def test_withdrawn_entry_stops_removal_at_each_awaited_panel_boundary(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cut: str,
) -> None:
    """Deleting the entry revokes consent before the next external mutation."""
    from custom_components.panel_assistant import local_adb
    from custom_components.panel_assistant.adb_protocol import AdbInstallTarget

    fake, server = panel
    entry = _entry(hass, server)
    target = AdbInstallTarget(
        PanelAddress("192.168.1.30", 8888),
        "PANEL1",
        "px30_evb",
        "arm64-v8a",
        27,
        local_shell=object(),
    )
    monkeypatch.setattr(local_adb, "async_target", AsyncMock(return_value=target))
    # An actual vendor restore would consume this record and change Settings.
    record = tmp_path / "vendor-record"
    monkeypatch.setattr(vendor_provisioning, "META_RESTORE_RECORD", str(record))
    record.write_text(
        "screensaver_components=meta/.Dream\nscreensaver_default_component=null\ncontrolcenter_overlay=allow\n"
    )
    control_center = vendor_provisioning.META_CONTROL_CENTER
    fake.set(installed=[PACKAGE_ID, control_center])
    (fake.state_path / "setting.screensaver_components").write_text("\n")
    (fake.state_path / f"appop.{control_center}.SYSTEM_ALERT_WINDOW").write_text(
        "deny\n"
    )

    async def withdraw() -> None:
        assert await hass.config_entries.async_remove(entry.entry_id)

    async def hand_back(*_args: Any) -> None:
        await fake.hand_back_home(None)
        if cut == "handback":
            await withdraw()

    monkeypatch.setattr(local_adb, "async_hand_back_home", hand_back)
    fake.adb_authorized = False

    async def recover(*_args: Any) -> None:
        fake.adb_authorized = True

    monkeypatch.setattr(local_adb, "async_recover", recover)
    move = removal.async_move_step
    observations = 0

    async def move_then_withdraw(*args: Any) -> Any:
        nonlocal observations
        outcome = await move(*args)
        if args[2] is install_adb.MoveStep.OBSERVE:
            observations += 1
            if (cut == "observe" and observations == 1) or (
                cut == "network-observe" and observations == 2
            ):
                await withdraw()
        return outcome

    monkeypatch.setattr(removal, "async_move_step", move_then_withdraw)
    restore = install_adb.async_restore_display_sizing

    async def restore_then_withdraw(*args: Any) -> Any:
        outcome = await restore(*args)
        if cut == "display":
            await withdraw()
        return outcome

    monkeypatch.setattr(
        install_adb, "async_restore_display_sizing", restore_then_withdraw
    )
    with pytest.raises(removal.RemovalError, match="panel_changed"):
        await removal.async_remove_app(hass, entry)

    assert PACKAGE_ID in fake.installed
    assert not any(event.startswith("uninstall ") for event in fake.read()["events"])
    if cut == "observe":
        assert fake.hand_back_requests == 0
        assert fake.read()["home"] == APP_HOME
    if cut in {"observe", "handback"}:
        assert record.exists()
        assert (fake.state_path / "setting.screensaver_components").read_text() == "\n"
    if cut != "network-observe":
        assert not fake.adb_authorized, "withdrawn consent must not reopen network ADB"


async def test_first_removal_cannot_trust_a_silent_app_even_when_home_is_safe(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.health_status = 503
    fake.set(home=VENDOR_HOME)
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_app_unreachable"}
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry


async def test_app_absence_does_not_authorize_custody_of_another_entry_identity(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            removal.CONF_REMOVAL_TARGET: {
                "did": entry.unique_id,
                "serial": fake.serial,
                "model": "px30_evb",
                "primary_abi": "arm64-v8a",
                "android_sdk": 27,
            },
        },
    )
    hass.config_entries.async_update_entry(entry, unique_id="b" * 64)
    fake.set(installed=[])

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_panel_changed"}
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []


async def test_live_removal_identity_requires_a_supported_app_package(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer]
) -> None:
    fake, server = panel
    fake.health_package = "com.example.other"
    entry = _entry(hass, server)

    result = await _remove(hass, entry)

    assert result.get("errors") == {"base": "removal_panel_changed"}
    assert fake.installed == [PACKAGE_ID]
    assert fake.hand_back_requests == 0
    assert fake.read()["events"] == []
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry


async def test_repair_keeps_failed_app_removal_retryable(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    fake.set(fail_uninstall=[PACKAGE_ID])
    entry = _entry(hass, server)
    await async_request_removal(hass, entry)
    issue_id = removal_issue_id(entry.entry_id)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())

    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})

    assert response.status == 200
    result = await response.json()
    assert result["type"] == "form"
    assert result["errors"] == {"base": "removal_remove_failed"}
    assert fake.installed == [PACKAGE_ID]
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize("consent", ["withdrawn", "same-entry"])
async def test_withdrawal_during_vendor_restore_preserves_pending_display_restore(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    consent: str,
) -> None:
    """Vendor completion grants no further changes after entry consent ends."""
    fake, server = panel
    entry = _entry(hass, server)
    display = tmp_path / "display-record"
    monkeypatch.setattr(display_provisioning, "DISPLAY_RESTORE_RECORD", str(display))
    display_record = "density=none\nfont_scale=null\n"
    display.write_text(display_record)
    (fake.state_path / "density").write_text("216\n")
    vendor = tmp_path / "vendor-record"
    monkeypatch.setattr(vendor_provisioning, "META_RESTORE_RECORD", str(vendor))
    vendor.write_text(
        "screensaver_components=meta/.Dream\nscreensaver_default_component=null\ncontrolcenter_overlay=allow\n"
    )
    control_center = vendor_provisioning.META_CONTROL_CENTER
    fake.set(installed=[PACKAGE_ID, control_center])
    (fake.state_path / "setting.screensaver_components").write_text("\n")
    overlay = fake.state_path / f"appop.{control_center}.SYSTEM_ALERT_WINDOW"
    overlay.write_text("deny\n")
    restore = install_adb.async_restore_vendor_setup

    async def restore_then_withdraw(*args: Any) -> Any:
        outcome = await restore(*args)
        if consent == "withdrawn":
            assert await hass.config_entries.async_remove(entry.entry_id)
        return outcome

    monkeypatch.setattr(
        install_adb, "async_restore_vendor_setup", restore_then_withdraw
    )
    if consent == "withdrawn":
        with pytest.raises(removal.RemovalError, match="panel_changed"):
            await removal.async_remove_app(hass, entry)
    else:
        await removal.async_remove_app(hass, entry)

    # Both callers completed the real shell vendor restoration before this cut.
    assert not vendor.exists()
    assert (fake.state_path / "setting.screensaver_components").read_text() == (
        "meta/.Dream\n"
    )
    assert overlay.read_text() == "allow\n"
    assert fake.hand_back_requests == 1
    assert fake.read()["home"] == VENDOR_HOME
    assert fake.adb_authorized, "removal must preserve the network recovery route"
    recovery = await fake.probe()
    assert recovery.serial == "PANEL1"
    if consent == "withdrawn":
        assert recovery.state is InstallTargetState.INSTALLED
        assert hass.config_entries.async_get_entry(entry.entry_id) is None
        assert PACKAGE_ID in fake.installed
        assert display.exists(), (
            "display restore must wait when entry consent ended during vendor restore"
        )
        assert display.read_text() == display_record
        assert (fake.state_path / "density").read_text() == "216\n"
        assert not any(
            event.startswith("uninstall ") for event in fake.read()["events"]
        )
    else:
        assert hass.config_entries.async_get_entry(entry.entry_id) is entry
        assert fake.installed == [control_center]
        assert not display.exists()
        assert not (fake.state_path / "density").exists()
        assert f"uninstall {PACKAGE_ID}" in fake.read()["events"]


async def test_the_owner_can_withdraw_a_removal_request_and_keep_the_app(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    """Keep Panel Assistant clears the Repair and touches neither panel nor entry."""
    from homeassistant.components import persistent_notification
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    await async_request_removal(hass, entry)
    issue_id = removal_issue_id(entry.entry_id)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    menu = await response.json()
    assert menu["type"] == "menu", menu
    response = await admin.post(
        f"/api/repairs/issues/fix/{menu['flow_id']}", json={"next_step_id": "keep_app"}
    )
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert fake.installed == [PACKAGE_ID]
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert f"{DOMAIN}_remove_from_panel_{entry.entry_id}" not in notices
    assert fake.hand_back_requests == 0

    await async_request_removal(hass, entry)
    again = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert again is not None
    assert again.dismissed_version is None


async def test_an_unauthorized_panel_is_asked_to_allow_home_assistant_in_the_repair(
    hass: HomeAssistant, panel: tuple[FakePanel, TestServer], hass_client: Any
) -> None:
    """The Repair offers its own Allow step instead of naming one it lacks."""
    from homeassistant.setup import async_setup_component

    from custom_components.panel_assistant.removal import REASON_ADB_AUTHORIZATION
    from custom_components.panel_assistant.repairs_removal import (
        async_request_removal,
        removal_issue_id,
    )

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    entry = _entry(hass, server)
    await async_request_removal(hass, entry)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": removal_issue_id(entry.entry_id)},
    )
    form = await _choose_removal(admin, await response.json())
    with patch(
        "custom_components.panel_assistant.repairs_removal.async_remove_app",
        AsyncMock(side_effect=removal.RemovalError(REASON_ADB_AUTHORIZATION)),
    ):
        response = await admin.post(
            f"/api/repairs/issues/fix/{form['flow_id']}", json={}
        )
    result = await response.json()

    assert result["type"] == "form", result
    assert result["step_id"] == "authorize_adb"
    assert fake.installed == [PACKAGE_ID]

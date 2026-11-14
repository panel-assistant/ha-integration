"""Typed helper commands give the same outcomes as the shell programs they replace."""

import asyncio
import base64
import logging
import re
from collections.abc import Mapping
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    adb_protocol,
    install_adb,
    local_adb,
    local_commands,
)
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    PACKAGE_ID,
)
from custom_components.panel_assistant.client import normalize_address
from custom_components.panel_assistant.transport_sessions import async_get_sessions
from custom_components.panel_assistant.vendor_provisioning import (
    UNREAD_SETUP_WARNING,
    ShellySetup,
    VendorSetup,
)

from .install_adb_fakes import _identity_root_output, _single_output
from .test_native import _setup
from .test_transport import WsClientFactory, _receive
from .test_transport_commands import _connect

LAUNCH = LAUNCH_COMPONENTS[PACKAGE_ID][-1]
IDENTITY = {
    "model": "Test Panel",
    "serial": "SERIAL-1",
    "abi": "arm64-v8a",
    "sdk": "34",
    "uid": "0",
    "secure": "1",
    "debuggable": "0",
    "su": "absent",
}
GRANTED = {
    "accessibility": "granted",
    "accessibility_enabled": "1",
    "op.WRITE_SETTINGS": "allow",
    "op.SYSTEM_ALERT_WINDOW": "allow",
    "op.REQUEST_INSTALL_PACKAGES": "allow",
    "notifications": "granted",
    "camera": "granted",
    "microphone": "unsupported",
    "secure_settings": "unsupported",
    "device": "lahaina",
    "vendor": "other",
}
HOMED = {
    "policy": "claimed",
    "chooser": "set",
    "home": "io.panelassistant.android/.DashboardActivity",
}


class FakeCommands:
    """Answers each verb with fixed fields, after the identity every reply carries."""

    def __init__(self, replies: Mapping[str, Any], **identity: str) -> None:
        self.replies = dict(replies)
        self.identity = {**IDENTITY, **identity}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run(self, verb: str, args: tuple[str, ...] = ()) -> dict[str, str]:
        self.calls.append((verb, tuple(args)))
        reply = self.replies[verb]
        if isinstance(reply, BaseException):
            raise reply
        return {**self.identity, **reply}


class NoShell:
    """A carrier that fails the test if anything sends shell text through it."""

    async def streaming_shell(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a typed route sent shell text")
        yield  # pragma: no cover

    async def close(self) -> None:
        """Nothing to close."""


def _target(commands: FakeCommands, sdk: int = 34) -> AdbInstallTarget:
    return AdbInstallTarget(
        normalize_address("192.168.1.23"),
        "SERIAL-1",
        "Test Panel",
        "arm64-v8a",
        sdk,
        local_shell=NoShell(),  # type: ignore[arg-type]
        local_commands=commands,
    )


@pytest.mark.parametrize(
    ("uid", "secure", "debuggable", "su"),
    [
        (uid, secure, debuggable, su)
        for uid in ("0", "2000", "1000")
        for secure in ("0", "1")
        for debuggable in ("0", "1")
        for su in ("present", "absent", "abnormal")
    ],
)
def test_typed_and_shell_postures_give_the_same_root_mode(
    uid: str, secure: str, debuggable: str, su: str
) -> None:
    sections = {
        "UID": ([uid], 0),
        "SECURE": ([secure], 0),
        "DEBUGGABLE": ([debuggable], 0),
        "SU": ([su], 0),
    }

    def outcome(read: Any) -> Any:
        try:
            return read()
        except adb_protocol._MalformedAdbResponse:
            return "malformed"
        except InstallAdbError as err:
            return err.code

    fields = {**IDENTITY, "uid": uid, "secure": secure, "debuggable": debuggable}
    fields["su"] = su
    assert outcome(lambda: local_commands.root_mode_from_fields(fields)) == outcome(
        lambda: adb_protocol._parse_root_mode(sections)
    )


def test_su_present_absent_abnormal_map_like_the_shell() -> None:
    def mode(su: str, uid: str = "2000") -> Any:
        return local_commands.root_mode_from_fields({**IDENTITY, "uid": uid, "su": su})

    assert mode("present") is AdbRootMode.ROOT_SU
    assert mode("absent") is AdbRootMode.ROOTLESS
    with pytest.raises(adb_protocol._MalformedAdbResponse):
        mode("abnormal")
    assert mode("absent", uid="0") is AdbRootMode.ROOT_ADBD


@pytest.mark.parametrize(
    "bad",
    [
        {"serial": "SERIAL 1"},
        {"abi": "arm64/v8a"},
        {"sdk": "0"},
        {"sdk": "1000"},
        {"model": " Test Panel"},
        {"model": ""},
        {"secure": "unknown"},
    ],
)
async def test_typed_identity_is_bounded_like_the_shell(bad: dict[str, str]) -> None:
    commands = FakeCommands({"PA_OBSERVE": {}})
    commands.replies["PA_OBSERVE"] = {
        "legacy": "absent",
        "new": "installed",
        "shelly": "absent",
        "home": "com.vendor/.Launcher",
        "firmware": "unknown",
        **bad,
    }
    with pytest.raises(InstallAdbError) as err:
        await install_adb.async_move_step(
            _target(commands), None, install_adb.MoveStep.OBSERVE
        )
    assert err.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE


async def test_typed_reply_from_another_panel_fails_the_identity_pin() -> None:
    commands = FakeCommands(
        {
            "PA_OBSERVE": {
                "serial": "SERIAL-2",
                "legacy": "absent",
                "new": "installed",
                "shelly": "absent",
                "home": "com.vendor/.Launcher",
                "firmware": "V1",
            }
        }
    )
    with pytest.raises(InstallAdbError) as err:
        await install_adb.async_move_step(
            _target(commands), None, install_adb.MoveStep.OBSERVE
        )
    assert err.value.code is InstallAdbErrorCode.TARGET_CHANGED


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "legacy": "installed",
                "new": "absent",
                "shelly": "installed",
                "home": "cloud.shelly.stargate/.activities.SplashActivity",
                "firmware": "V1.2.3",
            },
            install_adb.MoveObservation(
                legacy_installed=True,
                successor_installed=False,
                home="cloud.shelly.stargate",
                firmware="V1.2.3",
                shelly_installed=True,
            ),
        ),
        (
            {
                "legacy": "absent",
                "new": "installed",
                "shelly": "absent",
                "home": "android/com.android.internal.app.ResolverActivity",
                "firmware": "unknown",
            },
            install_adb.MoveObservation(False, True, None, None, False),
        ),
    ],
)
async def test_typed_move_observation_matches_the_shell_observation(
    fields: dict[str, str], expected: install_adb.MoveObservation
) -> None:
    commands = FakeCommands({"PA_OBSERVE": fields})
    observed = await install_adb.async_move_step(
        _target(commands), None, install_adb.MoveStep.OBSERVE
    )
    assert observed == expected
    assert commands.calls == [("PA_OBSERVE", ("move",))]


async def test_typed_observation_failure_is_not_absence() -> None:
    commands = FakeCommands(
        {
            "PA_OBSERVE": {
                "legacy": "failed",
                "new": "installed",
                "shelly": "absent",
                "home": "com.vendor/.Launcher",
                "firmware": "V1",
            }
        }
    )
    with pytest.raises(InstallAdbError) as err:
        await install_adb.async_move_step(
            _target(commands), None, install_adb.MoveStep.OBSERVE
        )
    assert err.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE


@pytest.mark.parametrize(("handed", "ok"), [("1", True), ("0", False)])
async def test_typed_handback_requires_the_exact_component(
    handed: str, ok: bool
) -> None:
    move = {
        "legacy": "absent",
        "new": "installed",
        "shelly": "absent",
        "home": "com.vendor/com.vendor.Launcher",
        "firmware": "V1",
    }
    commands = FakeCommands({"PA_HANDBACK": {"handed": handed, **move}})
    step = install_adb.async_move_step(
        _target(commands),
        None,
        install_adb.MoveStep.HAND_BACK_HOME,
        home_target="com.vendor/com.vendor.Launcher",
    )
    if ok:
        assert (await step).home == "com.vendor"
    else:
        with pytest.raises(InstallAdbError) as err:
            await step
        assert err.value.code is InstallAdbErrorCode.INSTALL_AMBIGUOUS
    assert commands.calls == [
        ("PA_HANDBACK", ("component", "com.vendor/com.vendor.Launcher"))
    ]


async def test_typed_vendor_home_hands_to_the_fixed_splash() -> None:
    commands = FakeCommands(
        {
            "PA_HANDBACK": {
                "handed": "1",
                "legacy": "absent",
                "new": "installed",
                "shelly": "installed",
                "home": "cloud.shelly.stargate/.activities.SplashActivity",
                "firmware": "V1",
            }
        }
    )
    observed = await install_adb.async_move_step(
        _target(commands), None, install_adb.MoveStep.VENDOR_HOME
    )
    assert observed.home == "cloud.shelly.stargate"
    assert commands.calls == [("PA_HANDBACK", ("vendor",))]


async def test_typed_vendor_home_not_handed_is_read_back_like_the_shell() -> None:
    """Removal judges an unhanded vendor HOME from ``.home``, as on the shell."""
    commands = FakeCommands(
        {
            "PA_HANDBACK": {
                "handed": "0",
                "legacy": "absent",
                "new": "installed",
                "shelly": "absent",
                "home": "com.vendor/com.vendor.Launcher",
                "firmware": "V1",
            }
        }
    )
    observed = await install_adb.async_move_step(
        _target(commands), None, install_adb.MoveStep.VENDOR_HOME
    )
    assert observed.home == "com.vendor"
    assert not observed.shelly_installed


@pytest.mark.parametrize("home", ["", "No activity found"])
def test_typed_grant_with_a_non_component_home_is_not_homed(home: str) -> None:
    grant = local_commands.permission_grant_from_fields(
        GRANTED, {**HOMED, "home": home}, LAUNCH
    )
    assert grant.missing == ()
    assert grant.home is False
    assert grant.chooser == "set"


@pytest.mark.parametrize("device", ["", "odd device!"])
def test_typed_grant_with_any_device_is_a_non_meta_device(device: str) -> None:
    grant = local_commands.permission_grant_from_fields(
        {**GRANTED, "device": device}, HOMED, LAUNCH
    )
    assert grant.missing == ()
    assert grant.home is True
    assert grant.vendor_setup is False


@pytest.mark.parametrize("result", ["restored", "unrestored", "none"])
async def test_typed_restores_report_the_shell_outcomes(result: str) -> None:
    commands = FakeCommands({"PA_RESTORE": {"result": result}})
    target = _target(commands)
    assert await install_adb.async_restore_vendor_setup(target, None) == result
    assert await install_adb.async_restore_display_sizing(target, None) == result
    assert commands.calls == [("PA_RESTORE", ("vendor",)), ("PA_RESTORE", ("display",))]


async def test_typed_restore_with_an_unknown_result_is_unreachable() -> None:
    commands = FakeCommands({"PA_RESTORE": {"result": "maybe"}})
    with pytest.raises(InstallAdbError) as err:
        await install_adb.async_restore_vendor_setup(_target(commands), None)
    assert err.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE


async def test_typed_repair_with_every_grant_warns_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    commands = FakeCommands({"PA_REPAIR": GRANTED, "PA_HOME": HOMED})
    target = _target(commands)
    with caplog.at_level(logging.WARNING):
        chooser = await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
        )
    assert chooser == "set"
    assert commands.calls == [("PA_REPAIR", ("1", "1")), ("PA_HOME", ("1", "repair"))]
    assert not caplog.records


async def test_typed_repair_reports_the_shell_missing_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repair = {
        **GRANTED,
        "accessibility": "unknown",
        "op.SYSTEM_ALERT_WINDOW": "default",
        "camera": "unknown",
        "notifications": "unknown",
    }
    home = {"policy": "builtin", "chooser": "other_home", "home": "unknown"}
    commands = FakeCommands({"PA_REPAIR": repair, "PA_HOME": home})
    target = _target(commands)
    with caplog.at_level(logging.WARNING):
        chooser = await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
            deferred_setup=True,
        )
    assert commands.calls[1] == ("PA_HOME", ("1", "deferred"))
    messages = [record.getMessage() for record in caplog.records]
    assert chooser == "other_home"
    assert any("notification permission" in message for message in messages)
    assert any("did not make" in message for message in messages)
    assert any(
        "missing: accessibility service, display over other apps, camera" in m
        for m in messages
    )


async def test_typed_repair_below_sdk_33_never_warns_about_notifications(
    caplog: pytest.LogCaptureFixture,
) -> None:
    commands = FakeCommands(
        {
            "PA_REPAIR": {**GRANTED, "notifications": "unsupported"},
            "PA_HOME": HOMED,
        },
        sdk="27",
    )
    target = _target(commands, sdk=27)
    with caplog.at_level(logging.WARNING):
        await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
        )
    assert not caplog.records


async def test_typed_repair_with_an_unbounded_readback_counts_as_unread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    commands = FakeCommands(
        {"PA_REPAIR": {**GRANTED, "camera": "granted!"}, "PA_HOME": HOMED}
    )
    target = _target(commands)
    with caplog.at_level(logging.WARNING):
        await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
        )
    assert any("grant readback" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("vendor", "device", "converged", "warning"),
    [
        ("shelly", "shelly", "0", ShellySetup.warning),
        ("other", "meta", "0", VendorSetup.warning),
        ("shelly", "shelly", "1", None),
        ("shelly", "nonsense", "1", UNREAD_SETUP_WARNING),
    ],
)
async def test_typed_vendor_setup_warns_like_the_shell(
    caplog: pytest.LogCaptureFixture,
    vendor: str,
    device: str,
    converged: str,
    warning: str | None,
) -> None:
    repair_device = "omni" if device == "meta" else "lahaina"
    commands = FakeCommands(
        {
            "PA_REPAIR": {**GRANTED, "vendor": vendor, "device": repair_device},
            "PA_HOME": HOMED,
            "PA_VENDOR": {"device": device, "converged": converged},
        }
    )
    target = _target(commands)
    with caplog.at_level(logging.WARNING):
        await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
        )
    assert commands.calls[-1] == ("PA_VENDOR", ("setup",))
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ([] if warning is None else [warning])


async def test_typed_repair_without_a_vendor_skips_vendor_setup() -> None:
    commands = FakeCommands({"PA_REPAIR": GRANTED, "PA_HOME": HOMED})
    target = _target(commands)
    await install_adb._async_repair_app_permissions(
        target.local_shell,  # type: ignore[arg-type]
        target,
        PACKAGE_ID,
        LAUNCH,
    )
    assert [verb for verb, _args in commands.calls] == ["PA_REPAIR", "PA_HOME"]


@pytest.mark.parametrize(
    ("fields", "done", "home"),
    [
        (
            {"admin": "active", "home": "io.panelassistant.android/.DashboardActivity"},
            True,
            "io.panelassistant.android/.DashboardActivity",
        ),
        ({"admin": "unknown", "home": "unknown"}, False, "unknown"),
        ({"admin": "yes", "home": "x"}, False, "x"),
    ],
)
async def test_typed_admin_claim_reads_back_like_the_shell(
    fields: dict[str, str], done: bool, home: str
) -> None:
    from custom_components.panel_assistant.coordinator import _async_admin_claim

    commands = FakeCommands({"PA_ADMIN": fields})
    target = _target(commands)
    assert await _async_admin_claim(
        target.local_shell,  # type: ignore[arg-type]
        target,
        PACKAGE_ID,
    ) == (done, home)
    assert commands.calls == [("PA_ADMIN", ())]


async def test_typed_identity_root_reads_the_posture_without_a_shell() -> None:
    commands = FakeCommands({"PA_IDENTITY": {"uid": "2000", "su": "absent"}})
    target = _target(commands)
    assert (
        await install_adb._async_identity_root(
            target.local_shell,  # type: ignore[arg-type]
            target,
        )
        is AdbRootMode.ROOTLESS
    )


@pytest.mark.parametrize(
    "result",
    [
        {"ok": True, "fields": {"a": "b=c"}},
        {"ok": True, "fields": {"a": "x" * 256}},
        {"ok": True, "fields": {"a": "line\nbreak"}},
        {"ok": True, "fields": {"bad key": "x"}},
        {"ok": True, "fields": {"a": 1}},
        {"ok": False, "fields": {}},
        {"ok": True, "fields": {}, "extra": 1},
        {"ok": True, "fields": {f"k{n}": "v" for n in range(65)}},
    ],
)
def test_reply_fields_are_bounded_at_the_boundary(result: dict[str, Any]) -> None:
    with pytest.raises(InstallAdbError):
        local_commands.bounded_fields(result)


# Routing through a real authenticated session.


async def _panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user: Any,
    token: str,
    capabilities: list[str],
) -> Any:
    entry = await _setup(hass, user.id)
    panel = await _connect(
        hass, hass_ws_client, token, capabilities=capabilities, sync=False
    )
    return entry, panel


async def _answer_ready(panel: Any) -> None:
    ready = (await _receive(panel.client))["event"]
    assert ready["op"] == "adb.ready"
    await panel.answer(
        ready["command_id"],
        "applied",
        result={"ready": True, "managed": True, "recovery_hold": False},
    )


async def _answer_identity_cmd(panel: Any) -> None:
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    assert event["args"] == {"verb": "PA_IDENTITY", "args": []}
    await panel.answer(
        event["command_id"], "applied", result={"ok": True, "fields": IDENTITY}
    )


async def _answer_identity_shells(panel: Any) -> None:
    for prefix in ("ID", "POSTURE"):
        event = (await _receive(panel.client))["event"]
        assert event["op"] == "adb.shell"
        nonce = re.search(
            r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", event["args"]["command"]
        )[1]
        body = _identity_root_output(nonce, uid="0")
        if prefix == "ID":
            body = (
                body.decode()
                .replace("POSTURE", "ID")
                .split(f"HAPANELD_ID_UID_BEGIN:{nonce}")[0]
                + f"HAPANELD_ID_END:{nonce}\n"
            ).encode()
        await panel.answer(
            event["command_id"],
            "applied",
            result={
                "stdout_b64": base64.b64encode(body).decode(),
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
            },
        )


async def test_granted_capability_routes_through_typed_commands(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    assert "local_adb_commands_v1" in panel.result["capabilities"]
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_cmd(panel)
    target = await task
    assert target is not None
    assert target.local_commands is not None
    assert target.serial == "SERIAL-1"
    restore = asyncio.create_task(install_adb.async_restore_vendor_setup(target, None))
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    assert event["args"] == {"verb": "PA_RESTORE", "args": ["vendor"]}
    await panel.answer(
        event["command_id"],
        "applied",
        result={"ok": True, "fields": {**IDENTITY, "result": "none"}},
    )
    assert await restore == "none"


async def test_absent_capability_routes_through_shell_text(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb"],
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_shells(panel)
    target = await task
    assert target is not None
    assert target.local_commands is None
    assert target.local_shell is not None


async def test_expired_neither_falls_back_nor_clears_the_capability(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    await panel.answer(event["command_id"], "refused", code="expired")
    with pytest.raises(InstallAdbError):
        await task
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    assert "local_adb_commands_v1" in session.capabilities
    assert session.local_adb_commands_disabled is False
    # No shell was sent instead: the next message answers a fresh request.
    await panel.nothing_sent()
    # After the helper is replaced the same session goes typed again.
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_cmd(panel)
    assert (await task).local_commands is not None


async def test_unknown_command_disables_typed_commands_for_the_session(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    await panel.answer(event["command_id"], "refused", code="unknown_command")
    with pytest.raises(InstallAdbError):
        await task
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and session.local_adb_commands_disabled
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_shells(panel)
    assert (await task).local_commands is None
    # A command object built before the refusal honours it on every later run.
    with pytest.raises(InstallAdbError):
        await local_adb.LocalAdbCommands(hass, entry, session).run(
            "PA_RESTORE", ("vendor",)
        )
    await panel.nothing_sent()


async def test_operation_after_unknown_command_reruns_over_shell_text(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_cmd(panel)
    typed_target = await task
    assert typed_target is not None and typed_target.local_commands is not None
    restore = asyncio.create_task(
        install_adb.async_restore_vendor_setup(typed_target, None)
    )
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    await panel.answer(event["command_id"], "refused", code="unknown_command")
    with pytest.raises(InstallAdbError):
        await restore
    # The retry builds a fresh target, which now carries shell text.
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_shells(panel)
    shell_target = await task
    assert shell_target is not None and shell_target.local_commands is None
    restore = asyncio.create_task(
        install_adb.async_restore_vendor_setup(shell_target, None)
    )
    for prefix in ("POSTURE", "VENDOR_RESTORE"):
        event = (await _receive(panel.client))["event"]
        assert event["op"] == "adb.shell"
        nonce = re.search(
            rf"HAPANELD_{prefix}_BEGIN:([a-f0-9]{{32}})", event["args"]["command"]
        )[1]
        body = (
            _identity_root_output(nonce, uid="0")
            if prefix == "POSTURE"
            else _single_output(prefix, nonce, ["none"], 0)
        )
        await panel.answer(
            event["command_id"],
            "applied",
            result={
                "stdout_b64": base64.b64encode(body).decode(),
                "exit_code": 0,
                "timed_out": False,
                "truncated": False,
            },
        )
    assert await restore == "none"


async def test_app_busy_reply_reaches_the_busy_refusal(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """The app reports a held HOME lock as failed/busy; PA reads it as busy."""
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None
    run = asyncio.create_task(
        local_adb.LocalAdbCommands(hass, entry, session).run("PA_HOME", (LAUNCH,))
    )
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    # The wire code must be one of the contract's; the helper's reason rides
    # in the result, as the app already sends it.
    await panel.answer(
        event["command_id"], "failed", code="failed", result={"code": "busy"}
    )
    with pytest.raises(local_commands.LocalCommandRefused) as err:
        await run
    assert err.value.refusal == "busy"


async def test_typed_remove_app_stays_on_network_adb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = FakeCommands({})
    reached = []

    async def connect(*_args: Any, **_kwargs: Any) -> Any:
        reached.append("network")
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)

    monkeypatch.setattr(install_adb, "_async_connect", connect)
    with pytest.raises(InstallAdbError):
        await install_adb.async_move_step(
            _target(commands), None, install_adb.MoveStep.REMOVE_APP
        )
    assert commands.calls == []
    assert reached == ["network"]


async def test_helper_args_refusal_surfaces_as_a_failure_not_a_shell(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    task = asyncio.create_task(
        local_adb.async_target(hass, entry, normalize_address("panel.local"))
    )
    await _answer_ready(panel)
    await _answer_identity_cmd(panel)
    target = await task
    restore = asyncio.create_task(install_adb.async_restore_vendor_setup(target, None))
    event = (await _receive(panel.client))["event"]
    await panel.answer(
        event["command_id"], "refused", code="failed", result={"code": "args"}
    )
    with pytest.raises(local_commands.LocalCommandRefused) as err:
        await restore
    assert err.value.refusal == "args"
    session = async_get_sessions(hass).get(entry.entry_id)
    assert session is not None and not session.local_adb_commands_disabled
    await panel.nothing_sent()


@pytest.mark.parametrize(
    "offered",
    [
        ["local_adb", "local_adb_commands_v1"],
        ["management", "local_adb_commands_v1"],
    ],
)
async def test_typed_capability_is_stripped_with_local_adb(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    offered: list[str],
) -> None:
    _entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        offered,
    )
    assert "local_adb_commands_v1" not in panel.result["capabilities"]


async def test_typed_capability_is_stripped_when_local_adb_is_not_allowed(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    from unittest.mock import patch

    from custom_components.panel_assistant import transport

    original = transport._accept_hello

    def refuse_local(*args: Any, **kwargs: Any) -> Any:
        kwargs["local_adb_allowed"] = False
        return original(*args, **kwargs)

    with patch.object(transport, "_accept_hello", refuse_local):
        _entry, panel = await _panel(
            hass,
            hass_ws_client,
            hass_read_only_user,
            hass_read_only_access_token,
            ["management", "local_adb", "local_adb_commands_v1"],
        )
    assert "local_adb" not in panel.result["capabilities"]
    assert "local_adb_commands_v1" not in panel.result["capabilities"]


async def test_hardened_typed_command_waits_for_approval_like_a_shell(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.panel_assistant import transport_sessions

    entry, panel = await _panel(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        ["management", "local_adb", "local_adb_commands_v1"],
    )
    original = transport_sessions.async_manage_outcome

    async def short_response(
        hass: Any, session: Any, op: str, wait_seconds: float, **fields: Any
    ) -> Any:
        assert fields["approval_seconds"] == 0.2
        return await original(hass, session, op, 0.02, **fields)

    monkeypatch.setattr(local_adb, "async_manage_outcome", short_response)
    monkeypatch.setattr(local_adb, "LOCAL_ADB_APPROVAL_SECONDS", 0.2, raising=False)
    session = async_get_sessions(hass).get(entry.entry_id)
    commands = local_adb.LocalAdbCommands(hass, entry, session)
    task = asyncio.create_task(commands.run("PA_OBSERVE", ("move",)))
    event = (await _receive(panel.client))["event"]
    assert event["op"] == "adb.cmd"
    await panel.answer(event["command_id"], "pending_approval")
    await asyncio.sleep(0.05)
    assert not task.done(), "a read-only verb still waits for on-panel approval"
    await panel.answer(
        event["command_id"],
        "applied",
        result={"ok": True, "fields": {**IDENTITY, "home": "unknown"}},
    )
    assert (await task)["home"] == "unknown"


async def test_typed_home_without_a_recognised_policy_defers_like_the_shell() -> None:
    """policy=none (no broadcast reply recognised) defers, as the shell did."""
    home = {"policy": "none", "chooser": "deferred", "home": "unknown"}
    commands = FakeCommands({"PA_REPAIR": GRANTED, "PA_HOME": home})
    target = _target(commands)
    chooser = await install_adb._async_repair_app_permissions(
        target.local_shell,  # type: ignore[arg-type]
        target,
        PACKAGE_ID,
        LAUNCH,
    )
    assert chooser == "deferred"


async def test_typed_repair_without_the_app_reports_it_missing_like_the_shell() -> None:
    """``ERR absent`` from PA_REPAIR is the shell route's installed-package-missing."""
    commands = FakeCommands({"PA_REPAIR": local_commands.LocalCommandRefused("absent")})
    target = _target(commands)
    with pytest.raises(InstallAdbError) as err:
        await install_adb._async_repair_app_permissions(
            target.local_shell,  # type: ignore[arg-type]
            target,
            PACKAGE_ID,
            LAUNCH,
        )
    assert err.value.code is InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING


async def test_typed_home_lock_held_elsewhere_defers_like_the_shell() -> None:
    """A held /system HOME lock defers the chooser, as the shell's failed flock did."""
    commands = FakeCommands(
        {"PA_REPAIR": GRANTED, "PA_HOME": local_commands.LocalCommandRefused("busy")}
    )
    target = _target(commands)
    chooser = await install_adb._async_repair_app_permissions(
        target.local_shell,  # type: ignore[arg-type]
        target,
        PACKAGE_ID,
        LAUNCH,
    )
    assert chooser == "deferred"

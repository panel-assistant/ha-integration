"""Tests for bounded, clean-install ADB primitives."""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
    DeviceAuthError,
    InvalidResponseError,
)

from custom_components.panel_assistant import adb_protocol
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
)
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    PACKAGE_ID,
)
from custom_components.panel_assistant.client import PanelAddress
from custom_components.panel_assistant.display_provisioning import DisplaySizing
from custom_components.panel_assistant.install_adb import (
    DefiniteCleanupReason,
    async_apply_display_sizing,
    async_cleanup_staged_apk,
    async_install_staged_apk,
    async_installed_artifact_size,
    async_launch_installed_app,
    async_preflight_install,
    async_restore_display_sizing,
    async_restore_vendor_setup,
    async_stage_apk,
    async_verify_installed_target,
)
from custom_components.panel_assistant.release import InstallDescriptor

from .install_adb_fakes import (
    _ACCESSIBILITY_SERVICES,
    APK_BYTES,
    APK_SHA256,
    JOB_ID,
    NONCES,
    REMOTE_PATH,
    FakeDevice,
    _allow_install,
    _cleanup_output,
    _identity_root_output,
    _install_fakes,
    _notifications_output,
    _permissions_output,
    _preflight_output,
    _remote_output,
    _section,
    _single_output,
    _staged,
    _su_output,
    _write_private_apk,
    descriptor,
    signer,
    target,
)

# The shared fixtures resolve by parameter name, and sibling modules import them
# from here; naming them as exports keeps each parameter from reading as a
# redefinition of the import.
__all__ = ["descriptor", "signer", "target"]


async def test_preflight_admits_positive_rootless_target_with_persistent_signer(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0])])
    _install_fakes(monkeypatch, [fake])

    proof = await async_preflight_install(target, signer, descriptor)

    assert proof.root_mode is AdbRootMode.ROOTLESS
    assert proof.serial == target.serial
    assert fake.connect_kwargs is not None
    assert fake.connect_kwargs["rsa_keys"] == [signer]
    assert fake.connect_kwargs["transport_timeout_s"] == 10.0
    assert fake.connect_kwargs["auth_timeout_s"] == 5.0
    assert fake.connect_kwargs["read_timeout_s"] == 10.0
    assert callable(fake.connect_kwargs["auth_callback"])
    assert fake.closed is True
    assert len(fake.commands) == 1
    assert "command -v su >/dev/null 2>&1" in fake.commands[0]
    assert "echo absent" in fake.commands[0]
    assert "echo abnormal" in fake.commands[0]
    assert "su -c" not in fake.commands[0]
    assert "adb root" not in fake.commands[0]
    assert fake.constructor is not None
    constructor_args, constructor_kwargs = fake.constructor
    assert len(constructor_args) == 1
    transport = constructor_args[0]
    assert isinstance(transport, adb_protocol._BoundedTcpTransportAsync)
    assert transport._host == "192.168.1.23"
    assert transport._port == 5555
    assert constructor_kwargs == {
        "default_transport_timeout_s": 10.0,
        "banner": "ha-paneld-home-assistant",
    }


@pytest.mark.parametrize("dialect", range(5))
async def test_su_preflight_requires_exact_root_inventory_proof(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    dialect: int,
) -> None:
    failures = [
        f"HAPANELD_DELEGATE_BEGIN:{nonce}\nunsupported dialect\n"
        f"HAPANELD_DELEGATE_END:{nonce}:1\n".encode()
        for nonce in NONCES[1 : dialect + 1]
    ]
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], su_lines=["present"], unreadable_base=0),
            *failures,
            _su_output(NONCES[dialect + 1]),
        ]
    )
    _install_fakes(monkeypatch, [fake])
    proof = await async_preflight_install(target, signer, descriptor)
    assert proof.root_mode is AdbRootMode.ROOT_SU
    assert len(fake.commands) == dialect + 2
    assert fake.closed
    assert not fake.pushes
    command = fake.commands[-1]
    prefix = adb_protocol._SU_PREFIXES[dialect]
    payload = adb_protocol._su_inspection_command(NONCES[dialect + 1], clean=True)
    assert f"{prefix} {shlex.quote(payload)};" in command
    assert shlex.split(f"{prefix} {shlex.quote(payload)}")[-1] == payload
    for path in adb_protocol._ROOT_DATA_BASES:
        assert f"ls -1A {path} >/dev/null" in payload
    for path in adb_protocol._RESIDUE_PATHS:
        assert f"[ -e {path} ] || [ -L {path} ]" in payload
    assert all(
        "pm install" not in cmd and "adb root" not in cmd for cmd in fake.commands
    )


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("kind", ["unreadable_base", "residue_index"])
async def test_su_cannot_admit_residue_or_unreadable_inventory(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    index: int,
    kind: str,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], su_lines=["present"]),
            _su_output(NONCES[1], **{kind: index}),
        ]
    )
    _install_fakes(monkeypatch, [fake])
    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)
    expected = (
        InstallAdbErrorCode.TARGET_NOT_CLEAN
        if kind == "residue_index"
        else InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS
    )
    assert caught.value.code is expected
    assert len(fake.commands) == 2
    assert not fake.pushes


@pytest.mark.parametrize(
    "output",
    [
        TimeoutError(),
        b"denied\n",
        _su_output(NONCES[1], uid="2000"),
        _su_output(NONCES[1]).replace(b"\n0\n", b"\nuid=0 noisy\n"),
        _su_output(NONCES[1]).replace(
            b"HAPANELD_SU_BEGIN", b"noise\nHAPANELD_SU_BEGIN"
        ),
        _su_output(NONCES[2]),
    ],
)
async def test_su_timeout_noise_nonroot_or_wrong_nonce_never_admits(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    output: bytes | BaseException,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], su_lines=["present"]), output])
    _install_fakes(monkeypatch, [fake])
    with pytest.raises(InstallAdbError):
        await async_preflight_install(target, signer, descriptor)
    assert len(fake.commands) == 2
    assert fake.closed
    assert not fake.pushes


async def test_existing_package_short_circuits_before_su_invocation(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0],
                su_lines=["present"],
                package_lines=["package:/data/app/base.apk"],
            )
        ]
    )
    _install_fakes(monkeypatch, [fake])
    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)
    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN
    assert fake.commands == [adb_protocol._preflight_command(NONCES[0])]


async def test_su_denial_stops_after_five_bounded_dialects(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], su_lines=["present"]),
            *[
                _single_output("DELEGATE", nonce, ["denied"], 1)
                for nonce in NONCES[1:6]
            ],
        ]
    )
    _install_fakes(monkeypatch, [fake])
    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)
    assert caught.value.code is InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS
    assert len(fake.commands) == 6
    assert all(kwargs["read_timeout_s"] == 3.0 for kwargs in fake.shell_kwargs[1:])
    assert fake.closed


@pytest.mark.parametrize("operation", ["stage", "install", "launch", "cleanup"])
async def test_su_never_wraps_mutation_commands(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    tmp_path: Path,
    operation: str,
) -> None:
    clean = operation in ("stage", "install")
    initial = _preflight_output if clean else _identity_root_output
    outputs = [
        initial(NONCES[0], su_lines=["present"]),
        _su_output(NONCES[1], clean=clean),
    ]
    if operation == "stage":
        outputs.extend(
            [
                _single_output("PATH", NONCES[2], ["absent"], 0),
                _remote_output(NONCES[3]),
            ]
        )
    elif operation == "install":
        outputs.extend(
            [
                _remote_output(NONCES[2]),
                _single_output("INSTALL", NONCES[3], ["Success"], 0),
            ]
        )
    elif operation == "launch":
        outputs.extend(
            [
                _single_output("PACKAGE", NONCES[2], ["package:/data/app/base.apk"], 0),
                _notifications_output(NONCES[3]),
                _permissions_output(NONCES[4]),
                _single_output(
                    "LAUNCH",
                    NONCES[5],
                    [
                        "Starting: Intent { "
                        "cmp=io.panelassistant.android/"
                        "io.github.maxlyth.hapaneld.MainActivity }"
                    ],
                    0,
                ),
            ]
        )
    else:
        outputs.append(_cleanup_output(NONCES[2]))
    fake = FakeDevice(outputs)
    _install_fakes(monkeypatch, [fake])
    if operation == "stage":
        apk = tmp_path / "fixture.apk"
        _write_private_apk(apk)
        result = await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOT_SU,
        )
        assert result == _staged()
        assert len(fake.pushes) == 1
    elif operation == "install":
        assert (
            await async_install_staged_apk(
                target,
                signer,
                descriptor,
                JOB_ID,
                expected_root_mode=AdbRootMode.ROOT_SU,
                before_install=_allow_install,
            )
            is InstallOutcome.INSTALLED
        )
    elif operation == "launch":
        assert (
            await async_launch_installed_app(
                target, signer, descriptor, expected_root_mode=AdbRootMode.ROOT_SU
            )
            is LaunchOutcome.STARTED
        )
    else:
        await async_cleanup_staged_apk(
            target,
            signer,
            _staged(),
            reason=DefiniteCleanupReason.CANCELLED,
            expected_root_mode=AdbRootMode.ROOT_SU,
        )
    assert "HAPANELD_DELEGATE" in fake.commands[1]
    for command in fake.commands[2:]:
        assert "su " not in command
        assert "HAPANELD_DELEGATE" not in command
    assert fake.closed


async def test_su_posture_barrier_reproves_capability_before_package_query(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0], su_lines=["present"]),
            _su_output(NONCES[1], clean=False, uid="2000"),
        ]
    )
    _install_fakes(monkeypatch, [fake])
    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOT_SU,
            package_id=PACKAGE_ID,
        )
    assert caught.value.code is InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS
    assert len(fake.commands) == 2
    assert all("pm path" not in command for command in fake.commands)


async def test_root_adbd_requires_all_exact_residue_paths_absent(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], uid="0")])
    _install_fakes(monkeypatch, [fake])

    proof = await async_preflight_install(target, signer, descriptor)

    assert proof.root_mode is AdbRootMode.ROOT_ADBD
    command = fake.commands[0]
    assert all(path in command for path in adb_protocol._ROOT_DATA_BASES)
    assert all(path in command for path in adb_protocol._RESIDUE_PATHS)


async def test_root_adbd_refuses_an_unreadable_data_inventory(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], uid="0", unreadable_base=1)])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS


@pytest.mark.parametrize(
    "changes",
    [
        {"uid": "2000", "secure": "0"},
        {"uid": "2000", "debuggable": "1"},
        {"uid": "1000"},
    ],
)
async def test_visible_or_ambiguous_root_state_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    changes: dict[str, Any],
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], **changes)])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS


@pytest.mark.parametrize(
    ("su_lines", "su_status"),
    [
        (["abnormal"], 0),
        (["absent"], 2),
        (["absent"], 126),
        (["absent"], 127),
        (["absent"], 129),
        (["absent"], 130),
        (["absent"], 137),
        (["absent"], 255),
    ],
)
async def test_abnormal_su_lookup_can_never_prove_rootless(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    su_lines: list[str],
    su_status: int,
) -> None:
    fake = FakeDevice(
        [_preflight_output(NONCES[0], su_lines=su_lines, su_status=su_status)]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE


@pytest.mark.parametrize(
    "response",
    [
        _preflight_output(NONCES[0], residue_index=2, uid="0"),
        _preflight_output(NONCES[0], residue_index=1),
        _preflight_output(
            NONCES[0],
            package_lines=["package:/data/app/io.github.maxlyth.hapaneld/base.apk"],
        ),
        _preflight_output(
            NONCES[0], retained_lines=["package:io.github.maxlyth.hapaneld"]
        ),
    ],
)
async def test_package_or_root_visible_residue_is_not_clean(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    response: bytes,
) -> None:
    fake = FakeDevice([response])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


@pytest.mark.parametrize(
    ("response", "changed_descriptor", "expected"),
    [
        (
            _preflight_output(NONCES[0], serial="SERIAL-2"),
            None,
            InstallAdbErrorCode.TARGET_CHANGED,
        ),
        (
            _preflight_output(NONCES[0], model="Another Panel"),
            None,
            InstallAdbErrorCode.TARGET_CHANGED,
        ),
        (
            _preflight_output(NONCES[0], abi="armeabi-v7a"),
            None,
            InstallAdbErrorCode.TARGET_CHANGED,
        ),
        (
            _preflight_output(NONCES[0], sdk=33),
            None,
            InstallAdbErrorCode.TARGET_CHANGED,
        ),
        (
            _preflight_output(NONCES[0]),
            {"min_sdk": 35},
            InstallAdbErrorCode.TARGET_INCOMPATIBLE,
        ),
        (
            _preflight_output(NONCES[0]),
            {"supported_abis": ("armeabi-v7a",)},
            InstallAdbErrorCode.INVALID_REQUEST,
        ),
    ],
)
async def test_identity_drift_or_descriptor_incompatibility_blocks_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    response: bytes,
    changed_descriptor: dict[str, Any] | None,
    expected: InstallAdbErrorCode,
) -> None:
    artifact = replace(descriptor, **(changed_descriptor or {}))
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice([response])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            artifact,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is expected
    assert fake.pushes == []


async def test_descriptor_abi_compatibility_is_rechecked_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    x86_target = replace(target, primary_abi="x86_64")
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice([_preflight_output(NONCES[0], abi="x86_64")])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            x86_target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_INCOMPATIBLE
    assert fake.pushes == []


async def test_standalone_preflight_rejects_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], serial="SERIAL-2")])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED


async def test_installed_target_verification_is_fresh_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    assert (
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=PACKAGE_ID,
        )
        is None
    )

    assert fake.connect_kwargs is not None
    assert fake.connect_kwargs["rsa_keys"] == [signer]
    assert fake.closed is True
    assert fake.pushes == []
    commands = "\n".join(fake.commands)
    assert len(fake.commands) == 2
    assert "HAPANELD_POSTURE_BEGIN" in fake.commands[0]
    assert "id -u" in fake.commands[0]
    assert "command -v su" in fake.commands[0]
    assert "pm path io.panelassistant.android" in commands
    assert "pm path io.panelassistant.android" in fake.commands[1]
    assert "pm install" not in commands
    assert "am start" not in commands
    assert "rm -f" not in commands
    assert "chmod" not in commands


async def test_installed_target_verification_refuses_the_old_app_before_contact(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    """Only the app is ever proved installed; the old id is not a target."""
    _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=LEGACY_PACKAGE_ID,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "identity_changes",
    [
        {"serial": "SERIAL-2"},
        {"model": "Another Panel"},
        {"abi": "armeabi-v7a"},
        {"sdk": 33},
    ],
)
async def test_installed_target_verification_rejects_each_identity_axis_drift(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    identity_changes: dict[str, Any],
) -> None:
    fake = FakeDevice([_identity_root_output(NONCES[0], **identity_changes)])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=PACKAGE_ID,
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert len(fake.commands) == 1
    assert fake.pushes == []


@pytest.mark.parametrize(
    ("package_response", "expected"),
    [
        (
            _single_output("PACKAGE", NONCES[1], [], 1),
            InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING,
        ),
        (
            _single_output("PACKAGE", NONCES[1], ["malformed"], 0),
            InstallAdbErrorCode.TARGET_RESPONSE_INVALID,
        ),
        (
            _single_output("PACKAGE", NONCES[1], [], 137),
            InstallAdbErrorCode.TARGET_RESPONSE_INVALID,
        ),
    ],
)
async def test_installed_target_verification_distinguishes_missing_from_malformed(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    package_response: bytes,
    expected: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice([_identity_root_output(NONCES[0]), package_response])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=PACKAGE_ID,
        )

    assert caught.value.code is expected
    assert fake.pushes == []


@pytest.mark.parametrize(
    ("connect_error", "expected"),
    [
        (
            DeviceAuthError("private authorization details"),
            InstallAdbErrorCode.AUTHORIZATION_REQUIRED,
        ),
        (
            AdbConnectionError("private network details"),
            InstallAdbErrorCode.TARGET_UNREACHABLE,
        ),
        (
            InvalidResponseError("private malformed peer details"),
            InstallAdbErrorCode.TARGET_RESPONSE_INVALID,
        ),
    ],
)
async def test_installed_target_verification_connection_errors_are_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    connect_error: BaseException,
    expected: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice([], connect_error=connect_error)
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=PACKAGE_ID,
        )

    assert caught.value.code is expected
    assert str(caught.value) == expected.value
    assert "private" not in str(caught.value)
    assert fake.closed is True


async def test_installed_target_verification_cancellation_closes_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    shell_started = asyncio.Event()

    class CancelledVerifyDevice(FakeDevice):
        async def streaming_shell(
            self, command: str, **kwargs: Any
        ) -> AsyncIterator[bytes]:
            self.commands.append(command)
            self.shell_kwargs.append(kwargs)
            shell_started.set()
            await asyncio.Future()
            yield b"unreachable"

    fake = CancelledVerifyDevice([])
    _install_fakes(monkeypatch, [fake])
    task = asyncio.create_task(
        async_verify_installed_target(
            target,
            signer,
            expected_root_mode=AdbRootMode.ROOTLESS,
            package_id=PACKAGE_ID,
        )
    )
    await shell_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed is True
    assert fake.pushes == []
    assert len(fake.commands) == 1
    assert "pm install" not in fake.commands[0]
    assert "am start" not in fake.commands[0]
    assert "rm -f" not in fake.commands[0]


async def test_installed_target_verification_accepts_matching_root_adbd_posture(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(
                NONCES[0],
                uid="0",
                secure="0",
                debuggable="1",
                su_lines=["present"],
            ),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    await async_verify_installed_target(
        target,
        signer,
        expected_root_mode=AdbRootMode.ROOT_ADBD,
        package_id=PACKAGE_ID,
    )

    assert len(fake.commands) == 2
    assert "HAPANELD_POSTURE_BEGIN" in fake.commands[0]
    assert all(path not in fake.commands[0] for path in adb_protocol._RESIDUE_PATHS)
    assert "pm path io.panelassistant.android" in fake.commands[1]


@pytest.mark.parametrize(
    ("posture", "expected_root_mode", "expected_error"),
    [
        (
            _identity_root_output(NONCES[0]),
            AdbRootMode.ROOT_ADBD,
            InstallAdbErrorCode.ROOT_MODE_CHANGED,
        ),
        (
            _identity_root_output(NONCES[0], su_lines=["abnormal"]),
            AdbRootMode.ROOTLESS,
            InstallAdbErrorCode.TARGET_RESPONSE_INVALID,
        ),
    ],
)
async def test_installed_target_root_drift_or_ambiguity_blocks_package_proof(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    posture: bytes,
    expected_root_mode: AdbRootMode,
    expected_error: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice(
        [
            posture,
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_verify_installed_target(
            target,
            signer,
            expected_root_mode=expected_root_mode,
            package_id=PACKAGE_ID,
        )

    assert caught.value.code is expected_error
    assert len(fake.commands) == 1
    assert "pm path" not in fake.commands[0]
    assert fake.pushes == []


@pytest.mark.parametrize(
    "operation", ["verify", "stage", "install", "launch", "cleanup"]
)
async def test_invalid_expected_root_mode_fails_before_panel_contact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    operation: str,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    device_iterator = _install_fakes(monkeypatch, [])
    invalid_root_mode = "rootless"

    with pytest.raises(InstallAdbError) as caught:
        if operation == "verify":
            await async_verify_installed_target(
                target,
                signer,
                expected_root_mode=invalid_root_mode,  # type: ignore[arg-type]
                package_id=PACKAGE_ID,
            )
        elif operation == "stage":
            await async_stage_apk(
                target,
                signer,
                descriptor,
                JOB_ID,
                apk,
                expected_root_mode=invalid_root_mode,  # type: ignore[arg-type]
            )
        elif operation == "install":
            await async_install_staged_apk(
                target,
                signer,
                descriptor,
                JOB_ID,
                expected_root_mode=invalid_root_mode,  # type: ignore[arg-type]
                before_install=_allow_install,
            )
        elif operation == "launch":
            await async_launch_installed_app(
                target,
                signer,
                descriptor,
                expected_root_mode=invalid_root_mode,  # type: ignore[arg-type]
            )
        else:
            await async_cleanup_staged_apk(
                target,
                signer,
                _staged(),
                DefiniteCleanupReason.CANCELLED,
                expected_root_mode=invalid_root_mode,  # type: ignore[arg-type]
            )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST
    with pytest.raises(StopIteration):
        next(device_iterator)


@pytest.mark.parametrize(
    ("preflight", "expected_error"),
    [
        (
            _preflight_output(NONCES[0], uid="0"),
            InstallAdbErrorCode.ROOT_MODE_CHANGED,
        ),
        (
            _preflight_output(NONCES[0], su_lines=["absent"], su_status=137),
            InstallAdbErrorCode.TARGET_UNREACHABLE,
        ),
    ],
)
async def test_install_root_drift_or_signal_proof_blocks_package_mutation(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    preflight: bytes,
    expected_error: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice(
        [
            preflight,
            _remote_output(NONCES[1]),
            _single_output("INSTALL", NONCES[2], ["Success"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=_allow_install,
        )

    assert caught.value.code is expected_error
    assert len(fake.commands) == 1
    assert "sha256sum" not in fake.commands[0]
    assert "pm install" not in fake.commands[0]


async def test_install_uses_no_replacement_or_grant_flags(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _remote_output(NONCES[1]),
            _single_output("INSTALL", NONCES[2], ["Success"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_install_staged_apk(
        target,
        signer,
        descriptor,
        JOB_ID,
        expected_root_mode=AdbRootMode.ROOTLESS,
        before_install=_allow_install,
    )

    assert outcome is InstallOutcome.INSTALLED
    install_command = fake.commands[-1]
    assert len(fake.commands) == 3
    assert "HAPANELD_PREFLIGHT_BEGIN" in fake.commands[0]
    assert "HAPANELD_ARTIFACT_BEGIN" in fake.commands[1]
    assert "HAPANELD_INSTALL_BEGIN" in fake.commands[2]
    assert f"pm install -R {REMOTE_PATH}" in install_command
    assert " -r" not in install_command
    assert " -g" not in install_command
    assert "--replace" not in install_command
    assert fake.shell_kwargs[-1]["transport_timeout_s"] == 180.0
    assert fake.shell_kwargs[-1]["read_timeout_s"] == 180.0


async def test_an_install_refused_at_the_last_check_sends_no_install(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """Consent re-checked right before the install command can still stop it."""
    fake = FakeDevice([_preflight_output(NONCES[0]), _remote_output(NONCES[1])])
    _install_fakes(monkeypatch, [fake])

    class Revoked(Exception):
        pass

    async def revoked() -> None:
        raise Revoked

    with pytest.raises(Revoked):
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=revoked,
        )

    assert len(fake.commands) == 2
    assert all("pm install" not in command for command in fake.commands)
    assert fake.closed is True


async def test_a_program_run_without_consent_never_offers_the_key(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    """Only an owner-started step may ask the panel to trust Home Assistant.

    Putting back a panel's display sizing runs on a key the panel already
    trusts; a panel that would prompt for it is refused before anything runs.
    """
    # Answers it would read if it went on, so going on is caught by its result.
    fake = FakeDevice(
        [_identity_root_output(NONCES[0]), b"unexpected"], prompt_for_authorization=True
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_restore_display_sizing(target, signer)

    assert caught.value.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED
    assert fake.commands == []
    assert fake.closed is True


async def test_api26_uses_legacy_nonreplacement_default_without_unknown_flag(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    api26_target = replace(target, android_sdk=26)
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], sdk=26),
            _remote_output(NONCES[1]),
            _single_output("INSTALL", NONCES[2], ["Success"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_install_staged_apk(
        api26_target,
        signer,
        descriptor,
        JOB_ID,
        expected_root_mode=AdbRootMode.ROOTLESS,
        before_install=_allow_install,
    )

    assert outcome is InstallOutcome.INSTALLED
    assert f"pm install {REMOTE_PATH}" in fake.commands[-1]
    assert "pm install -R" not in fake.commands[-1]
    assert "pm install -r" not in fake.commands[-1]


async def test_install_revalidates_identity_before_reading_or_mutating_stage(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([_preflight_output(NONCES[0], serial="SERIAL-2")])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=_allow_install,
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert len(fake.commands) == 1
    assert "pm install" not in fake.commands[0]
    assert "sha256sum" not in fake.commands[0]


@pytest.mark.parametrize(
    "install_response",
    [
        AdbConnectionError("peer EOF after mutation"),
        b"Success\n",
        _single_output("INSTALL", NONCES[2], ["unexpected"], 0),
    ],
)
async def test_install_transport_eof_or_malformed_result_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    install_response: bytes | BaseException,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _remote_output(NONCES[1]),
            install_response,
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=_allow_install,
        )

    assert caught.value.code is InstallAdbErrorCode.INSTALL_AMBIGUOUS


async def test_noncanonical_nonzero_install_exit_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _remote_output(NONCES[1]),
            _single_output("INSTALL", NONCES[2], ["Killed"], 137),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=_allow_install,
        )

    assert caught.value.code is InstallAdbErrorCode.INSTALL_AMBIGUOUS


@pytest.mark.parametrize("status", [2, 126, 127, 129, 130, 137, 255])
async def test_canonical_failure_with_abnormal_install_exit_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    status: int,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _remote_output(NONCES[1]),
            _single_output(
                "INSTALL",
                NONCES[2],
                ["Failure [INSTALL_FAILED_INVALID_APK: rejected]"],
                status,
            ),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_install_staged_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            expected_root_mode=AdbRootMode.ROOTLESS,
            before_install=_allow_install,
        )

    assert caught.value.code is InstallAdbErrorCode.INSTALL_AMBIGUOUS


async def test_package_manager_definite_refusal_is_not_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _remote_output(NONCES[1]),
            _single_output("INSTALL", NONCES[2], ["Failure [INSTALL_FAILED]"], 1),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_install_staged_apk(
        target,
        signer,
        descriptor,
        JOB_ID,
        expected_root_mode=AdbRootMode.ROOTLESS,
        before_install=_allow_install,
    )

    assert outcome is InstallOutcome.REFUSED


@pytest.mark.parametrize(
    ("posture", "expected_root_mode", "expected_error"),
    [
        (
            _identity_root_output(NONCES[0]),
            AdbRootMode.ROOT_ADBD,
            InstallAdbErrorCode.ROOT_MODE_CHANGED,
        ),
        (
            _identity_root_output(NONCES[0], su_lines=["absent"], su_status=137),
            AdbRootMode.ROOTLESS,
            InstallAdbErrorCode.TARGET_UNREACHABLE,
        ),
    ],
)
async def test_launch_root_drift_or_signal_proof_blocks_activity_mutation(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    posture: bytes,
    expected_root_mode: AdbRootMode,
    expected_error: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice(
        [
            posture,
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _single_output("LAUNCH", NONCES[2], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_launch_installed_app(
            target,
            signer,
            descriptor,
            expected_root_mode=expected_root_mode,
        )

    assert caught.value.code is expected_error
    assert len(fake.commands) == 1
    assert "pm path" not in fake.commands[0]
    assert "am start" not in fake.commands[0]


async def test_launch_uses_exact_descriptor_package_and_component_once(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _notifications_output(NONCES[2]),
            _permissions_output(NONCES[3]),
            _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target,
        signer,
        descriptor,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )

    assert outcome is LaunchOutcome.STARTED
    assert len(fake.commands) == 5
    assert "HAPANELD_POSTURE_BEGIN" in fake.commands[0]
    assert "HAPANELD_PACKAGE_BEGIN" in fake.commands[1]
    assert "HAPANELD_NOTIFICATIONS_BEGIN" in fake.commands[2]
    assert "HAPANELD_PERMISSIONS_BEGIN" in fake.commands[3]
    assert "HAPANELD_LAUNCH_BEGIN" in fake.commands[4]
    assert fake.commands.count(fake.commands[-1]) == 1
    assert (
        "am start -W -n "
        "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity "
        "-p io.panelassistant.android"
    ) in fake.commands[-1]


async def test_launch_returns_refused_only_for_ordinary_am_start_failure(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _notifications_output(NONCES[2]),
            _permissions_output(NONCES[3]),
            _single_output("LAUNCH", NONCES[4], ["Error: refused"], 1),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target,
        signer,
        descriptor,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )

    assert outcome is LaunchOutcome.REFUSED


# A stand-in for the panel's `settings` and `appops` commands, kept as files so a
# test can read what the real grant program left behind. `refuse.<OP or key>`
# makes a write accept the command and change nothing, as a locked-down ROM does;
# `race.<key>` is another app's write that lands just after the first read.
@pytest.mark.parametrize("status", [2, 126, 127, 130, 137, 255])
async def test_abnormal_launch_exit_is_ambiguous_after_start_attempt(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    status: int,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
            ),
            _notifications_output(NONCES[2]),
            _permissions_output(NONCES[3]),
            _single_output("LAUNCH", NONCES[4], ["abnormal"], status),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_launch_installed_app(
            target,
            signer,
            descriptor,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.LAUNCH_AMBIGUOUS


@pytest.mark.parametrize(
    ("posture", "expected_root_mode", "expected_error"),
    [
        (
            _identity_root_output(NONCES[0], uid="0", secure="0", debuggable="1"),
            AdbRootMode.ROOTLESS,
            InstallAdbErrorCode.ROOT_MODE_CHANGED,
        ),
        (
            _identity_root_output(NONCES[0], su_lines=["abnormal"]),
            AdbRootMode.ROOTLESS,
            InstallAdbErrorCode.TARGET_UNREACHABLE,
        ),
    ],
)
async def test_cleanup_root_drift_or_ambiguity_blocks_remote_delete(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    posture: bytes,
    expected_root_mode: AdbRootMode,
    expected_error: InstallAdbErrorCode,
) -> None:
    fake = FakeDevice([posture, _cleanup_output(NONCES[1])])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_cleanup_staged_apk(
            target,
            signer,
            _staged(),
            DefiniteCleanupReason.INSTALL_SUCCEEDED,
            expected_root_mode=expected_root_mode,
        )

    assert caught.value.code is expected_error
    assert len(fake.commands) == 1
    assert "rm -f" not in fake.commands[0]


async def test_cleanup_wrong_identity_never_reaches_rm(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0], serial="OTHER-SERIAL"),
            _cleanup_output(NONCES[1]),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_cleanup_staged_apk(
            target,
            signer,
            _staged(),
            DefiniteCleanupReason.INSTALL_SUCCEEDED,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert len(fake.commands) == 1
    assert "rm -f" not in fake.commands[0]


async def test_cleanup_deletes_only_exact_job_path_after_definite_outcome(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice([_identity_root_output(NONCES[0]), _cleanup_output(NONCES[1])])
    _install_fakes(monkeypatch, [fake])

    await async_cleanup_staged_apk(
        target,
        signer,
        _staged(),
        DefiniteCleanupReason.INSTALL_REFUSED,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )

    cleanup_command = fake.commands[-1]
    assert len(fake.commands) == 2
    assert "HAPANELD_POSTURE_BEGIN" in fake.commands[0]
    assert "HAPANELD_CLEANUP_BEGIN" in fake.commands[1]
    assert cleanup_command.count(REMOTE_PATH) == 3
    assert f"rm -f {REMOTE_PATH}" in cleanup_command
    assert "/data/local/tmp" in cleanup_command
    assert "*" not in cleanup_command


@pytest.mark.parametrize(
    "job_id",
    [
        "../escape",
        "a" * 31,
        "A" * 32,
        "a" * 31 + ";",
        "a" * 32 + " --grant-all",
    ],
)
async def test_invalid_job_id_cannot_reach_shell_or_filesync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    job_id: str,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            job_id,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


async def test_non_private_or_unpinned_target_fails_before_connection(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(
            replace(target, address=PanelAddress(host="panel.example", port=8888)),
            signer,
            descriptor,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


async def test_cleanup_requires_a_typed_definite_outcome(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_cleanup_staged_apk(
            target,
            signer,
            _staged(),
            "install_succeeded",  # type: ignore[arg-type]
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


async def test_cleanup_rejects_a_forged_or_nonowned_staging_receipt(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    _install_fakes(monkeypatch, [])
    forged = replace(_staged(), remote_path="/data/local/tmp/foreign.apk")

    with pytest.raises(InstallAdbError) as caught:
        await async_cleanup_staged_apk(
            target,
            signer,
            forged,
            DefiniteCleanupReason.CANCELLED,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


async def test_forged_descriptor_command_fields_fail_before_connection(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    _install_fakes(monkeypatch, [])
    forged = replace(
        descriptor,
        launch_component="io.github.maxlyth.hapaneld/.MainActivity; id",
    )

    with pytest.raises(InstallAdbError) as caught:
        await async_launch_installed_app(
            target,
            signer,
            forged,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.INVALID_REQUEST


async def test_each_phase_gets_a_new_connection_and_same_signer(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    first = FakeDevice([_preflight_output(NONCES[0])])
    second = FakeDevice([_preflight_output(NONCES[1])])
    _install_fakes(monkeypatch, [first, second])

    await async_preflight_install(target, signer, descriptor)
    await async_preflight_install(target, signer, descriptor)

    assert first is not second
    assert first.connect_kwargs is not None
    assert second.connect_kwargs is not None
    assert first.connect_kwargs["rsa_keys"] == [signer]
    assert second.connect_kwargs["rsa_keys"] == [signer]
    assert first.closed is True
    assert second.closed is True


async def test_authorization_error_is_stable_and_contains_no_peer_text(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice([], connect_error=DeviceAuthError("secret peer details"))
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED
    assert str(caught.value) == "authorization_required"
    assert "secret" not in str(caught.value)
    assert fake.closed is True


async def test_adb_shell_public_key_prompt_timeout_means_authorization_required(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    fake = FakeDevice(
        [],
        connect_error=TimeoutError("panel approval did not arrive"),
        prompt_for_authorization=True,
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)

    assert caught.value.code is InstallAdbErrorCode.AUTHORIZATION_REQUIRED
    assert str(caught.value) == "authorization_required"
    assert "approval" not in str(caught.value)
    assert fake.closed is True


async def test_cancellation_during_connect_drains_close_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    connect_started = asyncio.Event()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class CancelledConnectDevice(FakeDevice):
        async def connect(self, **kwargs: Any) -> bool:
            self.connect_kwargs = kwargs
            connect_started.set()
            await asyncio.Future()
            return True

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()
            self.closed = True

    fake = CancelledConnectDevice([])
    _install_fakes(monkeypatch, [fake])
    task = asyncio.create_task(async_preflight_install(target, signer, descriptor))
    await connect_started.wait()

    task.cancel()
    await close_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_close.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed is True


@pytest.fixture
def successor_descriptor(descriptor: InstallDescriptor) -> InstallDescriptor:
    """The release of a build whose classes are still in the old namespace.

    The launch component is fully qualified: the classes stay in the old
    namespace, which does not move with the application id, so the app's
    `<id>/.MainActivity` shorthand would name a class that does not exist.
    """
    return replace(
        descriptor,
        package_id="io.panelassistant.android",
        launch_component=(
            "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
        ),
    )


_LEGACY_PATH = "package:/data/app/io.github.maxlyth.hapaneld-1/base.apk"
_SUCCESSOR_PATH = "package:/data/app/io.panelassistant.android-1/base.apk"


async def test_a_clean_panel_admits_the_app(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
) -> None:
    """Neither app id present is a clean target."""
    fake = FakeDevice([_preflight_output(NONCES[0])])
    _install_fakes(monkeypatch, [fake])

    preflight = await async_preflight_install(target, signer, successor_descriptor)

    assert preflight.target_installed is False
    assert preflight.root_mode is AdbRootMode.ROOTLESS


@pytest.mark.parametrize(
    ("package_lines", "retained_lines"),
    [
        ([_LEGACY_PATH], ["package:io.github.maxlyth.hapaneld"]),
        ([], ["package:io.github.maxlyth.hapaneld"]),
    ],
    ids=["installed", "retained"],
)
async def test_the_old_package_alone_refuses_the_new_app_beside_it(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
    package_lines: list[str],
    retained_lines: list[str],
) -> None:
    """A panel still holding the old app is never given the new app beside it."""
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0], package_lines=package_lines, retained_lines=retained_lines
            )
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, successor_descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN
    # Nothing is done to the old package here: this observation is read-only.
    assert fake.commands == [adb_protocol._preflight_command(NONCES[0])]


async def test_both_packages_present_is_not_a_clean_target(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
) -> None:
    """A panel holding both apps is an existing panel; nothing installs onto it."""
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0],
                package_lines=[_LEGACY_PATH],
                successor_package_lines=[_SUCCESSOR_PATH],
            )
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, successor_descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


async def test_the_new_package_alone_is_not_a_clean_target(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
) -> None:
    """A panel with the app is an installed panel, not a fresh-install candidate."""
    fake = FakeDevice(
        [_preflight_output(NONCES[0], successor_package_lines=[_SUCCESSOR_PATH])]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, successor_descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


@pytest.mark.parametrize("residue_index", [0, 1, 2])
async def test_root_still_refuses_old_data_with_no_old_package(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
    residue_index: int,
) -> None:
    """The old app's data with no app is residue like any other."""
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0], su_lines=["present"], residue_index=residue_index
            )
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, successor_descriptor)

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


async def test_each_identity_is_launched_by_its_own_exact_component(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    successor_descriptor: InstallDescriptor,
) -> None:
    """The `/.Class` shorthand names nothing under the new application id."""
    for installed, component in (
        (
            successor_descriptor,
            "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity",
        ),
    ):
        fake = FakeDevice(
            [
                _identity_root_output(NONCES[0]),
                _single_output(
                    "PACKAGE", NONCES[1], ["package:/data/app/ha-paneld/base.apk"], 0
                ),
                _notifications_output(NONCES[2]),
                _permissions_output(NONCES[3], installed.package_id),
                _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
            ]
        )
        _install_fakes(monkeypatch, [fake])

        outcome = await async_launch_installed_app(
            target,
            signer,
            installed,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

        assert outcome is LaunchOutcome.STARTED
        assert (
            f"am start -W -n {component} -p {installed.package_id}"
            in (fake.commands[-1])
        )
        assert f"pm path {installed.package_id}" in fake.commands[-4]
        assert (
            f"pm grant {installed.package_id} android.permission.POST_NOTIFICATIONS"
            in fake.commands[-3]
        )
        assert _ACCESSIBILITY_SERVICES[installed.package_id] in fake.commands[-2]


_INSTALLED_APK_PATH = "/data/app/~~aBc==/io.panelassistant.android-xY9==/base.apk"


def _installed_artifact_output(
    nonce: str,
    *,
    path: str = _INSTALLED_APK_PATH,
    size: int = len(APK_BYTES),
    sha256: str = APK_SHA256,
    mode: str = "81a4",
) -> bytes:
    """The read-only observation of an installed application's own APK."""
    prefix = "ARTIFACT"
    lines = [f"HAPANELD_{prefix}_BEGIN:{nonce}"]
    lines.extend(_section(prefix, "MODE", nonce, [mode], 0))
    lines.extend(_section(prefix, "SIZE", nonce, [str(size)], 0))
    lines.extend(_section(prefix, "SHA", nonce, [f"{sha256}  {path}"], 0))
    lines.append(f"HAPANELD_{prefix}_END:{nonce}")
    return ("\n".join(lines) + "\n").encode()


def _installed_reads(**changes: Any) -> list[bytes]:
    return [
        _identity_root_output(NONCES[0]),
        _single_output("PACKAGE", NONCES[1], [f"package:{_INSTALLED_APK_PATH}"], 0),
        _installed_artifact_output(NONCES[2], **changes),
    ]


async def test_an_installed_apk_is_measured_and_hashed_without_being_touched(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """Byte identity is what says a panel already runs this artifact."""
    fake = FakeDevice(_installed_reads())
    _install_fakes(monkeypatch, [fake])

    size = await async_installed_artifact_size(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert size == len(APK_BYTES)
    assert len(fake.commands) == 3
    assert "pm path io.panelassistant.android" in fake.commands[1]
    assert _INSTALLED_APK_PATH in fake.commands[2]
    # An installed application's APK belongs to the system: it is read, never
    # prepared, so nothing in this observation writes to the panel.
    for command in fake.commands:
        for mutation in ("chmod", "rm ", "pm install", "am start", "mv "):
            assert mutation not in command


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "b" * 64},
        {"size": len(APK_BYTES) + 1},
        {"mode": "41ed"},
    ],
    ids=["other_digest", "other_size", "not_a_regular_file"],
)
async def test_another_build_of_the_same_app_is_never_reported_as_this_one(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    changes: dict[str, Any],
) -> None:
    """Only the exact bytes count; a different build is not this artifact."""
    _install_fakes(monkeypatch, [FakeDevice(_installed_reads(**changes))])

    assert (
        await async_installed_artifact_size(
            target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
        )
        is None
    )


async def test_an_absent_package_reports_nothing_installed(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """`pm path` answers nothing, and that is not an error."""
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output("PACKAGE", NONCES[1], [], 1),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    assert (
        await async_installed_artifact_size(
            target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
        )
        is None
    )
    assert len(fake.commands) == 2


@pytest.mark.parametrize(
    "path",
    [
        "/data/local/tmp/base.apk",
        "/data/app/../../etc/base.apk",
        "/data/app//base.apk",
        "/data/app/pkg/base.apk; rm -rf /",
        "/data/app/pkg/split_config.apk",
    ],
)
async def test_a_package_path_outside_an_installed_application_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    path: str,
) -> None:
    """The package manager's answer never becomes shell syntax or a stray read."""
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output("PACKAGE", NONCES[1], [f"package:{path}"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_installed_artifact_size(
            target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_RESPONSE_INVALID
    assert len(fake.commands) == 2


async def test_a_panel_holding_the_target_is_reported_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """The same reading refuses by default and reports on request.

    An installed application owns its own data, so its residue is expected
    exactly where the application itself was seen.
    """
    response = _preflight_output(
        NONCES[0],
        successor_package_lines=[f"package:{_INSTALLED_APK_PATH}"],
        residue_index=len(adb_protocol._ROOT_DATA_BASES),
    )
    _install_fakes(monkeypatch, [FakeDevice([response])])
    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(target, signer, descriptor)
    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN

    _install_fakes(monkeypatch, [FakeDevice([response])])
    observed = await async_preflight_install(
        target, signer, descriptor, admit_installed_target=True
    )

    assert observed.target_installed is True


@pytest.mark.parametrize(
    "changes",
    [
        {"package_lines": ["package:/data/app/io.github.maxlyth.hapaneld/base.apk"]},
        {"retained_lines": ["package:io.github.maxlyth.hapaneld"]},
        {"residue_index": 0},
    ],
    ids=["other_package", "other_package_retained", "other_packages_data"],
)
async def test_anything_beside_the_installed_target_is_still_unclean(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    changes: dict[str, Any],
) -> None:
    """Admitting an installed target admits nothing else on the panel."""
    _install_fakes(
        monkeypatch,
        [
            FakeDevice(
                [
                    _preflight_output(
                        NONCES[0],
                        successor_package_lines=[f"package:{_INSTALLED_APK_PATH}"],
                        **changes,
                    )
                ]
            )
        ],
    )

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(
            target, signer, descriptor, admit_installed_target=True
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


async def test_data_with_no_application_to_own_it_is_never_an_installed_target(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """Leftover data is an unclean panel, however the caller asks."""
    _install_fakes(
        monkeypatch,
        [
            FakeDevice(
                [
                    _preflight_output(
                        NONCES[0], residue_index=len(adb_protocol._ROOT_DATA_BASES)
                    )
                ]
            )
        ],
    )

    with pytest.raises(InstallAdbError) as caught:
        await async_preflight_install(
            target, signer, descriptor, admit_installed_target=True
        )

    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN


_HAND_BACKS = {
    "vendor_restore": lambda target, signer: async_restore_vendor_setup(target, signer),
    "display_restore": lambda target, signer: async_restore_display_sizing(
        target, signer
    ),
    "display_sizing": lambda target, signer: async_apply_display_sizing(
        target, signer, DisplaySizing(density=240, font_scale=1.0)
    ),
}


@pytest.mark.parametrize("hand_back", list(_HAND_BACKS))
async def test_a_hand_back_program_on_another_device_is_never_sent(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    hand_back: str,
) -> None:
    fake = FakeDevice([_identity_root_output(NONCES[0], serial="SERIAL-2")])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await _HAND_BACKS[hand_back](target, signer)

    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert len(fake.commands) == 1


async def test_a_display_restore_answer_in_no_known_shape_is_an_unreachable_panel(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
) -> None:
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output("DISPLAY_RESTORE", NONCES[1], ["maybe"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_restore_display_sizing(target, signer)

    assert caught.value.code is InstallAdbErrorCode.TARGET_UNREACHABLE

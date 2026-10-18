"""In-place update route: replacing an installed app with a newer signed build.

Covers ``async_update_installed_apk`` end to end against the fake panel: it
re-checks the installed identity before pushing, refuses a missing package,
retained data alone, bad local bytes, the old app beside it or an occupied
staging path, revalidates identity and root mode after the transfer, cleans a
bad or finished stage, and classifies package-manager and transport outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import AdbConnectionError

from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
)
from custom_components.panel_assistant.install_adb import async_update_installed_apk
from custom_components.panel_assistant.release import InstallDescriptor

from .install_adb_fakes import (
    NONCES,
    REMOTE_PATH,
    FakeDevice,
    _allow_install,
    _cleanup_output,
    _identity_root_output,
    _install_fakes,
    _install_update_fake,
    _installed_package_output,
    _preflight_output,
    _remote_output,
    _single_output,
    _write_private_apk,
    descriptor,
    signer,
    target,
)

# The shared fixtures resolve by parameter name; naming them as exports keeps
# each parameter from reading as a redefinition of the import.
__all__ = ["descriptor", "signer", "target"]


async def test_in_place_update_rechecks_installed_identity_and_cleans_its_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
            _remote_output(NONCES[3]),
            _preflight_output(NONCES[4], **installed),
            _installed_package_output(NONCES[5]),
            _remote_output(NONCES[6]),
            _single_output("INSTALL", NONCES[7], ["Success"], 0),
            _cleanup_output(NONCES[8]),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    outcome = await async_update_installed_apk(
        target, signer, descriptor, apk, before_install=_allow_install
    )

    assert outcome is InstallOutcome.INSTALLED
    assert len(fake.pushes) == 1
    assert fake.pushes[0][0][1] == REMOTE_PATH
    assert "pm install -r " + REMOTE_PATH in fake.commands[-2]
    assert " -d" not in fake.commands[-2]
    assert " -g" not in fake.commands[-2]
    assert "rm -f " + REMOTE_PATH in fake.commands[-1]
    assert fake.closed


async def test_in_place_update_refuses_missing_package_before_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [_preflight_output(NONCES[0]), _single_output("PACKAGE", NONCES[1], [], 1)]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING
    assert fake.pushes == []
    assert all("pm install" not in command for command in fake.commands)


async def test_in_place_update_refuses_only_retained_data_before_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0], successor_retained_lines=[f"package:{descriptor.package_id}"]
            ),
            _single_output("PACKAGE", NONCES[1], [], 1),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING
    assert fake.pushes == []


async def test_in_place_update_refuses_bad_local_bytes_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk, b"tampered")
    _install_fakes(monkeypatch, [])
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


async def test_in_place_update_refuses_a_second_accepted_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0],
                retained_lines=["package:io.github.maxlyth.hapaneld"],
                successor_retained_lines=[f"package:{descriptor.package_id}"],
            )
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.TARGET_NOT_CLEAN
    assert fake.pushes == []


async def test_in_place_update_revalidates_identity_after_transfer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
            _remote_output(NONCES[3]),
            _preflight_output(NONCES[4], serial="SERIAL-2", **installed),
            _identity_root_output(NONCES[5], serial="SERIAL-2"),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert all("pm install" not in command for command in fake.commands)
    assert all("rm -f" not in command for command in fake.commands)


async def test_in_place_update_refuses_a_package_removed_during_transfer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
            _remote_output(NONCES[3]),
            _preflight_output(NONCES[4]),
            _single_output("PACKAGE", NONCES[5], [], 1),
            _identity_root_output(NONCES[6]),
            _cleanup_output(NONCES[7]),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING
    assert all("pm install" not in command for command in fake.commands)
    assert "rm -f " + REMOTE_PATH in fake.commands[-1]


async def test_in_place_update_refuses_root_mode_change_after_transfer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
            _remote_output(NONCES[3]),
            _preflight_output(NONCES[4], uid="0", **installed),
            _identity_root_output(NONCES[5], uid="0"),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.ROOT_MODE_CHANGED
    assert all("pm install" not in command for command in fake.commands)


async def test_in_place_update_cleans_bad_stage_before_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0], **installed),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["absent"], 0),
            _remote_output(NONCES[3], sha256="0" * 64),
            _identity_root_output(NONCES[4]),
            _cleanup_output(NONCES[5]),
        ]
    )
    _install_update_fake(monkeypatch, fake)
    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )
    assert caught.value.code is InstallAdbErrorCode.STAGE_VERIFICATION_FAILED
    assert all("pm install" not in command for command in fake.commands)
    assert "rm -f " + REMOTE_PATH in fake.commands[-1]


@pytest.mark.parametrize(
    ("install_response", "expected"),
    [
        (
            _single_output(
                "INSTALL",
                NONCES[7],
                ["Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signer]"],
                1,
            ),
            InstallOutcome.REFUSED,
        ),
        (
            _single_output(
                "INSTALL",
                NONCES[7],
                ["Failure [INSTALL_FAILED_VERSION_DOWNGRADE: version too low]"],
                1,
            ),
            InstallOutcome.REFUSED,
        ),
        (
            AdbConnectionError("peer EOF after install"),
            InstallAdbErrorCode.INSTALL_AMBIGUOUS,
        ),
    ],
)
async def test_in_place_update_classifies_package_manager_and_transport_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    install_response: bytes | BaseException,
    expected: InstallOutcome | InstallAdbErrorCode,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    installed = {"successor_retained_lines": [f"package:{descriptor.package_id}"]}
    outputs: list[bytes | BaseException] = [
        _preflight_output(NONCES[0], **installed),
        _installed_package_output(NONCES[1]),
        _single_output("PATH", NONCES[2], ["absent"], 0),
        _remote_output(NONCES[3]),
        _preflight_output(NONCES[4], **installed),
        _installed_package_output(NONCES[5]),
        _remote_output(NONCES[6]),
        install_response,
    ]
    if expected is InstallOutcome.REFUSED:
        outputs.append(_cleanup_output(NONCES[8]))
    fake = FakeDevice(outputs)
    _install_update_fake(monkeypatch, fake)

    if isinstance(expected, InstallAdbErrorCode):
        with pytest.raises(InstallAdbError) as caught:
            await async_update_installed_apk(
                target, signer, descriptor, apk, before_install=_allow_install
            )
        assert caught.value.code is expected
        assert all("rm -f" not in command for command in fake.commands)
    else:
        assert (
            await async_update_installed_apk(
                target, signer, descriptor, apk, before_install=_allow_install
            )
            is expected
        )
        assert "rm -f " + REMOTE_PATH in fake.commands[-1]


async def test_in_place_update_refuses_an_occupied_staging_path_before_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "release.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(
                NONCES[0], successor_retained_lines=[f"package:{descriptor.package_id}"]
            ),
            _installed_package_output(NONCES[1]),
            _single_output("PATH", NONCES[2], ["present"], 0),
            # A panel that pushed over the file would read on into these.
            AdbConnectionError("peer EOF"),
            AdbConnectionError("peer EOF"),
        ]
    )
    _install_update_fake(monkeypatch, fake)

    with pytest.raises(InstallAdbError) as caught:
        await async_update_installed_apk(
            target, signer, descriptor, apk, before_install=_allow_install
        )

    assert caught.value.code is InstallAdbErrorCode.STAGING_PATH_OCCUPIED
    assert fake.pushes == []
    assert all("rm -f" not in command for command in fake.commands)

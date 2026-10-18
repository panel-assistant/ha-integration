"""The clean-install stage: local APK custody, the staging slot and the copy."""

from __future__ import annotations

import asyncio
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from time import time
from typing import Any

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
)

from custom_components.panel_assistant import install_adb
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.install_adb import (
    async_stage_apk,
)
from custom_components.panel_assistant.release import InstallDescriptor

from .install_adb_fakes import (
    APK_BYTES,
    JOB_ID,
    NONCES,
    REMOTE_PATH,
    FakeDevice,
    _cleanup_output,
    _identity_root_output,
    _install_fakes,
    _preflight_output,
    _remote_output,
    _single_output,
    _write_private_apk,
    descriptor,
    signer,
    target,
)

# The shared fixtures resolve by parameter name, and sibling modules import them
# from here; naming them as exports keeps each parameter from reading as a
# redefinition of the import.
__all__ = ["descriptor", "signer", "target"]


@pytest.mark.parametrize(
    ("preflight", "expected_root_mode", "expected_error"),
    [
        (
            _preflight_output(NONCES[0]),
            AdbRootMode.ROOT_ADBD,
            InstallAdbErrorCode.ROOT_MODE_CHANGED,
        ),
        (
            _preflight_output(NONCES[0], su_lines=["abnormal"]),
            AdbRootMode.ROOTLESS,
            InstallAdbErrorCode.TARGET_UNREACHABLE,
        ),
    ],
)
async def test_stage_root_drift_or_ambiguity_blocks_filesync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    preflight: bytes,
    expected_root_mode: AdbRootMode,
    expected_error: InstallAdbErrorCode,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            preflight,
            _single_output("PATH", NONCES[1], ["absent"], 0),
            _remote_output(NONCES[2]),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=expected_root_mode,
        )

    assert caught.value.code is expected_error
    assert fake.pushes == []
    assert len(fake.commands) == 1
    assert "HAPANELD_PREFLIGHT_BEGIN" in fake.commands[0]


@pytest.mark.parametrize("maxdata", [4095, 1024 * 1024 + 1, True, "65536"])
async def test_peer_maxdata_attack_is_rejected_before_filesync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    maxdata: object,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["absent"], 0),
            _remote_output(NONCES[2]),
        ],
        maxdata=maxdata,
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.FILESYNC_UNSAFE
    assert fake.pushes == []


async def test_stage_pushes_fixed_regular_0644_path_and_verifies_exact_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "name with shell ; metacharacters.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["absent"], 0),
            _remote_output(NONCES[2]),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    pushed_after = int(time())
    staged = await async_stage_apk(
        target,
        signer,
        descriptor,
        JOB_ID,
        apk,
        expected_root_mode=AdbRootMode.ROOTLESS,
    )
    assert staged.remote_path == REMOTE_PATH
    assert len(fake.pushes) == 1
    args, kwargs = fake.pushes[0]
    assert args[1] == REMOTE_PATH
    assert args[0].startswith("/proc/self/fd/")
    assert kwargs["st_mode"] == 0o100644
    # The push dates the staged file to the copy, not to the epoch: a constant
    # mtime is wrong on the wire and defeats a consumer that reads it.
    assert kwargs["mtime"] >= pushed_after
    assert kwargs["mtime"] <= int(time()) + 1
    assert datetime.fromtimestamp(kwargs["mtime"], UTC).year >= 2026
    assert str(apk) not in "\n".join(fake.commands)
    assert len(fake.commands) == 3
    assert "HAPANELD_PREFLIGHT_BEGIN" in fake.commands[0]
    assert "HAPANELD_PATH_BEGIN" in fake.commands[1]
    assert "HAPANELD_ARTIFACT_BEGIN" in fake.commands[2]
    legacy_permissions = kwargs["st_mode"] & 0o777
    legacy_permissions |= (legacy_permissions >> 3) & 0o070
    legacy_permissions |= (legacy_permissions >> 3) & 0o007
    assert legacy_permissions == 0o666
    verification_command = fake.commands[-1]
    assert verification_command.index(f"chmod 0644 {REMOTE_PATH}") < (
        verification_command.index(f"stat -c %f {REMOTE_PATH}")
    )


@pytest.mark.parametrize("unsafe_metadata", ["mode", "hardlink"])
async def test_stage_rejects_unsafe_local_apk_metadata_before_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    unsafe_metadata: str,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    if unsafe_metadata == "mode":
        apk.chmod(0o644)
    else:
        os.link(apk, tmp_path / "artifact-hardlink.apk")
    device_iterator = _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    with pytest.raises(StopIteration):
        next(device_iterator)


def test_verified_apk_identity_requires_current_effective_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    status = apk.lstat()
    monkeypatch.setattr(install_adb.os, "geteuid", lambda: status.st_uid + 1)

    with pytest.raises(InstallAdbError) as caught:
        install_adb._verified_apk_identity(status, len(APK_BYTES))

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


def test_verified_apk_identity_requires_a_regular_file(tmp_path: Path) -> None:
    fifo = tmp_path / "artifact.apk"
    os.mkfifo(fifo, mode=0o600)
    status = fifo.lstat()
    assert status.st_nlink == 1
    assert status.st_size == 0

    with pytest.raises(InstallAdbError) as caught:
        install_adb._verified_apk_identity(status, 0)

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


def test_verified_apk_identity_requires_the_descriptor_size(tmp_path: Path) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)

    with pytest.raises(InstallAdbError) as caught:
        install_adb._verified_apk_identity(apk.lstat(), len(APK_BYTES) + 1)

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


def test_verified_apk_identity_requires_private_mode(tmp_path: Path) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    apk.chmod(0o644)

    with pytest.raises(InstallAdbError) as caught:
        install_adb._verified_apk_identity(apk.lstat(), len(APK_BYTES))

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


def test_verified_apk_identity_requires_one_filesystem_link(tmp_path: Path) -> None:
    apk = tmp_path / "artifact.apk"
    hardlink = tmp_path / "artifact-hardlink.apk"
    _write_private_apk(apk)
    os.link(apk, hardlink)
    assert apk.lstat().st_nlink == 2

    with pytest.raises(InstallAdbError) as caught:
        install_adb._verified_apk_identity(apk.lstat(), len(APK_BYTES))

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID


def test_verified_apk_identity_includes_nanosecond_timestamps(tmp_path: Path) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    status = apk.lstat()

    assert install_adb._verified_apk_identity(status, len(APK_BYTES)) == (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_uid,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def test_open_verified_apk_compares_preopen_path_with_open_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    other_apk = tmp_path / "other.apk"
    _write_private_apk(apk)
    _write_private_apk(other_apk)
    original_open = os.open
    original_lstat = os.lstat
    path_stat_calls = 0

    def _open_other(path: Any, flags: int, *args: Any) -> int:
        if Path(path) == apk:
            return original_open(other_apk, flags, *args)
        return original_open(path, flags, *args)

    def _follow_opened_identity(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal path_stat_calls
        if Path(path) == apk:
            path_stat_calls += 1
            if path_stat_calls > 1:
                return original_lstat(other_apk, *args, **kwargs)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(install_adb.os, "open", _open_other)
    monkeypatch.setattr(install_adb.os, "lstat", _follow_opened_identity)

    try:
        file_descriptor = install_adb._open_verified_apk(apk, descriptor)
    except InstallAdbError as caught:
        assert caught.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    else:
        os.close(file_descriptor)
        pytest.fail("mismatched pathname and opened descriptor were accepted")

    assert path_stat_calls == 1


async def test_stage_fifo_swap_at_open_boundary_fails_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    displaced = tmp_path / "artifact-displaced.apk"
    original_open = os.open
    observed_flags: list[int] = []

    def _open_after_fifo_swap(path: Any, flags: int, *args: Any) -> int:
        if Path(path) == apk and not observed_flags:
            observed_flags.append(flags)
            if not flags & os.O_NONBLOCK:
                raise AssertionError("APK open omitted O_NONBLOCK")
            apk.rename(displaced)
            os.mkfifo(apk, mode=0o600)
        return original_open(path, flags, *args)

    monkeypatch.setattr(install_adb.os, "open", _open_after_fifo_swap)
    device_iterator = _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await asyncio.wait_for(
            async_stage_apk(
                target,
                signer,
                descriptor,
                JOB_ID,
                apk,
                expected_root_mode=AdbRootMode.ROOTLESS,
            ),
            timeout=1,
        )

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    assert observed_flags[0] & os.O_NONBLOCK
    assert stat.S_ISFIFO(apk.lstat().st_mode)
    assert displaced.read_bytes() == APK_BYTES
    with pytest.raises(StopIteration):
        next(device_iterator)


def test_open_verified_apk_rejects_post_hash_path_rebind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    replacement = tmp_path / "replacement.apk"
    displaced = tmp_path / "displaced.apk"
    _write_private_apk(apk)
    _write_private_apk(replacement)
    initial_status = apk.lstat()
    original_fstat = os.fstat
    original_read = os.read
    rebound = False

    def _rebind_after_hash(file_descriptor: int, size: int) -> bytes:
        nonlocal rebound
        data = original_read(file_descriptor, size)
        if not data and not rebound:
            apk.rename(displaced)
            replacement.rename(apk)
            rebound = True
        return data

    def _stable_opened_status(file_descriptor: int) -> os.stat_result:
        if rebound:
            return initial_status
        return original_fstat(file_descriptor)

    monkeypatch.setattr(install_adb.os, "read", _rebind_after_hash)
    monkeypatch.setattr(install_adb.os, "fstat", _stable_opened_status)

    try:
        file_descriptor = install_adb._open_verified_apk(apk, descriptor)
    except InstallAdbError as caught:
        assert caught.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    else:
        os.close(file_descriptor)
        pytest.fail("pathname rebound after hashing was accepted")

    assert rebound
    assert apk.lstat().st_ino != initial_status.st_ino


def test_open_verified_apk_rechecks_descriptor_metadata_after_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    initial_status = apk.lstat()
    original_read = os.read
    metadata_changed = False

    def _change_mode_after_hash(file_descriptor: int, size: int) -> bytes:
        nonlocal metadata_changed
        data = original_read(file_descriptor, size)
        if not data and not metadata_changed:
            os.fchmod(file_descriptor, 0o400)
            metadata_changed = True
        return data

    monkeypatch.setattr(install_adb.os, "read", _change_mode_after_hash)
    monkeypatch.setattr(install_adb.os, "lstat", lambda _path: initial_status)

    try:
        file_descriptor = install_adb._open_verified_apk(apk, descriptor)
    except InstallAdbError as caught:
        assert caught.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    else:
        os.close(file_descriptor)
        pytest.fail("opened descriptor metadata change after hashing was accepted")
    finally:
        apk.chmod(0o600)

    assert metadata_changed


async def test_stage_rejects_local_apk_growth_during_bounded_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    original_read = os.read
    grew = False

    def _grow_then_read(file_descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            with apk.open("ab") as stream:
                stream.write(b"x")
            grew = True
        return original_read(file_descriptor, size)

    monkeypatch.setattr(install_adb.os, "read", _grow_then_read)
    device_iterator = _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    assert grew
    with pytest.raises(StopIteration):
        next(device_iterator)


def test_open_verified_apk_rejects_oversize_before_a_second_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    read_calls = 0

    def _oversize_then_fail(_file_descriptor: int, _size: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            return APK_BYTES + b"x"
        raise AssertionError("oversize input was read again")

    monkeypatch.setattr(install_adb.os, "read", _oversize_then_fail)

    with pytest.raises(InstallAdbError) as caught:
        install_adb._open_verified_apk(apk, descriptor)

    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    assert read_calls == 1


async def test_a_slot_file_that_will_not_go_is_refused_before_filesync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """A leftover is removed first; one that stays is refused, never written over."""
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    still_there = _cleanup_output(NONCES[2]).replace(
        b"\nabsent\nHAPANELD_CLEANUP_STATE_END",
        b"\npresent\nHAPANELD_CLEANUP_STATE_END",
    )
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["present"], 0),
            still_there,
            # Answers a copy that must never be made.
            _remote_output(NONCES[3]),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.STAGING_PATH_OCCUPIED
    assert f"rm -f {REMOTE_PATH}" in fake.commands[2]
    assert fake.pushes == []


async def test_interrupted_filesync_is_a_retryable_interrupted_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    """A copy that stops is reported as interrupted even when the panel stays away."""
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["absent"], 0),
        ],
        push_error=AdbConnectionError("EOF during push"),
    )
    unreachable = FakeDevice([], connect_error=AdbConnectionError("no route"))
    _install_fakes(monkeypatch, [fake, unreachable])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.STAGE_INTERRUPTED
    assert len(fake.pushes) == 1
    assert unreachable.connect_kwargs is not None


async def test_local_hash_mismatch_never_connects_or_pushes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk, b"same byte count!!!")
    assert apk.stat().st_size == descriptor.apk_size
    device_iterator = _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    with pytest.raises(StopIteration):
        next(device_iterator)


@pytest.mark.parametrize("path", [Path("bad\0path.apk"), Path("missing.apk")])
async def test_invalid_local_path_is_contained_as_a_stable_artifact_error(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    path: Path,
) -> None:
    _install_fakes(monkeypatch, [])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            path,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    assert str(caught.value) == "local_artifact_invalid"


async def test_local_read_error_is_not_misreported_as_a_target_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    _install_fakes(monkeypatch, [])

    def fail_read(_file_descriptor: int, _size: int) -> bytes:
        raise OSError("local storage failed")

    monkeypatch.setattr(install_adb.os, "read", fail_read)

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID
    assert "storage" not in str(caught.value)


async def test_cancellation_drains_delayed_apk_worker_and_closes_returned_fd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    worker_opened = threading.Event()
    release_worker = threading.Event()
    returned_fds: list[int] = []
    original_open = install_adb._open_verified_apk

    def delayed_open(path: Path, artifact: InstallDescriptor) -> int:
        file_descriptor = original_open(path, artifact)
        returned_fds.append(file_descriptor)
        worker_opened.set()
        release_worker.wait(timeout=5)
        return file_descriptor

    monkeypatch.setattr(install_adb, "_open_verified_apk", delayed_open)
    device_iterator = _install_fakes(monkeypatch, [])
    task = asyncio.create_task(
        async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    )
    assert await asyncio.to_thread(worker_opened.wait, 5)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(returned_fds) == 1
    with pytest.raises(OSError):
        os.fstat(returned_fds[0])
    with pytest.raises(StopIteration):
        next(device_iterator)


async def test_repeated_cancellation_during_device_close_cannot_leak_staged_fd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    push_started = asyncio.Event()
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    pushed_fds: list[int] = []

    class CancelledStageDevice(FakeDevice):
        async def push(self, *args: Any, **kwargs: Any) -> None:
            self.pushes.append((args, kwargs))
            pushed_fds.append(int(str(args[0]).rsplit("/", maxsplit=1)[-1]))
            push_started.set()
            await asyncio.Future()

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()
            self.closed = True

    fake = CancelledStageDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["absent"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])
    task = asyncio.create_task(
        async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    )
    await push_started.wait()
    assert len(pushed_fds) == 1
    os.fstat(pushed_fds[0])

    task.cancel()
    await close_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    os.fstat(pushed_fds[0])
    release_close.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.closed is True
    with pytest.raises(OSError):
        os.fstat(pushed_fds[0])


async def test_cancellation_before_stage_task_starts_opens_no_apk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    opened = False
    original_open = install_adb._open_verified_apk

    def observed_open(path: Path, artifact: InstallDescriptor) -> int:
        nonlocal opened
        opened = True
        return original_open(path, artifact)

    monkeypatch.setattr(install_adb, "_open_verified_apk", observed_open)
    task = asyncio.create_task(
        async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    )
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert opened is False


@pytest.mark.parametrize(
    "remote",
    [
        _remote_output(NONCES[2], size=len(APK_BYTES) - 1),
        _remote_output(NONCES[2], sha256="f" * 64),
        _remote_output(NONCES[2], mode="81ed"),
    ],
    ids=["size", "sha256", "mode"],
)
async def test_remote_size_hash_or_mode_mismatch_never_reports_staged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    remote: bytes,
) -> None:
    apk = tmp_path / "artifact.apk"
    _write_private_apk(apk)
    fake = FakeDevice(
        [
            _preflight_output(NONCES[0]),
            _single_output("PATH", NONCES[1], ["absent"], 0),
            remote,
        ]
    )
    cleanup = FakeDevice([_identity_root_output(NONCES[3]), _cleanup_output(NONCES[4])])
    _install_fakes(monkeypatch, [fake, cleanup])

    with pytest.raises(InstallAdbError) as caught:
        await async_stage_apk(
            target,
            signer,
            descriptor,
            JOB_ID,
            apk,
            expected_root_mode=AdbRootMode.ROOTLESS,
        )
    assert caught.value.code is InstallAdbErrorCode.STAGE_VERIFICATION_FAILED
    assert len(fake.pushes) == 1
    # The wrong bytes are removed over a fresh connection, after the panel's
    # identity is proved again, before reporting.
    assert len(cleanup.commands) == 2
    assert "HAPANELD_POSTURE_BEGIN" in cleanup.commands[0]
    assert f"rm -f {REMOTE_PATH}" in cleanup.commands[1]

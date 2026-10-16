"""Install steps for a clean install or in-place app update over ADB.

This module deliberately does not own orchestration.  Every public operation
opens a fresh authenticated ADB connection (:mod:`adb_protocol`) and either
returns a small, privacy-safe result or raises :class:`InstallAdbError` with a
stable code.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import re
import stat
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from secrets import token_hex
from time import time

from adb_shell.adb_device_async import AdbDeviceAsync
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.exceptions import (
    AdbConnectionError,
    AdbTimeoutError,
    DevicePathInvalidError,
    InvalidChecksumError,
    InvalidCommandError,
    InvalidResponseError,
    InvalidTransportError,
    PushFailedError,
    TcpTimeoutException,
)

from .adb_protocol import (
    _ABI_PATTERN,
    _ADB_EXCEPTIONS,
    _READ_TIMEOUT_SECONDS,
    _RESIDUE_PATHS,
    _RESIDUE_PROBES,
    _ROOT_DATA_BASES,
    _SERIAL_PATTERN,
    _SU_PREFIXES,
    _SU_TIMEOUT_SECONDS,
    _TRANSPORT_TIMEOUT_SECONDS,
    AdbInstallTarget,
    AdbPreflight,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
    _artifact_observation_command,
    _async_close,
    _async_connect,
    _async_shell,
    _async_wait_for_completion,
    _cleanup_command,
    _decode_lines,
    _identity_root_command,
    _install_command,
    _launch_command,
    _MalformedAdbResponse,
    _notification_grant_command,
    _package_command,
    _parse_artifact_observation,
    _parse_cleanup,
    _parse_identity_root,
    _parse_install_outcome,
    _parse_installed_apk_path,
    _parse_launch_outcome,
    _parse_notification_grant,
    _parse_package_present,
    _parse_path_state,
    _parse_permission_grant,
    _parse_preflight,
    _parse_remote_artifact,
    _parse_sections,
    _parse_single_section,
    _parse_status,
    _path_state_command,
    _permission_grant_command,
    _preflight_command,
    _remote_artifact_command,
    _su_attempt_command,
    _UnsafeAdbPacket,
    _update_install_command,
    _validate_filesync_maxdata,
)
from .app_identity import (
    LEGACY_PACKAGE_ID,
    PACKAGE_ID,
    RELEASE_DEVICE_ADMIN_RECEIVER,
)
from .client import PanelAddress
from .display_provisioning import (
    DisplayResponseError,
    DisplayResult,
    DisplaySizing,
    display_restore_command,
    display_sizing_command,
    parse_display_restore,
    parse_display_sizing,
)
from .home_chooser import HOME_QUERY as _HOME_QUERY
from .install_network import is_allowed_install_address
from .release import (
    _MAX_APK_BYTES,
    _SHA256_PATTERN,
    InstallDescriptor,
    install_descriptor_valid,
)
from .vendor_provisioning import (
    UNREAD_SETUP_WARNING,
    VENDOR_HOME_COMMAND,
    ShellySetup,
    VendorResponseError,
    VendorSetup,
    parse_vendor_display_settlement,
    parse_vendor_restore,
    parse_vendor_setup,
    vendor_display_settlement_command,
    vendor_restore_command,
    vendor_setup_command,
)

_LOGGER = logging.getLogger(__name__)


_REMOTE_PREFIX = "/data/local/tmp/ha-paneld-install-"


_INSTALL_READ_TIMEOUT_SECONDS = 30.0


_PREFLIGHT_TIMEOUT_SECONDS = 30.0


_STAGE_TIMEOUT_SECONDS = 180.0


_INSTALL_TIMEOUT_SECONDS = 180.0


_LAUNCH_TIMEOUT_SECONDS = 30.0


_CLEANUP_TIMEOUT_SECONDS = 30.0


_UPDATE_TIMEOUT_SECONDS = (
    _PREFLIGHT_TIMEOUT_SECONDS
    + _STAGE_TIMEOUT_SECONDS
    + _INSTALL_TIMEOUT_SECONDS
    + _CLEANUP_TIMEOUT_SECONDS
)


_REMOTE_MODE = stat.S_IFREG | 0o644


_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$", flags=re.ASCII)


# A vendor build number travels to a public help page, so only a short,
# printable token is kept: Sonoff reports "4.0.12", others a longer build string.
_FIRMWARE_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z ._+-]{0,79}", flags=re.ASCII)


_APK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.apk(?:\.bin)?$", re.ASCII)


class DefiniteCleanupReason(StrEnum):
    """States which allow deletion of the one job-owned staging path."""

    CANCELLED = "cancelled"
    INSTALL_SUCCEEDED = "install_succeeded"
    INSTALL_REFUSED = "install_refused"


@dataclass(frozen=True, slots=True)
class StagedApk:
    """Exact remote artifact verified after a completed FileSync push."""

    job_id: str
    remote_path: str
    apk_size: int
    apk_sha256: str


def _remote_path(job_id: str) -> str:
    if not isinstance(job_id, str) or _JOB_ID_PATTERN.fullmatch(job_id) is None:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    return f"{_REMOTE_PREFIX}{job_id}.apk"


def _validate_target(target: AdbInstallTarget) -> None:
    if (
        not isinstance(target.address, PanelAddress)
        or not isinstance(target.address.host, str)
        or isinstance(target.address.port, bool)
        or not isinstance(target.address.port, int)
        or not 1 <= target.address.port <= 65535
        or not isinstance(target.serial, str)
        or not isinstance(target.model, str)
        or not isinstance(target.primary_abi, str)
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    try:
        address = ipaddress.ip_address(target.address.host)
    except ValueError:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST) from None
    if (
        not is_allowed_install_address(address)
        or _SERIAL_PATTERN.fullmatch(target.serial) is None
        or target.model != target.model.strip()
        or not 1 <= len(target.model) <= 128
        or not target.model.isprintable()
        or _ABI_PATTERN.fullmatch(target.primary_abi) is None
        or isinstance(target.android_sdk, bool)
        or not isinstance(target.android_sdk, int)
        or not 1 <= target.android_sdk <= 100
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _validate_descriptor(descriptor: InstallDescriptor) -> None:
    if (
        not install_descriptor_valid(descriptor)
        or _APK_NAME_PATTERN.fullmatch(descriptor.apk_name) is None
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _validate_request(target: AdbInstallTarget, descriptor: InstallDescriptor) -> None:
    if not isinstance(target, AdbInstallTarget) or not isinstance(
        descriptor, InstallDescriptor
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_descriptor(descriptor)


# POST_NOTIFICATIONS became a runtime permission in Android 13 (API 33).
_NOTIFICATIONS_RUNTIME_SDK = 33

# What a person loses without a grant, by the permission parser's own names.
_GRANT_CONSEQUENCES = {
    "modify system settings": "touch sounds stay unavailable",
    "install packages": "the panel cannot install its own update with a tap",
    "secure settings": "the screen dims instead of switching off",
    "grant readback": "Panel Assistant cannot tell which permissions it lacks",
}


def _require_expected_root_mode(observed: AdbRootMode, expected: AdbRootMode) -> None:
    if observed is not expected:
        raise InstallAdbError(InstallAdbErrorCode.ROOT_MODE_CHANGED)


def _validate_expected_root_mode(expected: AdbRootMode) -> None:
    if not isinstance(expected, AdbRootMode):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)


def _validate_staged_apk(staged: StagedApk) -> str:
    if (
        not isinstance(staged, StagedApk)
        or not isinstance(staged.job_id, str)
        or not isinstance(staged.remote_path, str)
        or isinstance(staged.apk_size, bool)
        or not isinstance(staged.apk_size, int)
        or not isinstance(staged.apk_sha256, str)
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    remote_path = _remote_path(staged.job_id)
    if (
        staged.remote_path != remote_path
        or not 1 <= staged.apk_size <= _MAX_APK_BYTES
        or _SHA256_PATTERN.fullmatch(staged.apk_sha256) is None
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    return remote_path


async def _async_preflight_on_device(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    *,
    admit_installed_target: bool = False,
) -> AdbPreflight:
    nonce = token_hex(16)
    body = await _async_shell(
        device, _preflight_command(nonce), read_timeout=_READ_TIMEOUT_SECONDS
    )
    preflight = _parse_preflight(
        body, nonce, target, descriptor, admit_installed_target=admit_installed_target
    )
    if preflight.root_mode is AdbRootMode.ROOT_SU:
        await _async_prove_su(
            device, admitted=preflight, target_package_id=descriptor.package_id
        )
    return preflight


async def _async_prove_su(
    device: AdbDeviceAsync,
    *,
    admitted: AdbPreflight | None,
    target_package_id: str | None = None,
) -> None:
    """Prove delegated root afresh without elevating any mutation command.

    ``admitted`` is the preflight this root reading has to agree with, or None
    when only root itself is being re-proved. ``target_package_id`` names the
    package that preflight was about, because the data an installed target owns
    is expected exactly where that target was reported.
    """
    clean = admitted is not None
    for prefix in _SU_PREFIXES:
        nonce = token_hex(16)
        async with asyncio.timeout(_SU_TIMEOUT_SECONDS):
            body = await _async_shell(
                device,
                _su_attempt_command(prefix, nonce, clean=clean),
                read_timeout=_SU_TIMEOUT_SECONDS,
                transport_timeout=_SU_TIMEOUT_SECONDS,
            )
        lines = _decode_lines(body)
        if not lines or lines[0] != f"HAPANELD_DELEGATE_BEGIN:{nonce}":
            raise _MalformedAdbResponse
        status = _parse_status(lines[-1], "DELEGATE", nonce)
        if status is None:
            raise _MalformedAdbResponse
        if status != 0:
            continue  # A different vendor dialect may be needed.
        names: tuple[str, ...] = ("UID",)
        if clean:
            names += tuple(f"BASE{i}" for i in range(len(_ROOT_DATA_BASES)))
            names += tuple(f"RESIDUE{i}" for i in range(len(_RESIDUE_PATHS)))
        sections = _parse_sections(
            ("\n".join(lines[1:-1]) + "\n").encode(),
            prefix="SU",
            nonce=nonce,
            names=names,
        )
        if sections["UID"] != (["0"], 0):
            raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)
        if admitted is not None:
            for index in range(len(_ROOT_DATA_BASES)):
                if sections[f"BASE{index}"] != (["readable"], 0):
                    raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)
            # Root can see data directories the unprivileged shell cannot, so
            # this is the authoritative residue reading. It is judged against
            # the same rule.
            for index in range(len(_RESIDUE_PATHS)):
                values, status = sections[f"RESIDUE{index}"]
                if status != 0 or values not in (["absent"], ["present"]):
                    raise _MalformedAdbResponse
                owner = _RESIDUE_PROBES[index][0]
                expected = admitted.target_installed and owner == target_package_id
                if values == ["present"] and not expected:
                    raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
        return
    raise InstallAdbError(InstallAdbErrorCode.ROOT_STATE_AMBIGUOUS)


async def _async_require_identity_root(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    expected_root_mode: AdbRootMode,
) -> None:
    nonce = token_hex(16)
    observed_root_mode = _parse_identity_root(
        await _async_shell(
            device,
            _identity_root_command(nonce),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
        target,
    )
    _require_expected_root_mode(observed_root_mode, expected_root_mode)
    if observed_root_mode is AdbRootMode.ROOT_SU:
        await _async_prove_su(device, admitted=None)


def _verified_apk_identity(
    status: os.stat_result, expected_size: int
) -> tuple[int, int, int, int, int, int, int, int]:
    if (
        not stat.S_ISREG(status.st_mode)
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_uid != os.geteuid()
        or status.st_nlink != 1
        or status.st_size != expected_size
    ):
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_uid,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _open_verified_apk(path: Path, descriptor: InstallDescriptor) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        path_stat = os.lstat(path)
        file_descriptor = os.open(path, flags)
    except OSError, TypeError, ValueError:
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID) from None
    try:
        path_identity = _verified_apk_identity(path_stat, descriptor.apk_size)
        opened_identity = _verified_apk_identity(
            os.fstat(file_descriptor), descriptor.apk_size
        )
        if opened_identity != path_identity:
            raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
        digest = hashlib.sha256()
        actual_size = 0
        while chunk := os.read(file_descriptor, 1024 * 1024):
            actual_size += len(chunk)
            if actual_size > descriptor.apk_size:
                raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
            digest.update(chunk)
        if (
            actual_size != descriptor.apk_size
            or digest.hexdigest() != descriptor.apk_sha256
            or _verified_apk_identity(os.fstat(file_descriptor), descriptor.apk_size)
            != opened_identity
            or _verified_apk_identity(os.lstat(path), descriptor.apk_size)
            != opened_identity
        ):
            raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID)
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        return file_descriptor
    except InstallAdbError:
        os.close(file_descriptor)
        raise
    except OSError, TypeError, ValueError:
        os.close(file_descriptor)
        raise InstallAdbError(InstallAdbErrorCode.LOCAL_ARTIFACT_INVALID) from None
    except BaseException:
        os.close(file_descriptor)
        raise


async def _async_open_verified_apk(path: Path, descriptor: InstallDescriptor) -> int:
    """Retain custody of a raw descriptor across caller cancellation."""
    worker = asyncio.create_task(
        asyncio.to_thread(_open_verified_apk, path, descriptor)
    )
    cancelled = await _async_wait_for_completion(worker)
    if cancelled:
        try:
            file_descriptor = worker.result()
        except BaseException:
            pass
        else:
            with suppress(OSError):
                os.close(file_descriptor)
        raise asyncio.CancelledError
    return worker.result()


async def _async_verify_remote_artifact(
    device: AdbDeviceAsync, remote_path: str, descriptor: InstallDescriptor
) -> None:
    nonce = token_hex(16)
    body = await _async_shell(
        device,
        _remote_artifact_command(nonce, remote_path),
        read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
        transport_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
    )
    try:
        mode, size, sha256 = _parse_remote_artifact(body, nonce, remote_path)
    except _MalformedAdbResponse:
        raise InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED) from None
    if (
        not stat.S_ISREG(mode)
        or stat.S_IMODE(mode) != 0o644
        or size != descriptor.apk_size
        or sha256 != descriptor.apk_sha256
    ):
        raise InstallAdbError(InstallAdbErrorCode.STAGE_VERIFICATION_FAILED)


async def async_preflight_install(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    admit_installed_target: bool = False,
    authorize: bool = True,
) -> AdbPreflight:
    """Re-prove clean admission without mutating the panel.

    `admit_installed_target` reports a panel already holding exactly this
    package as `target_installed` instead of refusing it. The caller then owns
    what happens next, and may not stage or install on that panel.
    """
    _validate_request(target, descriptor)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer, authorize=authorize)
            return await _async_preflight_on_device(
                device,
                target,
                descriptor,
                admit_installed_target=admit_installed_target,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    finally:
        await _async_close(device)


async def async_verify_installed_target(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    *,
    expected_root_mode: AdbRootMode,
    package_id: str,
) -> None:
    """Re-prove the exact target and installed package without mutation."""
    if not isinstance(target, AdbInstallTarget) or package_id != PACKAGE_ID:
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        AdbConnectionError,
        AdbTimeoutError,
        OSError,
        TcpTimeoutException,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    except (
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        DevicePathInvalidError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        InvalidTransportError,
        PushFailedError,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID) from None
    finally:
        await _async_close(device)


async def async_installed_artifact_size(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
    authorize: bool = True,
) -> int | None:
    """Return the installed APK's size when the panel runs exactly these bytes.

    The installed package's own APK is measured and hashed, and compared with
    the signed descriptor. Equal size and digest mean the panel is already
    running the very bytes this job would install, so installing them again
    would change nothing on it. Anything else returns None: a different build
    of the same application is never admitted here, and this observation
    mutates nothing.
    """
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_PREFLIGHT_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer, authorize=authorize)
            await _async_require_identity_root(device, target, expected_root_mode)
            return await _async_installed_artifact_size_on_device(device, descriptor)
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        AdbConnectionError,
        AdbTimeoutError,
        OSError,
        TcpTimeoutException,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    except (
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        DevicePathInvalidError,
        InvalidChecksumError,
        InvalidCommandError,
        InvalidResponseError,
        InvalidTransportError,
        PushFailedError,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_RESPONSE_INVALID) from None
    finally:
        await _async_close(device)


async def _async_installed_artifact_size_on_device(
    device: AdbDeviceAsync, descriptor: InstallDescriptor
) -> int | None:
    """Read exact installed bytes through the caller's verified connection."""
    nonce = token_hex(16)
    path = _parse_installed_apk_path(
        await _async_shell(
            device,
            _package_command(nonce, descriptor.package_id),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
    )
    if path is None:
        return None
    nonce = token_hex(16)
    mode, size, digest = _parse_artifact_observation(
        await _async_shell(
            device,
            _artifact_observation_command(nonce, path, prepare=False),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
        path,
        prepare=False,
    )
    if (
        not stat.S_ISREG(mode)
        or size != descriptor.apk_size
        or digest != descriptor.apk_sha256
    ):
        return None
    return size


async def async_stage_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    job_id: str,
    local_apk: Path,
    *,
    expected_root_mode: AdbRootMode,
) -> StagedApk:
    """Revalidate, push and authenticate one exact job-owned APK."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _remote_path(job_id)
    if not isinstance(local_apk, Path):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    device: AdbDeviceAsync | None = None
    file_descriptor: int | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_STAGE_TIMEOUT_SECONDS):
            file_descriptor = await _async_open_verified_apk(local_apk, descriptor)
            device = await _async_connect(target, signer)
            preflight = await _async_preflight_on_device(device, target, descriptor)
            _require_expected_root_mode(preflight.root_mode, expected_root_mode)
            nonce = token_hex(16)
            occupied = _parse_path_state(
                await _async_shell(
                    device,
                    _path_state_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            if occupied:
                raise InstallAdbError(InstallAdbErrorCode.STAGING_PATH_OCCUPIED)
            _validate_filesync_maxdata(device)
            mutation_started = True
            await device.push(
                f"/proc/self/fd/{file_descriptor}",
                remote_path,
                st_mode=_REMOTE_MODE,
                # The sync protocol carries this as the staged file's
                # modification time in whole seconds. The bytes come from a
                # file descriptor with no meaningful time of its own, so send
                # the time of the copy rather than a constant that dates the
                # file to 1970 and tells a consumer nothing.
                mtime=int(time()),
                transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
                read_timeout_s=_INSTALL_READ_TIMEOUT_SECONDS,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            return StagedApk(
                job_id=job_id,
                remote_path=remote_path,
                apk_size=descriptor.apk_size,
                apk_sha256=descriptor.apk_sha256,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.STAGE_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        try:
            await _async_close(device)
        finally:
            if file_descriptor is not None:
                descriptor_to_close = file_descriptor
                file_descriptor = None
                os.close(descriptor_to_close)


async def async_install_staged_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    job_id: str,
    *,
    expected_root_mode: AdbRootMode,
    before_install: Callable[[], Awaitable[None]],
) -> InstallOutcome:
    """Revalidate and run exactly one non-replacing package installation."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _remote_path(job_id)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_INSTALL_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            preflight = await _async_preflight_on_device(device, target, descriptor)
            _require_expected_root_mode(preflight.root_mode, expected_root_mode)
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            await before_install()
            nonce = token_hex(16)
            mutation_started = True
            body = await _async_shell(
                device,
                _install_command(nonce, remote_path, target.android_sdk),
                read_timeout=_INSTALL_TIMEOUT_SECONDS,
                transport_timeout=_INSTALL_TIMEOUT_SECONDS,
            )
            return _parse_install_outcome(body, nonce)
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


async def async_update_installed_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    local_apk: Path,
    *,
    before_install: Callable[[], Awaitable[None]],
) -> InstallOutcome:
    """Replace one already installed app using a fresh, pinned ADB target.

    This is deliberately separate from clean installation. A verified APK is
    pushed to a random owned path, then both panel identity and exclusive
    ownership of the target package are re-proved before package mutation.
    Android's package manager enforces signer and version checks for ``-r``;
    this integration never supplies its downgrade override.
    """
    _validate_request(target, descriptor)
    if not isinstance(local_apk, Path):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    file_descriptor = await _async_open_verified_apk(local_apk, descriptor)
    remote_path = _remote_path(token_hex(16))
    device: AdbDeviceAsync | None = None
    stage_started = False
    install_started = False
    install_definite = False
    admitted_root_mode: AdbRootMode | None = None
    try:
        async with asyncio.timeout(_UPDATE_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            admitted = await _async_preflight_on_device(
                device, target, descriptor, admit_installed_target=True
            )
            admitted_root_mode = admitted.root_mode
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, descriptor.package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            nonce = token_hex(16)
            occupied = _parse_path_state(
                await _async_shell(
                    device,
                    _path_state_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            if occupied:
                raise InstallAdbError(InstallAdbErrorCode.STAGING_PATH_OCCUPIED)
            _validate_filesync_maxdata(device)
            stage_started = True
            await device.push(
                f"/proc/self/fd/{file_descriptor}",
                remote_path,
                st_mode=_REMOTE_MODE,
                mtime=int(time()),
                transport_timeout_s=_TRANSPORT_TIMEOUT_SECONDS,
                read_timeout_s=_INSTALL_READ_TIMEOUT_SECONDS,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            # The package and identity can change while the file crosses ADB.
            admitted = await _async_preflight_on_device(
                device, target, descriptor, admit_installed_target=True
            )
            assert admitted_root_mode is not None
            _require_expected_root_mode(admitted.root_mode, admitted_root_mode)
            nonce = token_hex(16)
            _parse_package_present(
                await _async_shell(
                    device,
                    _package_command(nonce, descriptor.package_id),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            await _async_verify_remote_artifact(device, remote_path, descriptor)
            await before_install()
            nonce = token_hex(16)
            install_started = True
            outcome = _parse_install_outcome(
                await _async_shell(
                    device,
                    _update_install_command(nonce, remote_path),
                    read_timeout=_INSTALL_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            install_definite = True
            nonce = token_hex(16)
            _parse_cleanup(
                await _async_shell(
                    device,
                    _cleanup_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
            return outcome
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.CLEANUP_AMBIGUOUS
            if install_definite
            else InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if install_started
            else InstallAdbErrorCode.STAGE_AMBIGUOUS
            if stage_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        try:
            # Before package mutation, the random path is ours to remove. Once
            # pm install starts, an unknown outcome may still be reading it.
            if stage_started and not install_started and device is not None:
                try:
                    assert admitted_root_mode is not None
                    async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
                        await _async_require_identity_root(
                            device, target, admitted_root_mode
                        )
                        nonce = token_hex(16)
                        _parse_cleanup(
                            await _async_shell(
                                device,
                                _cleanup_command(nonce, remote_path),
                                read_timeout=_READ_TIMEOUT_SECONDS,
                            ),
                            nonce,
                        )
                except (
                    InstallAdbError,
                    TimeoutError,
                    _MalformedAdbResponse,
                    _UnsafeAdbPacket,
                    *_ADB_EXCEPTIONS,
                ):
                    _LOGGER.warning("Could not clear an interrupted ADB update stage")
            await _async_close(device)
        finally:
            os.close(file_descriptor)


async def _async_repair_app_permissions(
    device: AdbDeviceAsync,
    target: AdbInstallTarget,
    package_id: str,
    launch_component: str,
) -> None:
    """Share observed grant repair between startup and an already-running update."""
    nonce = token_hex(16)
    _parse_package_present(
        await _async_shell(
            device,
            _package_command(nonce, package_id),
            read_timeout=_READ_TIMEOUT_SECONDS,
        ),
        nonce,
    )
    if target.android_sdk >= _NOTIFICATIONS_RUNTIME_SDK:
        # Never a reason not to start: a refusal is reported, and the app
        # still runs without notification visibility.
        nonce = token_hex(16)
        if not _parse_notification_grant(
            await _async_shell(
                device,
                _notification_grant_command(nonce, package_id),
                read_timeout=_READ_TIMEOUT_SECONDS,
            ),
            nonce,
        ):
            _LOGGER.warning(
                "The panel did not grant %s the notification permission. "
                "The app runs without it; allow it on the panel in Android "
                "Settings, under the app's Notifications",
                package_id,
            )
    # The same rule as notifications: a refusal is reported, never fatal.
    nonce = token_hex(16)
    grant = _parse_permission_grant(
        await _async_shell(
            device,
            _permission_grant_command(nonce, package_id, launch_component),
            # Pressing a HOME chooser's Always waits on the panel's UI dump.
            read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
        ),
        nonce,
        launch_component,
    )
    if not grant.home:
        _LOGGER.warning(
            "The panel did not make %s its home screen (%s). It still runs; "
            "choose it on the panel when Android asks which app to use for Home",
            package_id,
            grant.chooser or "unread",
        )
    if grant.vendor_setup:
        await _async_vendor_setup(device)
    if not grant.granted:
        _LOGGER.warning(
            "The panel did not grant %s every permission it needs "
            "(missing: %s; panel %s). Until it does, %s; allow them on the "
            "panel in Android Settings, under the app",
            package_id,
            ", ".join(grant.missing),
            target.address.stored_value,
            "; ".join(
                _GRANT_CONSEQUENCES.get(name, f"what needs {name} stays unavailable")
                for name in grant.missing
            ),
        )


async def _async_vendor_setup(
    device: AdbDeviceAsync,
) -> VendorSetup | ShellySetup | None:
    """Apply a vendor's setup; a refusal is reported, never fatal."""
    nonce = token_hex(16)
    body = await _async_shell(
        device, vendor_setup_command(nonce), read_timeout=_READ_TIMEOUT_SECONDS
    )
    try:
        lines, status = _parse_single_section(body, prefix="VENDOR", nonce=nonce)
        if status != 0:
            raise _MalformedAdbResponse
        observed = parse_vendor_setup(lines)
    except _MalformedAdbResponse, VendorResponseError:
        observed = None
        converged = False
    else:
        converged = observed is None or observed.converged
    if not converged:
        _LOGGER.warning(UNREAD_SETUP_WARNING if observed is None else observed.warning)
    return observed


async def _async_target_section(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    command: Callable[[str], str],
    prefix: str,
) -> list[str]:
    """Run one framed program on the identified panel and return its lines."""
    _validate_target(target)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer, authorize=False)
            nonce = token_hex(16)
            _parse_identity_root(
                await _async_shell(
                    device,
                    _identity_root_command(nonce),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
                target,
            )
            nonce = token_hex(16)
            lines, status = _parse_single_section(
                await _async_shell(
                    device, command(nonce), read_timeout=_READ_TIMEOUT_SECONDS
                ),
                prefix=prefix,
                nonce=nonce,
            )
            if status != 0:
                raise _MalformedAdbResponse
            return lines
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    finally:
        await _async_close(device)


async def async_apply_display_sizing(
    target: AdbInstallTarget, signer: PythonRSASigner, sizing: DisplaySizing
) -> DisplayResult:
    """Apply the profile's recommended display and text size at setup."""
    lines = await _async_target_section(
        target,
        signer,
        lambda nonce: display_sizing_command(nonce, sizing),
        "DISPLAY",
    )
    try:
        return parse_display_sizing(lines, sizing)
    except DisplayResponseError:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None


async def async_restore_display_sizing(
    target: AdbInstallTarget, signer: PythonRSASigner
) -> str:
    """Put back the display and text size setup changed, before the app goes.

    Returns ``restored``, ``unrestored`` or ``none`` (nothing was changed).
    """
    lines = await _async_target_section(
        target, signer, display_restore_command, "DISPLAY_RESTORE"
    )
    try:
        return parse_display_restore(lines)
    except DisplayResponseError:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None


async def async_settle_vendor_display(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> str:
    """Finish a pending vendor display restart before the final app launch."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer, authorize=False)
            await _async_require_identity_root(device, target, expected_root_mode)
            if (
                await _async_installed_artifact_size_on_device(device, descriptor)
                != descriptor.apk_size
            ):
                raise InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)
            nonce = token_hex(16)
            mutation_started = True
            lines, status = _parse_single_section(
                await _async_shell(
                    device,
                    vendor_display_settlement_command(nonce, descriptor.package_id),
                    read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                ),
                prefix="VENDOR_DISPLAY_SETTLEMENT",
                nonce=nonce,
            )
            if status != 0:
                raise VendorResponseError
            return parse_vendor_display_settlement(lines)
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        VendorResponseError,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(
            InstallAdbErrorCode.LAUNCH_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        ) from None
    finally:
        await _async_close(device)


async def async_restore_vendor_setup(
    target: AdbInstallTarget, signer: PythonRSASigner
) -> str:
    """Put back the vendor values an install changed, before the app goes.

    Returns ``restored``, ``unrestored`` or ``none`` (nothing was changed).
    """
    lines = await _async_target_section(
        target, signer, vendor_restore_command, "VENDOR_RESTORE"
    )
    try:
        return parse_vendor_restore(lines)
    except VendorResponseError:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None


async def async_repair_installed_app_permissions(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> None:
    """Repair an installed app's grants without relaunching or replacing it."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            await _async_repair_app_permissions(
                device, target, descriptor.package_id, descriptor.launch_component
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE) from None
    finally:
        await _async_close(device)


async def async_launch_installed_app(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    descriptor: InstallDescriptor,
    *,
    expected_root_mode: AdbRootMode,
) -> LaunchOutcome:
    """Launch the descriptor's one fixed package component exactly once."""
    _validate_request(target, descriptor)
    _validate_expected_root_mode(expected_root_mode)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_LAUNCH_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            await _async_repair_app_permissions(
                device, target, descriptor.package_id, descriptor.launch_component
            )
            nonce = token_hex(16)
            mutation_started = True
            return _parse_launch_outcome(
                await _async_shell(
                    device,
                    _launch_command(
                        nonce, descriptor.package_id, descriptor.launch_component
                    ),
                    read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.LAUNCH_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


async def async_cleanup_staged_apk(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    staged: StagedApk,
    reason: DefiniteCleanupReason,
    *,
    expected_root_mode: AdbRootMode,
) -> None:
    """Delete one verified job-owned stage after a definite disposition."""
    if not isinstance(target, AdbInstallTarget) or not isinstance(
        reason, DefiniteCleanupReason
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    _validate_target(target)
    _validate_expected_root_mode(expected_root_mode)
    remote_path = _validate_staged_apk(staged)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_CLEANUP_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            await _async_require_identity_root(device, target, expected_root_mode)
            nonce = token_hex(16)
            mutation_started = True
            _parse_cleanup(
                await _async_shell(
                    device,
                    _cleanup_command(nonce, remote_path),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.CLEANUP_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)


class MoveStep(StrEnum):
    """One package-manager step of removing the app from a panel (``removal``)."""

    OBSERVE = "OBSERVE"
    REMOVE_APP = "REMOVE_APP"
    VENDOR_HOME = "VENDOR_HOME"


@dataclass(frozen=True, slots=True)
class MoveObservation:
    """What the panel reports after a step, read in the same shell."""

    legacy_installed: bool
    successor_installed: bool
    #: The package HOME resolves to now, or None when nothing single answers.
    home: str | None
    #: The vendor's own firmware number (``ro.build.display.id``), or None
    #: when the panel reports none a help page could match.
    firmware: str | None = None


#: The old app's APK that Panel Assistant 0.9.0's move kept on a panel while
#: it moved; removal deletes any copy left behind.
_LEGACY_COPY = "/data/local/tmp/panel-assistant-old-app.apk"


def _uninstall(package: str) -> str:
    """Uninstall the app, asking it first to give up a device admin that blocks it.

    Android refuses to uninstall an active device admin, and the shell cannot
    remove one, so only the app can, and only once asked. The admin goes once
    Android has told the app it is disabled, so the uninstall is retried for up
    to ten seconds. An app that cannot give it up stays installed, which the
    observation reports.
    """
    uninstall = f"pm uninstall {package}"
    blocked = "*DELETE_FAILED_DEVICE_POLICY_MANAGER*"
    return (
        f'case "$({uninstall})" in {blocked}) '
        "am broadcast --include-stopped-packages "
        f"-n {package}/{RELEASE_DEVICE_ADMIN_RECEIVER}; "
        "for n in 1 2 3 4 5 6 7 8 9 10; do "
        f'case "$({uninstall})" in {blocked}) sleep 1 ;; *) break ;; esac; '
        "done ;; esac"
    )


_MOVE_ACTIONS: dict[MoveStep, tuple[str, ...]] = {
    MoveStep.OBSERVE: (),
    # Removes the app under both ids, only while HOME resolves to one launcher
    # that is neither of them: never the system chooser, an empty answer, or
    # Settings' FallbackHome, each of which would leave the panel without a
    # home screen. Read in the same shell as the uninstall, so a HOME that
    # changed since the last observation counts. A stray old app goes first,
    # and the new app only once nothing of the old one is listed: the new app
    # is what a retry reaches the panel through.
    MoveStep.REMOVE_APP: (
        f'case "$({_HOME_QUERY})" in {LEGACY_PACKAGE_ID}/*|{PACKAGE_ID}/*'
        "|android/*|com.android.settings/*) ;; ?*/?*) "
        f"am force-stop {LEGACY_PACKAGE_ID}; {_uninstall(LEGACY_PACKAGE_ID)}; "
        f"rm -f {_LEGACY_COPY}; "
        f'case "$(pm list packages -u {LEGACY_PACKAGE_ID} 2>/dev/null)" in '
        f"*package:{LEGACY_PACKAGE_ID}*) ;; *) "
        f"am force-stop {PACKAGE_ID}; {_uninstall(PACKAGE_ID)} ;; "
        "esac ;; esac",
    ),
    # The vendor's launcher takes HOME from the panel's shell when the app
    # cannot hand it back (vendor_provisioning names the panels).
    MoveStep.VENDOR_HOME: (VENDOR_HOME_COMMAND,),
}


def _move_command(nonce: str, step: MoveStep) -> str:
    quiet = ">/dev/null 2>&1"
    return "; ".join(
        (
            f"echo HAPANELD_MOVE_BEGIN:{nonce}",
            *(f"{action} {quiet}" for action in _MOVE_ACTIONS[step]),
            # No pipe into grep: toybox grep on some panels reports an error
            # on an empty pipe, which would corrupt the frame.
            *(
                f'case "$(pm path {package} 2>/dev/null)" in '
                f"package:*) echo installed:{package} ;; esac"
                for package in (LEGACY_PACKAGE_ID, PACKAGE_ID)
            ),
            f'echo "home:$({_HOME_QUERY})"',
            'echo "firmware:$(getprop ro.build.display.id 2>/dev/null)"',
            f"echo HAPANELD_MOVE_END:{nonce}:0",
        )
    )


def _parse_move(body: bytes, nonce: str) -> MoveObservation:
    lines, status_code = _parse_single_section(body, prefix="MOVE", nonce=nonce)
    homes = [line for line in lines if line.startswith("home:")]
    installed = {
        line.removeprefix("installed:")
        for line in lines
        if line.startswith("installed:")
    }
    firmwares = [
        line.removeprefix("firmware:") for line in lines if line.startswith("firmware:")
    ]
    if (
        status_code != 0
        or len(homes) != 1
        or not installed <= {LEGACY_PACKAGE_ID, PACKAGE_ID}
        or len(firmwares) > 1
        or len(homes) + len(installed) + len(firmwares) != len(lines)
    ):
        raise _MalformedAdbResponse
    home = homes[0].removeprefix("home:").strip()
    package, slash, _activity = home.partition("/")
    return MoveObservation(
        legacy_installed=LEGACY_PACKAGE_ID in installed,
        successor_installed=PACKAGE_ID in installed,
        # The system chooser and an empty answer are not a HOME.
        home=package if slash and package and package != "android" else None,
        firmware=_firmware(firmwares[0]) if firmwares else None,
    )


def _firmware(value: str) -> str | None:
    """Keep a firmware number a help page could match, and nothing else."""
    value = value.strip()
    return value if _FIRMWARE_PATTERN.fullmatch(value) else None


async def async_move_step(
    target: AdbInstallTarget, signer: PythonRSASigner, step: MoveStep
) -> MoveObservation:
    """Run one removal step on the pinned panel and observe its result."""
    _validate_target(target)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_INSTALL_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer)
            # Every connection proves the device at the address is the one the
            # removal started on before any command can change it.
            nonce = token_hex(16)
            _parse_identity_root(
                await _async_shell(
                    device,
                    _identity_root_command(nonce),
                    read_timeout=_READ_TIMEOUT_SECONDS,
                ),
                nonce,
                target,
            )
            mutation_started = step is not MoveStep.OBSERVE
            nonce = token_hex(16)
            return _parse_move(
                await _async_shell(
                    device,
                    _move_command(nonce, step),
                    read_timeout=_INSTALL_READ_TIMEOUT_SECONDS,
                    transport_timeout=_INSTALL_TIMEOUT_SECONDS,
                ),
                nonce,
            )
    except InstallAdbError:
        raise
    except (
        TimeoutError,
        _MalformedAdbResponse,
        _UnsafeAdbPacket,
        *_ADB_EXCEPTIONS,
    ):
        code = (
            InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if mutation_started
            else InstallAdbErrorCode.TARGET_UNREACHABLE
        )
        raise InstallAdbError(code) from None
    finally:
        await _async_close(device)

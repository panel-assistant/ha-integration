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
    HOME_COMPONENTS,
    LEGACY_LAUNCH_COMPONENT,
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
    is_accepted_package_id,
    is_launch_component,
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
    parse_vendor_restore,
    parse_vendor_setup,
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
            # the same rule, including the legacy residue a migration expects.
            for index in range(len(_RESIDUE_PATHS)):
                values, status = sections[f"RESIDUE{index}"]
                if status != 0 or values not in (["absent"], ["present"]):
                    raise _MalformedAdbResponse
                owner = _RESIDUE_PROBES[index][0]
                expected = (
                    admitted.migration_candidate and owner == LEGACY_PACKAGE_ID
                ) or (admitted.target_installed and owner == target_package_id)
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
    if not isinstance(target, AdbInstallTarget) or not is_accepted_package_id(
        package_id
    ):
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
            if not admitted.target_installed:
                raise InstallAdbError(InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING)
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
            if not admitted.target_installed:
                raise InstallAdbError(InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING)
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
            "The panel did not grant %s every permission it needs: "
            "modify system settings, display over other apps and its "
            "accessibility service, camera and microphone where present, "
            "and the secure-settings permission full screen-off needs. "
            "Controls that depend on them, such as "
            "touch sounds, stay unavailable, and the screen dims instead of "
            "switching off; allow them on the panel in Android Settings, "
            "under the app",
            package_id,
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
    """One package-manager step of moving a panel from the legacy app id."""

    OBSERVE = "OBSERVE"
    REMOVE_SUCCESSOR = "REMOVE_SUCCESSOR"
    CLAIM_HOME = "CLAIM_HOME"
    KEEP_LEGACY = "KEEP_LEGACY"
    SET_ASIDE_LEGACY = "SET_ASIDE_LEGACY"
    RETIRE_LEGACY = "RETIRE_LEGACY"
    RESET_SUCCESSOR = "RESET_SUCCESSOR"
    RESTORE_LEGACY = "RESTORE_LEGACY"
    RETURN_HOME = "RETURN_HOME"
    START_LEGACY = "START_LEGACY"
    REMOVE_APP = "REMOVE_APP"
    VENDOR_HOME = "VENDOR_HOME"


@dataclass(frozen=True, slots=True)
class MoveObservation:
    """What the panel reports after a move step, read in the same shell."""

    legacy_installed: bool
    successor_installed: bool
    #: The package HOME resolves to now, or None when nothing single answers.
    home: str | None
    #: The new app is installed and has never run: Android still marks it not
    #: launched, which no start of any of its components leaves set.
    successor_unlaunched: bool = False
    #: Read through the panel's root: the new app's own migration records,
    #: or None when they could not be read. A new app that has run only while
    #: waiting for the old app's handover holds no state of its own; one that
    #: finished, or started on its own, records ``complete``.
    successor_records: frozenset[str] | None = None
    #: The old app is set aside: not installed for the panel's user, so the
    #: new app starts as an ordinary app, while Android keeps its data.
    legacy_set_aside: bool = False
    #: SHA-256 of the copy of the old app's APK kept on the panel, which
    #: reinstalls it onto its kept data, or None when there is no copy.
    legacy_copy: str | None = None
    #: SHA-256 of the old app's installed APK, read beside a kept copy: only
    #: a copy with this digest is a whole copy of the app it will reinstall.
    legacy_apk: str | None = None
    #: The vendor's own firmware number (``ro.build.display.id``), or None
    #: when the panel reports none a help page could match.
    firmware: str | None = None


_LEGACY_COPY = "/data/local/tmp/panel-assistant-old-app.apk"


_MOVE_ACTIONS: dict[MoveStep, tuple[str, ...]] = {
    MoveStep.OBSERVE: (),
    # A successor beside a running legacy app holds only a copy of that app's
    # state, so removing it loses nothing the legacy app does not still hold.
    MoveStep.REMOVE_SUCCESSOR: (
        f"am force-stop {SUCCESSOR_PACKAGE_ID}",
        f"pm uninstall {SUCCESSOR_PACKAGE_ID}",
    ),
    # The old app's APK is kept beside its data until the new app is proven,
    # because setting the old app aside deletes the APK Android installed.
    # An earlier copy is never reused, and a copy that did not finish never
    # takes the copy's name; the observation then compares it with the APK.
    MoveStep.KEEP_LEGACY: (
        f"{{ rm -f {_LEGACY_COPY} {_LEGACY_COPY}.part; "
        f"p=$(pm path {LEGACY_PACKAGE_ID}); p=${{p#package:}}; "
        f'[ -n "$p" ] && cp "$p" {_LEGACY_COPY}.part '
        f"&& chmod 644 {_LEGACY_COPY}.part "
        f"&& mv {_LEGACY_COPY}.part {_LEGACY_COPY}; rm -f {_LEGACY_COPY}.part; }}",
    ),
    MoveStep.SET_ASIDE_LEGACY: (
        f"am force-stop {LEGACY_PACKAGE_ID}",
        f"pm uninstall -k --user 0 {LEGACY_PACKAGE_ID}",
    ),
    # Only while HOME resolves to one launcher that is not the old app: never
    # the old app itself, which would leave the panel without one, nor a HOME
    # that cannot be read, nor the system chooser. Read in the same shell as
    # the uninstall, so a HOME that changed since the last observation counts.
    MoveStep.RETIRE_LEGACY: (
        f'case "$({_HOME_QUERY})" in {LEGACY_PACKAGE_ID}/*|android/*) ;; ?*/?*) '
        f"am force-stop {LEGACY_PACKAGE_ID}; pm uninstall {LEGACY_PACKAGE_ID}; "
        f"rm -f {_LEGACY_COPY} ;; esac",
    ),
    # Reinstalling the kept APK returns the old app onto the data Android kept.
    MoveStep.RESTORE_LEGACY: (
        f"{{ [ -f {_LEGACY_COPY} ] && pm install -r {_LEGACY_COPY}; "
        f'case "$(pm path {LEGACY_PACKAGE_ID})" in '
        f"package:*) rm -f {_LEGACY_COPY} ;; esac; }}",
    ),
    MoveStep.RETURN_HOME: (
        f"cmd package set-home-activity {HOME_COMPONENTS[LEGACY_LAUNCH_COMPONENT]}",
    ),
    MoveStep.START_LEGACY: (f"am start -n {HOME_COMPONENTS[LEGACY_LAUNCH_COMPONENT]}",),
    # Removes the app under both ids, only while HOME resolves to one launcher
    # that is neither of them: never the system chooser, an empty answer, or
    # Settings' FallbackHome, each of which would leave the panel without a
    # home screen. Read in the same shell as the uninstall, as for retiring.
    # The old id goes first, and the new app only once nothing of the old one
    # is listed: the new app is what a retry reaches the panel through.
    MoveStep.REMOVE_APP: (
        f'case "$({_HOME_QUERY})" in {LEGACY_PACKAGE_ID}/*|{SUCCESSOR_PACKAGE_ID}/*'
        "|android/*|com.android.settings/*) ;; ?*/?*) "
        f"am force-stop {LEGACY_PACKAGE_ID}; pm uninstall {LEGACY_PACKAGE_ID}; "
        f"rm -f {_LEGACY_COPY}; "
        f'case "$(pm list packages -u {LEGACY_PACKAGE_ID} 2>/dev/null)" in '
        f"*package:{LEGACY_PACKAGE_ID}*) ;; *) "
        f"am force-stop {SUCCESSOR_PACKAGE_ID}; pm uninstall {SUCCESSOR_PACKAGE_ID} ;; "
        "esac ;; esac",
    ),
    # The vendor's launcher takes HOME from the panel's shell when the app
    # cannot hand it back (vendor_provisioning names the panels).
    MoveStep.VENDOR_HOME: (VENDOR_HOME_COMMAND,),
    # A successor that first starts with no legacy app and no migration record
    # beside it runs as an ordinary app; clearing it makes that start certain.
    MoveStep.RESET_SUCCESSOR: (
        f"am force-stop {SUCCESSOR_PACKAGE_ID}",
        f"pm clear {SUCCESSOR_PACKAGE_ID}",
    ),
}


_SUCCESSOR_RECORDS_DIRECTORY = (
    f"/data/data/{SUCCESSOR_PACKAGE_ID}/no_backup/identity-migration"
)


# The app's data is private to it, so only root can read its records. The
# script goes to su on standard input, which both su styles on the panels
# accept, and each root route is tried without waiting on a prompt; none
# answering leaves the records unknown rather than empty.
_SUCCESSOR_RECORDS_COMMAND = (
    "for p in 'su 0' 'su root'; do "
    "r=$(printf '%s\\n' '"
    # Only a directory that exists and can be listed counts as read: an
    # absent or unlistable one is unknown, never an empty set of records.
    f"[ -d {_SUCCESSOR_RECORDS_DIRECTORY} ] || exit 3; "
    f"ls {_SUCCESSOR_RECORDS_DIRECTORY} >/dev/null 2>&1 || exit 3; "
    f"for f in {_SUCCESSOR_RECORDS_DIRECTORY}/*; do "
    '[ -e "$f" ] && echo "record:${f##*/}"; done; echo records:read'
    "' | timeout 3 $p sh 2>/dev/null) && "
    'case "$r" in *records:read*) echo "$r"; break ;; esac; done'
)


def _move_command(
    nonce: str, step: MoveStep, successor_launch: str | None = None
) -> str:
    quiet = ">/dev/null 2>&1"
    actions: tuple[str, ...]
    if step is MoveStep.CLAIM_HOME:
        # HOME goes to the dashboard of the exact build the move installed,
        # whichever package its classes live in.
        assert successor_launch is not None
        actions = (
            f"cmd package set-home-activity {HOME_COMPONENTS[successor_launch]}",
        )
    else:
        actions = _MOVE_ACTIONS[step]
    return "; ".join(
        (
            f"echo HAPANELD_MOVE_BEGIN:{nonce}",
            *(f"{action} {quiet}" for action in actions),
            # No pipe into grep: toybox grep on some panels reports an error
            # on an empty pipe, which would corrupt the frame.
            *(
                f'case "$(pm path {package} 2>/dev/null)" in '
                f"package:*) echo installed:{package} ;; esac"
                for package in (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID)
            ),
            # Listed with uninstalled packages: installed, or set aside with
            # its data kept.
            f"for l in $(pm list packages -u {LEGACY_PACKAGE_ID} 2>/dev/null); do "
            f'[ "$l" = package:{LEGACY_PACKAGE_ID} ] '
            f"&& echo listed:{LEGACY_PACKAGE_ID}; done",
            f"[ -f {_LEGACY_COPY} ] && set -- $(sha256sum {_LEGACY_COPY} 2>/dev/null)"
            ' && [ -n "$1" ] && echo "copy:$1"',
            f"[ -f {_LEGACY_COPY} ] && p=$(pm path {LEGACY_PACKAGE_ID} 2>/dev/null) "
            '&& set -- $(sha256sum "${p#package:}" 2>/dev/null) && [ -n "$1" ] '
            '&& echo "apk:$1"',
            f'case "$(dumpsys package {SUCCESSOR_PACKAGE_ID} 2>/dev/null)" in '
            f"*notLaunched=true*) echo unlaunched:{SUCCESSOR_PACKAGE_ID} ;; esac",
            _SUCCESSOR_RECORDS_COMMAND,
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
    unlaunched = [
        line for line in lines if line == f"unlaunched:{SUCCESSOR_PACKAGE_ID}"
    ]
    read = [line for line in lines if line == "records:read"]
    records = [line for line in lines if line.startswith("record:")]
    listed = [line for line in lines if line == f"listed:{LEGACY_PACKAGE_ID}"]
    copies = [line.removeprefix("copy:") for line in lines if line.startswith("copy:")]
    apks = [line.removeprefix("apk:") for line in lines if line.startswith("apk:")]
    firmwares = [
        line.removeprefix("firmware:") for line in lines if line.startswith("firmware:")
    ]
    if (
        status_code != 0
        or len(homes) != 1
        or not installed <= {LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID}
        or len(unlaunched) > 1
        or len(read) > 1
        or (records and not read)
        or len(listed) > 1
        or len(copies) > 1
        or len(apks) > 1
        or len(firmwares) > 1
        or any(not _SHA256_PATTERN.match(digest) for digest in (*copies, *apks))
        or len(homes)
        + len(installed)
        + len(unlaunched)
        + len(read)
        + len(records)
        + len(listed)
        + len(copies)
        + len(apks)
        + len(firmwares)
        != len(lines)
    ):
        raise _MalformedAdbResponse
    home = homes[0].removeprefix("home:").strip()
    package, slash, _activity = home.partition("/")
    return MoveObservation(
        legacy_installed=LEGACY_PACKAGE_ID in installed,
        successor_installed=SUCCESSOR_PACKAGE_ID in installed,
        # The system chooser and an empty answer are not a HOME.
        home=package if slash and package and package != "android" else None,
        successor_unlaunched=bool(unlaunched) and SUCCESSOR_PACKAGE_ID in installed,
        successor_records=(
            frozenset(line.removeprefix("record:") for line in records)
            if read and SUCCESSOR_PACKAGE_ID in installed
            else None
        ),
        legacy_set_aside=bool(listed) and LEGACY_PACKAGE_ID not in installed,
        legacy_copy=copies[0] if copies else None,
        legacy_apk=apks[0] if apks and LEGACY_PACKAGE_ID in installed else None,
        firmware=_firmware(firmwares[0]) if firmwares else None,
    )


def _firmware(value: str) -> str | None:
    """Keep a firmware number a help page could match, and nothing else."""
    value = value.strip()
    return value if _FIRMWARE_PATTERN.fullmatch(value) else None


async def async_move_step(
    target: AdbInstallTarget,
    signer: PythonRSASigner,
    step: MoveStep,
    *,
    authorize: bool = True,
    successor_launch: str | None = None,
) -> MoveObservation:
    """Run one move step on the pinned panel and observe its result.

    Only a step the owner started may offer Panel Assistant's key to the panel.
    Claiming HOME names the launcher of the new-app build the move installed.
    """
    _validate_target(target)
    if (step is MoveStep.CLAIM_HOME) != is_launch_component(
        SUCCESSOR_PACKAGE_ID, successor_launch
    ):
        raise InstallAdbError(InstallAdbErrorCode.INVALID_REQUEST)
    device: AdbDeviceAsync | None = None
    mutation_started = False
    try:
        async with asyncio.timeout(_INSTALL_TIMEOUT_SECONDS):
            device = await _async_connect(target, signer, authorize=authorize)
            # Every connection proves the device at the address is the one the
            # move started on before any command can change it.
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
            if step is MoveStep.START_LEGACY:
                # Setting the old app aside drops its app-op and accessibility
                # grants, so they are repaired, as for an install, before it
                # starts again.
                await _async_repair_app_permissions(
                    device, target, LEGACY_PACKAGE_ID, LEGACY_LAUNCH_COMPONENT
                )
            nonce = token_hex(16)
            return _parse_move(
                await _async_shell(
                    device,
                    _move_command(nonce, step, successor_launch),
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

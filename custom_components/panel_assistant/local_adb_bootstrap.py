"""Bootstrap the independent recovery owner over the original admitted ADB."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import PurePosixPath
from secrets import token_hex
from typing import Any

from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from . import adb_protocol as adb
from .app_identity import PACKAGE_ID
from .install_adb import _async_prove_su

ROOT_PARENT = "/data/local"
RECOVERY_DIRECTORY = f"{ROOT_PARENT}/panel-assistant-adb-recovery"
RECOVERY_EXECUTABLE = f"{RECOVERY_DIRECTORY}/helper"
_SHA = re.compile(r"[0-9a-f]{64}")


def prepared_asset(result: dict[str, Any]) -> tuple[str, str, str]:
    """Admit only the exact bundled file staged in this app's private cache."""
    path, sha, build = (result.get(key) for key in ("path", "sha256", "build_id"))
    if (
        set(result) != {"path", "sha256", "build_id"}
        or not isinstance(path, str)
        or not isinstance(sha, str)
        or not isinstance(build, str)
        or _SHA.fullmatch(sha) is None
        or _SHA.fullmatch(build) is None
        or path
        not in tuple(
            f"{prefix}/{PACKAGE_ID}/cache/adb-recovery/helper-{sha}"
            for prefix in ("/data/user/0", "/data/data")
        )
    ):
        raise adb.InstallAdbError(adb.InstallAdbErrorCode.INVALID_REQUEST)
    return path, sha, build


def bootstrap_command(path: str, sha: str, nonce: str) -> str:
    """Publish and execute only a verified root-owned copy, never the app file.

    The signed app owns its source directory and already has administrator
    authority. Its source may change during copy: the digest is proved on the
    root-owned copy before execution. No unrelated app can replace that copy
    or either destination directory. The incumbent helper is never touched.
    """
    source = shlex.quote(path)
    private = shlex.quote(str(PurePosixPath(path).parent))
    cache = shlex.quote(str(PurePosixPath(path).parents[1]))
    package = shlex.quote(str(PurePosixPath(path).parents[2]))
    parent, directory, executable = map(
        shlex.quote, (ROOT_PARENT, RECOVERY_DIRECTORY, RECOVERY_EXECUTABLE)
    )
    stage = shlex.quote(f"{RECOVERY_DIRECTORY}/.stage-{nonce}")
    # Refusing an existing stage avoids clobbering an interrupted or competing
    # bootstrap. Only this invocation's newly created directory is cleaned.
    program = "; ".join(
        (
            '[ "$(id -u)" = 0 ] || exit 1',
            f"[ -d {parent} ] && [ ! -L {parent} ] || exit 1",
            f'case "$(stat -c %u:%a {parent})" in '
            "0:700|0:750|0:751|0:755) ;; *) exit 1 ;; esac",
            f"if [ ! -e {directory} ] && [ ! -L {directory} ]; then "
            f"mkdir -m 700 {directory} || exit 1; fi",
            f"[ -d {directory} ] && [ ! -L {directory} ] && "
            f'[ "$(stat -c %u:%a {directory})" = 0:700 ] || exit 1',
            f"[ -d {package} ] && [ ! -L {package} ] && "
            f"[ -d {cache} ] && [ ! -L {cache} ] && "
            f"[ -d {private} ] && [ ! -L {private} ] || exit 1",
            f'uid=$(stat -c %u {package}); [ "$uid" -ge 10000 ] || exit 1',
            f'[ "$(stat -c %u:%a {private})" = "$uid:700" ] || exit 1',
            f"[ -f {source} ] && [ ! -L {source} ] && "
            f'[ "$(stat -c %u:%a:%h {source})" = "$uid:600:1" ] || exit 1',
            f"size=$(stat -c %s {source}) || exit 1",
            '[ "$size" -ge 1 ] && [ "$size" -le 8388608 ] || exit 1',
            f"mkdir -m 700 {stage} || exit 1",
            f"trap 'rm -f {stage}/helper; rmdir {stage}' EXIT",
            f"cp {source} {stage}/helper && chmod 700 {stage}/helper || exit 1",
            f"[ -f {stage}/helper ] && [ ! -L {stage}/helper ] && "
            f'[ "$(stat -c %u:%a:%h {stage}/helper)" = 0:700:1 ] || exit 1',
            f'set -- $(sha256sum {stage}/helper); [ "$1" = {sha} ] || exit 1',
            f"if [ -e {executable} ] || [ -L {executable} ]; then "
            f"[ -f {executable} ] && [ ! -L {executable} ] && "
            f'[ "$(stat -c %u:%a:%h {executable})" = 0:700:1 ] || exit 1; fi',
            f"mv -f {stage}/helper {executable} || exit 1",
            f"nohup {executable} --adb-recovery >/dev/null 2>&1 </dev/null & hp_pid=$!",
            "echo launched",
        )
    )
    return (
        f"echo HAPANELD_BOOTSTRAP_BEGIN:{nonce}; sh -c {shlex.quote(program)}; "
        f"echo HAPANELD_BOOTSTRAP_END:{nonce}:$?"
    )


async def async_bootstrap(
    target: adb.AdbInstallTarget,
    signer: PythonRSASigner,
    path: str,
    sha: str,
    *,
    before_mutation: Callable[[], bool],
) -> bool:
    """Use the same physical target and a freshly proven root dialect."""
    device = None
    mutation_started = False
    try:
        device = await adb._async_connect(target, signer, authorize=False)
        nonce = token_hex(16)
        mode = adb._parse_identity_root(
            await adb._async_shell(
                device, adb._identity_root_command(nonce), read_timeout=10
            ),
            nonce,
            target,
        )
        if mode is adb.AdbRootMode.ROOTLESS:
            return False
        prefix = (
            await _async_prove_su(device, admitted=None)
            if mode is adb.AdbRootMode.ROOT_SU
            else None
        )
        if not before_mutation():
            raise adb.InstallAdbError(adb.InstallAdbErrorCode.TARGET_CHANGED)
        nonce = token_hex(16)
        command = bootstrap_command(path, sha, nonce)
        if prefix is not None:
            command = f"{prefix} {shlex.quote(command)}"
        mutation_started = True
        lines, status = adb._parse_single_section(
            await adb._async_shell(device, command, read_timeout=30),
            prefix="BOOTSTRAP",
            nonce=nonce,
        )
        if status != 0 or lines != ["launched"]:
            raise adb.InstallAdbError(adb.InstallAdbErrorCode.TARGET_RESPONSE_INVALID)
        return True
    except adb.InstallAdbError:
        raise
    except (
        TimeoutError,
        adb._MalformedAdbResponse,
        adb._UnsafeAdbPacket,
        *adb._ADB_EXCEPTIONS,
    ):
        raise adb.InstallAdbError(
            adb.InstallAdbErrorCode.INSTALL_AMBIGUOUS
            if mutation_started
            else adb.InstallAdbErrorCode.TARGET_UNREACHABLE
        ) from None
    finally:
        await adb._async_close(device)

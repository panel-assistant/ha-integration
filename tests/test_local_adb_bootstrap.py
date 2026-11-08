"""Bootstrap publishes only the exact root-owned copy through the real shell."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from custom_components.panel_assistant import local_adb_bootstrap as bootstrap
from custom_components.panel_assistant.adb_protocol import InstallAdbError

# Every damage variant changes file ownership to model Android app UIDs.
needs_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="needs root to own files as the panel does"
)


@pytest.mark.parametrize("invalid", ["path", "hash", "build", "extra"])
def test_prepare_refuses_unconstrained_bootstrap_metadata(invalid: str) -> None:
    sha = "a" * 64
    result = {
        "path": "/data/user/0/io.panelassistant.android/cache/adb-recovery/"
        f"helper-{sha}",
        "sha256": sha,
        "build_id": "b" * 64,
    }
    if invalid == "path":
        result["path"] = "/data/local/hapaneld-helper"
    elif invalid == "extra":
        result["other"] = "unexpected"
    else:
        result["sha256" if invalid == "hash" else "build_id"] = "not-a-digest"
    with pytest.raises(InstallAdbError):
        bootstrap.prepared_asset(result)


@needs_root
@pytest.mark.parametrize(
    "damage", ["none", "digest", "symlink", "writable-parent", "source-owner"]
)
def test_bootstrap_executes_only_verified_root_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    parent = tmp_path / "root-parent"
    parent.mkdir(mode=0o700)
    directory = parent / "panel-assistant-adb-recovery"
    marker = tmp_path / "launched"
    package = tmp_path / "app"
    private = package / "cache" / "adb-recovery"
    private.mkdir(mode=0o700, parents=True)
    source = private / "helper-asset"
    source.write_text(f"#!/bin/sh\nprintf '%s' \"$0\" > '{marker}'\n")
    source.chmod(0o600)
    for path in (package, private, source):
        os.chown(path, 10000, 10000)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if damage == "digest":
        sha = "a" * 64
    elif damage == "symlink":
        directory.symlink_to(private)
    elif damage == "writable-parent":
        parent.chmod(0o777)
    elif damage == "source-owner":
        os.chown(source, 10001, 10001)
    monkeypatch.setattr(bootstrap, "ROOT_PARENT", str(parent))
    monkeypatch.setattr(bootstrap, "RECOVERY_DIRECTORY", str(directory))
    monkeypatch.setattr(bootstrap, "RECOVERY_EXECUTABLE", str(directory / "helper"))
    command = bootstrap.bootstrap_command(str(source), sha, "1" * 32)
    result = subprocess.run(
        ["sh", "-c", command], capture_output=True, timeout=5, check=False
    )
    if damage == "none":
        assert b"launched\n" in result.stdout, result.stderr
        # Wait for the short fake daemon without assuming scheduler order.
        subprocess.run(
            [
                "sh",
                "-c",
                f"for i in 1 2 3 4 5; do [ -f '{marker}' ] && exit 0; "
                "sleep .02; done; exit 1",
            ],
            check=True,
        )
        assert marker.read_text() == str(directory / "helper")
        installed = directory / "helper"
        assert installed.stat().st_uid == 0
        assert installed.stat().st_mode & 0o777 == 0o700
        assert hashlib.sha256(installed.read_bytes()).hexdigest() == sha
    else:
        assert b"launched\n" not in result.stdout
        assert not marker.exists()
    assert source.exists()


@pytest.mark.parametrize("mode", ["root-adbd", "su", "rootless", "revoked"])
async def test_bootstrap_proves_root_and_current_authority_before_copy(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import re

    from adb_shell.auth.sign_pythonrsa import PythonRSASigner

    from custom_components.panel_assistant import adb_protocol as adb
    from custom_components.panel_assistant.client import normalize_address

    from .install_adb_fakes import _identity_root_output, _section, _single_output

    commands = []

    class Device:
        async def streaming_shell(self, command: str, **_kwargs: object) -> object:
            commands.append(command)
            nonce = re.search(r"HAPANELD_\w+_BEGIN:([a-f0-9]{32})", command)[1]
            if "HAPANELD_POSTURE_BEGIN" in command:
                yield _identity_root_output(
                    nonce,
                    uid="0" if mode == "root-adbd" else "2000",
                    su_lines=["present"] if mode in ("su", "revoked") else ["absent"],
                )
            elif "HAPANELD_DELEGATE_BEGIN" in command:
                yield (
                    "\n".join(
                        [
                            f"HAPANELD_DELEGATE_BEGIN:{nonce}",
                            f"HAPANELD_SU_BEGIN:{nonce}",
                            *_section("SU", "UID", nonce, ["0"], 0),
                            f"HAPANELD_SU_END:{nonce}",
                            f"HAPANELD_DELEGATE_END:{nonce}:0",
                        ]
                    )
                    + "\n"
                ).encode()
            else:
                assert "HAPANELD_BOOTSTRAP_BEGIN" in command
                if mode == "su":
                    assert command.startswith("su 0 ")
                yield _single_output("BOOTSTRAP", nonce, ["launched"], 0)

        async def close(self) -> None:
            pass

    from unittest.mock import AsyncMock

    monkeypatch.setattr(adb, "_async_connect", AsyncMock(return_value=Device()))
    target = adb.AdbInstallTarget(
        normalize_address("192.168.1.23"), "SERIAL-1", "Test Panel", "arm64-v8a", 34
    )
    call = bootstrap.async_bootstrap(
        target,
        object.__new__(PythonRSASigner),
        "/unused/app-cache/asset",
        "a" * 64,
        before_mutation=lambda: mode != "revoked",
    )
    if mode == "revoked":
        with pytest.raises(InstallAdbError):
            await call
    else:
        assert await call is (mode != "rootless")
    copies = [command for command in commands if "HAPANELD_BOOTSTRAP_BEGIN" in command]
    assert len(copies) == (1 if mode in ("root-adbd", "su") else 0)

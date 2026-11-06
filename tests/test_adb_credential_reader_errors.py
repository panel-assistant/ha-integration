"""Filesystem error contracts of the durable ADB credential reader."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.panel_assistant import adb_credentials
from custom_components.panel_assistant.adb_credentials import (
    AdbCredentialError,
    AdbCredentialMissingError,
)


def test_missing_store_keeps_missing_specific_error(tmp_path: Path) -> None:
    """A missing identity remains distinguishable from an unreadable identity."""
    with pytest.raises(AdbCredentialMissingError) as raised:
        adb_credentials._read_durable_credential(str(tmp_path / "missing"))

    assert isinstance(raised.value.__cause__, FileNotFoundError)


def test_open_failure_retains_filesystem_cause(tmp_path: Path) -> None:
    """Callers receive a credential error with the original I/O failure."""
    failure = OSError(errno.EIO, "credential storage unavailable")
    with (
        patch.object(adb_credentials.os, "open", side_effect=failure),
        pytest.raises(AdbCredentialError) as raised,
    ):
        adb_credentials._read_durable_credential(str(tmp_path / "store"))

    assert raised.value.__cause__ is failure
    assert not isinstance(raised.value, AdbCredentialMissingError)


def test_late_path_failure_retains_cause_and_closes_descriptor(tmp_path: Path) -> None:
    """After parsing a real Store, path failure releases its acquired descriptor."""
    credential = adb_credentials._generate_credential()
    store_path = tmp_path / "panel_assistant.adb_key"
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "minor_version": 1,
                "key": "panel_assistant.adb_key",
                "data": adb_credentials._serialize_credential(credential),
            }
        ),
        encoding="utf-8",
    )
    store_path.chmod(0o600)
    real_open, real_fstat, real_close = os.open, os.fstat, os.close
    path_metadata = os.lstat(store_path)
    opened: list[int] = []
    failure = OSError(errno.EIO, "credential path unavailable")

    def capture_open(path: str, flags: int) -> int:
        file_fd = real_open(path, flags)
        opened.append(file_fd)
        return file_fd

    try:
        with (
            patch.object(adb_credentials.os, "open", side_effect=capture_open),
            patch.object(
                adb_credentials.os, "lstat", side_effect=[path_metadata, failure]
            ),
            pytest.raises(AdbCredentialError) as raised,
        ):
            adb_credentials._read_durable_credential(str(store_path))

        assert raised.value.__cause__ is failure
        assert len(opened) == 1
        with pytest.raises(OSError) as closed:
            real_fstat(opened[0])
        assert closed.value.errno == errno.EBADF
    finally:
        for file_fd in opened:
            try:
                real_fstat(file_fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
            else:
                real_close(file_fd)

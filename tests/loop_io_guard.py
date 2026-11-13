"""Record whole-body hashing, zip checks and update files that run on the event loop.

Home Assistant runs every integration on one event loop. Hashing an APK or a
backup of up to hundreds of megabytes there, reading every entry of a zip, or
creating and removing a file holds every other integration until it ends. A
test installs these guards, drives the real caller, and asserts nothing was
recorded.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any
from zipfile import ZipFile

import pytest

# The largest piece hashed on the loop as it streams in (blob._CHUNK_BYTES).
CHUNK_BYTES = 64 * 1024
UPDATE_FILE_PREFIX = "panel-assistant-update-"


class LoopIoGuard:
    """Collect blocking work seen on the thread that created the guard."""

    def __init__(self) -> None:
        self.loop_thread = threading.get_ident()
        self.on_loop: list[str] = []

    def _record(self, what: str) -> None:
        if threading.get_ident() == self.loop_thread:
            self.on_loop.append(what)

    def hashlib(self) -> Any:
        """A stand-in for a module's ``hashlib`` that records whole-body hashing."""
        guard = self

        class _Digest:
            def __init__(self, data: bytes) -> None:
                self._digest = hashlib.sha256()
                if data:
                    guard._record(f"sha256 of {len(data)} bytes at once")
                    self._digest.update(data)

            def update(self, chunk: bytes) -> None:
                if len(chunk) > CHUNK_BYTES:
                    guard._record(f"sha256 update of {len(chunk)} bytes")
                self._digest.update(chunk)

            def hexdigest(self) -> str:
                return self._digest.hexdigest()

            def digest(self) -> bytes:
                return self._digest.digest()

        class _Hashlib:
            @staticmethod
            def sha256(data: bytes = b"") -> _Digest:
                return _Digest(data)

        return _Hashlib()

    def install_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record zip checks and the update file's creation and removal."""
        real_open, real_unlink, real_testzip = os.open, os.unlink, ZipFile.testzip

        def guarded_open(path: Any, *args: Any, **kwargs: Any) -> int:
            if UPDATE_FILE_PREFIX in os.fspath(path):
                self._record("update file created")
            return real_open(path, *args, **kwargs)

        def guarded_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            if UPDATE_FILE_PREFIX in os.fspath(path):
                self._record("update file removed")
            real_unlink(path, *args, **kwargs)

        def guarded_testzip(archive: ZipFile) -> str | None:
            self._record("testzip")
            return real_testzip(archive)

        monkeypatch.setattr(os, "open", guarded_open)
        monkeypatch.setattr(os, "unlink", guarded_unlink)
        monkeypatch.setattr(ZipFile, "testzip", guarded_testzip)

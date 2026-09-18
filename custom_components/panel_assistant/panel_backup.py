"""Keep the panel's own settings backup in Home Assistant before changing its app."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_KEEP_PER_PANEL = 5
# The panel's archive is one manifest plus a small, deliberately bounded set of
# file-backed payloads. A plausible archive has to be a readable zip carrying
# that manifest, so a silently truncated or empty download cannot be mistaken
# for a backup.
_MANIFEST_ENTRY = "manifest.json"
_MAX_ARCHIVE_ENTRIES = 8


class PanelBackupInvalidError(Exception):
    """The bytes returned by the panel are not a usable settings backup."""


@dataclass(frozen=True, slots=True)
class PanelBackupReceipt:
    """What was actually verified about one stored backup, for the record."""

    path: Path
    size: int
    sha256: str
    entries: int
    taken_at: str

    def as_dict(self) -> dict[str, str | int]:
        """Return the serializable receipt written beside the archive."""
        return {
            "archive": self.path.name,
            "size": self.size,
            "sha256": self.sha256,
            "entries": self.entries,
            "taken_at": self.taken_at,
        }


def verify_panel_backup(data: bytes) -> tuple[int, str]:
    """Prove these bytes are a readable panel archive; return entries and digest.

    Existence is not proof: a copy can fail, truncate, or arrive empty while
    still producing a file. Every check here is on the bytes about to be kept,
    before anything upgrades or replaces the app that produced them.
    """
    if not data:
        raise PanelBackupInvalidError
    try:
        with ZipFile(BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise PanelBackupInvalidError
            names = archive.namelist()
            if (
                not names
                or len(names) > _MAX_ARCHIVE_ENTRIES
                or len(set(names)) != len(names)
                or _MANIFEST_ENTRY not in names
                or not archive.read(_MANIFEST_ENTRY)
            ):
                raise PanelBackupInvalidError
    except (BadZipFile, OSError, ValueError) as err:
        raise PanelBackupInvalidError from err
    return len(names), sha256(data).hexdigest()


def _write_backup(
    directory: Path, name: str, prefix: str, data: bytes, receipt: dict[str, str | int]
) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / name
    partial = directory / f".{name}.partial"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, target)
    _write_receipt(directory, name, receipt)
    kept = sorted(directory.glob(f"{prefix}*.zip"))
    for old in kept[:-_KEEP_PER_PANEL]:
        old.unlink(missing_ok=True)
        (old.parent / f"{old.name}.json").unlink(missing_ok=True)
    return target


def _write_receipt(directory: Path, name: str, receipt: dict[str, str | int]) -> None:
    target = directory / f"{name}.json"
    partial = directory / f".{name}.json.partial"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, target)


async def async_store_panel_backup(
    hass: HomeAssistant, entry_id: str, version_code: int | None, data: bytes
) -> PanelBackupReceipt:
    """Verify one backup, write it atomically, and keep the newest few."""
    entries, digest = verify_panel_backup(data)
    taken_at = datetime.now(UTC)
    stamp = taken_at.strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{entry_id}-"
    name = f"{prefix}{stamp}-vc{version_code if version_code else 'unknown'}.zip"
    directory = Path(hass.config.path(DOMAIN, "backups"))
    receipt = PanelBackupReceipt(
        path=directory / name,
        size=len(data),
        sha256=digest,
        entries=entries,
        taken_at=taken_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    await hass.async_add_executor_job(
        _write_backup, directory, name, prefix, data, receipt.as_dict()
    )
    return receipt

"""Panel Assistant keeps only a whole, unambiguous panel backup."""

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.panel_backup import (
    PanelBackupInvalidError,
    async_store_panel_backup,
)

PAYLOAD = b"panel settings payload " * 8


def _zip(*names: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_STORED) as archive:
        for name in names:
            body = json.dumps({"kind": "ha-paneld-backup"}).encode()
            archive.writestr(name, body if name == "manifest.json" else PAYLOAD)
    return buffer.getvalue()


def _nothing_kept(tmp_path: Path) -> bool:
    directory = tmp_path / DOMAIN / "backups"
    return not directory.exists() or not list(directory.iterdir())


async def _store(hass: HomeAssistant, tmp_path: Path, data: bytes):
    hass.config.config_dir = str(tmp_path)
    return await async_store_panel_backup(hass, "entry-id", 771, data)


async def test_a_backup_with_a_corrupt_payload_is_never_kept(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """A flipped payload byte leaves a readable zip whose content is wrong."""
    data = bytearray(_zip("manifest.json", "payload-0.bin"))
    at = data.find(PAYLOAD)
    data[at] ^= 0xFF

    with pytest.raises(PanelBackupInvalidError):
        await _store(hass, tmp_path, bytes(data))
    assert _nothing_kept(tmp_path)


async def test_a_backup_with_duplicate_entries_is_never_kept(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Two manifests leave it to the reader which one counts."""
    with pytest.warns(UserWarning):
        data = _zip("manifest.json", "manifest.json")

    with pytest.raises(PanelBackupInvalidError):
        await _store(hass, tmp_path, data)
    assert _nothing_kept(tmp_path)


async def test_a_backup_with_too_many_entries_is_never_kept(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The panel's archive is a manifest and at most seven payloads."""
    payloads = [f"payload-{index}.bin" for index in range(8)]

    with pytest.raises(PanelBackupInvalidError):
        await _store(hass, tmp_path, _zip("manifest.json", *payloads))
    assert _nothing_kept(tmp_path)

    receipt = await _store(hass, tmp_path, _zip("manifest.json", *payloads[:7]))
    assert receipt.entries == 8 and receipt.path.exists()

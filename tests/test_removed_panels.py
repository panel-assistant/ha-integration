"""A removed entry's panel is told so at its next hello, across restarts."""

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from zipfile import ZipFile

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import removed_panels
from custom_components.panel_assistant.const import DOMAIN

from .test_transport import DID, HEALTH, WsClientFactory, _hello, _send
from .test_upgrade_repairs import CURRENT, _entry, _set_up


async def _hello_error(
    hass: HomeAssistant, hass_ws_client: WsClientFactory, token: str
) -> str | None:
    client = await hass_ws_client(hass, token)
    response = await _send(client, _hello())
    if response["success"]:
        return None
    code: str = response["error"]["code"]
    return code


async def _reloaded_store(hass: HomeAssistant) -> removed_panels.RemovedPanels:
    """Read the removed panels back from storage, as after a restart."""
    removed = removed_panels.RemovedPanels(hass)
    await removed.async_load()
    return removed


async def test_a_removed_entry_is_remembered_until_the_panel_is_added_again(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Removed, then unknown once an entry with its identity loads and unloads."""
    entry = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, entry, CURRENT)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    token = hass_read_only_access_token
    assert await _hello_error(hass, hass_ws_client, token) == "entry_removed"
    # Across a restart.
    reloaded = await _reloaded_store(hass)
    assert DID in reloaded
    hass.data[DOMAIN]["removed_panels"] = reloaded
    assert await _hello_error(hass, hass_ws_client, token) == "entry_removed"

    again = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, again, CURRENT)
    assert DID not in await _reloaded_store(hass)
    assert await _hello_error(hass, hass_ws_client, token) is None
    assert await hass.config_entries.async_unload(again.entry_id)
    await hass.async_block_till_done()
    assert await _hello_error(hass, hass_ws_client, token) == "unknown_panel"


async def test_a_panel_added_again_loads_when_the_removed_list_cannot_be_saved(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    await _set_up(hass, _entry(hass, hass_read_only_user.id), CURRENT)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    again = _entry(hass, hass_read_only_user.id)

    with patch.object(Store, "async_save", side_effect=OSError):
        await _set_up(hass, again, CURRENT)

    assert again.state is ConfigEntryState.LOADED


async def test_a_removed_identity_two_loaded_entries_report_is_not_called_removed(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """An ambiguous identity is unknown, whatever was removed before."""
    await _set_up(hass, _entry(hass, hass_read_only_user.id), CURRENT)
    twin = MockConfigEntry(
        domain=DOMAIN, title="twin", data={CONF_ADDRESS: "twin.local"}, unique_id=DID
    )
    twin.add_to_hass(hass)
    twin.mock_state(hass, ConfigEntryState.LOADED)
    # A clone: the twin's own panel answers health with the same identity.
    twin.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(
            data=SimpleNamespace(health=HEALTH), last_update_success=True
        )
    )
    await hass.data[DOMAIN]["removed_panels"].async_add(DID)

    assert (
        await _hello_error(hass, hass_ws_client, hass_read_only_access_token)
        == "unknown_panel"
    )


async def test_the_removed_panels_keep_only_the_newest(hass: HomeAssistant) -> None:
    """Bounded, oldest dropped first."""
    removed = removed_panels.RemovedPanels(hass)
    dids = [f"{index:064x}" for index in range(removed_panels.MAX_REMOVED_PANELS + 1)]
    for did in dids:
        await removed.async_add(did)
    await removed.async_add(dids[1])

    reloaded = await _reloaded_store(hass)

    assert dids[0] not in reloaded
    assert all(did in reloaded for did in dids[1:])
    await reloaded.async_add("f" * 64)
    assert dids[2] not in await _reloaded_store(hass)
    assert dids[1] in await _reloaded_store(hass)


async def test_removing_an_entry_deletes_only_its_archives_and_receipts(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    from custom_components.panel_assistant.panel_backup import async_store_panel_backup

    entry = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, entry, CURRENT)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", '{"settings":{}}')
    own = await async_store_panel_backup(hass, entry.entry_id, 10, data.getvalue())
    neighbor = await async_store_panel_backup(hass, "neighbor", 10, data.getvalue())
    own_receipt = own.path.with_name(own.path.name + ".json")
    orphan = own.path.parent / f"{entry.entry_id}-orphan.zip.json"
    orphan.write_text("{}")
    unrelated = own.path.parent / f"{entry.entry_id}-notes.zip.keep"
    unrelated.write_text("keep")
    neighbor_receipt = neighbor.path.with_name(neighbor.path.name + ".json")
    before = (neighbor.path.read_bytes(), neighbor_receipt.read_bytes())
    assert own.path.exists() and own_receipt.exists()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not own.path.exists()
    assert not own_receipt.exists()
    assert not orphan.exists()
    assert unrelated.read_text() == "keep"
    assert (neighbor.path.read_bytes(), neighbor_receipt.read_bytes()) == before
    assert DID in await _reloaded_store(hass)


async def test_backup_cleanup_failure_keeps_removal_bookkeeping_and_neighbor_files(
    hass: HomeAssistant, hass_read_only_user: Any, caplog: Any
) -> None:
    from pathlib import Path

    from custom_components.panel_assistant.panel_backup import async_store_panel_backup

    entry = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, entry, CURRENT)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", '{"settings":{}}')
    own = await async_store_panel_backup(hass, entry.entry_id, 10, data.getvalue())
    neighbor = await async_store_panel_backup(hass, "neighbor", 10, data.getvalue())
    before = neighbor.path.read_bytes()
    unlink = Path.unlink

    def refuse_owned(path: Path, missing_ok: bool = False) -> None:
        if path.name.startswith(entry.entry_id + "-"):
            raise PermissionError("backup cleanup denied")
        unlink(path, missing_ok=missing_ok)

    with patch.object(Path, "unlink", refuse_owned):
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert DID in await _reloaded_store(hass)
    assert neighbor.path.read_bytes() == before
    assert own.path.exists()
    assert "backup cleanup denied" in caplog.text

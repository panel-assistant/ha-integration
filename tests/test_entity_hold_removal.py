"""A refresh finishing after entry removal must not leave a panel Repair."""

import asyncio
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    _failure_store,
    async_record_update_failure,
)

from .test_availability import _load_offline
from .test_management import _http_blocked
from .test_session_update import FakePanel
from .test_transport import DID


async def test_native_refresh_finishing_after_removal_leaves_no_repair(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    with _http_blocked():
        entry = await _load_offline(hass, hass_read_only_user.id, DID)
        coordinator = entry.runtime_data.coordinator
        panel = FakePanel(
            hass, hass_ws_client, hass_client, hass_read_only_access_token
        )
        await panel.connect()
        try:
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            await async_record_update_failure(
                hass, entry.entry_id, entry.title, "770", RuntimeError("interrupted")
            )
            panel.status["renderer"] = {
                "mode": "builtin",
                "state": "rendered",
                "rendered": True,
                "entity_hold_state": "released",
                "entity_hold": "learning",
                "entity_hold_age_ms": 95_000,
            }
            store = _failure_store(hass)
            saving = asyncio.Event()
            release_save = asyncio.Event()
            original_save = store.store.async_save

            async def save(data: Any) -> None:
                saving.set()
                await release_save.wait()
                await original_save(data)

            issue_id = f"dashboard_unfiltered_{entry.entry_id}"
            snapshots_before = panel.ops.count("snapshot")
            with patch.object(store.store, "async_save", save):
                poll = asyncio.create_task(coordinator.async_refresh())
                await asyncio.wait_for(saving.wait(), 5)
                remove = asyncio.create_task(
                    hass.config_entries.async_remove(entry.entry_id)
                )
                try:
                    async with asyncio.timeout(5):
                        # HA exposes no awaitable entry-removal notification.
                        while hass.config_entries.async_get_entry(  # noqa: ASYNC110
                            entry.entry_id
                        ):
                            await asyncio.sleep(0)
                    assert not remove.done()
                    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
                finally:
                    release_save.set()
                    await asyncio.wait_for(asyncio.gather(poll, remove), 5)

            assert hass.config_entries.async_get_entry(entry.entry_id) is None
            assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
            assert panel.ops.count("snapshot") == snapshots_before + 1
        finally:
            await panel.close()

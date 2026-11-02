"""A WebView Repair recorded while its panel entry is removed must not outlive it."""

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_failure_events,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.transport_protocol import (
    CAPABILITY_UI_SUBSCRIBE,
)

from . import test_session_update
from .test_availability import _load_offline
from .test_management import MANAGED, _http_blocked
from .test_session_update import FakePanel
from .test_transport import DID
from .test_webview import observation


@pytest.mark.parametrize("removed", [True, False])
async def test_webview_repair_is_not_recorded_for_an_entry_removed_mid_refresh(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    removed: bool,
) -> None:
    with _http_blocked():
        entry = await _load_offline(hass, hass_read_only_user.id, DID)
        coordinator = entry.runtime_data.coordinator
        panel = FakePanel(
            hass, hass_ws_client, hass_client, hass_read_only_access_token
        )
        # The panel can stream its log, so the Repair's report waits for it.
        with patch.object(
            test_session_update, "MANAGED", [*MANAGED, CAPABILITY_UI_SUBSCRIBE]
        ):
            await panel.connect()
        try:
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
            subscribing = asyncio.Event()
            release = asyncio.Event()

            async def subscribe(_args: dict[str, Any]) -> list[Any]:
                subscribing.set()
                await release.wait()
                return [("refused", "unknown_command", None)]

            panel.answers["subscribe"] = subscribe
            panel.status["webview"] = observation("deferred")
            poll = asyncio.create_task(coordinator.async_refresh())
            remove: asyncio.Task[Any] | None = None
            try:
                await asyncio.wait_for(subscribing.wait(), 5)
                if removed:
                    remove = asyncio.create_task(
                        hass.config_entries.async_remove(entry.entry_id)
                    )
                    async with asyncio.timeout(5):
                        # HA exposes no awaitable entry-removal notification.
                        while hass.config_entries.async_get_entry(  # noqa: ASYNC110
                            entry.entry_id
                        ):
                            await asyncio.sleep(0)
            finally:
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(poll, *([remove] if remove else [])), 5
                )
            await hass.async_block_till_done()

            issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
            events = await async_failure_events(hass, issue_id)
            if removed:
                assert hass.config_entries.async_get_entry(entry.entry_id) is None
                assert issue is None
                assert events == []
            else:
                assert issue is not None
                assert issue.translation_key == "installer_failure_update_webview"
                assert [event["kind"] for event in events] == ["update"]
        finally:
            await panel.close()

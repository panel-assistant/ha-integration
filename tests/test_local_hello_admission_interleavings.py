"""Local command admission stays current while install custody is read."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import install_jobs
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID
from custom_components.panel_assistant.identity import CONF_IDENTITY_PENDING
from custom_components.panel_assistant.transport_sessions import async_get_sessions

from .test_native import _setup, panel_patches
from .test_transport import OTHER_DID, WsClientFactory, _hello, _receive


@asynccontextmanager
async def _pending_hello(
    hass: HomeAssistant, client_factory: WsClientFactory, token: str, user_id: str
) -> AsyncIterator[tuple[Any, Any, asyncio.Event, asyncio.Event]]:
    """Hold only store I/O; websocket dispatch and admission run unchanged."""
    entry = await _setup(hass, user_id)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def read_jobs() -> list[Any]:
        entered.set()
        try:
            await release.wait()
            return []
        finally:
            finished.set()

    manager = SimpleNamespace(async_list=read_jobs)
    client = await client_factory(hass, token)
    with patch.object(
        install_jobs, "async_get_install_job_manager", AsyncMock(return_value=manager)
    ):
        await client.send_json(
            {"id": 1, **_hello(capabilities=["management", "local_adb"])}
        )
        async with asyncio.timeout(5):
            await entered.wait()
        try:
            yield entry, client, release, finished
        finally:
            release.set()
            await client.close()
            async with asyncio.timeout(5):
                await finished.wait()
            await hass.async_block_till_done()


@pytest.mark.parametrize(
    "change",
    [
        "healthy",
        "removed",
        "rekeyed",
        "rebound",
        "identity_pending",
        "installation_invalid",
        "identity_mismatch",
    ],
)
async def test_local_hello_rechecks_admission_after_store_read(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    change: str,
) -> None:
    async with _pending_hello(
        hass, hass_ws_client, hass_read_only_access_token, hass_read_only_user.id
    ) as (entry, client, release, finished):
        if change == "removed":
            with panel_patches():
                assert await hass.config_entries.async_remove(entry.entry_id)
        elif change == "rekeyed":
            hass.config_entries.async_update_entry(entry, unique_id=OTHER_DID)
        elif change == "identity_mismatch":
            entry.runtime_data.coordinator.identity_mismatch = True
        elif change != "healthy":
            key, value = {
                "rebound": (CONF_TRANSPORT_USER_ID, "another-user"),
                "identity_pending": (CONF_IDENTITY_PENDING, {"did": OTHER_DID}),
                "installation_invalid": ("installation_identity", False),
            }[change]
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, key: value}
            )
        release.set()
        response = await _receive(client)
        async with asyncio.timeout(5):
            await finished.wait()
        if change == "healthy":
            assert response["success"], response
            assert set(response["result"]["capabilities"]) == {
                "management",
                "local_adb",
            }
            assert async_get_sessions(hass).get(entry.entry_id) is not None
        else:
            assert response == {
                "id": 1,
                "type": "result",
                "success": False,
                "error": {
                    "code": "unknown_panel",
                    "message": "Panel admission changed.",
                },
            }
            assert async_get_sessions(hass).get(entry.entry_id) is None


@pytest.mark.parametrize("ending", ["unsubscribe", "close"])
async def test_local_hello_cancelled_on_wire_cannot_grant_after_store_read(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    ending: str,
) -> None:
    async with _pending_hello(
        hass, hass_ws_client, hass_read_only_access_token, hass_read_only_user.id
    ) as (entry, client, release, finished):
        if ending == "unsubscribe":
            await client.send_json(
                {"id": 2, "type": "unsubscribe_events", "subscription": 1}
            )
            assert (await _receive(client))["success"]
        else:
            await client.close()
        async with asyncio.timeout(5):
            await finished.wait()
        release.set()
        await hass.async_block_till_done()
        assert async_get_sessions(hass).get(entry.entry_id) is None
        if ending == "unsubscribe":
            await client.send_json({"id": 3, "type": "ping"})
            assert await _receive(client) == {"id": 3, "type": "pong"}


async def test_duplicate_wire_id_cannot_replace_pending_local_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
) -> None:
    async with _pending_hello(
        hass, hass_ws_client, hass_read_only_access_token, hass_read_only_user.id
    ) as (entry, client, release, _finished):
        await client.send_json(
            {"id": 1, **_hello(capabilities=["management", "local_adb"])}
        )
        refused = await _receive(client)
        assert not refused["success"]
        assert refused["error"]["code"] == "id_reuse"
        assert async_get_sessions(hass).get(entry.entry_id) is None
        release.set()
        original = await _receive(client)
        assert original["success"], original
        assert "local_adb" in original["result"]["capabilities"]
        assert async_get_sessions(hass).get(entry.entry_id) is not None

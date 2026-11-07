"""Registry renames while the panel owns an announcement and voice timers."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.assist_satellite.const import DATA_COMPONENT
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.const import __version__ as CORE_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .test_transport import WsClientFactory
from .test_voice import _ready, entry  # noqa: F401
from .test_voice_timers import (
    _announcement,
    _device,
    _nothing_announced,
    _played,
    _timer,
)

IN_PLACE = tuple(int(part) for part in CORE_VERSION.split(".")[:2]) >= (2026, 11)


async def test_rename_during_announcement_and_later_cleanup(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    component = hass.data[DATA_COMPONENT]
    satellite = component.get_entity(entity_id)
    registry = er.async_get(hass)
    registry_id = registry.async_get(entity_id).id

    def announce(target: str) -> asyncio.Task[Any]:
        return asyncio.create_task(
            hass.services.async_call(
                "assist_satellite",
                "announce",
                {
                    "entity_id": target,
                    "media_id": "/local/doorbell.mp3",
                    "preannounce": False,
                },
                blocking=True,
            )
        )

    for index in range(2):
        call = announce(entity_id)
        event = await _announcement(panel)
        renamed = f"assist_satellite.renamed_panel_{index}"
        changed = asyncio.Event()

        @callback
        def renamed_state(
            event: Any, target: str = renamed, signal: asyncio.Event = changed
        ) -> None:
            if (
                event.data["entity_id"] == target
                and event.data["new_state"] is not None
            ):
                signal.set()

        unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, renamed_state)
        try:
            registry.async_update_entity(entity_id, new_entity_id=renamed)
            async with asyncio.timeout(5):
                await changed.wait()
        finally:
            unsub()
        assert registry.async_get(renamed).id == registry_id
        assert component.get_entity(entity_id) is None
        assert component.get_entity(renamed) is satellite
        assert hass.states.get(entity_id) is None
        assert hass.states.get(renamed) is not None
        if IN_PLACE:
            # A rename must leave the existing service awaiting its panel answer.
            assert not call.done()
            await _played(panel, event)
            async with asyncio.timeout(5):
                await call
        else:
            # Older Core always removes/re-adds: removal settles the old call.
            with pytest.raises(HomeAssistantError) as raised:
                async with asyncio.timeout(5):
                    await call
            assert raised.value.translation_key == "voice_announcement_disconnected"
        entity_id = renamed

    # Services resolve the retained entity by its new ID after either lifecycle.
    call = announce(entity_id)
    event = await _announcement(panel)
    await _played(panel, event)
    async with asyncio.timeout(5):
        await call
    assert hass.states.get(entity_id).state == "idle"

    # A genuine unload must still fail in-flight work and remove the new ID.
    call = announce(entity_id)
    await _announcement(panel)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError) as raised:
        async with asyncio.timeout(5):
            await call
    assert raised.value.translation_key == "voice_announcement_disconnected"
    assert component.get_entity(entity_id) is None
    assert hass.states.get(entity_id).state == "unavailable"


async def test_timer_survives_repeated_renames_and_is_removed_on_unload(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
) -> None:
    panel, entity_id = await _ready(
        hass, hass_ws_client, hass_read_only_access_token, entry
    )
    device_id = _device(hass, entity_id)
    # The device reference and registered timer handler exist before renaming.
    await _timer(hass, device_id, seconds=60)
    for index in range(2):
        renamed = f"assist_satellite.timer_panel_{index}"
        er.async_get(hass).async_update_entity(entity_id, new_entity_id=renamed)
        await hass.async_block_till_done()
        entity_id = renamed
        assert _device(hass, entity_id) == device_id

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    event = await _announcement(panel)
    await _played(panel, event, outcome="stopped")
    await hass.async_block_till_done()
    # A duplicate handler or leftover ring must not produce a second announcement.
    await _nothing_announced(panel)
    assert hass.states.get(entity_id).state == "idle"

    await _timer(hass, device_id, seconds=60)
    assert await hass.config_entries.async_unload(entry.entry_id)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.data[DATA_COMPONENT].get_entity(entity_id) is None

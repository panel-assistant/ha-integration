"""Voice platform setup and the config entry's real unload/reload lifecycle."""

import asyncio
from typing import Any

import pytest
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.loader import Integration
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import assist_satellite
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.identity import CONF_INSTALL_IDENTITY

from .test_native import _setup, panel_patches
from .test_transport import WsClientFactory
from .test_voice import _connect, _satellite


@pytest.fixture
async def entry(hass: HomeAssistant, hass_read_only_user: Any) -> MockConfigEntry:
    """A loaded, bound entry, before its first voice-capable session."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = await _setup(hass, hass_read_only_user.id, native=None, described=None)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_INSTALL_IDENTITY: True}
    )
    return entry


@pytest.mark.parametrize("pending", ["import", "platform-setup", "startup-setup"])
async def test_reload_during_voice_setup_accepts_the_next_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    pending: str,
) -> None:
    """A dependency already in Core must not strand an entry on late setup."""
    assert await async_setup_component(hass, Platform.ASSIST_SATELLITE, {})
    setup_started = asyncio.Event()
    finish_setup = asyncio.Event()
    unload_started = asyncio.Event()
    reload_requested = asyncio.Event()
    get_platforms = Integration.async_get_platforms
    setup = assist_satellite.async_setup_entry

    async def delayed_platform_import(integration: Integration, platforms: Any) -> Any:
        if integration.domain == DOMAIN and Platform.ASSIST_SATELLITE in platforms:
            setup_started.set()
            await finish_setup.wait()
        return await get_platforms(integration, platforms)

    async def delayed_platform_setup(
        hass: HomeAssistant,
        entry: Any,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        setup_started.set()
        await finish_setup.wait()
        await setup(hass, entry, async_add_entities)

    @callback
    def entry_changed(_change: ConfigEntryChange, changed_entry: ConfigEntry) -> None:
        if (
            changed_entry is entry
            and entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS
        ):
            unload_started.set()

    async def reload_entry() -> bool:
        reload_requested.set()
        return await hass.config_entries.async_reload(entry.entry_id)

    if pending == "import":
        monkeypatch.setattr(Integration, "async_get_platforms", delayed_platform_import)
    else:
        monkeypatch.setattr(
            assist_satellite, "async_setup_entry", delayed_platform_setup
        )
    remove_listener = async_dispatcher_connect(
        hass, SIGNAL_CONFIG_ENTRY_CHANGED, entry_changed
    )
    try:
        async with asyncio.timeout(5):
            with panel_patches():
                initial_setup = None
                panel = None
                if pending == "startup-setup":
                    er.async_get(hass).async_get_or_create(
                        Platform.ASSIST_SATELLITE,
                        DOMAIN,
                        f"{entry.entry_id}_assist_satellite",
                        config_entry=entry,
                    )
                    initial_setup = hass.async_create_task(
                        hass.config_entries.async_reload(entry.entry_id)
                    )
                else:
                    panel = await _connect(
                        hass, hass_ws_client, hass_read_only_access_token
                    )
                await setup_started.wait()
                reload_task = hass.async_create_task(reload_entry())
                await reload_requested.wait()
                if pending == "import":
                    await unload_started.wait()
                finish_setup.set()
                if initial_setup is not None:
                    assert await initial_setup
                assert await reload_task
                await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        if panel is not None:
            await panel.client.close()
        replacement = await _connect(hass, hass_ws_client, hass_read_only_access_token)
        assert (await replacement.configure())["success"]
        await hass.async_block_till_done()
        entity_id = _satellite(hass, entry)
        assert entity_id is not None
        assert hass.states.get(entity_id).state == "idle"
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert hass.states.get(entity_id).state == "unavailable"
    finally:
        finish_setup.set()
        remove_listener()


@pytest.mark.parametrize("restored", [False, True], ids=["first-hello", "startup"])
async def test_failed_voice_setup_retries_on_the_next_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    restored: bool,
) -> None:
    """A failed satellite setup must leave the panel entry recoverable."""
    setup = assist_satellite.async_setup_entry
    fail_setup = True

    async def failing_setup(
        hass: HomeAssistant,
        entry: Any,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        if fail_setup:
            raise RuntimeError("Satellite dependency failed during setup")
        await setup(hass, entry, async_add_entities)

    monkeypatch.setattr(assist_satellite, "async_setup_entry", failing_setup)
    if restored:
        er.async_get(hass).async_get_or_create(
            Platform.ASSIST_SATELLITE,
            DOMAIN,
            f"{entry.entry_id}_assist_satellite",
            config_entry=entry,
        )
        with panel_patches():
            assert await hass.config_entries.async_reload(entry.entry_id)
    else:
        await _connect(hass, hass_ws_client, hass_read_only_access_token)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    failed_panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    await hass.async_block_till_done()
    refused = await failed_panel.send(
        {"type": "panel_assistant/voice_run", "session": failed_panel.token}
    )
    assert refused["error"]["code"] == "voice_unavailable"
    fail_setup = False
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    await hass.async_block_till_done()
    assert (await panel.configure())["success"]
    await hass.async_block_till_done()
    entity_id = _satellite(hass, entry)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "idle"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_satellite_stays_disabled_after_reload_and_unloads_cleanly(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled satellite loads once, even when a panel offers voice again."""
    setup = assist_satellite.async_setup_entry
    setup_attempts = 0

    async def count_platform_setups(
        hass: HomeAssistant,
        entry: Any,
        async_add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        nonlocal setup_attempts
        setup_attempts += 1
        await setup(hass, entry, async_add_entities)

    monkeypatch.setattr(assist_satellite, "async_setup_entry", count_platform_setups)
    satellite = er.async_get(hass).async_get_or_create(
        Platform.ASSIST_SATELLITE,
        DOMAIN,
        f"{entry.entry_id}_assist_satellite",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    with panel_patches():
        assert await hass.config_entries.async_reload(entry.entry_id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert "voice" in panel.result["capabilities"]
    await hass.async_block_till_done()
    assert setup_attempts == 1
    assert hass.states.get(satellite.entity_id) is None
    assert er.async_get(hass).async_get(satellite.entity_id).disabled_by is (
        er.RegistryEntryDisabler.USER
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED

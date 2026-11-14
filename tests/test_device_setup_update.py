"""An update the owner started owes the panel one device setup pass.

The update entity is driven as the owner presses Install; the pass is a
recording stand-in here (test_deferred_admin.py runs the real one). It leaves
the vendor setup in, so an update stopped before its own grant repair still
applies it. The LAN route's ordering after that repair is tested in
test_lan_staged_update.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import device_setup, update
from custom_components.panel_assistant.const import (
    CONF_DEVICE_SETUP,
    CONF_DEVICE_SETUP_OWED,
    DOMAIN,
)

from .test_update_adb_route import route  # noqa: F401

RECORD = {"did": "d" * 64, "package": "p", "version_code": 2, "root": False}


async def test_an_adb_update_runs_the_pass_once(
    hass: HomeAssistant,
    route: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", data={CONF_ADDRESS: "192.168.1.10"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=route.entity.coordinator)
    monkeypatch.setattr(route.entity._delivery, "async_deliver_adb", AsyncMock())
    attempt = AsyncMock(return_value=RECORD)
    monkeypatch.setattr(device_setup, "async_attempt", attempt)

    await route.entity._async_refresh_route()
    await route.entity.async_install(None, False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert attempt.await_count == 1
    assert attempt.await_args.kwargs == {}
    assert entry.data[CONF_DEVICE_SETUP] == RECORD
    assert CONF_DEVICE_SETUP_OWED not in entry.data


async def test_a_refused_update_owes_no_pass(
    hass: HomeAssistant,
    route: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a delivered build is an update; a failed one changes nothing."""
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", data={CONF_ADDRESS: "192.168.1.10"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=route.entity.coordinator)
    monkeypatch.setattr(
        route.entity._delivery,
        "async_deliver_adb",
        AsyncMock(side_effect=update.HomeAssistantError("refused")),
    )
    attempt = AsyncMock(return_value=RECORD)
    monkeypatch.setattr(device_setup, "async_attempt", attempt)

    await route.entity._async_refresh_route()
    with pytest.raises(update.HomeAssistantError):
        await route.entity.async_install(None, False)
    await hass.async_block_till_done(wait_background_tasks=True)

    attempt.assert_not_awaited()
    assert CONF_DEVICE_SETUP_OWED not in entry.data


async def test_an_update_stopped_during_its_webview_step_still_owes_the_pass(
    hass: HomeAssistant,
    route: SimpleNamespace,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app is delivered, so a restart while the WebView updates keeps it owed."""
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", data={CONF_ADDRESS: "192.168.1.10"}
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=route.entity.coordinator)
    monkeypatch.setattr(route.entity._delivery, "async_deliver_adb", AsyncMock())
    webview_started = asyncio.Event()

    async def webview_never_ends(*_args, **_kwargs):
        webview_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(update, "async_update_webview", webview_never_ends)
    attempt = AsyncMock(return_value=RECORD)
    monkeypatch.setattr(device_setup, "async_attempt", attempt)

    await route.entity._async_refresh_route()
    install = asyncio.ensure_future(route.entity.async_install(None, False))
    async with asyncio.timeout(5):
        await webview_started.wait()
    # A restart now ends Home Assistant here: the entry already says what is
    # owed, so its next setup raises the Repair.
    assert entry.data[CONF_DEVICE_SETUP_OWED] == device_setup.OWED_UPDATE
    attempt.assert_not_awaited()
    install.cancel()
    with pytest.raises(asyncio.CancelledError):
        await install
    await hass.async_block_till_done(wait_background_tasks=True)
    # Stopped before its grant repair: the pass it then runs includes the
    # vendor setup that repair would have applied.
    assert attempt.await_count == 1
    assert attempt.await_args.kwargs == {}

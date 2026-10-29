"""A local removal retry keeps custody of the originally identified panel."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.panel_assistant import local_adb, removal
from custom_components.panel_assistant.adb_protocol import AdbInstallTarget
from custom_components.panel_assistant.client import PanelAddress

from .test_removal import (
    PACKAGE_ID,
    VENDOR_HOME,
    FakePanel,
    _entry,
    _remove,
    panel,  # noqa: F401
)


@pytest.mark.parametrize("same_panel", [False, True], ids=["replacement", "original"])
async def test_local_retry_preserves_saved_panel_custody(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    same_panel: bool,
) -> None:
    fake, server = panel
    entry = _entry(hass, server)
    saved = {
        "did": entry.unique_id,
        "serial": fake.serial if same_panel else "ORIGINAL-PANEL",
        "model": "px30_evb",
        "primary_abi": "arm64-v8a",
        "android_sdk": 27,
    }
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, removal.CONF_REMOVAL_TARGET: saved}
    )
    original_data = deepcopy(dict(entry.data))
    target = AdbInstallTarget(
        PanelAddress("192.168.1.30", 8888),
        fake.serial,
        "px30_evb",
        "arm64-v8a",
        27,
        local_shell=object(),
    )
    local_target = AsyncMock(return_value=target)
    monkeypatch.setattr(local_adb, "async_target", local_target)

    async def hand_back(*_args: Any) -> None:
        await fake.hand_back_home(None)

    async def recover(*_args: Any) -> None:
        assert PACKAGE_ID in fake.installed
        assert fake.read()["home"] == VENDOR_HOME
        fake.adb_authorized = True

    handback = AsyncMock(side_effect=hand_back)
    recovery = AsyncMock(side_effect=recover)
    monkeypatch.setattr(local_adb, "async_hand_back_home", handback)
    monkeypatch.setattr(local_adb, "async_recover", recovery)

    result = await _remove(hass, entry)

    assert local_target.await_count > 0, "the options caller must select local ADB"
    if not same_panel:
        assert result.get("errors") == {"base": "removal_panel_changed"}, (
            "a local retry must refuse replacement hardware before changing custody"
        )
        assert dict(entry.data) == original_data, "saved removal custody must survive"
        assert fake.installed == [PACKAGE_ID]
        assert fake.hand_back_requests == 0
        handback.assert_not_awaited()
        recovery.assert_not_awaited()
        assert fake.read()["events"] == []
        assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "removed_entry_deleted"
        handback.assert_awaited_once()
        recovery.assert_awaited_once()
        assert fake.installed == []
        assert hass.config_entries.async_get_entry(entry.entry_id) is None

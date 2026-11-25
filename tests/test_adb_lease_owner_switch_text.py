"""What removal tells the owner when the panel app keeps ADB off.

The panel's own Network ADB switch works at once while the app's Keep ADB off
setting holds ADB off, so the text must point there and must not promise a
wait or say this Home Assistant decided it. With no switch on offer, it names
a restart and, for a hardened panel, Relaxed mode.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from custom_components.panel_assistant import removal
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)

from .test_adb_lease_session_lost import (
    MANAGED,
    SWITCH_NOW,
    _app_port,
    _entry,
    _lost_session,
)
from .test_removal import FakePanel, _open_form, panel  # noqa: F401


async def _removal_text(
    hass: HomeAssistant,
    server: TestServer,
    monkeypatch: pytest.MonkeyPatch,
    report: dict[str, Any],
    *,
    history: bool,
) -> str:
    entry = _entry(hass, server)
    if history:
        _lost_session(hass, entry)
    _app_port(monkeypatch, report)
    monkeypatch.setattr(
        removal,
        "async_probe_install_target",
        AsyncMock(return_value=InstallTargetProbe(InstallTargetState.ADB_UNREACHABLE)),
    )
    form = await _open_form(hass, entry)
    result = await hass.config_entries.options.async_configure(form["flow_id"], {})
    key = result["errors"]["base"]
    strings = await async_get_translations(hass, "en", "options", {"panel_assistant"})
    return strings[f"component.panel_assistant.options.error.{key}"].format(
        **result.get("description_placeholders") or {}
    )


async def test_owner_switch_that_works_now_is_offered_without_a_wait(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake, server = panel
    text = await _removal_text(hass, server, monkeypatch, SWITCH_NOW, history=False)

    assert "turn on Network ADB" in text
    assert "allows this now" in text
    assert "Keep ADB off" in text
    assert "a few minutes" not in text
    assert "manages the panel" not in text


async def test_no_owner_switch_names_a_restart_and_relaxed_mode(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake, server = panel
    text = await _removal_text(hass, server, monkeypatch, MANAGED, history=True)

    assert "restart the panel" in text
    assert "Relaxed mode" in text
    assert "manages the panel" not in text

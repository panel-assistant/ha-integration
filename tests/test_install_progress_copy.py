"""The install progress dialog tells the owner how to unblock a waiting install."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations

from custom_components.panel_assistant.config_flow import HaPaneldConfigFlow
from custom_components.panel_assistant.install_jobs import InstallPhase
from tests.test_config_flow import _executor_for, _receipt


async def test_install_progress_tells_owner_to_answer_security_check(
    hass: HomeAssistant,
) -> None:
    """A Play Protect prompt holds the ADB install; the dialog says to answer it."""
    receipt = _receipt(InstallPhase.DOWNLOADING)
    gate = asyncio.Event()
    worker = hass.async_create_task(gate.wait())

    async def _wait(_job_id: str, *, until_webview: bool = False) -> Any:
        await asyncio.shield(worker)
        return receipt

    executor = _executor_for()
    executor.async_ensure_job.return_value = worker
    executor.async_wait.side_effect = _wait
    executor.is_updating_webview.return_value = False
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = "flow-security-check"
    with patch(
        "custom_components.panel_assistant.config_flow.async_get_install_executor",
        AsyncMock(return_value=executor),
    ):
        shown = await flow._async_show_install_progress(receipt)
    gate.set()
    await worker

    assert shown["type"] is FlowResultType.SHOW_PROGRESS
    action = shown["progress_action"]
    assert action == "installing"
    translations = await async_get_translations(
        hass, "en", "config", {"panel_assistant"}
    )
    text = translations[f"component.panel_assistant.config.progress.{action}"]
    assert '"Send app for a security check?"' in text
    assert "tap one of its buttons on the panel" in text
    assert "waits until you do" in text

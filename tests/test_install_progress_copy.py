"""The install progress dialog tells the owner how to unblock a waiting install."""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations

from custom_components.panel_assistant.config_flow import HaPaneldConfigFlow
from custom_components.panel_assistant.install_jobs import InstallPhase
from custom_components.panel_assistant.repairs import InstallerFailureFlow
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


_ENGLISH = json.loads(
    (
        Path(__file__).parents[1]
        / "custom_components/panel_assistant/translations/en.json"
    ).read_text(encoding="utf-8")
)
_RETRYING_ISSUES = sorted(
    key
    for key, issue in _ENGLISH["issues"].items()
    if key.startswith("installer_failure")
    and "retrying" in issue.get("fix_flow", {}).get("progress", {})
)


def test_every_installer_failure_retry_progress_is_covered() -> None:
    """The parametrized list below cannot silently shrink to nothing."""
    assert len(_RETRYING_ISSUES) >= 14


@pytest.mark.parametrize("issue_key", _RETRYING_ISSUES)
async def test_repair_retry_progress_tells_owner_to_answer_security_check(
    hass: HomeAssistant, issue_key: str
) -> None:
    """A Repair retry re-runs the ADB install, so it carries the same hint."""
    gate = asyncio.Event()
    retry = hass.async_create_task(gate.wait())
    flow = InstallerFailureFlow()
    flow.hass = hass
    flow.issue_id = issue_key
    flow.flow_id = f"repair-{issue_key}"
    flow._retry_task = retry

    shown = await flow.async_step_retry_progress()
    gate.set()
    await retry

    assert shown["type"] is FlowResultType.SHOW_PROGRESS
    action = shown["progress_action"]
    assert action == "retrying"
    translations = await async_get_translations(
        hass, "en", "issues", {"panel_assistant"}
    )
    text = translations[
        f"component.panel_assistant.issues.{issue_key}.fix_flow.progress.{action}"
    ]
    assert '"Send app for a security check?"' in text
    assert "tap one of its buttons on the panel" in text
    assert "retry waits until you do" in text

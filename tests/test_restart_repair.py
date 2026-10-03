"""The Repairs issue that asks for a restart after a new build is copied in."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.panel_assistant import restart_repair
from custom_components.panel_assistant.const import (
    DOMAIN,
    INTEGRATION_BUILD,
    INTEGRATION_VERSION,
)
from custom_components.panel_assistant.restart_repair import (
    ISSUE_RESTART_REQUIRED,
    async_check_restart_needed,
    read_installed_build,
    restart_pending,
)

LOADED = ("0.7.0-rc1", 267)


def _install(root: Path, version: str, build: int) -> Path:
    """Write the two files a copied-in build carries."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps({"domain": DOMAIN, "version": version}), encoding="utf-8"
    )
    (root / "const.py").write_text(
        f'DOMAIN = "{DOMAIN}"\nINTEGRATION_BUILD = {build}\n', encoding="utf-8"
    )
    return root


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_RESTART_REQUIRED)


async def _check(hass: HomeAssistant, root: Path) -> None:
    await async_check_restart_needed(
        hass, lambda: read_installed_build(root), loaded=LOADED
    )


def test_the_shipped_files_describe_the_loaded_build() -> None:
    """Without this, every install would ask for a restart it does not need."""
    assert read_installed_build() == (INTEGRATION_VERSION, INTEGRATION_BUILD)


def test_a_half_copied_build_is_unknown(tmp_path: Path) -> None:
    root = _install(tmp_path, "0.7.0", 270)
    (root / "manifest.json").write_text("{", encoding="utf-8")
    assert read_installed_build(root) is None
    (root / "manifest.json").unlink()
    assert read_installed_build(root) is None


async def test_a_newer_build_on_disk_asks_for_a_restart(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    await _check(hass, _install(tmp_path, "0.7.0", 270))

    issue = _issue(hass)
    assert issue is not None
    assert issue.is_fixable
    assert not issue.is_persistent
    assert issue.translation_key == ISSUE_RESTART_REQUIRED
    assert issue.translation_placeholders == {
        "loaded": "0.7.0-rc1 (build 267)",
        "installed": "0.7.0 (build 270)",
    }
    assert restart_pending(hass) == {
        "loaded_version": "0.7.0-rc1",
        "loaded_build": 267,
        "installed_version": "0.7.0",
        "installed_build": 270,
    }


async def test_a_new_build_with_the_same_version_still_asks(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    await _check(hass, _install(tmp_path, LOADED[0], LOADED[1] + 1))
    assert _issue(hass) is not None


async def test_matching_files_clear_the_issue(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    await _check(hass, _install(tmp_path, "0.7.0", 270))
    assert _issue(hass) is not None

    await _check(hass, _install(tmp_path, *LOADED))

    assert _issue(hass) is None
    assert restart_pending(hass) is None


async def test_an_unreadable_build_neither_raises_nor_clears(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    root = _install(tmp_path, "0.7.0", 270)
    await _check(hass, root)
    (root / "const.py").unlink()

    await _check(hass, root)

    assert _issue(hass) is not None


async def test_repeated_checks_raise_the_issue_once(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    real = ir.async_create_issue

    def _record(*args: Any, **kwargs: Any) -> None:
        created.append(args[2])
        real(*args, **kwargs)

    monkeypatch.setattr(restart_repair.ir, "async_create_issue", _record)
    root = _install(tmp_path, "0.7.0", 270)
    for _ in range(3):
        await _check(hass, root)
    assert created == [ISSUE_RESTART_REQUIRED]

    # A further build replaces the issue's words rather than adding another.
    await _check(hass, _install(tmp_path, "0.7.0", 271))
    assert created == [ISSUE_RESTART_REQUIRED] * 2
    assert len([key for key in ir.async_get(hass).issues if key[0] == DOMAIN]) == 1
    issue = _issue(hass)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["installed"] == "0.7.0 (build 271)"


async def test_files_are_read_only_in_the_executor(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    root = _install(tmp_path, "0.7.0", 270)
    threads: list[threading.Thread] = []

    def _reader() -> tuple[str, int] | None:
        threads.append(threading.current_thread())
        return read_installed_build(root)

    await async_check_restart_needed(hass, _reader, loaded=LOADED)

    assert threads
    assert threading.main_thread() not in threads


async def test_installing_a_new_build_shows_the_repair_within_one_second(
    hass: HomeAssistant,
    hass_ws_client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _install(tmp_path, INTEGRATION_VERSION, INTEGRATION_BUILD)
    monkeypatch.setattr(restart_repair, "INTEGRATION_ROOT", root)
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done(wait_background_tasks=True)
    client = await hass_ws_client(hass)

    async def visible_issues(request_id: int) -> list[dict[str, Any]]:
        await client.send_json({"id": request_id, "type": "repairs/list_issues"})
        response = await client.receive_json()
        assert response["success"]
        return [
            issue
            for issue in response["result"]["issues"]
            if issue["domain"] == DOMAIN and issue["issue_id"] == ISSUE_RESTART_REQUIRED
        ]

    assert await visible_issues(1) == []
    _install(root, INTEGRATION_VERSION, INTEGRATION_BUILD + 1)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)

    issues = await visible_issues(2)
    assert len(issues) == 1
    assert issues[0]["translation_placeholders"] == {
        "loaded": f"{INTEGRATION_VERSION} (build {INTEGRATION_BUILD})",
        "installed": f"{INTEGRATION_VERSION} (build {INTEGRATION_BUILD + 1})",
    }

    _install(root, INTEGRATION_VERSION, INTEGRATION_BUILD)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await visible_issues(3) == []


async def test_the_fix_restarts_home_assistant(
    hass: HomeAssistant, hass_client: Any, tmp_path: Path
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    restarts = async_mock_service(hass, "homeassistant", "restart")
    await _check(hass, _install(tmp_path, "0.7.0", 270))
    client = await hass_client()

    response = await client.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": ISSUE_RESTART_REQUIRED},
    )
    assert response.status == 200
    form = await response.json()
    assert form["step_id"] == "confirm_restart"
    assert form["description_placeholders"] == {
        "loaded": "0.7.0-rc1 (build 267)",
        "installed": "0.7.0 (build 270)",
    }
    assert restarts == []

    response = await client.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert len(restarts) == 1

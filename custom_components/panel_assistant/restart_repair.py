"""Ask for a restart when a different Panel Assistant build is on disk.

A new build is copied into Home Assistant without restarting it, so the code
running is the build that was loaded at startup. This compares that build with
the files on disk now and raises one Repairs issue while they differ.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, INTEGRATION_BUILD, INTEGRATION_VERSION

_LOGGER = logging.getLogger(__name__)

ISSUE_RESTART_REQUIRED = "restart_required"
DATA_RESTART_CHECK = "restart_check"
CHECK_INTERVAL = timedelta(seconds=1)
INTEGRATION_ROOT = Path(__file__).parent

_BUILD = re.compile(r"^INTEGRATION_BUILD\s*=\s*(\d+)\s*$", re.MULTILINE)

type Build = tuple[str, int]


def read_installed_build(root: Path | None = None) -> Build | None:
    """Return the version and build the files under root describe.

    None when they cannot be read, as while an update is still being copied:
    an unknown build neither raises nor clears the issue.
    """
    root = root or INTEGRATION_ROOT
    try:
        version = json.loads((root / "manifest.json").read_text(encoding="utf-8"))[
            "version"
        ]
        match = _BUILD.search((root / "const.py").read_text(encoding="utf-8"))
    except OSError, ValueError, KeyError, TypeError:
        return None
    if not isinstance(version, str) or match is None:
        return None
    return version, int(match.group(1))


def _label(build: Build) -> str:
    return f"{build[0]} (build {build[1]})"


async def async_check_restart_needed(
    hass: HomeAssistant,
    reader: Callable[[], Build | None] | None = None,
    loaded: Build = (INTEGRATION_VERSION, INTEGRATION_BUILD),
) -> None:
    """Raise or clear the restart issue from one read of the files on disk."""
    installed = await hass.async_add_executor_job(reader or read_installed_build)
    if installed is None:
        return
    state = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_RESTART_CHECK, {})
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, ISSUE_RESTART_REQUIRED)
    if installed == loaded:
        state["pending"] = None
        if issue is not None:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_RESTART_REQUIRED)
        return
    placeholders = {"loaded": _label(loaded), "installed": _label(installed)}
    state["pending"] = {
        "loaded_version": loaded[0],
        "loaded_build": loaded[1],
        "installed_version": installed[0],
        "installed_build": installed[1],
    }
    if issue is not None and issue.translation_placeholders == placeholders:
        return
    _LOGGER.info(
        "Panel Assistant %s is installed but %s is running; restart Home Assistant",
        placeholders["installed"],
        placeholders["loaded"],
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_RESTART_REQUIRED,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RESTART_REQUIRED,
        translation_placeholders=placeholders,
    )


def restart_pending(hass: HomeAssistant) -> dict[str, Any] | None:
    """Return the builds a pending restart would swap, or None when none is."""
    state = hass.data.get(DOMAIN, {}).get(DATA_RESTART_CHECK, {})
    pending: dict[str, Any] | None = state.get("pending")
    return pending


async def async_setup_restart_check(hass: HomeAssistant) -> None:
    """Check now and every CHECK_INTERVAL, once per Home Assistant run."""
    state = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_RESTART_CHECK, {})
    if "cancel" in state:
        return

    async def _async_tick(_now: datetime) -> None:
        await async_check_restart_needed(hass)

    cancel = async_track_time_interval(
        hass, _async_tick, CHECK_INTERVAL, name=f"{DOMAIN} restart check"
    )
    state["cancel"] = cancel

    @callback
    def _async_stop(_event: Event) -> None:
        cancel()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    await async_check_restart_needed(hass)


class RestartRequiredFlow(RepairsFlow):
    """Restart Home Assistant once the person confirms."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm_restart()

    async def async_step_confirm_restart(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            await self.hass.services.async_call(
                "homeassistant", "restart", blocking=False
            )
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
        placeholders = (issue.translation_placeholders if issue else None) or {}
        return self.async_show_form(
            step_id="confirm_restart",
            data_schema=vol.Schema({}),
            description_placeholders={
                "loaded": placeholders.get("loaded", ""),
                "installed": placeholders.get("installed", ""),
            },
        )

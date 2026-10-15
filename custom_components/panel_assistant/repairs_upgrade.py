"""Repairs that say what to install first when a panel and Panel Assistant disagree.

Panel Assistant serves one native protocol (``PROTOCOL_MIN``..``PROTOCOL_MAX``).
A panel whose app speaks an older one is refused at hello, so its entities read
unavailable; its health line still answers, and says which protocol it speaks,
so its entry is told the app it needs (``panel_app_too_old``). The update entity
already offers that app, installed over ADB, which needs no session.

A panel whose entities were never moved off MQTT cannot be moved by this
release: Panel Assistant 0.9.0 was the last that could. Setting its entry up
would create a second set of entities beside the MQTT ones and split their
history, so setup refuses it and says to install 0.9.0 first
(``mqtt_panel_needs_090``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .client import PanelHealth
from .const import (
    CONF_CUTOVER,
    DOMAIN,
    REQUIRED_APP_VERSION,
)
from .device import panel_display_name
from .panel_move import move_issue_id, runs_old_app
from .transport_sessions import async_get_sessions
from .update_policy import overlaps

ISSUE_PANEL_APP_TOO_OLD: Final = "panel_app_too_old"
ISSUE_MQTT_PANEL_NEEDS_090: Final = "mqtt_panel_needs_090"
# The device registry's name for MQTT, and how the app named its MQTT device.
_MQTT_DOMAIN: Final = "mqtt"
_CUTOVER_COMPLETE: Final = "complete"


def app_too_old_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID saying one entry's panel needs a newer app."""
    return f"{ISSUE_PANEL_APP_TOO_OLD}_{entry_id}"


def needs_090_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID saying one entry was never moved off MQTT."""
    return f"{ISSUE_MQTT_PANEL_NEEDS_090}_{entry_id}"


def speaks_this_protocol(health: PanelHealth) -> bool:
    """Return whether the app's health says it speaks this protocol."""
    return health.protocol is not None and overlaps(*health.protocol)


@callback
def async_evaluate_app_too_old(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise or clear ``panel_app_too_old`` from the latest health read.

    A live session means the hello was accepted, so the app speaks this
    protocol. A failed read keeps the last health, so it changes nothing.
    A panel still on the old app id has its own Repair, which says what to
    install first; two Repairs asking for one install would read as two jobs.
    """
    issue_id = app_too_old_issue_id(entry.entry_id)
    if async_get_sessions(hass).get(entry.entry_id) is not None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if coordinator is None or coordinator.data is None:
        return
    health: PanelHealth = coordinator.data.health
    if (
        speaks_this_protocol(health)
        or runs_old_app(health)
        or ir.async_get(hass).async_get_issue(DOMAIN, move_issue_id(entry.entry_id))
        is not None
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PANEL_APP_TOO_OLD,
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "version": health.version,
            "required_version": REQUIRED_APP_VERSION,
        },
    )


def _mqtt_device(hass: HomeAssistant, panel_id: str) -> dr.DeviceEntry | None:
    """Return the panel's MQTT device, whichever MQTT config entry owns it.

    Only Home Assistant's device registry is read; nothing of its MQTT
    integration is imported.
    """
    registry = dr.async_get(hass)
    identifier = (_MQTT_DOMAIN, f"ha-paneld-{panel_id}")
    for mqtt_entry in hass.config_entries.async_entries(_MQTT_DOMAIN):
        device = registry.async_get_device_by_identifier(
            identifier, mqtt_entry.entry_id
        )
        if device is not None:
            return device
    return None


def needs_090(
    hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth | None
) -> bool:
    """Return whether this entry's panel still has entities on MQTT.

    True when MQTT holds a device for the panel, under the panel ID the move
    record names or the one its health reports now, and Panel Assistant 0.9.0
    never completed the move. Without either panel ID nothing can be looked up.
    """
    record = entry.data.get(CONF_CUTOVER)
    if not isinstance(record, Mapping):
        record = {}
    if record.get("state") == _CUTOVER_COMPLETE:
        return False
    panel_ids = {
        panel_id
        for panel_id in (record.get("panel_id"), getattr(health, "panel_id", None))
        if isinstance(panel_id, str) and panel_id
    }
    return any(_mqtt_device(hass, panel_id) is not None for panel_id in panel_ids)


@callback
def async_raise_needs_090(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Say that this entry's panel must be moved off MQTT by 0.9.0 first."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        needs_090_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_MQTT_PANEL_NEEDS_090,
        translation_placeholders={"panel": panel_display_name(hass, entry)},
    )


@callback
def async_delete_upgrade_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw both of an entry's upgrade Repairs."""
    ir.async_delete_issue(hass, DOMAIN, app_too_old_issue_id(entry_id))
    ir.async_delete_issue(hass, DOMAIN, needs_090_issue_id(entry_id))

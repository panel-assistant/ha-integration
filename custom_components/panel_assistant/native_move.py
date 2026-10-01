"""Ask every panel still on MQTT to move to Panel Assistant's own connection.

MQTT support ends after this release line, and so does the only way to move a
panel's entities off it. So a panel whose entities MQTT still controls (the
``mqtt`` and ``shadow`` authorities) gets a Repairs issue whose fix does
exactly what choosing Panel Assistant under Configure, Control does: it saves
the native authority, and the entry's reload runs the cutover. Only a panel
that has really been an MQTT panel here is asked: one that never used MQTT
would only be confused by the offer.

A panel whose ha-paneld predates the MQTT withdrawal would announce its MQTT
entities again after the move, so it is asked to update first instead. A panel
whose version is not known yet, offline since Home Assistant started, is asked
nothing until it reports one. A cutover that failed already has its own issue.

Everything here exists only while MQTT does, and goes with it.
"""

from __future__ import annotations

from typing import Any, Final

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_AUTHORITY, DOMAIN, PANEL_MQTT_WITHDRAW_VERSION
from .device import panel_display_name
from .guards import (
    _panel_ids,
    mqtt_device,
    panel_follows_mqtt_withdrawal,
    reported_panel_version,
)
from .transport import (
    AUTHORITY_MQTT,
    AUTHORITY_NATIVE,
    ISSUE_CUTOVER_INCOMPLETE,
    authority_options,
    cutover_issue_id,
    cutover_record,
    effective_authority,
    native_entities_turned_off,
)

ISSUE_MOVE_TO_NATIVE: Final = "move_to_native_connection"
ISSUE_UPDATE_BEFORE_MOVE: Final = "update_before_native_move"
ISSUE_DATA_ENTRY_ID: Final = "entry_id"

ABORT_ENTRY_REMOVED: Final = "entry_removed"
ABORT_UPDATE_FIRST: Final = "update_first"


def move_to_native_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID offering one entry's move."""
    return cutover_issue_id(ISSUE_MOVE_TO_NATIVE, entry_id)


def update_before_move_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID asking for one entry's panel to be updated."""
    return cutover_issue_id(ISSUE_UPDATE_BEFORE_MOVE, entry_id)


@callback
def async_delete_native_move_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw both of an entry's move issues."""
    ir.async_delete_issue(hass, DOMAIN, move_to_native_issue_id(entry_id))
    ir.async_delete_issue(hass, DOMAIN, update_before_move_issue_id(entry_id))


def has_mqtt_history(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return whether this panel has ever been an MQTT panel here.

    MQTT knows a device for it, under the panel ID it reports or the one a
    cutover recorded, or MQTT was chosen outright. A panel added natively,
    left at the shadow default and never announced over MQTT, has nothing to
    move, and an offer to move it would only confuse someone who never used
    MQTT.
    """
    if entry.options.get(CONF_AUTHORITY) == AUTHORITY_MQTT:
        return True
    return any(
        mqtt_device(hass, panel_id) is not None
        for panel_id in _panel_ids(entry, dict(cutover_record(entry) or {}))
    )


def _nothing_to_offer(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Return whether there is nothing to offer: never on MQTT, native
    already, or a cutover that failed, whose own issue says how it carries on.
    """
    return (
        not has_mqtt_history(hass, entry)
        or effective_authority(hass, entry) == AUTHORITY_NATIVE
        or ir.async_get(hass).async_get_issue(
            DOMAIN, cutover_issue_id(ISSUE_CUTOVER_INCOMPLETE, entry.entry_id)
        )
        is not None
    )


@callback
def async_evaluate_native_move(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise, swap or clear the entry's move issue to match the panel now."""
    if native_entities_turned_off(hass) or _nothing_to_offer(hass, entry):
        async_delete_native_move_issues(hass, entry.entry_id)
        return
    follows = panel_follows_mqtt_withdrawal(hass, entry)
    if follows is None:
        return
    panel = panel_display_name(hass, entry)
    if follows:
        ir.async_delete_issue(hass, DOMAIN, update_before_move_issue_id(entry.entry_id))
        ir.async_create_issue(
            hass,
            DOMAIN,
            move_to_native_issue_id(entry.entry_id),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MOVE_TO_NATIVE,
            translation_placeholders={"panel": panel},
            data={ISSUE_DATA_ENTRY_ID: entry.entry_id},
        )
        return
    ir.async_delete_issue(hass, DOMAIN, move_to_native_issue_id(entry.entry_id))
    ir.async_create_issue(
        hass,
        DOMAIN,
        update_before_move_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UPDATE_BEFORE_MOVE,
        translation_placeholders={
            "panel": panel,
            "version": reported_panel_version(hass, entry) or "",
            "required_version": PANEL_MQTT_WITHDRAW_VERSION,
        },
    )


class NativeMoveFlow(RepairsFlow):
    """Move one panel to Panel Assistant's own connection on confirmation."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm_move()

    async def async_step_confirm_move(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason=ABORT_ENTRY_REMOVED)
        if panel_follows_mqtt_withdrawal(self.hass, entry) is False:
            return self.async_abort(
                reason=ABORT_UPDATE_FIRST,
                description_placeholders={
                    "panel": panel_display_name(self.hass, entry),
                    "required_version": PANEL_MQTT_WITHDRAW_VERSION,
                },
            )
        if user_input is not None:
            # What saving Panel Assistant under Configure, Control saves; the
            # entry's update listener reloads it and the cutover runs.
            self.hass.config_entries.async_update_entry(
                entry, options=authority_options(entry, AUTHORITY_NATIVE)
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm_move",
            data_schema=vol.Schema({}),
            description_placeholders={"panel": panel_display_name(self.hass, entry)},
        )

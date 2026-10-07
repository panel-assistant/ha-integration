"""Move every panel still on MQTT to Panel Assistant's own connection.

MQTT support ends after this release line, and so does the only way to move a
panel's entities off it. So a panel whose entities MQTT still controls (the
``mqtt`` and ``shadow`` authorities) is moved without asking: Panel Assistant
saves the native authority, exactly as choosing Panel Assistant under
Configure, Control does, and the cutover moves its entities, keeping their
entity IDs and history. Only a panel that has really been an MQTT panel here
is moved; one that never used MQTT has nothing to move.

A panel whose ha-paneld predates the MQTT withdrawal would announce its MQTT
entities again after the move, so it is asked to update first instead. A panel
whose version is not known yet, offline since Home Assistant started, is left
until it reports one. A cutover that failed already has its own issue.

An earlier release offered the move as a fixable Repairs issue; any such
issue is withdrawn here. Everything here exists only while MQTT does, and
goes with it.
"""

from __future__ import annotations

import logging
from typing import Final

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

_LOGGER = logging.getLogger(__name__)

ISSUE_MOVE_TO_NATIVE: Final = "move_to_native_connection"
ISSUE_UPDATE_BEFORE_MOVE: Final = "update_before_native_move"


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
    """Move the panel to native now, or say why it cannot move yet.

    The move saves the entry's options, as the options flow's Control step
    does: the update listener reloads the entry and the cutover runs.
    """
    if native_entities_turned_off(hass) or _nothing_to_offer(hass, entry):
        async_delete_native_move_issues(hass, entry.entry_id)
        return
    follows = panel_follows_mqtt_withdrawal(hass, entry)
    if follows is None:
        return
    if follows:
        async_delete_native_move_issues(hass, entry.entry_id)
        _LOGGER.info(
            "Moving %s from MQTT to Panel Assistant's own connection",
            panel_display_name(hass, entry),
        )
        hass.config_entries.async_update_entry(
            entry, options=authority_options(entry, AUTHORITY_NATIVE)
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
            "panel": panel_display_name(hass, entry),
            "version": reported_panel_version(hass, entry) or "",
            "required_version": PANEL_MQTT_WITHDRAW_VERSION,
        },
    )

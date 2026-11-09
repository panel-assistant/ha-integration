"""Say so when a panel answers polls but never connects to Home Assistant.

A panel whose sign-in never finished answers its health, so it looks online,
yet it has no session: its device card stays at the static entities and its
sidebar page never loads. Nothing else would tell the owner why.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .device import panel_display_name
from .foreign_ha import foreign_ha_issue_id

ISSUE_NOT_SIGNED_IN = "not_signed_in"


def not_signed_in_issue_id(entry_id: str) -> str:
    """One Repair for each added panel."""
    return f"{ISSUE_NOT_SIGNED_IN}_{entry_id}"


@callback
def async_delete_not_signed_in_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the Repair once the panel connects, or its entry unloads."""
    ir.async_delete_issue(hass, DOMAIN, not_signed_in_issue_id(entry_id))


@callback
def async_evaluate_not_signed_in(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise or clear the Repair from the coordinator's latest answer."""
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if (
        coordinator is None
        or not coordinator.not_signed_in
        # A panel signed in to another Home Assistant already has its Repair.
        or ir.async_get(hass).async_get_issue(
            DOMAIN, foreign_ha_issue_id(entry.entry_id)
        )
        is not None
    ):
        async_delete_not_signed_in_issue(hass, entry.entry_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        not_signed_in_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NOT_SIGNED_IN,
        learn_more_url=help_url("repair", issue=ISSUE_NOT_SIGNED_IN),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "panel_page": f"{coordinator.client.address.base_url}/",
        },
        data={"entry_id": entry.entry_id},
    )

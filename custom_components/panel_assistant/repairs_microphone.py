"""Say why a panel's voice assistant is not listening after its microphone check."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from .voice import VoiceConfiguration

ISSUE_VOICE_MICROPHONE = "voice_microphone"
ISSUE_MICROPHONE_SILENT = "voice_microphone_silent"
ISSUE_MICROPHONE_NO_AUDIO = "voice_microphone_no_audio"
ISSUE_MICROPHONE_NO_AUDIO_DETAIL = "voice_microphone_no_audio_detail"


def microphone_issue_id(entry_id: str) -> str:
    """One microphone issue per configured panel, whatever the check found."""
    return f"{ISSUE_VOICE_MICROPHONE}_{entry_id}"


def async_reconcile_microphone_issue(
    hass: HomeAssistant, entry: ConfigEntry, voice: VoiceConfiguration
) -> None:
    """Raise the issue while a failed check keeps the panel from listening."""
    issue_id = microphone_issue_id(entry.entry_id)
    microphone = voice.microphone
    check = None if microphone is None or not voice.enabled else microphone.check
    if check not in ("silent", "no_audio"):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    from .device import panel_display_name

    placeholders = {"panel": panel_display_name(hass, entry)}
    if check == "silent":
        translation_key = ISSUE_MICROPHONE_SILENT
    elif microphone is not None and microphone.detail:
        translation_key = ISSUE_MICROPHONE_NO_AUDIO_DETAIL
        placeholders["detail"] = microphone.detail
    else:
        translation_key = ISSUE_MICROPHONE_NO_AUDIO
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=translation_key,
        translation_placeholders=placeholders,
        data={"entry_id": entry.entry_id},
    )


def async_delete_microphone_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the issue of a panel that is unloaded or removed."""
    ir.async_delete_issue(hass, DOMAIN, microphone_issue_id(entry_id))

"""Keep missing supported grants separate from successful APK installation."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .status import PERMISSION_NAMES, ComponentStatus, PanelStatus
from .transport import async_get_sessions

ISSUE_PANEL_PERMISSIONS = "panel_permissions"


def permission_issue_id(entry_id: str) -> str:
    """Keep commissioning attached to the configured panel across address changes."""
    return f"{ISSUE_PANEL_PERMISSIONS}_{entry_id}"


def permission_observations(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> ComponentStatus | None:
    """Ignore dormant media grants only when current feature state proves off."""
    if status.permissions is None:
        return None
    permissions = dict(status.permissions)
    camera_off = status.camera is not None and status.camera.get("state") in (
        "disabled",
        "absent",
    )
    session = async_get_sessions(hass).get(entry.entry_id)
    voice_off = (
        session is not None
        and session.did == entry.unique_id
        and session.voice is not None
        and session.voice.enabled is False
    )
    if camera_off:
        permissions["camera"] = "not_required"
        if voice_off:
            permissions["microphone"] = "not_required"
    return permissions


def permissions_held(permissions: ComponentStatus | None) -> bool:
    """Require complete readback for every grant relevant to current feature use."""
    return (
        permissions is not None
        and set(permissions) == PERMISSION_NAMES
        and all(state in ("held", "not_required") for state in permissions.values())
    )


def async_reconcile_permission_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Use accepted, fresh grant observations; unknown readings retain the issue."""
    issue_id = permission_issue_id(entry.entry_id)
    permissions = permission_observations(hass, entry, status)
    if permissions is not None and "missing" in permissions.values():
        from .device import panel_display_name

        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PANEL_PERMISSIONS,
            translation_placeholders={"panel": panel_display_name(hass, entry)},
            data={"entry_id": entry.entry_id},
        )
    elif permissions_held(permissions):
        ir.async_delete_issue(hass, DOMAIN, issue_id)

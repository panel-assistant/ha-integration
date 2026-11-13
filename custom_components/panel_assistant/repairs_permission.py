"""Keep missing supported grants separate from successful APK installation."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .adb_protocol import DenialReporter, OwnerDenials
from .const import DOMAIN, help_url
from .status import PERMISSION_NAMES, ComponentStatus, PanelStatus
from .transport_sessions import async_get_sessions

ISSUE_PANEL_PERMISSIONS = "panel_permissions"
ISSUE_PERMISSIONS_TURNED_OFF = "permissions_turned_off"

#: The owner-facing names an install or update reports as turned off, by the
#: permission name the panel's status uses.
_TURNED_OFF_PERMISSIONS = {
    "modify system settings": "write_settings",
    "display over other apps": "overlay",
    "camera": "camera",
    "microphone": "microphone",
}


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
    # The owner turned these off; that Repair says so, this one never asks to undo it.
    for name in _owner_turned_off(hass, entry.entry_id):
        if permissions.get(name) == "missing":
            permissions[name] = "not_required"
    return permissions


def _owner_turned_off(hass: HomeAssistant, entry_id: str) -> frozenset[str]:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, turned_off_issue_id(entry_id))
    if issue is None or not issue.data:
        return frozenset()
    names = issue.data.get("permissions")
    return frozenset(names.split(",")) if isinstance(names, str) else frozenset()


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
            learn_more_url=help_url("repair", issue=ISSUE_PANEL_PERMISSIONS),
            translation_placeholders={
                "panel": panel_display_name(hass, entry),
                "learn_more_url": help_url("repair", issue=ISSUE_PANEL_PERMISSIONS),
                "permissions_url": help_url("repair", issue=ISSUE_PANEL_PERMISSIONS),
            },
            data={"entry_id": entry.entry_id},
        )
    elif permissions_held(permissions):
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def turned_off_issue_id(entry_id: str) -> str:
    """One Repair per panel for the permissions its owner turned off."""
    return f"{ISSUE_PERMISSIONS_TURNED_OFF}_{entry_id}"


def _feature_list(features: tuple[str, ...]) -> str:
    return (
        features[0]
        if len(features) == 1
        else f"{', '.join(features[:-1])} and {features[-1]}"
    )


def async_reconcile_turned_off_issue(
    hass: HomeAssistant, entry_id: str, denials: OwnerDenials
) -> None:
    """Say which permissions an install or update kept off for the owner.

    Installs and updates never turn a permission back on that the owner turned
    off in Android settings. This Repair names them; Ignore keeps it quiet. A
    complete reading with nothing off clears it; a partial one leaves it.
    """
    issue_id = turned_off_issue_id(entry_id)
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if not denials.features:
        if denials.complete:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    from .device import panel_display_name

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PERMISSIONS_TURNED_OFF,
        learn_more_url=help_url("repair", issue=ISSUE_PERMISSIONS_TURNED_OFF),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "features": _feature_list(denials.features),
            "permissions_url": help_url("repair", issue=ISSUE_PANEL_PERMISSIONS),
        },
        data={
            "entry_id": entry_id,
            "permissions": ",".join(
                _TURNED_OFF_PERMISSIONS[feature]
                for feature in denials.features
                if feature in _TURNED_OFF_PERMISSIONS
            ),
        },
    )


def turned_off_reporter(hass: HomeAssistant, entry_id: str) -> DenialReporter:
    """Report one panel's grant readback to its Repair."""

    def report(denials: OwnerDenials) -> None:
        async_reconcile_turned_off_issue(hass, entry_id, denials)

    return report

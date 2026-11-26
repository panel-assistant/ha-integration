"""Point the owner at apps a panel kept from its previous setup.

After an install Panel Assistant ran, each session the panel opens is asked
for its app list until no app the panel marks as left over remains. While any
does, one Repair names them and links the panel's own Install page, where the
panel's uninstall picker asks for its own confirmation and approval. Home
Assistant's Ignore keeps them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .app_identity import LEGACY_PACKAGE_ID, PACKAGE_ID
from .const import CONF_INSTALL_APPS_PENDING, DOMAIN, help_url
from .device import panel_display_name, sidebar_url
from .transport_sessions import (
    async_get_sessions,
    async_manage,
    session_manages,
)

ISSUE_REMOVE_PREVIOUS_APPS = "remove_previous_apps"
MANAGE_PACKAGES = "packages"
# Never listed, whatever a panel reports.
PROTECTED_PACKAGES = frozenset({PACKAGE_ID, LEGACY_PACKAGE_ID})


def previous_apps_issue_id(entry_id: str) -> str:
    """Keep one offer attached to its panel."""
    return f"{ISSUE_REMOVE_PREVIOUS_APPS}_{entry_id}"


def _leftovers(answer: dict[str, Any]) -> list[str]:
    """Labels of the removable apps the panel marks as left over, in its order."""
    packages = answer.get("packages")
    labels: list[str] = []
    for item in packages if isinstance(packages, list) else []:
        if not isinstance(item, dict) or item.get("previous_setup") is not True:
            continue
        pkg = item.get("pkg")
        if not isinstance(pkg, str) or not pkg or pkg in PROTECTED_PACKAGES:
            continue
        label = item.get("label")
        labels.append(label if isinstance(label, str) and label else pkg)
    return labels


async def async_check_previous_apps(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Raise, refresh or withdraw the offer from the panel's current list."""
    session = async_get_sessions(hass).get(entry.entry_id)
    if not entry.data.get(CONF_INSTALL_APPS_PENDING) or not session_manages(session):
        return
    assert session is not None
    try:
        answer = await async_manage(hass, session, MANAGE_PACKAGES)
    except HomeAssistantError:
        return
    issue_id = previous_apps_issue_id(entry.entry_id)
    # An older panel marks nothing and a panel without root removes nothing.
    if answer.get("uninstall_available") is not True:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    if not (labels := _leftovers(answer)):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        hass.config_entries.async_update_entry(
            entry,
            data={
                k: v for k, v in entry.data.items() if k != CONF_INSTALL_APPS_PENDING
            },
        )
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_REMOVE_PREVIOUS_APPS,
        learn_more_url=help_url("repair", issue=ISSUE_REMOVE_PREVIOUS_APPS),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "apps": ", ".join(labels),
            "panel_install": sidebar_url(entry.entry_id, "install"),
        },
        data={"entry_id": entry.entry_id},
    )


@callback
def async_follow_previous_apps(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Any
) -> Callable[[], None]:
    """Check once per session while its install's offer is due."""
    checked: list[object] = [None]

    @callback
    def _updated() -> None:
        session = async_get_sessions(hass).get(entry.entry_id)
        # A session counts as asked only once the offer is due, so an install
        # recorded while the panel is already connected is checked at once.
        if (
            not entry.data.get(CONF_INSTALL_APPS_PENDING)
            or not session_manages(session)
            or session is checked[0]
        ):
            return
        checked[0] = session
        entry.async_create_background_task(
            hass,
            async_check_previous_apps(hass, entry),
            f"{DOMAIN}_previous_apps_{entry.entry_id}",
            # Not eagerly: the session's hello is answered before it is asked.
            eager_start=False,
        )

    unsubscribe = coordinator.async_add_listener(_updated)

    def _unsubscribe() -> None:
        unsubscribe()
        ir.async_delete_issue(hass, DOMAIN, previous_apps_issue_id(entry.entry_id))

    return _unsubscribe

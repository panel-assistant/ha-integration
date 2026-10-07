"""Say a panel cannot be updated at the moment we learn it, not at the next release.

A panel reaches Panel Assistant updates one of two ways: it installs the build
itself, which needs a privileged route it may not have, or Home Assistant sends
it over an authorized ADB connection. A panel with neither is not degraded in any
way its owner can see -- it keeps its dashboard, its entities and its home screen
-- so nothing announces that it has quietly stopped being updatable.

Raising this the moment the route check notices puts it in front of the owner
while the cause is still fresh, which in practice is minutes after a vendor
firmware update reset the panel's developer mode. Waiting for the next Panel
Assistant release would ask them to remember a change made weeks earlier and to
do the work twice.

The remedy differs per device, so this issue carries no instructions. It links to
the site, which routes on what the panel reported and can be corrected and
illustrated without shipping an integration release.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, help_url
from .status import PanelStatus

ISSUE_NO_UPDATE_ROUTE = "no_update_route"


def no_update_route_issue_id(entry_id: str) -> str:
    """Keep the issue attached to the configured panel across address changes."""
    return f"{ISSUE_NO_UPDATE_ROUTE}_{entry_id}"


def help_parameters(status: PanelStatus | None) -> dict[str, str]:
    """Describe the hardware well enough to route help, and nothing further.

    This URL is opened by the owner's browser against a public site, so it
    carries only facts that describe a kind of device. The panel also reports its
    name and its area, and both are excluded deliberately rather than by
    oversight, as are its address, serial and identity.

    Every value is optional. Older panels send no device block at all, so the
    page has to be useful from the topic alone and treat each parameter as a
    refinement.
    """
    parameters: dict[str, str] = {}
    if status is None:
        return parameters
    device = status.panel_assistant_device
    if device is not None:
        for key, value in (
            ("make", device.manufacturer),
            ("model", device.model),
            ("hw", device.hw_version),
        ):
            if value:
                parameters[key] = value
    cached = status.panel_assistant_update
    if cached is not None and cached.current_version:
        parameters["app"] = cached.current_version
    # Separates a panel whose route was withdrawn from one that never had a
    # route, so the page can word them differently without a second issue.
    if status.install_capability:
        parameters["cap"] = status.install_capability
    return parameters


def async_reconcile_update_route_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    has_route: bool,
    status: PanelStatus | None,
) -> None:
    """Raise the issue while no route exists, and withdraw it once one does."""
    issue_id = no_update_route_issue_id(entry.entry_id)
    if has_route:
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
        translation_key=ISSUE_NO_UPDATE_ROUTE,
        translation_placeholders={"panel": panel_display_name(hass, entry)},
        # The topic names the situation, never a remedy: restoring ADB is the
        # answer on one panel family and the wrong one on the next.
        learn_more_url=help_url("panel-cannot-update", **help_parameters(status)),
    )

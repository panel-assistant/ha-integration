"""One app-changing operation at a time per panel.

An update, a removal, the adoption of a reinstalled panel and a Shelly vendor
recovery each change or prove which app the panel runs, and two at once would
each judge the panel from a state the other is changing. Each claims the
entry first and gives up when another holds it.
"""

from __future__ import annotations

from typing import Final

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

DATA_PANEL_OPERATIONS: Final = "panel_operations"


def _operations(hass: HomeAssistant) -> set[str]:
    operations: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_PANEL_OPERATIONS, set()
    )
    return operations


@callback
def claim_panel_operation(hass: HomeAssistant, entry_id: str) -> bool:
    """Reserve the panel for one app-changing operation."""
    operations = _operations(hass)
    if entry_id in operations:
        return False
    operations.add(entry_id)
    return True


@callback
def release_panel_operation(hass: HomeAssistant, entry_id: str) -> None:
    """End the reservation taken by claim_panel_operation."""
    _operations(hass).discard(entry_id)

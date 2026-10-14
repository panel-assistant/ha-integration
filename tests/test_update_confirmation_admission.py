"""A pending panel Install tap excludes another update transaction."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.update import async_install as async_core_install
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.client import PanelInstallStatus, UpdateBusyError
from custom_components.panel_assistant.update_coordinator import PanelUpdateSnapshot

from .test_feed_update import _entity


@pytest.mark.parametrize("caller", ["core", "entity"])
async def test_pending_apk_confirmation_blocks_duplicate_install(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caller: str
) -> None:
    """Reloaded panel work prevents a second backup, upload and failure Repair."""
    entity, client = _entity(hass)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(
            running=True, component="APK", awaiting_confirmation=True
        ),
        error=None,
    )
    client.async_commit_apk.side_effect = UpdateBusyError()
    failure = AsyncMock()
    monkeypatch.setattr(panel_update, "async_record_update_failure", failure)

    with pytest.raises(HomeAssistantError):
        if caller == "core":
            await async_core_install(
                entity, ServiceCall(hass, "update", "install", {"backup": False})
            )
        else:
            await entity.async_install(None, False)

    client.async_backup_panel.assert_not_awaited()
    client.async_stage_apk.assert_not_awaited()
    client.async_commit_apk.assert_not_awaited()
    failure.assert_not_awaited()
    assert entity.in_progress is True
    assert entity.release_summary == "Tap Install on the panel to finish the update."
    assert (entity.extra_state_attributes or {}).get("awaiting_confirmation") is True

    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(running=False, component="APK"), error=None
    )
    assert entity.in_progress is False


@pytest.mark.parametrize(
    ("running", "awaiting_confirmation"), [(True, False), (False, True)]
)
def test_other_apk_status_does_not_claim_an_update(
    hass: HomeAssistant, running: bool, awaiting_confirmation: bool
) -> None:
    """An unrelated APK operation or a finished prompt does not latch progress."""
    entity, _ = _entity(hass)
    entity._update_coordinator.data = PanelUpdateSnapshot(
        operation=PanelInstallStatus(
            running=running,
            component="APK",
            awaiting_confirmation=awaiting_confirmation,
        ),
        error=None,
    )

    assert entity.in_progress is False

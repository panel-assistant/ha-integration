"""An Area move in Home Assistant asks the panel to read its Area again at once."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from .test_availability import _load
from .test_device_name import _device
from .test_management import MANAGED, _connect


async def test_an_area_move_asks_the_panel_to_read_its_area_again(
    hass: HomeAssistant,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token, MANAGED)
    areas = ar.async_get(hass)
    registry = dr.async_get(hass)
    device = _device(hass, entry)

    for name in ("Kitchen", "Study"):
        registry.async_update_device(device.id, area_id=areas.async_create(name).id)
        request = await panel.manage()
        # The panel owns the Area rule: no value is sent, only a request to read it.
        assert request["op"] == "ha_area.refresh"
        assert "settings" not in request
        assert (await panel.answer(request, "applied"))["success"]
        await hass.async_block_till_done()

    # A panel that does not know the request refuses it; nothing is retried or raised.
    registry.async_update_device(device.id, area_id=areas.async_create("Hall").id)
    request = await panel.manage()
    assert (await panel.answer(request, "refused", code="unknown_command"))["success"]
    await hass.async_block_till_done()

    # A change that is not an Area move sends nothing.
    with patch(
        "custom_components.panel_assistant.device.async_manage", AsyncMock()
    ) as send:
        registry.async_update_device(device.id, sw_version="9.9.9")
        await hass.async_block_till_done()
        send.assert_not_called()

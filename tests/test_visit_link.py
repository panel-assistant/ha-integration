"""The device page's Visit link goes where the panel can be reached.

It opens the panel in the sidebar while the sidebar can show it, and the
panel's own page at its stored address while the sidebar could only say the
panel is unreachable, so its owner can still fix what disconnected it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import DOMAIN

from .test_availability import (
    MOVED,
    STORED,
    _connect,
    _issue,
    _load,
    _poll,
)
from .test_transport import HEALTH, OTHER_DID, WsClientFactory, _open

# Where the test client's socket connects from.
LOOPBACK = "127.0.0.1"


@pytest.fixture
async def entry(
    hass: HomeAssistant, hass_read_only_user: Any
) -> AsyncGenerator[MockConfigEntry]:
    """One loaded, bound panel entry whose stored address answers."""
    yield await _load(hass, hass_read_only_user.id)


def _visit(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    """Return where the device page's Visit link goes."""
    card = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert card is not None
    return card.configuration_url


async def test_visit_opens_the_panel_page_while_nothing_reaches_the_panel(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """The sidebar could only say the panel is unreachable, so Visit goes to
    the panel's own page, and back to the sidebar once the panel answers."""
    sidebar = f"homeassistant://panel-assistant/{entry.entry_id}"
    assert _visit(hass, entry) == sidebar

    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _visit(hass, entry) == f"http://{STORED}:8888/"

    await _poll(hass, entry, {STORED: HEALTH})
    assert _visit(hass, entry) == sidebar


async def test_visit_follows_the_session_when_the_stored_address_fails(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A connected panel opens in the sidebar; its session ending switches
    Visit to the panel's page at once, and a new session switches it back."""
    sidebar = f"homeassistant://panel-assistant/{entry.entry_id}"
    client = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, entry, None
    )
    await _poll(hass, entry, {STORED: CannotConnectError()})
    assert _visit(hass, entry) == sidebar

    await client.close()
    await hass.async_block_till_done()
    assert _visit(hass, entry) == f"http://{STORED}:8888/"

    await _connect(hass, hass_ws_client, hass_read_only_access_token, entry, None)
    await hass.async_block_till_done()
    assert _visit(hass, entry) == sidebar


async def test_a_disconnected_panel_is_found_where_its_last_session_came_from(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel whose session ended and whose stored address went dead is
    tried at the addresses its last session reported; answering there as itself
    moves the stored address, and Visit opens it in the sidebar again."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client, addresses=[MOVED])
    await client.close()
    await hass.async_block_till_done()

    await _poll(
        hass,
        entry,
        # The test socket connected from loopback, which is tried as well.
        {STORED: CannotConnectError(), LOOPBACK: CannotConnectError(), MOVED: HEALTH},
    )

    assert entry.data[CONF_ADDRESS] == MOVED
    assert entry.runtime_data.coordinator.available
    assert _visit(hass, entry) == f"homeassistant://panel-assistant/{entry.entry_id}"
    # No session, so nothing is reported beyond the poll itself.
    assert _issue(hass, entry) is None


async def test_a_stranger_at_the_last_session_address_moves_nothing(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Another panel answering where this one last said it was is not it:
    the stored address stays, and Visit goes to the panel's last-known page."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client, addresses=[MOVED])
    await client.close()
    await hass.async_block_till_done()

    await _poll(
        hass,
        entry,
        {
            STORED: CannotConnectError(),
            LOOPBACK: CannotConnectError(),
            MOVED: replace(HEALTH, discovery_id=OTHER_DID),
        },
    )

    assert entry.data[CONF_ADDRESS] == STORED
    assert _visit(hass, entry) == f"http://{STORED}:8888/"
    assert _issue(hass, entry) is None

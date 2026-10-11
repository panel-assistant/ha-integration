"""A reinstalled panel rejoins its one entry by itself, wherever it now is.

A clean reinstall gives the app a new installation identity. What survives is
the legacy identity the device reports beside it, which the entry recorded
while it accepted the panel's own identity. That link, held by exactly one
entry, is followed: the entry takes the new identity and keeps its entities.
A panel that matches no entry, or several, is not adopted.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import panel_move
from custom_components.panel_assistant.app_identity import SUCCESSOR_PACKAGE_ID
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
)
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.panel_move import (
    MoveError,
    claim_panel_operation,
    move_issue_id,
    release_panel_operation,
)
from custom_components.panel_assistant.transport import async_get_sessions

from .test_transport import HEALTH, STATUS, WsClientFactory, _hello, _send

STORED = "panel.local"
MOVED = "192.0.2.9"
OTHER_ADDRESS = "192.0.2.77"
OLD = "a" * 64  # the identity the entry holds, from the app as first installed
NEW = "b" * 64  # the reinstalled app's identity
OTHER = "c" * 64
LINK = "1" * 64  # the device's legacy identity, the same across reinstalls
OTHER_LINK = "2" * 64


def _health(did: str, link: str | None = LINK) -> PanelHealth:
    return replace(HEALTH, discovery_id=did, legacy_discovery_id=link)


class Network:
    """What answers at each address, like a LAN."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {"127.0.0.1": CannotConnectError()}

    async def health(self, client: HaPaneldClient) -> PanelHealth:
        answer = self.answers.get(client.address.host, CannotConnectError())
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
async def network() -> AsyncGenerator[Network]:
    lan = Network()

    async def health(client: HaPaneldClient) -> PanelHealth:
        return await lan.health(client)

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=SimpleNamespace(async_reconcile_entry=AsyncMock())),
        ),
    ):
        yield lan


def _entry(
    hass: HomeAssistant,
    user_id: str,
    did: str = OLD,
    address: str = STORED,
    title: str = "alpha",
    **data: Any,
) -> MockConfigEntry:
    """An installation entry with one native entity, not yet set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=did,
        data={
            CONF_ADDRESS: address,
            CONF_TRANSPORT_USER_ID: user_id,
            "installation_identity": True,
            **data,
        },
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{did}_wifi",
        config_entry=entry,
        suggested_object_id=f"{title}_wifi",
    )
    return entry


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _load(
    hass: HomeAssistant,
    network: Network,
    user_id: str,
    did: str = OLD,
    address: str = STORED,
    title: str = "alpha",
    link: str | None = LINK,
) -> MockConfigEntry:
    """An entry whose panel has answered as itself, as every entry has."""
    network.answers[address] = _health(did, link)
    entry = _entry(hass, user_id, did, address, title)
    await _set_up(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.last_update_success
    return entry


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _wifi(hass: HomeAssistant, title: str = "alpha") -> str | None:
    found = er.async_get(hass).async_get(f"sensor.{title}_wifi")
    return found.unique_id if found is not None else None


def _address_issue(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    return (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_identity_mismatch_{entry.entry_id}"
        )
        is not None
    )


def _adopted(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    current = hass.config_entries.async_get_entry(entry.entry_id)
    assert current is not None
    return current.unique_id == NEW and _wifi(hass) == f"{NEW}_wifi"


async def _hello_from(
    hass: HomeAssistant, client: Any, did: str = NEW, addresses: list[str] | None = None
) -> dict[str, Any]:
    response = await _send(
        client, _hello(did=did, addresses=addresses if addresses is not None else [])
    )
    await hass.async_block_till_done()
    return response


# --- the panel the owner already has --------------------------------------------


async def test_a_reinstalled_panel_rejoins_its_entry_at_the_same_address(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    network: Network,
) -> None:
    entry = await _load(hass, network, hass_read_only_user.id)
    network.answers[STORED] = _health(NEW)

    await _poll(hass, entry)

    assert _adopted(hass, entry)
    # The same entity, so the same history, under the new identity.
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{NEW}_wifi") == (
        "sensor.alpha_wifi"
    )
    assert entry.data["previous_installation_identity"] == OLD
    assert not _address_issue(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.last_update_success
    # The panel's own connection is accepted under the identity it now has.
    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _hello_from(hass, client)
    assert response["success"], response
    assert async_get_sessions(hass).get(entry.entry_id) is not None
    # Settled: a further poll changes nothing.
    data = dict(entry.data)
    await _poll(hass, entry)
    assert entry.unique_id == NEW and dict(entry.data) == data


async def test_a_reinstalled_panel_that_also_moved_rejoins_from_its_hello(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    network: Network,
) -> None:
    """The stored address is silent; only the panel's hello says where it went."""
    entry = await _load(hass, network, hass_read_only_user.id)
    network.answers[STORED] = CannotConnectError()
    network.answers[MOVED] = _health(NEW)
    await _poll(hass, entry)
    assert not entry.runtime_data.coordinator.available

    client = await hass_ws_client(hass, hass_read_only_access_token)
    first = await _hello_from(hass, client, addresses=[MOVED])

    assert first["error"]["code"] == "unknown_panel"
    assert _adopted(hass, entry)
    assert entry.data[CONF_ADDRESS] == MOVED
    assert not _address_issue(hass, entry)
    # Its next hello, a few seconds later, opens the session.
    second = await _hello_from(hass, client, addresses=[MOVED])
    assert second["success"], second
    assert entry.runtime_data.coordinator.available


async def test_a_reinstalled_panel_is_adopted_when_its_entry_sets_up(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Home Assistant restarted while the panel was being reinstalled."""
    entry = await _load(hass, network, hass_read_only_user.id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    network.answers[STORED] = _health(NEW)

    await _set_up(hass, entry)

    assert _adopted(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert not _address_issue(hass, entry)


async def test_a_panel_that_moved_apps_before_rejoins_without_adb(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Most entries hold the device's link as the identity they replaced when
    their panel moved to the new app. A reinstall of one looks like that move
    too, and is followed on the recorded link, with no ADB proof to wait for."""
    network.answers[STORED] = replace(_health(OLD), package=SUCCESSOR_PACKAGE_ID)
    entry = _entry(hass, hass_read_only_user.id, previous_installation_identity=LINK)
    await _set_up(hass, entry)
    network.answers[STORED] = replace(_health(NEW), package=SUCCESSOR_PACKAGE_ID)

    await _poll(hass, entry)

    assert _adopted(hass, entry)
    assert entry.runtime_data.coordinator.last_update_success


# --- what is never adopted ------------------------------------------------------


async def test_a_different_device_at_the_address_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, network, hass_read_only_user.id)
    network.answers[STORED] = _health(NEW, OTHER_LINK)

    await _poll(hass, entry)

    assert entry.unique_id == OLD and _wifi(hass) == f"{OLD}_wifi"
    assert _address_issue(hass, entry)
    assert not entry.runtime_data.coordinator.last_update_success


async def test_a_panel_whose_entry_never_learned_its_link_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """The app reported no legacy identity, so nothing links a reinstall to it."""
    entry = await _load(hass, network, hass_read_only_user.id, link=None)
    network.answers[STORED] = _health(NEW)

    await _poll(hass, entry)

    assert entry.unique_id == OLD and _wifi(hass) == f"{OLD}_wifi"
    assert _address_issue(hass, entry)


async def test_a_link_two_entries_hold_is_not_followed(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """A stale duplicate of the same device makes the link ambiguous."""
    entry = await _load(hass, network, hass_read_only_user.id)
    await _load(
        hass, network, hass_read_only_user.id, OTHER, OTHER_ADDRESS, title="beta"
    )
    network.answers[STORED] = _health(NEW)

    await _poll(hass, entry)

    assert entry.unique_id == OLD and _wifi(hass) == f"{OLD}_wifi"
    assert _wifi(hass, "beta") == f"{OTHER}_wifi"
    assert _address_issue(hass, entry)


async def test_an_identity_another_entry_owns_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """The owner re-added the panel by hand before Panel Assistant caught up."""
    entry = await _load(hass, network, hass_read_only_user.id)
    await _load(
        hass, network, hass_read_only_user.id, NEW, OTHER_ADDRESS, "beta", link=None
    )
    network.answers[STORED] = _health(NEW)

    await _poll(hass, entry)

    assert entry.unique_id == OLD and _wifi(hass) == f"{OLD}_wifi"
    assert _wifi(hass, "beta") == f"{NEW}_wifi"


async def test_a_moved_panel_whose_identity_another_entry_owns_moves_nothing(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    network: Network,
) -> None:
    """An entry not loaded still owns its identity, so the address stays."""
    entry = await _load(hass, network, hass_read_only_user.id)
    owner = _entry(hass, hass_read_only_user.id, NEW, OTHER_ADDRESS, "beta")
    await hass.config_entries.async_set_disabled_by(
        owner.entry_id, ConfigEntryDisabler.USER
    )
    network.answers[STORED] = CannotConnectError()
    network.answers[MOVED] = _health(NEW)
    await _poll(hass, entry)

    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _hello_from(hass, client, addresses=[MOVED])

    assert response["error"]["code"] == "unknown_panel"
    assert entry.unique_id == OLD and entry.data[CONF_ADDRESS] == STORED


async def test_an_unknown_hello_from_another_account_moves_nothing(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_access_token: str,
    network: Network,
) -> None:
    """Only the account bound to an entry is heard about where its panel went."""
    entry = await _load(hass, network, hass_read_only_user.id)
    network.answers[STORED] = CannotConnectError()
    network.answers[MOVED] = _health(NEW)
    await _poll(hass, entry)

    client = await hass_ws_client(hass, hass_access_token)
    response = await _hello_from(hass, client, addresses=[MOVED])

    assert response["error"]["code"] == "unknown_panel"
    assert entry.unique_id == OLD and entry.data[CONF_ADDRESS] == STORED


# --- interruption points --------------------------------------------------------


async def test_a_panel_busy_with_an_update_is_adopted_on_the_next_poll(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, network, hass_read_only_user.id)
    network.answers[STORED] = _health(NEW)
    assert claim_panel_operation(hass, entry.entry_id)

    await _poll(hass, entry)

    assert entry.unique_id == OLD and not _address_issue(hass, entry)
    release_panel_operation(hass, entry.entry_id)
    await _poll(hass, entry)
    assert _adopted(hass, entry)


async def test_a_move_that_stopped_part_way_is_left_to_that_move(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Panel Assistant's own move installed the new app, then lost the panel
    before restoring it. Only that move can finish: the panel is not taken on
    the link, and the move Repair stays the way to finish it."""
    entry = await _load(hass, network, hass_read_only_user.id)
    record = {
        "receipt": "receipt.zip",
        "sha256": "0" * 64,
        "panel_id": "alpha",
        "config_hash": "1a2b3c4d",
        "legacy_did": OLD,
        "serial": "SERIAL",
    }
    await hass.async_add_executor_job(
        panel_move._write_record, panel_move._record_path(hass, entry.entry_id), record
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "successor_move": {"panel_id": "alpha"}}
    )
    network.answers[STORED] = replace(_health(NEW), package=SUCCESSOR_PACKAGE_ID)

    with patch.object(
        panel_move,
        "_async_target",
        AsyncMock(side_effect=MoveError(panel_move.REASON_ADB_UNREACHABLE)),
    ):
        await _poll(hass, entry)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.unique_id == OLD and _wifi(hass) == f"{OLD}_wifi"
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, move_issue_id(entry.entry_id))
        is not None
    )


@pytest.mark.parametrize("saved", ["registry", "entry"])
async def test_a_restart_mid_adoption_finishes_it_whichever_save_landed(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, saved: str
) -> None:
    """The registry and the entry save separately; a stop between the two
    leaves one behind. The panel has moved too, so setup cannot read it."""
    if saved == "registry":
        # The entities were rekeyed, the entry still names the old identity.
        entry = _entry(hass, hass_read_only_user.id, OLD, legacy_did=LINK)
        er.async_get(hass).async_update_entity(
            "sensor.alpha_wifi", new_unique_id=f"{NEW}_wifi"
        )
    else:
        entry = _entry(
            hass,
            hass_read_only_user.id,
            OLD,
            legacy_did=LINK,
        )
        hass.config_entries.async_update_entry(
            entry,
            unique_id=NEW,
            data={**entry.data, "previous_installation_identity": OLD},
        )

    await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _adopted(hass, entry)

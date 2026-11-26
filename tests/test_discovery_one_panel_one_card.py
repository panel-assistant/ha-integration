"""One panel heard in both address families, on two channels, is one card.

A panel on another VLAN, with mDNS forwarded by the router, was offered twice:
by Android's network ADB announcement at its IPv4 address and by the app's own
announcement at its IPv6 address. Each card now keeps every address its
announcement carried, and cards sharing one are the same panel.
"""

import asyncio
from dataclasses import replace
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
)
from custom_components.panel_assistant.const import DOMAIN

from .test_migration_targets import APP_HEALTH, HEALTH_CHECK, _ignore_card, _offers
from .test_migration_targets import adb_open as adb_open

IPV4 = "192.168.1.5"
IPV6 = "fd00::5"
SERIAL = "1234567890123"
ADB_UID = f"adb:{SERIAL}"


async def _adb(
    hass: HomeAssistant,
    addresses: list[str],
    serial: str = SERIAL,
) -> None:
    """Android announcing network ADB, the app not answering at its address."""
    with patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address(addresses[0]),
                ip_addresses=[ip_address(a) for a in addresses],
                port=5555,
                hostname="Android.local.",
                type="_adb._tcp.local.",
                name=f"adb-{serial}._adb._tcp.local.",
                properties={},
            ),
        )
        await hass.async_block_till_done()


async def _app(
    hass: HomeAssistant,
    addresses: list[str],
    health=APP_HEALTH,
    also_answers_at: tuple[str, ...] = (),
    others: dict[str, PanelHealth] | None = None,
) -> None:
    """The app's own announcement, its health answering as the panel.

    The app answers at the addresses it announced and at any other address its
    panel has; every other address is another device, where no app answers.
    """
    panel = {*addresses, *also_answers_at}

    async def _health(client: HaPaneldClient) -> PanelHealth:
        if client.address.host in panel:
            return health
        if others and client.address.host in others:
            return others[client.address.host]
        raise CannotConnectError

    with patch(HEALTH_CHECK, _health):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address(addresses[0]),
                ip_addresses=[ip_address(a) for a in addresses],
                port=8888,
                hostname=f"{health.panel_id}.local.",
                type="_ha-paneld._tcp.local.",
                name=f"{health.panel_id}._ha-paneld._tcp.local.",
                properties={"did": health.discovery_id, "name": "Wall panel"},
            ),
        )
        await hass.async_block_till_done()


def _unique_ids(hass: HomeAssistant) -> list[str]:
    return [flow["context"]["unique_id"] for flow in _offers(hass)]


async def test_adb_card_in_ipv4_closes_when_the_app_announces_in_ipv6(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The app's verified IPv6 announcement replaces the ADB card heard by IPv4."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4, IPV6])
    assert _unique_ids(hass) == [ADB_UID]

    await _app(hass, [IPV6])

    assert _unique_ids(hass) == [APP_HEALTH.discovery_id]


async def test_adb_announcement_after_the_app_card_adds_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """ADB heard by IPv4 after the app's IPv6 card stands down."""
    adb_open.open.add(IPV4)
    await _app(hass, [IPV6])
    assert _unique_ids(hass) == [APP_HEALTH.discovery_id]

    await _adb(hass, [IPV4, IPV6])

    assert _unique_ids(hass) == [APP_HEALTH.discovery_id]


async def test_different_panels_sharing_only_a_link_local_address_stay_two(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A link-local address or the `Android.local` host name never joins cards."""
    other = "192.168.1.6"
    adb_open.open.update({IPV4, other})
    await _adb(hass, [IPV4, "fe80::1"])
    await _adb(hass, [other, "fe80::1"], serial="9876543210987")
    await _app(hass, ["fd00::6", "fe80::1"])

    assert sorted(_unique_ids(hass)) == sorted(
        [ADB_UID, "adb:9876543210987", APP_HEALTH.discovery_id]
    )


@pytest.mark.parametrize("ignored_first", ["adb", "app"])
async def test_ignore_on_one_channel_covers_the_other_address_family(
    hass: HomeAssistant, adb_open: SimpleNamespace, ignored_first: str
) -> None:
    """Ignoring either card keeps the panel's other announcement off the list."""
    adb_open.open.add(IPV4)
    if ignored_first == "adb":
        await _adb(hass, [IPV4, IPV6])
    else:
        await _app(hass, [IPV6])
    [card] = _offers(hass)
    await _ignore_card(hass, card)
    assert _offers(hass) == []

    if ignored_first == "adb":
        await _app(hass, [IPV6])
    else:
        await _adb(hass, [IPV4, IPV6])

    assert _offers(hass) == []


async def test_pressed_adb_card_keeps_the_panel_when_the_app_announces_in_ipv6(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A person installing from the ADB card keeps it; the app's card stands down."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4, IPV6])
    [card] = _offers(hass)

    async def pin(*_args):
        raise RuntimeError

    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            side_effect=pin,
        ),
    ):
        form = await hass.config_entries.flow.async_configure(card["flow_id"], {})
    assert form["step_id"] == "add_panel"

    await _app(hass, [IPV6])
    await asyncio.sleep(0)

    assert [flow["flow_id"] for flow in _offers(hass)] == [card["flow_id"]]


@pytest.mark.parametrize(
    ("installing", "known", "stands_down"),
    [(True, False, True), (False, False, False), (True, True, False)],
)
async def test_the_app_restarting_during_an_install_raises_no_second_flow(
    hass: HomeAssistant,
    adb_open: SimpleNamespace,
    installing: bool,
    known: bool,
    stands_down: bool,
) -> None:
    """The app announces on every start of the install a person confirmed.

    Each announcement used to read the panel's health twice and then end
    ``already_configured``, once per restart of the app being installed.
    It now stands down before reading anything while that install runs; a
    card whose install has ended, or a panel Home Assistant already knows,
    takes the old path.
    """
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    [card] = _offers(hass)

    async def pin(*_args):
        raise RuntimeError

    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            side_effect=pin,
        ),
    ):
        await hass.config_entries.flow.async_configure(card["flow_id"], {})

    if known:
        MockConfigEntry(
            domain=DOMAIN,
            unique_id=APP_HEALTH.discovery_id,
            data={CONF_ADDRESS: "192.168.1.99"},
        ).add_to_hass(hass)
    receipt = SimpleNamespace(
        is_terminal=not installing,
        target=SimpleNamespace(address=IPV4, pinned_address=IPV4),
    )
    jobs = SimpleNamespace(async_list=AsyncMock(return_value=(receipt,)))
    health = AsyncMock(return_value=APP_HEALTH)
    for _restart in range(2):
        with (
            patch(HEALTH_CHECK, health),
            patch(
                "custom_components.panel_assistant.install_jobs."
                "async_get_install_job_manager",
                AsyncMock(return_value=jobs),
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=ZeroconfServiceInfo(
                    ip_address=ip_address(IPV4),
                    ip_addresses=[ip_address(IPV4)],
                    port=8888,
                    hostname=f"{APP_HEALTH.panel_id}.local.",
                    type="_ha-paneld._tcp.local.",
                    name=f"{APP_HEALTH.panel_id}._ha-paneld._tcp.local.",
                    properties={"did": APP_HEALTH.discovery_id, "name": "Wall panel"},
                ),
            )
        assert (result["reason"] == "already_in_progress") is stands_down
    assert (health.await_count == 0) is stands_down


async def test_a_card_from_before_the_app_closes_when_its_address_answers_as_the_panel(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Nothing announced is shared, but the IPv4 card's address is the same panel."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    assert _unique_ids(hass) == [ADB_UID]

    await _app(hass, [IPV6], also_answers_at=(IPV4,))

    assert _unique_ids(hass) == [APP_HEALTH.discovery_id]


async def test_a_card_whose_address_does_not_answer_as_the_panel_stays(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Another device's card, at an address the panel does not answer on, is kept."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])

    await _app(hass, [IPV6])

    assert sorted(_unique_ids(hass)) == sorted([ADB_UID, APP_HEALTH.discovery_id])


async def test_a_card_at_an_address_where_another_panel_answers_stays(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An answer is not enough: it must be the panel that was just proven."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])

    await _app(hass, [IPV6], others={IPV4: replace(APP_HEALTH, discovery_id="c" * 64)})

    assert sorted(_unique_ids(hass)) == sorted([ADB_UID, APP_HEALTH.discovery_id])

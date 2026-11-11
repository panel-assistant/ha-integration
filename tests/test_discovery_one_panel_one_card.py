"""One panel heard in both address families, on two channels, is one card.

A panel on another VLAN, with mDNS forwarded by the router, was offered twice:
by Android's network ADB announcement at its IPv4 address and by the app's own
announcement at its IPv6 address. Each card now keeps every address its
announcement carried, and cards sharing one are the same panel.
"""

import asyncio
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.panel_assistant.client import CannotConnectError
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


async def _app(hass: HomeAssistant, addresses: list[str], health=APP_HEALTH) -> None:
    """The app's own announcement, its health answering as the panel."""
    with patch(HEALTH_CHECK, AsyncMock(return_value=health)):
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

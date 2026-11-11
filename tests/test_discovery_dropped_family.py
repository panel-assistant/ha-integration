"""Discovery offers a panel when one of its address families cannot connect.

On a network that drops IPv6 between Home Assistant and the panel, a dual-stack
panel's IPv6 announcement could arrive first. Its flow took the panel's id and
waited on the IPv6 address while the announcement carrying the working IPv4
address, a second later, was turned away as already in progress; the first flow
then gave up, so the panel was never offered and nothing above DEBUG said why.
"""

import asyncio
import logging
from dataclasses import replace
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
)
from custom_components.panel_assistant.const import DOMAIN

from .test_discovery_one_panel_one_card import _adb
from .test_migration_targets import APP_HEALTH, HEALTH_CHECK, _offers
from .test_migration_targets import adb_open as adb_open

STATUS_CHECK = HEALTH_CHECK.replace("async_get_health", "async_get_status")
IPV4 = "192.168.1.5"
IPV6 = "fd00::5"


def _record(addresses: list[str], health: PanelHealth = APP_HEALTH):
    return ZeroconfServiceInfo(
        ip_address=ip_address(addresses[0]),
        ip_addresses=[ip_address(a) for a in addresses],
        port=8888,
        hostname=f"{health.panel_id}.local.",
        type="_ha-paneld._tcp.local.",
        name=f"{health.panel_id}._ha-paneld._tcp.local.",
        properties={"did": health.discovery_id, "name": "Wall panel"},
    )


class _Network:
    """The panel answering at some of its addresses; a dropped one hangs, then fails."""

    def __init__(self, answering: set[str], dropped: set[str] = frozenset()) -> None:
        self.answering = answering
        self.dropped = dropped
        self.waiting = asyncio.Event()
        self.timeout = asyncio.Event()
        self.asked: list[str] = []

    @property
    def health(self):
        """The patch: a plain function, so it binds to the client it replaces on."""

        async def health(client: HaPaneldClient) -> PanelHealth:
            return await self._answer(client)

        return health

    async def _answer(self, client: HaPaneldClient) -> PanelHealth:
        host = client.address.host
        self.asked.append(host)
        if host in self.answering:
            return APP_HEALTH
        if host in self.dropped:
            self.waiting.set()
            await self.timeout.wait()
        raise CannotConnectError


async def _announce(hass: HomeAssistant, addresses: list[str]):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=_record(addresses),
    )


def _card_addresses(hass: HomeAssistant) -> list[str]:
    return [
        flow["context"]["title_placeholders"]["address"]
        for flow in _offers(hass)
        if flow["context"].get("unique_id") == APP_HEALTH.discovery_id
    ]


async def test_ipv6_first_then_both_families_offers_one_card_at_ipv4(
    hass: HomeAssistant,
) -> None:
    """The IPv4 address heard while the IPv6 read hangs is tried, and answers."""
    network = _Network(answering={IPV4}, dropped={IPV6})
    with patch(HEALTH_CHECK, network.health):
        first = hass.async_create_task(_announce(hass, [IPV6]))
        await network.waiting.wait()
        # The same panel's next announcement, both families, while the first
        # flow still waits on IPv6.
        second = await _announce(hass, [IPV6, IPV4])
        assert second["type"] is FlowResultType.ABORT
        network.timeout.set()
        result = await first
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_discovery"
    assert _card_addresses(hass) == [IPV4]
    assert len(_offers(hass)) == 1

    # The card is the panel at the address that answered.
    with (
        patch(HEALTH_CHECK, network.health),
        patch(STATUS_CHECK, AsyncMock(side_effect=CannotConnectError)),
    ):
        created = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["data"]["address"] == IPV4
    assert _offers(hass) == []


async def test_the_card_found_by_a_handed_over_address_keeps_both_families(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Android's ADB announcement by the panel's IPv6 address adds no second card."""
    network = _Network(answering={IPV4}, dropped={IPV6})
    with patch(HEALTH_CHECK, network.health):
        first = hass.async_create_task(_announce(hass, [IPV6]))
        await network.waiting.wait()
        await _announce(hass, [IPV6, IPV4])
        network.timeout.set()
        await first
        await hass.async_block_till_done()
    assert _card_addresses(hass) == [IPV4]

    adb_open.open.add(IPV6)
    await _adb(hass, [IPV6])

    assert [flow["context"]["unique_id"] for flow in _offers(hass)] == [
        APP_HEALTH.discovery_id
    ]


async def test_one_record_whose_first_address_cannot_connect_tries_the_next(
    hass: HomeAssistant,
) -> None:
    """A both-family record whose IPv4 is dropped is offered at its IPv6 address."""
    network = _Network(answering={IPV6})
    with patch(HEALTH_CHECK, network.health):
        result = await _announce(hass, [IPV4, IPV6])
        await hass.async_block_till_done()

    assert network.asked == [IPV4, IPV6]
    assert result["step_id"] == "confirm_discovery"
    assert _card_addresses(hass) == [f"[{IPV6}]"]


async def test_unreachable_panel_is_a_warning_naming_every_address_tried(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Heard but unreachable is its own refusal, at WARNING, not a wrong identity."""
    network = _Network(answering=set())
    caplog.set_level(logging.DEBUG, logger="custom_components.panel_assistant")
    with patch(HEALTH_CHECK, network.health):
        result = await _announce(hass, [IPV4, IPV6])
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert _offers(hass) == []
    refusals = [
        record for record in caplog.records if "setup refused" in record.getMessage()
    ]
    assert len(refusals) == 1
    line = refusals[0]
    assert line.levelno == logging.WARNING
    message = line.getMessage()
    assert "error=cannot_connect" in message
    assert f"{IPV4}:8888" in message
    assert f"[{IPV6}]:8888" in message
    assert "Wall panel" in message
    assert "CannotConnectError" in message


async def test_an_address_answering_as_another_panel_is_not_offered(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Identity is proved per address: another panel there is never this card."""
    other = replace(APP_HEALTH, panel_id="other_panel", discovery_id="c" * 64)

    async def health(client: HaPaneldClient) -> PanelHealth:
        if client.address.host == IPV4:
            return other
        raise CannotConnectError

    caplog.set_level(logging.DEBUG, logger="custom_components.panel_assistant")
    with patch(HEALTH_CHECK, health):
        result = await _announce(hass, [IPV4, IPV6])
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert _offers(hass) == []
    assert not [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "refused" in r.getMessage()
    ]


@pytest.mark.parametrize("source", ["user", "ignore"])
async def test_an_added_or_ignored_panel_out_of_reach_stays_quiet(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, source: str
) -> None:
    """Announcements of a panel already decided on arrive all day: no WARNING."""
    MockConfigEntry(
        domain=DOMAIN,
        source=source,
        unique_id=APP_HEALTH.discovery_id,
        data={"address": "192.168.1.99"},
    ).add_to_hass(hass)
    network = _Network(answering=set())
    caplog.set_level(logging.DEBUG, logger="custom_components.panel_assistant")
    with patch(HEALTH_CHECK, network.health):
        result = await _announce(hass, [IPV4, IPV6])
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert not [
        r
        for r in caplog.records
        if r.name.startswith("custom_components.panel_assistant")
        and r.levelno >= logging.WARNING
    ]

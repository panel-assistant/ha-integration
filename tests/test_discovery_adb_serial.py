"""A panel's ADB card and its app are one panel when the app names the serial.

Android announces network ADB as `adb-<serial>`; from 1.0.3 the app hears that
announcement from its own panel and repeats the serial on its health line. A
panel heard by ADB in IPv4 only and by the app in IPv6 only, whose IPv4 port
8888 Home Assistant cannot reach, shares nothing else with its app's card.
"""

import asyncio
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import discovery
from custom_components.panel_assistant.app_identity import PACKAGE_ID
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    parse_health_response,
)
from custom_components.panel_assistant.const import DOMAIN

from .config_flow_helpers import _probe
from .test_discovery_one_panel_one_card import IPV4, IPV6, SERIAL, _adb
from .test_migration_targets import HEALTH_CHECK, _host_entry, _offers
from .test_migration_targets import adb_open as adb_open

ADB_UID = f"adb:{SERIAL}"
DID = "b" * 64
OTHER_DID = "c" * 64


def _health(did: str = DID, adb: str | None = SERIAL) -> PanelHealth:
    """The panel's health line as the app sends it, read by the real parser."""
    line = (
        f"ha-paneld 1.0.3 panel=wall_panel build=1000 cfg=1a2b3c4d did={did} "
        f"identity=install pkg={PACKAGE_ID} vc=2510 proto=5-5"
    )
    if adb is not None:
        line += f" adb={adb}"
    return parse_health_response(line + "\n")


async def _app(hass: HomeAssistant, address: str, health: PanelHealth) -> None:
    """The app's announcement at one address; its health answers only there."""

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host == address:
            return health
        raise CannotConnectError

    with patch(HEALTH_CHECK, answer):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address(address),
                ip_addresses=[ip_address(address)],
                port=8888,
                hostname=f"{health.panel_id}.local.",
                type="_ha-paneld._tcp.local.",
                name=f"{health.panel_id}._ha-paneld._tcp.local.",
                properties={"did": health.discovery_id, "name": "Wall panel"},
            ),
        )
        await hass.async_block_till_done()


async def _adb_heard_while_app_answers(
    hass: HomeAssistant, app_address: str, health: PanelHealth
) -> None:
    """ADB announced at IPv4, where no app answers; the app answers at its own."""

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host == app_address:
            return health
        raise CannotConnectError

    with patch(HEALTH_CHECK, answer):
        await _adb_heard(hass)


async def _adb_heard(hass: HomeAssistant) -> None:
    """Android announcing network ADB at IPv4 only."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=ip_address(IPV4),
            ip_addresses=[ip_address(IPV4)],
            port=5555,
            hostname="Android.local.",
            type="_adb._tcp.local.",
            name=f"adb-{SERIAL}._adb._tcp.local.",
            properties={},
        ),
    )
    await hass.async_block_till_done()


def _added(
    hass: HomeAssistant,
    did: str,
    address: str,
    health: PanelHealth,
    available: bool = True,
) -> None:
    """A panel already added, its coordinator holding the health it last read."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=did,
        title="Wall panel",
        data={CONF_ADDRESS: address},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        coordinator=SimpleNamespace(
            data=SimpleNamespace(health=health), available=available
        )
    )


def _unique_ids(hass: HomeAssistant) -> list[str]:
    return sorted(flow["context"]["unique_id"] for flow in _offers(hass))


def test_health_line_carries_the_serial_and_drops_an_unusable_one() -> None:
    """The serial is read from the line; a malformed one never breaks the line."""
    assert _health().adb_serial == SERIAL
    assert _health(adb=None).adb_serial is None
    assert _health(adb="adb-X(2)").adb_serial is None


async def test_adb_card_closes_when_the_app_names_its_serial(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """ADB in IPv4 only, the app in IPv6 only: one card, the app's."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    assert _unique_ids(hass) == [ADB_UID]

    await _app(hass, IPV6, _health())

    assert _unique_ids(hass) == [DID]


async def test_adb_heard_after_the_app_card_adds_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The waiting app card is asked, and its health names the serial."""
    adb_open.open.add(IPV4)
    await _app(hass, IPV6, _health(adb=None))
    assert _unique_ids(hass) == [DID]

    # The app heard its own network ADB after its card appeared.
    await _adb_heard_while_app_answers(hass, IPV6, _health())

    assert _unique_ids(hass) == [DID]


async def test_adb_heard_for_an_added_panel_adds_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An added panel reporting the serial is not offered again by its ADB."""
    adb_open.open.add(IPV4)
    _added(hass, DID, f"[{IPV6}]:8888", _health())

    await _adb(hass, [IPV4])

    assert _unique_ids(hass) == []


async def test_a_pressed_adb_card_stays_when_the_app_names_its_serial(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Someone installing from the ADB card keeps it."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    [card] = _offers(hass)
    with (
        patch(HEALTH_CHECK, AsyncMock(side_effect=CannotConnectError)),
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            side_effect=RuntimeError,
        ),
    ):
        form = await hass.config_entries.flow.async_configure(card["flow_id"], {})
    assert form["step_id"] == "add_panel"

    await _app(hass, IPV6, _health())

    assert card["flow_id"] in [flow["flow_id"] for flow in _offers(hass)]


async def test_another_serial_or_none_keeps_both_cards(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Only the same serial folds; an app that names none folds nothing."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    await _app(hass, IPV6, _health(adb="9876543210987"))
    await _app(hass, "fd00::7", _health(did=OTHER_DID, adb=None))

    assert _unique_ids(hass) == sorted([ADB_UID, DID, OTHER_DID])


async def test_a_card_whose_address_now_answers_as_another_panel_folds_nothing(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The serial counts only from the panel the card was raised for."""
    adb_open.open.add(IPV4)
    await _app(hass, IPV6, _health(adb=None))

    await _adb_heard_while_app_answers(hass, IPV6, _health(did=OTHER_DID))

    assert _unique_ids(hass) == sorted([ADB_UID, DID])


async def test_an_added_panel_that_no_longer_answers_can_still_be_reinstalled(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Its remembered serial does not hide the ADB card that offers a reinstall."""
    adb_open.open.add(IPV4)
    _added(hass, DID, f"{IPV4}:8889", _health(), available=False)

    with patch(
        "custom_components.panel_assistant.discovery.async_probe_install_target",
        AsyncMock(return_value=_probe("install_candidate")),
    ):
        await _adb(hass, [IPV4])

    assert len(_offers(hass)) == 1


async def test_diagnostics_redact_the_serial(hass: HomeAssistant) -> None:
    """The serial is redacted from diagnostics like the panel's other identifiers."""
    from custom_components.panel_assistant.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    _added(hass, DID, f"[{IPV6}]:8888", _health())
    [entry] = hass.config_entries.async_entries(DOMAIN)
    entry.runtime_data.coordinator.last_update_success = True
    entry.runtime_data.coordinator.reachable = True
    entry.runtime_data.coordinator.connected = False
    entry.runtime_data.coordinator.data.status = None
    entry.runtime_data.coordinator.data.status_error = None

    data = await async_get_config_entry_diagnostics(hass, entry)

    assert data["health"]["adb_serial"] == "**REDACTED**"
    assert SERIAL not in str(data)


async def _app_among(
    hass: HomeAssistant, address: str, health: PanelHealth, lan: dict[str, PanelHealth]
) -> None:
    """The app's announcement at one address while other panels answer at theirs."""
    answers = {**lan, address: health}

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host in answers:
            return answers[client.address.host]
        raise CannotConnectError

    with patch(HEALTH_CHECK, answer):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address(address),
                ip_addresses=[ip_address(address)],
                port=8888,
                hostname=f"{health.panel_id}.local.",
                type="_ha-paneld._tcp.local.",
                name=f"{health.panel_id}._ha-paneld._tcp.local.",
                properties={"did": health.discovery_id, "name": "Wall panel"},
            ),
        )
        await hass.async_block_till_done()


async def test_an_app_naming_a_serial_no_waiting_app_reports_closes_the_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A waiting app that reports no serial does not stop the fold."""
    adb_open.open.add(IPV4)
    await _adb(hass, [IPV4])
    await _app(hass, IPV6, _health(adb=None))

    await _app_among(
        hass, "fd00::7", _health(did=OTHER_DID), lan={IPV6: _health(adb=None)}
    )

    assert _unique_ids(hass) == sorted([DID, OTHER_DID])


async def test_an_app_shown_while_the_adb_card_waits_on_its_probe_leaves_one_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The ADB flow looks again where it shows its card."""
    adb_open.open.add(IPV4)
    started, release = asyncio.Event(), asyncio.Event()
    original = discovery.async_check_migration_target

    async def held(*args):
        started.set()
        await release.wait()
        return await original(*args)

    with patch.object(discovery, "async_check_migration_target", held):
        adb = asyncio.create_task(_adb(hass, [IPV4]))
        await started.wait()
        await _app(hass, IPV6, _health())
        release.set()
        await adb

    assert _unique_ids(hass) == [DID]


async def test_an_adb_card_shown_while_the_app_flow_waits_leaves_one_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The app flow looks again where it shows its card."""
    adb_open.open.add(IPV4)
    started, release = asyncio.Event(), asyncio.Event()

    async def held(*_args):
        started.set()
        await release.wait()

    with patch.object(discovery, "_async_close_cards_answering_as", held):
        app = asyncio.create_task(_app(hass, IPV6, _health()))
        await started.wait()
        await _adb(hass, [IPV4])
        assert ADB_UID in _unique_ids(hass)
        release.set()
        await app

    assert _unique_ids(hass) == [DID]


async def test_a_finished_app_card_does_not_hide_a_later_adb_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """A serial remembered for a card that is gone folds nothing."""
    adb_open.open.add(IPV4)
    await _app(hass, IPV6, _health())
    [card] = _offers(hass)
    hass.config_entries.flow.async_abort(card["flow_id"])

    await _adb(hass, [IPV4])

    assert _unique_ids(hass) == [ADB_UID]


def _hold(name: str):
    """Hold one discovery step until released, to interleave two flows."""
    started, release = asyncio.Event(), asyncio.Event()
    original = getattr(discovery, name)

    async def held(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    return started, release, patch.object(discovery, name, held)


async def test_an_added_panel_back_in_reach_closes_the_adb_card_shown_meanwhile(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Its app announcing again at its stored address closes its ADB card."""
    adb_open.open.add(IPV4)
    _added(hass, DID, f"[{IPV6}]", _health(), available=False)
    await _adb(hass, [IPV4])
    assert _unique_ids(hass) == [ADB_UID]
    [entry] = hass.config_entries.async_entries(DOMAIN)
    entry.runtime_data.coordinator.available = True

    await _app(hass, IPV6, _health())

    assert _unique_ids(hass) == []


async def test_a_panel_added_while_the_adb_card_waits_on_its_probe_adds_no_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The ADB flow sees added panels, not only cards, where it shows its card."""
    adb_open.open.add(IPV4)
    started, release, held = _hold("async_check_migration_target")
    with held:
        adb = asyncio.create_task(_adb(hass, [IPV4]))
        await started.wait()
        _added(hass, DID, f"[{IPV6}]:8888", _health())
        release.set()
        await adb

    assert _unique_ids(hass) == []


async def test_an_adb_card_shown_while_an_added_panels_app_flow_waits_closes(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """An app flow that ends without a card of its own still closes the ADB card."""
    adb_open.open.add(IPV4)
    _added(hass, DID, "[fd00::9]:8888", _health(), available=False)
    started, release, held = _hold("_async_close_cards_answering_as")
    with held:
        app = asyncio.create_task(_app(hass, IPV6, _health()))
        await started.wait()
        await _adb(hass, [IPV4])
        assert ADB_UID in _unique_ids(hass)
        release.set()
        await app

    assert ADB_UID not in _unique_ids(hass)


async def test_a_card_another_integration_raised_first_closes_by_serial_too(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The ADB announcement's serial is kept with the card already showing."""
    adb_open.open.add(IPV4)
    _host_entry(hass, "shelly", "Wall display", IPV4)
    [candidate] = discovery._candidates(hass).values()
    with patch(HEALTH_CHECK, side_effect=CannotConnectError):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data=candidate,
        )
    assert len(_unique_ids(hass)) == 1
    await _adb(hass, [IPV4])

    await _app(hass, IPV6, _health())

    assert _unique_ids(hass) == [DID]


async def test_adb_heard_again_closes_the_adb_card_shown_before_the_app_knew(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Both cards showing, the app now reports: the next ADB announcement folds."""
    adb_open.open.add(IPV4)
    await _app(hass, IPV6, _health(adb=None))
    await _adb_heard_while_app_answers(hass, IPV6, _health(adb=None))
    assert _unique_ids(hass) == sorted([ADB_UID, DID])

    await _adb_heard_while_app_answers(hass, IPV6, _health())

    assert _unique_ids(hass) == [DID]


@pytest.mark.parametrize("first", ["adb", "app"])
async def test_an_app_that_learns_its_serial_after_both_cards_show_is_asked_again(
    hass: HomeAssistant, adb_open: SimpleNamespace, first: str
) -> None:
    """At a panel's start nothing announces again, so the card is asked once more."""
    adb_open.open.add(IPV4)
    known: dict[str, PanelHealth] = {IPV6: _health(adb=None)}

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host in known:
            return known[client.address.host]
        raise CannotConnectError

    with (
        patch.object(discovery, "_SERIAL_RECHECK_SECONDS", 0.05),
        patch(HEALTH_CHECK, answer),
    ):
        if first == "adb":
            await _adb_heard(hass)
            await _app_among(hass, IPV6, _health(adb=None), lan=known)
        else:
            await _app_among(hass, IPV6, _health(adb=None), lan=known)
            await _adb_heard(hass)
        assert _unique_ids(hass) == sorted([ADB_UID, DID])
        # The app has now heard its own network ADB.
        known[IPV6] = _health()
        await asyncio.sleep(0.2)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert _unique_ids(hass) == [DID]


async def test_an_app_card_dismissed_while_it_is_asked_does_not_hide_the_adb_card(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """Its answer arrives after the card is gone, so the ADB card is shown."""
    adb_open.open.add(IPV4)
    await _app(hass, IPV6, _health(adb=None))
    [card] = _offers(hass)

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host == IPV6:
            hass.config_entries.flow.async_abort(card["flow_id"])
            return _health()
        raise CannotConnectError

    with patch(HEALTH_CHECK, answer):
        await _adb_heard(hass)

    assert _unique_ids(hass) == [ADB_UID]


async def test_an_app_card_dismissed_during_the_later_ask_closes_nothing(
    hass: HomeAssistant, adb_open: SimpleNamespace
) -> None:
    """The same holds for the one later ask: only a card still showing counts."""
    adb_open.open.add(IPV4)
    dismiss: list[str] = []

    async def answer(client: HaPaneldClient) -> PanelHealth:
        if client.address.host != IPV6:
            raise CannotConnectError
        if dismiss:
            hass.config_entries.flow.async_abort(dismiss.pop())
            return _health()
        return _health(adb=None)

    with (
        patch.object(discovery, "_SERIAL_RECHECK_SECONDS", 0.05),
        patch(HEALTH_CHECK, answer),
    ):
        await _adb_heard(hass)
        await _app_among(hass, IPV6, _health(adb=None), lan={})
        [app_card] = [c for c in _offers(hass) if c["context"]["unique_id"] == DID]
        dismiss.append(app_card["flow_id"])
        await asyncio.sleep(0.2)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert _unique_ids(hass) == [ADB_UID]

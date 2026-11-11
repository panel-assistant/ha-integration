"""A panel that opens a session but cannot be reached is named, not lost.

Home Assistant cannot open connections to the panel, but the panel can open its
session to Home Assistant. Its refused hello is logged, and adding it at an
address it reported says that port 8888 is blocked instead of blaming the
address or the panel's power.
"""

import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientConnectorError, ServerDisconnectedError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_network import async_pin_install_target
from custom_components.panel_assistant.refused_hellos import async_refused_hello_at

from .config_flow_helpers import (
    _probe,
    _start_step,
    install_network_pin,  # noqa: F401  pins every typed name to 192.168.1.23
    install_release_catalog,  # noqa: F401
)
from .test_transport import (
    DID,
    WsClientFactory,
    _hello,
    _open,
    _send,
    entry,  # noqa: F401
)
from .test_upgrade_repairs import CURRENT, _entry, _set_up

PINNED = "192.168.1.23"
HEALTH_URL = f"http://{PINNED}:8888/api/v1/health"


def _refused() -> ClientConnectorError:
    """What a firewall that rejects the connection looks like to aiohttp."""
    key = SimpleNamespace(host=PINNED, port=8888, ssl=True)
    return ClientConnectorError(key, ConnectionRefusedError(111, "Connection refused"))


@pytest.fixture
async def setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Load the domain with no entry; port 8888 refuses; ADB is observed."""
    monkeypatch.setattr(AiohttpClientMockResponse, "connection", None, raising=False)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    with patch(
        "custom_components.panel_assistant.config_flow.async_probe_install_target",
        AsyncMock(return_value=_probe("adb_unreachable")),
    ) as probe:
        yield probe


async def _call_in(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    token: str,
    addresses: list[str],
) -> None:
    client = await hass_ws_client(hass, token)
    response = await _send(client, _hello(addresses=addresses))
    assert response["error"]["code"] == "unknown_panel"


async def _add(hass: HomeAssistant, address: str) -> dict[str, Any]:
    form = await _start_step(hass, "add_panel")
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ADDRESS: address}
    )


def _warnings(caplog: pytest.LogCaptureFixture, text: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and text in record.getMessage()
    ]


async def test_add_at_an_address_the_calling_panel_reported_names_port_8888(
    hass: HomeAssistant,
    setup: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hello is logged once with its addresses; the add names port 8888."""
    aioclient_mock.get(HEALTH_URL, exc=_refused())
    with caplog.at_level(logging.WARNING):
        await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])
        # The panel retries every 30 s; the log does not repeat with it.
        await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])
        # A new address is news.
        await _call_in(
            hass, hass_ws_client, hass_read_only_access_token, [PINNED, "fd00::23"]
        )
        result = await _add(hass, "panel.local")

    assert _warnings(caplog, "refused a panel session") == [
        "Panel Assistant refused a panel session: error=unknown_panel "
        f"panel={DID} addresses={addresses} connected_from=unknown; "
        "add the panel to accept it"
        for addresses in (PINNED, f"{PINNED},fd00::23")
    ]
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "panel_connected_unreachable"}
    # ADB has nothing to offer a panel that already runs the app.
    setup.assert_not_awaited()
    assert _warnings(caplog, "setup refused") == [
        "Panel Assistant setup refused: step=add_panel "
        "error=panel_connected_unreachable panel=panel.local "
        f"cause=panel {DID} opened a session from unknown reporting "
        f"{PINNED},fd00::23; "
        "port 8888: NoAnswerError from "
        f"{_refused()!r}"
    ]
    strings = await async_get_translations(hass, "en", "config", {DOMAIN})
    text = strings[f"component.{DOMAIN}.config.error.panel_connected_unreachable"]
    assert "has connected to Home Assistant" in text
    assert "port 8888" in text


async def test_a_calling_panel_outside_the_install_range_names_port_8888(
    hass: HomeAssistant,
    setup: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A carrier-grade NAT address it reported: the 8888 text, not the install rule."""
    aioclient_mock.get("http://100.127.77.20:8888/api/v1/health", exc=TimeoutError())
    await _call_in(hass, hass_ws_client, hass_read_only_access_token, ["100.127.77.20"])

    with patch(
        "custom_components.panel_assistant.config_flow.async_pin_install_target",
        async_pin_install_target,
    ):
        result = await _add(hass, "100.127.77.20")

    assert result["errors"] == {"base": "panel_connected_unreachable"}
    setup.assert_not_awaited()


async def test_an_address_the_calling_panel_did_not_report_keeps_the_old_answer(
    hass: HomeAssistant,
    setup: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Another panel's hello does not explain this address."""
    aioclient_mock.get(HEALTH_URL, exc=_refused())
    await _call_in(hass, hass_ws_client, hass_read_only_access_token, ["192.168.1.99"])

    with patch(
        "custom_components.panel_assistant.config_flow._async_host_answers",
        AsyncMock(return_value=False),
    ):
        result = await _add(hass, "panel.local")

    assert result["errors"] == {"base": "app_port_refused"}
    setup.assert_awaited_once()


async def test_a_panel_that_stopped_calling_in_is_not_named(
    hass: HomeAssistant,
    setup: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A hello older than half an hour proves nothing about the panel now."""
    aioclient_mock.get(HEALTH_URL, exc=_refused())
    await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])
    freezer.tick(timedelta(minutes=31))
    with patch(
        "custom_components.panel_assistant.config_flow._async_host_answers",
        AsyncMock(return_value=False),
    ):
        result = await _add(hass, "panel.local")

    assert result["errors"] == {"base": "app_port_refused"}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"text": "not panel health"}, "app_port_not_panel"),
        ({"status": 401}, "app_port_not_panel"),
        ({"status": 404}, "app_port_not_panel"),
        ({"status": 503}, "app_port_not_panel"),
        ({"exc": ServerDisconnectedError()}, "app_port_no_answer"),
    ],
    ids=[
        "not_health",
        "http_401",
        "http_404",
        "http_503",
        "dropped_reply",
    ],
)
async def test_an_address_where_something_else_answers_keeps_the_old_answer(
    hass: HomeAssistant,
    setup: AsyncMock,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    answer: dict[str, Any],
    expected: str,
) -> None:
    """Something answered on 8888, so no firewall is proved to block it."""
    aioclient_mock.get(HEALTH_URL, **answer)
    await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])

    with patch(
        "custom_components.panel_assistant.config_flow._async_host_answers",
        AsyncMock(return_value=True),
    ):
        result = await _add(hass, "panel.local")

    assert result["errors"] == {"base": expected}
    setup.assert_awaited_once()


async def test_a_panel_whose_entry_is_not_loaded_is_not_logged(
    hass: HomeAssistant,
    entry: Any,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At start-up or in setup retry the panel is known; it is not a new panel."""
    await hass.config_entries.async_unload(entry.entry_id)
    with caplog.at_level(logging.INFO):
        await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])

    assert not [r for r in caplog.records if "refused a panel session" in r.message]
    assert async_refused_hello_at(hass, PINNED) is None


async def test_a_removed_panel_calling_in_is_noted_not_warned(
    hass: HomeAssistant,
    entry: Any,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The owner removed it; it is still recorded, at INFO."""
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with caplog.at_level(logging.INFO):
        response = await _send(client, _hello(addresses=[PINNED]))

    assert response["error"]["code"] == "entry_removed"
    assert [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if "refused a panel session" in record.getMessage()
    ] == [
        (
            logging.INFO,
            "Panel Assistant refused a panel session: error=entry_removed "
            f"panel={DID} addresses={PINNED} connected_from=unknown; "
            "its entry was removed",
        )
    ]
    assert async_refused_hello_at(hass, PINNED) is not None


async def test_an_accepted_session_is_no_longer_a_calling_panel(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """Refused while it had no entry, then added: its refusal explains nothing."""
    assert await async_setup_component(hass, DOMAIN, {})
    await _call_in(hass, hass_ws_client, hass_read_only_access_token, [PINNED])
    assert async_refused_hello_at(hass, PINNED) is not None

    added = _entry(hass, hass_read_only_user.id)
    await _set_up(hass, added, CURRENT)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client, addresses=[PINNED])

    assert async_refused_hello_at(hass, PINNED) is None

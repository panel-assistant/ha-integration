"""A signed-in panel that cannot reach Home Assistant is told so, not to sign in."""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.foreign_ha import async_raise_foreign_ha_issue
from custom_components.panel_assistant.ha_url import DATA_INSTANCE_ID
from custom_components.panel_assistant.status import parse_status_response
from custom_components.panel_assistant.transport_binding import (
    async_record_binding_request,
)
from tests.test_repair_links import assert_repair_links

from .test_transport import (
    DID,
    _open,
    entry,  # noqa: F401
)

SAVED = "http://192.0.2.50:8123"
OTHER_ID = "2" * 32
LOGGER = "custom_components.panel_assistant.saved_account"


def _saved_account(url: Any) -> Any:
    """Make the panel report this saved server, as its configuration read does."""
    answer = (
        AsyncMock(side_effect=url)
        if isinstance(url, Exception)
        else AsyncMock(return_value=url)
    )
    return patch.object(HaPaneldClient, "async_get_configured_ha_url", answer)


async def _polls(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int
) -> None:
    for _ in range(count):
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _status(hass: HomeAssistant, entry: MockConfigEntry) -> str:  # noqa: F811
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_status"
    )
    assert entity_id is not None
    return hass.states.get(entity_id).state


def _repairs(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:  # noqa: F811
    return {
        issue.translation_key
        for key in ("not_signed_in", "cannot_reach_home_assistant")
        if (
            issue := ir.async_get(hass).async_get_issue(
                DOMAIN, f"{key}_{entry.entry_id}"
            )
        )
        is not None
    }


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.WARNING
    ]


async def test_a_signed_in_panel_that_cannot_connect_is_told_what_to_allow(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
) -> None:
    """The Repair and one log line name the address and port the panel is trying.

    A panel on a network that lets Home Assistant reach it, but not the other
    way, answers its health and never connects. It already holds an account
    here, so signing in again cannot help.
    """
    aioclient_mock.get(
        f"{SAVED}/api/panel_assistant/instance",
        json={"instance_id": hass.data[DOMAIN][DATA_INSTANCE_ID]},
    )
    with _saved_account(SAVED + "/"):
        await _polls(hass, freezer, 3)
        # Inside the grace a panel that is still connecting is left alone.
        assert _status(hass, entry) == "online"
        assert _repairs(hass, entry) == set()
        assert _warnings(caplog) == []

        # Past the grace, nothing is said before a poll has asked the panel.
        freezer.tick(timedelta(seconds=29))
        entry.runtime_data.coordinator.async_update_listeners()
        await hass.async_block_till_done()
        assert _status(hass, entry) == "online"
        assert _repairs(hass, entry) == set()

        await _polls(hass, freezer, 5)

        assert _status(hass, entry) == "cannot_reach_home_assistant"
        assert _repairs(hass, entry) == {"cannot_reach_home_assistant"}
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"cannot_reach_home_assistant_{entry.entry_id}"
        )
        assert issue is not None
        assert not issue.is_fixable and not issue.is_persistent
        assert_repair_links(issue)
        strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
        prefix = f"component.{DOMAIN}.issues.cannot_reach_home_assistant."
        text = "\n".join(
            strings[prefix + part].format(**issue.translation_placeholders)
            for part in ("title", "description")
        )
        assert SAVED in text
        assert "192.0.2.50 on TCP port 8123" in text
        assert "http://panel.local:8888/logs" in text
        assert "sign in to this Home Assistant" not in text
        # One line for the whole episode, however many polls it lasts.
        (warning,) = _warnings(caplog)
        assert "alpha" in warning and SAVED in warning
        assert "192.0.2.50 on TCP port 8123" in warning

    client = await hass_ws_client(hass, hass_read_only_access_token)
    await _open(client)
    await hass.async_block_till_done()

    assert _repairs(hass, entry) == set()
    assert _status(hass, entry) == "online"


@pytest.mark.parametrize(
    ("saved", "identity", "status"),
    [
        (None, None, "not_signed_in"),
        (CannotConnectError(), None, "not_signed_in"),
        (SAVED, OTHER_ID, "not_signed_in"),
        ("http://localhost:8123", None, "not_signed_in"),
        (SAVED, ClientError(), "cannot_reach_home_assistant"),
        ("https://ha.example", ClientError(), "cannot_reach_home_assistant"),
    ],
    ids=[
        "no-account",
        "unreadable",
        "another-server",
        "unusable-address",
        "server-does-not-answer",
        "default-port",
    ],
)
async def test_only_a_panel_with_an_account_here_is_told_about_its_network(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    saved: Any,
    identity: Any,
    status: str,
) -> None:
    """Sign-in advice stays for a panel with no account on this Home Assistant."""
    if isinstance(saved, str):
        answer = (
            {"exc": identity}
            if isinstance(identity, Exception)
            else {"json": {"instance_id": identity}}
        )
        aioclient_mock.get(f"{saved}/api/panel_assistant/instance", **answer)
    with _saved_account(saved):
        await _polls(hass, freezer, 6)

    assert _status(hass, entry) == status
    assert _repairs(hass, entry) == {status}
    assert len(_warnings(caplog)) == (status == "cannot_reach_home_assistant")
    if saved == "https://ha.example":
        issue = ir.async_get(hass).async_get_issue(DOMAIN, f"{status}_{entry.entry_id}")
        assert issue.translation_placeholders["port"] == "443"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _repairs(hass, entry) == set()


async def test_a_panel_that_stops_reporting_its_account_keeps_its_last_answer(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One failed read neither flips the advice nor logs the cause again."""
    aioclient_mock.get(f"{SAVED}/api/panel_assistant/instance", exc=ClientError())
    with _saved_account(SAVED):
        await _polls(hass, freezer, 6)
    with _saved_account(CannotConnectError()):
        await _polls(hass, freezer, 2)
        assert _repairs(hass, entry) == {"cannot_reach_home_assistant"}
    with _saved_account(SAVED):
        await _polls(hass, freezer, 2)

    assert _repairs(hass, entry) == {"cannot_reach_home_assistant"}
    assert len(_warnings(caplog)) == 1
    # Signing out on the panel returns the sign-in advice.
    with _saved_account(None):
        await _polls(hass, freezer, 1)
    assert _repairs(hass, entry) == {"not_signed_in"}
    assert _status(hass, entry) == "not_signed_in"


async def test_a_panel_that_connects_while_it_is_asked_is_not_reported(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    hass_ws_client: Any,
    hass_read_only_access_token: str,
) -> None:
    """A session that opens during the read ends the question."""
    aioclient_mock.get(f"{SAVED}/api/panel_assistant/instance", exc=ClientError())
    client = await hass_ws_client(hass, hass_read_only_access_token)
    asked, answer = asyncio.Event(), asyncio.Event()

    async def answer_once_connected(self: HaPaneldClient) -> str:
        asked.set()
        await answer.wait()
        return SAVED

    with patch.object(
        HaPaneldClient, "async_get_configured_ha_url", answer_once_connected
    ):
        freezer.tick(timedelta(minutes=3))
        async_fire_time_changed(hass)
        for _ in range(100):
            if asked.is_set():
                break
            await asyncio.sleep(0)
        assert asked.is_set()
        await _open(client)
        answer.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.runtime_data.coordinator.connected
    assert _warnings(caplog) == []
    assert _repairs(hass, entry) == set()
    assert _status(hass, entry) == "online"


async def test_a_panel_known_to_be_on_another_server_keeps_that_repair_alone(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
) -> None:
    """The Repair raised when the panel was added already says where it is."""
    aioclient_mock.get(f"{SAVED}/api/panel_assistant/instance", exc=ClientError())
    assert async_raise_foreign_ha_issue(hass, entry)
    with _saved_account(SAVED):
        await _polls(hass, freezer, 6)

    assert _repairs(hass, entry) == set()
    assert _status(hass, entry) == "another_home_assistant"


@pytest.mark.parametrize(
    ("refusal", "status"),
    [
        ("route_unavailable", "cannot_reach_home_assistant"),
        ("transport_failure", "cannot_reach_home_assistant"),
        ("credential_unavailable", "cannot_reach_home_assistant"),
        ("credential_rejected", "not_signed_in"),
        ("auth_invalid", "not_signed_in"),
        ("unknown_panel", "not_signed_in"),
    ],
)
async def test_a_panel_that_home_assistant_answered_is_not_told_about_its_network(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    refusal: str,
    status: str,
) -> None:
    """A refused account or hello reached Home Assistant: sign-in advice stays."""
    aioclient_mock.get(f"{SAVED}/api/panel_assistant/instance", exc=ClientError())
    reported = parse_status_response(
        json.dumps(
            {
                "warnings": [],
                "capabilities": [],
                "panel_assistant_transport": {"phase": "waiting", "refusal": refusal},
            }
        )
    )
    with (
        _saved_account(SAVED),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=reported)
        ),
    ):
        await _polls(hass, freezer, 6)

    assert _status(hass, entry) == status
    assert _repairs(hass, entry) == {status}
    assert len(_warnings(caplog)) == (status == "cannot_reach_home_assistant")


async def test_a_panel_waiting_for_its_account_to_be_confirmed_is_left_to_that(
    hass: HomeAssistant,
    entry: MockConfigEntry,  # noqa: F811
    freezer: FrozenDateTimeFactory,
    aioclient_mock: Any,
    caplog: pytest.LogCaptureFixture,
    hass_admin_user: Any,
) -> None:
    """A panel that reached Home Assistant under another account has its own Repair."""
    aioclient_mock.get(f"{SAVED}/api/panel_assistant/instance", exc=ClientError())
    answer = AsyncMock(return_value=SAVED)
    with patch.object(HaPaneldClient, "async_get_configured_ha_url", answer):
        await _polls(hass, freezer, 3)
        freezer.tick(timedelta(seconds=29))
        async_record_binding_request(hass, DID, hass_admin_user.id)
        await _polls(hass, freezer, 1)

    assert answer.await_count == 0
    assert _warnings(caplog) == []
    assert _repairs(hass, entry) == set()
    assert _status(hass, entry) == "online"

"""A wake word imported once in the sidebar reaches every panel, digest-checked."""

import asyncio
import base64
import dataclasses
import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN

from .test_embed import (  # noqa: F401  (fixtures)
    HEALTH,
    HELLO,
    RELAY,
    SessionPanel,
    WsClientFactory,
    _connect_panel,
    _load_entry,
    _ui,
    entry,
    panel,
    session_panels,
)

VOICE_HELLO = HELLO | {"capabilities": [*RELAY, "voice"]}
HALL_DID = "e" * 64
MANIFEST = (
    '{"type":"micro","wake_word":"Hey Pickle","model":"hey_pickle.tflite",'
    '"micro":{"probability_cutoff":0.9,"feature_step_size":10,"sliding_window_size":5}}'
)
MODEL = b"\x01tflite model bytes"
BUNDLED = {"id": "okay_nabu", "wake_word": "Okay Nabu", "trained_languages": ["en"]}


def _word(model: bytes) -> dict[str, Any]:
    return {
        "id": "hey_pickle",
        "wake_word": "Hey Pickle",
        "trained_languages": [],
        "sha256": hashlib.sha256(model).hexdigest(),
    }


async def _report(peer: SessionPanel, *words: dict[str, Any]) -> None:
    """Send the panel's voice configuration and wait for Home Assistant's answer."""
    peer.acknowledged.clear()
    await peer.ws.send_json_auto_id(
        {
            "type": "panel_assistant/voice_configuration",
            "session": peer.token,
            "enabled": True,
            "wake_words": [BUNDLED, *words],
            "active": ["okay_nabu"],
        }
    )
    async with asyncio.timeout(5):
        await peer.acknowledged.wait()
    assert peer.results[-1]["success"], peer.results[-1]


async def _installed(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome": "applied",
        "result": {"id": "hey_pickle", "wake_word": "Hey Pickle"},
    }


def _fetching(peer: SessionPanel, fetched: list[bytes]) -> Any:
    """Answer an install as the app does: fetch the blob, then say it is installed."""

    async def answer(event: dict[str, Any]) -> dict[str, Any]:
        response = await peer.http.get(event["args"]["blob"]["url"])
        assert response.status == 200
        fetched.append(await response.read())
        return await _installed(event)

    return answer


def _imports(peer: SessionPanel) -> list[dict[str, Any]]:
    return [e for e in peer.events if e.get("op") == "voice.wake_words.import"]


async def _until_imports(peer: SessionPanel, count: int) -> list[dict[str, Any]]:
    async with asyncio.timeout(5):
        while len(found := _imports(peer)) < count:
            peer.arrived.clear()
            await peer.arrived.wait()
    return found


@pytest.fixture
async def panels(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],  # noqa: F811
    entry: MockConfigEntry,  # noqa: F811
) -> tuple[SessionPanel, SessionPanel, MockConfigEntry]:
    """Two microphone panels with sessions: the entry's, and a second one."""
    assert await async_setup_component(hass, "homeassistant", {})
    hall = MockConfigEntry(
        domain=DOMAIN, title="Hall", data={CONF_ADDRESS: "127.0.0.1:1"}
    )
    hall.add_to_hass(hass)
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(
            return_value=dataclasses.replace(
                HEALTH, panel_id="beta", discovery_id=HALL_DID
            )
        ),
    ):
        await _load_entry(hass, hall)
    connected = []
    for each, hello in ((entry, VOICE_HELLO), (hall, VOICE_HELLO | {"did": HALL_DID})):
        connected.append(
            await _connect_panel(
                hass,
                hass_ws_client,
                hass_client,
                session_panels,
                each,
                hass_read_only_user.id,
                hass_read_only_access_token,
                hello,
            )
        )
    kitchen, hall_panel = connected
    for each in connected:
        await _report(each)
    return kitchen, hall_panel, hall


async def _import_in_sidebar(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    kitchen: SessionPanel,
    entry_id: str,
) -> None:
    """Import the model on one panel's Configure page, through the sidebar relay."""
    kitchen.answers["voice.wake_words.set"] = _installed
    admin = await hass_ws_client(hass)
    reply = await _ui(
        admin,
        entry_id,
        {
            "kind": "manage",
            "command_id": "w1",
            "op": "voice.wake_words.set",
            "args": {
                "name": "hey_pickle.json",
                "manifest": MANIFEST,
                "model": base64.b64encode(MODEL).decode(),
            },
        },
    )
    assert reply["result"]["outcome"] == "applied", reply
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_wake_word_imported_in_the_sidebar_is_installed_on_every_other_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    fetched: list[bytes] = []
    hall.answers["voice.wake_words.import"] = _fetching(hall, fetched)

    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)

    request = (await hall.event("voice.wake_words.import"))["args"]
    assert (request["name"], request["manifest"], request["replaces"]) == (
        "hey_pickle",
        MANIFEST,
        [],
    )
    reference = request["blob"]
    await hass.async_block_till_done(wait_background_tasks=True)
    # The bytes the panel fetches are the model, and the reference names their digest.
    assert fetched == [MODEL]
    assert reference["size"] == len(MODEL)
    assert reference["sha256"] == hashlib.sha256(MODEL).hexdigest()
    # The panel the owner imported on already has it, so it is not asked again.
    assert _imports(kitchen) == []

    # Once the panel reports the copy, nothing more is sent, however often it reports.
    await _report(hall, _word(MODEL))
    await _report(hall, _word(MODEL))
    await _report(kitchen, _word(MODEL))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_imports(hall)) == 1
    assert _imports(kitchen) == []


async def test_a_panel_that_connects_later_gets_the_wake_word_after_a_restart(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    session_panels: list[SessionPanel],  # noqa: F811
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, hall_entry = panels
    hall.answers["voice.wake_words.import"] = _installed
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    await hall.event("voice.wake_words.import")

    # Panel Assistant forgets everything it holds in memory, as a restart does;
    # the panel's next session still gets the model, from what was stored.
    hass.data[DOMAIN].pop("shared_wake_words")
    again = await _connect_panel(
        hass,
        hass_ws_client,
        hass_client,
        session_panels,
        hall_entry,
        hass_read_only_user.id,
        hass_read_only_access_token,
        VOICE_HELLO | {"did": HALL_DID},
    )
    fetched: list[bytes] = []
    again.answers["voice.wake_words.import"] = _fetching(again, fetched)
    await _report(again)
    request = (await again.event("voice.wake_words.import"))["args"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fetched == [MODEL]
    assert request["blob"]["sha256"] == hashlib.sha256(MODEL).hexdigest()


async def test_an_owners_own_model_of_the_same_name_is_kept_and_reported(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, hall_entry = panels
    await _report(hall, _word(b"the owner's own model"))

    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)

    assert _imports(hall) == []
    issue_id = f"wake_word_{hall_entry.entry_id}_hey_pickle"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "wake_word_kept"
    assert issue.translation_placeholders["wake_word"] == "Hey Pickle"
    # The owner imports the shared model on that panel too: the issue goes.
    await _report(hall, _word(MODEL))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_newer_model_replaces_the_copy_panel_assistant_sent_before(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    hall.answers["voice.wake_words.import"] = _installed
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    await hall.event("voice.wake_words.import")
    await _report(hall, _word(MODEL))

    newer = b"\x01a retrained model"
    kitchen.answers["voice.wake_words.set"] = _installed
    admin = await hass_ws_client(hass)
    await _ui(
        admin,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "w2",
            "op": "voice.wake_words.set",
            "args": {
                "name": "hey_pickle",
                "manifest": MANIFEST,
                "model": base64.b64encode(newer).decode(),
            },
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    request = (await _until_imports(hall, 2))[-1]["args"]
    assert request["replaces"] == [hashlib.sha256(MODEL).hexdigest()]
    assert request["blob"]["sha256"] == hashlib.sha256(newer).hexdigest()


async def test_a_panel_that_refuses_the_model_raises_a_repair_and_is_not_asked_again(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, hall_entry = panels

    async def refused(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "outcome": "refused",
            "code": "invalid_value",
            "result": {"error": "the model was not accepted by the wake-word engine"},
        }

    hall.answers["voice.wake_words.import"] = refused
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    await hall.event("voice.wake_words.import")
    await hass.async_block_till_done(wait_background_tasks=True)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"wake_word_{hall_entry.entry_id}_hey_pickle"
    )
    assert issue is not None
    assert issue.translation_key == "wake_word_refused"
    assert issue.translation_placeholders["reason"] == (
        "the model was not accepted by the wake-word engine"
    )
    await _report(hall)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_imports(hall)) == 1


async def test_a_panel_app_too_old_for_shared_wake_words_gets_a_warning_not_a_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
    caplog: pytest.LogCaptureFixture,
) -> None:
    kitchen, hall, hall_entry = panels
    # The fake panel answers a name it does not serve with unknown_command,
    # as an older app does.
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    await hall.event("voice.wake_words.import")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"wake_word_{hall_entry.entry_id}_hey_pickle"
        )
        is None
    )
    assert "cannot take shared wake words until its app is updated" in caplog.text

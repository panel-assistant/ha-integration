"""A wake word imported once in the sidebar reaches every panel, digest-checked."""

import asyncio
import base64
import dataclasses
import hashlib
import json
import threading
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import wake_words
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


async def test_a_wake_word_imported_while_a_panel_installs_another_reaches_it_too(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    admin = await hass_ws_client(hass)
    second = b"\x01another model"

    async def install_while_another_is_imported(
        event: dict[str, Any],
    ) -> dict[str, Any]:
        if event["args"]["name"] == "hey_pickle":
            # The administrator imports a second wake word while this one installs.
            async def other(_: dict[str, Any]) -> dict[str, Any]:
                return {
                    "outcome": "applied",
                    "result": {"id": "hey_radish", "wake_word": "Hey Radish"},
                }

            kitchen.answers["voice.wake_words.set"] = other
            await _ui(
                admin,
                entry.entry_id,
                {
                    "kind": "manage",
                    "command_id": "w2",
                    "op": "voice.wake_words.set",
                    "args": {
                        "name": "hey_radish",
                        "manifest": MANIFEST,
                        "model": base64.b64encode(second).decode(),
                    },
                },
            )
            await hass.async_block_till_done(wait_background_tasks=False)
            await asyncio.sleep(0.2)
        return {"outcome": "applied", "result": {"id": event["args"]["name"]}}

    hall.answers["voice.wake_words.import"] = install_while_another_is_imported
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    names = [e["args"]["name"] for e in await _until_imports(hall, 2)]
    assert sorted(names) == ["hey_pickle", "hey_radish"]


async def test_the_panel_imported_on_reporting_the_new_copy_early_gets_no_repair(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    hall.answers["voice.wake_words.import"] = _installed
    await _import_in_sidebar(hass, hass_ws_client, kitchen, entry.entry_id)
    await _report(kitchen, _word(MODEL))
    newer = b"\x01a retrained model"

    async def installed_and_reported_first(event: dict[str, Any]) -> dict[str, Any]:
        # The app reports its new wake words before it answers the import.
        await kitchen.ws.send_json_auto_id(
            {
                "type": "panel_assistant/voice_configuration",
                "session": kitchen.token,
                "enabled": True,
                "wake_words": [BUNDLED, _word(newer)],
                "active": ["okay_nabu"],
            }
        )
        await asyncio.sleep(0.2)
        return await _installed(event)

    kitchen.answers["voice.wake_words.set"] = installed_and_reported_first
    admin = await hass_ws_client(hass)
    reply = await _ui(
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
    assert reply["result"]["outcome"] == "applied"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"wake_word_{entry.entry_id}_hey_pickle"
        )
        is None
    )


async def _import_together(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    kitchen: SessionPanel,
    entry_id: str,
    models: dict[str, bytes],
) -> None:
    """Two administrators import at once on one panel's Configure page."""
    by_name = {name: name.removesuffix(".json") for name in models}

    async def installed(event: dict[str, Any]) -> dict[str, Any]:
        name = by_name[event["args"]["name"]]
        return {"outcome": "applied", "result": {"id": name, "wake_word": name}}

    kitchen.answers["voice.wake_words.set"] = installed
    admins = [await hass_ws_client(hass) for _ in models]
    replies = await asyncio.gather(
        *(
            _ui(
                admin,
                entry_id,
                {
                    "kind": "manage",
                    "command_id": f"w{index}",
                    "op": "voice.wake_words.set",
                    "args": {
                        "name": name,
                        "manifest": MANIFEST,
                        "model": base64.b64encode(model).decode(),
                    },
                },
            )
            for index, (admin, (name, model)) in enumerate(
                zip(admins, models.items(), strict=True)
            )
        )
    )
    assert all(reply["result"]["outcome"] == "applied" for reply in replies)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_two_wake_words_imported_at_once_both_reach_the_other_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    first, second = b"\x01first model", b"\x01second model"
    fetched: list[bytes] = []
    hall.answers["voice.wake_words.import"] = _fetching(hall, fetched)
    written = wake_words._write_atomically
    removed = wake_words._remove_unnamed
    cleaned = threading.Event()

    def write(path: Any, data: bytes) -> None:
        written(path, data)
        if data == second:
            # Hold the second record between its file and its index entry until
            # the other record has removed the files its index does not name.
            cleaned.wait(2)

    def remove(directory: Any, named: set[str]) -> None:
        removed(directory, named)
        cleaned.set()

    with (
        patch.object(wake_words, "_write_atomically", write),
        patch.object(wake_words, "_remove_unnamed", remove),
    ):
        await _import_together(
            hass,
            hass_ws_client,
            kitchen,
            entry.entry_id,
            {"hey_first.json": first, "hey_second.json": second},
        )
    await _until_imports(hall, 2)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sorted(fetched) == sorted([first, second])


async def test_two_imports_of_one_name_at_once_keep_the_earlier_copy_replaceable(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    kitchen, hall, _ = panels
    one, other = b"\x01one model", b"\x01other model"
    hall.answers["voice.wake_words.import"] = _installed
    written = wake_words._write_atomically
    second_started = threading.Event()
    writes: list[bytes] = []

    def write(path: Any, data: bytes) -> None:
        writes.append(data)
        if len(writes) == 1:
            # Hold the first record mid-way until the second has begun its own.
            second_started.wait(2)
        else:
            second_started.set()
        written(path, data)

    with patch.object(wake_words, "_write_atomically", write):
        await _import_together(
            hass,
            hass_ws_client,
            kitchen,
            entry.entry_id,
            {"hey_pickle.json": one, "hey_pickle": other},
        )
    imports = await _until_imports(hall, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    last = _imports(hall)[-1]["args"]
    kept, earlier = (writes[-1], writes[0]) if len(writes) == 2 else (writes[0], None)
    assert last["blob"]["sha256"] == hashlib.sha256(kept).hexdigest()
    # The copy kept first stays one the newer model may replace on a panel.
    assert earlier is not None
    assert last["replaces"] == [hashlib.sha256(earlier).hexdigest()], imports


@pytest.mark.parametrize("arena", [2147483647, 1048577, -1])
async def test_an_oversized_arena_is_not_retained_or_distributed(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
    arena: int,
) -> None:
    kitchen, hall, _ = panels
    kitchen.answers["voice.wake_words.set"] = _installed
    hall.answers["voice.wake_words.import"] = _installed
    manifest = json.loads(MANIFEST)
    manifest["micro"]["tensor_arena_size"] = arena
    admin = await hass_ws_client(hass)
    reply = await _ui(
        admin,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "arena",
            "op": "voice.wake_words.set",
            "args": {
                "name": "hey_pickle.json",
                "manifest": json.dumps(manifest),
                "model": base64.b64encode(MODEL).decode(),
            },
        },
    )
    assert reply["result"]["outcome"] == "applied", reply
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not _imports(hall)
    from homeassistant.helpers.storage import Store

    stored = await Store(
        hass, wake_words.STORAGE_VERSION, wake_words.STORAGE_KEY
    ).async_load()
    assert not stored or not stored.get("words")


@pytest.mark.parametrize("arena", [0, 1048576])
async def test_bounded_arena_is_shared_through_the_sidebar(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    entry: MockConfigEntry,  # noqa: F811
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
    arena: int,
) -> None:
    kitchen, hall, _ = panels
    kitchen.answers["voice.wake_words.set"] = _installed
    hall.answers["voice.wake_words.import"] = _installed
    manifest = json.loads(MANIFEST)
    manifest["micro"]["tensor_arena_size"] = arena
    admin = await hass_ws_client(hass)
    reply = await _ui(
        admin,
        entry.entry_id,
        {
            "kind": "manage",
            "command_id": "bounded",
            "op": "voice.wake_words.set",
            "args": {
                "name": "hey_pickle.json",
                "manifest": json.dumps(manifest),
                "model": base64.b64encode(MODEL).decode(),
            },
        },
    )
    assert reply["result"]["outcome"] == "applied", reply
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(_imports(hall)) == 1
    assert (
        json.loads(_imports(hall)[0]["args"]["manifest"])["micro"]["tensor_arena_size"]
        == arena
    )


async def test_retained_oversized_arena_is_not_redistributed_after_restart(
    hass: HomeAssistant,
    panels: tuple[SessionPanel, SessionPanel, MockConfigEntry],
) -> None:
    from homeassistant.helpers.storage import Store

    _, hall, _ = panels
    from pathlib import Path

    model_dir = Path(hass.config.path(".storage", f"{DOMAIN}.wake_word_models"))
    await hass.async_add_executor_job(
        lambda: model_dir.mkdir(parents=True, exist_ok=True)
    )
    (model_dir / f"{hashlib.sha256(MODEL).hexdigest()}.tflite").write_bytes(MODEL)
    manifest = json.loads(MANIFEST)
    manifest["micro"]["tensor_arena_size"] = 2147483647
    await Store(hass, wake_words.STORAGE_VERSION, wake_words.STORAGE_KEY).async_save(
        {
            "words": [
                {
                    "id": "hey_pickle",
                    "phrase": "Hey Pickle",
                    "manifest": json.dumps(manifest),
                    "sha256": hashlib.sha256(MODEL).hexdigest(),
                    "earlier": [],
                }
            ]
        }
    )
    hass.data[DOMAIN].pop("shared_wake_words", None)
    hall.answers["voice.wake_words.import"] = _installed
    await _report(hall)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not _imports(hall)

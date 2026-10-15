"""Panel voice commands: what each locale's sentences do, said to a panel.

Requests go through Home Assistant's own conversation agent, with the panel's
device as the requester, so they take the route a spoken request takes from
the panel's satellite. Every locale's grammar is also checked against Home
Assistant's built-in sentences: the build fails if a locale's phrase would take
over a request a built-in intent understands.
"""

import asyncio
from typing import Any

import pytest
from homeassistant.components import conversation
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN

from . import voice_grammar
from .test_native import DESCRIPTORS, _setup
from .test_transport import WsClientFactory
from .test_transport_commands import Panel, _connect, _entity_id

LANGUAGES = voice_grammar.grammar_languages()


@pytest.mark.parametrize("builtin_language", LANGUAGES)
def test_no_locale_takes_over_a_built_in_request(builtin_language: str) -> None:
    """Every locale's triggers, as Core merges them, against one locale's built-ins."""
    assert voice_grammar.stolen(builtin_language, tuple(LANGUAGES)) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_built_in_intent_claims_none_of_a_locales_sentences(language: str) -> None:
    assert voice_grammar.claimed(language) == []


def test_no_two_commands_accept_the_same_request() -> None:
    assert voice_grammar.overlaps(tuple(LANGUAGES)) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_sample_reaches_its_command_and_the_screen_its_built_in(
    language: str,
) -> None:
    assert voice_grammar.sample_errors(language, tuple(LANGUAGES)) == []
    assert voice_grammar.builtin_errors(language) == []


def test_every_locale_answers_with_the_same_replies() -> None:
    english = voice_grammar.load_grammar("en")
    for language in LANGUAGES:
        grammar = voice_grammar.load_grammar(language)
        assert grammar["language"] == language
        assert set(grammar["commands"]) == set(english["commands"]), language
        assert set(grammar["responses"]) == set(english["responses"]), language
        for key, reply in english["responses"].items():
            placeholders = "{page}" in reply
            assert ("{page}" in grammar["responses"][key]) == placeholders, (
                language,
                key,
            )
        assert grammar["home_pages"], language
        assert grammar["screen_names"], language


def _sample(language: str, command: str) -> dict[str, Any]:
    return next(
        sample
        for sample in voice_grammar.load_samples(language)["match"]
        if sample["command"] == command
    )


async def _ready(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    user: Any,
    token: str,
    language: str,
    views: list[dict[str, Any]] | None = None,
    channels: list[dict[str, Any]] | None = None,
) -> tuple[MockConfigEntry, Panel, str]:
    hass.config.language = language
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "lovelace", {})
    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": views or []})
    entry = await _setup(hass, user.id)
    panel = await _connect(hass, hass_ws_client, token) if channels is None else None
    if panel is None:
        from . import test_transport_commands

        hello = test_transport_commands._hello()
        hello["channels"] = channels
        client = await hass_ws_client(hass, token)
        await client.send_json_auto_id(hello)
        from .test_transport import _receive

        panel = Panel(client, await _receive(client))
        await panel.sync(hass)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    return entry, panel, device.id


def _say(
    hass: HomeAssistant, text: str, language: str, device_id: str | None
) -> asyncio.Task[conversation.ConversationResult]:
    return asyncio.ensure_future(
        conversation.async_converse(
            hass, text, None, Context(), language=language, device_id=device_id
        )
    )


def _speech(result: conversation.ConversationResult) -> str:
    speech: str = result.response.speech["plain"]["speech"]
    return speech


@pytest.mark.parametrize("language", LANGUAGES)
async def test_show_a_page_navigates_the_panel_that_heard_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    language: str,
) -> None:
    sample = _sample(language, "show_page")
    _, panel, device_id = await _ready(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        language,
        views=[
            {"title": "Overview", "path": "home"},
            {"title": sample["page"], "path": "view-two"},
        ],
    )
    said = _say(hass, sample["text"], language, device_id)
    command = await panel.command()
    assert (command["channel"], command["value"]) == ("navigate", "/lovelace/view-two")
    await panel.answer(command["command_id"], "applied")
    replies = voice_grammar.load_grammar(language)["responses"]
    assert _speech(await said) == replies["show_page"].format(page=sample["page"])


@pytest.mark.parametrize("command", ["android_settings", "launcher"])
@pytest.mark.parametrize("language", LANGUAGES)
async def test_android_settings_and_the_launcher_open_on_that_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    language: str,
    command: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, language
    )
    said = _say(hass, _sample(language, command)["text"], language, device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == (command, None)
    await panel.answer(sent["command_id"], "applied")
    replies = voice_grammar.load_grammar(language)["responses"]
    assert _speech(await said) == replies[command]


@pytest.mark.parametrize("language", LANGUAGES)
async def test_turn_off_the_screen_reaches_the_screen_through_its_alias(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    language: str,
) -> None:
    """A built-in request: Home Assistant's own intent finds the screen by name."""
    entry, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, language
    )
    screen = _entity_id(hass, entry, "screen")
    assert (
        voice_grammar.screen_alias(language)
        in er.async_get(hass).async_get(screen).aliases
    )
    said = _say(
        hass,
        voice_grammar.load_samples(language)["builtin"]["screen_off"],
        language,
        device_id,
    )
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("screen", {"on": False})
    await panel.answer(sent["command_id"], "applied")
    result = await said
    assert (
        result.response.response_type
        == conversation.intent.IntentResponseType.ACTION_DONE
    )


async def test_an_owner_removed_alias_is_not_added_back(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, _, _ = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    registry = er.async_get(hass)
    screen = _entity_id(hass, entry, "screen")
    registry.async_update_entity(screen, aliases=[er.COMPUTED_NAME])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(screen).aliases == [er.COMPUTED_NAME]


async def test_said_away_from_a_panel_it_says_so(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, _ = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    result = await _say(hass, "show android settings", "en", None)
    assert (
        _speech(result) == voice_grammar.load_grammar("en")["responses"]["not_a_panel"]
    )
    await panel.nothing_sent()


async def test_an_unknown_page_is_named_and_nothing_is_sent(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    result = await _say(hass, "show the garden page", "en", device_id)
    replies = voice_grammar.load_grammar("en")["responses"]
    assert _speech(result) == replies["page_unknown"].format(page="garden")
    await panel.nothing_sent()


async def test_a_home_word_shows_the_home_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    said = _say(hass, "show the home page", "en", device_id)
    command = await panel.command()
    assert (command["channel"], command["value"]) == ("navigate", "/lovelace")
    await panel.answer(command["command_id"], "applied")
    await said


async def test_an_older_panel_app_asks_for_an_update(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    older = [
        d for d in DESCRIPTORS if d["channel"] not in ("android_settings", "launcher")
    ]
    _, panel, device_id = await _ready(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        "en",
        channels=older,
    )
    result = await _say(hass, "show android settings", "en", device_id)
    assert (
        _speech(result) == voice_grammar.load_grammar("en")["responses"]["needs_update"]
    )
    await panel.nothing_sent()


async def test_kiosk_lock_keeps_the_panel_on_its_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    from .test_native import _report
    from .test_transport import _send

    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    locked = {"channel": "kiosk_lock", "state": "known", "value": True}
    assert (await _send(panel.client, _report(panel.token, "delta", [locked])))[
        "success"
    ]
    await hass.async_block_till_done()
    result = await _say(hass, "show the launcher", "en", device_id)
    assert (
        _speech(result) == voice_grammar.load_grammar("en")["responses"]["panel_locked"]
    )
    await panel.nothing_sent()

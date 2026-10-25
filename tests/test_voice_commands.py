"""Panel voice commands: what each locale's sentences do, said to a panel.

Requests go through Home Assistant's own conversation agent, with the panel's
device as the requester, so they take the route a spoken request takes from
the panel's satellite. Every locale's grammar is also checked against Home
Assistant's built-in sentences: the build fails if a locale's phrase would take
over a request a built-in intent understands.
"""

import asyncio
import re
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
from .test_repository import TRANSLATED_LANGUAGES
from .test_transport import WsClientFactory
from .test_transport_commands import Panel, _connect, _entity_id

LANGUAGES = voice_grammar.grammar_languages()


def test_every_shipped_language_has_a_grammar() -> None:
    """A missing locale fails here rather than leaving its checks uncollected."""
    assert set(LANGUAGES) == {"en", *TRANSLATED_LANGUAGES}


@pytest.mark.parametrize("builtin_language", LANGUAGES)
def test_no_locale_takes_over_a_built_in_request(builtin_language: str) -> None:
    """Every locale's triggers, as Core merges them, against one locale's built-ins."""
    assert voice_grammar.stolen(builtin_language, tuple(LANGUAGES)) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_built_in_intent_claims_none_of_a_locales_sentences(language: str) -> None:
    assert voice_grammar.claimed(language) == []


def test_no_two_commands_accept_the_same_request() -> None:
    assert voice_grammar.overlaps(tuple(LANGUAGES)) == []


def test_the_checker_sees_built_in_list_words(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A word from a built-in list ("lauter", "na maksa") is a built-in request,
    and a named panel never stands for a built-in number."""
    probe = tmp_path / "probe.yaml"
    probe.write_text(
        "commands:\n"
        "  loud: {sentences: [lauter, volume up]}\n"
        "  bright: {sentences: [jasność na maksa]}\n",
        "utf-8",
    )
    named = tmp_path / "nl.yaml"
    named.write_text("commands:\n  dim: {sentences: ['dimmen op het {panel}']}\n")
    monkeypatch.setitem(voice_grammar.DRAFTS, "probe", probe)
    monkeypatch.setitem(voice_grammar.DRAFTS, "nl", named)
    voice_grammar.load_grammar.cache_clear()
    try:
        taken = {
            (c.builtin_intent, c.command)
            for language in ("de", "en", "pl")
            for c in voice_grammar.stolen(language, ("probe",))
        }
        assert taken == {
            ("HassSetVolumeRelative", "loud"),
            ("HassLightSet", "bright"),
        }
        assert voice_grammar.claimed("nl") == []
    finally:
        voice_grammar.load_grammar.cache_clear()


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_sample_reaches_its_command_and_the_screen_its_built_in(
    language: str,
) -> None:
    assert voice_grammar.sample_errors(language, tuple(LANGUAGES)) == []
    assert voice_grammar.builtin_errors(language) == []


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", text))


def test_every_locale_answers_with_the_same_replies() -> None:
    english = voice_grammar.load_grammar("en")
    for language in LANGUAGES:
        grammar = voice_grammar.load_grammar(language)
        assert grammar["language"] == language
        assert set(grammar["commands"]) == set(english["commands"]), language
        assert set(grammar["responses"]) == set(english["responses"]), language
        for key, reply in english["responses"].items():
            assert _placeholders(grammar["responses"][key]) == _placeholders(reply), (
                language,
                key,
            )
        for words in ("home_pages", "back_pages", "panel_words", "screen_names"):
            assert grammar[words], (language, words)


def _sample(language: str, command: str) -> dict[str, Any]:
    """The locale's first sample of ``command`` said to the panel itself."""
    return next(
        sample
        for sample in voice_grammar.load_samples(language)["match"]
        if sample["command"] == command
        and "panel" not in sample
        and (command != "show_page" or sample["page"] not in _back_words())
    )


def _back_words() -> set[str]:
    return {
        word
        for language in LANGUAGES
        for word in voice_grammar.load_grammar(language)["back_pages"]
    }


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
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
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


@pytest.mark.parametrize("command", ["android_settings", "launcher", "recents", "back"])
@pytest.mark.parametrize("language", LANGUAGES)
async def test_app_actions_run_on_the_panel_that_heard_them(
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


def _named(language: str) -> dict[str, Any]:
    """The locale's first sample naming a panel for a command that always sends."""
    sends = ("screen_sleep", "screen_wake", "launcher", "android_settings")
    return next(
        s
        for s in voice_grammar.load_samples(language)["match"]
        if "panel" in s and s["command"] in sends
    )


@pytest.mark.parametrize(
    ("language", "name"),
    [
        *((language, None) for language in LANGUAGES),
        ("de", "Küchen"),
        ("pt-BR", "Cozinha"),
    ],
)
async def test_a_panel_named_in_any_locale_carries_out_the_request(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    language: str,
    name: str | None,
) -> None:
    """Said away from any panel; the panel is called what the sample names, or
    that name without its word for panel ("Küchen" for "Küchenpanel")."""
    sample = _named(language)
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, language
    )
    dr.async_get(hass).async_update_device(
        device_id, name_by_user=name or sample["panel"]
    )
    said = _say(hass, sample["text"], language, None)
    sent = await panel.command()
    await panel.answer(sent["command_id"], "applied")
    reply = _speech(await said)
    replies = voice_grammar.load_grammar(language)["responses"]
    assert reply not in (
        replies["panel_unknown"].format(panel=sample["panel"]),
        replies["not_a_panel"],
    )


async def test_a_panel_that_cannot_press_back_says_so(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """No root and no accessibility service: the panel states Back unsupported."""
    no_back = [d for d in DESCRIPTORS if d["channel"] != "back"]
    _, panel, device_id = await _ready(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        "en",
        channels=no_back,
    )
    result = await _say(hass, "previous page", "en", device_id)
    assert _speech(result) == voice_grammar.load_grammar("en")["responses"]["back_none"]
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
    replies = voice_grammar.load_grammar("en")["responses"]
    for request in ("show the launcher", "show recent apps"):
        result = await _say(hass, request, "en", device_id)
        assert _speech(result) == replies["panel_locked"]
        await panel.nothing_sent()
    # Going back stays on the dashboard, which kiosk lock allows.
    said = _say(hass, "previous page", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("back", None)
    await panel.answer(sent["command_id"], "applied")
    await said


async def test_a_page_is_found_on_the_account_default_dashboard(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """A panel that reports no home dashboard shows its account's default."""
    from homeassistant.components.frontend import storage
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    dashboard = LovelaceStorage(
        hass,
        {
            "id": "panel_home",
            "url_path": "panel-home",
            "title": "Panel",
            "mode": "storage",
        },
    )
    hass.data[LOVELACE_DATA].dashboards["panel-home"] = dashboard
    await dashboard.async_save({"views": [{"title": "Cameras", "path": "cams"}]})
    user = await storage.async_user_store(hass, hass_read_only_user.id)
    await user.async_set_item("core", {"default_panel": "panel-home"})

    said = _say(hass, "show the cameras page", "en", device_id)
    command = await panel.command()
    assert (command["channel"], command["value"]) == ("navigate", "/panel-home/cams")
    await panel.answer(command["command_id"], "applied")
    await said


async def _report_state(panel: Panel, hass: HomeAssistant, **values: Any) -> None:
    """The panel reports these channel values, as it does when they change."""
    from .test_native import _report
    from .test_transport import _send

    observations = [
        {"channel": channel, "state": "known", "value": value}
        for channel, value in values.items()
    ]
    response = await _send(panel.client, _report(panel.token, "delta", observations))
    assert response["success"], response
    await hass.async_block_till_done()


# Said at brightness 128 (50 %) and volume 50: what each command sends and the
# value its reply names.
STEPPED = {
    "screen_wake": (("screen", {"on": True}), {}),
    "screen_sleep": (("screen", {"on": False}), {}),
    "screen_brighter": (
        ("screen", {"on": True, "brightness": 153}),
        {"brightness": 60},
    ),
    "screen_dimmer": (("screen", {"on": True, "brightness": 102}), {"brightness": 40}),
    "volume_up": (("volume", 60), {"volume": 60}),
    "volume_down": (("volume", 40), {"volume": 40}),
}


@pytest.mark.parametrize("command", list(STEPPED))
@pytest.mark.parametrize("language", LANGUAGES)
async def test_the_screen_and_volume_commands_act_on_the_panel_that_heard_them(
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
    await _report_state(panel, hass, screen={"on": True, "brightness": 128}, volume=50)
    said = _say(hass, _sample(language, command)["text"], language, device_id)
    sent = await panel.command()
    (channel, value), named = STEPPED[command]
    assert (sent["channel"], sent["value"]) == (channel, value)
    await panel.answer(sent["command_id"], "applied")
    replies = voice_grammar.load_grammar(language)["responses"]
    assert _speech(await said) == replies[command].format(**named)


@pytest.mark.parametrize(
    ("command", "state", "reply"),
    [
        ("screen_dimmer", {"screen": {"on": True, "brightness": 10}}, "screen_dimmest"),
        (
            "screen_brighter",
            {"screen": {"on": True, "brightness": 255}},
            "screen_brightest",
        ),
        ("screen_dimmer", {"screen": {"on": False}}, "screen_dark"),
        ("volume_up", {"volume": 100}, "volume_max"),
        ("volume_down", {"volume": 0}, "volume_min"),
    ],
)
async def test_a_step_past_the_end_sends_nothing_and_says_so(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    command: str,
    state: dict[str, Any],
    reply: str,
) -> None:
    """Never dimmed to off: the dimmest step stays lit."""
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    await _report_state(panel, hass, **state)
    result = await _say(hass, _sample("en", command)["text"], "en", device_id)
    assert _speech(result) == voice_grammar.load_grammar("en")["responses"][reply]
    await panel.nothing_sent()


async def test_brighter_on_a_dark_screen_wakes_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    said = _say(hass, "brighten the screen", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("screen", {"on": True})
    await panel.answer(sent["command_id"], "applied")
    replies = voice_grammar.load_grammar("en")["responses"]
    assert _speech(await said) == replies["screen_wake"]


async def test_back_with_nowhere_to_go_says_so(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """The dashboard has no previous page: the panel answers it could not act."""
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    said = _say(hass, "previous page", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("back", None)
    await panel.answer(sent["command_id"], "failed", code="hardware_unavailable")
    replies = voice_grammar.load_grammar("en")["responses"]
    assert _speech(await said) == replies["back_none"]


async def test_the_previous_page_goes_back_unless_a_view_has_that_name(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        "en",
    )
    said = _say(hass, "go to the previous page", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("back", None)
    await panel.answer(sent["command_id"], "applied")
    await said
    views = [{"title": "Previous", "path": "previous-view"}]
    await hass.data[LOVELACE_DATA].dashboards[None].async_save({"views": views})
    said = _say(hass, "go to the previous page", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("navigate", "/lovelace/previous-view")
    await panel.answer(sent["command_id"], "applied")
    await said


async def test_a_request_names_the_panel_to_act_on(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """Said away from any panel, "on the attic panel" picks it by its name."""
    _, panel, device_id = await _ready(
        hass,
        hass_ws_client,
        hass_read_only_user,
        hass_read_only_access_token,
        "en",
        views=[{"title": "Cameras", "path": "cams"}],
    )
    dr.async_get(hass).async_update_device(device_id, name_by_user="Attic panel")
    said = _say(hass, "show the cameras page on the attic panel", "en", None)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("navigate", "/lovelace/cams")
    await panel.answer(sent["command_id"], "applied")
    await said
    said = _say(hass, "put the attic panel to sleep", "en", None)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("screen", {"on": False})
    await panel.answer(sent["command_id"], "applied")
    await said

    replies = voice_grammar.load_grammar("en")["responses"]
    result = await _say(hass, "wake up the garage panel", "en", device_id)
    assert _speech(result) == replies["panel_unknown"].format(panel="garage panel")
    # Only a word for panel: the panel that heard it.
    await _report_state(panel, hass, volume=50)
    said = _say(hass, "louder on the panel", "en", device_id)
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("volume", 60)
    await panel.answer(sent["command_id"], "applied")
    await said
    MockConfigEntry(domain=DOMAIN, title="Attic panel").add_to_hass(hass)
    result = await _say(hass, "wake up the attic panel", "en", device_id)
    assert _speech(result) == replies["panel_ambiguous"].format(panel="attic panel")
    await panel.nothing_sent()

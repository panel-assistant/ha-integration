"""Panel commands offered to an AI conversation agent as tools.

Called the way an agent calls them: through Home Assistant's Assist LLM API
with the requesting panel's device in the context.
"""

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm

from custom_components.panel_assistant.const import DOMAIN

from . import voice_grammar
from .test_transport import WsClientFactory
from .test_voice_commands import _ready


def _context(device_id: str | None, language: str = "en") -> llm.LLMContext:
    return llm.LLMContext(
        platform="test",
        context=Context(),
        language=language,
        assistant="conversation",
        device_id=device_id,
    )


async def test_an_agent_shows_a_page_on_the_panel_that_heard_it(
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
        views=[{"title": "Cameras", "path": "view-two"}],
    )
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(device_id))
    names = {tool.name for tool in api.tools}
    assert {
        "panel_assistant__show_page",
        "panel_assistant__back",
        "panel_assistant__android_settings",
        "panel_assistant__launcher",
        "panel_assistant__recents",
    } <= names
    call = hass.async_create_task(
        api.async_call_tool(
            llm.ToolInput(
                tool_name="panel_assistant__show_page", tool_args={"page": "Cameras"}
            )
        )
    )
    command = await panel.command()
    assert (command["channel"], command["value"]) == ("navigate", "/lovelace/view-two")
    await panel.answer(command["command_id"], "applied")
    replies = voice_grammar.load_grammar("en")["responses"]
    assert await call == {
        "success": True,
        "speech": replies["show_page"].format(page="Cameras"),
    }


async def test_an_agent_opens_settings_and_hears_why_not_away_from_a_panel(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "de"
    )
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(device_id, "de"))
    call = hass.async_create_task(
        api.async_call_tool(
            llm.ToolInput(tool_name="panel_assistant__android_settings", tool_args={})
        )
    )
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("android_settings", None)
    await panel.answer(sent["command_id"], "applied")
    german = voice_grammar.load_grammar("de")["responses"]
    assert await call == {"success": True, "speech": german["android_settings"]}

    away = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(None))
    result = await away.async_call_tool(
        llm.ToolInput(tool_name="panel_assistant__launcher", tool_args={})
    )
    english = voice_grammar.load_grammar("en")["responses"]
    assert result == {"success": False, "speech": english["not_a_panel"]}
    await panel.nothing_sent()


async def test_the_tools_are_offered_only_while_a_panel_is_exposed(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    entry, _, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )

    async def ours() -> list[str]:
        api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(device_id))
        return [tool.name for tool in api.tools if tool.name.startswith(f"{DOMAIN}__")]

    assert len(await ours()) == 5
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        async_expose_entity(hass, "conversation", entity.entity_id, False)
    assert await ours() == []
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        async_expose_entity(hass, "conversation", entity.entity_id, True)
    assert len(await ours()) == 5
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await ours() == []


async def test_an_agent_names_the_panel_to_act_on(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    from homeassistant.helpers import device_registry as dr

    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    name = dr.async_get(hass).async_get(device_id).name
    away = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(None))
    english = voice_grammar.load_grammar("en")["responses"]
    unknown = await away.async_call_tool(
        llm.ToolInput(
            tool_name="panel_assistant__launcher", tool_args={"panel": "garage"}
        )
    )
    assert unknown == {
        "success": False,
        "speech": english["panel_unknown"].format(panel="garage"),
    }
    call = hass.async_create_task(
        away.async_call_tool(
            llm.ToolInput(
                tool_name="panel_assistant__launcher", tool_args={"panel": name.upper()}
            )
        )
    )
    sent = await panel.command()
    assert (sent["channel"], sent["value"]) == ("launcher", None)
    await panel.answer(sent["command_id"], "applied")
    assert await call == {"success": True, "speech": english["launcher"]}


async def test_bad_arguments_reach_the_agent_as_an_error(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    _, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(device_id))
    with pytest.raises(vol.Invalid):
        await api.async_call_tool(
            llm.ToolInput(tool_name="panel_assistant__show_page", tool_args={})
        )
    # A blank name would otherwise match a view with no title.
    blank = await api.async_call_tool(
        llm.ToolInput(tool_name="panel_assistant__show_page", tool_args={"page": " "})
    )
    assert blank["success"] is False
    await panel.nothing_sent()


async def test_an_agent_acts_only_on_a_panel_exposed_to_it(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """Another panel being exposed offers the tools; a panel that is not exposed
    is not acted on, named or as the requester."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    dr.async_get(hass).async_update_device(device_id, name_by_user="Private")
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        async_expose_entity(hass, "conversation", entity.entity_id, False)
    public = MockConfigEntry(domain=DOMAIN, title="Public")
    public.add_to_hass(hass)
    public.mock_state(hass, ConfigEntryState.LOADED)
    screen = registry.async_get_or_create(
        "light", DOMAIN, "public_screen", config_entry=public
    )
    async_expose_entity(hass, "conversation", screen.entity_id, True)

    english = voice_grammar.load_grammar("en")["responses"]
    for heard, args, reply in (
        (None, {"panel": "Private"}, english["panel_unknown"].format(panel="Private")),
        (device_id, {}, english["not_a_panel"]),
    ):
        api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(heard))
        result = await api.async_call_tool(
            llm.ToolInput(tool_name="panel_assistant__launcher", tool_args=args)
        )
        assert result == {"success": False, "speech": reply}
    await panel.nothing_sent()


@pytest.mark.parametrize("namesake_name", ["Attic", "Attic panel"])
@pytest.mark.parametrize(
    ("live_exposed", "namesake_exposed", "reply"),
    [
        (True, False, "launcher"),
        (False, True, "panel_unavailable"),
        (False, False, "panel_unknown"),
        (True, True, "panel_ambiguous"),
    ],
)
async def test_an_agent_counts_only_exposed_namesakes(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    namesake_name: str,
    live_exposed: bool,
    namesake_exposed: bool,
    reply: str,
) -> None:
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry, panel, device_id = await _ready(
        hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, "en"
    )
    dr.async_get(hass).async_update_device(device_id, name_by_user="Attic")
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        async_expose_entity(hass, "conversation", entity.entity_id, live_exposed)
    # An unrelated exposed panel keeps the tools available even if both
    # namesakes are hidden. The second namesake has no connected session.
    for title, exposed in ((namesake_name, namesake_exposed), ("Public", True)):
        other = MockConfigEntry(domain=DOMAIN, title=title)
        other.add_to_hass(hass)
        other.mock_state(hass, ConfigEntryState.LOADED)
        screen = registry.async_get_or_create(
            "light", DOMAIN, other.entry_id, config_entry=other
        )
        async_expose_entity(hass, "conversation", screen.entity_id, exposed)

    api = await llm.async_get_api(hass, llm.LLM_API_ASSIST, _context(None))
    args = llm.ToolInput(
        tool_name="panel_assistant__launcher", tool_args={"panel": "Attic"}
    )
    if reply == "launcher":
        call = hass.async_create_task(api.async_call_tool(args))
        sent = await panel.command()
        assert (sent["channel"], sent["value"]) == ("launcher", None)
        await panel.answer(sent["command_id"], "applied")
        result = await call
    else:
        result = await api.async_call_tool(args)
    english = voice_grammar.load_grammar("en")["responses"]
    assert result == {
        "success": reply == "launcher",
        "speech": english[reply].format(panel="Attic"),
    }
    await panel.nothing_sent()

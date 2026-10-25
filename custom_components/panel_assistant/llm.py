"""Panel voice commands as tools an AI conversation agent can call.

The commands the sentence triggers run (``voice_commands``), on the panel that
heard the request unless the agent names another, in any language the agent
speaks. Only the commands Home Assistant has no tool for: the screen and the
volume are already the built-in light and media player tools. Plain tools
rather than intent tools, because the commands are not intents; the names
carry the ``panel_assistant__`` prefix Home Assistant asks for from 2026.9.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.llm import LLM_API_ASSIST, LLMContext, Tool, ToolInput
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN
from .voice_commands import async_run_command, panel_exposed

PANEL = vol.Optional(
    "panel",
    description="The name of another wall panel to act on; leave it out to act"
    " on the panel the user is speaking to",
)
DESCRIPTIONS = {
    "show_page": "Show a dashboard view or a dashboard on a wall panel, by its"
    " name; a word for home shows the panel's home dashboard",
    "back": "Go back to the previous page on a wall panel",
    "android_settings": "Open Android settings on a wall panel",
    "launcher": "Show the Android launcher (home screen) on a wall panel",
    "recents": "Show the recent apps on a wall panel",
}


class PanelCommandTool(Tool):
    """One panel command."""

    def __init__(self, command: str) -> None:
        self.command = command
        self.name = f"{DOMAIN}__{command}"
        self.description = DESCRIPTIONS[command]
        fields: dict[Any, Any] = {PANEL: str}
        if command == "show_page":
            fields[vol.Required("page", description="View or dashboard name")] = str
        self.parameters = vol.Schema(fields)

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> JsonObjectType:
        """Run the command; the reply is what to tell the user."""
        # Home Assistant does not check tool arguments against the schema.
        args = self.parameters(tool_input.tool_args)
        done, speech = await async_run_command(
            hass,
            self.command,
            language=llm_context.language,
            device_id=llm_context.device_id,
            page=args.get("page"),
            panel=args.get("panel"),
            assistant=llm_context.assistant,
        )
        return {"success": done, "speech": speech}


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """The panel commands, for the Assist API, while a panel is exposed to it."""
    if api_id != LLM_API_ASSIST:
        return None
    exposed = any(
        panel_exposed(hass, entry.entry_id, llm_context.assistant)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
    )
    if not exposed:
        return None
    return LLMTools(tools=[PanelCommandTool(command) for command in DESCRIPTIONS])

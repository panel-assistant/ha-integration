"""Voice commands a panel answers itself, in every shipped locale.

Saying "show the cameras page" or "show Android settings" to a panel acts on
that panel. The sentences live in ``sentences/<language>.yaml``. Home Assistant
compiles every sentence trigger into one set, whatever the request's language,
and tries it before its built-in intents, so each command is registered once
with every locale's sentences and answers in the request's language. A phrase
a built-in intent already understands is never registered (the tests check
every locale against Home Assistant's own sentences): "turn off the screen"
reaches the built-in on/off intents through the screen light's alias instead,
which ``async_alias_screen`` adds.

``register_trigger`` is Home Assistant's own, undocumented for integrations. If
it goes, voice commands are left off with one warning, and the tests that run
on every supported Core release fail; the documented route then is a
``custom_sentences`` file per language plus public intent handlers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import language as language_util
from homeassistant.util.hass_dict import HassKey
from homeassistant.util.yaml import load_yaml_dict

from .const import CONF_TRANSPORT_USER_ID, DOMAIN
from .transport_protocol import ERR_PANEL_UNAVAILABLE, STATE_KNOWN
from .transport_sessions import PanelSession, async_get_sessions, async_send_command

_LOGGER = logging.getLogger(__name__)

SENTENCES = Path(__file__).parent / "sentences"
FALLBACK_LANGUAGE = "en"
# Commands sent as a channel with no value; an older panel app does not describe them.
APP_ACTIONS = ("android_settings", "launcher")
COMMANDS = ("show_page", *APP_ACTIONS)
# Registry option recording that the screen aliases were added once, so an
# alias the owner removes is never added back.
OPTION_SCREEN_ALIAS = "voice_alias"
DATA_GRAMMARS: HassKey[dict[str, dict[str, Any]]] = HassKey(f"{DOMAIN}_voice_grammars")


def _load_grammars() -> dict[str, dict[str, Any]]:
    return {
        path.stem: load_yaml_dict(path) for path in sorted(SENTENCES.glob("*.yaml"))
    }


async def async_setup_voice_commands(hass: HomeAssistant) -> None:
    """Register each command once, with every locale's sentences."""
    from homeassistant.components.conversation import (
        agent_manager,
    )

    grammars = await hass.async_add_executor_job(_load_grammars)
    hass.data[DATA_GRAMMARS] = grammars
    manager = agent_manager.get_agent_manager(hass)
    register: Callable[..., Any] | None = getattr(manager, "register_trigger", None)
    if register is None:
        _LOGGER.warning(
            "This Home Assistant release has no sentence triggers, so panels"
            " do not answer their own voice commands"
        )
        return
    for command in COMMANDS:
        sentences = [
            sentence
            for grammar in grammars.values()
            for sentence in grammar["commands"][command]["sentences"]
        ]
        register(sentences, partial(_async_answer, hass, grammars, command))


def _grammar(
    grammars: dict[str, dict[str, Any]], language: str | None
) -> dict[str, Any]:
    matches = language_util.matches(language or FALLBACK_LANGUAGE, list(grammars))
    return grammars[matches[0] if matches else FALLBACK_LANGUAGE]


@callback
def _panel_entry_id(hass: HomeAssistant, user_input: Any) -> str | None:
    """The panel that heard the request: its satellite's device, else the device."""
    device_id = user_input.device_id
    if user_input.satellite_id is not None:
        satellite = er.async_get(hass).async_get(user_input.satellite_id)
        if satellite is not None:
            device_id = satellite.device_id
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is None:
        return None
    return next(
        (value for domain, value in device.identifiers if domain == DOMAIN), None
    )


def _known(session: PanelSession, channel: str) -> Any:
    observation = session.observations.get(channel)
    if observation is None or observation.state != STATE_KNOWN:
        return None
    return observation.value


def _normalized(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text).strip().casefold()


async def _async_home_dashboard(
    hass: HomeAssistant, entry_id: str, session: PanelSession
) -> str:
    """The url path of the dashboard the panel shows as home.

    The panel reports its own setting; blank means its account's default
    dashboard, which the frontend keeps per user, then for the system.
    """
    reported = str(_known(session, "home_dashboard") or "").strip("/")
    if reported:
        return reported.split("/")[0]
    entry = hass.config_entries.async_get_entry(entry_id)
    user_id = entry.data.get(CONF_TRANSPORT_USER_ID) if entry is not None else None
    try:
        from homeassistant.components.frontend import storage

        default = None
        if user_id:
            user = await storage.async_user_store(hass, user_id)
            default = (user.data.get("core") or {}).get("default_panel")
        if not default:
            system = await storage.async_system_store(hass)
            default = (system.data.get("core") or {}).get("default_panel")
    except ImportError, AttributeError, HomeAssistantError:
        default = None
    return str(default or "lovelace")


async def _async_page_path(
    hass: HomeAssistant,
    entry_id: str,
    session: PanelSession,
    page: str,
    grammars: dict[str, dict[str, Any]],
) -> str | None:
    """The path of the panel's view or dashboard called ``page``.

    The panel's own home dashboard's views first, then every dashboard by
    title or path; a word for "home" means the home dashboard itself.
    """
    from homeassistant.components.lovelace.const import (
        LOVELACE_DATA,
    )

    wanted = _normalized(page)
    home_url = await _async_home_dashboard(hass, entry_id, session)
    home = f"/{home_url}"
    lovelace = hass.data.get(LOVELACE_DATA)
    dashboards = lovelace.dashboards if lovelace is not None else {}
    home_dashboard = dashboards.get(None if home_url == "lovelace" else home_url)
    if home_dashboard is not None:
        try:
            config = await home_dashboard.async_load(False)
        except HomeAssistantError:
            config = {}
        for index, view in enumerate(config.get("views") or ()):
            names = {_normalized(str(view.get(key) or "")) for key in ("title", "path")}
            if wanted in names:
                return f"/{home_url}/{view.get('path') or index}"
    for url_path, dashboard in dashboards.items():
        title = (dashboard.config or {}).get("title") if url_path else None
        names = {_normalized(str(title or "")), _normalized(str(url_path or ""))}
        if wanted in names - {""}:
            return f"/{url_path}"
    home_words = {
        _normalized(word)
        for grammar in grammars.values()
        for word in grammar.get("home_pages") or ()
    }
    return home if wanted in home_words else None


async def _async_answer(
    hass: HomeAssistant,
    grammars: dict[str, dict[str, Any]],
    command: str,
    user_input: Any,
    result: Any,
) -> str:
    """Act on the panel that heard ``command`` and say what happened."""
    replies: dict[str, str] = _grammar(grammars, user_input.language)["responses"]
    entry_id = _panel_entry_id(hass, user_input)
    if entry_id is None:
        return replies["not_a_panel"]
    session = async_get_sessions(hass).get(entry_id)
    if session is None:
        return replies["panel_unavailable"]
    try:
        if command == "show_page":
            page = result.entities["page"].text.strip()
            path = await _async_page_path(hass, entry_id, session, page, grammars)
            if path is None:
                return replies["page_unknown"].format(page=page)
            await async_send_command(hass, session, "navigate", path)
            return replies["show_page"].format(page=page)
        if command not in session.descriptors:
            return replies["needs_update"]
        if _known(session, "kiosk_lock") in (True, "ON", "on"):
            return replies["panel_locked"]
        await async_send_command(hass, session, command, None)
        return replies[command]
    except HomeAssistantError as err:
        if err.translation_key == ERR_PANEL_UNAVAILABLE:
            return replies["panel_unavailable"]
        return replies["panel_refused"]


@callback
def async_alias_screen(hass: HomeAssistant, entity_id: str) -> None:
    """Give the screen light the names people call it as aliases, once.

    Home Assistant matches a spoken name against an entity's aliases, whose
    default is the full name ("<panel name> Screen"), so "turn off the
    screen" found nothing. With the short name ("Screen", "Bildschirm") and
    the forms a sentence puts it in ("obrazovku") added, the built-in intents
    act on the screen in the satellite's area.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return
    options: dict[str, Any] = dict(entry.options.get(DOMAIN) or {})
    if options.get(OPTION_SCREEN_ALIAS):
        return
    grammar = _grammar(
        hass.data.get(DATA_GRAMMARS) or {FALLBACK_LANGUAGE: {}}, hass.config.language
    )
    aliases = list(entry.aliases) or [er.COMPUTED_NAME]
    known = {alias.casefold() for alias in aliases if isinstance(alias, str)}
    for name in [entry.original_name, *grammar.get("screen_names", ())]:
        if name and name.casefold() not in known:
            aliases.append(name)
            known.add(name.casefold())
    registry.async_update_entity(entity_id, aliases=aliases)
    registry.async_update_entity_options(
        entity_id, DOMAIN, {**options, OPTION_SCREEN_ALIAS: True}
    )

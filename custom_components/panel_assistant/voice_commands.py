"""Voice commands a panel answers itself, in every shipped locale.

Saying "show the cameras page", "go to sleep" or "louder" to a panel acts on
that panel, or on the panel the request names ("... on the attic panel").
The sentences live in ``sentences/<language>.yaml``. Home Assistant
compiles every sentence trigger into one set, whatever the request's language,
and tries it before its built-in intents, so each command is registered once
with every locale's sentences and answers in the request's language. A phrase
a built-in intent already understands is never registered (the tests check
every locale against Home Assistant's own sentences): "turn off the screen"
reaches the built-in on/off intents through the screen light's alias instead,
which ``async_alias_screen`` adds. AI conversation agents reach the same
commands through ``llm.py``, which calls ``async_run_command``.

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
APP_ACTIONS = ("android_settings", "launcher", "recents", "back")
# The app actions that take the panel off its dashboard, which kiosk lock refuses.
LEAVES_DASHBOARD = ("android_settings", "launcher", "recents")
# Commands on the screen and volume channels every panel app describes.
SCREEN_STEPS = {"screen_brighter": 1, "screen_dimmer": -1}
VOLUME_STEPS = {"volume_up": 1, "volume_down": -1}
COMMANDS = (
    "show_page",
    *APP_ACTIONS,
    "screen_wake",
    "screen_sleep",
    *SCREEN_STEPS,
    *VOLUME_STEPS,
)
# One brighter, dimmer, louder or quieter step, in percentage points: Home
# Assistant's own light brighten and dim step.
STEP_PERCENT = 10
# The dimmest screen the panel shows, on the 0-255 scale; the app's
# ScreenCommandPolicy raises anything lower to it, and 0 would mean off.
MIN_SCREEN_BRIGHTNESS = 10
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
def _panel_entry_id(
    hass: HomeAssistant, device_id: str | None, satellite_id: str | None
) -> str | None:
    """The panel that heard the request: its satellite's device, else the device."""
    if satellite_id is not None:
        satellite = er.async_get(hass).async_get(satellite_id)
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
    return home if wanted in _words(grammars, "home_pages") else None


def _words(grammars: dict[str, dict[str, Any]], key: str) -> set[str]:
    """A grammar word list, such as the words for home, in every locale."""
    return {_normalized(word) for grammar in grammars.values() for word in grammar[key]}


async def _async_answer(
    hass: HomeAssistant,
    grammars: dict[str, dict[str, Any]],
    command: str,
    user_input: Any,
    result: Any,
) -> str:
    """A sentence trigger: act on the panel that heard ``command``, or the named one."""
    spoken = {name: entity.text.strip() for name, entity in result.entities.items()}
    _, reply = await async_run_command(
        hass,
        command,
        language=user_input.language,
        device_id=user_input.device_id,
        satellite_id=user_input.satellite_id,
        page=spoken.get("page"),
        panel=spoken.get("panel"),
    )
    return reply


def _panels_called(
    hass: HomeAssistant, grammars: dict[str, dict[str, Any]], name: str
) -> list[str]:
    """The panels Home Assistant names ``name``, with or without a word for panel.

    "Attic panel", "Dachbodenpanel" and "painel do sótão" all name the panel
    called "Attic", "Dachboden" or "Sótão" as well as the one called in full.
    """
    from .device import panel_display_name

    words = _words(grammars, "panel_words")

    def forms(text: str) -> set[str]:
        text = _normalized(text)
        found = {text}
        for word in words:
            if text != word and text.endswith(word):
                found.add(text.removesuffix(word).rstrip())
            head, _, rest = text.partition(" ")
            if head == word and rest:
                # A short linking word may follow: "painel da cozinha".
                link, _, tail = rest.partition(" ")
                found.add(tail if tail and len(link) <= 3 else rest)
        return found

    wanted = forms(name)
    return [
        entry.entry_id
        for entry in hass.config_entries.async_entries(
            DOMAIN, include_ignore=False, include_disabled=False
        )
        if wanted & forms(panel_display_name(hass, entry))
    ]


@callback
def panel_exposed(hass: HomeAssistant, entry_id: str, assistant: str) -> bool:
    """Whether any of the panel's entities is exposed to ``assistant``."""
    from homeassistant.components.homeassistant.exposed_entities import (
        async_should_expose,
    )

    return any(
        async_should_expose(hass, assistant, entity.entity_id)
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
    )


def _stepped(percent: int, direction: int) -> int:
    return max(0, min(100, percent + direction * STEP_PERCENT))


async def async_run_command(
    hass: HomeAssistant,
    command: str,
    *,
    language: str | None,
    device_id: str | None,
    satellite_id: str | None = None,
    page: str | None = None,
    panel: str | None = None,
    assistant: str | None = None,
) -> tuple[bool, str]:
    """Act on the named panel, else the one that heard the request.

    Returns whether it was done and what to say, in the request's language.
    Sentence triggers and the AI agent tools both come here; a tool names its
    ``assistant``, which may act only on a panel exposed to it.
    """
    grammars = hass.data[DATA_GRAMMARS]
    replies: dict[str, str] = _grammar(grammars, language)["responses"]
    if panel and _normalized(panel) in _words(grammars, "panel_words"):
        panel = None  # "on the screen": the panel itself, not a name
    if panel:
        named = _panels_called(hass, grammars, panel)
        if len(named) != 1:
            key = "panel_ambiguous" if named else "panel_unknown"
            return False, replies[key].format(panel=panel)
        entry_id: str | None = named[0]
    else:
        entry_id = _panel_entry_id(hass, device_id, satellite_id)
    if entry_id is None or (
        assistant is not None and not panel_exposed(hass, entry_id, assistant)
    ):
        if panel:
            return False, replies["panel_unknown"].format(panel=panel)
        return False, replies["not_a_panel"]
    session = async_get_sessions(hass).get(entry_id)
    if session is None:
        return False, replies["panel_unavailable"]
    try:
        if command == "show_page":
            page = (page or "").strip()
            # A blank name (an agent's empty argument) would match an untitled view.
            path = page and await _async_page_path(
                hass, entry_id, session, page, grammars
            )
            if path:
                await async_send_command(hass, session, "navigate", path)
                return True, replies["show_page"].format(page=page)
            if _normalized(page) not in _words(grammars, "back_pages"):
                return False, replies["page_unknown"].format(page=page)
            # "Go to the previous page", where no view or dashboard has that name.
            command = "back"
        if command in ("screen_wake", "screen_sleep"):
            await async_send_command(
                hass, session, "screen", {"on": command == "screen_wake"}
            )
            return True, replies[command]
        if command in SCREEN_STEPS:
            return await _async_step_screen(hass, session, command, replies)
        if command in VOLUME_STEPS:
            return await _async_step_volume(hass, session, command, replies)
        if command not in session.descriptors:
            # A panel with no way to press Back states it unsupported.
            return False, replies["back_none" if command == "back" else "needs_update"]
        locked = _known(session, "kiosk_lock") in (True, "ON", "on")
        if locked and command in LEAVES_DASHBOARD:
            return False, replies["panel_locked"]
        await async_send_command(hass, session, command, None)
        return True, replies[command]
    except HomeAssistantError as err:
        if err.translation_key == ERR_PANEL_UNAVAILABLE:
            return False, replies["panel_unavailable"]
        if command == "back" and err.translation_key == "hardware_unavailable":
            # The dashboard has no previous page, or nothing can press Back.
            return False, replies["back_none"]
        return False, replies["panel_refused"]


async def _async_step_screen(
    hass: HomeAssistant, session: PanelSession, command: str, replies: dict[str, str]
) -> tuple[bool, str]:
    """One step brighter or dimmer, never down to off; a dark screen wakes."""
    screen = _known(session, "screen")
    if not isinstance(screen, dict):
        return False, replies["panel_refused"]
    up = SCREEN_STEPS[command] > 0
    if not screen.get("on"):
        if not up:
            return False, replies["screen_dark"]
        await async_send_command(hass, session, "screen", {"on": True})
        return True, replies["screen_wake"]
    level = int(screen.get("brightness") or 0)
    percent = _stepped(round(level * 100 / 255), SCREEN_STEPS[command])
    target = max(MIN_SCREEN_BRIGHTNESS, round(percent * 255 / 100))
    if (target <= level) if up else (target >= level):
        return False, replies["screen_brightest" if up else "screen_dimmest"]
    await async_send_command(
        hass, session, "screen", {"on": True, "brightness": target}
    )
    percent = round(target * 100 / 255)
    return True, replies[command].format(brightness=percent)


async def _async_step_volume(
    hass: HomeAssistant, session: PanelSession, command: str, replies: dict[str, str]
) -> tuple[bool, str]:
    """One step louder or quieter on the panel's own volume channel."""
    volume = _known(session, "volume")
    if not isinstance(volume, (int, float)):
        return False, replies["panel_refused"]
    target = _stepped(round(volume), VOLUME_STEPS[command])
    if target == round(volume):
        return False, replies["volume_max" if target else "volume_min"]
    await async_send_command(hass, session, "volume", target)
    return True, replies[command].format(volume=target)


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

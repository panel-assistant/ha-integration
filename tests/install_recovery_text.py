"""Render an install failure the way Home Assistant shows it, in every language."""

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .test_repository import TRANSLATED_LANGUAGES

RECOVERY_ADDRESS = "panel-assistant.io/go/install-recovery"


async def assert_names_recovery_page(
    hass: HomeAssistant, key: str, result: dict[str, Any]
) -> None:
    """Every language's text for ``key``, filled from the result, names the page.

    ``key`` is the catalogue path below the integration, such as
    ``config.abort.install_failed``. A placeholder the flow did not supply
    raises KeyError here, as the frontend would show a raw error instead.
    """
    category = key.split(".", 1)[0]
    placeholders = result.get("description_placeholders") or {}
    for language in ("en", *TRANSLATED_LANGUAGES):
        catalogue = await async_get_translations(
            hass, language, category, {"panel_assistant"}
        )
        text = catalogue[f"component.panel_assistant.{key}"].format(**placeholders)
        assert RECOVERY_ADDRESS in text, f"{language} {key}: {text}"
        if ".abort." in key:
            assert f"](https://{RECOVERY_ADDRESS}?" in text, f"{language} {key}"

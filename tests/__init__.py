"""Tests for the ha-paneld integration."""

import importlib
import importlib.util
import sys

# Home Assistant 2026.11 ships its own test package, pytest-homeassistant, in place
# of pytest-homeassistant-custom-component, which stops at 2026.10. The suite keeps
# importing the old name; where only the new package is installed, that name refers
# to the same modules. The plugins module goes first: importing common before it
# trips the new package's recorder import guard.
if importlib.util.find_spec("pytest_homeassistant_custom_component.plugins") is None:
    for _suffix in ("", ".plugins", ".common", ".test_util", ".test_util.aiohttp"):
        sys.modules[f"pytest_homeassistant_custom_component{_suffix}"] = (
            importlib.import_module(f"pytest_homeassistant{_suffix}")
        )

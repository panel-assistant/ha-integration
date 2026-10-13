"""Register the integration's administrator sidebar and hidden USB panel."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from homeassistant.components import panel_custom
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .build import INTEGRATION_BUILD
from .const import DOMAIN, INTEGRATION_VERSION
from .embed import EmbedProxyView

DATA_BROWSER_PANEL = "browser_panel"
STATIC_PATH = Path(__file__).parent / "static"
STATIC_URL = "/panel_assistant/usb"
PANEL_PATH = "panel-assistant-usb"
SIDEBAR_PANEL_PATH = "panel-assistant"
# Each release opens the installer copy published with it, so a later installer
# deploy never changes what an installed release talks to. The root keeps the
# newest copy for releases up to 0.9.0, which open the root.
PUBLISHED_INSTALLER_ROOT = "https://install.panel-assistant.io/"
DEFAULT_INSTALLER_URL = f"https://install.panel-assistant.io/{INTEGRATION_VERSION}/"
# A self-hosted or development installer can replace the published one. The browser
# side still refuses anything that is not HTTPS or plain HTTP on localhost.
INSTALLER_URL = os.environ.get("PANEL_ASSISTANT_INSTALLER_URL", DEFAULT_INSTALLER_URL)


async def _async_copy_missing(hass: HomeAssistant, url: str) -> bool:
    """Whether the site answers that this installer copy does not exist."""
    try:
        async with async_get_clientsession(hass).head(
            url, timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            return response.status == 404
    except aiohttp.ClientError, TimeoutError:
        return False


async def _async_installer_url(hass: HomeAssistant) -> str:
    """Open this release's copy, or the newest one while its own is unpublished.

    A build numbered ahead of its release tag has no copy yet, and the site
    publishes one only when the tag is pushed. Only a definite 404 falls back,
    so a check that cannot reach the site changes nothing.
    """
    if INSTALLER_URL == DEFAULT_INSTALLER_URL and await _async_copy_missing(
        hass, INSTALLER_URL
    ):
        return PUBLISHED_INSTALLER_ROOT
    return INSTALLER_URL


@dataclass
class _Registration:
    """Retain successful registration steps across concurrent calls and retries."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    proxy_registered: bool = False
    static_registered: bool = False
    panel_registered: bool = False
    sidebar_registered: bool = False


async def async_register_browser_panel(hass: HomeAssistant) -> None:
    """Register once per HA lifetime, independently of configuration entries."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    registration = domain_data.setdefault(DATA_BROWSER_PANEL, _Registration())
    async with registration.lock:
        if not registration.proxy_registered:
            hass.http.register_view(EmbedProxyView(hass))
            registration.proxy_registered = True
        if not registration.static_registered:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(STATIC_URL, str(STATIC_PATH), cache_headers=False)]
            )
            registration.static_registered = True
        if not registration.panel_registered:
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=PANEL_PATH,
                webcomponent_name="panel-assistant-usb-install",
                module_url=f"{STATIC_URL}/ha-panel.js",
                sidebar_title=None,
                embed_iframe=False,
                require_admin=True,
                # The version caption under the brand, as in the sidebar's top menu.
                config={
                    "installer_url": await _async_installer_url(hass),
                    "version": INTEGRATION_VERSION,
                    "build": INTEGRATION_BUILD,
                },
            )
            registration.panel_registered = True
        if not registration.sidebar_registered:
            # Hiding the panel from non-administrators is not the gate: the
            # embed commands and the proxy check the user themselves.
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=SIDEBAR_PANEL_PATH,
                webcomponent_name="panel-assistant-sidebar",
                module_url=f"{STATIC_URL}/ha-panel.js",
                sidebar_title="Panel Assistant",
                sidebar_icon="mdi:tablet-dashboard",
                embed_iframe=False,
                require_admin=True,
                # Shown in the top menu, so which build is running is visible without
                # downloading diagnostics.
                config={"version": INTEGRATION_VERSION, "build": INTEGRATION_BUILD},
            )
            registration.sidebar_registered = True

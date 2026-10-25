"""Warn on adding a panel whose saved account belongs to another server."""

from __future__ import annotations

import asyncio
import json
import re

from aiohttp import ClientError, ClientTimeout, DummyCookieJar
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import HaPaneldClient, HaPaneldError
from .const import DOMAIN, help_url
from .device import panel_display_name, sidebar_url
from .ha_url import DATA_INSTANCE_ID, _connection_origin
from .release import strict_json_hooks

_INSTANCE_ID = re.compile(r"[0-9a-f]{32}")
_MAX_IDENTITY_BYTES = 512


async def async_connected_to_other_ha(
    hass: HomeAssistant, client: HaPaneldClient
) -> bool:
    """Make one bounded, credential-free check; unavailable evidence is unknown."""
    try:
        async with asyncio.timeout(8):
            own_id = hass.data.get(DOMAIN, {}).get(DATA_INSTANCE_ID)
            if own_id is None:
                own_id = await instance_id.async_get(hass)
            if not isinstance(own_id, str) or _INSTANCE_ID.fullmatch(own_id) is None:
                return False
            configured_url = await client.async_get_configured_ha_url()
            origin = (
                _connection_origin(configured_url)
                if configured_url is not None
                else None
            )
            if origin is None:
                return False
            # Core's connector is shared. This short-lived session owns neither
            # that connector nor any cookie or authenticated session state.
            async with (
                async_create_clientsession(
                    hass,
                    auto_cleanup=False,
                    cookie_jar=DummyCookieJar(),
                    connector_owner=False,
                ) as session,
                session.get(
                    f"{origin}/api/panel_assistant/instance",
                    allow_redirects=False,
                    timeout=ClientTimeout(total=3),
                ) as response,
            ):
                if response.status != 200:
                    return False
                body = bytearray()
                async for chunk in response.content.iter_chunked(
                    _MAX_IDENTITY_BYTES + 1
                ):
                    body.extend(chunk)
                    if len(body) > _MAX_IDENTITY_BYTES:
                        return False
            document = json.loads(body, **strict_json_hooks(ValueError))
            foreign_id = (
                document.get("instance_id") if isinstance(document, dict) else None
            )
            return (
                isinstance(foreign_id, str)
                and _INSTANCE_ID.fullmatch(foreign_id) is not None
                and foreign_id != own_id
            )
    except HaPaneldError, ClientError, TimeoutError, ValueError, UnicodeError:
        return False


def foreign_ha_issue_id(entry_id: str) -> str:
    """One advisory for each added panel."""
    return f"another_home_assistant_{entry_id}"


@callback
def async_clear_foreign_ha_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Retire stale foreign-server evidence when a panel connects here."""
    ir.async_delete_issue(hass, DOMAIN, foreign_ha_issue_id(entry_id))


@callback
def async_raise_foreign_ha_issue(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Present positive evidence unless the panel has since contacted this Core."""
    from .transport import _panel_did
    from .transport_binding import async_binding_request

    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    if getattr(coordinator, "connected", False) or async_binding_request(
        hass, _panel_did(entry)
    ):
        async_clear_foreign_ha_issue(hass, entry.entry_id)
        return False
    ir.async_create_issue(
        hass,
        DOMAIN,
        foreign_ha_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="another_home_assistant",
        learn_more_url=help_url("repair", issue="another_home_assistant"),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "configure_url": sidebar_url(entry.entry_id, "configure", "cfg-ha_url"),
        },
        data={"entry_id": entry.entry_id},
    )
    if coordinator is not None:
        coordinator.async_update_listeners()
    return True

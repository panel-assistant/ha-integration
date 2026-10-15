"""The USB installer offers Panel Assistant's own ADB key at the panel's prompt.

So the one approval given over USB lets Panel Assistant reach the panel over
network ADB afterwards, as a network install does. These tests drive the real
HTTP view and credential manager over one stored key.
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant import adb_credentials
from custom_components.panel_assistant import browser_delivery as delivery
from custom_components.panel_assistant.adb_credentials import (
    _generate_credential,
    async_get_adb_signer,
)

URL = "/api/panel_assistant/usb/adb_key"
_HANDOFF = (
    Path(__file__).parents[1]
    / "custom_components/panel_assistant/frontend/src/release-handoff.mjs"
)


@pytest.fixture(autouse=True)
def stored_key() -> Iterator[None]:
    """One durable key, as the credential store holds it, without its file."""
    store = MagicMock()
    store.path = "/not/read/by/this/test"
    with (
        patch.object(adb_credentials, "Store", return_value=store),
        patch.object(adb_credentials, "_store_presence", return_value=(True, False)),
        patch.object(
            adb_credentials,
            "_read_durable_credential",
            return_value=_generate_credential(),
        ),
    ):
        yield


async def _endpoint(hass: HomeAssistant, hass_client: Any) -> Any:
    assert await async_setup_component(hass, "http", {})
    delivery.async_register_browser_delivery(hass)
    return await hass_client()


async def test_the_installer_gets_the_key_panel_assistant_signs_with(
    hass: HomeAssistant, hass_client: Any
) -> None:
    client = await _endpoint(hass, hass_client)

    first = await client.get(URL)
    again = await client.get(URL)

    assert first.status == again.status == 200
    key = (await first.json())["public_key"]
    assert (await again.json())["public_key"] == key
    # The key every later network connection signs with, in Android's format.
    assert key == (await async_get_adb_signer(hass)).GetPublicKey()
    encoded, _, name = key.partition(" ")
    assert name
    raw = base64.b64decode(encoded, validate=True)
    assert len(raw) == 524 and struct.unpack_from("<I", raw)[0] == 64
    assert "PRIVATE" not in await first.text()


async def test_only_an_admin_may_read_the_key(
    hass: HomeAssistant,
    hass_client: Any,
    hass_client_no_auth: Any,
    hass_read_only_access_token: str,
) -> None:
    await _endpoint(hass, hass_client)
    client = await hass_client_no_auth()
    for headers in ({}, {"Authorization": f"Bearer {hass_read_only_access_token}"}):
        response = await client.get(URL, headers=headers)
        assert response.status == 401


def test_the_installer_accepts_the_key_exactly_as_home_assistant_sends_it() -> None:
    key = _generate_credential().public_key
    script = (
        f"import {{ parsePanelAssistantKey }} from {json.dumps(_HANDOFF.as_uri())};"
        f"console.log(JSON.stringify(parsePanelAssistantKey({json.dumps(key)})));"
    )
    accepted = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert json.loads(accepted) == key


async def test_a_key_home_assistant_cannot_load_is_reported_not_raised(
    hass: HomeAssistant, hass_client: Any
) -> None:
    client = await _endpoint(hass, hass_client)
    with patch.object(
        delivery,
        "async_get_adb_credential",
        side_effect=adb_credentials.AdbCredentialError,
    ):
        response = await client.get(URL)
    assert response.status == 503
    assert await response.json() == {"error": "browser_adb_key_unavailable"}

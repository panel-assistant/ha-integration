"""A panel with a speaker shows its volume on the media player alone.

The media player reads and sets the volume through the volume channel, so the
separate volume number is redundant there: a panel that describes its media
player gets no volume number, and the hello removes the one an earlier version
added. A panel without a speaker keeps its volume number.
"""

from typing import Any

import pytest
from homeassistant.components.media_player import ATTR_MEDIA_VOLUME_LEVEL
from homeassistant.const import (
    ATTR_RESTORED,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN

from .test_media_player import _observe, _player, _sent
from .test_native import DESCRIPTORS
from .test_transport import DID, WsClientFactory
from .test_transport_commands import (
    _connect,
    native,  # noqa: F401  # the fixture
)

# A panel whose profile declares no speaker: it describes no media player.
SPEAKERLESS = [d for d in DESCRIPTORS if d["channel"] != "media"]
# A newer panel describing its media player in a shape this version cannot show.
UNKNOWN_PLAYER = [
    d | {"translation_key": "speaker"} if d["channel"] == "media" else d
    for d in DESCRIPTORS
]


def _volume_number(hass: HomeAssistant, did: str = DID) -> str | None:
    return er.async_get(hass).async_get_entity_id("number", DOMAIN, f"{did}_volume")


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"volume_number_removed_{entry.entry_id}"
    )


async def test_a_panel_with_a_speaker_has_no_volume_number(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The media player shows and sets the volume; no number entity exists."""
    panel = await _connect(hass, hass_ws_client, hass_read_only_access_token)

    assert _volume_number(hass) is None
    assert hass.states.async_all("number") == []
    await _observe(hass, panel, "volume", 35)
    assert hass.states.get(_player(hass)).attributes[ATTR_MEDIA_VOLUME_LEVEL] == 0.35
    command = await _sent(hass, panel, "volume_set", {"volume_level": 0.42})
    assert (command["channel"], command["value"]) == ("volume", 42)


@pytest.mark.parametrize("channels", [SPEAKERLESS, UNKNOWN_PLAYER])
async def test_a_panel_without_a_shown_media_player_keeps_its_volume_number(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    channels: list[dict[str, Any]],
) -> None:
    """No media player to show the volume, so the number does."""
    panel = await _connect(
        hass, hass_ws_client, hass_read_only_access_token, channels=channels
    )
    await _observe(hass, panel, "volume", 35)

    entity_id = _volume_number(hass)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "35"
    assert hass.states.async_all("media_player") == []
    assert _issue(hass, native) is None


@pytest.mark.parametrize("before", ["restored", "rendered"])
async def test_the_hello_removes_the_volume_number_an_earlier_version_added(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    before: str,
) -> None:
    """Nothing is left unavailable, and only this panel's own number goes."""
    registry = er.async_get(hass)
    mqtt = registry.async_get_or_create(
        "number", "mqtt", f"{DID}_volume", config_entry=native
    )
    if before == "restored":
        # After the restart that installs this version, Core shows the number
        # registered by the earlier one as unavailable until something adds it.
        old = registry.async_get_or_create(
            "number", DOMAIN, f"{DID}_volume", config_entry=native
        )
        hass.states.async_set(old.entity_id, STATE_UNAVAILABLE, {ATTR_RESTORED: True})
        entity_id = old.entity_id
    else:
        # The same panel described no media player earlier in this run.
        speakerless = await _connect(
            hass, hass_ws_client, hass_read_only_access_token, channels=SPEAKERLESS
        )
        await speakerless.client.close()
        entity_id = _volume_number(hass)
        assert entity_id is not None
    assert hass.states.get(entity_id) is not None

    await _connect(hass, hass_ws_client, hass_read_only_access_token)

    assert registry.async_get(entity_id) is None
    assert hass.states.get(entity_id) is None
    assert registry.async_get(mqtt.entity_id) is not None
    assert _issue(hass, native) is None

    # A hello from the panel without its speaker adds the number anew.
    await _connect(
        hass, hass_ws_client, hass_read_only_access_token, channels=SPEAKERLESS
    )
    assert _volume_number(hass) is not None
    # With the speaker again, neither another hello nor a reload keeps it.
    await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert _volume_number(hass) is None
    await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert await hass.config_entries.async_reload(native.entry_id)
    await hass.async_block_till_done()
    await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert _volume_number(hass) is None
    assert hass.states.async_all("number") == []


async def test_a_volume_number_of_another_entry_is_left_alone(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The same panel identity registered under another entry is not this one's."""
    other = MockConfigEntry(domain=DOMAIN, title="other")
    other.add_to_hass(hass)
    theirs = er.async_get(hass).async_get_or_create(
        "number", DOMAIN, f"{DID}_volume", config_entry=other
    )

    await _connect(hass, hass_ws_client, hass_read_only_access_token)

    assert er.async_get(hass).async_get(theirs.entity_id) is not None


async def test_automations_and_scripts_still_using_the_number_are_named(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """The owner must change them, so one Repair names them until dismissed."""
    old = er.async_get(hass).async_get_or_create(
        "number", DOMAIN, f"{DID}_volume", config_entry=native
    )
    set_value: dict[str, Any] = {
        "action": "number.set_value",
        "target": {"entity_id": old.entity_id},
        "data": {"value": 10},
    }
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Quiet night",
                "triggers": [{"trigger": "event", "event_type": "night"}],
                "actions": [set_value],
            }
        },
    )
    assert await async_setup_component(
        hass, "script", {"script": {"evening": {"sequence": [set_value]}}}
    )
    await hass.async_block_till_done()

    await _connect(hass, hass_ws_client, hass_read_only_access_token)

    issue = _issue(hass, native)
    assert issue is not None
    assert issue.translation_key == "volume_number_removed"
    assert issue.is_persistent
    assert not issue.is_fixable
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["entity"] == old.entity_id
    assert issue.translation_placeholders["users"] == (
        "automation.quiet_night, script.evening"
    )


async def test_removal_waits_for_start_so_a_device_action_is_still_named(
    hass: HomeAssistant,
    native: MockConfigEntry,  # noqa: F811
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
) -> None:
    """A panel may say hello before automations load on the upgrade restart."""
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, native.entry_id)}
    )
    assert device is not None
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "number",
        DOMAIN,
        f"{DID}_volume",
        config_entry=native,
        device_id=device.id,
    )
    hass.set_state(CoreState.starting)

    await _connect(hass, hass_ws_client, hass_read_only_access_token)
    assert registry.async_get(old.entity_id) is not None
    # Loaded after the hello; the UI's device action holds the registry ID.
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Quiet night",
                "triggers": [{"trigger": "event", "event_type": "night"}],
                "actions": [
                    {
                        "device_id": device.id,
                        "domain": "number",
                        "entity_id": old.id,
                        "type": "set_value",
                        "value": 10,
                    }
                ],
            }
        },
    )
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert registry.async_get(old.entity_id) is None
    issue = _issue(hass, native)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["users"] == "automation.quiet_night"

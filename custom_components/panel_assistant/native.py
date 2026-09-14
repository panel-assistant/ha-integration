"""Native entities rendered from a panel's transport session.

Dormant by default. Only the ``native_entities`` option in this integration's
YAML turns them on; without it no native platform is set up and no registry
entry is written. MQTT stays the authority either way: these entities render
what the panel reports over the native transport beside the MQTT entities, so
the two can be compared, and they send no commands.

Every entity's shape comes from the descriptor the panel sent in ``hello``: its
platform, translation key, category, default enablement, device class, unit
and options. Its unique ID is ``<did>_<unique_suffix>``, the ID a later cutover
migrates the MQTT entity to, so a native entity never claims an MQTT one.
Entities are added when a session describes them and never removed when a
later session does not: a channel that disappears only becomes unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .transport import (
    STATE_KNOWN,
    Observation,
    PanelSession,
    async_get_sessions,
    session_available,
    signal_observations,
    signal_session_changed,
)

CONF_NATIVE_ENTITIES: Final = "native_entities"
DATA_NATIVE_ENTITIES: Final = "native_entities"

# Platforms only native entities use. Sensor and update are always set up for
# the status sensor and the ha-paneld update entity, and add native entities
# of their own only while the option is on.
NATIVE_ONLY_PLATFORMS: Final = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
)

# Channels no native entity renders. The ha-paneld update already has its one
# entity, keyed by config entry, so its channel adds no second one.
NOT_RENDERED: Final = frozenset({"update_paneld"})

# Platforms whose channels are never reported, only described.
_UNREPORTED: Final = frozenset({Platform.BUTTON, Platform.EVENT})

# The exception every native command raises while MQTT holds the authority.
ERR_AUTHORITY_MISMATCH: Final = "authority_mismatch"


def native_entities_enabled(hass: HomeAssistant) -> bool:
    """Return whether native entities were turned on for this Home Assistant."""
    return bool(hass.data.get(DOMAIN, {}).get(DATA_NATIVE_ENTITIES, False))


def native_unique_id(did: str, unique_suffix: str) -> str:
    """Return a native entity's unique ID."""
    return f"{did}_{unique_suffix}"


def enum_or_none[E: StrEnum](enum: type[E], value: str | None) -> E | None:
    """Return a Home Assistant enum member for a code, or None if it has none."""
    if value is None:
        return None
    try:
        return enum(value)
    except ValueError:
        return None


def commands_refused() -> HomeAssistantError:
    """Return the error for a command that MQTT, not this entity, would carry."""
    return HomeAssistantError(
        "The panel is controlled through MQTT, not Panel Assistant",
        translation_domain=DOMAIN,
        translation_key=ERR_AUTHORITY_MISMATCH,
    )


class NativeEntity(Entity):
    """One channel of one panel, as its latest session describes it."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry_id: str, session: PanelSession, descriptor: Mapping[str, Any]
    ) -> None:
        """Take the entity's registry shape from the descriptor."""
        self._entry_id = entry_id
        self._channel: str = descriptor["channel"]
        self._descriptor = descriptor
        self._attr_unique_id = native_unique_id(
            session.did, descriptor["unique_suffix"]
        )
        self._attr_translation_key = descriptor["translation_key"]
        if descriptor["index"] is not None:
            self._attr_translation_placeholders = {"index": str(descriptor["index"])}
        self._attr_entity_category = enum_or_none(
            EntityCategory, descriptor["entity_category"]
        )
        self._attr_entity_registry_enabled_default = descriptor["enabled_default"]
        # The entry's own device, which its status sensor already describes.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry_id)})

    @property
    def descriptor(self) -> Mapping[str, Any]:
        """Return the live session's descriptor, else the last one seen."""
        session = async_get_sessions(self.hass).get(self._entry_id)
        if session is not None and self._channel in session.descriptors:
            return session.descriptors[self._channel]
        return self._descriptor

    @property
    def observation(self) -> Observation | None:
        """Return the live session's latest known observation of this channel."""
        session = async_get_sessions(self.hass).get(self._entry_id)
        if session is None:
            return None
        observation = session.observations.get(self._channel)
        if observation is None or observation.state != STATE_KNOWN:
            return None
        return observation

    @property
    def reported_value(self) -> Any:
        """Return the latest known value, or None."""
        observation = self.observation
        return None if observation is None else observation.value

    @property
    def available(self) -> bool:
        """Available while a synced session describes and reports the channel."""
        if not session_available(self.hass, self._entry_id):
            return False
        session = async_get_sessions(self.hass).get(self._entry_id)
        if (
            session is None
            or self._channel not in session.descriptors
            or self._channel in session.unknown_channels
        ):
            return False
        return self.platform_name in _UNREPORTED or self.observation is not None

    @property
    def platform_name(self) -> str:
        """Return the entity platform the descriptor declared."""
        platform: str = self._descriptor["platform"]
        return platform

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the reported attributes, whose keys are translation codes."""
        observation = self.observation
        return None if observation is None else dict(observation.attributes) or None

    async def async_added_to_hass(self) -> None:
        """Follow the session and this channel's reports."""
        await super().async_added_to_hass()
        self.handle_observation()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_session_changed(self._entry_id), self._refresh
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_observations(self._entry_id), self._observed
            )
        )

    @callback
    def _refresh(self) -> None:
        session = async_get_sessions(self.hass).get(self._entry_id)
        if session is not None and self._channel in session.descriptors:
            self._descriptor = session.descriptors[self._channel]
        self.async_write_ha_state()

    @callback
    def _observed(self, channels: frozenset[str]) -> None:
        if self._channel in channels:
            self.handle_observation()
            self.async_write_ha_state()

    @callback
    def handle_observation(self) -> None:
        """React to a newly stored observation before the state is written."""


type NativeFactory = Callable[[str, PanelSession, Mapping[str, Any]], Entity]


@callback
def async_setup_native_platform(
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: Platform,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: NativeFactory,
) -> None:
    """Add an entity for each channel of this platform a session describes.

    Does nothing unless native entities are turned on.
    """
    if not native_entities_enabled(hass):
        return
    added: set[str] = set()

    @callback
    def _add_described() -> None:
        session = async_get_sessions(hass).get(entry.entry_id)
        if session is None:
            return
        new: list[Entity] = []
        for channel, descriptor in session.descriptors.items():
            if (
                descriptor["platform"] != platform
                or channel in session.unknown_channels
                or channel in NOT_RENDERED
            ):
                continue
            unique_id = native_unique_id(session.did, descriptor["unique_suffix"])
            if unique_id in added:
                continue
            added.add(unique_id)
            new.append(factory(entry.entry_id, session, descriptor))
        if new:
            async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_session_changed(entry.entry_id), _add_described
        )
    )
    _add_described()

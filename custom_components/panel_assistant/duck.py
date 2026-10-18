"""Turn down the other players in a panel's area while it holds a voice turn.

The area is the one Core gives the satellite (its entity's area, else its
device's), so the room turned down is the room "pause the music" acts on.
Every media player there that is making sound, other than the panel's own,
is lowered when the satellite starts listening and set back when the
conversation ends. The panel pauses its own player for a turn itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Final

import voluptuous as vol
from homeassistant.components.media_player import DATA_COMPONENT
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    Platform,
)
from homeassistant.core import CALLBACK_TYPE, Context, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# A ducked player plays at this share of its level.
DUCK_FACTOR: Final = 0.3
# The panel's own bounds on a conversation (SatelliteTurnRunner in the app):
# no turn outlasts two minutes, and a follow-up turn starts within 4 s of the
# reply ending. A duck outliving either is let go.
TURN_LIMIT: Final = 120.0
FOLLOW_UP_LIMIT: Final = 4.0

_SOUNDING: Final = frozenset(
    {MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING, MediaPlayerState.ON}
)


@dataclass
class _Ducked:
    """A player held down: its level before, and the level it was set to.

    A player that can only mute has no level; it is muted instead.
    """

    level: float | None
    lowered: float | None
    holders: set[str] = field(default_factory=set)
    # The newest call to the player; each call waits for the one before it to
    # land, so a late restore can never overtake a later duck.
    last: asyncio.Task[None] | None = None


# Every ducked player, shared by the panels in an area: a player goes back
# only when the last conversation holding it ends, and its record stays
# until that restore has landed, so a conversation starting meanwhile takes
# the same record (and the level from before the first duck) back.
_DATA_DUCKED: HassKey[dict[str, _Ducked]] = HassKey(f"{DOMAIN}_ducked_players")


class RoomDuck:
    """One panel's hold on the players in its area."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Hold players for the panel of one entry."""
        self._hass = hass
        self._entry_id = entry_id
        self._satellite_id = ""
        self._held: set[str] = set()
        self._cancel_limit: CALLBACK_TYPE | None = None

    @callback
    def duck(self, satellite_id: str) -> None:
        """Lower the area's sounding players, or keep holding them for this turn."""
        if not self._held:
            self._satellite_id = satellite_id
            ducked = self._hass.data.setdefault(_DATA_DUCKED, {})
            for entity_id, state in self._sounding(satellite_id):
                held = ducked.get(entity_id)
                if held is None:
                    held = _lower(state)
                    if held is None:
                        continue
                    ducked[entity_id] = held
                    self._call(entity_id, held, held.lowered, mute=True)
                elif not held.holders:
                    # Its restore has not landed: lower it again after that.
                    self._call(entity_id, held, held.lowered, mute=True)
                held.holders.add(self._entry_id)
                self._held.add(entity_id)
        if self._held:
            self._limit(TURN_LIMIT, overrun=True)

    @callback
    def end(self, *, follow_up: bool) -> None:
        """End a turn: hold briefly for the follow-up turn, or let go."""
        if follow_up and self._held:
            self._limit(FOLLOW_UP_LIMIT, overrun=False)
        else:
            self.release()

    @callback
    def release(self) -> None:
        """Set back every player only this panel still holds."""
        if self._cancel_limit is not None:
            self._cancel_limit()
            self._cancel_limit = None
        ducked = self._hass.data.get(_DATA_DUCKED, {})
        for entity_id in sorted(self._held):
            held = ducked.get(entity_id)
            if held is None:
                continue
            held.holders.discard(self._entry_id)
            # Skipped when it runs if a conversation holds the player again.
            self._call(entity_id, held, held.level, mute=False, restoring=True)
        self._held.clear()

    @callback
    def _sounding(self, satellite_id: str) -> list[tuple[str, State]]:
        entities = er.async_get(self._hass)
        satellite = entities.async_get(satellite_id)
        area_id = satellite.area_id if satellite is not None else None
        if area_id is None and satellite is not None and satellite.device_id:
            device = dr.async_get(self._hass).async_get(satellite.device_id)
            area_id = device.area_id if device is not None else None
        if area_id is None:
            return []
        selected = async_extract_referenced_entity_ids(
            self._hass, TargetSelection({ATTR_AREA_ID: area_id})
        )
        players = []
        for entity_id in sorted(selected.indirectly_referenced):
            entry = entities.async_get(entity_id)
            if (
                entry is None
                or entry.domain != Platform.MEDIA_PLAYER
                or entry.config_entry_id == self._entry_id
            ):
                continue
            state = self._hass.states.get(entity_id)
            if state is not None and state.state in _SOUNDING:
                players.append((entity_id, state))
        return players

    @callback
    def _untouched(self, entity_id: str, held: _Ducked) -> bool:
        """Whether a player still sits where the duck left it.

        A level within half the player's own volume step of the lowered level
        (or of its old one, when the duck has not landed yet) was left alone;
        anything further was set by someone during the conversation.
        """
        state = self._hass.states.get(entity_id)
        player = self._hass.data[DATA_COMPONENT].get_entity(entity_id)
        level = None if state is None else state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
        if level is None or player is None:
            return False
        tolerance = player.volume_step / 2
        return any(
            abs(level - mark) <= tolerance
            for mark in (held.lowered, held.level)
            if mark is not None
        )

    @callback
    def _limit(self, delay: float, *, overrun: bool) -> None:
        if self._cancel_limit is not None:
            self._cancel_limit()

        @callback
        def _expired(_now: Any) -> None:
            self._cancel_limit = None
            if overrun:
                _LOGGER.warning(
                    "A voice turn on %s did not end within %.0f s; "
                    "setting the players in its area back",
                    self._satellite_id,
                    delay,
                )
            self.release()

        self._cancel_limit = async_call_later(self._hass, delay, _expired)

    @callback
    def _call(
        self,
        entity_id: str,
        held: _Ducked,
        level: float | None,
        *,
        mute: bool,
        restoring: bool = False,
    ) -> None:
        held.last = self._hass.async_create_task(
            self._async_call(entity_id, held, held.last, level, mute, restoring),
            f"{DOMAIN}_duck_{entity_id}",
            eager_start=True,
        )

    async def _async_call(
        self,
        entity_id: str,
        held: _Ducked,
        previous: asyncio.Task[None] | None,
        level: float | None,
        mute: bool,
        restoring: bool,
    ) -> None:
        if previous is not None:
            # Its own failure was logged when it ran.
            await asyncio.wait([previous])
        try:
            if restoring and (
                held.holders
                or (held.level is not None and not self._untouched(entity_id, held))
            ):
                return
            data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}
            if level is None:
                service, data[ATTR_MEDIA_VOLUME_MUTED] = SERVICE_VOLUME_MUTE, mute
            else:
                service, data[ATTR_MEDIA_VOLUME_LEVEL] = SERVICE_VOLUME_SET, level
            try:
                await self._hass.services.async_call(
                    Platform.MEDIA_PLAYER,
                    service,
                    data,
                    blocking=True,
                    context=Context(),
                )
            except (HomeAssistantError, vol.Invalid) as err:
                _LOGGER.warning("Could not change the volume of %s: %s", entity_id, err)
        finally:
            # The last call to a player nobody holds closes its record.
            ducked = self._hass.data.get(_DATA_DUCKED, {})
            if (
                not held.holders
                and held.last is asyncio.current_task()
                and ducked.get(entity_id) is held
            ):
                del ducked[entity_id]


def _lower(state: State) -> _Ducked | None:
    """Return how to hold one sounding player down, or None if it cannot be."""
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
    level = state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
    if features & MediaPlayerEntityFeature.VOLUME_SET and level is not None:
        return _Ducked(level, level * DUCK_FACTOR)
    if (
        features & MediaPlayerEntityFeature.VOLUME_MUTE
        and state.attributes.get(ATTR_MEDIA_VOLUME_MUTED) is False
    ):
        return _Ducked(None, None)
    return None

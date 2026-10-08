"""The native transport's channel catalogue and command outcome codes.

``panel_assistant_transport_v1.json`` is the one definition of the command
outcome codes and of every channel this integration can render. The message
vocabulary (commands, sync, states, errors) lives in ``transport.py``. The
catalogue holds each channel's identity and the presentation only Home
Assistant decides (attributes, colour modes); every other entity fact comes
from the panel's own descriptor. It decides which descriptors are known: a
descriptor the catalogue does not know is accepted but creates nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

CONTRACT_PATH: Final = Path(__file__).parent / "panel_assistant_transport_v1.json"
CONTRACT: Final[dict[str, Any]] = json.loads(CONTRACT_PATH.read_bytes())

_BY_CHANNEL: Final[dict[str, dict[str, Any]]] = {
    entry["channel"]: entry for entry in CONTRACT["channels"] if entry["family"] is None
}
_BY_FAMILY: Final[dict[str, dict[str, Any]]] = {
    entry["family"]: entry
    for entry in CONTRACT["channels"]
    if entry["family"] is not None
}


def catalogue_entry(descriptor: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the catalogue entry a descriptor names, if the catalogue knows it.

    The channel, platform, translation key and unique suffix must all agree, so
    a descriptor that reuses a known channel for another shape is unknown.
    """
    family = descriptor["family"]
    if family is None:
        entry = _BY_CHANNEL.get(descriptor["channel"])
        # Older apps still describe their camera control as a switch. Keep that
        # exact shape usable while Home Assistant is updated before the app.
        if (
            entry is not None
            and descriptor["channel"] == "camera_enabled"
            and descriptor["platform"] == "switch"
            and descriptor["translation_key"] == "camera_enabled"
            and descriptor["unique_suffix"] == "camera_enabled"
        ):
            entry = entry | {
                "platform": "switch",
                "translation_key": "camera_enabled",
                "unique_suffix": "camera_enabled",
            }
        suffix = None if entry is None else entry["unique_suffix"]
    else:
        entry = _BY_FAMILY.get(family)
        index = descriptor["index"]
        if entry is None or index < 1 or descriptor["channel"] != f"{family}{index}":
            return None
        suffix = entry["unique_suffix"].format(index=index)
    if (
        entry is None
        or entry["platform"] != descriptor["platform"]
        or entry["translation_key"] != descriptor["translation_key"]
        or suffix != descriptor["unique_suffix"]
    ):
        return None
    return entry


def catalogue_entry_for_channel(channel: str) -> tuple[dict[str, Any], str] | None:
    """Return the catalogue entry of a channel ID and its entities' unique suffix.

    A family member's ID carries its index within the contract's bound:
    ``relay3`` is the relay family with the suffix ``relay3``; ``relay0`` and
    ``relay065`` are nothing.
    """
    entry = _BY_CHANNEL.get(channel)
    if entry is not None:
        return entry, entry["unique_suffix"]
    for family, candidate in _BY_FAMILY.items():
        index = channel.removeprefix(family)
        if (
            index != channel
            and index.isdecimal()
            and str(int(index)) == index
            and 1 <= int(index) <= CONTRACT["max_family_index"]
        ):
            return candidate, candidate["unique_suffix"].format(index=index)
    return None

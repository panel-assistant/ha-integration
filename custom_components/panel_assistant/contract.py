"""The shared native transport contract, vendored from the panel.

``panel_assistant_transport_v1.json`` is the one definition of the protocol's
closed code lists and of every channel the panel can describe. The panel owns
it; this integration reads it and never restates it in Python. Its channel
catalogue decides which descriptors are known: a descriptor the catalogue does
not know is accepted but creates nothing.
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
_BY_SUFFIX: Final[dict[str, dict[str, Any]]] = {
    entry["unique_suffix"]: entry for entry in _BY_CHANNEL.values()
}


def _split_family_suffix(template: str) -> tuple[str, str]:
    """Split a family's suffix template around its index: ``relay{index}``."""
    head, _, tail = template.partition("{index}")
    return head, tail


# Each family's suffix template split around its index, with its entry.
_FAMILY_SUFFIXES: Final[list[tuple[str, str, dict[str, Any]]]] = [
    (*_split_family_suffix(entry["unique_suffix"]), entry)
    for entry in _BY_FAMILY.values()
]


def catalogue_entry(descriptor: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the catalogue entry a descriptor names, if the catalogue knows it.

    The channel, platform, translation key and unique suffix must all agree, so
    a descriptor that reuses a known channel for another shape is unknown.
    """
    family = descriptor["family"]
    if family is None:
        entry = _BY_CHANNEL.get(descriptor["channel"])
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


def catalogue_entry_for_suffix(
    platform: str, unique_suffix: str
) -> dict[str, Any] | None:
    """Return the catalogue entry whose entities carry this unique suffix, if any.

    The MQTT bridge keys a panel's entities by the same suffixes, so this is
    how an MQTT entity is matched to the channel that replaces it. A family
    suffix carries an index within the contract's bound: ``relay3`` is the
    relay family, ``relay0`` and ``relay065`` are nothing.
    """
    entry = _BY_SUFFIX.get(unique_suffix)
    if entry is None:
        for head, tail, candidate in _FAMILY_SUFFIXES:
            if not (unique_suffix.startswith(head) and unique_suffix.endswith(tail)):
                continue
            index = unique_suffix[len(head) : len(unique_suffix) - len(tail)]
            if (
                index.isdecimal()
                and str(int(index)) == index
                and 1 <= int(index) <= CONTRACT["max_family_index"]
            ):
                entry = candidate
                break
    if entry is None or entry["platform"] != platform:
        return None
    return entry

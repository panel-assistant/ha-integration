"""The shared native transport contract, vendored from the panel.

``panel_assistant_transport_v1.json`` is the one definition of the protocol's
closed code lists and of every channel the panel can describe. The panel owns
it; this integration reads it and never restates it in Python. Its channel
catalogue decides which descriptors are known: a descriptor the catalogue does
not know is accepted but creates nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

CONTRACT_PATH: Final = Path(__file__).parent / "panel_assistant_transport_v1.json"
_CONTRACT_BYTES: Final = CONTRACT_PATH.read_bytes()
CONTRACT: Final[dict[str, Any]] = json.loads(_CONTRACT_BYTES)
CONTRACT_DIGEST: Final = hashlib.sha256(_CONTRACT_BYTES).hexdigest()

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

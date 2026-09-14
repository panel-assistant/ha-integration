"""Moving a panel's MQTT entities to this integration, and back.

While MQTT owns a panel, its entities are registry entries of the ``mqtt``
platform with the unique IDs ``<panel_id>_<unique_suffix>``. Choosing the
native authority moves each one whose suffix the catalogue knows to this
integration: the same registry entry, so its entity ID, history and every
customisation stay, under the native unique ID ``<did>_<unique_suffix>`` that
the channel's native entity renders into. Choosing MQTT or shadow again moves
them back.

Both run in ``async_setup_entry`` before any platform loads, so no entity of
this integration is loaded, and each MQTT entity is disabled first, which
unloads it, because Home Assistant migrates only unloaded entities. Every step
lands in the entry's data as it completes: a failure leaves a partial record,
a Repairs issue and a loadable entry, and the next setup carries on from the
record. MQTT entities the catalogue does not know, and the ha-paneld update
whose one entity this integration already shows, stay behind for the panel's
own tombstones; a customised one holds back the panel's MQTT withdrawal until
a person deletes it or clears the customisation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import entity_sources
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_CUTOVER, DOMAIN
from .contract import catalogue_entry_for_suffix
from .device import panel_device_info
from .native import NOT_RENDERED, native_unique_id
from .transport import (
    AUTHORITY_NATIVE,
    CUTOVER_COMPLETE,
    CUTOVER_IN_PROGRESS,
    CUTOVER_REGISTRY_ID,
    CUTOVER_REVERSING,
    CUTOVER_STATE,
    CUTOVER_UNMIGRATED,
    ISSUE_CUTOVER_BLOCKED,
    MQTT_DOMAIN,
    _panel_did,
    async_delete_cutover_issues,
    async_raise_cutover_blocked_issue,
    async_raise_cutover_incomplete_issue,
    blocking_entity_ids,
    cutover_record,
    effective_authority,
    is_customised,
)

if TYPE_CHECKING:
    from . import HaPaneldConfigEntry

_LOGGER = logging.getLogger(__name__)

# The steps a record names when one fails.
STEP_IDENTITY: Final = "identity"
STEP_DEVICE: Final = "device"
STEP_TARGET: Final = "target"
STEP_DISABLE: Final = "disable"
STEP_MIGRATE: Final = "migrate"
STEP_ENABLE: Final = "enable"
STEP_RECORD: Final = "record"
STEP_MQTT_ENTRY: Final = "mqtt_entry"
STEP_UNEXPECTED: Final = "unexpected"

# How far one entity got. Pending is recorded before anything is done to it,
# so a later step's failure still shows what its MQTT registry entry was.
ENTITY_PENDING: Final = "pending"
ENTITY_DISABLED: Final = "disabled"
ENTITY_MIGRATED: Final = "migrated"
ENTITY_DONE: Final = "done"
ENTITY_REVERSED: Final = "reversed"
# A person deleted the entity meanwhile; nothing is left to move.
ENTITY_DELETED: Final = "deleted"

# Why an MQTT entity stays behind.
REASON_UNKNOWN_SUFFIX: Final = "unknown_suffix"
REASON_NOT_RENDERED: Final = "not_rendered"

# How long a disabled MQTT entity may take to unload before the step fails.
UNLOAD_TIMEOUT: Final = 10.0

_KEY_ENTITIES: Final = "entities"
_KEY_ERROR: Final = "error"
_KEY_REMOVED: Final = "removed"
_KEY_DISABLED_BEFORE: Final = "disabled_by_before"


class CutoverStepFailed(Exception):
    """A step failed; the cause is chained, and the record names the step."""

    def __init__(self, step: str, entity_id: str | None) -> None:
        """Remember which step, and which entity if one was being moved."""
        super().__init__(step)
        self.step = step
        self.entity_id = entity_id


def _refuse(step: str, message: str, entity_id: str | None = None) -> CutoverStepFailed:
    """Return a failure of this integration's own finding, its reason chained."""
    failure = CutoverStepFailed(step, entity_id)
    failure.__cause__ = ValueError(message)
    return failure


@contextmanager
def _step(step: str, entity_id: str | None = None) -> Iterator[None]:
    """Attribute any failure inside to one step of the transaction."""
    try:
        yield
    except CutoverStepFailed:
        raise
    except Exception as err:
        raise CutoverStepFailed(step, entity_id) from err


def _write(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, record: dict[str, Any]
) -> None:
    """Land the record in the entry's data. Only a copy is stored."""
    with _step(STEP_RECORD):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CUTOVER: deepcopy(record)}
        )


def _mqtt_prefix(panel_id: str) -> str:
    return f"{panel_id}_"


def _mqtt_device(hass: HomeAssistant, panel_id: str) -> dr.DeviceEntry | None:
    """Return the panel's MQTT device, whichever MQTT config entry owns it."""
    device_registry = dr.async_get(hass)
    identifier = (MQTT_DOMAIN, f"ha-paneld-{panel_id}")
    for mqtt_entry in hass.config_entries.async_entries(MQTT_DOMAIN):
        device = device_registry.async_get_device_by_identifier(
            identifier, mqtt_entry.entry_id
        )
        if device is not None:
            return device
    return None


def _mqtt_candidates(
    hass: HomeAssistant, registry: er.EntityRegistry, panel_id: str
) -> list[er.RegistryEntry]:
    """Return the panel's MQTT entities: on its MQTT device, or on no device.

    An entry on some other device is another integration's, whatever its
    unique ID says.
    """
    prefix = _mqtt_prefix(panel_id)
    device = _mqtt_device(hass, panel_id)
    found: dict[str, er.RegistryEntry] = {}
    if device is not None:
        for item in er.async_entries_for_device(
            registry, device.id, include_disabled_entities=True
        ):
            found[item.id] = item
    for mqtt_entry in hass.config_entries.async_entries(MQTT_DOMAIN):
        for item in er.async_entries_for_config_entry(registry, mqtt_entry.entry_id):
            if item.device_id is None:
                found[item.id] = item
    return sorted(
        (
            item
            for item in found.values()
            if item.platform == MQTT_DOMAIN and item.unique_id.startswith(prefix)
        ),
        key=lambda item: item.entity_id,
    )


async def _async_wait_unloaded(hass: HomeAssistant, entity_id: str) -> None:
    """Wait for a disabled entity to leave the loaded entities, bounded.

    A removed entity leaves the loaded entities before its state goes, so
    its state change is the moment to look again.
    """
    changed = asyncio.Event()

    @callback
    def _state_changed(_event: Event[EventStateChangedData]) -> None:
        changed.set()

    unsubscribe = async_track_state_change_event(hass, entity_id, _state_changed)
    try:
        async with asyncio.timeout(UNLOAD_TIMEOUT):
            while entity_id in entity_sources(hass):
                await changed.wait()
                changed.clear()
    finally:
        unsubscribe()


def _own_device(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, panel_id: str
) -> dr.DeviceEntry:
    """Return the entry's own device, as its status sensor describes it.

    The MQTT device's area is copied when ours has none, so the entities keep
    their room.
    """
    device_registry = dr.async_get(hass)
    runtime_data = entry.runtime_data
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **panel_device_info(
            entry.entry_id,
            runtime_data.coordinator.data,
            runtime_data.client.configuration_url,
        ),
    )
    mqtt_device = _mqtt_device(hass, panel_id)
    if device.area_id is None and mqtt_device is not None and mqtt_device.area_id:
        device_registry.async_update_device(device.id, area_id=mqtt_device.area_id)
    return device


def _check_identity(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, did: str | None
) -> str:
    """Return the panel's identity, refusing one that is missing or shared."""
    if did is None:
        raise _refuse(
            STEP_IDENTITY, "The panel reports no identity to key its entities by"
        )
    for other in hass.config_entries.async_loaded_entries(DOMAIN):
        if other.entry_id != entry.entry_id and _panel_did(other) == did:
            raise _refuse(
                STEP_IDENTITY, "Another loaded panel entry reports this identity"
            )
    return did


def _entity_info(item: er.RegistryEntry) -> dict[str, Any]:
    """Return the record of one MQTT entity before anything is done to it."""
    return {
        "entity_id": item.entity_id,
        "mqtt_unique_id": item.unique_id,
        "mqtt_config_entry_id": item.config_entry_id,
        "mqtt_device_id": item.device_id,
        _KEY_DISABLED_BEFORE: (
            None if item.disabled_by is None else item.disabled_by.value
        ),
        CUTOVER_STATE: ENTITY_PENDING,
    }


def _disabled_by_us(info: dict[str, Any], item: er.RegistryEntry) -> bool:
    """Return whether the entity's disable is this integration's own."""
    return (
        info[_KEY_DISABLED_BEFORE] is None
        and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )


async def _async_migrate_one(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    record: dict[str, Any],
    item: er.RegistryEntry,
    target: str,
    device_id: str,
) -> None:
    """Move one MQTT entity to this integration, recording each step."""
    registry = er.async_get(hass)
    entities: dict[str, dict[str, Any]] = record[_KEY_ENTITIES]
    # A record from an earlier attempt already knows whether the entity was
    # disabled before this integration disabled it.
    info = entities.get(item.id)
    if info is None:
        info = entities[item.id] = _entity_info(item)
    info["entity_id"] = item.entity_id

    with _step(STEP_TARGET, item.entity_id):
        existing = registry.async_get_entity_id(item.domain, DOMAIN, target)
        if existing is not None:
            # Only a native entity rendered while the panel was still MQTT's
            # carries this ID, and none is loaded now.
            registry.async_remove(existing)
            record[_KEY_REMOVED].append(existing)
    _write(hass, entry, record)

    with _step(STEP_DISABLE, item.entity_id):
        if item.disabled_by is None:
            registry.async_update_entity(
                item.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
            info[CUTOVER_STATE] = ENTITY_DISABLED
            _write(hass, entry, record)
        await _async_wait_unloaded(hass, item.entity_id)

    with _step(STEP_MIGRATE, item.entity_id):
        registry.async_update_entity_platform(
            item.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            # An entity of an MQTT subentry may not keep it under another
            # config entry; Core accepts None here though its type omits it.
            new_config_subentry_id=None,  # type: ignore[arg-type]
            new_unique_id=target,
            new_device_id=device_id,
        )
    info[CUTOVER_STATE] = ENTITY_MIGRATED
    _write(hass, entry, record)
    _enable_if_ours(hass, entry, record, item.id, info)


def _enable_if_ours(
    hass: HomeAssistant,
    entry: HaPaneldConfigEntry,
    record: dict[str, Any],
    registry_id: str,
    info: dict[str, Any],
) -> None:
    """Clear a disable this integration made; a person's stays."""
    registry = er.async_get(hass)
    item = registry.entities.get_entry(registry_id)
    if item is None:
        info[CUTOVER_STATE] = ENTITY_DELETED
        _write(hass, entry, record)
        return
    with _step(STEP_ENABLE, item.entity_id):
        if _disabled_by_us(info, item):
            registry.async_update_entity(item.entity_id, disabled_by=None)
    info[CUTOVER_STATE] = ENTITY_DONE
    _write(hass, entry, record)


async def _async_forward(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, record: dict[str, Any]
) -> None:
    """Move every known MQTT entity of the panel to this integration."""
    panel_id: str = entry.runtime_data.coordinator.data.health.panel_id
    record[CUTOVER_STATE] = CUTOVER_IN_PROGRESS
    record["panel_id"] = panel_id
    record.setdefault(_KEY_ENTITIES, {})
    record.setdefault(_KEY_REMOVED, [])
    record.setdefault(CUTOVER_UNMIGRATED, [])
    record.pop(_KEY_ERROR, None)
    did = _check_identity(hass, entry, _panel_did(entry))
    record["did"] = did
    _write(hass, entry, record)

    with _step(STEP_DEVICE):
        device = _own_device(hass, entry, panel_id)

    registry = er.async_get(hass)
    prefix = _mqtt_prefix(panel_id)
    unmigrated: list[dict[str, Any]] = []
    for item in _mqtt_candidates(hass, registry, panel_id):
        suffix = item.unique_id.removeprefix(prefix)
        catalogue = catalogue_entry_for_suffix(item.domain, suffix)
        reason = None
        if catalogue is None:
            reason = REASON_UNKNOWN_SUFFIX
        elif catalogue["channel"] in NOT_RENDERED:
            reason = REASON_NOT_RENDERED
        if reason is not None:
            unmigrated.append(
                {
                    CUTOVER_REGISTRY_ID: item.id,
                    "entity_id": item.entity_id,
                    "unique_suffix": suffix,
                    "reason": reason,
                    "customised": is_customised(item),
                }
            )
            continue
        await _async_migrate_one(
            hass, entry, record, item, native_unique_id(did, suffix), device.id
        )
    record[CUTOVER_UNMIGRATED] = unmigrated

    # Entities an earlier attempt moved but did not finish with, and ones a
    # person deleted since they were recorded.
    entities: dict[str, dict[str, Any]] = record[_KEY_ENTITIES]
    for registry_id, info in list(entities.items()):
        if info[CUTOVER_STATE] in (ENTITY_DONE, ENTITY_DELETED):
            continue
        if registry.entities.get_entry(registry_id) is None:
            info[CUTOVER_STATE] = ENTITY_DELETED
            _write(hass, entry, record)
        elif info[CUTOVER_STATE] == ENTITY_MIGRATED:
            _enable_if_ours(hass, entry, record, registry_id, info)

    record[CUTOVER_STATE] = CUTOVER_COMPLETE
    _write(hass, entry, record)
    async_delete_cutover_issues(hass, entry.entry_id)
    if blocking := blocking_entity_ids(hass, entry):
        async_raise_cutover_blocked_issue(hass, entry, blocking)


def _mqtt_entry_id(hass: HomeAssistant, recorded: str | None) -> str:
    """Return the MQTT config entry to hand an entity back to."""
    if recorded is not None and hass.config_entries.async_get_entry(recorded):
        return recorded
    loaded = hass.config_entries.async_loaded_entries(MQTT_DOMAIN)
    if len(loaded) != 1:
        raise _refuse(
            STEP_MQTT_ENTRY, "No single MQTT config entry to hand the entities back to"
        )
    return loaded[0].entry_id


async def _async_reverse(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, record: dict[str, Any]
) -> None:
    """Hand every moved entity back to MQTT, then forget the record."""
    record[CUTOVER_STATE] = CUTOVER_REVERSING
    record.pop(_KEY_ERROR, None)
    _write(hass, entry, record)
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entities: dict[str, dict[str, Any]] = record.get(_KEY_ENTITIES, {})
    for registry_id, info in entities.items():
        item = registry.entities.get_entry(registry_id)
        if item is None:
            info[CUTOVER_STATE] = ENTITY_DELETED
            _write(hass, entry, record)
            continue
        if item.platform != DOMAIN:
            # Never moved, or already handed back.
            info[CUTOVER_STATE] = ENTITY_REVERSED
            _write(hass, entry, record)
            continue
        mqtt_entry_id = _mqtt_entry_id(hass, info["mqtt_config_entry_id"])
        conflict = registry.async_get_entity_id(
            item.domain, MQTT_DOMAIN, info["mqtt_unique_id"]
        )
        if conflict is not None:
            # MQTT discovered the entity again meanwhile. That entry is MQTT's
            # to keep or drop, so this one waits.
            raise _refuse(
                STEP_TARGET,
                f"{conflict} already carries the MQTT unique ID",
                item.entity_id,
            )
        with _step(STEP_ENABLE, item.entity_id):
            # A disable this integration made and never cleared goes now,
            # while the entity is still its own: MQTT never sees it.
            if _disabled_by_us(info, item):
                registry.async_update_entity(item.entity_id, disabled_by=None)
        device_id = info["mqtt_device_id"]
        if device_id is not None and device_registry.async_get(device_id) is None:
            device_id = None
        with _step(STEP_MIGRATE, item.entity_id):
            registry.async_update_entity_platform(
                item.entity_id,
                MQTT_DOMAIN,
                new_config_entry_id=mqtt_entry_id,
                new_config_subentry_id=None,  # type: ignore[arg-type]
                new_unique_id=info["mqtt_unique_id"],
                new_device_id=device_id,
            )
        info[CUTOVER_STATE] = ENTITY_REVERSED
        _write(hass, entry, record)

    with _step(STEP_RECORD):
        data = {key: value for key, value in entry.data.items() if key != CONF_CUTOVER}
        hass.config_entries.async_update_entry(entry, data=data)
    async_delete_cutover_issues(hass, entry.entry_id)


type _Transaction = Callable[
    [HomeAssistant, HaPaneldConfigEntry, dict[str, Any]], Coroutine[Any, Any, None]
]


async def async_apply_cutover(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Move the panel's entities the way the entry's authority says, once.

    Never raises: a failure is recorded, reported as a Repairs issue, and
    picked up by the next setup. Nothing happens when the record already
    agrees with the authority.
    """
    record = cutover_record(entry)
    native = effective_authority(hass, entry) == AUTHORITY_NATIVE
    transaction: _Transaction
    if native and (record is None or record.get(CUTOVER_STATE) != CUTOVER_COMPLETE):
        transaction = _async_forward
    elif not native and record is not None:
        transaction = _async_reverse
    else:
        return
    working: dict[str, Any] = {} if record is None else deepcopy(dict(record))
    try:
        await transaction(hass, entry, working)
    except Exception as err:
        if isinstance(err, CutoverStepFailed):
            step, entity_id, cause = err.step, err.entity_id, err.__cause__ or err
        else:
            step, entity_id, cause = STEP_UNEXPECTED, None, err
        error = type(cause).__name__
        _LOGGER.warning(
            "Moving the entities of %s stopped at the %s step; it resumes on the"
            " next setup",
            entry.title,
            step,
            exc_info=cause,
        )
        working[_KEY_ERROR] = {"step": step, "exception": error, "entity_id": entity_id}
        try:
            _write(hass, entry, working)
        except CutoverStepFailed:
            _LOGGER.warning("The cutover record of %s could not be saved", entry.title)
        async_raise_cutover_incomplete_issue(hass, entry, step, error)
        async_delete_cutover_issues(hass, entry.entry_id, ISSUE_CUTOVER_BLOCKED)

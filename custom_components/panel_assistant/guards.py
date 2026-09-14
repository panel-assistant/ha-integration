"""Guards for the migration window, while panels move from MQTT to this integration.

Once a cutover has moved a panel's entities here (see ``cutover.py``), a panel
running an ha-paneld release that does not negotiate its MQTT withdrawal still
announces its MQTT discovery, and MQTT creates every entity a second time. So
while this integration owns the panel's entities, each entity MQTT creates on
the panel's MQTT device is quarantined: kept disabled and listed in the
cutover record, with a Repairs issue saying which side to update. When a panel
that follows the withdrawal completes its full sync, the quarantined entities
and the empty MQTT device are removed.

A removed entry cannot tell its panel anything, because Home Assistant unloads
an entry, ending its session, before it removes it. So the panel's identity is
remembered, across restarts, and its next hello is refused as removed; the
panel then releases its claim and announces its MQTT discovery again.

Everything here exists only while MQTT does, and goes with it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import entity_sources
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import is_valid_discovery_id
from .const import CONF_CUTOVER, DOMAIN, PANEL_MQTT_WITHDRAW_VERSION
from .transport import (
    AUTHORITY_NATIVE,
    CUTOVER_ENTITIES,
    CUTOVER_QUARANTINED,
    CUTOVER_REGISTRY_ID,
    CUTOVER_UNMIGRATED,
    DATA_REMOVED_PANELS,
    MQTT_DISCOVERY_WITHDRAW,
    MQTT_DOMAIN,
    _panel_did,
    async_get_sessions,
    cutover_issue_id,
    cutover_record,
    entity_owner,
    is_customised,
    signal_session_changed,
)

if TYPE_CHECKING:
    from . import HaPaneldConfigEntry

_LOGGER = logging.getLogger(__name__)

# The Repairs issue asking for ha-paneld on a panel to be updated, one per entry.
ISSUE_PANEL_UPDATE_REQUIRED: Final = "panel_update_required"

# How long a quarantined entity may take to load before it is disabled anyway.
LOAD_TIMEOUT: Final = 10.0

# The panels whose entry was removed, newest last.
REMOVED_PANELS_STORAGE_KEY: Final = f"{DOMAIN}.removed_panels"
REMOVED_PANELS_STORAGE_VERSION: Final = 1
MAX_REMOVED_PANELS: Final = 64

# Writes a cutover record; None forgets it.
type RecordWriter = Callable[[dict[str, Any] | None], None]


def mqtt_device(hass: HomeAssistant, panel_id: str) -> dr.DeviceEntry | None:
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


def _panel_ids(entry: ConfigEntry, record: dict[str, Any] | None) -> list[str]:
    """Return the panel IDs the record names and the panel reports now.

    The panel ID is editable, so MQTT may announce the panel under either.
    """
    ids: list[str] = []
    recorded = None if record is None else record.get("panel_id")
    runtime_data = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime_data, "coordinator", None)
    health = getattr(getattr(coordinator, "data", None), "health", None)
    for panel_id in (recorded, getattr(health, "panel_id", None)):
        if isinstance(panel_id, str) and panel_id and panel_id not in ids:
            ids.append(panel_id)
    return ids


def _listed(record: dict[str, Any]) -> set[str]:
    """Return every registry ID the cutover record already accounts for."""
    return {
        *record.get(CUTOVER_ENTITIES, {}),
        *(item[CUTOVER_REGISTRY_ID] for item in record.get(CUTOVER_UNMIGRATED, ())),
        *record.get(CUTOVER_QUARANTINED, ()),
    }


def _on_panel_device(
    hass: HomeAssistant, item: er.RegistryEntry, panel_ids: list[str]
) -> bool:
    """Return whether an entity sits on the panel's MQTT device.

    By the device's identifier, never by unique ID prefix: one panel's ID can
    prefix another's, as ``office_`` does ``office_dash_``.
    """
    if item.device_id is None:
        return False
    device = dr.async_get(hass).async_get(item.device_id)
    return device is not None and any(
        (MQTT_DOMAIN, f"ha-paneld-{panel_id}") in device.identifiers
        for panel_id in panel_ids
    )


def _is_duplicate(
    hass: HomeAssistant, entry: HaPaneldConfigEntry, item: er.RegistryEntry
) -> bool:
    """Return whether MQTT created this entity for a panel this entry owns."""
    if item.platform != MQTT_DOMAIN or entity_owner(hass, entry) != AUTHORITY_NATIVE:
        return False
    record = dict(cutover_record(entry) or {})
    return item.id not in _listed(record) and _on_panel_device(
        hass, item, _panel_ids(entry, record)
    )


def panel_update_required_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID asking for one entry's panel to be updated."""
    return cutover_issue_id(ISSUE_PANEL_UPDATE_REQUIRED, entry_id)


@callback
def async_raise_panel_update_required(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Report that the panel announces MQTT entities this integration owns."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        panel_update_required_issue_id(entry.entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_PANEL_UPDATE_REQUIRED,
        translation_placeholders={
            "panel": entry.title,
            "required_version": PANEL_MQTT_WITHDRAW_VERSION,
        },
    )


@callback
def async_delete_panel_update_required(hass: HomeAssistant, entry_id: str) -> None:
    """Withdraw the request to update an entry's panel."""
    ir.async_delete_issue(hass, DOMAIN, panel_update_required_issue_id(entry_id))


def entry_record_writer(hass: HomeAssistant, entry: ConfigEntry) -> RecordWriter:
    """Return a writer landing a record in a loaded entry's data, as a copy."""

    def write(record: dict[str, Any] | None) -> None:
        data = {key: value for key, value in entry.data.items() if key != CONF_CUTOVER}
        if record is not None:
            data[CONF_CUTOVER] = deepcopy(record)
        hass.config_entries.async_update_entry(entry, data=data)

    return write


@callback
def async_remove_quarantined(
    hass: HomeAssistant,
    entry: ConfigEntry,
    record: dict[str, Any],
    write: RecordWriter,
    *,
    remove_device: bool = True,
) -> None:
    """Remove the quarantined duplicates, then the panel's empty MQTT device.

    Only an entry that is still MQTT's, still disabled by this integration and
    carries nothing a person set is removed. Removed and vanished IDs leave the
    record. Home Assistant keeps a removed entry, disabled, and MQTT enables it
    again when it is discovered again, which the quarantine catches like any
    other creation.
    """
    registry = er.async_get(hass)
    quarantined: list[str] = list(record.get(CUTOVER_QUARANTINED, ()))
    kept: list[str] = []
    for registry_id in quarantined:
        item = registry.entities.get_entry(registry_id)
        if item is None:
            continue
        if (
            item.platform == MQTT_DOMAIN
            and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            and not is_customised(item)
        ):
            registry.async_remove(item.entity_id)
            continue
        kept.append(registry_id)
    if kept != quarantined:
        record[CUTOVER_QUARANTINED] = kept
        write(record)
    device_registry = dr.async_get(hass)
    for panel_id in _panel_ids(entry, record) if remove_device else ():
        device = mqtt_device(hass, panel_id)
        if device is not None and not er.async_entries_for_device(
            registry, device.id, include_disabled_entities=True
        ):
            device_registry.async_remove_device(device.id)
    async_delete_panel_update_required(hass, entry.entry_id)


async def _async_wait_loaded(hass: HomeAssistant, entity_id: str) -> None:
    """Wait, bounded, until an entity has loaded.

    An entity is among the loaded entities before its first state is written,
    so its state change is the moment to look again.
    """
    changed = asyncio.Event()

    @callback
    def _state_changed(_event: Event[EventStateChangedData]) -> None:
        changed.set()

    unsubscribe = async_track_state_change_event(hass, entity_id, _state_changed)
    try:
        async with asyncio.timeout(LOAD_TIMEOUT):
            while entity_id not in entity_sources(hass):
                await changed.wait()
                changed.clear()
    except TimeoutError:
        pass
    finally:
        unsubscribe()


class _EntryGuard:
    """The quarantine and the cleanup for one loaded entry that owns its panel."""

    def __init__(self, hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._pending: set[str] = set()
        self._cleaned_session: str | None = None

    @callback
    def registry_updated(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """Consider an entity MQTT created, or moved onto a device.

        MQTT restores an entity it discovers again without a device, and its
        platform links the device in a later update, so both count.
        """
        data = event.data
        if data["action"] == "create" or (
            data["action"] == "update" and "device_id" in data["changes"]
        ):
            self.consider(data["entity_id"])

    @callback
    def consider(self, entity_id: str) -> None:
        """Quarantine an entity if it duplicates one this entry owns."""
        item = er.async_get(self._hass).async_get(entity_id)
        if (
            item is None
            or item.id in self._pending
            or not _is_duplicate(self._hass, self._entry, item)
        ):
            return
        self._pending.add(item.id)
        self._entry.async_create_background_task(
            self._hass,
            self._async_quarantine(item.id, item.entity_id),
            f"{DOMAIN} quarantine {item.entity_id}",
        )

    async def _async_quarantine(self, registry_id: str, entity_id: str) -> None:
        # Wait for the entity to load before disabling it. The entity platform
        # (helpers/entity_platform.py) keeps the registry entry that
        # async_get_or_create returned and checks that stale copy for a
        # disable, so a disable made inside the synchronous create listener
        # still lets the entity load, and it would stay loaded. Once loaded,
        # the entity follows registry updates and a disable unloads it.
        try:
            await _async_wait_loaded(self._hass, entity_id)
            registry = er.async_get(self._hass)
            item = registry.entities.get_entry(registry_id)
            if item is None or not _is_duplicate(self._hass, self._entry, item):
                return
            # Always, even for an entry created disabled: MQTT restores a
            # removed entry disabled and then enables it again.
            if item.disabled_by is None:
                registry.async_update_entity(
                    item.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
                )
            record = dict(cutover_record(self._entry) or {})
            record[CUTOVER_QUARANTINED] = [
                *record.get(CUTOVER_QUARANTINED, ()),
                registry_id,
            ]
            entry_record_writer(self._hass, self._entry)(record)
            _LOGGER.info(
                "%s announced %s over MQTT although Panel Assistant owns its"
                " entities; it is kept disabled until the panel is updated",
                self._entry.title,
                item.entity_id,
            )
            async_raise_panel_update_required(self._hass, self._entry)
        finally:
            self._pending.discard(registry_id)

    @callback
    def session_changed(self) -> None:
        """Act on the entry's session opening or completing its full sync."""
        hass, entry = self._hass, self._entry
        session = async_get_sessions(hass).get(entry.entry_id)
        if session is None or entity_owner(hass, entry) != AUTHORITY_NATIVE:
            return
        if not session.mqtt_withdraw_offered:
            async_raise_panel_update_required(hass, entry)
            return
        if (
            session.full_sync_complete
            and session.mqtt_discovery == MQTT_DISCOVERY_WITHDRAW
            and session.token != self._cleaned_session
        ):
            # The panel has withdrawn its MQTT discovery, or does so now.
            self._cleaned_session = session.token
            record = deepcopy(dict(cutover_record(entry) or {}))
            async_remove_quarantined(
                hass, entry, record, entry_record_writer(hass, entry)
            )


def _mqtt_entities_on_panel_devices(
    hass: HomeAssistant, entry: ConfigEntry, record: dict[str, Any]
) -> Iterator[er.RegistryEntry]:
    registry = er.async_get(hass)
    for panel_id in _panel_ids(entry, record):
        if (device := mqtt_device(hass, panel_id)) is not None:
            yield from er.async_entries_for_device(
                registry, device.id, include_disabled_entities=True
            )


@callback
def async_start_guards(hass: HomeAssistant, entry: HaPaneldConfigEntry) -> None:
    """Guard a loaded entry that owns its panel's entities until it unloads.

    Runs once its cutover is settled. What MQTT created before now is
    considered once, as if it had just been created.
    """
    if entity_owner(hass, entry) != AUTHORITY_NATIVE:
        return
    record = dict(cutover_record(entry) or {})
    registry = er.async_get(hass)
    if any(
        registry.entities.get_entry(registry_id) is not None
        for registry_id in record.get(CUTOVER_QUARANTINED, ())
    ):
        async_raise_panel_update_required(hass, entry)
    guard = _EntryGuard(hass, entry)
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, guard.registry_updated)
    )
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, signal_session_changed(entry.entry_id), guard.session_changed
        )
    )
    for item in list(_mqtt_entities_on_panel_devices(hass, entry, record)):
        guard.consider(item.entity_id)


# ---------------------------------------------------------------------------
# Removed panels.


class RemovedPanels:
    """The identities of panels whose entry was removed, kept across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty list backed by Home Assistant's storage."""
        self._store: Store[dict[str, Any]] = Store(
            hass, REMOVED_PANELS_STORAGE_VERSION, REMOVED_PANELS_STORAGE_KEY
        )
        # Identity to removal time, oldest first.
        self._removed: dict[str, str] = {}

    def __contains__(self, did: object) -> bool:
        """Return whether a panel's entry was removed."""
        return did in self._removed

    async def async_load(self) -> None:
        """Read the stored list, ignoring anything malformed."""
        data = await self._store.async_load()
        panels = data.get("panels") if isinstance(data, dict) else None
        self._removed = {}
        for item in panels if isinstance(panels, list) else ():
            if (
                isinstance(item, dict)
                and isinstance(did := item.get("did"), str)
                and is_valid_discovery_id(did)
                and isinstance(removed_at := item.get("removed_at"), str)
            ):
                self._removed[did] = removed_at
        self._trim()

    async def async_add(self, did: str) -> None:
        """Remember a removed panel as the newest, and save."""
        self._removed.pop(did, None)
        self._removed[did] = dt_util.utcnow().isoformat()
        self._trim()
        await self._async_save()

    async def async_discard(self, did: str) -> None:
        """Forget a panel whose entry is loaded again, and save."""
        if self._removed.pop(did, None) is not None:
            await self._async_save()

    def _trim(self) -> None:
        while len(self._removed) > MAX_REMOVED_PANELS:
            del self._removed[next(iter(self._removed))]

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "panels": [
                    {"did": did, "removed_at": removed_at}
                    for did, removed_at in self._removed.items()
                ]
            }
        )


def _removed_panels(hass: HomeAssistant) -> RemovedPanels:
    removed: RemovedPanels = hass.data[DOMAIN][DATA_REMOVED_PANELS]
    return removed


async def async_load_removed_panels(hass: HomeAssistant) -> None:
    """Load the removed panels before any hello can ask about one."""
    removed = RemovedPanels(hass)
    await removed.async_load()
    hass.data.setdefault(DOMAIN, {})[DATA_REMOVED_PANELS] = removed


async def async_forget_removed_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget a removed panel once an entry reporting its identity loads."""
    did = _panel_did(entry)
    if did is None:
        return
    try:
        await _removed_panels(hass).async_discard(did)
    except Exception:
        _LOGGER.warning("The removed panels could not be saved", exc_info=True)


async def async_remember_removed_panel(
    hass: HomeAssistant, entry: ConfigEntry, record_did: str | None
) -> None:
    """Remember a removed entry's panel, so its next hello is told. Never raises."""
    async_delete_panel_update_required(hass, entry.entry_id)
    did = record_did if record_did is not None else _panel_did(entry)
    if did is None or not is_valid_discovery_id(did):
        return
    try:
        await _removed_panels(hass).async_add(did)
    except Exception:
        _LOGGER.warning(
            "The removal of %s could not be saved for its panel",
            entry.title,
            exc_info=True,
        )

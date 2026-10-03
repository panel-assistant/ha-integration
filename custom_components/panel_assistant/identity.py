"""Installation identity admission and preservation of an existing panel setup."""

from __future__ import annotations

import re
from copy import deepcopy
from hashlib import sha256
from ipaddress import ip_address

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .client import PanelHealth, is_valid_discovery_id, normalize_address
from .const import CONF_CUTOVER, CONF_SUPPORTED_CHANNELS, DOMAIN

CONF_INSTALL_IDENTITY = "installation_identity"
CONF_PREVIOUS_IDENTITY = "previous_installation_identity"
CONF_IDENTITY_PENDING = "identity_pending"
ISSUE_IDENTITY = "panel_identity_confirmation"
_UID = re.compile(r"[0-9a-f]{32}")


def is_installation(entry: ConfigEntry) -> bool:
    """Whether the saved identity was established by the installation protocol."""
    return entry.data.get(CONF_INSTALL_IDENTITY) is True


def identity_available(hass: HomeAssistant, entry: ConfigEntry, did: str) -> bool:
    """Include unloaded entries when reserving an identity."""
    return not any(
        other.entry_id != entry.entry_id and other.unique_id == did
        for other in hass.config_entries.async_entries(DOMAIN)
    )


def _native_identities(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    return {
        item.unique_id[:64]
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if item.platform == DOMAIN and re.fullmatch(r"[0-9a-f]{64}_.+", item.unique_id)
    }


def migration_candidate(entry: ConfigEntry, health: PanelHealth) -> bool:
    """A legacy hint identifies a migration candidate, never grants admission."""
    return (
        not is_installation(entry)
        and entry.unique_id is not None
        and health.installation_identity
        and health.discovery_id is not None
        and health.discovery_id != entry.unique_id
        and health.legacy_discovery_id == entry.unique_id
    )


def self_moved(entry: ConfigEntry, health: PanelHealth) -> bool:
    """The panel answers from the new app under a new identity linked to this entry.

    A move can finish on the panel without this integration seeing it finish:
    the move Repair's last ADB step can drop after the old app is already gone.
    The entry then still names the old app's installation identity while the
    panel answers with the new one. The panel links the two through its legacy
    identity, which is stable across apps and which this entry holds either as
    its own identity or as the one it replaced.
    An entry still on its original identity is the first hop, which
    ``migration_candidate`` already owns.

    This identifies the case; it admits nothing. The new identity is adopted
    only after the installed app is proved over ADB (``panel_move``).
    """
    from .app_identity import SUCCESSOR_PACKAGE_ID, reports_package

    linked = {entry.unique_id, entry.data.get(CONF_PREVIOUS_IDENTITY)} - {None}
    return (
        is_installation(entry)
        and entry.unique_id is not None
        and health.installation_identity
        and health.discovery_id is not None
        and health.discovery_id != entry.unique_id
        and health.legacy_discovery_id is not None
        and health.legacy_discovery_id in linked
        and reports_package(health.package, SUCCESSOR_PACKAGE_ID)
    )


def _prior_installation(hass: HomeAssistant, entry: ConfigEntry, did: str) -> bool:
    """Require exclusive MQTT evidence, including records retained after cleanup."""
    if any(
        other.entry_id != entry.entry_id
        and entry.unique_id in (other.unique_id, other.data.get(CONF_PREVIOUS_IDENTITY))
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        return False
    record = entry.data.get(CONF_CUTOVER)
    if not isinstance(record, dict) or record.get("did") != entry.unique_id:
        return False
    entities = record.get("entities", {})
    if not isinstance(entities, dict):
        return False
    device_ids = {
        item["mqtt_device_id"]
        for item in entities.values()
        if isinstance(item, dict) and isinstance(item.get("mqtt_device_id"), str)
    }
    if not device_ids:
        return False
    registry = dr.async_get(hass)
    for device_id in device_ids:
        device = registry.async_get(device_id) or registry.deleted_devices.get(
            device_id
        )
        if device is None:
            return False
        uids = {
            value.removeprefix("ha-paneld-uid-")
            for domain, value in device.identifiers
            if domain == "mqtt" and value.startswith("ha-paneld-uid-")
        }
        if len(uids) != 1:
            return False
        uid = next(iter(uids))
        if (
            not _UID.fullmatch(uid)
            or sha256(("panel-assistant-mdns-v1\0" + uid).encode()).hexdigest() != did
        ):
            return False
        panel_ids = {
            value
            for domain, value in device.identifiers
            if domain == "mqtt"
            and value.startswith("ha-paneld-")
            and not value.startswith(("ha-paneld-uid-", "ha-paneld-aid-"))
        }
        if panel_ids != {f"ha-paneld-{record.get('panel_id')}"}:
            return False
        for other in hass.config_entries.async_entries(DOMAIN):
            if other.entry_id == entry.entry_id:
                continue
            if other.entry_id in device.config_entries:
                return False
            other_record = other.data.get(CONF_CUTOVER, {})
            if isinstance(other_record, dict) and any(
                isinstance(item, dict) and item.get("mqtt_device_id") == device_id
                for item in other_record.get("entities", {}).values()
            ):
                return False
    return True


@callback
def can_confirm_identity(
    hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth
) -> bool:
    """Preflight identity adoption without changing any registry or entry."""
    if not migration_candidate(entry, health):
        return False
    did = health.discovery_id
    assert did is not None
    pending = entry.data.get(CONF_IDENTITY_PENDING)
    expected = {
        "did": did,
        "legacy_did": entry.unique_id,
        "address": entry.data[CONF_ADDRESS],
    }
    if pending is not None and pending != expected:
        return False
    if not identity_available(hass, entry, did):
        return False
    if not _native_identities(hass, entry) <= {entry.unique_id, did}:
        return False
    registry = er.async_get(hass)
    assert entry.unique_id is not None
    prefix = f"{entry.unique_id}_"
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.platform != DOMAIN or not item.unique_id.startswith(prefix):
            continue
        target = did + item.unique_id[len(entry.unique_id) :]
        holder = registry.async_get_entity_id(item.domain, DOMAIN, target)
        if holder is not None and holder != item.entity_id:
            return False
    return True


@callback
def confirm_identity(
    hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth
) -> bool:
    """Rekey owned entities in place, committing the entry identity last."""
    if not can_confirm_identity(hass, entry, health):
        return False
    did = health.discovery_id
    assert did is not None
    _commit_identity(hass, entry, did)
    return True


@callback
def adopt_moved_identity(
    hass: HomeAssistant, entry: ConfigEntry, did: str, *, reload: bool = True
) -> bool:
    """Rekey an entry to the identity of the app Panel Assistant moved it to.

    The move is this integration's own action: it backed the old app up,
    installed the new one and restored it, so the new identity is admitted on
    that evidence rather than on a legacy hint from the panel.
    """
    if (
        entry.unique_id is None
        or not is_valid_discovery_id(did)
        or did == entry.unique_id
        or not identity_available(hass, entry, did)
        or not _native_identities(hass, entry) <= {entry.unique_id, did}
    ):
        return False
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}_"
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.platform != DOMAIN or not item.unique_id.startswith(prefix):
            continue
        holder = registry.async_get_entity_id(
            item.domain, DOMAIN, did + item.unique_id[len(entry.unique_id) :]
        )
        if holder is not None and holder != item.entity_id:
            return False
    _commit_identity(hass, entry, did, reload=reload)
    return True


@callback
def _commit_identity(
    hass: HomeAssistant, entry: ConfigEntry, did: str, *, reload: bool = True
) -> None:
    """Rekey owned entities in place, committing the entry identity last."""
    assert entry.unique_id is not None
    registry = er.async_get(hass)
    changes = [
        (item.entity_id, did + item.unique_id[len(entry.unique_id) :])
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.platform == DOMAIN and item.unique_id.startswith(f"{entry.unique_id}_")
    ]
    from .transport import async_get_sessions

    async_get_sessions(hass).close_entry(entry.entry_id, "entry_unloaded")
    pending = {
        "did": did,
        "legacy_did": entry.unique_id,
        "address": entry.data[CONF_ADDRESS],
    }
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_IDENTITY_PENDING: pending}
    )
    for entity_id, target in changes:
        registry.async_update_entity(entity_id, new_unique_id=target)
    data = deepcopy(dict(entry.data))
    for key in (CONF_CUTOVER, CONF_SUPPORTED_CHANNELS):
        value = data.get(key)
        if isinstance(value, dict) and value.get("did") == entry.unique_id:
            value["did"] = did
    data[CONF_INSTALL_IDENTITY] = True
    data[CONF_PREVIOUS_IDENTITY] = entry.unique_id
    data.pop(CONF_IDENTITY_PENDING, None)
    hass.config_entries.async_update_entry(entry, unique_id=did, data=data)
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_IDENTITY}_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"panel_identity_mismatch_{entry.entry_id}")
    if reload:
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


@callback
def reconcile_identity(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Finish saved registry rekeys before entities or sessions can be loaded.

    Registry and config-entry storage save independently. Keeping the previous
    identity makes either disk-write order recoverable after a Core restart.
    """
    previous = entry.data.get(CONF_PREVIOUS_IDENTITY)
    if not isinstance(previous, str) or not is_installation(entry):
        # Entity and entry storage can also persist in the opposite order.
        # An unexplained native prefix must never create a second set under
        # the old identity; health can still offer its administrator repair.
        return _native_identities(hass, entry) <= {entry.unique_id}
    did = entry.unique_id
    if did is None or not identity_available(hass, entry, did):
        return False
    if not _native_identities(hass, entry) <= {previous, did}:
        return False
    registry = er.async_get(hass)
    changes = []
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.platform != DOMAIN or not item.unique_id.startswith(f"{previous}_"):
            continue
        target = did + item.unique_id[len(previous) :]
        holder = registry.async_get_entity_id(item.domain, DOMAIN, target)
        if holder is not None and holder != item.entity_id:
            return False
        changes.append((item.entity_id, target))
    for entity_id, target in changes:
        registry.async_update_entity(entity_id, new_unique_id=target)
    return True


@callback
def _reject_health(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Withdraw both inbound and outbound authority for a changed endpoint."""
    if (
        ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_IDENTITY}_{entry.entry_id}")
        is None
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"panel_identity_mismatch_{entry.entry_id}",
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="panel_identity_mismatch",
            translation_placeholders={
                "panel": entry.title,
                "address": entry.data[CONF_ADDRESS],
            },
        )
    _withdraw_authority(hass, entry)
    return False


@callback
def _withdraw_authority(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Stop status, controls and the session until the identity is settled."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from .transport import async_get_sessions

    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if coordinator is not None:
        coordinator.identity_mismatch = True
        coordinator.client.health_peer = None
        coordinator.async_set_update_error(UpdateFailed("Panel identity changed"))
    async_get_sessions(hass).close_entry(entry.entry_id, "entry_unloaded")


@callback
def _hold_self_moved(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Hold a panel already on the new app, without blaming its address.

    Authority is withdrawn exactly as for any changed identity, so nothing binds
    to the new identity on the panel's word alone. What differs is what the
    owner is told and what happens next: this is the panel they already have,
    on the new app, so the address Repair would be both wrong and unfixable.
    Panel Assistant proves the installed app over ADB and adopts the identity
    itself; the move Repair is the owner's fallback when it cannot reach the
    panel over ADB, and it now finishes an already-moved panel too.
    """
    from .panel_move import async_offer_move, async_schedule_self_move_adoption

    ir.async_delete_issue(hass, DOMAIN, f"panel_identity_mismatch_{entry.entry_id}")
    async_offer_move(hass, entry)
    _withdraw_authority(hass, entry)
    async_schedule_self_move_adoption(hass, entry)
    return False


@callback
def accept_health(hass: HomeAssistant, entry: ConfigEntry, health: PanelHealth) -> bool:
    """Reject a different panel before status or session state can change."""
    did = health.discovery_id
    if entry.unique_id is None:
        recorded = _native_identities(hass, entry)
        cutover = entry.data.get(CONF_CUTOVER)
        old = cutover.get("did") if isinstance(cutover, dict) else None
        if isinstance(old, str) and is_valid_discovery_id(old):
            recorded.add(old)
        if len(recorded) > 1:
            return _reject_health(hass, entry)
        if recorded:
            # Older address-created entries kept their identity only in owned
            # registry records. Preserve it before considering updated health.
            hass.config_entries.async_update_entry(
                entry, unique_id=next(iter(recorded))
            )
    pending = entry.data.get(CONF_IDENTITY_PENDING)
    if isinstance(pending, dict):
        if pending == {
            "did": did,
            "legacy_did": health.legacy_discovery_id,
            "address": entry.data[CONF_ADDRESS],
        } and confirm_identity(hass, entry, health):
            return True
        if pending.get("did") == did and self_moved(entry, health):
            # Core stopped part way through adopting this panel's move; the
            # adoption is proved again and finishes the rekey.
            return _hold_self_moved(hass, entry)
        return _reject_health(hass, entry)
    if entry.unique_id is None:
        if did is None:
            return True
        if not identity_available(hass, entry, did):
            return _reject_health(hass, entry)
        data = dict(entry.data)
        if health.installation_identity:
            data[CONF_INSTALL_IDENTITY] = True
        hass.config_entries.async_update_entry(entry, unique_id=did, data=data)
        return True
    if did == entry.unique_id:
        ir.async_delete_issue(hass, DOMAIN, f"panel_identity_mismatch_{entry.entry_id}")
        if health.installation_identity and not is_installation(entry):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_INSTALL_IDENTITY: True}
            )
        return True
    if migration_candidate(entry, health):
        assert did is not None
        if _prior_installation(hass, entry, did) and confirm_identity(
            hass, entry, health
        ):
            return True
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_IDENTITY}_{entry.entry_id}",
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_IDENTITY,
            translation_placeholders={
                "panel": entry.title,
                "address": entry.data[CONF_ADDRESS],
            },
            data={
                "entry_id": entry.entry_id,
                "did": did,
                "legacy_did": entry.unique_id,
                "address": entry.data[CONF_ADDRESS],
            },
        )
    if self_moved(entry, health):
        return _hold_self_moved(hass, entry)
    return _reject_health(hass, entry)


def legacy_peer_matches(entry: ConfigEntry, remote: str | None) -> bool:
    """Legacy identities cannot move an entry to another endpoint."""
    if not isinstance(remote, str):
        return False
    address = normalize_address(entry.data[CONF_ADDRESS])
    expected = address.host
    try:
        ip_address(expected)
    except ValueError:
        coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
        client = getattr(coordinator, "client", None)
        peer = getattr(client, "health_peer", None)
        snapshot = getattr(coordinator, "data", None)
        health = getattr(snapshot, "health", None)
        if (
            not isinstance(peer, tuple)
            or peer[0] != str(address.base_url.with_path("/api/v1/health"))
            or getattr(health, "discovery_id", None) != entry.unique_id
        ):
            return False
        expected = peer[1]
    try:
        actual_ip = ip_address(remote)
        expected_ip = ip_address(expected)
        return (getattr(actual_ip, "ipv4_mapped", None) or actual_ip) == (
            getattr(expected_ip, "ipv4_mapped", None) or expected_ip
        )
    except ValueError:
        return False

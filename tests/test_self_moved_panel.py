"""A panel already on the new app is adopted after proof, not blamed for its address."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import panel_move
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.feed_coordinator import StableReleaseCoordinator
from custom_components.panel_assistant.identity import CONF_IDENTITY_PENDING
from custom_components.panel_assistant.install_adb import (
    AdbInstallTarget,
    AdbPreflight,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    MoveObservation,
    _classify_target_packages,
)
from custom_components.panel_assistant.panel_move import (
    MoveError,
    async_move_to_new_app,
    move_issue_id,
)
from custom_components.panel_assistant.transport import async_get_sessions

from .test_feed_install_paths import _build, _install_feed
from .test_transport import STATUS

STORED = "panel.local"
OLD_APP_DID = "d" * 64  # the old app's installation identity, held by the entry
ORIGINAL_DID = "f" * 64  # the panel's first identity, stable across apps
NEW_APP_DID = "9" * 64  # the new app's installation identity
VERSION_CODE = 1095
SERIAL = "SAWD959D900AN"


def _health(
    *,
    did: str = NEW_APP_DID,
    legacy: str = ORIGINAL_DID,
    package: str = SUCCESSOR_PACKAGE_ID,
    version_code: int = VERSION_CODE,
) -> PanelHealth:
    return PanelHealth(
        version="0.9.7-rc4",
        panel_id="alpha",
        build="1000",
        config_hash="0bc578af",
        discovery_id=did,
        legacy_discovery_id=legacy,
        installation_identity=True,
        package=package,
        version_code=version_code,
    )


OLD_APP_HEALTH = _health(did=OLD_APP_DID, package=LEGACY_PACKAGE_ID)


class Network:
    """What the panel answers over HTTP and over ADB."""

    def __init__(self) -> None:
        self.health: Any = OLD_APP_HEALTH
        self.installed: tuple[str, ...] = (SUCCESSOR_PACKAGE_ID,)
        self.residue: frozenset[str] = frozenset()
        self.serial = SERIAL
        self.adb: Exception | None = None
        self.installed_size: int | None = 14
        self.preflight_error: InstallAdbError | None = None
        self.targets = 0

    async def get_health(self) -> PanelHealth:
        if isinstance(self.health, Exception):
            raise self.health
        return self.health

    async def target(self, *_args: Any) -> tuple[AdbInstallTarget, Any]:
        self.targets += 1
        if self.adb is not None:
            raise self.adb
        return (
            AdbInstallTarget(
                normalize_address("192.168.1.23"), self.serial, "X2i", "arm64-v8a", 33
            ),
            object.__new__(PythonRSASigner),
        )

    async def preflight(
        self, target: AdbInstallTarget, _signer: Any, descriptor: Any, **kwargs: Any
    ) -> AdbPreflight:
        if self.preflight_error is not None:
            raise self.preflight_error
        # The real admission rule decides what this panel holds.
        migration, installed = _classify_target_packages(
            descriptor.package_id,
            self.installed,
            self.residue,
            admit_installed_target=kwargs.get("admit_installed_target", False),
        )
        return AdbPreflight(
            serial=target.serial,
            model=target.model,
            primary_abi=target.primary_abi,
            android_sdk=target.android_sdk,
            root_mode=AdbRootMode.ROOTLESS,
            migration_candidate=migration,
            target_installed=installed,
        )

    async def measure(self, *_args: Any, **_kwargs: Any) -> int | None:
        return self.installed_size

    async def observe(self, *_args: Any, **_kwargs: Any) -> MoveObservation:
        # Once adopted, the panel is checked for an old app left beside it.
        return MoveObservation(
            LEGACY_PACKAGE_ID in self.installed,
            SUCCESSOR_PACKAGE_ID in self.installed,
            SUCCESSOR_PACKAGE_ID,
        )


@pytest.fixture
async def network(hass: HomeAssistant) -> AsyncGenerator[Network]:
    panel = Network()
    _install_feed(hass, _build(code=VERSION_CODE, package_id=SUCCESSOR_PACKAGE_ID))

    async def health(_client: HaPaneldClient) -> PanelHealth:
        return await panel.get_health()

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
        # The update entity's route check: the panel installs its own updates.
        patch.object(
            HaPaneldClient,
            "async_get_legacy_install_capability",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=SimpleNamespace(async_reconcile_entry=AsyncMock())),
        ),
        patch.object(
            StableReleaseCoordinator, "_async_update_data", AsyncMock(return_value=None)
        ),
        patch.object(panel_move, "_async_target", panel.target),
        patch.object(panel_move, "async_preflight_install", panel.preflight),
        patch.object(panel_move, "async_installed_artifact_size", panel.measure),
        patch.object(panel_move, "async_move_step", panel.observe),
    ):
        yield panel


async def _load(
    hass: HomeAssistant, user_id: str, address: str = STORED, **data: Any
) -> MockConfigEntry:
    """A panel entry on the old app's installation identity, with an entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=OLD_APP_DID,
        data={
            CONF_ADDRESS: address,
            "transport_user_id": user_id,
            "installation_identity": True,
            "previous_installation_identity": ORIGINAL_DID,
            **data,
        },
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{OLD_APP_DID}_wifi",
        config_entry=entry,
        suggested_object_id="alpha_wifi",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def _issues(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = ir.async_get(hass)
    return {
        name
        for name, issue_id in (
            ("address", f"panel_identity_mismatch_{entry.entry_id}"),
            ("confirmation", f"panel_identity_confirmation_{entry.entry_id}"),
            ("move", move_issue_id(entry.entry_id)),
        )
        if registry.async_get_issue(DOMAIN, issue_id) is not None
    }


def _write_record(
    hass: HomeAssistant, entry: MockConfigEntry, serial: str, **extra: Any
) -> Path:
    """The record a move of Panel Assistant's own left before adoption."""
    path = Path(hass.config.path(DOMAIN, "backups", f"{entry.entry_id}-move.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "receipt": "receipt.zip",
        "sha256": "0" * 64,
        "panel_id": "alpha",
        "config_hash": "70502055",
        "legacy_did": OLD_APP_DID,
        "serial": serial,
        **extra,
    }
    path.write_text(json.dumps(record))
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _entity_unique_id(hass: HomeAssistant) -> str | None:
    found = er.async_get(hass).async_get("sensor.alpha_wifi")
    return found.unique_id if found is not None else None


@pytest.fixture
def retry_now() -> Callable[[], Any]:
    return lambda: patch.object(panel_move, "_SELF_MOVE_RETRY_SECONDS", 0)


# --- the panel the owner already has ------------------------------------------


async def test_a_self_moved_panel_is_adopted_with_no_owner_action(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()

    await _poll(hass, entry)

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry is not None and entry.unique_id == NEW_APP_DID
    assert entry.data["previous_installation_identity"] == OLD_APP_DID
    # The same entity, under the new identity.
    assert _entity_unique_id(hass) == f"{NEW_APP_DID}_wifi"
    assert _issues(hass, entry) == set()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.last_update_success
    assert not entry.runtime_data.coordinator.identity_mismatch


async def test_without_adb_the_move_repair_is_offered_and_the_panel_held(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_AUTHORIZATION)

    await _poll(hass, entry)

    assert _issues(hass, entry) == {"move"}
    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"
    coordinator = entry.runtime_data.coordinator
    assert coordinator.identity_mismatch and not coordinator.last_update_success
    assert async_get_sessions(hass).get(entry.entry_id) is None


async def test_a_held_panel_after_a_restart_is_offered_the_move(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Setup's first poll is already held: nothing accepted describes it."""
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=OLD_APP_DID,
        data={
            CONF_ADDRESS: STORED,
            "transport_user_id": hass_read_only_user.id,
            "installation_identity": True,
            "previous_installation_identity": ORIGINAL_DID,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _issues(hass, entry) == {"move"}
    assert entry.unique_id == OLD_APP_DID


async def test_adb_attempts_are_throttled(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, retry_now: Any
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)

    await _poll(hass, entry)
    await _poll(hass, entry)
    assert network.targets == 1

    with retry_now():
        await _poll(hass, entry)
    assert network.targets == 2


async def test_an_offline_poll_attempts_nothing_and_changes_no_repair(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, retry_now: Any
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)
    await _poll(hass, entry)
    before = (_issues(hass, entry), network.targets)

    network.health = CannotConnectError()
    with retry_now():
        await _poll(hass, entry)

    assert (_issues(hass, entry), network.targets) == before
    assert entry.unique_id == OLD_APP_DID


async def test_a_move_the_repair_left_unfinished_settles_its_record(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """The bench case: the Repair's last step lost ADB after the old app went."""
    entry = await _load(hass, hass_read_only_user.id)
    path = _write_record(hass, entry, SERIAL)
    network.health = _health()

    await _poll(hass, entry)

    assert entry.unique_id == NEW_APP_DID
    assert _read(path).get("new_did") == NEW_APP_DID
    assert _issues(hass, entry) == set()


async def test_an_adoption_interrupted_by_a_restart_finishes(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Core stopped after the pending rekey was saved, before the identity was."""
    pending = {"did": NEW_APP_DID, "legacy_did": OLD_APP_DID, "address": STORED}
    entry = await _load(hass, hass_read_only_user.id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_IDENTITY_PENDING: pending}
    )
    network.health = _health()

    await _poll(hass, entry)

    assert entry.unique_id == NEW_APP_DID
    assert CONF_IDENTITY_PENDING not in entry.data
    assert _entity_unique_id(hass) == f"{NEW_APP_DID}_wifi"
    assert _issues(hass, entry) == set()


# --- the move Repair on a panel that is already moved ---------------------------


async def test_pressing_the_move_repair_adopts_an_already_moved_panel(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)
    await _poll(hass, entry)
    assert entry.runtime_data.coordinator.identity_mismatch

    network.adb = None
    await async_move_to_new_app(hass, entry)
    await hass.async_block_till_done()

    assert entry.unique_id == NEW_APP_DID
    assert _issues(hass, entry) == set()


@pytest.mark.parametrize(
    ("adb", "reason"),
    [
        (MoveError(panel_move.REASON_ADB_AUTHORIZATION), "adb_authorization"),
        (MoveError(panel_move.REASON_ADB_UNREACHABLE), "adb_unreachable"),
    ],
)
async def test_the_move_repair_says_why_adb_stops_it(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    network: Network,
    adb: MoveError,
    reason: str,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.adb = adb
    await _poll(hass, entry)

    with pytest.raises(MoveError) as error:
        await async_move_to_new_app(hass, entry)

    assert error.value.reason == reason
    assert entry.unique_id == OLD_APP_DID


# --- what proof refuses ---------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["wrong size", "wrong version code", "old app still present", "serial changed"],
)
async def test_an_unproven_install_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, case: str
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    if case == "wrong size":
        # Different bytes from the signed release measure as no match.
        network.installed_size = None
    elif case == "wrong version code":
        network.health = _health(version_code=VERSION_CODE + 1)
    elif case == "old app still present":
        network.installed = (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID)
    else:
        network.preflight_error = InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"
    assert _issues(hass, entry) == {"move"}


async def test_a_different_panel_at_the_address_is_still_refused(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Its legacy identity links it to nothing this entry ever held."""
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health(legacy="a" * 64)

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"
    # The move Repair it already had for the old app stays; the address one is new.
    assert _issues(hass, entry) == {"address", "move"}
    assert network.targets == 0


async def test_an_install_that_is_not_the_signed_release_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.installed_size = 15

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"


async def test_a_linked_panel_still_on_the_old_app_is_not_self_moved(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health(package=LEGACY_PACKAGE_ID)

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert "address" in _issues(hass, entry)
    assert network.targets == 0


async def test_a_panel_without_the_new_app_installed_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Only the old app is there: the new identity cannot be its own."""
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    network.installed = (LEGACY_PACKAGE_ID,)

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"


async def test_an_identity_another_entry_owns_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id=NEW_APP_DID, data={CONF_ADDRESS: "other.local"}
    ).add_to_hass(hass)
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert _entity_unique_id(hass) == f"{OLD_APP_DID}_wifi"


async def test_a_stale_address_repair_is_withdrawn_by_the_hold(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health(legacy="a" * 64)
    await _poll(hass, entry)
    assert "address" in _issues(hass, entry)
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)

    network.health = _health()
    await _poll(hass, entry)

    assert _issues(hass, entry) == {"move"}


async def test_a_panel_on_its_original_identity_keeps_the_confirmation_path(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """The first identity change is confirmed by its own evidence or the owner."""
    network.health = replace(
        _health(did=ORIGINAL_DID, package=LEGACY_PACKAGE_ID),
        installation_identity=False,
        legacy_discovery_id=None,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=ORIGINAL_DID,
        data={CONF_ADDRESS: STORED, "transport_user_id": hass_read_only_user.id},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    network.health = _health()
    await _poll(hass, entry)

    assert entry.unique_id == ORIGINAL_DID
    assert "confirmation" in _issues(hass, entry)
    assert network.targets == 0


async def test_a_record_from_another_device_is_not_adopted(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    path = _write_record(hass, entry, "ANOTHER")
    network.health = _health()

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert "new_did" not in _read(path)


async def test_rediscovery_of_a_held_panel_closes_its_session(
    hass: HomeAssistant,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
    network: Network,
) -> None:
    """Discovery is a second way the panel's answer arrives; it holds the same."""
    from dataclasses import replace as replace_info

    from homeassistant import config_entries

    from .test_config_flow import _zeroconf_info
    from .test_transport import _hello, _receive, _send

    entry = await _load(hass, hass_read_only_user.id, address="192.168.1.23")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    hello = _hello(did=OLD_APP_DID, protocol={"min": 3, "max": 3})
    assert (await _send(client, hello))["success"]
    network.health = _health()
    network.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)
    info = _zeroconf_info()
    info = replace_info(info, properties={**info.properties, "did": NEW_APP_DID})

    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.unique_id == OLD_APP_DID
    assert entry.runtime_data.coordinator.identity_mismatch
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert (await _receive(client))["event"]["kind"] == "session_closed"
    assert _issues(hass, entry) == {"move"}


async def test_a_panel_held_during_setup_is_adopted_on_the_next_poll(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Setup's first poll runs before the entry's runtime exists."""
    network.health = _health()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=OLD_APP_DID,
        data={
            CONF_ADDRESS: STORED,
            "transport_user_id": hass_read_only_user.id,
            "installation_identity": True,
            "previous_installation_identity": ORIGINAL_DID,
        },
    )
    entry.add_to_hass(hass)

    async def install_status(_client: HaPaneldClient) -> None:
        # Setup reads the panel again after its first poll; a real panel takes
        # a moment to answer, which is when an adoption scheduled by that poll
        # starts.
        await asyncio.sleep(0.2)
        raise CannotConnectError

    with patch.object(HaPaneldClient, "async_get_panel_install_status", install_status):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    await _poll(hass, entry)

    assert entry.unique_id == NEW_APP_DID
    assert _issues(hass, entry) == set()


class _UntrustedDevice:
    """An adbd that rejects Panel Assistant's signature, as after a revoke."""

    offered: ClassVar[list[bool]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def connect(self, *, auth_callback: Any = None, **_kwargs: Any) -> bool:
        if auth_callback is not None:
            auth_callback(self)
        # The library sends the public key here, and the panel shows a prompt.
        _UntrustedDevice.offered.append(True)
        return False

    async def close(self) -> None:
        return None


@pytest.fixture
def untrusted() -> Any:
    from custom_components.panel_assistant import install_adb

    _UntrustedDevice.offered = []
    with (
        patch.object(install_adb, "AdbDeviceAsync", _UntrustedDevice),
        patch.object(
            panel_move, "async_preflight_install", install_adb.async_preflight_install
        ),
    ):
        yield _UntrustedDevice.offered


async def test_the_background_attempt_never_asks_the_panel_to_trust_a_key(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, untrusted: Any
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()

    await _poll(hass, entry)

    assert untrusted == []
    assert entry.unique_id == OLD_APP_DID
    assert _issues(hass, entry) == {"move"}


async def test_the_owners_repair_may_ask_the_panel_to_trust_the_key(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network, untrusted: Any
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    await _poll(hass, entry)

    with pytest.raises(MoveError) as error:
        await async_move_to_new_app(hass, entry)

    assert error.value.reason == "adb_authorization"
    assert untrusted == [True]


def _write_marker(hass: HomeAssistant, entry_id: str) -> Path:
    path = Path(hass.config.path(DOMAIN, "backups", f"{entry_id}-adopted.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"legacy_did": OLD_APP_DID, "new_did": NEW_APP_DID}))
    return path


async def _setup_after_partial_rekey(
    hass: HomeAssistant, user_id: str, *, marker: bool
) -> MockConfigEntry:
    """Core stopped after the registry saved the rekey, before the entry did."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=OLD_APP_DID,
        data={
            CONF_ADDRESS: STORED,
            "transport_user_id": user_id,
            "installation_identity": True,
            "previous_installation_identity": ORIGINAL_DID,
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{NEW_APP_DID}_wifi",
        config_entry=entry,
        suggested_object_id="alpha_wifi",
    )
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{OLD_APP_DID}_light",
        config_entry=entry,
        suggested_object_id="alpha_light",
    )
    if marker:
        await hass.async_add_executor_job(_write_marker, hass, entry.entry_id)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_setup_finishes_an_adoption_whose_registry_saved_first(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    network.health = _health()

    entry = await _setup_after_partial_rekey(hass, hass_read_only_user.id, marker=True)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == NEW_APP_DID
    found = er.async_get(hass).async_get("sensor.alpha_light")
    assert found is not None and found.unique_id == f"{NEW_APP_DID}_light"
    assert _entity_unique_id(hass) == f"{NEW_APP_DID}_wifi"


async def test_without_its_marker_a_mixed_registry_is_still_refused(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """New-identity entities alone never let an entry take that identity."""
    network.health = _health()

    entry = await _setup_after_partial_rekey(hass, hass_read_only_user.id, marker=False)

    assert entry.state is not ConfigEntryState.LOADED
    assert entry.unique_id == OLD_APP_DID


async def test_an_adoption_marker_is_kept_until_the_identity_is_saved(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    from custom_components.panel_assistant.panel_move import async_restore_move_offer

    from .test_panel_move import _save_entry

    entry = await _load(hass, hass_read_only_user.id)
    network.health = _health()
    await _poll(hass, entry)
    path = Path(hass.config.path(DOMAIN, "backups", f"{entry.entry_id}-adopted.json"))
    assert await hass.async_add_executor_job(_read, path) == {
        "legacy_did": OLD_APP_DID,
        "new_did": NEW_APP_DID,
    }
    storage = Path(hass.config.path(".storage", "core.config_entries"))

    await hass.async_add_executor_job(_save_entry, storage, entry.entry_id, OLD_APP_DID)
    await async_restore_move_offer(hass, entry)
    assert await hass.async_add_executor_job(path.exists)

    await hass.async_add_executor_job(_save_entry, storage, entry.entry_id, NEW_APP_DID)
    await async_restore_move_offer(hass, entry)
    assert not await hass.async_add_executor_job(path.exists)


async def test_a_move_that_kept_the_old_app_finishes_itself(
    hass: HomeAssistant, hass_read_only_user: Any, network: Network
) -> None:
    """Only that move can bring the old app back from its copy, or remove it."""
    entry = await _load(hass, hass_read_only_user.id)
    path = await hass.async_add_executor_job(
        lambda: _write_record(hass, entry, SERIAL, legacy_copy="0" * 64)
    )
    network.health = _health()

    await _poll(hass, entry)

    assert entry.unique_id == OLD_APP_DID
    assert network.targets == 0
    assert "new_did" not in await hass.async_add_executor_job(_read, path)

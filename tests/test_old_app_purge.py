"""An old app left beside the new one is removed once the new app holds the panel."""

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import install_adb, old_app, panel_move
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import BuildDownloadError
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.feed_coordinator import (
    StableReleaseCoordinator,
    async_get_stable_release_coordinator,
)
from custom_components.panel_assistant.install_adb import (
    AdbInstallTarget,
    InstallAdbError,
    InstallAdbErrorCode,
    MoveObservation,
    MoveStep,
)
from custom_components.panel_assistant.old_app import old_app_issue_id
from custom_components.panel_assistant.panel_move import MoveError, move_issue_id
from custom_components.panel_assistant.release import ReleaseArtifact

from .test_transport import STATUS

NEW_DID = "9" * 64
ORIGINAL_DID = "f" * 64
SERIAL = "PANEL1"

HEALTH = PanelHealth(
    version="0.9.9-rc5",
    panel_id="landing",
    build="1000",
    config_hash="91e3fbfc",
    discovery_id=NEW_DID,
    legacy_discovery_id=ORIGINAL_DID,
    installation_identity=True,
    package=SUCCESSOR_PACKAGE_ID,
    version_code=1102,
)


class Panel:
    """A panel with the new app running and, perhaps, the old app beside it."""

    def __init__(self) -> None:
        self.health: Any = HEALTH
        self.legacy = True
        #: Uninstalled for the panel's user with its data kept.
        self.aside = False
        self.successor = True
        self.home: str | None = SUCCESSOR_PACKAGE_ID
        self.adb: Exception | None = None
        self.retire_fails: Exception | None = None
        self.backup_fails = False
        #: Where HOME points once the backup has been taken, if it moves.
        self.home_after_backup: str | None = None
        self.events: list[str] = []
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
                normalize_address("192.168.1.30"), SERIAL, "NSPanel", "arm64-v8a", 27
            ),
            object.__new__(PythonRSASigner),
        )

    def observe(self) -> MoveObservation:
        return MoveObservation(
            self.legacy, self.successor, self.home, legacy_set_aside=self.aside
        )

    async def step(
        self, _target: Any, _signer: Any, step: MoveStep, **_kwargs: Any
    ) -> MoveObservation:
        self.events.append(step.value)
        if step is MoveStep.RETIRE_LEGACY:
            assert self.home != LEGACY_PACKAGE_ID, "launcher stranded"
            assert "backup" in self.events, "removed before the backup"
            # The uninstall runs on the panel even when the connection drops.
            self.legacy = self.aside = False
            if self.retire_fails is not None:
                failure, self.retire_fails = self.retire_fails, None
                raise failure
        else:
            assert step is MoveStep.OBSERVE, f"unexpected step {step}"
        return self.observe()

    async def backup(self) -> bytes:
        if self.backup_fails:
            raise CannotConnectError
        return b"archive"

    async def store(self, _hass: Any, _entry_id: str, _code: Any, data: bytes) -> Any:
        assert data == b"archive"
        self.events.append("backup")
        if self.home_after_backup is not None:
            self.home = self.home_after_backup
        return SimpleNamespace(path=Path("backup.zip"), sha256="0" * 64)


@pytest.fixture
async def panel(hass: HomeAssistant) -> AsyncGenerator[Panel]:
    fake = Panel()

    async def health(_client: HaPaneldClient) -> PanelHealth:
        return await fake.get_health()

    async def backup(_client: HaPaneldClient) -> bytes:
        return await fake.backup()

    with (
        patch.object(HaPaneldClient, "async_get_health", health),
        patch.object(
            HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
        ),
        patch.object(HaPaneldClient, "async_backup_panel", backup),
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
        patch.object(panel_move, "_async_target", fake.target),
        patch.object(panel_move, "async_move_step", fake.step),
        patch.object(old_app, "async_store_panel_backup", fake.store),
    ):
        yield fake


async def _load(
    hass: HomeAssistant, user_id: str, *, wait_background_tasks: bool = True
) -> MockConfigEntry:
    """A panel entry already on the new app's identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="landing",
        unique_id=NEW_DID,
        data={
            CONF_ADDRESS: "panel.local",
            "transport_user_id": user_id,
            "installation_identity": True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=wait_background_tasks)
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, old_app_issue_id(entry.entry_id))
    return issue.translation_key if issue is not None else None


def _retry_now() -> Any:
    return patch.object(old_app, "_OLD_APP_RETRY_SECONDS", 0)


@contextmanager
def _offered_update(hass: HomeAssistant, panel: Panel) -> Iterator[None]:
    """An authenticated offer the panel downloads when HA cannot fetch it."""
    release = ReleaseArtifact(
        "v0.9.10",
        "0.9.10",
        "app.apk",
        "https://github.com/panel-assistant/android/releases/download/v0.9.10/app.apk",
        "a" * 64,
        descriptor=SimpleNamespace(package_id=SUCCESSOR_PACKAGE_ID, version_code=1103),
        protocol_min=3,
        protocol_max=3,
    )
    catalogue = async_get_stable_release_coordinator(hass)
    catalogue._candidates[release.tag, SUCCESSOR_PACKAGE_ID] = release

    async def start(_client: HaPaneldClient, tag: str) -> None:
        assert tag == release.tag
        panel.events.append("update")
        panel.health = replace(
            HEALTH, version=release.version, build="1001", version_code=1103
        )

    with (
        patch.object(HaPaneldClient, "async_start_panel_update", start),
        patch.object(
            HaPaneldClient,
            "async_get_status",
            AsyncMock(
                return_value=replace(
                    STATUS,
                    home_ui={
                        "state": "ready",
                        "reason": "dashboard",
                        "evidence": "foreground",
                    },
                )
            ),
        ),
        patch.object(panel_update, "async_store_panel_backup", panel.store),
        patch.object(
            panel_update,
            "async_download_build",
            AsyncMock(side_effect=BuildDownloadError),
        ),
    ):
        yield


async def _install_update(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "update", DOMAIN, f"{entry.entry_id}_update"
    )
    assert entity_id is not None
    await hass.services.async_call(
        "update", "install", {"entity_id": entity_id}, blocking=True
    )


@contextmanager
def _blocked_observation(panel: Panel) -> Iterator[tuple[asyncio.Event, asyncio.Event]]:
    observing, observed = asyncio.Event(), asyncio.Event()

    async def step(*args: Any, **kwargs: Any) -> MoveObservation:
        if args[2] is MoveStep.OBSERVE:
            observing.set()
            await observed.wait()
        return await panel.step(*args, **kwargs)

    with patch.object(panel_move, "async_move_step", step):
        yield observing, observed


async def test_update_immediately_after_adding_succeeds_while_old_app_is_observed(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy = False
    with (
        _offered_update(hass, panel),
        _blocked_observation(panel) as (
            observing,
            observed,
        ),
    ):
        try:
            entry = await _load(
                hass, hass_read_only_user.id, wait_background_tasks=False
            )
            async with asyncio.timeout(5):
                await observing.wait()
                await _install_update(hass, entry)
            assert panel.health.version == "0.9.10"
            assert panel.events == ["backup", "update"]
        finally:
            observed.set()
            await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_old_app_is_backed_up_around_and_removed_with_no_owner_action(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE", "backup", "RETIRE_LEGACY"]
    assert not panel.legacy and panel.successor
    assert _issue(hass, entry) is None
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, move_issue_id(entry.entry_id))
        is None
    )


async def test_an_old_app_set_aside_with_its_data_is_removed_too(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy, panel.aside = False, True
    entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE", "backup", "RETIRE_LEGACY"]
    assert not panel.aside
    assert _issue(hass, entry) is None


async def test_a_panel_seen_clean_is_not_checked_again_until_its_build_changes(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy = False
    entry = await _load(hass, hass_read_only_user.id)
    assert panel.targets == 1

    with _retry_now():
        await _poll(hass, entry)
    assert panel.targets == 1

    panel.health = replace(HEALTH, version_code=1103)
    await _poll(hass, entry)
    assert panel.targets == 2
    assert panel.events == ["OBSERVE", "OBSERVE"]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (
            lambda panel: setattr(panel, "home", LEGACY_PACKAGE_ID),
            "home is the old app",
        ),
        (lambda panel: setattr(panel, "home", None), "nothing single is home"),
        (
            lambda panel: setattr(panel, "health", CannotConnectError()),
            "the new app stops answering",
        ),
        (
            lambda panel: setattr(
                panel, "health", replace(HEALTH, package=LEGACY_PACKAGE_ID)
            ),
            "the old app answers",
        ),
    ],
)
async def test_when_the_proof_fails_nothing_is_removed_and_a_repair_says_why(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    panel: Panel,
    change: Any,
    why: str,
) -> None:
    panel.legacy = False
    entry = await _load(hass, hass_read_only_user.id)
    panel.legacy = True
    panel.events.clear()

    # Between the poll that schedules the check and the check itself.
    original = panel.target

    async def target(*args: Any) -> Any:
        change(panel)
        return await original(*args)

    with patch.object(panel_move, "_async_target", target):
        panel.health = replace(HEALTH, version_code=1103)
        await _poll(hass, entry)

    assert panel.events == ["OBSERVE"], why
    assert panel.legacy
    assert _issue(hass, entry) == "remove_old_app_unproven", why


async def test_without_adb_an_unseen_panel_gets_no_repair(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.adb = MoveError(panel_move.REASON_ADB_UNREACHABLE)
    entry = await _load(hass, hass_read_only_user.id)

    assert panel.targets == 1
    assert panel.events == []
    assert _issue(hass, entry) is None


async def test_without_adb_a_seen_old_app_gets_a_repair_naming_adb(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    # The first check sees the old app; its backup cannot be taken.
    panel.backup_fails = True
    entry = await _load(hass, hass_read_only_user.id)
    assert _issue(hass, entry) == "remove_old_app_failed"
    assert panel.legacy

    # Then ADB stops answering: the Repair says so.
    panel.backup_fails = False
    panel.adb = MoveError(panel_move.REASON_ADB_AUTHORIZATION)
    with _retry_now():
        await _poll(hass, entry)
    assert _issue(hass, entry) == "remove_old_app_adb"
    assert panel.legacy

    # And once ADB is back the old app goes and the Repair with it.
    panel.adb = None
    with _retry_now():
        await _poll(hass, entry)
    assert not panel.legacy
    assert _issue(hass, entry) is None


async def test_a_refused_key_while_removing_names_adb(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.retire_fails = InstallAdbError(InstallAdbErrorCode.AUTHORIZATION_REQUIRED)
    entry = await _load(hass, hass_read_only_user.id)
    assert _issue(hass, entry) == "remove_old_app_adb"


async def test_a_removal_that_dropped_its_connection_ends_clean_on_the_next_check(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.retire_fails = InstallAdbError(InstallAdbErrorCode.INSTALL_AMBIGUOUS)
    entry = await _load(hass, hass_read_only_user.id)
    assert _issue(hass, entry) == "remove_old_app_failed"

    panel.events.clear()
    with _retry_now():
        await _poll(hass, entry)
    assert panel.events == ["OBSERVE"]
    assert _issue(hass, entry) is None


async def test_a_backup_that_fails_removes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.backup_fails = True
    entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE"]
    assert panel.legacy
    assert _issue(hass, entry) == "remove_old_app_failed"


async def test_a_panel_the_old_app_answers_keeps_the_move_repair(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.health = replace(HEALTH, package=LEGACY_PACKAGE_ID, version_code=1096)
    entry = await _load(hass, hass_read_only_user.id)

    assert panel.targets == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, move_issue_id(entry.entry_id))
    assert _issue(hass, entry) is None


def _write_move_record(hass: HomeAssistant, entry_id: str) -> None:
    """The record a move of Panel Assistant's own keeps until it finishes."""
    path = Path(hass.config.path(DOMAIN, "backups", f"{entry_id}-move.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = ("receipt", "sha256", "panel_id", "config_hash", "legacy_did", "serial")
    path.write_text(json.dumps(dict.fromkeys(keys, "x")))


async def test_a_move_of_its_own_keeps_the_old_app_to_itself(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    _write_move_record(hass, "moving")
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="moving",
        title="landing",
        unique_id=NEW_DID,
        data={
            CONF_ADDRESS: "panel.local",
            "transport_user_id": hass_read_only_user.id,
            "installation_identity": True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert panel.targets == 0
    assert panel.legacy
    assert _issue(hass, entry) is None


async def test_a_claimed_move_refuses_the_update_service(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy = False
    with _offered_update(hass, panel):
        entry = await _load(hass, hass_read_only_user.id)
        assert panel_move.claim_panel_operation(hass, entry.entry_id)
        try:
            with pytest.raises(HomeAssistantError) as error:
                await _install_update(hass, entry)
            assert error.value.translation_key == "update_busy"
            assert "update" not in panel.events
        finally:
            panel_move.release_panel_operation(hass, entry.entry_id)

        await _install_update(hass, entry)
        assert panel.events.count("update") == 1


async def test_an_update_claim_refuses_a_second_update_service_call(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy = False
    checking, checked = asyncio.Event(), asyncio.Event()

    async def capability(_client: HaPaneldClient) -> bool:
        checking.set()
        await checked.wait()
        return True

    with _offered_update(hass, panel):
        entry = await _load(hass, hass_read_only_user.id)
        with patch.object(
            HaPaneldClient, "async_get_legacy_install_capability", capability
        ):
            updating = asyncio.create_task(_install_update(hass, entry))
            try:
                async with asyncio.timeout(5):
                    await checking.wait()
                    with pytest.raises(HomeAssistantError) as error:
                        await _install_update(hass, entry)
                    assert error.value.translation_key == "update_busy"
                    assert "update" not in panel.events
                    checked.set()
                    await updating
                assert panel.events.count("update") == 1
            finally:
                checked.set()
                await asyncio.gather(updating, return_exceptions=True)
                await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_claim_won_during_observation_keeps_the_old_app_and_retries_next_poll(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    with (
        _offered_update(hass, panel),
        _blocked_observation(panel) as (
            observing,
            observed,
        ),
    ):
        try:
            entry = await _load(
                hass, hass_read_only_user.id, wait_background_tasks=False
            )
            async with asyncio.timeout(5):
                await observing.wait()
            assert panel_move.claim_panel_operation(hass, entry.entry_id)
            try:
                observed.set()
                await hass.async_block_till_done(wait_background_tasks=True)
                assert panel.events == ["OBSERVE"]
                assert panel.legacy
                with pytest.raises(HomeAssistantError) as error:
                    await _install_update(hass, entry)
                assert error.value.translation_key == "update_busy"
            finally:
                panel_move.release_panel_operation(hass, entry.entry_id)

            await _poll(hass, entry)
            assert panel.events == ["OBSERVE", "OBSERVE", "backup", "RETIRE_LEGACY"]
            assert not panel.legacy
        finally:
            observed.set()
            await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_move_started_during_observation_keeps_its_old_app(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    with _blocked_observation(panel) as (observing, observed):
        try:
            entry = await _load(
                hass, hass_read_only_user.id, wait_background_tasks=False
            )
            async with asyncio.timeout(5):
                await observing.wait()
            assert panel_move.claim_panel_operation(hass, entry.entry_id)
            try:
                hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        panel_move.CONF_SUCCESSOR_MOVE: {"panel_id": HEALTH.panel_id},
                    },
                )
            finally:
                panel_move.release_panel_operation(hass, entry.entry_id)
            observed.set()
            await hass.async_block_till_done(wait_background_tasks=True)
            assert panel.events == ["OBSERVE"]
            assert panel.legacy
        finally:
            observed.set()
            await hass.async_block_till_done(wait_background_tasks=True)


async def test_an_older_observation_does_not_restart_the_pending_new_build_check(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    panel.legacy = False
    first_started, first_finished = asyncio.Event(), asyncio.Event()
    newer_started = asyncio.Event()
    first_release, newer_release = asyncio.Event(), asyncio.Event()

    async def step(*args: Any, **kwargs: Any) -> MoveObservation:
        first = panel.targets == 1
        (first_started if first else newer_started).set()
        await (first_release if first else newer_release).wait()
        result = await panel.step(*args, **kwargs)
        if first:
            first_finished.set()
        return result

    with patch.object(panel_move, "async_move_step", step):
        try:
            entry = await _load(
                hass, hass_read_only_user.id, wait_background_tasks=False
            )
            async with asyncio.timeout(5):
                await first_started.wait()
                panel.health = replace(HEALTH, version_code=1103)
                await entry.runtime_data.coordinator.async_refresh()
                await newer_started.wait()
                first_release.set()
                await first_finished.wait()
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done(wait_background_tasks=False)
            assert panel.targets == 2
        finally:
            first_release.set()
            newer_release.set()
            await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize("cancel", [False, True])
async def test_update_is_refused_during_old_app_backup_and_the_claim_is_released(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel, cancel: bool
) -> None:
    backing_up, backed_up = asyncio.Event(), asyncio.Event()

    async def backup() -> bytes:
        backing_up.set()
        await backed_up.wait()
        return b"archive"

    with _offered_update(hass, panel), patch.object(panel, "backup", backup):
        try:
            entry = await _load(
                hass, hass_read_only_user.id, wait_background_tasks=False
            )
            async with asyncio.timeout(5):
                await backing_up.wait()
                with pytest.raises(HomeAssistantError) as error:
                    await _install_update(hass, entry)
                assert error.value.translation_key == "update_busy"
                assert panel.events == ["OBSERVE"]
                assert panel.legacy
                if cancel:
                    assert await hass.config_entries.async_unload(entry.entry_id)
                    assert panel.legacy
                    assert panel.events == ["OBSERVE"]
                else:
                    backed_up.set()
                    await hass.async_block_till_done(wait_background_tasks=True)
                    assert not panel.legacy
                    await _install_update(hass, entry)
                    assert panel.events.count("update") == 1
            assert panel_move.claim_panel_operation(hass, entry.entry_id)
            panel_move.release_panel_operation(hass, entry.entry_id)
        finally:
            backed_up.set()
            await hass.async_block_till_done(wait_background_tasks=True)


# --- the panel's own ADB, under the move step ------------------------------------


class _Adbd:
    """A panel's adbd that stops trusting Panel Assistant's key on one connection.

    The connections before it are trusted; on it, adbd asks for the public key,
    which the library sends straight after the callback, putting "Allow USB
    debugging?" on the panel.
    """

    revoke_at = 0
    connections = 0
    offered: ClassVar[list[int]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def connect(self, *, auth_callback: Any = None, **_kwargs: Any) -> bool:
        _Adbd.connections += 1
        if _Adbd.connections == _Adbd.revoke_at:
            if auth_callback is not None:
                auth_callback(self)
            _Adbd.offered.append(_Adbd.connections)
            return False
        return True

    async def close(self) -> None:
        return None


#: The panel's package manager, HOME and activity manager, as shell commands
#: reading and writing ``$STATE``, so the move step's real shell runs on them.
_PANEL_TOOLS = {
    "pm": """#!/bin/sh
case "$1" in
  path) [ -f "$STATE/$2" ] && echo "package:/data/app/$2/base.apk" ;;
  uninstall) for a; do p=$a; done; rm -f "$STATE/$p" "$STATE/$p.aside" ;;
  list) for a; do p=$a; done
        { [ -f "$STATE/$p" ] || [ -f "$STATE/$p.aside" ]; } && echo "package:$p" ;;
esac
exit 0
""",
    "cmd": """#!/bin/sh
[ "$2" = resolve-activity ] && cat "$STATE/home"
exit 0
""",
    "am": "#!/bin/sh\nexit 0\n",
    "dumpsys": "#!/bin/sh\nexit 0\n",
}


@contextmanager
def _real_move_steps(panel: Panel, revoke_at: int) -> Iterator[list[int]]:
    """Run the real move step and its real shell against ``panel``.

    Only the transport is replaced: adbd trusts the key except on connection
    ``revoke_at``, and each shell command runs in ``sh`` over stand-ins for the
    panel's package manager and HOME.
    """
    nonce = "0" * 32
    _Adbd.revoke_at, _Adbd.connections, _Adbd.offered = revoke_at, 0, []
    root = Path(tempfile.mkdtemp(prefix="panel-shell-"))
    tools, state = root / "bin", root / "state"
    tools.mkdir()
    state.mkdir()
    for name, script in _PANEL_TOOLS.items():
        (tools / name).write_text(script)
        (tools / name).chmod(0o755)

    async def shell(_device: Any, command: str, **_kwargs: Any) -> bytes:
        if "HAPANELD_MOVE_BEGIN" not in command:
            return b""  # the device proof, which _parse_identity_root accepts
        retire = f"pm uninstall {LEGACY_PACKAGE_ID}" in command
        if retire:
            assert "backup" in panel.events, "removed before the backup"
        panel.events.append("RETIRE_LEGACY" if retire else "OBSERVE")
        for package, present in (
            (LEGACY_PACKAGE_ID, panel.legacy),
            (f"{LEGACY_PACKAGE_ID}.aside", panel.aside),
            (SUCCESSOR_PACKAGE_ID, panel.successor),
        ):
            (state / package).unlink(missing_ok=True)
            if present:
                (state / package).touch()
        home = panel.home or ""
        if home and "/" not in home and "." in home:
            home = f"{home}/.DashboardActivity"
        (state / "home").write_text(f"{home}\n")
        process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "STATE": str(state),
                "PATH": f"{tools}:{os.environ['PATH']}",
            },
        )
        stdout, _ = await process.communicate()
        panel.legacy = (state / LEGACY_PACKAGE_ID).exists()
        panel.aside = (state / f"{LEGACY_PACKAGE_ID}.aside").exists()
        panel.successor = (state / SUCCESSOR_PACKAGE_ID).exists()
        return stdout

    try:
        with (
            patch.object(panel_move, "async_move_step", install_adb.async_move_step),
            patch.object(install_adb, "AdbDeviceAsync", _Adbd),
            patch.object(install_adb, "_async_shell", shell),
            patch.object(install_adb, "_parse_identity_root", lambda *_args: None),
            patch.object(install_adb, "_validate_target", lambda _target: None),
            patch.object(install_adb, "token_hex", lambda _size: nonce),
        ):
            yield _Adbd.offered
    finally:
        shutil.rmtree(root)


async def test_the_real_move_step_removes_the_old_app(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    """The seam the other tests replace, run for real once."""
    with _real_move_steps(panel, revoke_at=0) as offered:
        entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE", "backup", "RETIRE_LEGACY"]
    assert not panel.legacy
    assert offered == []
    assert _issue(hass, entry) is None


async def test_trust_revoked_before_the_look_offers_no_key(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    # An earlier check saw the old app and could not back the panel up.
    panel.backup_fails = True
    entry = await _load(hass, hass_read_only_user.id)
    assert _issue(hass, entry) == "remove_old_app_failed"
    panel.backup_fails = False
    panel.events.clear()

    # The probe still trusts the key; adbd refuses it when OBSERVE connects.
    with _real_move_steps(panel, revoke_at=1) as offered, _retry_now():
        await _poll(hass, entry)

    assert offered == []
    assert panel.legacy
    assert panel.events == []
    assert _issue(hass, entry) == "remove_old_app_adb"


async def test_trust_revoked_before_the_removal_offers_no_key(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    # OBSERVE connects trusted; adbd refuses the key when RETIRE connects.
    with _real_move_steps(panel, revoke_at=2) as offered:
        entry = await _load(hass, hass_read_only_user.id)

    assert offered == []
    assert panel.legacy
    assert panel.events == ["OBSERVE", "backup"]
    assert _issue(hass, entry) == "remove_old_app_adb"


@pytest.mark.parametrize(
    "home",
    [
        pytest.param(f"{LEGACY_PACKAGE_ID}/.DashboardActivity", id="old app"),
        pytest.param("", id="unreadable"),
        pytest.param("android/com.android.internal.app.ResolverActivity", id="chooser"),
        pytest.param("garbage", id="malformed"),
    ],
)
async def test_home_that_leaves_the_new_app_during_the_backup_keeps_the_old_app(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel, home: str
) -> None:
    """The removal reads HOME again in the shell that would uninstall.

    Only a readable HOME that is one launcher other than the old app lets the
    old app go; anything else keeps it.
    """
    panel.home_after_backup = home
    with _real_move_steps(panel, revoke_at=0) as offered:
        entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE", "backup", "RETIRE_LEGACY"]
    assert panel.legacy, "the launcher was uninstalled"
    assert offered == []
    assert _issue(hass, entry) == "remove_old_app_unproven"


async def test_a_vendor_launcher_taking_home_during_the_backup_lets_it_go(
    hass: HomeAssistant, hass_read_only_user: Any, panel: Panel
) -> None:
    """Removing the old app cannot strand a panel whose HOME is another launcher."""
    panel.home_after_backup = "com.vendor.launcher/.Launcher"
    with _real_move_steps(panel, revoke_at=0):
        entry = await _load(hass, hass_read_only_user.id)

    assert panel.events == ["OBSERVE", "backup", "RETIRE_LEGACY"]
    assert not panel.legacy
    assert _issue(hass, entry) is None

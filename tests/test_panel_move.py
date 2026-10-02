"""Panel Assistant moves a panel from the old app id to the new one over ADB."""

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import install_adb, panel_move
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.client import (
    HaPaneldError,
    PanelHealth,
    UpdateApprovalRequiredError,
    UpdateBusyError,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.identity import (
    adopt_moved_identity,
    reconcile_identity,
)
from custom_components.panel_assistant.install_adb import (
    InstallAdbError,
    InstallAdbErrorCode,
    LaunchOutcome,
    MoveObservation,
    MoveStep,
    _move_command,
    _parse_move,
)
from custom_components.panel_assistant.panel_backup import PanelBackupInvalidError
from custom_components.panel_assistant.panel_move import (
    CONF_SUCCESSOR_MOVE,
    MoveError,
    async_evaluate_successor_move,
    async_move_to_new_app,
    async_recover_moved_identity,
    async_restore_move_offer,
    claim_panel_operation,
    move_issue_id,
    release_panel_operation,
    verify_move_receipt,
)

OLD_DID = "a" * 64
NEW_DID = "b" * 64
OTHER_DID = "c" * 64
LEGACY_CFG = "5c80b060"
SERIAL = "PANEL1"


def _archive(**manifest: Any) -> bytes:
    body = {
        "kind": "ha-paneld-backup",
        "panel_id": "office",
        "discovery_id": OLD_DID,
        "package": LEGACY_PACKAGE_ID,
        "state": {"entry": "state/app-state.txt", "size": 1, "rows": 177},
        **manifest,
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(body))
        archive.writestr(
            "state/app-state.txt",
            "".join(
                f"S\t{namespace}\tkey{index}\tstring\tvalue\t1\n"
                for index, namespace in enumerate(
                    ("controller-state", "controller-state", "wifi-stability", "config")
                )
            ),
        )
    return buffer.getvalue()


class FakePanel:
    """A panel's package manager, HOME and HTTP app, as the move sees them.

    It refuses what would hurt a real panel: removing the app HOME resolves
    to, or a first start of the new app beside the old one.
    """

    def __init__(self, *, home: str | None = LEGACY_PACKAGE_ID) -> None:
        self.legacy = True
        self.successor = False
        self.successor_state = False
        self.home = home
        self.running: str | None = LEGACY_PACKAGE_ID
        self.panel_id = "office"
        self.cfg = LEGACY_CFG
        self.did: str | None = OLD_DID
        self.restores = 0
        self.running_restore = 0
        self.outcome: tuple[bool, int] = (True, 3)
        self.successor_launched = False
        self.successor_records: frozenset[str] | None = None
        self.steps: list[str] = []
        self.backup = _archive()
        self.serial = SERIAL
        self.retire_fails: str | None = None

    def observe(self) -> MoveObservation:
        return MoveObservation(
            self.legacy,
            self.successor,
            self.home,
            self.successor and not self.successor_launched,
            self.successor_records if self.successor else None,
        )

    async def step(self, _target: Any, _signer: Any, step: MoveStep) -> MoveObservation:
        self.steps.append(step.value)
        if step is MoveStep.REMOVE_SUCCESSOR:
            assert self.home != SUCCESSOR_PACKAGE_ID
            self.successor = self.successor_state = False
            self.successor_launched = False
        elif step is MoveStep.CLAIM_HOME:
            assert self.successor
            self.home = SUCCESSOR_PACKAGE_ID
        elif step is MoveStep.RETIRE_LEGACY:
            assert self.home != LEGACY_PACKAGE_ID, "launcher stranded"
            if self.retire_fails == "ambiguous":
                self.retire_fails = None
                raise InstallAdbError(InstallAdbErrorCode.INSTALL_AMBIGUOUS)
            if self.retire_fails == "refused":
                self.retire_fails = None
                return self.observe()
            self.legacy = False
            self.running = None
        elif step is MoveStep.RESET_SUCCESSOR:
            self.successor_state = False
            self.running = None
        return self.observe()

    async def install(self, *_args: Any) -> None:
        self.steps.append("INSTALL")
        assert self.legacy and not self.successor
        self.successor = True

    async def launch(self, *_args: Any, **_kwargs: Any) -> LaunchOutcome:
        self.steps.append("LAUNCH")
        assert not self.legacy, "new app started beside the old one"
        assert not self.successor_state, "new app started with stale state"
        self.running = SUCCESSOR_PACKAGE_ID
        self.successor_launched = True
        self.panel_id, self.cfg, self.did = "office_new", "00000001", NEW_DID
        self.successor_state = True
        return LaunchOutcome.STARTED

    async def health(self) -> PanelHealth:
        if self.running is None:
            raise HaPaneldError
        return PanelHealth(
            version="0.9.9-rc3",
            panel_id=self.panel_id,
            build="1000",
            config_hash=self.cfg,
            discovery_id=self.did,
            installation_identity=self.did is not None,
            package=self.running,
        )

    async def backup_panel(self) -> bytes:
        assert self.running == LEGACY_PACKAGE_ID
        self.steps.append("BACKUP")
        return self.backup

    async def restore(self, data: bytes) -> None:
        assert self.running == SUCCESSOR_PACKAGE_ID and data == self.backup
        self.restores += 1
        self.steps.append("RESTORE")
        # The first restore adopts the panel id; only a restore onto that id
        # brings the device's own state back, which completes the config.
        if self.panel_id == "office":
            self.cfg = LEGACY_CFG
        self.panel_id = "office"
        # The restore keeps running for a while after health shows its result.
        self.running_restore = 2

    async def restore_outcome(self) -> tuple[bool, int] | None:
        self.steps.append("LANE")
        if self.running_restore:
            self.running_restore -= 1
            return None
        return self.outcome


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office", unique_id=OLD_DID, data={"address": "x"}
    )
    entry.add_to_hass(hass)
    return entry


def _attach(entry: MockConfigEntry, panel: FakePanel) -> None:
    client = SimpleNamespace(
        async_get_health=panel.health,
        async_backup_panel=panel.backup_panel,
        async_restore_panel=panel.restore,
        async_get_restore_outcome=panel.restore_outcome,
        address=None,
    )
    snapshot = SimpleNamespace(
        health=PanelHealth(
            version="0.9.8",
            panel_id="office",
            build="1",
            config_hash=LEGACY_CFG,
            package=panel.running,
        )
    )
    entry.runtime_data = SimpleNamespace(
        client=client,
        coordinator=SimpleNamespace(data=snapshot, identity_mismatch=False),
    )


def _patches(panel: FakePanel, tmp_path: Path, adopted: list[str]) -> Any:
    async def store(_hass: Any, _entry_id: str, _code: Any, data: bytes) -> Any:
        path = tmp_path / "receipt.zip"
        path.write_bytes(data)
        return SimpleNamespace(path=path, sha256=sha256(data).hexdigest())

    def adopt(_hass: Any, entry: Any, did: str) -> bool:
        adopted.append(did)
        _hass.config_entries.async_update_entry(entry, unique_id=did)
        return True

    async def target(*_args: Any) -> Any:
        return SimpleNamespace(serial=panel.serial), "key"

    descriptor = SimpleNamespace(package_id=SUCCESSOR_PACKAGE_ID)
    artifact = SimpleNamespace(descriptor=descriptor)
    return [
        patch.object(panel_move, "_successor_artifact", return_value=artifact),
        patch.object(panel_move, "_async_target", target),
        patch.object(panel_move, "async_move_step", panel.step),
        patch.object(panel_move, "_async_install_successor", panel.install),
        patch.object(panel_move, "async_store_panel_backup", store),
        patch.object(
            panel_move,
            "async_preflight_install",
            AsyncMock(return_value=SimpleNamespace(root_mode="none")),
        ),
        patch.object(panel_move, "async_launch_installed_app", panel.launch),
        patch.object(panel_move, "adopt_moved_identity", adopt),
        patch.object(panel_move, "_POLL_SECONDS", 0),
    ]


async def _move(
    hass: HomeAssistant, entry: MockConfigEntry, panel: FakePanel, tmp_path: Path
) -> list[str]:
    adopted: list[str] = []
    patches = _patches(panel, tmp_path, adopted)
    for item in patches:
        item.start()
    try:
        await async_move_to_new_app(hass, entry)
    finally:
        for item in patches:
            item.stop()
    return adopted


def _save_entry(storage: Path, entry_id: str, unique_id: str) -> None:
    """Stand in for Home Assistant's delayed save of the config entries."""
    storage.parent.mkdir(parents=True, exist_ok=True)
    storage.write_text(
        json.dumps(
            {"data": {"entries": [{"entry_id": entry_id, "unique_id": unique_id}]}}
        )
    )


def _record_file(hass: HomeAssistant, entry: MockConfigEntry) -> Path:
    return Path(hass.config.path(DOMAIN, "backups", f"{entry.entry_id}-move.json"))


async def test_a_kiosk_panel_moves_with_home_safe_and_settings_restored(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)

    adopted = await _move(hass, entry, panel, tmp_path)

    assert [s for s in panel.steps if s != "LANE"] == [
        "OBSERVE",
        "BACKUP",
        "INSTALL",
        "OBSERVE",
        "CLAIM_HOME",
        "RETIRE_LEGACY",
        "RESET_SUCCESSOR",
        "LAUNCH",
        "RESTORE",
        "RESTORE",
    ]
    assert not panel.legacy and panel.home == SUCCESSOR_PACKAGE_ID
    assert (panel.panel_id, panel.cfg) == ("office", LEGACY_CFG)
    assert adopted == [NEW_DID]
    assert CONF_SUCCESSOR_MOVE not in entry.data


async def test_the_record_is_forgotten_only_after_the_restore_finished(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    """Health shows the restored config while the last restore still runs."""
    panel = FakePanel()
    _attach(entry, panel)

    await _move(hass, entry, panel, tmp_path)

    last_restore = len(panel.steps) - panel.steps[::-1].index("RESTORE") - 1
    assert panel.steps[last_restore + 1 :] == ["LANE", "LANE", "LANE"]
    assert CONF_SUCCESSOR_MOVE not in entry.data


async def test_a_new_app_without_a_valid_identity_keeps_the_record(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    real_launch = panel.launch

    async def launch_anonymous(*args: Any, **kwargs: Any) -> LaunchOutcome:
        outcome = await real_launch(*args, **kwargs)
        panel.did = None
        return outcome

    panel.launch = launch_anonymous  # type: ignore[method-assign]
    with (
        patch.object(panel_move, "_RESTORE_WAIT_SECONDS", 0.2),
        pytest.raises(MoveError, match="move_failed"),
    ):
        await _move(hass, entry, panel, tmp_path)

    assert _record_file(hass, entry).exists()
    assert entry.unique_id == OLD_DID


async def test_a_panel_with_its_own_launcher_keeps_it(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel(home="com.android.launcher3")
    _attach(entry, panel)

    await _move(hass, entry, panel, tmp_path)

    assert "CLAIM_HOME" not in panel.steps
    assert panel.home == "com.android.launcher3"


async def test_a_refused_handover_leftover_is_removed_after_the_receipt(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    """State (a)/(b): the new app sits passive beside the old one."""
    panel = FakePanel()
    panel.successor = True
    _attach(entry, panel)

    await _move(hass, entry, panel, tmp_path)

    assert panel.steps[:4] == ["OBSERVE", "BACKUP", "REMOVE_SUCCESSOR", "INSTALL"]
    assert not panel.legacy and panel.panel_id == "office"


async def test_a_new_app_already_home_is_left_untouched(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel(home=SUCCESSOR_PACKAGE_ID)
    panel.successor = True
    _attach(entry, panel)

    with pytest.raises(MoveError, match="new_app_is_home"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["OBSERVE"]
    assert panel.legacy and not _record_file(hass, entry).exists()


async def test_an_incomplete_backup_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    panel.backup = _archive(state={"rows": 0})
    _attach(entry, panel)

    with pytest.raises(MoveError, match="backup_failed"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["OBSERVE", "BACKUP"]
    assert panel.legacy and not panel.successor


@pytest.mark.parametrize(
    ("health_did", "backup_did"),
    [(OTHER_DID, OTHER_DID), (OLD_DID, OTHER_DID)],
    ids=["another-panel-answers", "backup-from-another-panel"],
)
async def test_another_panel_is_never_changed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    tmp_path: Path,
    health_did: str,
    backup_did: str,
) -> None:
    panel = FakePanel()
    panel.did = health_did
    panel.backup = _archive(discovery_id=backup_did)
    _attach(entry, panel)

    with pytest.raises(MoveError, match="panel_changed"):
        await _move(hass, entry, panel, tmp_path)

    assert set(panel.steps) <= {"OBSERVE", "BACKUP"}
    assert panel.legacy and not panel.successor


async def test_a_stale_entry_is_never_moved(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    entry.runtime_data.coordinator.identity_mismatch = True

    with pytest.raises(MoveError, match="panel_changed"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == []


@pytest.mark.parametrize("failure", ["ambiguous", "refused"])
async def test_a_move_stopped_before_the_old_app_went_finishes_on_retry(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path, failure: str
) -> None:
    """The record exists, HOME is the new app, and the old app is still there."""
    panel = FakePanel()
    panel.retire_fails = failure
    _attach(entry, panel)

    with pytest.raises(MoveError, match="move_failed"):
        await _move(hass, entry, panel, tmp_path)
    assert panel.legacy and panel.home == SUCCESSOR_PACKAGE_ID
    assert _record_file(hass, entry).exists()

    panel.steps.clear()
    adopted = await _move(hass, entry, panel, tmp_path)

    # Its own new app beside the old one is kept, and a fresh receipt is taken
    # from the old app that still owns the panel.
    assert [s for s in panel.steps if s != "LANE"][:3] == [
        "OBSERVE",
        "BACKUP",
        "RETIRE_LEGACY",
    ]
    assert "REMOVE_SUCCESSOR" not in panel.steps
    assert not panel.legacy and adopted == [NEW_DID]
    assert CONF_SUCCESSOR_MOVE not in entry.data


async def test_a_move_interrupted_after_the_old_app_went_resumes_after_a_restart(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    real_restore = panel.restore

    async def refuse(_data: bytes) -> None:
        raise UpdateApprovalRequiredError

    panel.restore = refuse  # type: ignore[method-assign]
    _attach(entry, panel)
    with pytest.raises(MoveError, match="restore_approval"):
        await _move(hass, entry, panel, tmp_path)
    assert not panel.legacy

    # Core stopped before the entry's copy of the offer was saved: only the
    # durable record knows of the move.
    hass.config_entries.async_update_entry(entry, data={"address": "x"})
    await async_restore_move_offer(hass, entry)
    assert entry.data.get(CONF_SUCCESSOR_MOVE) == {"panel_id": "office"}

    panel.restore = real_restore  # type: ignore[method-assign]
    panel.steps.clear()
    _attach(entry, panel)
    adopted = await _move(hass, entry, panel, tmp_path)

    # The new app was started clean before the stop and still serves.
    assert [s for s in panel.steps if s != "LANE"] == [
        "OBSERVE",
        "RESTORE",
        "RESTORE",
    ]
    assert adopted == [NEW_DID] and CONF_SUCCESSOR_MOVE not in entry.data


async def test_a_replaced_panel_at_the_address_is_never_changed(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    panel.retire_fails = "ambiguous"
    _attach(entry, panel)
    with pytest.raises(MoveError):
        await _move(hass, entry, panel, tmp_path)

    panel.serial = "ANOTHER"
    panel.steps.clear()
    with pytest.raises(MoveError, match="panel_changed"):
        await _move(hass, entry, panel, tmp_path)
    assert panel.steps == []


async def test_a_move_waits_for_no_update_and_blocks_one(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    assert claim_panel_operation(hass, entry.entry_id)
    try:
        with pytest.raises(MoveError, match="busy"):
            await _move(hass, entry, panel, tmp_path)
        assert panel.steps == []
    finally:
        release_panel_operation(hass, entry.entry_id)

    claimed: list[bool] = []

    async def observe_claim(*args: Any) -> MoveObservation:
        claimed.append(claim_panel_operation(hass, entry.entry_id))
        return await panel.step(*args)

    patches = _patches(panel, tmp_path, [])
    for item in patches:
        item.start()
    try:
        with patch.object(panel_move, "async_move_step", observe_claim):
            await async_move_to_new_app(hass, entry)
    finally:
        for item in patches:
            item.stop()
    assert claimed and not any(claimed)
    assert claim_panel_operation(hass, entry.entry_id)


async def test_a_moved_panel_is_a_no_op(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    await _move(hass, entry, panel, tmp_path)
    panel.steps.clear()

    with pytest.raises(MoveError, match="already_moved"):
        await _move(hass, entry, panel, tmp_path)
    assert panel.steps == []


async def test_a_restore_still_running_is_waited_out(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    """The panel adopts its id before the first restore has finished."""
    panel = FakePanel()
    real_restore = panel.restore
    busy = [True]

    async def busy_once(data: bytes) -> None:
        if panel.restores == 1 and busy[0]:
            busy[0] = False
            raise UpdateBusyError
        await real_restore(data)

    panel.restore = busy_once  # type: ignore[method-assign]
    _attach(entry, panel)

    await _move(hass, entry, panel, tmp_path)

    assert panel.restores == 2 and not busy[0]
    assert CONF_SUCCESSOR_MOVE not in entry.data


async def test_the_repair_follows_the_package_and_the_record(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    registry = ir.async_get(hass)
    issue = (DOMAIN, move_issue_id(entry.entry_id))

    async_evaluate_successor_move(hass, entry)
    assert issue in registry.issues

    panel.running = SUCCESSOR_PACKAGE_ID
    entry.runtime_data.coordinator.data.health = await panel.health()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_SUCCESSOR_MOVE: {"panel_id": "office"}}
    )
    async_evaluate_successor_move(hass, entry)
    assert issue in registry.issues

    hass.config_entries.async_update_entry(entry, data={"address": "x"})
    async_evaluate_successor_move(hass, entry)
    assert issue not in registry.issues


def test_a_receipt_must_be_the_old_apps_whole_state() -> None:
    assert verify_move_receipt(_archive()) == ("office", OLD_DID, 3)
    for broken in (
        _archive(package=SUCCESSOR_PACKAGE_ID),
        _archive(state={"rows": 0}),
        _archive(discovery_id=None),
        _archive(discovery_id="not-an-id"),
        b"not a zip",
    ):
        with pytest.raises(PanelBackupInvalidError):
            verify_move_receipt(broken)


def test_move_observation_reads_packages_and_home() -> None:
    nonce = "0" * 32
    command = _move_command(nonce, MoveStep.RETIRE_LEGACY)
    assert f"pm uninstall {LEGACY_PACKAGE_ID}" in command
    body = (
        f"HAPANELD_MOVE_BEGIN:{nonce}\ninstalled:{SUCCESSOR_PACKAGE_ID}\n"
        f"home:{SUCCESSOR_PACKAGE_ID}/io.github.maxlyth.hapaneld.DashboardActivity\n"
        f"HAPANELD_MOVE_END:{nonce}:0\n"
    ).encode()
    assert _parse_move(body, nonce) == MoveObservation(
        False, True, SUCCESSOR_PACKAGE_ID
    )
    chooser = body.replace(
        f"home:{SUCCESSOR_PACKAGE_ID}/io.github.maxlyth.hapaneld.DashboardActivity".encode(),
        b"home:android/com.android.internal.app.ResolverActivity",
    )
    assert _parse_move(chooser, nonce).home is None
    with pytest.raises(Exception):  # noqa: B017 - any parse refusal
        _parse_move(body.replace(b"installed:", b"stray:"), nonce)


async def test_a_move_step_on_another_device_sends_no_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each connection proves the device before any move command is sent."""
    sent: list[str] = []

    async def shell(_device: Any, command: str, **_kwargs: Any) -> bytes:
        sent.append(command)
        return b""

    def changed(*_args: Any) -> Any:
        raise InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)

    monkeypatch.setattr(install_adb, "_async_connect", AsyncMock(return_value=object()))
    monkeypatch.setattr(install_adb, "_async_close", AsyncMock())
    monkeypatch.setattr(install_adb, "_async_shell", shell)
    monkeypatch.setattr(install_adb, "_parse_identity_root", changed)
    monkeypatch.setattr(install_adb, "_validate_target", lambda _target: None)

    with pytest.raises(InstallAdbError) as error:
        await install_adb.async_move_step(
            SimpleNamespace(), "key", MoveStep.RETIRE_LEGACY
        )

    assert error.value.code is InstallAdbErrorCode.TARGET_CHANGED
    assert len(sent) == 1 and "pm uninstall" not in sent[0]


async def test_a_moved_panel_keeps_its_entities_under_the_new_identity(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create(
        "sensor", DOMAIN, f"{OLD_DID}_status", config_entry=entry
    )

    assert adopt_moved_identity(hass, entry, NEW_DID)
    await hass.async_block_till_done()

    assert entry.unique_id == NEW_DID
    moved = registry.async_get(sensor.entity_id)
    assert moved is not None and moved.unique_id == f"{NEW_DID}_status"


async def test_an_identity_another_panel_holds_is_never_adopted(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id=NEW_DID, data={"address": "y"}
    ).add_to_hass(hass)

    assert not adopt_moved_identity(hass, entry, NEW_DID)
    assert entry.unique_id == OLD_DID


async def test_a_new_app_that_has_run_beside_the_old_one_is_kept(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    """It may have been set up on its own first: nothing proves it empty."""
    panel = FakePanel(home="com.android.launcher3")
    panel.successor = panel.successor_launched = True
    _attach(entry, panel)

    with pytest.raises(MoveError, match="new_app_is_home"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["OBSERVE"]
    assert panel.successor and panel.legacy


@pytest.mark.parametrize(
    "outcome",
    [(False, 3), (True, 0), (True, 2)],
    ids=["restore-failed", "state-not-written", "state-partly-written"],
)
async def test_an_incomplete_restore_keeps_the_record_and_the_offer(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    tmp_path: Path,
    outcome: tuple[bool, int],
) -> None:
    panel = FakePanel()
    panel.outcome = outcome
    _attach(entry, panel)

    with pytest.raises(MoveError, match="move_failed"):
        await _move(hass, entry, panel, tmp_path)

    assert _record_file(hass, entry).exists()
    assert entry.data.get(CONF_SUCCESSOR_MOVE) == {"panel_id": "office"}
    assert entry.unique_id == OLD_DID


async def test_the_record_outlives_the_adoption_until_it_is_saved(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(entry, panel)
    await _move(hass, entry, panel, tmp_path)
    record = _record_file(hass, entry)
    assert record.exists() and json.loads(record.read_text())["new_did"] == NEW_DID
    assert CONF_SUCCESSOR_MOVE not in entry.data

    # Core stopped before it saved the adopted identity: the entry comes back
    # with the old one, and the record adopts the new identity again before
    # setup compares identities.
    hass.config_entries.async_update_entry(entry, unique_id=OLD_DID)
    with patch.object(panel_move, "adopt_moved_identity") as adopt:
        await async_recover_moved_identity(hass, entry)
    adopt.assert_called_once_with(hass, entry, NEW_DID, reload=False)
    await async_restore_move_offer(hass, entry)
    assert record.exists()

    # Saved: the next setup reads the new identity back from disk and settles.
    hass.config_entries.async_update_entry(entry, unique_id=NEW_DID)
    storage = Path(hass.config.path(".storage", "core.config_entries"))
    _save_entry(storage, entry.entry_id, OLD_DID)
    await async_restore_move_offer(hass, entry)
    assert record.exists()
    _save_entry(storage, entry.entry_id, NEW_DID)
    await async_restore_move_offer(hass, entry)
    assert not record.exists()


@pytest.mark.parametrize("saved_first", ["registry", "entry"])
async def test_setup_passes_its_identity_gate_whichever_save_landed_first(
    hass: HomeAssistant, entry: MockConfigEntry, saved_first: str
) -> None:
    """Adoption writes the registry and the entry separately; a stop between
    the two leaves one of them behind, and setup must still accept the entry."""
    registry = er.async_get(hass)
    path = Path(hass.config.path(DOMAIN, "backups", f"{entry.entry_id}-move.json"))
    _write_record_file(path)
    if saved_first == "registry":
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{NEW_DID}_status", config_entry=entry
        )
    else:
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{OLD_DID}_status", config_entry=entry
        )
        hass.config_entries.async_update_entry(
            entry,
            unique_id=NEW_DID,
            data={
                **entry.data,
                "installation_identity": True,
                "previous_installation_identity": OLD_DID,
            },
        )

    await async_recover_moved_identity(hass, entry)

    assert entry.unique_id == NEW_DID
    assert reconcile_identity(hass, entry)
    assert {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    } == {f"{NEW_DID}_status"}


def _write_record_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "receipt": "r",
                "sha256": "s",
                "panel_id": "office",
                "config_hash": LEGACY_CFG,
                "legacy_did": OLD_DID,
                "serial": SERIAL,
                "carried": 3,
                "new_did": NEW_DID,
            }
        )
    )


@pytest.mark.parametrize(
    ("records", "removed"),
    [
        (frozenset({"step-pull.v1", "step-verify.v1"}), True),
        (frozenset(), True),
        (frozenset({"step-pull.v1", "complete.v1"}), False),
        (frozenset({"step-restore.v1"}), False),
        (None, False),
    ],
    ids=["waited-for-handover", "no-records", "finished", "restored", "unreadable"],
)
async def test_a_new_app_that_only_waited_beside_the_old_one_is_replaced(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    tmp_path: Path,
    records: frozenset[str] | None,
    removed: bool,
) -> None:
    """Its own records, read through root, prove it never held the panel's state."""
    panel = FakePanel(home="com.android.launcher3")
    panel.successor = panel.successor_launched = True
    panel.successor_records = records
    _attach(entry, panel)

    if removed:
        await _move(hass, entry, panel, tmp_path)
        assert panel.steps[:3] == ["OBSERVE", "BACKUP", "REMOVE_SUCCESSOR"]
        assert not panel.legacy
    else:
        with pytest.raises(MoveError, match="new_app_is_home"):
            await _move(hass, entry, panel, tmp_path)
        assert panel.steps == ["OBSERVE"] and panel.legacy and panel.successor


def test_the_new_apps_records_are_read_only_when_root_answers() -> None:
    nonce = "0" * 32
    head = f"HAPANELD_MOVE_BEGIN:{nonce}\ninstalled:{LEGACY_PACKAGE_ID}\n"
    tail = f"home:{LEGACY_PACKAGE_ID}/.DashboardActivity\nHAPANELD_MOVE_END:{nonce}:0\n"
    both = head + f"installed:{SUCCESSOR_PACKAGE_ID}\n"
    read = _parse_move(
        (both + "record:step-pull.v1\nrecords:read\n" + tail).encode(), nonce
    )
    assert read.successor_records == frozenset({"step-pull.v1"})
    empty = _parse_move((both + "records:read\n" + tail).encode(), nonce)
    assert empty.successor_records == frozenset()
    assert _parse_move((both + tail).encode(), nonce).successor_records is None
    with pytest.raises(Exception):  # noqa: B017 - records without a completed read
        _parse_move((both + "record:complete.v1\n" + tail).encode(), nonce)

"""Panel Assistant moves a panel from the old app id to the new one over ADB."""

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import panel_move
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.client import (
    PanelHealth,
    UpdateApprovalRequiredError,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_adb import (
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
    move_issue_id,
    verify_move_receipt,
)

OLD_DID = "a" * 64
NEW_DID = "b" * 64
LEGACY_CFG = "5c80b060"


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
        archive.writestr("state/app-state.txt", "x")
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
        self.did = OLD_DID
        self.restores = 0
        self.steps: list[str] = []
        self.backup = _archive()

    def observe(self) -> MoveObservation:
        return MoveObservation(self.legacy, self.successor, self.home)

    async def step(self, _target: Any, _signer: Any, step: MoveStep) -> MoveObservation:
        self.steps.append(step.value)
        if step is MoveStep.REMOVE_SUCCESSOR:
            assert self.home != SUCCESSOR_PACKAGE_ID
            self.successor = self.successor_state = False
        elif step is MoveStep.CLAIM_HOME:
            assert self.successor
            self.home = SUCCESSOR_PACKAGE_ID
        elif step is MoveStep.RETIRE_LEGACY:
            assert self.home != LEGACY_PACKAGE_ID, "launcher stranded"
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
        self.panel_id, self.cfg, self.did = "office_new", "00000001", NEW_DID
        self.successor_state = True
        return LaunchOutcome.STARTED

    async def health(self) -> PanelHealth:
        if self.running is None:
            raise panel_move.HaPaneldError
        return PanelHealth(
            version="0.9.9-rc3",
            panel_id=self.panel_id,
            build="1000",
            config_hash=self.cfg,
            discovery_id=self.did,
            installation_identity=True,
            package=self.running,
        )

    async def backup_panel(self) -> bytes:
        assert self.running == LEGACY_PACKAGE_ID
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


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Office", unique_id=OLD_DID, data={"address": "x"}
    )
    entry.add_to_hass(hass)
    return entry


def _attach(hass: HomeAssistant, entry: MockConfigEntry, panel: FakePanel) -> None:
    client = SimpleNamespace(
        async_get_health=panel.health,
        async_backup_panel=panel.backup_panel,
        async_restore_panel=panel.restore,
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
        client=client, coordinator=SimpleNamespace(data=snapshot)
    )


def _patches(panel: FakePanel, tmp_path: Path, adopted: list[str]) -> Any:
    async def store(_hass: Any, _entry_id: str, _code: Any, data: bytes) -> Any:
        path = tmp_path / "receipt.zip"
        path.write_bytes(data)
        from hashlib import sha256

        return SimpleNamespace(path=path, sha256=sha256(data).hexdigest())

    def adopt(_hass: Any, _entry: Any, did: str) -> bool:
        adopted.append(did)
        return True

    descriptor = SimpleNamespace(package_id=SUCCESSOR_PACKAGE_ID)
    artifact = SimpleNamespace(descriptor=descriptor)
    return [
        patch.object(panel_move, "_successor_artifact", return_value=artifact),
        patch.object(
            panel_move, "_async_target", AsyncMock(return_value=("target", "key"))
        ),
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


async def test_a_kiosk_panel_moves_with_home_safe_and_settings_restored(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(hass, entry, panel)

    adopted = await _move(hass, entry, panel, tmp_path)

    assert panel.steps == [
        "OBSERVE",
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


async def test_a_panel_with_its_own_launcher_keeps_it(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel(home="com.android.launcher3")
    _attach(hass, entry, panel)

    await _move(hass, entry, panel, tmp_path)

    assert "CLAIM_HOME" not in panel.steps
    assert panel.home == "com.android.launcher3"


async def test_a_refused_handover_leftover_is_removed_before_the_move(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    """State (a)/(b): the new app sits passive beside the old one."""
    panel = FakePanel()
    panel.successor = panel.successor_state = True
    _attach(hass, entry, panel)

    await _move(hass, entry, panel, tmp_path)

    assert panel.steps[:3] == ["OBSERVE", "REMOVE_SUCCESSOR", "INSTALL"]
    assert not panel.legacy and panel.panel_id == "office"


async def test_a_new_app_already_home_is_left_untouched(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel(home=SUCCESSOR_PACKAGE_ID)
    panel.successor = True
    _attach(hass, entry, panel)

    with pytest.raises(MoveError, match="new_app_is_home"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["OBSERVE"]
    assert panel.legacy and CONF_SUCCESSOR_MOVE not in entry.data


async def test_an_incomplete_backup_changes_nothing(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    panel.backup = _archive(state={"rows": 0})
    _attach(hass, entry, panel)

    with pytest.raises(MoveError, match="backup_failed"):
        await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["OBSERVE"]
    assert panel.legacy and not panel.successor


async def test_a_move_interrupted_after_the_old_app_went_resumes(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(hass, entry, panel)
    real_restore = panel.restore

    async def refuse(_data: bytes) -> None:
        raise UpdateApprovalRequiredError

    panel.restore = refuse  # type: ignore[method-assign]
    _attach(hass, entry, panel)
    with pytest.raises(MoveError, match="restore_approval"):
        await _move(hass, entry, panel, tmp_path)
    assert not panel.legacy
    assert entry.data[CONF_SUCCESSOR_MOVE]["panel_id"] == "office"

    panel.restore = real_restore  # type: ignore[method-assign]
    panel.steps.clear()
    _attach(hass, entry, panel)
    adopted = await _move(hass, entry, panel, tmp_path)

    assert panel.steps == ["RESET_SUCCESSOR", "LAUNCH", "RESTORE", "RESTORE"]
    assert adopted == [NEW_DID] and CONF_SUCCESSOR_MOVE not in entry.data


async def test_a_moved_panel_is_a_no_op(
    hass: HomeAssistant, entry: MockConfigEntry, tmp_path: Path
) -> None:
    panel = FakePanel()
    _attach(hass, entry, panel)
    await _move(hass, entry, panel, tmp_path)
    panel.steps.clear()
    entry.runtime_data.coordinator.data.health = await panel.health()

    with pytest.raises(MoveError, match="already_moved"):
        await _move(hass, entry, panel, tmp_path)
    assert panel.steps == []


async def test_the_repair_follows_the_package_and_the_record(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    panel = FakePanel()
    _attach(hass, entry, panel)
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
    assert verify_move_receipt(_archive()) == "office"
    for broken in (
        _archive(package=SUCCESSOR_PACKAGE_ID),
        _archive(state={"rows": 0}),
        _archive(discovery_id=None),
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

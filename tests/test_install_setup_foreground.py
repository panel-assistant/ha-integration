"""A network install proves the setup screen while the app defers its HOME claim.

The installed app claims HOME only once its first setup has completed. Until
then the install's grant reports the chooser ``deferred`` and HOME stays with
Android's resolver. The app's status endpoint then demands owned HOME unless
the request asks for ``setup_foreground`` while setup is incomplete, so the
network install must ask for it exactly when its own grant deferred the claim.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import (
    adb_protocol,
    install_adb,
    install_executor,
)
from custom_components.panel_assistant.adb_protocol import AdbInstallTarget, AdbRootMode
from custom_components.panel_assistant.app_identity import HOME_COMPONENTS, PACKAGE_ID
from custom_components.panel_assistant.client import PanelSetupState
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_executor import (
    FinalizationOutcome,
    InstallExecutor,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.release import InstallDescriptor
from custom_components.panel_assistant.status import PanelStatus

from .install_executor_harness import (  # noqa: F401 - shared autouse Store fixture
    Harness,
    artifact,
    create_job,
    emulate_home_assistant_store_file,
    seed_phase,
)
from .test_install_adb_grants import _RoutingFakeDevice
from .test_install_executor_finalizer import seed_healthy

# The component the grant program claims, as the stand-in Android records it.
_OWNED_HOME = HOME_COMPONENTS[artifact().launch_component]


class _AppPanel(_RoutingFakeDevice):
    """The panel's ADB side: the real grant program against a stand-in Android."""

    async def streaming_shell(
        self, command: str, **kwargs: Any
    ) -> AsyncIterator[bytes]:
        moved = re.search(r"echo HAPANELD_PREVIOUS_HOME:([0-9a-f]{32}):", command)
        if moved is not None:
            self.commands.append(command)
            yield f"HAPANELD_PREVIOUS_HOME:{moved.group(1)}:0\n".encode()
            return
        async for chunk in super().streaming_shell(command, **kwargs):
            yield chunk


def _app_panel(
    monkeypatch: pytest.MonkeyPatch, state: Path, *, setup_complete: bool
) -> Harness:
    """Run the real launch over ADB, and answer status as the app does."""
    if not setup_complete:
        (state / "incomplete.setup").touch()
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=setup_complete)
    monkeypatch.setattr(
        install_executor,
        "async_start_installed_app",
        install_adb.async_start_installed_app,
    )
    monkeypatch.setattr(
        adb_protocol, "AdbDeviceAsync", lambda *_args, **_kwargs: _AppPanel(state)
    )
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())

    stand_in = install_executor.HaPaneldClient

    class AppStatusClient(stand_in):  # type: ignore[misc,valid-type]
        async def async_get_status(
            self, *, home_proof: bool = False, setup_foreground: bool = False
        ) -> PanelStatus:
            # ConfigPreviousHome: owned HOME is demanded unless setup_foreground
            # is asked for while first setup is incomplete. SystemController:
            # the resolver in front answers blocked/home_resolver.
            assert home_proof
            harness.setup_foreground_requests.append(setup_foreground)
            home = state / "home"
            owned = home.exists() and home.read_text().startswith(f"{PACKAGE_ID}/")
            incomplete = (state / "incomplete.setup").exists()
            if owned or (setup_foreground and incomplete):
                home_ui = {
                    "state": "setup" if incomplete else "ready",
                    "reason": "activity",
                    "evidence": "foreground",
                }
            else:
                home_ui = {
                    "state": "blocked",
                    "reason": "home_resolver",
                    "evidence": "resolver",
                }
            return PanelStatus(warning_count=0, capability_count=0, home_ui=home_ui)

    monkeypatch.setattr(install_executor, "HaPaneldClient", AppStatusClient)
    return harness


def _stored_job(hass_storage: dict[str, Any], job_id: str) -> dict[str, Any]:
    jobs = hass_storage[f"{DOMAIN}.install_jobs"]["data"]["jobs"]
    return next(job for job in jobs if job["job_id"] == job_id)


async def _finalize(hass: HomeAssistant, job_id: str) -> FinalizationOutcome:
    executor = InstallExecutor(hass, InstallJobManager(hass))
    return (await executor.async_verify_finalization(job_id, "flow_one")).outcome


async def test_a_deferred_home_claim_proves_setup_through_health_and_final_verification(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fresh panel with HOME left to the resolver installs and verifies."""
    harness = _app_panel(monkeypatch, tmp_path, setup_complete=False)
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert not (tmp_path / "home").exists(), "the grant must not have claimed HOME"
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.home_claim_deferred
    assert _stored_job(hass_storage, receipt.job_id)["home_claim_deferred"] is True
    assert harness.setup_foreground_requests == [True]

    assert await _finalize(hass, receipt.job_id) is FinalizationOutcome.VERIFIED
    assert harness.setup_foreground_requests == [True, True]


@pytest.mark.parametrize("chooser", ["kept", "set"])
async def test_a_claimed_home_is_proven_owned_and_drift_still_fails(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    chooser: str,
) -> None:
    """A grant that kept or set HOME never relaxes the proof."""
    if chooser == "kept":
        (tmp_path / "home").write_text(f"{_OWNED_HOME}\n")
    harness = _app_panel(monkeypatch, tmp_path, setup_complete=True)
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert (tmp_path / "home").read_text().strip() == _OWNED_HOME
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert not completed.home_claim_deferred
    assert "home_claim_deferred" not in _stored_job(hass_storage, receipt.job_id)
    assert harness.setup_foreground_requests == [False]

    # HOME drifts away from the app before the entry is created.
    (tmp_path / "home").unlink()
    assert (
        await _finalize(hass, receipt.job_id) is FinalizationOutcome.RECOVERY_REQUIRED
    )
    assert harness.setup_foreground_requests == [False, False]
    rejected = await InstallJobManager(hass).async_get(receipt.job_id)
    assert rejected.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert rejected.result_subcode == "home_ui:not_ready"


@pytest.mark.parametrize("persisted", [False, True], ids=["older", "deferred"])
async def test_a_restarted_health_check_uses_only_the_persisted_deferral(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    persisted: bool,
) -> None:
    """A receipt without the deferral, as older builds wrote it, stays strict."""
    harness = _app_panel(monkeypatch, tmp_path, setup_complete=False)
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTH_CHECK)
    stored = _stored_job(hass_storage, receipt.job_id)
    assert "home_claim_deferred" not in stored
    if persisted:
        stored["home_claim_deferred"] = True

    restarted = InstallJobManager(hass)
    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    if persisted:
        assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
        assert harness.setup_foreground_requests == [True]
    else:
        assert completed.phase is InstallPhase.RECOVERY_REQUIRED
        assert completed.result_subcode == "home_ui:not_ready"
        assert harness.setup_foreground_requests == [False] * (
            install_executor._HEALTH_ATTEMPTS
        )


@pytest.mark.parametrize("persisted", [False, True], ids=["older", "deferred"])
async def test_final_verification_after_restart_uses_only_the_persisted_deferral(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    persisted: bool,
) -> None:
    harness = _app_panel(monkeypatch, tmp_path, setup_complete=False)
    receipt, _manager = await seed_healthy(hass)
    stored = _stored_job(hass_storage, receipt.job_id)
    assert "home_claim_deferred" not in stored
    if persisted:
        stored["home_claim_deferred"] = True

    outcome = await _finalize(hass, receipt.job_id)

    assert harness.setup_foreground_requests == [persisted]
    assert outcome is (
        FinalizationOutcome.VERIFIED
        if persisted
        else FinalizationOutcome.RECOVERY_REQUIRED
    )


@pytest.mark.parametrize(
    "first_deferred", [True, False], ids=["deferred-then-set", "set-then-deferred"]
)
async def test_a_display_settlement_relaunch_replaces_the_first_claims_authority(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first_deferred: bool,
) -> None:
    """The proof after the relaunch, and the one persisted, follow its grant."""
    harness = _app_panel(monkeypatch, tmp_path, setup_complete=not first_deferred)
    harness.installed_bytes = artifact().apk_size

    async def settle(
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> str:
        # The relaunch's grant answers the other way from the first launch's.
        if first_deferred:
            (tmp_path / "incomplete.setup").unlink()
        else:
            (tmp_path / "incomplete.setup").touch()
            (tmp_path / "home").unlink()
        return "settled"

    monkeypatch.setattr(install_executor, "async_settle_vendor_display", settle)
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.home_claim_deferred is not first_deferred
    assert (
        "home_claim_deferred" in _stored_job(hass_storage, receipt.job_id)
    ) is not first_deferred
    assert harness.setup_foreground_requests == [first_deferred, not first_deferred]

    assert await _finalize(hass, receipt.job_id) is FinalizationOutcome.VERIFIED
    assert harness.setup_foreground_requests[-1] is not first_deferred


@pytest.mark.parametrize(
    "chooser", ["tapped", "other_app", "no_button", "other_home", None]
)
async def test_no_other_home_claim_outcome_relaxes_the_proof(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    chooser: str | None,
) -> None:
    """Only a deferred claim may ask for the setup screen; HOME stays unowned."""
    harness = Harness(monkeypatch)
    harness.home_chooser = chooser
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)
    stand_in = install_executor.HaPaneldClient

    class UnownedHomeClient(stand_in):  # type: ignore[misc,valid-type]
        async def async_get_status(
            self, *, home_proof: bool = False, setup_foreground: bool = False
        ) -> PanelStatus:
            # Setup is incomplete and HOME is the resolver's: the app shows its
            # setup screen only to a request that asks for it.
            harness.home_ui_state = "setup" if setup_foreground else "blocked"
            return await super().async_get_status(
                home_proof=home_proof, setup_foreground=setup_foreground
            )

    monkeypatch.setattr(install_executor, "HaPaneldClient", UnownedHomeClient)
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_subcode == "home_ui:not_ready"
    assert not completed.home_claim_deferred
    assert harness.setup_foreground_requests == [False] * (
        install_executor._HEALTH_ATTEMPTS
    )

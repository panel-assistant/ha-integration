"""Display sizing and vendor settlement through the guarded install paths."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import install_executor
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    LaunchOutcome,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    PanelHealth,
    PanelSetupState,
)
from custom_components.panel_assistant.display_provisioning import (
    DisplayResult,
    DisplaySizing,
)
from custom_components.panel_assistant.install_adb import async_settle_vendor_display
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.release import InstallDescriptor

from .install_adb_fakes import (
    NONCES,
    FakeDevice,
    _identity_root_output,
    _install_fakes,
    _single_output,
)
from .install_executor_harness import Harness, artifact
from .test_install_adb import (
    _installed_reads,
)
from .test_install_adb import (
    descriptor as _descriptor_fixture,
)
from .test_install_adb import (
    signer as _signer_fixture,
)
from .test_install_adb import (
    target as _target_fixture,
)
from .test_install_executor import (  # noqa: F401 - shared autouse Store fixture
    create_job,
    emulate_home_assistant_store_file,
)

# Re-export the existing transport fixtures for this focused module.
descriptor = _descriptor_fixture
signer = _signer_fixture
target = _target_fixture


@pytest.mark.parametrize("outcome", ["settled", "skipped"])
async def test_display_settlement_runs_only_after_exact_installed_bytes_and_posture(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    outcome: str,
) -> None:
    fake = FakeDevice(
        [
            *_installed_reads(),
            _single_output("VENDOR_DISPLAY_SETTLEMENT", NONCES[3], [outcome], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    observed = await async_settle_vendor_display(
        target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert observed == outcome
    assert len(fake.commands) == 4
    assert "sha256sum" in fake.commands[2]
    assert "HAPANELD_VENDOR_DISPLAY_SETTLEMENT_BEGIN" in fake.commands[3]
    assert fake.closed


@pytest.mark.parametrize("changed", ["bytes", "identity", "root"])
async def test_changed_panel_or_installed_bytes_refuses_native_resume(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    changed: str,
) -> None:
    replies = (
        _installed_reads(sha256="f" * 64) if changed == "bytes" else _installed_reads()
    )
    expected = InstallAdbErrorCode.TARGET_CHANGED
    if changed == "identity":
        replies[0] = _identity_root_output(NONCES[0], serial="OTHER")
    elif changed == "root":
        replies[0] = _identity_root_output(NONCES[0], uid="0")
        expected = InstallAdbErrorCode.ROOT_MODE_CHANGED
    fake = FakeDevice(replies)
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_settle_vendor_display(
            target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
        )

    assert caught.value.code is expected
    assert not any(
        "VENDOR_DISPLAY_SETTLEMENT_BEGIN" in command for command in fake.commands
    )
    assert fake.closed


@pytest.mark.parametrize(
    "reply",
    [
        _single_output("VENDOR_DISPLAY_SETTLEMENT", NONCES[3], ["error"], 0),
        _single_output("VENDOR_DISPLAY_SETTLEMENT", NONCES[3], ["settled"], 1),
        b"connection lost during native resume",
        TimeoutError(),
    ],
)
async def test_unproven_native_resume_is_ambiguous_after_mutation(
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,
    target: AdbInstallTarget,
    descriptor: InstallDescriptor,
    reply: bytes | Exception,
) -> None:
    fake = FakeDevice([*_installed_reads(), reply])
    _install_fakes(monkeypatch, [fake])

    with pytest.raises(InstallAdbError) as caught:
        await async_settle_vendor_display(
            target, signer, descriptor, expected_root_mode=AdbRootMode.ROOTLESS
        )

    assert caught.value.code is InstallAdbErrorCode.LAUNCH_AMBIGUOUS
    assert len(fake.commands) == 4
    assert fake.closed


_X2I_DISPLAY = {
    "physical_pixels": {"width": 720, "height": 1440},
    "recommended": {"density": 216, "font_scale": 1},
    "privileged_sizing": False,
}


@pytest.mark.parametrize("outcome", ["applied", "refused", "unreachable"])
async def test_a_rootless_panel_gets_its_recommended_display_size_once_at_setup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=True)
    harness.display = _X2I_DISPLAY
    applied: list[tuple[AdbInstallTarget, DisplaySizing]] = []

    async def apply(
        target: AdbInstallTarget, signer: PythonRSASigner, sizing: DisplaySizing
    ) -> DisplayResult:
        harness.events.append("display_sizing")
        applied.append((target, sizing))
        if outcome == "unreachable":
            raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
        density = "216" if outcome == "applied" else "none"
        return DisplayResult(("none", "null"), density, "null", sizing)

    monkeypatch.setattr(install_executor, "async_apply_display_sizing", apply)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    # A completed sizing refusal is optional. A lost shell may still write,
    # so acceptance must stop before settlement or another app launch.
    assert completed.phase is (
        InstallPhase.RECOVERY_REQUIRED
        if outcome == "unreachable"
        else InstallPhase.HEALTHY_UNCLAIMED
    )
    assert [sizing for _, sizing in applied] == [DisplaySizing(216, 1.0)]
    assert applied[0][0].address.host == "192.168.250.23"
    assert harness.events.index("display_sizing") > harness.events.index("launch")
    if outcome == "unreachable":
        assert "display_settlement" not in harness.events
        assert harness.events.count("launch") == 1
        assert completed.health_checked_at is None


@pytest.mark.parametrize(
    "display",
    [
        None,
        {**_X2I_DISPLAY, "privileged_sizing": True},
        {"physical_pixels": {"width": 720, "height": 1440}},
    ],
)
async def test_no_display_size_is_applied_where_the_panel_sizes_itself_or_says_nothing(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, display: Any
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=True)
    harness.display = display
    apply = AsyncMock()
    monkeypatch.setattr(install_executor, "async_apply_display_sizing", apply)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    apply.assert_not_awaited()


class ShellySettlementHarness(Harness):
    """A native resume can stop PA until the post-settlement guarded launch."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.installed_bytes = artifact().apk_size
        self.pending = True
        self.stopped = False
        self.settled = False
        self.settlement_started = asyncio.Event()
        self.settlement_release = asyncio.Event()
        self.settlement_fault: str | None = None
        original_client = install_executor.HaPaneldClient
        harness = self

        class Client(original_client):
            async def async_get_health(self) -> PanelHealth:
                if harness.stopped:
                    raise CannotConnectError
                return await super().async_get_health()

        self.monkeypatch.setattr(install_executor, "HaPaneldClient", Client)

    async def async_settle_display(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> str:
        self.events.append("native_resume")
        self.settlement_started.set()
        await self.settlement_release.wait()
        if self.settlement_fault == "ambiguous":
            raise InstallAdbError(InstallAdbErrorCode.LAUNCH_AMBIGUOUS)
        if self.pending:
            self.stopped = True
            self.events.append("native_exit")
            self.pending = False
        self.settled = True
        self.events.append("native_settled")
        if self.settlement_fault == "bytes":
            self.installed_bytes = None
        elif self.settlement_fault == "credential":
            self.credential_wrong_at = self.credential_calls + 1
        elif self.settlement_fault == "pin":
            self.pin_wrong_at = self.pin_calls + 1
        elif self.settlement_fault == "health":
            self.expected_version = "9.9.9"
        elif self.settlement_fault == "launch":
            self.launch_outcome = LaunchOutcome.REFUSED
        return "settled"

    async def async_launch(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> LaunchOutcome:
        if self.launch_arguments:
            assert self.settled, "The final app launch must follow native settlement"
        outcome = await super().async_launch(
            target, signer, descriptor, expected_root_mode=expected_root_mode
        )
        self.stopped = False
        return outcome


@pytest.mark.parametrize("sizing_changed", [True, False])
@pytest.mark.parametrize("pending", [True, False])
async def test_shelly_success_requires_native_settlement_then_fresh_app_acceptance(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    sizing_changed: bool,
    pending: bool,
) -> None:
    """Equal sizing still consumes a historical restart before setup succeeds."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = ShellySettlementHarness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=True)
    harness.pending = pending
    harness.display = _X2I_DISPLAY

    async def apply(
        target: AdbInstallTarget, signer: PythonRSASigner, sizing: DisplaySizing
    ) -> DisplayResult:
        harness.events.append("display_sizing")
        previous = "none" if sizing_changed else "216"
        return DisplayResult((previous, "null"), "216", "null", sizing)

    monkeypatch.setattr(install_executor, "async_apply_display_sizing", apply)
    executor = InstallExecutor(hass, manager)
    waiter = asyncio.create_task(executor.async_wait(receipt.job_id))
    await harness.settlement_started.wait()
    provisional = await manager.async_get(receipt.job_id)
    assert provisional.phase is InstallPhase.HEALTH_CHECK
    assert provisional.health_checked_at is None
    assert harness.events.count("launch") == 1
    assert harness.health_calls == 1

    harness.settlement_release.set()
    completed = await waiter

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert completed.health_checked_at is not None
    assert harness.events.count("native_resume") == 1
    assert harness.events.count("launch") == 2
    assert harness.events.index("native_resume") > harness.events.index(
        "display_sizing"
    )
    assert harness.events.index("native_settled") < len(harness.events) - 1
    assert harness.events[-1] == "health"
    assert harness.health_calls == 2
    assert harness.home_proof_calls == 2
    assert harness.events.count("native_exit") == int(pending)
    assert not harness.stopped


@pytest.mark.parametrize(
    "fault", ["ambiguous", "bytes", "credential", "pin", "health", "launch"]
)
async def test_shelly_incomplete_settlement_or_changed_custody_never_accepts_setup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = ShellySettlementHarness(monkeypatch)
    harness.settlement_fault = fault
    harness.settlement_release.set()

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.failure_stage is InstallPhase.HEALTH_CHECK
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert completed.health_checked_at is None
    assert harness.events.count("native_resume") == 1
    assert harness.events.count("launch") == (2 if fault in {"health", "launch"} else 1)


async def test_interrupted_native_settlement_is_repeated_before_acceptance(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = ShellySettlementHarness(monkeypatch)
    executor = InstallExecutor(hass, manager)
    worker = await executor.async_ensure_job(receipt.job_id)
    assert worker is not None
    await harness.settlement_started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    current = await manager.async_get(receipt.job_id)
    assert current.phase is InstallPhase.HEALTH_CHECK
    assert current.health_checked_at is None
    assert harness.events.count("launch") == 1
    harness.settlement_release.set()

    restarted = InstallJobManager(hass)
    completed = await InstallExecutor(hass, restarted).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("native_resume") == 2
    assert harness.events.count("launch") == 2
    assert harness.health_calls == 3


async def test_other_vendor_settlement_bypasses_extra_launch_and_health(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("display_settlement") == 1
    assert harness.events.count("launch") == 1
    assert harness.health_calls == 1


async def test_failed_shelly_settlement_retries_without_reinstalling_exact_bytes(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = ShellySettlementHarness(monkeypatch)
    harness.settlement_fault = "ambiguous"
    harness.settlement_release.set()
    failed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    assert failed.phase is InstallPhase.RECOVERY_REQUIRED

    retry = await create_job(manager)
    harness = ShellySettlementHarness(monkeypatch)
    harness.target_installed = True
    harness.settlement_release.set()
    completed = await InstallExecutor(hass, manager).async_wait(retry.job_id)

    assert retry.job_id != failed.job_id
    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("native_resume") == 1
    assert harness.events.count("launch") == 2
    assert all(
        event not in harness.events for event in ("download", "stage", "install")
    )


@pytest.mark.parametrize("custody", ["credential", "pin"])
async def test_sizing_custody_loss_stops_before_display_or_native_mutation(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, custody: str
) -> None:
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=False, accepts_handover=True)

    async def recommended(panel: HaPaneldClient) -> DisplaySizing:
        if custody == "credential":
            harness.credential_wrong_at = harness.credential_calls + 1
        else:
            harness.pin_wrong_at = harness.pin_calls + 1
        return DisplaySizing(216, 1.0)

    apply = AsyncMock()
    monkeypatch.setattr(install_executor, "async_read_recommended_sizing", recommended)
    monkeypatch.setattr(install_executor, "async_apply_display_sizing", apply)
    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.health_checked_at is None
    assert "display_settlement" not in harness.events
    assert harness.events.count("launch") == 1
    apply.assert_not_awaited()


async def test_a_reinstall_over_a_set_up_panel_leaves_its_display_size_alone(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sizing happens at setup only: never fight an owner's later change."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.setup_state = PanelSetupState(complete=True, accepts_handover=True)
    harness.display = {
        "recommended": {"density": 200, "font_scale": 0.9},
        "privileged_sizing": False,
    }
    apply = AsyncMock()
    monkeypatch.setattr(install_executor, "async_apply_display_sizing", apply)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    apply.assert_not_awaited()

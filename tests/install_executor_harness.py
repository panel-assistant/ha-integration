"""The install executor's deterministic stand-ins, shared by its test modules."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.core import HomeAssistant

from custom_components.panel_assistant import install_executor, install_jobs
from custom_components.panel_assistant.adb_credentials import (
    AdbCredential,
    AdbCredentialError,
)
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbPreflight,
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
    InstallOutcome,
    LaunchOutcome,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelAddress,
    PanelHealth,
    PanelSetupState,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_adb import (
    DefiniteCleanupReason,
    StagedApk,
)
from custom_components.panel_assistant.install_artifacts import (
    ArtifactCustodyError,
)
from custom_components.panel_assistant.install_artifacts import (
    InstallArtifact as CustodiedArtifact,
)
from custom_components.panel_assistant.install_executor import (
    InstallExecutor,
)
from custom_components.panel_assistant.install_jobs import (
    InstallArtifact,
    InstallJobManager,
    InstallJobReceipt,
    InstallPhase,
    InstallTarget,
    install_plan_sha256,
)
from custom_components.panel_assistant.install_network import (
    InstallNetworkErrorCode,
    PinnedPanelTarget,
)
from custom_components.panel_assistant.release import InstallDescriptor, ReleaseArtifact
from custom_components.panel_assistant.status import PanelStatus

APK_SHA256 = "a" * 64
CREDENTIAL_ID = "b" * 64
OTHER_CREDENTIAL_ID = "c" * 64


def target() -> InstallTarget:
    """Return one literal-address target accepted by the durable authority."""
    return InstallTarget(
        address="panel-one.local",
        pinned_address="192.168.250.23",
        adb_serial="SERIAL-1",
        model="Test Panel",
        primary_abi="arm64-v8a",
        android_sdk=34,
    )


def artifact() -> InstallArtifact:
    """Return one fully authenticated frozen release contract."""
    return InstallArtifact(
        descriptor_schema="io.github.maxlyth.hapaneld.install.v1",
        release_tag="v0.1.0",
        version_name="0.1.0",
        version_code=100,
        apk_name="ha-paneld-v0.1.0-manual-setup-required.apk",
        apk_sha256=APK_SHA256,
        apk_size=12_345,
        package_id="io.github.maxlyth.hapaneld",
        signer_certificate_sha256=(
            "ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339"
        ),
        min_sdk=26,
        supported_abis=("arm64-v8a", "armeabi-v7a"),
        database_compatibility="hapaneld-db:v1:ha-paneld.db:1:14",
        launch_component="io.github.maxlyth.hapaneld/.MainActivity",
        protocol_min=5,
        protocol_max=5,
    )


@dataclass
class Harness:
    """Deterministic stand-ins with a complete side-effect call log."""

    monkeypatch: pytest.MonkeyPatch
    expected_version: str = "0.1.0"

    def __post_init__(self) -> None:
        self.events: list[str] = []
        self.credentials: list[AdbCredential] = []
        self.pin_arguments: list[PinnedPanelTarget] = []
        self.preflight_arguments: list[
            tuple[AdbInstallTarget, PythonRSASigner, InstallDescriptor]
        ] = []
        self.download_arguments: list[tuple[ReleaseArtifact, str]] = []
        self.local_cleanup_job_ids: list[str] = []
        self.stage_arguments: list[
            tuple[
                AdbInstallTarget,
                PythonRSASigner,
                InstallDescriptor,
                str,
                Path,
                AdbRootMode,
            ]
        ] = []
        self.install_arguments: list[
            tuple[
                AdbInstallTarget,
                PythonRSASigner,
                InstallDescriptor,
                str,
                AdbRootMode,
            ]
        ] = []
        self.remote_cleanup_arguments: list[
            tuple[
                AdbInstallTarget,
                PythonRSASigner,
                StagedApk,
                DefiniteCleanupReason,
                AdbRootMode,
            ]
        ] = []
        self.launch_arguments: list[
            tuple[
                AdbInstallTarget,
                PythonRSASigner,
                InstallDescriptor,
                AdbRootMode,
            ]
        ] = []
        self.health_client_addresses: list[object] = []
        self.mutation_barrier_arguments: list[tuple[str, int, InstallPhase]] = []
        self.cleanup_barrier_arguments: list[tuple[str, int, InstallPhase]] = []
        self.credential_calls = 0
        self.credential_wrong_at: int | None = None
        self.credential_error_at: int | None = None
        self.pin_error_at: int | None = None
        self.pin_error_code = InstallNetworkErrorCode.PINNED_TARGET_REMOVED
        self.pin_wrong_at: int | None = None
        self.pin_calls = 0
        self.preflight_calls = 0
        self.preflight_error: InstallAdbError | None = None
        self.preflight_wrong_at: int | None = None
        self.preflight_wrong_field = "serial"
        self.preflight_root_modes: list[AdbRootMode] = []
        # What the panel already holds, and whether each preflight was allowed
        # to report it. The size is what the installed APK measures when it is
        # this artifact byte for byte; None means it is not.
        self.target_installed = False
        self.installed_bytes: int | None = None
        self.preflight_admissions: list[bool] = []
        self.installed_size_arguments: list[
            tuple[AdbInstallTarget, InstallDescriptor, AdbRootMode]
        ] = []
        self.download_error: ArtifactCustodyError | None = None
        self.download_mutation: str | None = None
        self.local_cleanup_error: ArtifactCustodyError | None = None
        self.local_cleanup_error_at: int | None = None
        self.local_cleanup_entered: asyncio.Event | None = None
        self.local_cleanup_release: asyncio.Event | None = None
        self.stage_error: Exception | None = None
        self.stage_mismatch = False
        self.install_error: Exception | None = None
        self.install_outcome = InstallOutcome.INSTALLED
        self.cleanup_error: Exception | None = None
        self.launch_error: Exception | None = None
        self.launch_outcome = LaunchOutcome.STARTED
        self.health_error: Exception | None = None
        self.display: dict[str, Any] | None = None
        self.health_calls = 0
        self.home_ui_state: str | None = "setup"
        self.home_proof_calls = 0
        # Scripted `pkg=` answers, consumed in order; the last one repeats. A
        # string is that reply's reported package, an exception is raised.
        self.health_packages: list[Exception | str | None] = []
        # What the installed panel says about its own setup at the end of the
        # install, and every Home Assistant address it was then handed. Setup
        # reads as finished by default, so no handover happens unless a test
        # asks for one.
        self.setup_state = PanelSetupState(complete=True)
        self.handed_over_urls: list[str] = []
        self.stage_entered: asyncio.Event | None = None
        self.stage_release: asyncio.Event | None = None
        self.stage_callback: Any = None
        self._install()

    def _install(self) -> None:
        self.monkeypatch.setattr(
            install_executor,
            "async_get_clientsession",
            lambda _hass: object(),
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_get_durable_adb_credential",
            self.async_credential,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_revalidate_install_target",
            self.async_pin,
        )
        self.monkeypatch.setattr(
            install_executor, "async_preflight_install", self.async_preflight
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_installed_artifact_size",
            self.async_installed_size,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_download_install_artifact",
            self.async_download,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_cleanup_install_artifact",
            self.async_cleanup_local,
        )
        self.monkeypatch.setattr(install_executor, "async_stage_apk", self.async_stage)
        self.monkeypatch.setattr(
            install_executor,
            "async_install_staged_apk",
            self.async_install,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_cleanup_staged_apk",
            self.async_cleanup_remote,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_launch_installed_app",
            self.async_launch,
        )
        self.monkeypatch.setattr(
            install_executor,
            "async_settle_vendor_display",
            self.async_settle_display,
        )
        harness = self

        class FakeClient:
            """Return the harness's bounded health observation."""

            def __init__(self, _session: object, address: object) -> None:
                harness.events.append("health_client")
                harness.health_client_addresses.append(address)
                self.address = address

            async def async_get_display(self) -> dict[str, Any]:
                harness.events.append("display")
                if harness.display is None:
                    raise CannotConnectError
                return harness.display

            async def async_get_health(self) -> PanelHealth:
                harness.events.append("health")
                harness.health_calls += 1
                if harness.health_error is not None:
                    raise harness.health_error
                package: Exception | str | None = None
                if harness.health_packages:
                    index = min(
                        harness.health_calls - 1, len(harness.health_packages) - 1
                    )
                    package = harness.health_packages[index]
                if isinstance(package, Exception):
                    raise package
                return PanelHealth(
                    version=harness.expected_version,
                    panel_id="panel",
                    build="release",
                    config_hash="01234567",
                    package=package,
                )

            async def async_get_status(
                self, *, home_proof: bool = False
            ) -> PanelStatus:
                assert home_proof
                harness.home_proof_calls += 1
                return PanelStatus(
                    warning_count=0,
                    capability_count=0,
                    home_ui=(
                        {
                            "state": harness.home_ui_state,
                            "reason": "activity",
                            "evidence": "foreground",
                        }
                        if harness.home_ui_state is not None
                        else None
                    ),
                )

            async def async_get_setup_state(self) -> PanelSetupState:
                """Report setup finished, so no URL handover happens by default.

                A panel that reaches the end of an install is offered Home
                Assistant's address; the handover's own tests choose what this
                answers, and every other test here is about the install itself.
                """
                harness.events.append("setup_state")
                return harness.setup_state

            async def async_hand_over_ha_url(self, ha_url: str) -> None:
                harness.handed_over_urls.append(ha_url)

        self.monkeypatch.setattr(install_executor, "HaPaneldClient", FakeClient)

    async def async_credential(self, _hass: HomeAssistant) -> AdbCredential:
        self.events.append("credential")
        self.credential_calls += 1
        if self.credential_calls == self.credential_error_at:
            raise AdbCredentialError
        generation = (
            OTHER_CREDENTIAL_ID
            if self.credential_calls == self.credential_wrong_at
            else CREDENTIAL_ID
        )
        credential = AdbCredential(
            signer=cast(PythonRSASigner, object()),
            generation_id=generation,
        )
        self.credentials.append(credential)
        return credential

    async def async_pin(
        self, _hass: HomeAssistant, pinned: PinnedPanelTarget
    ) -> PinnedPanelTarget:
        self.events.append("pin")
        self.pin_arguments.append(pinned)
        self.pin_calls += 1
        if self.pin_calls == self.pin_error_at:
            raise install_executor.InstallNetworkError(self.pin_error_code)
        if self.pin_calls == self.pin_wrong_at:
            return PinnedPanelTarget(
                original=pinned.original,
                pinned=PanelAddress(host="192.168.250.99", port=8888),
            )
        return pinned

    async def async_preflight(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        admit_installed_target: bool = False,
    ) -> AdbPreflight:
        self.events.append("preflight")
        self.preflight_arguments.append((target, signer, descriptor))
        self.preflight_admissions.append(admit_installed_target)
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        if self.target_installed and not admit_installed_target:
            raise InstallAdbError(InstallAdbErrorCode.TARGET_NOT_CLEAN)
        root_mode = (
            self.preflight_root_modes.pop(0)
            if self.preflight_root_modes
            else AdbRootMode.ROOTLESS
        )
        wrong = self.preflight_calls == self.preflight_wrong_at
        return AdbPreflight(
            serial="OTHER"
            if wrong and self.preflight_wrong_field == "serial"
            else target.serial,
            model="Other"
            if wrong and self.preflight_wrong_field == "model"
            else target.model,
            primary_abi=(
                "armeabi-v7a"
                if wrong and self.preflight_wrong_field == "primary_abi"
                else target.primary_abi
            ),
            android_sdk=(
                target.android_sdk - 1
                if wrong and self.preflight_wrong_field == "android_sdk"
                else target.android_sdk
            ),
            root_mode=root_mode,
            target_installed=self.target_installed,
        )

    async def async_installed_size(
        self,
        target: AdbInstallTarget,
        _signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> int | None:
        self.events.append("installed_size")
        self.installed_size_arguments.append((target, descriptor, expected_root_mode))
        return self.installed_bytes

    async def async_download(
        self,
        _hass: HomeAssistant,
        _session: object,
        release: ReleaseArtifact,
        job_id: str,
    ) -> CustodiedArtifact:
        self.events.append("download")
        self.download_arguments.append((release, job_id))
        if self.download_error is not None:
            raise self.download_error
        return CustodiedArtifact(
            job_id="d" * 32 if self.download_mutation == "job_id" else job_id,
            path=(
                cast(str, Path(f"/private/{job_id}.apk"))
                if self.download_mutation == "path_type"
                else (
                    ""
                    if self.download_mutation == "path_empty"
                    else f"/private/{job_id}.apk"
                )
            ),
            size=(
                artifact().apk_size + 1
                if self.download_mutation == "size"
                else artifact().apk_size
            ),
            sha256=(
                OTHER_CREDENTIAL_ID
                if self.download_mutation == "sha256"
                else APK_SHA256
            ),
        )

    async def async_cleanup_local(self, _hass: HomeAssistant, job_id: str) -> None:
        self.events.append("local_cleanup")
        self.local_cleanup_job_ids.append(job_id)
        if self.local_cleanup_entered is not None:
            self.local_cleanup_entered.set()
        if self.local_cleanup_release is not None:
            await self.local_cleanup_release.wait()
        if self.local_cleanup_error is not None and (
            self.local_cleanup_error_at is None
            or len(self.local_cleanup_job_ids) == self.local_cleanup_error_at
        ):
            raise self.local_cleanup_error

    async def async_stage(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        job_id: str,
        path: Path,
        *,
        expected_root_mode: AdbRootMode,
    ) -> StagedApk:
        self.events.append("stage")
        self.stage_arguments.append(
            (target, signer, descriptor, job_id, path, expected_root_mode)
        )
        if self.stage_entered is not None:
            self.stage_entered.set()
        if self.stage_callback is not None:
            await self.stage_callback()
        if self.stage_release is not None:
            await self.stage_release.wait()
        if self.stage_error is not None:
            raise self.stage_error
        return StagedApk(
            job_id=job_id,
            remote_path=(
                "/data/local/tmp/ha-paneld-install-wrong.apk"
                if self.stage_mismatch
                else f"/data/local/tmp/ha-paneld-install-{job_id}.apk"
            ),
            apk_size=descriptor.apk_size,
            apk_sha256=descriptor.apk_sha256,
        )

    async def async_install(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        job_id: str,
        *,
        expected_root_mode: AdbRootMode,
        before_install,
    ) -> InstallOutcome:
        await before_install()
        self.events.append("install")
        self.install_arguments.append(
            (target, signer, descriptor, job_id, expected_root_mode)
        )
        if self.install_error is not None:
            raise self.install_error
        return self.install_outcome

    async def async_cleanup_remote(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        staged: StagedApk,
        reason: DefiniteCleanupReason,
        *,
        expected_root_mode: AdbRootMode,
    ) -> None:
        self.events.append(f"remote_cleanup:{reason.value}")
        self.remote_cleanup_arguments.append(
            (target, signer, staged, reason, expected_root_mode)
        )
        if self.cleanup_error is not None:
            raise self.cleanup_error

    async def async_launch(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> LaunchOutcome:
        self.events.append("launch")
        self.launch_arguments.append((target, signer, descriptor, expected_root_mode))
        if self.launch_error is not None:
            raise self.launch_error
        return self.launch_outcome

    async def async_settle_display(
        self,
        target: AdbInstallTarget,
        signer: PythonRSASigner,
        descriptor: InstallDescriptor,
        *,
        expected_root_mode: AdbRootMode,
    ) -> str:
        self.events.append("display_settlement")
        return "skipped"


async def run_job(
    hass: HomeAssistant,
    manager: InstallJobManager,
) -> InstallJobReceipt:
    """Run one job until the detached worker stops."""
    receipt = (await manager.async_list())[0]
    executor = InstallExecutor(hass, manager)
    return await executor.async_wait(receipt.job_id)


_REAL_STORE_PRESENCE = install_jobs._store_presence
_REAL_PARSE_STORE_DOCUMENT = install_jobs._parse_store_document
_REAL_PARSE_DETAIL_STORE_DOCUMENT = install_jobs._parse_detail_store_document


@pytest.fixture(autouse=True)
def emulate_home_assistant_store_file(
    hass_storage: dict[str, Any],
) -> Generator[None]:
    """Model the Store file hidden by HA's in-memory test storage manager."""

    def _presence(path: str) -> tuple[bool, bool]:
        exists, corrupt = _REAL_STORE_PRESENCE(path)
        if exists or corrupt:
            return exists, corrupt
        key = Path(path).name
        if key in {
            f"{DOMAIN}.install_jobs",
            f"{DOMAIN}.install_jobs.details",
        }:
            return key in hass_storage, False
        return False, False

    def _durable_reader(_path: str) -> dict[str, InstallJobReceipt]:
        document = hass_storage.get(f"{DOMAIN}.install_jobs")
        if document is None:
            raise install_jobs.InstallJobStoreError
        return _REAL_PARSE_STORE_DOCUMENT(
            json.dumps(document, separators=(",", ":")).encode("utf-8")
        )

    def _detail_reader(_path: str) -> Any:
        document = hass_storage.get(f"{DOMAIN}.install_jobs.details")
        if document is None:
            raise install_jobs.InstallJobStoreError
        return _REAL_PARSE_DETAIL_STORE_DOCUMENT(
            json.dumps(document, separators=(",", ":")).encode("utf-8")
        )

    with (
        patch.object(install_jobs, "_store_presence", side_effect=_presence),
        patch.object(install_jobs, "_read_durable_jobs", side_effect=_durable_reader),
        patch.object(install_jobs, "_read_durable_details", side_effect=_detail_reader),
    ):
        yield


async def create_job(
    manager: InstallJobManager,
    selected_artifact: InstallArtifact | None = None,
) -> InstallJobReceipt:
    """Create one durable approved receipt."""
    selected_target = target()
    selected_artifact = selected_artifact or artifact()
    receipt, created = await manager.async_create_or_join(
        selected_target,
        selected_artifact,
        install_plan_sha256(selected_target, selected_artifact, CREDENTIAL_ID),
        CREDENTIAL_ID,
    )
    assert created
    return receipt


async def seed_phase(
    hass: HomeAssistant, phase: InstallPhase
) -> tuple[InstallJobReceipt, InstallJobManager]:
    """Persist a phase, then return a new manager which has no memory claim."""
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    if phase is InstallPhase.APPROVED:
        return receipt, InstallJobManager(hass)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    phases = (
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.INSTALLED,
        InstallPhase.LAUNCHING,
        InstallPhase.HEALTH_CHECK,
    )
    for next_phase in phases:
        transition_fields: dict[str, object] = {}
        if next_phase is InstallPhase.ARTIFACT_READY:
            transition_fields["actual_apk_bytes"] = artifact().apk_size
        if next_phase is InstallPhase.DOWNLOADING:
            transition_fields["preflight_root_mode"] = AdbRootMode.ROOTLESS.value
        receipt = await manager.async_transition(
            receipt.job_id,
            receipt.revision,
            next_phase,
            **transition_fields,
        )
        if next_phase is phase:
            return receipt, InstallJobManager(hass)
    raise AssertionError

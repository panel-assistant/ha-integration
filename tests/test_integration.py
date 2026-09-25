"""Integration lifecycle, device and diagnostics tests."""

import asyncio
import json
import logging
from dataclasses import replace
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import (
    _async_reconcile_install_receipt,
    _async_resume_install_jobs,
    async_reload_entry,
)
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidResponseError,
    PanelHealth,
    PanelInstallStatus,
)
from custom_components.panel_assistant.const import (
    DOMAIN,
    INTEGRATION_BUILD,
    INTEGRATION_VERSION,
)
from custom_components.panel_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.panel_assistant.install_artifacts import (
    ArtifactCustodyError,
    ArtifactErrorCode,
)
from custom_components.panel_assistant.install_executor import (
    FinalizationOutcome,
    InstallExecutor,
    _FinalizerLease,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobRevisionError,
    InstallJobStoreError,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.status import PanelStatus, parse_status_response

HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    ha_state="normal",
    ha_source="mqtt",
)
BETA_HEALTH = PanelHealth(
    version="0.9.1",
    panel_id="beta",
    build="1001",
    config_hash="2a2b3c4d",
)
DISCOVERY_HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    ha_state="normal",
    ha_source="mqtt",
    discovery_id="a" * 64,
)
STATUS = PanelStatus(
    warning_count=2,
    capability_count=3,
    renderer={"mode": "builtin", "state": "rendered", "rendered": True},
    camera={"state": "absent", "live": False},
)


def _entry(hass: HomeAssistant, address: str = "panel.local") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: address},
    )
    entry.add_to_hass(hass)
    return entry


def _receipt(
    *,
    address: str = "panel.local",
    version: str = HEALTH.version,
    phase: InstallPhase = InstallPhase.HEALTHY_UNCLAIMED,
    package_id: str = LEGACY_PACKAGE_ID,
) -> SimpleNamespace:
    """Return the receipt fields used by setup reconciliation."""
    return SimpleNamespace(
        job_id="1" * 32,
        revision=7,
        phase=phase,
        target=SimpleNamespace(address=address),
        artifact=SimpleNamespace(version_name=version, package_id=package_id),
    )


def _installer_doubles(
    hass: HomeAssistant,
    receipts: tuple[SimpleNamespace, ...] = (),
    *,
    flow_owner: str | None = None,
) -> tuple[InstallExecutor, SimpleNamespace]:
    """Return the real process executor over an isolated receipt-manager double.

    Entry recovery belongs to the executor, so the executor is the unit under
    test; only the durable store is replaced.
    """

    async def _get(job_id: str) -> SimpleNamespace:
        return next(receipt for receipt in receipts if receipt.job_id == job_id)

    manager = SimpleNamespace(
        async_list=AsyncMock(return_value=receipts),
        async_get=AsyncMock(side_effect=_get),
        async_transition=AsyncMock(),
    )
    executor = InstallExecutor(hass, manager)  # type: ignore[arg-type]
    if flow_owner is not None:
        for receipt in receipts:
            executor._finalizers[receipt.job_id] = _FinalizerLease(flow_owner)
    return executor, manager


def _assert_healthy_entry_loaded(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Assert the existing entry, device, status sensor, and update entity remain."""
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.data.health == HEALTH
    entities = [
        item
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry.entry_id
    ]
    assert len(entities) == 3
    sensor = next(item for item in entities if item.unique_id.endswith("_status"))
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    assert state.state == "online"
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )


async def test_setup_entry_diagnostics_unload_reload(hass: HomeAssistant) -> None:
    """The vertical slice creates one device and unloads/reloads cleanly."""
    entry = _entry(hass)
    health_mock = AsyncMock(side_effect=[DISCOVERY_HEALTH, BETA_HEALTH])
    status_mock = AsyncMock(return_value=STATUS)
    operation_mock = AsyncMock(
        return_value=PanelInstallStatus(running=True, component="ha-paneld")
    )
    resume_mock = AsyncMock(return_value=())
    executor, manager = _installer_doubles(hass)

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status_mock,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_panel_install_status",
            operation_mock,
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            resume_mock,
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_entries = er.async_get(hass).entities.values()
        entities = [
            item for item in entity_entries if item.config_entry_id == entry.entry_id
        ]
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

        assert len(entities) == 3
        assert len(devices) == 1
        sensor = next(item for item in entities if item.unique_id.endswith("_status"))
        update = next(item for item in entities if item.domain == "update")
        state = hass.states.get(sensor.entity_id)
        assert state is not None
        assert state.state == "online"
        assert state.attributes["build"] == HEALTH.build
        # The panel's device names the Panel Assistant build it is connected
        # through, as a diagnostic entity beside its own Update entity.
        version = next(
            item
            for item in entities
            if item.unique_id == f"{entry.entry_id}_panel_assistant_version"
        )
        assert version.entity_category is EntityCategory.DIAGNOSTIC
        assert version.device_id == devices[0].id
        version_state = hass.states.get(version.entity_id)
        assert version_state is not None
        assert version_state.state == (
            f"{INTEGRATION_VERSION} (build {INTEGRATION_BUILD})"
        )
        update_state = hass.states.get(update.entity_id)
        assert update_state is not None
        assert devices[0].identifiers == {(DOMAIN, entry.entry_id)}
        assert devices[0].sw_version == HEALTH.version
        assert (
            entry.runtime_data.update_coordinator.data.operation
            == PanelInstallStatus(
                running=True,
                component="ha-paneld",
            )
        )

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["entry"][CONF_ADDRESS] == "**REDACTED**"
        assert diagnostics["last_update_success"] is True
        assert diagnostics["health"]["panel_id"] == "**REDACTED**"
        assert diagnostics["health"]["discovery_id"] == "**REDACTED**"
        assert diagnostics["health"]["build"] == HEALTH.build
        assert diagnostics["status"] == STATUS.as_dict()
        assert diagnostics["status_error"] is None
        assert health_mock.await_count == 1
        assert status_mock.await_count == 1

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED

        await async_reload_entry(hass, entry)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        reloaded_entities = [
            item
            for item in er.async_get(hass).entities.values()
            if item.config_entry_id == entry.entry_id
        ]
        reloaded_devices = dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        assert [item.id for item in reloaded_entities] == [item.id for item in entities]
        assert [item.id for item in reloaded_devices] == [item.id for item in devices]
        assert entry.runtime_data.coordinator.data.health.panel_id == "beta"
        assert (
            entry.runtime_data.update_coordinator.data.operation
            == PanelInstallStatus(
                running=True,
                component="ha-paneld",
            )
        )

    assert health_mock.await_count == 2
    assert status_mock.await_count == 2
    assert operation_mock.await_count == 2
    assert resume_mock.await_count == 2
    assert manager.async_list.await_count == 2


async def test_diagnostics_download_uses_privacy_safe_entry_filename(
    hass: HomeAssistant, hass_client
) -> None:
    """Diagnostics never expose the panel ID in a download filename."""
    private_panel_id = "private-bedroom-panel"
    entry = _entry(hass)
    health = PanelHealth(
        version=HEALTH.version,
        panel_id=private_panel_id,
        build=HEALTH.build,
        config_hash=HEALTH.config_hash,
        ha_state=HEALTH.ha_state,
        ha_source=HEALTH.ha_source,
    )
    executor, _manager = _installer_doubles(hass)

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert await async_setup_component(hass, "diagnostics", {})
        await hass.async_block_till_done()

        client = await hass_client()
        response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
        assert response.status == HTTPStatus.OK
        assert response.headers["Content-Disposition"] == (
            f'attachment; filename="config_entry-panel_assistant-{entry.entry_id}.json"'
        )
        assert private_panel_id not in response.headers["Content-Disposition"]
        payload = await response.json()
        assert payload["data"]["entry"][CONF_ADDRESS] == "**REDACTED**"
        assert payload["data"]["health"]["panel_id"] == "**REDACTED**"

        device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[
            0
        ]
        response = await client.get(
            f"/api/diagnostics/config_entry/{entry.entry_id}/device/{device.id}"
        )
        assert response.status == HTTPStatus.NOT_FOUND


async def test_setup_survives_install_job_resume_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt installer state cannot block an ordinary existing entry."""
    entry = _entry(hass)
    executor, _manager = _installer_doubles(hass)
    private_detail = "panel-secret.local"

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(side_effect=InstallJobStoreError(private_detail)),
        ) as resume_mock,
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.data.health == HEALTH
    resume_mock.assert_awaited_once_with(hass)
    assert private_detail not in caplog.text
    assert "Unable to resume durable ha-paneld install jobs" in caplog.text


async def test_setup_contains_unexpected_install_resume_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Unexpected installer faults cannot block an ordinary existing entry."""
    entry = _entry(hass)
    private_detail = "panel-secret.local"

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(side_effect=RuntimeError(private_detail)),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(side_effect=RuntimeError(private_detail)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    _assert_healthy_entry_loaded(hass, entry)
    assert private_detail not in caplog.text
    assert "Unable to resume durable ha-paneld install jobs" in caplog.text
    assert "Unable to reconcile a durable ha-paneld install receipt" in caplog.text


async def test_setup_propagates_cancelled_install_resume(
    hass: HomeAssistant,
) -> None:
    """Best-effort containment must not consume task cancellation."""
    with (
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _async_resume_install_jobs(hass)


async def test_setup_propagates_cancelled_install_reconciliation(
    hass: HomeAssistant,
) -> None:
    """Receipt reconciliation must not consume task cancellation."""
    entry = _entry(hass)

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_setup_survives_install_artifact_resume_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Artifact custody failure cannot block an ordinary existing entry."""
    entry = _entry(hass)
    executor, manager = _installer_doubles(hass)
    private_detail = "private-artifact-path"
    error = ArtifactCustodyError(ArtifactErrorCode.PATH_INVALID)
    error.args = (private_detail,)

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(side_effect=error),
        ) as resume_mock,
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    _assert_healthy_entry_loaded(hass, entry)
    resume_mock.assert_awaited_once_with(hass)
    manager.async_list.assert_awaited_once_with()
    assert private_detail not in caplog.text
    assert "Unable to resume durable ha-paneld install jobs" in caplog.text


async def test_setup_survives_install_receipt_store_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Post-refresh receipt corruption leaves platform setup available."""
    entry = _entry(hass)
    executor, manager = _installer_doubles(hass)
    private_detail = "192.168.1.44"
    manager.async_list.side_effect = InstallJobStoreError(private_detail)

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert any(
        item.config_entry_id == entry.entry_id
        for item in er.async_get(hass).entities.values()
    )
    assert private_detail not in caplog.text
    assert "Unable to reconcile a durable ha-paneld install receipt" in caplog.text


async def test_setup_survives_install_artifact_reconciliation_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Artifact cleanup failure cannot block a healthy existing entry."""
    entry = _entry(hass)
    private_detail = "private-artifact-path"
    error = ArtifactCustodyError(ArtifactErrorCode.PATH_INVALID)
    error.args = (private_detail,)

    with (
        caplog.at_level(logging.WARNING),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(side_effect=error),
        ) as executor_mock,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    _assert_healthy_entry_loaded(hass, entry)
    executor_mock.assert_awaited_once_with(hass)
    assert private_detail not in caplog.text
    assert "Unable to reconcile a durable ha-paneld install receipt" in caplog.text


async def test_artifact_resume_failure_does_not_hide_health_setup_failure(
    hass: HomeAssistant,
) -> None:
    """Installer containment leaves the coordinator's own answer unchanged."""
    entry = _entry(hass)
    status_mock = AsyncMock(return_value=STATUS)

    with (
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(side_effect=ArtifactCustodyError(ArtifactErrorCode.PATH_INVALID)),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status_mock,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not entry.runtime_data.coordinator.last_update_success
    assert entry.runtime_data.coordinator.data is None
    status_mock.assert_not_awaited()


async def _async_setup_with_installer(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    executor: InstallExecutor,
    health: PanelHealth = HEALTH,
) -> bool:
    """Set the entry up against one executor, its panel answering ``health``."""
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        loaded = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return loaded


async def test_setup_consumes_matching_healthy_install_receipt(
    hass: HomeAssistant,
) -> None:
    """A loaded entry completes the cross-store handoff with its actual ID."""
    entry = _entry(hass, "Panel.Local")
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,))

    assert await _async_setup_with_installer(hass, entry, executor)

    assert len(entry.entry_id) == 26
    manager.async_transition.assert_awaited_once_with(
        receipt.job_id,
        receipt.revision,
        InstallPhase.CONSUMED,
        result_code=InstallResultCode.ENTRY_CREATED,
        consumed_entry_id=entry.entry_id,
    )
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_setup_leaves_flow_owned_healthy_receipt_unconsumed(
    hass: HomeAssistant,
) -> None:
    """An active finalizer keeps ownership of entry creation handoff."""
    entry = _entry(hass)
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,), flow_owner="flow_one")

    assert await _async_setup_with_installer(hass, entry, executor)

    manager.async_transition.assert_not_awaited()
    assert executor._finalizers[receipt.job_id].owner == "flow_one"


async def test_setup_quarantines_matching_receipt_on_version_mismatch(
    hass: HomeAssistant,
) -> None:
    """Unexpected installed version requires recovery without blocking setup."""
    entry = _entry(hass)
    receipt = _receipt(version="9.9.9")
    executor, manager = _installer_doubles(hass, (receipt,))

    assert await _async_setup_with_installer(hass, entry, executor)

    assert entry.state is ConfigEntryState.LOADED
    manager.async_transition.assert_awaited_once_with(
        receipt.job_id,
        receipt.revision,
        InstallPhase.RECOVERY_REQUIRED,
        result_code=InstallResultCode.VERIFICATION_REQUIRED,
    )
    assert not executor.is_finalizer_active(receipt.job_id)


@pytest.mark.parametrize(
    ("receipt_package", "observed_package", "expected_phase"),
    [
        (SUCCESSOR_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, InstallPhase.CONSUMED),
        (SUCCESSOR_PACKAGE_ID, LEGACY_PACKAGE_ID, InstallPhase.RECOVERY_REQUIRED),
        (SUCCESSOR_PACKAGE_ID, None, InstallPhase.RECOVERY_REQUIRED),
        (LEGACY_PACKAGE_ID, None, InstallPhase.CONSUMED),
        (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, InstallPhase.RECOVERY_REQUIRED),
    ],
    ids=[
        "successor",
        "successor-receipt-legacy-app",
        "successor-receipt-silent-app",
        "legacy-predating-package-report",
        "legacy-receipt-successor-app",
    ],
)
async def test_setup_consumes_a_receipt_only_for_the_package_it_installed(
    hass: HomeAssistant,
    receipt_package: str,
    observed_package: str | None,
    expected_phase: InstallPhase,
) -> None:
    """Entry recovery applies the executor's package rule, not the version alone.

    Both apps are built from one tree, so the legacy app answering at the very
    version a successor receipt installed is not that install completing.
    """
    entry = _entry(hass)
    receipt = _receipt(package_id=receipt_package)
    executor, manager = _installer_doubles(hass, (receipt,))

    assert await _async_setup_with_installer(
        hass, entry, executor, replace(HEALTH, package=observed_package)
    )

    assert entry.state is ConfigEntryState.LOADED
    manager.async_transition.assert_awaited_once()
    assert manager.async_transition.await_args.args[2] is expected_phase


@pytest.mark.parametrize(
    ("observed_health", "expected_phase"),
    [
        (HEALTH, InstallPhase.CONSUMED),
        (BETA_HEALTH, InstallPhase.RECOVERY_REQUIRED),
    ],
)
async def test_setup_holds_finalizer_lease_through_receipt_transition(
    hass: HomeAssistant,
    observed_health: PanelHealth,
    expected_phase: InstallPhase,
) -> None:
    """A flow cannot acquire finalization during consume or quarantine."""
    entry = _entry(hass)
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,))
    transition_entered = asyncio.Event()
    allow_transition = asyncio.Event()

    async def _blocking_transition(*_args: object, **_kwargs: object) -> None:
        transition_entered.set()
        await allow_transition.wait()
        receipt.phase = expected_phase

    manager.async_transition.side_effect = _blocking_transition
    setup_task = hass.async_create_task(
        _async_setup_with_installer(hass, entry, executor, observed_health),
        "setup entry during finalizer race",
    )
    await asyncio.wait_for(transition_entered.wait(), timeout=1)

    busy = await executor.async_verify_finalization(receipt.job_id, "flow_one")
    assert busy.outcome is FinalizationOutcome.BUSY

    allow_transition.set()
    assert await setup_task

    assert entry.state is ConfigEntryState.LOADED
    assert receipt.phase is expected_phase
    assert not executor.is_finalizer_active(receipt.job_id)


@pytest.mark.parametrize(
    "failure",
    [InstallJobRevisionError(), RuntimeError("private-finalizer-detail")],
)
async def test_setup_survives_install_receipt_persistence_failure(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    """Finalization persistence failures cannot remove or block the entry."""
    entry = _entry(hass)
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,))
    manager.async_transition.side_effect = failure

    with caplog.at_level(logging.WARNING):
        assert await _async_setup_with_installer(hass, entry, executor)

    _assert_healthy_entry_loaded(hass, entry)
    assert not executor.is_finalizer_active(receipt.job_id)
    assert "private-finalizer-detail" not in caplog.text
    assert "Unable to reconcile a durable ha-paneld install receipt" in caplog.text


async def test_setup_propagates_cancelled_receipt_transition(
    hass: HomeAssistant,
) -> None:
    """Receipt reconciliation must not consume task cancellation."""
    entry = _entry(hass)
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,))
    manager.async_transition.side_effect = asyncio.CancelledError

    assert not await _async_setup_with_installer(hass, entry, executor)
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_setup_reconciliation_cancelled_mid_transition_releases_its_lease(
    hass: HomeAssistant,
) -> None:
    """Cancellation cannot strand setup's process-wide finalizer lease."""
    entry = _entry(hass)
    receipt = _receipt()
    executor, manager = _installer_doubles(hass, (receipt,))
    entered = asyncio.Event()

    async def _blocked_transition(*_args: object, **_kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    manager.async_transition.side_effect = _blocked_transition
    with patch(
        "custom_components.panel_assistant.async_get_install_executor",
        AsyncMock(return_value=executor),
    ):
        task = hass.async_create_task(
            _async_reconcile_install_receipt(
                hass, entry, receipt.target.address, HEALTH
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not executor.is_finalizer_active(receipt.job_id)
    verdict = await executor._async_acquire_finalizer(receipt.job_id, "flow_next")
    assert verdict is receipt


@pytest.mark.parametrize(
    "receipts",
    [
        (_receipt(address="other-panel.local"),),
        (_receipt(phase=InstallPhase.CONSUMED),),
    ],
)
async def test_setup_ignores_unrelated_or_terminal_install_receipts(
    hass: HomeAssistant, receipts: tuple[SimpleNamespace, ...]
) -> None:
    """Only the matching active handoff receipt can affect setup."""
    entry = _entry(hass)
    executor, manager = _installer_doubles(hass, receipts)

    assert await _async_setup_with_installer(hass, entry, executor)

    assert entry.state is ConfigEntryState.LOADED
    manager.async_get.assert_not_awaited()
    manager.async_transition.assert_not_awaited()
    assert not executor._finalizers


async def test_setup_loads_unavailable_when_panel_is_offline(
    hass: HomeAssistant,
) -> None:
    """A dead stored address loads the entry with nothing known about the panel.

    Only a loaded entry can accept the panel's session, which is what repairs
    the address, so the entry is not held in retry. Until either side answers
    the entities are unavailable, the card carries the entry's own name, and
    diagnostics say nothing has been read.
    """
    entry = _entry(hass)

    status_mock = AsyncMock(return_value=STATUS)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status_mock,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    status_mock.assert_not_awaited()
    assert entry.runtime_data.coordinator.data is None
    for entity_id in ("sensor.alpha_status", "update.alpha_ha_paneld_update"):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state == "unavailable"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "alpha"
    assert device.sw_version is None
    assert device.hw_version is None
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["last_update_success"] is False
    assert diagnostics["connected"] is False
    assert diagnostics["health"] is None
    assert diagnostics["status"] is None
    assert diagnostics["status_error"] is None
    assert "shadow" not in diagnostics["transport"]


@pytest.mark.parametrize("error_type", [CannotConnectError, InvalidResponseError])
async def test_coordinator_translates_client_failure(
    hass: HomeAssistant, error_type: type[CannotConnectError | InvalidResponseError]
) -> None:
    """Polling exposes a keyed error without interpolating client exception text."""
    entry = _entry(hass)
    status_mock = AsyncMock(return_value=STATUS)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status_mock,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    client_error = error_type("untrusted panel response detail")
    entry.runtime_data.client.async_get_health = AsyncMock(  # type: ignore[method-assign]
        side_effect=client_error
    )
    with pytest.raises(UpdateFailed) as raised:
        await entry.runtime_data.coordinator._async_update_data()
    assert raised.value.translation_domain == DOMAIN
    assert raised.value.translation_key == "health_update_failed"
    assert raised.value.translation_placeholders is None
    assert str(raised.value) == "Unable to read panel health"
    assert raised.value.__cause__ is client_error
    assert status_mock.await_count == 1


async def test_diagnostics_marks_cached_snapshot_after_health_failure(
    hass: HomeAssistant,
) -> None:
    """Diagnostics distinguish cached data after the current refresh fails."""
    entry = _entry(hass)
    executor, _manager = _installer_doubles(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entry.runtime_data.client.async_get_health = AsyncMock(  # type: ignore[method-assign]
        side_effect=CannotConnectError
    )
    await entry.runtime_data.coordinator.async_request_refresh()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["last_update_success"] is False
    assert diagnostics["health"]["panel_id"] == "**REDACTED**"
    assert diagnostics["health"]["build"] == HEALTH.build


@pytest.mark.parametrize(
    ("status_error", "diagnostic_error"),
    [
        (InvalidResponseError(), "invalid_response"),
        (CannotConnectError(), "unavailable"),
    ],
)
async def test_status_failure_does_not_override_health_authority(
    hass: HomeAssistant, status_error: Exception, diagnostic_error: str
) -> None:
    """Rejected optional diagnostics leave the existing health sensor online."""
    entry = _entry(hass)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=status_error),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entities = [
        item
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry.entry_id
    ]
    assert len(entities) == 3
    sensor = next(item for item in entities if item.unique_id.endswith("_status"))
    state = hass.states.get(sensor.entity_id)
    assert state is not None
    assert state.state == "online"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["status"] is None
    assert diagnostics["status_error"] == diagnostic_error
    assert diagnostics["health"]["build"] == HEALTH.build


async def test_huge_status_integer_does_not_override_health_authority(
    hass: HomeAssistant,
) -> None:
    """An integer conversion failure remains optional diagnostic failure."""
    entry = _entry(hass)

    async def _get_invalid_status(*, update_owner: bool = False) -> PanelStatus:
        return parse_status_response(
            json.dumps(
                {
                    "warnings": [],
                    "capabilities": [],
                    "camera": {"state": "live", "delivered_fps": 10**400},
                }
            )
        )

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=_get_invalid_status),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    _assert_healthy_entry_loaded(hass, entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["status"] is None
    assert diagnostics["status_error"] == "invalid_response"
    assert diagnostics["health"]["build"] == HEALTH.build


async def test_status_poll_claims_the_panel_update_while_its_entity_is_enabled(
    hass: HomeAssistant,
) -> None:
    """The panel is told to withhold its MQTT update only while ours is shown."""
    entry = _entry(hass)
    executor, _manager = _installer_doubles(hass)
    status_mock = AsyncMock(return_value=STATUS)
    health_mock = AsyncMock(return_value=HEALTH)

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            status_mock,
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator

        # The first poll runs before the update platform registers its entity.
        assert status_mock.await_args_list[0].kwargs == {"update_owner": False}

        await coordinator.async_refresh()
        assert status_mock.await_args.kwargs == {"update_owner": True}

        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "update", DOMAIN, f"{entry.entry_id}_update"
        )
        assert entity_id is not None
        registry.async_update_entity(
            entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        await coordinator.async_refresh()
        assert status_mock.await_args.kwargs == {"update_owner": False}

    # The health probe never claims anything.
    for call in health_mock.await_args_list:
        assert call.kwargs == {}


async def test_an_offline_setup_keeps_the_product_the_panel_named(
    hass: HomeAssistant,
) -> None:
    """A restart while the stored address is silent must not rename the product.

    Setup registers the card before anything has answered. The model and
    manufacturer the panel last reported stay; the Android release line goes.
    """
    entry = _entry(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Shelly",
        model="Wall Display X2i",
        sw_version="0.9.8-rc1",
        hw_version="Android 11 · RD2A.211001.002 release-keys",
    )
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(side_effect=CannotConnectError),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.manufacturer == "Shelly"
    assert device.model == "Wall Display X2i"
    assert device.sw_version == "0.9.8-rc1"
    assert device.hw_version is None


async def test_a_later_poll_brings_the_card_up_to_date(hass: HomeAssistant) -> None:
    """The card is written as the entities load; a later upgrade must reach it."""
    entry = _entry(hass)
    health_mock = AsyncMock(return_value=HEALTH)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        assert device is not None
        assert device.sw_version == HEALTH.version

        health_mock.return_value = replace(HEALTH, version="0.9.1", version_code=904)
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()

    device = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    assert device.sw_version == "0.9.1 (build 904)"

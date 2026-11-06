"""An interrupted restore the panel reports is raised as a Repair until it is not."""

import asyncio
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidResponseError,
    normalize_address,
    parse_panel_install_status,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.update_coordinator import PanelUpdateCoordinator

from .test_availability import _load
from .test_transport import DID, HEALTH, OTHER_DID, STATUS

INTERRUPTED = {
    "running": False,
    "component": "Restore",
    "message": (
        "Restore interrupted after the configuration was saved; "
        "restore the backup again"
    ),
    "presentation": {"code": "restore-partial", "params": {}},
    "result": {
        "status": "partial",
        "interrupted": True,
        "config": {
            "status": "partial",
            "items": 0,
            "detail": "interrupted after the configuration was saved",
        },
    },
}
PLAIN = {"running": False, "component": "", "message": ""}


@pytest.fixture(autouse=True)
def _panel_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep subsequent independent operation polls on the declared panel."""
    monkeypatch.setattr(
        HaPaneldClient, "async_get_health", AsyncMock(return_value=HEALTH)
    )
    monkeypatch.setattr(
        HaPaneldClient, "async_get_status", AsyncMock(return_value=STATUS)
    )


def _body(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode()


async def test_interrupted_restore_is_raised_kept_on_failed_read_and_cleared(
    hass: HomeAssistant, hass_admin_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer: dict[str, Any] = {"body": _body(INTERRUPTED)}

    async def install_status(self: HaPaneldClient) -> Any:
        if answer["body"] is None:
            raise CannotConnectError
        return parse_panel_install_status(answer["body"])

    monkeypatch.setattr(
        HaPaneldClient, "async_get_panel_install_status", install_status
    )
    entry = await _load(hass, hass_admin_user.id)
    coordinator = entry.runtime_data.update_coordinator
    issue_id = f"panel_restore_interrupted_{entry.entry_id}"

    def issue() -> Any:
        return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)

    raised = issue()
    assert raised is not None
    assert raised.severity is ir.IssueSeverity.WARNING
    assert not raised.is_fixable and not raised.is_persistent
    assert raised.translation_key == "panel_restore_interrupted"
    assert set(raised.translation_placeholders or {}) == {
        "panel",
        "install_url",
        "learn_more_url",
    }

    answer["body"] = None
    await coordinator.async_refresh()
    assert issue() is not None

    answer["body"] = b"not json"
    await coordinator.async_refresh()
    assert issue() is not None

    answer["body"] = _body(PLAIN)
    await coordinator.async_refresh()
    assert issue() is None

    answer["body"] = _body(INTERRUPTED)
    await coordinator.async_refresh()
    assert issue() is not None
    assert await hass.config_entries.async_remove(entry.entry_id)
    assert issue() is None


@pytest.mark.parametrize("interrupted", [False, True])
async def test_foreign_panel_cannot_change_restore_repair(
    hass: HomeAssistant,
    hass_admin_user: Any,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: bool,
) -> None:
    """A foreign endpoint cannot raise a warning or clear an existing one."""
    answer = {"body": PLAIN if interrupted else INTERRUPTED}

    async def install_status(self: HaPaneldClient) -> Any:
        return parse_panel_install_status(_body(answer["body"]))

    monkeypatch.setattr(
        HaPaneldClient, "async_get_panel_install_status", install_status
    )
    entry = await _load(hass, hass_admin_user.id, unique_id=DID)
    issue_id = f"panel_restore_interrupted_{entry.entry_id}"
    registry = ir.async_get(hass)
    assert (registry.async_get_issue(DOMAIN, issue_id) is not None) is (not interrupted)
    answer["body"] = INTERRUPTED if interrupted else PLAIN

    async def foreign_health(self: HaPaneldClient) -> Any:
        return replace(HEALTH, discovery_id=OTHER_DID)

    monkeypatch.setattr(HaPaneldClient, "async_get_health", foreign_health)
    await entry.runtime_data.coordinator.async_refresh()
    assert entry.runtime_data.coordinator.identity_mismatch
    await entry.runtime_data.update_coordinator.async_refresh()
    assert (registry.async_get_issue(DOMAIN, issue_id) is not None) is (not interrupted)
    # Operation progress remains independent of health admission.
    assert entry.runtime_data.update_coordinator.data.operation is not None


async def test_initial_foreign_panel_cannot_raise_restore_repair(
    hass: HomeAssistant, hass_admin_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup keeps the entry loaded while refusing the foreign warning."""
    monkeypatch.setattr(
        HaPaneldClient,
        "async_get_panel_install_status",
        AsyncMock(return_value=parse_panel_install_status(_body(INTERRUPTED))),
    )
    entry = await _load(hass, hass_admin_user.id, unique_id=OTHER_DID)
    assert entry.runtime_data.coordinator.identity_mismatch
    assert entry.runtime_data.update_coordinator.data.operation.restore_interrupted
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_restore_interrupted_{entry.entry_id}"
        )
        is None
    )


@pytest.mark.parametrize(
    "proof", ["unavailable", "invalid", "unidentified", "legacy", "retired_address"]
)
async def test_unproven_owner_keeps_repair_and_operation_progress(
    hass: HomeAssistant,
    hass_admin_user: Any,
    monkeypatch: pytest.MonkeyPatch,
    proof: str,
) -> None:
    """Health failure never turns working operation progress into a failed read."""
    operation = AsyncMock(return_value=parse_panel_install_status(_body(INTERRUPTED)))
    monkeypatch.setattr(HaPaneldClient, "async_get_panel_install_status", operation)
    entry = await _load(hass, hass_admin_user.id, unique_id=DID)
    if proof in {"unavailable", "invalid"}:
        health = AsyncMock(
            side_effect=CannotConnectError
            if proof == "unavailable"
            else InvalidResponseError
        )
    else:
        health = AsyncMock(
            return_value=replace(
                HEALTH,
                discovery_id=None if proof == "unidentified" else DID,
                installation_identity=proof != "legacy",
            )
        )
    monkeypatch.setattr(HaPaneldClient, "async_get_health", health)
    if proof == "retired_address":
        entry.runtime_data.client.address = normalize_address("192.0.2.9")
    progress = parse_panel_install_status(
        _body({"running": True, "component": "APK", "awaiting_confirmation": True})
    )
    operation.return_value = progress
    updates = entry.runtime_data.update_coordinator
    await updates.async_refresh()
    assert updates.data.operation == progress
    assert updates.data.error is None
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_restore_interrupted_{entry.entry_id}"
        )
        is not None
    )
    assert entry.runtime_data.coordinator.last_update_success


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("phase", ["health", "operation"])
@pytest.mark.parametrize(
    "retirement",
    ["address", "roundtrip", "client", "identity", "runtime", "foreign", "none"],
)
async def test_retired_owner_cannot_change_restore_repair(
    hass: HomeAssistant,
    hass_admin_user: Any,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: bool,
    phase: str,
    retirement: str,
) -> None:
    """Only an unchanged owner may reconcile an in-flight observation."""
    answer = {"body": PLAIN if interrupted else INTERRUPTED}
    started, release = asyncio.Event(), asyncio.Event()
    delay = False

    async def install_status(self: HaPaneldClient) -> Any:
        body = _body(answer["body"])
        if delay and phase == "operation":
            started.set()
            await release.wait()
        return parse_panel_install_status(body)

    async def health(self: HaPaneldClient) -> Any:
        if delay and phase == "health" and not started.is_set():
            started.set()
            await release.wait()
        return HEALTH

    monkeypatch.setattr(
        HaPaneldClient, "async_get_panel_install_status", install_status
    )
    entry = await _load(hass, hass_admin_user.id, unique_id=DID)
    monkeypatch.setattr(HaPaneldClient, "async_get_health", health)
    issue_id = f"panel_restore_interrupted_{entry.entry_id}"
    registry = ir.async_get(hass)
    answer["body"] = INTERRUPTED if interrupted else PLAIN
    delay = True
    runtime = entry.runtime_data
    refresh = asyncio.create_task(entry.runtime_data.update_coordinator.async_refresh())
    await started.wait()
    original_address = entry.data[CONF_ADDRESS]
    if retirement in {"address", "roundtrip"}:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: "192.0.2.9"}
        )
        if retirement == "roundtrip":
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_ADDRESS: original_address}
            )
    elif retirement == "client":
        entry.runtime_data.client.address = normalize_address("192.0.2.9")
    elif retirement == "identity":
        hass.config_entries.async_update_entry(entry, unique_id=OTHER_DID)
    elif retirement == "runtime":
        entry.runtime_data = replace(
            runtime,
            update_coordinator=PanelUpdateCoordinator(
                hass, runtime.client, entry.entry_id
            ),
        )
    elif retirement == "foreign":
        monkeypatch.setattr(
            HaPaneldClient,
            "async_get_health",
            AsyncMock(return_value=replace(HEALTH, discovery_id=OTHER_DID)),
        )
        await runtime.coordinator.async_refresh()
        assert runtime.coordinator.identity_mismatch
    await hass.async_block_till_done(wait_background_tasks=False)
    release.set()
    await refresh
    expected = interrupted if retirement == "none" else not interrupted
    assert (registry.async_get_issue(DOMAIN, issue_id) is not None) is expected
    delay = False
    if retirement == "identity":
        hass.config_entries.async_update_entry(entry, unique_id=DID)
    elif retirement == "client":
        entry.runtime_data.client.address = normalize_address(original_address)
    elif retirement == "runtime":
        entry.runtime_data = runtime
    elif retirement == "foreign":
        monkeypatch.setattr(
            HaPaneldClient, "async_get_health", AsyncMock(return_value=HEALTH)
        )
        await runtime.coordinator.async_refresh()
    await entry.runtime_data.update_coordinator.async_refresh()
    assert (registry.async_get_issue(DOMAIN, issue_id) is not None) is interrupted


async def test_retired_coordinator_cannot_withdraw_current_identity(
    hass: HomeAssistant, hass_admin_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Polling an old coordinator cannot reject the replacement runtime's panel."""
    monkeypatch.setattr(
        HaPaneldClient,
        "async_get_panel_install_status",
        AsyncMock(return_value=parse_panel_install_status(_body(PLAIN))),
    )
    entry = await _load(hass, hass_admin_user.id, unique_id=DID)
    retired = entry.runtime_data.update_coordinator
    current = PanelUpdateCoordinator(hass, retired.client, entry.entry_id)
    entry.runtime_data = replace(entry.runtime_data, update_coordinator=current)
    monkeypatch.setattr(
        HaPaneldClient,
        "async_get_health",
        AsyncMock(return_value=replace(HEALTH, discovery_id=OTHER_DID)),
    )
    await retired.async_refresh()
    assert not entry.runtime_data.coordinator.identity_mismatch
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_identity_mismatch_{entry.entry_id}"
        )
        is None
    )


@pytest.mark.parametrize(
    ("result", "running", "expected"),
    [
        ({"status": "failed", "interrupted": True}, False, True),
        ({"status": "partial", "interrupted": "true"}, False, False),
        ({"status": "partial", "interrupted": 1}, False, False),
        ({"status": "partial"}, False, False),
        ("interrupted", False, False),
        ({"interrupted": True}, True, False),
    ],
)
def test_only_a_finished_strict_true_counts_as_interrupted(
    result: Any, running: bool, expected: bool
) -> None:
    document = {"running": running, "component": "Restore", "result": result}
    assert parse_panel_install_status(_body(document)).restore_interrupted is expected

"""Missing supported grants stay actionable without interrupting the dashboard."""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidResponseError,
    PanelHealth,
    PanelInstallStatus,
    normalize_address,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import HaPaneldDataUpdateCoordinator
from custom_components.panel_assistant.failure_repair import (
    async_clear_update_failure_if_installed,
)
from custom_components.panel_assistant.status import parse_status_response
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import PanelUpdateCoordinator

HELD = {
    "notifications": "held",
    "write_settings": "held",
    "overlay": "held",
    "accessibility": "held",
    "microphone": "not_required",
    "camera": "held",
}
HEALTH = PanelHealth(
    version="0.9.9",
    panel_id="alpha",
    build="100",
    config_hash="abcd1234",
    discovery_id="a" * 64,
)


def _status(permissions: Any) -> Any:
    document = {"warnings": [], "capabilities": []}
    if permissions is not None:
        document["permissions"] = permissions
    return parse_status_response(json.dumps(document))


def _panel(
    hass: HomeAssistant,
) -> tuple[MockConfigEntry, HaPaneldDataUpdateCoordinator]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Kitchen display",
        unique_id=HEALTH.discovery_id,
        data={CONF_ADDRESS: "panel.local"},
    )
    entry.add_to_hass(hass)
    client = HaPaneldClient(object(), normalize_address("panel.local"))
    coordinator = HaPaneldDataUpdateCoordinator(hass, client, entry.entry_id)
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return entry, coordinator


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"panel_permissions_{entry.entry_id}"
    )


@pytest.mark.parametrize("permissions", [HELD, {"camera": "missing"}, {}])
def test_status_retains_only_actual_permission_observations(permissions: Any) -> None:
    assert _status(permissions).as_dict().get("permissions") == permissions


@pytest.mark.parametrize(
    "permissions",
    [
        None,
        {**HELD, "camera": "unreadable"},
        {"camera": "held"},
    ],
)
async def test_unknown_permissions_do_not_invent_a_missing_grant(
    hass: HomeAssistant,
    permissions: Any,
) -> None:
    entry, coordinator = _panel(hass)
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=_status(permissions)),
        ),
    ):
        await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert _issue(hass, entry) is None


@pytest.mark.parametrize(
    "permissions",
    [
        [],
        "held",
        {"camera": True},
        {"camera": "granted"},
        {**HELD, "camera": None},
    ],
)
def test_malformed_permission_observations_are_refused(permissions: Any) -> None:
    with pytest.raises(InvalidResponseError):
        _status(permissions)


@pytest.mark.parametrize("grant", sorted(HELD))
async def test_each_missing_supported_grant_creates_one_persistent_warning(
    hass: HomeAssistant,
    grant: str,
) -> None:
    entry, coordinator = _panel(hass)
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=_status({grant: "missing"})),
        ),
    ):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_persistent and issue.severity is ir.IssueSeverity.WARNING
    assert sum(domain == DOMAIN for domain, issue_id in ir.async_get(hass).issues) == 1


async def test_missing_grants_from_another_panel_create_no_warning(
    hass: HomeAssistant,
) -> None:
    entry, coordinator = _panel(hass)
    with (
        patch.object(
            coordinator.client,
            "async_get_health",
            AsyncMock(return_value=replace(HEALTH, discovery_id="b" * 64)),
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=_status({"camera": "missing"})),
        ),
    ):
        await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert _issue(hass, entry) is None


@pytest.mark.parametrize(
    "next_read",
    [
        None,
        {},
        {"camera": "held"},
        {**HELD, "camera": "unreadable"},
        "malformed",
        "unavailable",
        "wrong-panel",
    ],
)
async def test_a_new_running_build_cannot_clear_unresolved_grants(
    hass: HomeAssistant,
    next_read: Any,
) -> None:
    entry, coordinator = _panel(hass)
    health = AsyncMock(return_value=HEALTH)
    status = AsyncMock(return_value=_status({**HELD, "camera": "missing"}))
    with (
        patch.object(coordinator.client, "async_get_health", health),
        patch.object(
            coordinator.client,
            "async_get_status",
            status,
        ),
    ):
        await coordinator.async_refresh()
        issue = _issue(hass, entry)
        assert issue is not None
        assert issue.severity is ir.IssueSeverity.WARNING
        assert issue.is_persistent and issue.is_fixable
        assert issue.translation_placeholders == {"panel": "Kitchen display"}
        assert issue.data == {"entry_id": entry.entry_id}
        health.return_value = replace(HEALTH, version="0.9.10", build="101")
        if next_read == "wrong-panel":
            health.return_value = replace(health.return_value, discovery_id="b" * 64)
            status.return_value = _status(HELD)
        elif next_read == "malformed":
            status.side_effect = InvalidResponseError
        elif next_read == "unavailable":
            status.side_effect = CannotConnectError
        else:
            status.return_value = _status(next_read)
        await coordinator.async_refresh()
        await async_clear_update_failure_if_installed(
            hass,
            entry.entry_id,
            "0.9.10",
            101,
            verified_success=True,
        )
        assert _issue(hass, entry) is not None
        # A newly loaded coordinator must retain the persistent warning too.
        reloaded = HaPaneldDataUpdateCoordinator(
            hass, coordinator.client, entry.entry_id
        )
        await reloaded.async_refresh()
        assert _issue(hass, entry) is not None
        health.return_value = replace(HEALTH, version="0.9.10", build="101")
        status.side_effect = None
        status.return_value = _status(HELD)
        await reloaded.async_refresh()
        assert reloaded.last_update_success
        assert reloaded.data.health.version == "0.9.10"
        assert _issue(hass, entry) is None


@pytest.mark.parametrize("change", ["address", "identity", "removed"])
async def test_status_from_a_retired_entry_cannot_resolve_or_recreate_a_warning(
    hass: HomeAssistant,
    change: str,
) -> None:
    entry, coordinator = _panel(hass)
    status = AsyncMock(return_value=_status({**HELD, "camera": "missing"}))
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            status,
        ),
    ):
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None

        async def retired_status(**kwargs: Any) -> Any:
            if change == "removed":
                assert await hass.config_entries.async_remove(entry.entry_id)
                return _status({**HELD, "camera": "missing"})
            if change == "address":
                hass.config_entries.async_update_entry(
                    entry, data={CONF_ADDRESS: "new-panel.local"}
                )
            else:
                hass.config_entries.async_update_entry(entry, unique_id="b" * 64)
            return _status(HELD)

        status.side_effect = retired_status
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert (_issue(hass, entry) is None) is (change == "removed")


async def test_successful_panel_update_keeps_its_dashboard_and_permission_warning(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry, coordinator = _panel(hass)
    health = AsyncMock(return_value=HEALTH)
    document = {
        "warnings": [],
        "capabilities": [],
        "install_capability": "api",
        "permissions": {**HELD, "camera": "missing"},
        "home_ui": {"state": "ready", "reason": "dashboard", "evidence": "foreground"},
        "panel_assistant_update": {
            "state": "available",
            "current_version": HEALTH.version,
            "target_version": "0.9.10",
            "tag": "v0.9.10",
        },
    }

    async def installed(tag: str) -> None:
        assert tag == "v0.9.10"
        health.return_value = replace(HEALTH, version="0.9.10", build="101")
        document.pop("panel_assistant_update")

    with (
        patch.object(coordinator.client, "async_get_health", health),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(
                side_effect=lambda **kwargs: parse_status_response(json.dumps(document))
            ),
        ),
        patch.object(
            coordinator.client,
            "async_start_panel_update",
            AsyncMock(side_effect=installed),
        ),
        patch.object(
            coordinator.client,
            "async_get_panel_install_status",
            AsyncMock(
                return_value=PanelInstallStatus(running=False, component="ha-paneld")
            ),
        ),
    ):
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None
        updates = PanelUpdateCoordinator(hass, coordinator.client)
        await updates.async_refresh()
        entity = HaPaneldUpdateEntity(entry.entry_id, coordinator, updates)
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()
        # Poll the real coordinator immediately so the test exercises the
        # accepted observation rather than waiting on its scheduler cooldown.
        monkeypatch.setattr(
            coordinator, "async_request_refresh", coordinator.async_refresh
        )
        await entity.async_install(None, backup=False)
        assert entity.installed_version == "0.9.10"
        assert not entity.in_progress
        assert coordinator.available
        assert coordinator.data.status.home_ui["state"] == "ready"
        assert _issue(hass, entry) is not None
        await updates.async_shutdown()


@pytest.mark.parametrize(
    "next_read",
    [
        HELD,
        {**HELD, "camera": "missing"},
        None,
        "wrong-panel",
        "retired-address",
        "removed",
        "shutdown",
    ],
)
async def test_one_page_repair_requires_a_fresh_accepted_permission_read(
    hass: HomeAssistant,
    hass_client: Any,
    next_read: Any,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    entry, coordinator = _panel(hass)
    health = AsyncMock(return_value=HEALTH)
    status = AsyncMock(return_value=_status({**HELD, "camera": "missing"}))
    with (
        patch.object(coordinator.client, "async_get_health", health),
        patch.object(
            coordinator.client,
            "async_get_status",
            status,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(side_effect=AssertionError("manual Repair offered an ADB key")),
        ),
    ):
        await coordinator.async_refresh()
        assert _issue(hass, entry) is not None
        admin = await hass_client()
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={
                "handler": DOMAIN,
                "issue_id": f"panel_permissions_{entry.entry_id}",
            },
        )
        assert response.status == 200
        flow = await response.json()
        assert flow["type"] == "form"
        assert flow["step_id"] == "commission_permissions"
        assert flow["data_schema"] == []
        assert flow["description_placeholders"] == {"panel": "Kitchen display"}
        if next_read == "wrong-panel":
            health.return_value = replace(HEALTH, discovery_id="b" * 64)
            status.return_value = _status(HELD)
        elif next_read in ("retired-address", "removed"):

            async def retired_status(**kwargs: Any) -> Any:
                if next_read == "removed":
                    assert await hass.config_entries.async_remove(entry.entry_id)
                else:
                    hass.config_entries.async_update_entry(
                        entry, data={CONF_ADDRESS: "new-panel.local"}
                    )
                return _status(HELD)

            status.side_effect = retired_status
        elif next_read == "shutdown":
            coordinator.data = replace(coordinator.data, status=_status(HELD))
            await coordinator.async_shutdown()
        else:
            status.return_value = _status(next_read)
        response = await admin.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
        )
        result = await response.json()
        assert response.status == 200
        assert result["type"] == (
            "create_entry"
            if next_read == HELD
            else "abort"
            if next_read == "removed"
            else "form"
        )
        assert (_issue(hass, entry) is None) is (
            next_read == HELD or next_read == "removed"
        )
        if next_read != HELD and next_read != "removed":
            assert result["step_id"] == "commission_permissions"
            assert result["errors"]["base"]


@pytest.mark.parametrize(
    "language", ["en", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"]
)
async def test_permission_repair_has_actionable_translated_guidance(
    hass: HomeAssistant,
    language: str,
) -> None:
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.panel_permissions"
    assert "{panel}" in strings.get(f"{prefix}.title", "")
    description = strings[f"{prefix}.fix_flow.step.commission_permissions.description"]
    assert "{panel}" in description
    assert strings[f"{prefix}.fix_flow.error.permissions_missing"]
    assert strings[f"{prefix}.fix_flow.error.permissions_unreadable"]

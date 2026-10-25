"""Provider work belongs to an owner-started app bundle and its one retry Repair."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidResponseError,
)
from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    update_unique_id,
)
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.status import parse_status_response
from custom_components.panel_assistant.transport_binding import async_unbind_user
from custom_components.panel_assistant.webview import async_provision_webview

from .test_permission_repairs import HEALTH, _panel

pytest_plugins = ["tests.test_voice"]


def observation(state: str | None = None, *, eligible: bool = True) -> dict[str, Any]:
    """The panel's bounded additive native snapshot."""
    return {
        "schema": 1,
        "supported": True,
        "eligible": eligible,
        "reason": "current" if not eligible else None,
        "candidate": {"sha256": "c" * 64},
        "transaction": {"state": state, "request_id": "prior", "reason": None}
        if state
        else None,
    }


def status(value: dict[str, Any]) -> Any:
    return parse_status_response(
        json.dumps({"warnings": [], "capabilities": [], "webview": value})
    )


@pytest.mark.parametrize(
    "state", ["installed", "deferred", "rejected", "submitted_unknown"]
)
async def test_resumed_installer_never_replays_a_provider_transaction(
    state: str,
) -> None:
    client = SimpleNamespace(
        async_get_webview=AsyncMock(return_value=observation(state)),
        async_install_webview=AsyncMock(),
    )
    await async_provision_webview(client, "same-install")
    client.async_install_webview.assert_not_awaited()


@pytest.mark.parametrize("attempted", [False, True])
async def test_polling_recovers_attempts_into_one_combined_repair_without_installing(
    hass: HomeAssistant, attempted: bool
) -> None:
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(
                return_value=status(observation("deferred" if attempted else None))
            ),
        ),
        patch(
            "custom_components.panel_assistant.repairs_webview.async_manage",
            AsyncMock(),
        ) as manage,
    ):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        manage.assert_not_awaited()
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"panel_webview_{entry.entry_id}") is None
    assert (registry.async_get_issue(DOMAIN, issue_id) is not None) == attempted
    if attempted:
        events = await async_failure_events(hass, issue_id)
        assert len(events) == 1 and events[0]["webview_pending"]
        # A healthy app read cannot erase the unfinished provider component.
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, HEALTH.version, 100, verified_success=True
        )
        assert registry.async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize(
    "receipt,newer_offer,compatibility",
    [
        (receipt, offer, "bound")
        for receipt in [None, "submitted_unknown", "deferred"]
        for offer in [False, True, "behind"]
    ]
    + [(None, False, mode) for mode in ["unbound", "legacy", "unpaired"]],
)
async def test_combined_repair_retries_provider_without_reinstalling_current_app(
    hass: HomeAssistant,
    hass_client: Any,
    newer_offer: bool | str,
    receipt: str | None,
    compatibility: str,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    entity = er.async_get(hass).async_get_or_create(
        Platform.UPDATE,
        DOMAIN,
        update_unique_id(entry.entry_id),
        config_entry=entry,
        suggested_object_id="panel_app",
    )
    hass.states.async_set(
        entity.entity_id,
        "on" if newer_offer else "off",
        {
            "installed_version": "0.9.10 build 102",
            "latest_version": "0.9.10 build 103" if newer_offer else "0.9.10 build 102",
        },
    )
    if newer_offer == "behind":
        hass.states.async_set(
            entity.entity_id,
            "off",
            {
                "installed_version": "0.9.9 build 101",
                "latest_version": "0.9.9 build 101",
            },
        )
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10 build 102",
        RuntimeError("provider interrupted"),
        webview_pending=compatibility != "unpaired",
    )
    if compatibility in {"unbound", "unpaired"}:
        async_unbind_user(hass, entry)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    operations = []
    session = SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )

    async def manage(_hass: Any, _session: Any, op: str, **fields: Any) -> Any:
        operations.append(op)
        if op == "webview.observe":
            if compatibility == "legacy":
                raise InvalidResponseError
            return observation(receipt)
        assert fields["artifact_sha256"] == "c" * 64
        return observation("installed", eligible=False)

    service = AsyncMock()
    hass.services.async_register("update", "install", service)
    client = await hass_client()
    with (
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(
                return_value=parse_status_response(
                    '{"warnings": [], "capabilities": []}'
                )
                if compatibility == "legacy"
                else status(observation(receipt))
            ),
        ),
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        sessions.return_value.get.return_value = session
        response = await client.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
        )
        flow = await response.json()
        assert flow["type"] == "menu" and operations == []
        response = await client.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={"next_step_id": "retry"}
        )
        done = await response.json()
        if done["type"] == "progress":
            await hass.async_block_till_done()
            response = await client.post(
                f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
            )
            done = await response.json()
        if done["type"] == "progress_done":
            response = await client.post(
                f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
            )
            done = await response.json()
        service.assert_not_awaited()
    assert operations == (
        []
        if newer_offer == "behind" or compatibility in {"unbound", "unpaired"}
        else ["webview.observe"]
        + (
            []
            if receipt == "submitted_unknown" or compatibility == "legacy"
            else ["webview.install"]
        )
    )
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None) == (
        receipt == "submitted_unknown"
        or newer_offer == "behind"
        or compatibility == "unbound"
    )
    assert done["type"] == (
        "form"
        if receipt == "submitted_unknown"
        or newer_offer == "behind"
        or compatibility == "unbound"
        else "create_entry"
    )


@pytest.mark.parametrize("retire", [None, "remove", "unbind", "identity"])
async def test_bundle_observes_one_submission_through_reconnect_and_binding_retirement(
    hass: HomeAssistant, retire: str | None
) -> None:
    # This operation is invoked by both the update entity and combined Repair.
    from custom_components.panel_assistant.repairs_webview import async_update_webview

    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    current = [
        SimpleNamespace(
            did=entry.unique_id, user_id="owner", capabilities={"management"}
        )
    ]
    operations = []
    reads = 0

    async def manage(_hass: Any, _session: Any, op: str, **fields: Any) -> Any:
        nonlocal reads
        operations.append(op)
        if op == "webview.install":
            assert fields["artifact_sha256"] == "c" * 64
            return observation("installing")
        reads += 1
        if reads == 1:
            return observation()
        if reads == 2:
            if retire == "remove":
                await hass.config_entries.async_remove(entry.entry_id)
            elif retire == "unbind":
                async_unbind_user(hass, entry)
            elif retire == "identity":
                current[0] = SimpleNamespace(
                    did="b" * 64, user_id="owner", capabilities={"management"}
                )
            raise CannotConnectError
        return observation("installed", eligible=False)

    with (
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(observation())),
        ),
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
        patch(
            "custom_components.panel_assistant.repairs_webview.asyncio.sleep",
            AsyncMock(),
        ),
    ):
        sessions.return_value.get.side_effect = lambda _: current[0]
        result = await async_update_webview(hass, entry.entry_id)
    assert result == (None if retire is None else "webview_unavailable")
    assert operations.count("webview.install") == 1
    assert operations.count("webview.observe") == (3 if retire is None else 2)


@pytest.mark.parametrize("native_receipt", [False, True])
async def test_startup_restores_combined_repair_when_only_report_was_saved(
    hass: HomeAssistant, native_receipt: bool
) -> None:
    """A hard stop before IssueRegistry saves cannot hide the durable bundle."""
    from custom_components.panel_assistant.repairs_failure import (
        async_record_update_bundle,
    )
    from custom_components.panel_assistant.repairs_webview import (
        async_reconcile_webview_issue,
    )

    entry, _coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_bundle(hass, entry.entry_id, "0.9.10 build 102", None)
    saved = await async_failure_events(hass, issue_id)
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    hass.data.pop(f"{DOMAIN}.failure_repair_store")
    with patch(
        "custom_components.panel_assistant.repairs_webview.async_manage", AsyncMock()
    ) as manage:
        assert await async_setup_component(hass, DOMAIN, {})
        if native_receipt:
            await async_reconcile_webview_issue(
                hass, entry, status(observation("submitted_unknown"))
            )
        manage.assert_not_awaited()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.is_fixable
    assert await async_failure_events(hass, issue_id) == saved
    assert (
        saved[-1]["target_version"] == "0.9.10 build 102"
        and saved[-1]["webview_pending"]
    )

"""The WebView update and its Repair, against the panel's manage contract.

The stand-in panel answers as the app does: a ``manage`` request's arguments are
read only from its ``args`` object (``PanelAssistantTransportProtocol.manage``),
and ``webview.install`` without a request id and pinned hash is refused as an
invalid value. Panel Assistant's real ``async_manage`` builds every request.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    update_unique_id,
)
from custom_components.panel_assistant.repairs_failure import (
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.repairs_webview import (
    async_reconcile_webview_issue,
)
from custom_components.panel_assistant.transport_sessions import CommandOutcome

from .test_permission_repairs import _panel
from .test_webview import status
from .test_webview_update_outcomes import _app_current, _native

pytest_plugins = ["tests.test_voice"]

PIN = "c" * 64


def snapshot(
    state: str | None = None,
    *,
    reason: str | None = None,
    transaction_reason: str | None = None,
) -> dict[str, Any]:
    """The panel's WebView snapshot; a reason is its refusal of a new attempt."""
    return {
        "schema": 1,
        "supported": True,
        "eligible": reason is None,
        "reason": reason,
        "candidate": {"sha256": PIN},
        "transaction": None
        if state is None
        else {"state": state, "request_id": "prior", "reason": transaction_reason},
    }


class Panel:
    """A panel's manage endpoint for the WebView, with the app's argument contract."""

    def __init__(self, observed: dict[str, Any]) -> None:
        self.observed = observed
        self.installs: list[tuple[str, str]] = []
        self.ops: list[str] = []

    async def deliver(
        self,
        _hass: Any,
        _session: Any,
        _label: str,
        fields: dict[str, Any],
        _wait: float,
        *_args: Any,
        **_kwargs: Any,
    ) -> CommandOutcome:
        op = fields["op"]
        self.ops.append(op)
        args = fields.get("args") or {}
        if op == "webview.observe":
            return CommandOutcome("applied", None, self.observed)
        assert op == "webview.install"
        request_id, pin = args.get("request_id"), args.get("artifact_sha256")
        if not isinstance(request_id, str) or not isinstance(pin, str):
            # WebViewUpdate.install(null, null) answers 400 invalid_request.
            body = {**self.observed, "accepted": False, "reason": "invalid_request"}
            return CommandOutcome("refused", "invalid_value", body)
        assert pin == PIN
        self.installs.append((request_id, pin))
        # Download, verification and package replacement finish on the panel.
        self.observed = snapshot("installed", reason="current")
        return CommandOutcome("applied", None, {**self.observed, "accepted": True})


def _sessions(entry: Any) -> Any:
    session = SimpleNamespace(
        entry_id=entry.entry_id,
        did=entry.unique_id,
        user_id="owner",
        capabilities={"management"},
    )
    return SimpleNamespace(get=lambda _entry_id: session)


def _through_the_panel(panel: Panel, entry: Any) -> Any:
    sessions = _sessions(entry)
    return (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions",
            return_value=sessions,
        ),
        patch(
            "custom_components.panel_assistant.transport_sessions.async_get_sessions",
            return_value=sessions,
        ),
        patch(
            "custom_components.panel_assistant.transport_sessions.async_deliver",
            panel.deliver,
        ),
    )


async def _install(hass: HomeAssistant, entity: Any, entry: Any, panel: Panel) -> None:
    first, second, third = _through_the_panel(panel, entry)
    with first, second, third:
        await entity.async_install(None, backup=False)


async def test_webview_offered_alone_installs_on_the_panel(
    hass: HomeAssistant,
) -> None:
    """An owner whose app is current installs the offered WebView from HA."""
    entity, _client, entry, issue_id = _native(hass)
    _app_current(entity, snapshot())
    assert entity.latest_version == f"{entity.installed_version} + WebView"
    panel = Panel(snapshot())

    await _install(hass, entity, entry, panel)

    assert len(panel.installs) == 1
    assert panel.installs[0][0].startswith("pa-update-")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_app_update_installs_its_webview_on_the_panel(
    hass: HomeAssistant,
) -> None:
    """The app update's WebView leg finishes on the panel: no unfinished Repair."""
    entity, _client, entry, issue_id = _native(hass)
    panel = Panel(snapshot())

    await _install(hass, entity, entry, panel)

    entity._delivery.async_deliver_build.assert_awaited_once()
    assert panel.ops == ["webview.observe", "webview.install"]
    assert len(panel.installs) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_panel_refusing_the_webview_is_not_offered_it(
    hass: HomeAssistant,
) -> None:
    """A panel short of storage is not offered the WebView and raises no Repair."""
    entity, _client, entry, issue_id = _native(hass)
    refused = snapshot("rejected", reason="insufficient_storage")
    _app_current(entity, refused)
    assert entity.latest_version == entity.installed_version
    panel = Panel(refused)

    await async_reconcile_webview_issue(hass, entry, status(refused))
    # The app update itself still installs; its WebView leg finds nothing to do.
    entity2, _client2, entry2, issue_id2 = _native(hass)
    panel2 = Panel(refused)
    await _install(hass, entity2, entry2, panel2)

    assert panel.installs == [] and panel2.installs == []
    assert "webview.install" not in panel2.ops
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id2) is None


async def _retry(hass: HomeAssistant, hass_client: Any, issue_id: str) -> Any:
    client = await hass_client()
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    flow = await response.json()
    response = await client.post(
        f"/api/repairs/issues/fix/{flow['flow_id']}", json={"next_step_id": "retry"}
    )
    done = await response.json()
    for _ in range(2):
        if done["type"] not in ("progress", "progress_done"):
            break
        await hass.async_block_till_done()
        response = await client.post(
            f"/api/repairs/issues/fix/{flow['flow_id']}", json={}
        )
        done = await response.json()
    return done


async def _reported_unfinished_webview(hass: HomeAssistant) -> tuple[Any, Any, str]:
    """A panel on app 0.9.10 holding the reporters' unfinished WebView Repair."""
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
        "off",
        {
            "installed_version": "0.9.10 build 102",
            "latest_version": "0.9.10 build 102",
        },
    )
    await async_record_update_failure(
        hass,
        entry.entry_id,
        entry.title,
        "0.9.10 build 102",
        HomeAssistantError(
            "The app is installed; its WebView update is unfinished: webview_deferred"
        ),
        webview_pending=True,
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    hass.services.async_register("update", "install", AsyncMock())
    return entry, coordinator, issue_id


async def test_retry_finishes_the_reported_unfinished_webview(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """Try again on the reporters' Repair installs the WebView and clears it."""
    entry, coordinator, issue_id = await _reported_unfinished_webview(hass)
    panel = Panel(snapshot())
    first, second, third = _through_the_panel(panel, entry)
    with (
        first,
        second,
        third,
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(snapshot())),
        ),
    ):
        done = await _retry(hass, hass_client, issue_id)

    assert len(panel.installs) == 1
    assert done["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize(
    "refusal", ["insufficient_storage", "no_install_route", "signer_mismatch"]
)
async def test_retry_clears_a_repair_the_panel_cannot_act_on(
    hass: HomeAssistant, hass_client: Any, refusal: str
) -> None:
    """A panel that refuses the WebView leaves its owner no error to clear by hand."""
    entry, coordinator, issue_id = await _reported_unfinished_webview(hass)
    refused = snapshot("rejected", reason=refusal, transaction_reason="install_refused")
    panel = Panel(refused)
    first, second, third = _through_the_panel(panel, entry)
    with (
        first,
        second,
        third,
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(refused)),
        ),
    ):
        done = await _retry(hass, hass_client, issue_id)
        # Later polls do not raise it again.
        await async_reconcile_webview_issue(hass, entry, status(refused))

    assert panel.installs == []
    assert done["type"] == "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("submitted_unknown", "version_unknown"),
        ("submitted_unknown", "installer_unknown"),
        ("submitted_unknown", "insufficient_storage"),
        ("deferred", "receipt_unreadable"),
        ("rejected", "version_unknown"),
    ],
)
async def test_unproven_webview_outcome_keeps_its_repair(
    hass: HomeAssistant, hass_client: Any, state: str, reason: str
) -> None:
    """A panel that cannot read its provider, or an unconfirmed install, keeps Retry."""
    entry, coordinator, issue_id = await _reported_unfinished_webview(hass)
    unproven = snapshot(state, reason=reason, transaction_reason="reply_lost")
    panel = Panel(unproven)
    first, second, third = _through_the_panel(panel, entry)
    with (
        first,
        second,
        third,
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(unproven)),
        ),
    ):
        done = await _retry(hass, hass_client, issue_id)
        await async_reconcile_webview_issue(hass, entry, status(unproven))

    assert panel.installs == []
    assert done["type"] != "create_entry"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

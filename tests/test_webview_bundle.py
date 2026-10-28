"""The owner's existing app update carries its successor's optional provider pin."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidResponseError,
)
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import (
    ROUTE_ADB,
    ROUTE_SESSION,
    ROUTE_STAGED,
)

from .test_update_failure_repairs import _entity
from .test_webview import observation, status


@pytest.mark.parametrize(
    "route,feed",
    [
        (ROUTE_STAGED, False),
        (ROUTE_ADB, False),
        (ROUTE_STAGED, True),
        (ROUTE_SESSION, False),
    ],
)
@pytest.mark.parametrize(
    "outcome",
    [
        "installed",
        "submitted_unknown",
        "unknown_without_candidate",
        "cancel_app",
        "cancel_provider",
        "managed",
        "no_candidate",
        "current",
        "legacy",
    ],
)
async def test_owner_app_update_retains_and_finishes_successor_provider_bundle(
    hass: HomeAssistant, route: str, feed: bool, outcome: str
) -> None:
    entity, client = _entity(hass, feed=feed)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Panel",
        unique_id="a" * 64,
        data={CONF_ADDRESS: "panel.local", CONF_TRANSPORT_USER_ID: "owner"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(route, object(), object()))
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    operations = []
    submitted = False
    skipped = outcome in {
        "managed",
        "no_candidate",
        "current",
        "legacy",
        "unknown_without_candidate",
    }
    legacy_unavailable = outcome == "legacy" and route == ROUTE_SESSION
    pending = (
        outcome
        in {
            "submitted_unknown",
            "unknown_without_candidate",
            "cancel_app",
            "cancel_provider",
        }
        or legacy_unavailable
    )

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        # Persistence precedes the app restart, including cancellation with no
        # receipt; the Repair itself waits for a real failure or a restart.
        assert await async_failure_events(hass, issue_id)
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
        operations.append("app.install")
        if outcome == "cancel_app":
            raise asyncio.CancelledError
        current = entity.coordinator.data
        # Deliberately keep the predecessor status (which omitted WebView) cached.
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_adb = AsyncMock(side_effect=delivery)
    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)

    async def fresh_status() -> Any:
        assert operations == ["app.install", "webview.observe"]
        operations.append("successor.status")
        document = observation(eligible=outcome != "current")
        if outcome in {"managed", "no_candidate"}:
            document.update(
                supported=outcome != "managed", eligible=False, candidate=None
            )
        return (
            replace(status(document), webview=None)
            if outcome == "legacy"
            else status(document)
        )

    client.async_get_status = AsyncMock(
        side_effect=CannotConnectError if route == ROUTE_SESSION else fresh_status
    )

    async def manage(_hass: Any, _session: Any, operation: str, **fields: Any) -> Any:
        nonlocal submitted
        operations.append(operation)
        if operation == "webview.observe":
            if outcome == "legacy":
                raise InvalidResponseError
            if outcome == "unknown_without_candidate":
                # A durable receipt remains authoritative after its pin is retired.
                document = observation("submitted_unknown", eligible=False)
                document["candidate"] = None
                return document
            document = observation(eligible=outcome != "current")
            if outcome in {"managed", "no_candidate"}:
                document.update(
                    supported=outcome != "managed", eligible=False, candidate=None
                )
            return document
        submitted = True
        assert fields["artifact_sha256"] == "c" * 64
        if outcome == "cancel_provider":
            raise asyncio.CancelledError
        return observation(outcome, eligible=outcome != "installed")

    session = SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )
    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        sessions.return_value.get.return_value = session
        if outcome.startswith("cancel_"):
            with pytest.raises(asyncio.CancelledError):
                await entity.async_install(None, backup=False)
        elif (
            outcome in {"submitted_unknown", "unknown_without_candidate"}
            or legacy_unavailable
        ):
            with pytest.raises(HomeAssistantError):
                await entity.async_install(None, backup=False)
        else:
            await entity.async_install(None, backup=False)
    expected = [] if outcome == "cancel_app" else ["webview.observe"]
    if outcome == "legacy" and route != ROUTE_SESSION:
        expected += ["successor.status"]
    elif not skipped and outcome != "cancel_app":
        expected += ["webview.install"]
    assert operations == ["app.install", *expected]
    assert submitted == (outcome != "cancel_app" and not skipped)
    # A cancelled update keeps only its record; the next startup restores the
    # Repair from it.
    cancelled = outcome.startswith("cancel_")
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None) == (
        pending and not cancelled
    )
    if pending and not legacy_unavailable:
        events = await async_failure_events(hass, issue_id)
        assert events[-1]["webview_pending"] is True
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, "0.9.10", 102, verified_success=True
        )
        assert await async_failure_events(hass, issue_id)
    # The existing single operation owner is released even when either leg is cancelled.
    from custom_components.panel_assistant.panel_operations import (
        claim_panel_operation,
        release_panel_operation,
    )

    assert claim_panel_operation(hass, entry.entry_id)
    release_panel_operation(hass, entry.entry_id)


async def test_new_owner_update_replaces_an_older_pending_target(
    hass: HomeAssistant,
) -> None:
    entity, _client = _entity(hass, feed=True)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    await async_record_update_failure(
        hass, entry.entry_id, "Panel", "0.9.9 build 101", RuntimeError("old failure")
    )
    entity._delivery.async_deliver_build = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await entity.async_install(None, backup=False)
    events = await async_failure_events(hass, issue_id)
    assert events[-1]["target_version"] == "0.9.10 build 102"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_unpaired_app_update_does_not_create_provider_failure(
    hass: HomeAssistant,
    cancelled: bool,
) -> None:
    entity, _client = _entity(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))
    entity._delivery.async_deliver_build = AsyncMock(
        side_effect=asyncio.CancelledError if cancelled else None
    )
    with patch(
        "custom_components.panel_assistant.repairs_webview.async_manage", AsyncMock()
    ) as manage:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await entity.async_install(None, backup=False)
        else:
            await entity.async_install(None, backup=False)
        manage.assert_not_awaited()
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    if cancelled:
        assert not (await async_failure_events(hass, issue_id))[-1].get(
            "webview_pending"
        )
        await async_clear_update_failure_if_installed(
            hass, entry.entry_id, "0.9.10", None
        )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_provider_recovery_cannot_replace_an_in_progress_app_target(
    hass: HomeAssistant,
) -> None:
    from custom_components.panel_assistant import repairs_failure
    from custom_components.panel_assistant.repairs_webview import (
        async_reconcile_webview_issue,
    )

    entity, _client = _entity(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: "panel.local", CONF_TRANSPORT_USER_ID: "owner"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(coordinator=entity.coordinator)
    entity._entry_id = entry.entry_id
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))
    entity._delivery.async_deliver_build = AsyncMock(side_effect=asyncio.CancelledError)
    logs_started, release_logs = asyncio.Event(), asyncio.Event()

    async def logs(*_args: Any) -> list[str]:
        logs_started.set()
        await release_logs.wait()
        return []

    with patch.object(repairs_failure, "_async_panel_logs", logs):
        recovery = asyncio.create_task(
            async_reconcile_webview_issue(hass, entry, status(observation("deferred")))
        )
        await logs_started.wait()
        with pytest.raises(HomeAssistantError, match="busy"):
            await entity.async_install(None, backup=False)
        release_logs.set()
        await recovery
    with pytest.raises(asyncio.CancelledError):
        await entity.async_install(None, backup=False)
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    assert (await async_failure_events(hass, issue_id))[-1][
        "target_version"
    ] == "0.9.10"

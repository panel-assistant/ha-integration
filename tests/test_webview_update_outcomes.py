"""How the WebView leg of an owner-started app update ends, seen from its caller."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import repairs_webview
from custom_components.panel_assistant.client import CannotConnectError
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.repairs_failure import (
    async_clear_update_failure_if_installed,
    async_failure_events,
    async_record_update_failure,
    panel_failure_issue_id,
)
from custom_components.panel_assistant.update_route import ROUTE_STAGED

from .test_permission_repairs import HEALTH, _panel
from .test_update_failure_repairs import _entity
from .test_webview import observation, status

STRINGS = "custom_components/panel_assistant/strings.json"


def _native(
    hass: HomeAssistant, *, feed: bool = False
) -> tuple[Any, Any, MockConfigEntry, str]:
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
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)
    return entity, client, entry, panel_failure_issue_id(f"update:{entry.entry_id}")


def _session(entry: MockConfigEntry) -> Any:
    return SimpleNamespace(
        did=entry.unique_id, user_id="owner", capabilities={"management"}
    )


async def _install(
    hass: HomeAssistant,
    entity: Any,
    entry: MockConfigEntry,
    manage: Callable[..., Any],
    *,
    session: Callable[[], Any] | None = None,
) -> None:
    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
    ):
        if session is None:
            sessions.return_value.get.return_value = _session(entry)
        else:
            sessions.return_value.get.side_effect = lambda _id: session()
        await entity.async_install(None, backup=False)


async def test_failed_observation_repair_clears_once_the_panel_runs_the_target(
    hass: HomeAssistant,
) -> None:
    """Nothing was submitted, so reaching the target build ends the Repair."""
    entity, client, entry, issue_id = _native(hass)
    client.async_get_status.side_effect = ConnectionError("unreachable")

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        assert operation == "webview.observe"
        raise TimeoutError

    with pytest.raises(HomeAssistantError):
        await _install(hass, entity, entry, manage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    # The coordinator's health poll after the panel reconnected on the new build.
    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_current_code=102
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_submitted_webview_install_keeps_its_repair_through_the_poll(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("submitted_unknown")

    with pytest.raises(HomeAssistantError):
        await _install(hass, entity, entry, manage)
    events = await async_failure_events(hass, issue_id)
    assert events[-1].get("webview_pending") is True
    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_current_code=102
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize("route", ["native", "http", "submitted"])
async def test_webview_install_still_running_keeps_its_repair_through_the_poll(
    hass: HomeAssistant, route: str
) -> None:
    """WebView work still running when the step timed out remains pending.

    The panel was already installing (natively or seen over HTTP), or took the
    install this step submitted.
    """
    entity, client, entry, issue_id = _native(hass)
    reads = 0

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        nonlocal reads
        if operation == "webview.install":
            return observation("downloading")
        assert operation == "webview.observe"
        reads += 1
        if route == "http":
            raise ConnectionError("native read lost")
        if reads == 1:
            return observation(None if route == "submitted" else "downloading")
        raise TimeoutError

    client.async_get_status.return_value = status(observation("installing"))
    with pytest.raises(HomeAssistantError):
        await _install(hass, entity, entry, manage)
    events = await async_failure_events(hass, issue_id)
    assert events[-1].get("webview_pending") is True
    await async_clear_update_failure_if_installed(
        hass, entry.entry_id, "0.9.10", 102, verified_current_code=102
    )
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize("fails", [False, True])
async def test_running_update_shows_no_failure_repair(
    hass: HomeAssistant, fails: bool
) -> None:
    entity, _client, entry, issue_id = _native(hass)
    entity._async_install_route = AsyncMock(return_value=(ROUTE_STAGED, None, None))
    started, release = asyncio.Event(), asyncio.Event()

    async def delivery(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await release.wait()
        if fails:
            raise RuntimeError("delivery failed")
        current = entity.coordinator.data
        entity.coordinator.data = replace(
            current, health=replace(current.health, version="0.9.10", version_code=102)
        )

    entity._delivery.async_deliver_build = AsyncMock(side_effect=delivery)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        return observation(eligible=False)

    task = asyncio.create_task(_install(hass, entity, entry, manage))
    await started.wait()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    release.set()
    if fails:
        with pytest.raises(RuntimeError):
            await task
    else:
        await task
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None) == fails
    if not fails:
        # Nothing is left for a later startup to turn into a Repair.
        assert await async_failure_events(hass, issue_id) == []


@pytest.mark.parametrize("cause", [None, "not_found"])
async def test_unpublished_webview_download_leaves_the_app_update_complete(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, cause: str | None
) -> None:
    """HTTP 404, or an app too old to name a cause, means not published yet."""
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        document = observation("deferred")
        document["transaction"]["reason"] = "download_failed"
        if cause is not None:
            document["transaction"]["cause"] = cause
        return document

    await _install(hass, entity, entry, manage)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []
    assert any(
        record.levelname == "WARNING" and "c" * 64 in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("cause", "logged"),
    [
        ("unreachable", "panel-assistant.io could not be reached"),
        ("unresolved", "the name panel-assistant.io could not be resolved"),
        ("timed_out", "the download from panel-assistant.io timed out"),
        ("http_503", "panel-assistant.io answered HTTP 503"),
    ],
    ids=["unreachable", "unresolved", "timed_out", "http_503"],
)
async def test_blocked_webview_download_raises_the_repair_and_logs_its_cause(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, cause: str, logged: str
) -> None:
    """A file that exists but did not arrive is a Repair, not "unpublished"."""
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        document = observation("deferred")
        document["transaction"].update(reason="download_failed", cause=cause)
        return document

    with pytest.raises(HomeAssistantError) as raised:
        await _install(hass, entity, entry, manage)
    assert raised.value.translation_key == "update_webview_unfinished"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and "c" * 64 in record.getMessage()
    ]
    assert len(warnings) == 1
    assert logged in warnings[0]
    advice = "Allow Home Assistant to reach https://panel-assistant.io, then retry"
    assert advice in warnings[0]
    assert "not published" not in warnings[0]


async def _poll(hass: HomeAssistant, document: dict[str, Any], *, pending: bool) -> str:
    """One health poll of a native panel that reports this WebView document."""
    entry, coordinator = _panel(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT_USER_ID: "owner"}
    )
    issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
    if pending:
        await async_record_update_failure(
            hass,
            entry.entry_id,
            "Panel",
            HEALTH.version,
            RuntimeError("The app is installed; its WebView update is unfinished"),
            webview_pending=True,
        )
    with (
        patch.object(
            coordinator.client, "async_get_health", AsyncMock(return_value=HEALTH)
        ),
        patch.object(
            coordinator.client,
            "async_get_status",
            AsyncMock(return_value=status(document)),
        ),
        patch.object(
            coordinator.client,
            "async_get_version_code",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        await coordinator.async_refresh()
    return issue_id


@pytest.mark.parametrize("state", [None, "installed"])
async def test_poll_seeing_the_webview_current_clears_its_pending_repair(
    hass: HomeAssistant, state: str | None
) -> None:
    issue_id = await _poll(hass, observation(state, eligible=False), pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_poll_keeps_the_repair_while_the_webview_is_still_offered(
    hass: HomeAssistant,
) -> None:
    issue_id = await _poll(hass, observation("deferred"), pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


@pytest.mark.parametrize("cause", [None, "not_found"])
@pytest.mark.parametrize("pending", [False, True])
async def test_poll_after_an_unpublished_webview_download_raises_no_repair(
    hass: HomeAssistant, pending: bool, cause: str | None
) -> None:
    """The app update is complete; unpublished WebView files are only a WARNING."""
    document = observation("deferred")
    document["transaction"]["reason"] = "download_failed"
    if cause is not None:
        document["transaction"]["cause"] = cause
    issue_id = await _poll(hass, document, pending=pending)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_poll_after_a_blocked_webview_download_raises_the_repair(
    hass: HomeAssistant,
) -> None:
    """A panel that could not reach the file needs its owner, update or not."""
    document = observation("deferred")
    document["transaction"].update(reason="download_failed", cause="unreachable")
    issue_id = await _poll(hass, document, pending=False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


async def test_poll_with_no_webview_candidate_clears_an_interrupted_update(
    hass: HomeAssistant,
) -> None:
    """A panel with no WebView to install never reports `current`."""
    document = observation(eligible=False)
    document.update(reason="no_candidate", candidate=None)
    issue_id = await _poll(hass, document, pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_poll_unsure_how_a_webview_install_ended_keeps_the_update_report(
    hass: HomeAssistant,
) -> None:
    """A lost answer leaves the WebView outcome unknown, even with no candidate left."""
    document = observation("submitted_unknown", eligible=False)
    document.update(reason="no_candidate", candidate=None)
    issue_id = await _poll(hass, document, pending=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    # The report still names the build the update was sent to install.
    events = await async_failure_events(hass, issue_id)
    assert events and events[0].get("target_version") == HEALTH.version


async def test_running_webview_install_after_setup_raises_no_repair(
    hass: HomeAssistant,
) -> None:
    issue_id = await _poll(hass, observation("downloading"), pending=False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert await async_failure_events(hass, issue_id) == []


async def test_unfinished_webview_names_the_webview_and_the_installed_app(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("rejected")

    with pytest.raises(HomeAssistantError) as raised:
        await _install(hass, entity, entry, manage)
    assert raised.value.translation_key == "update_webview_unfinished"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update_webview"
    assert "issue=installer_failure_update" in (issue.learn_more_url or "")
    strings = json.loads(
        (Path(__file__).parents[1] / STRINGS).read_text(encoding="utf-8")
    )
    message = strings["exceptions"]["update_webview_unfinished"]["message"]
    text = strings["issues"]["installer_failure_update_webview"]
    for value in (
        message,
        text["title"],
        text["fix_flow"]["step"]["init"]["description"],
    ):
        assert "WebView" in value
    assert (
        "installed" in message
        and "installed" in text["fix_flow"]["step"]["init"]["description"]
    )


async def test_recovered_webview_attempt_names_the_webview_after_a_restart(
    hass: HomeAssistant,
) -> None:
    from custom_components.panel_assistant.repairs_failure import (
        async_restore_update_failure,
    )

    issue_id = await _poll(hass, observation("deferred"), pending=False)
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update_webview"
    assert "issue=installer_failure_update" in (issue.learn_more_url or "")
    entry_id = hass.config_entries.async_entries(DOMAIN)[0].entry_id
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    # Home Assistant starts again with only the saved report.
    await async_restore_update_failure(hass, entry_id)
    restored = registry.async_get_issue(DOMAIN, issue_id)
    assert restored is not None
    assert restored.translation_key == "installer_failure_update_webview"


async def test_plain_update_failure_keeps_the_update_wording(
    hass: HomeAssistant,
) -> None:
    _, _, entry, issue_id = _native(hass)
    await async_record_update_failure(
        hass, entry.entry_id, "Panel", "0.9.10", RuntimeError("delivery failed")
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_key == "installer_failure_update"


async def test_adb_authorization_update_repair_comes_back_as_itself_after_a_restart(
    hass: HomeAssistant,
) -> None:
    from custom_components.panel_assistant.repairs_failure import (
        async_restore_update_failure,
    )
    from custom_components.panel_assistant.update_errors import adb_authorization_error

    _, _, entry, issue_id = _native(hass)
    await async_record_update_failure(
        hass,
        entry.entry_id,
        "Panel",
        "0.9.10",
        adb_authorization_error(hass, entry.entry_id, "Panel"),
    )
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_adb_authorization"
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    # Home Assistant starts again with only the saved report.
    await async_restore_update_failure(hass, entry.entry_id)
    restored = registry.async_get_issue(DOMAIN, issue_id)
    assert restored is not None
    assert restored.translation_key == "installer_failure_adb_authorization"
    assert "adb_update_authorization" in (restored.translation_placeholders or {}).get(
        "approval_url", ""
    )


@pytest.mark.parametrize("kind", ["update", "retry_hold", "adb_authorization"])
async def test_update_repair_returns_through_fresh_setup_after_a_hard_stop(
    hass: HomeAssistant, kind: str
) -> None:
    from custom_components.panel_assistant.repairs_failure import (
        _DATA_KEY,
        RetrySafetyHold,
        async_record_retry_hold,
    )
    from custom_components.panel_assistant.update_errors import adb_authorization_error

    _, _, entry, issue_id = _native(hass)
    # Setup restores the report; the panel itself is not needed for that.
    await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    if kind == "retry_hold":
        await async_record_retry_hold(
            hass, issue_id, {"entry_id": entry.entry_id}, RetrySafetyHold("receipt")
        )
    else:
        error: Exception = (
            adb_authorization_error(hass, entry.entry_id, "Panel")
            if kind == "adb_authorization"
            else RuntimeError("delivery failed")
        )
        await async_record_update_failure(
            hass, entry.entry_id, "Panel", "0.9.10", error
        )
    registry = ir.async_get(hass)
    before = registry.async_get_issue(DOMAIN, issue_id)
    assert before is not None
    expected_key = {
        "update": "installer_failure_update",
        "retry_hold": "installer_failure_retry_hold",
        "adb_authorization": "installer_failure_adb_authorization",
    }[kind]
    assert before.translation_key == expected_key
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    # A hard stop loses the registry's delayed save and the in-memory report
    # cache; only the saved failure report remains.
    hass.data.pop(_DATA_KEY)
    hass.config.components.remove(DOMAIN)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    restored = registry.async_get_issue(DOMAIN, issue_id)
    assert restored is not None
    assert restored.translation_key == before.translation_key
    assert restored.data["key"] == issue_id
    assert restored.data["entry_id"] == entry.entry_id
    assert restored.translation_placeholders == before.translation_placeholders
    if kind == "adb_authorization":
        assert (
            "adb_update_authorization"
            in restored.translation_placeholders["approval_url"]
        )


class _NoPauseAsyncio:
    """asyncio as a module sees it, except that a pause returns at once."""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_seconds: float) -> None:
        await asyncio.sleep(0)


async def test_webview_step_waits_for_the_restarted_app_to_reconnect(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    entity, _client, entry, issue_id = _native(hass)
    # The one-second pause between session checks passes at once.
    monkeypatch.setattr(repairs_webview, "asyncio", _NoPauseAsyncio())
    answers = iter([None, None])
    sent: list[str] = []

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        sent.append(operation)
        if operation == "webview.observe":
            return observation()
        return observation("installed", eligible=False)

    await _install(
        hass, entity, entry, manage, session=lambda: next(answers, _session(entry))
    )
    assert sent == ["webview.observe", "webview.install"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unfinished_webview_still_restores_the_panel_permissions(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, _issue_id = _native(hass, feed=True)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("rejected")

    repair = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.update.async_repair_update_permissions",
            repair,
        ),
        pytest.raises(HomeAssistantError),
    ):
        await _install(hass, entity, entry, manage)
    await hass.async_block_till_done()
    repair.assert_awaited_once()


def _app_current(entity: Any, document: dict[str, Any] | None) -> None:
    """The panel already runs the offered app; its WebView may still be offered."""
    current = entity.coordinator.data
    entity.coordinator.data = replace(
        current,
        health=replace(current.health, version="0.9.10", version_code=102),
        status=status(document) if document is not None else current.status,
    )


async def test_app_updated_first_is_offered_the_webview_alone(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)
    _app_current(entity, observation())
    assert entity.latest_version == f"{entity.installed_version} + WebView"
    assert entity.state == "on"
    sent: list[tuple[str, Any]] = []

    async def manage(_hass: Any, _session: Any, operation: str, **fields: Any) -> Any:
        sent.append((operation, fields.get("args", {}).get("artifact_sha256")))
        if operation == "webview.observe":
            return observation()
        return observation("installed", eligible=False)

    await _install(hass, entity, entry, manage)
    assert sent == [("webview.observe", None), ("webview.install", "c" * 64)]
    entity._delivery.async_deliver_build.assert_not_awaited()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_panel_behind_the_app_release_is_offered_the_app_update(
    hass: HomeAssistant,
) -> None:
    """An offered WebView rides with the app update; it is never offered alone."""
    entity, _client, entry, _issue_id = _native(hass)
    current = entity.coordinator.data
    # The app update route stays; only the WebView offer is added.
    entity.coordinator.data = replace(
        current,
        status=replace(current.status, webview=status(observation()).webview),
    )
    assert entity.latest_version == "0.9.10"
    assert entity.installed_version != entity.latest_version

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("installed", eligible=False)

    await _install(hass, entity, entry, manage)
    entity._delivery.async_deliver_build.assert_awaited_once()


@pytest.mark.parametrize("kind", ["not_eligible", "no_candidate", "running"])
async def test_no_webview_offer_without_an_eligible_candidate(
    hass: HomeAssistant, kind: str
) -> None:
    entity, _client, _entry, _issue_id = _native(hass)
    document = observation("downloading" if kind == "running" else None)
    if kind == "not_eligible":
        document = observation(eligible=False)
    elif kind == "no_candidate":
        # Eligible, but with nothing named to install.
        document.update(candidate=None)
    _app_current(entity, document)
    assert entity.latest_version == entity.installed_version
    assert entity.state == "off"


async def test_failed_webview_only_update_raises_the_webview_repair(
    hass: HomeAssistant,
) -> None:
    entity, _client, entry, issue_id = _native(hass)
    _app_current(entity, observation())

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        if operation == "webview.observe":
            return observation()
        return observation("rejected")

    with pytest.raises(HomeAssistantError) as raised:
        await _install(hass, entity, entry, manage)
    assert raised.value.translation_key == "update_webview_unfinished"
    entity._delivery.async_deliver_build.assert_not_awaited()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "installer_failure_update_webview"


async def test_webview_offer_does_not_answer_a_request_for_another_build(
    hass: HomeAssistant,
) -> None:
    """A specific other build goes through the feed, which refuses an unknown one."""
    entity, _client, entry, _issue_id = _native(hass, feed=True)
    _app_current(entity, observation())
    assert entity.latest_version.endswith(" + WebView")
    sent: list[str] = []

    async def manage(_hass: Any, _session: Any, operation: str, **_: Any) -> Any:
        sent.append(operation)
        if operation == "webview.observe":
            return observation()
        return observation("installed", eligible=False)

    with (
        patch(
            "custom_components.panel_assistant.repairs_webview.async_get_sessions"
        ) as sessions,
        patch("custom_components.panel_assistant.repairs_webview.async_manage", manage),
        pytest.raises(HomeAssistantError) as raised,
    ):
        sessions.return_value.get.return_value = _session(entry)
        await entity.async_install("0.9.11 build 999", backup=False)
    assert raised.value.translation_key == "update_unavailable"
    assert sent == []
    entity._delivery.async_deliver_build.assert_not_awaited()

"""The Repairs issue for a panel whose move to the new app has not finished."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownStep
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant import repairs
from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidResponseError,
    PanelHealth,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.migration_repair import (
    ISSUE_PANEL_MIGRATION_INCOMPLETE,
    async_delete_panel_migration_incomplete,
    async_raise_panel_migration_incomplete,
    panel_migration_issue_id,
)

JOB_ID = "0" * 32
ADDRESS = "192.168.250.23"
VERSION = "0.9.8"
LEGACY = "io.github.maxlyth.hapaneld"
SUCCESSOR = "io.panelassistant.android"


def _health(package: str | None, version: str = VERSION) -> PanelHealth:
    return PanelHealth(
        version=version,
        panel_id="panel",
        build="release",
        config_hash="01234567",
        package=package,
    )


def _install_client(
    monkeypatch: pytest.MonkeyPatch, answer: PanelHealth | Exception
) -> list[object]:
    """Stand in for the one read this flow makes, and record what it asked."""
    asked: list[object] = []

    class FakeClient:
        def __init__(self, _session: object, address: object) -> None:
            asked.append(address)

        async def async_get_health(self) -> PanelHealth:
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(repairs, "HaPaneldClient", FakeClient)
    monkeypatch.setattr(repairs, "async_get_clientsession", lambda _hass: object())
    return asked


@pytest.fixture
async def issues(hass: HomeAssistant) -> None:
    """Load Repairs, whose fix flow views are the only way into this flow."""
    assert await async_setup_component(hass, "repairs", {})
    # Core only asks an integration for its own fix flow once that integration
    # has been loaded and its repairs platform registered.
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def _start(client: Any, issue_id: str) -> tuple[int, dict[str, Any]]:
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    body: dict[str, Any] = await response.json() if response.status == 200 else {}
    return response.status, body


async def _submit(client: Any, flow_id: str) -> dict[str, Any]:
    response = await client.post(f"/api/repairs/issues/fix/{flow_id}", json={})
    assert response.status == 200, await response.text()
    body: dict[str, Any] = await response.json()
    return body


def test_the_issue_is_named_after_the_job_that_raised_it() -> None:
    """One handover, one issue: a second install never overwrites the first."""
    assert panel_migration_issue_id(JOB_ID) == (
        f"{ISSUE_PANEL_MIGRATION_INCOMPLETE}_{JOB_ID}"
    )
    assert panel_migration_issue_id("other") != panel_migration_issue_id(JOB_ID)


async def test_the_report_carries_what_a_person_needs_to_recognise_the_panel(
    hass: HomeAssistant, issues: None
) -> None:
    """The address and version are shown; nothing private travels with them."""
    async_raise_panel_migration_incomplete(hass, JOB_ID, ADDRESS, VERSION)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, panel_migration_issue_id(JOB_ID))

    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_PANEL_MIGRATION_INCOMPLETE
    assert issue.translation_placeholders == {"address": ADDRESS, "version": VERSION}
    assert issue.data == {"address": ADDRESS, "version": VERSION}

    async_delete_panel_migration_incomplete(hass, JOB_ID)

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, panel_migration_issue_id(JOB_ID))
        is None
    )


async def test_the_new_app_answering_for_itself_closes_the_issue(
    hass: HomeAssistant,
    issues: None,
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking again is the whole fix: the panel did the work by itself."""
    asked = _install_client(monkeypatch, _health(SUCCESSOR))
    async_raise_panel_migration_incomplete(hass, JOB_ID, ADDRESS, VERSION)
    admin = await hass_client()

    status, form = await _start(admin, panel_migration_issue_id(JOB_ID))

    assert status == 200
    assert form["step_id"] == "confirm_migration"
    assert form["description_placeholders"] == {
        "address": ADDRESS,
        "version": VERSION,
    }

    result = await _submit(admin, form["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert [getattr(address, "stored_value", address) for address in asked] == [ADDRESS]
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, panel_migration_issue_id(JOB_ID))
        is None
    )


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(_health(LEGACY), id="old-app-still-answering"),
        pytest.param(_health(None), id="no-package-reported"),
        pytest.param(_health(SUCCESSOR, version="0.9.7"), id="another-version"),
        pytest.param(CannotConnectError(), id="nothing-answering"),
        pytest.param(InvalidResponseError(), id="unreadable-answer"),
    ],
)
async def test_an_unfinished_handover_says_so_and_keeps_the_issue(
    hass: HomeAssistant,
    issues: None,
    hass_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    answer: PanelHealth | Exception,
) -> None:
    """Nothing here touches the panel, so the issue has to survive the check."""
    _install_client(monkeypatch, answer)
    async_raise_panel_migration_incomplete(hass, JOB_ID, ADDRESS, VERSION)
    admin = await hass_client()

    _status, form = await _start(admin, panel_migration_issue_id(JOB_ID))
    print("FORM", form)
    result = await _submit(admin, form["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "migration_unfinished"
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, panel_migration_issue_id(JOB_ID))
        is not None
    )


async def test_an_issue_without_its_own_facts_opens_no_flow(
    hass: HomeAssistant, issues: None
) -> None:
    """A flow that cannot name one exact panel must not be offered at all."""
    for data in (None, {}, {"address": ADDRESS}, {"address": 8888, "version": VERSION}):
        with pytest.raises(UnknownStep):
            await repairs.async_create_fix_flow(
                hass,
                panel_migration_issue_id(JOB_ID),
                data,  # type: ignore[arg-type]
            )


async def test_a_bad_address_in_a_stored_issue_never_reaches_the_network(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored value is re-parsed, so a corrupted issue fails closed."""
    asked = _install_client(monkeypatch, _health(SUCCESSOR))
    flow = repairs.PanelMigrationFlow("not an address", VERSION)
    flow.hass = hass

    assert await flow._async_successor_answered() is False
    assert asked == []

"""One shown identity repair preserves exactly the listed panel setups."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.identity import ISSUE_IDENTITY, accept_health
from custom_components.panel_assistant.repairs import async_create_fix_flow

from .test_installation_identity import _health


def _candidate(hass, index):
    legacy, target = str(index) * 64, chr(97 + index) * 64
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Panel {index}",
        unique_id=legacy,
        data={"address": f"192.0.2.{index}"},
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    entity = er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, f"{legacy}_relay1", config_entry=entry, device_id=device.id
    )
    health = _health(target, legacy)
    assert not accept_health(hass, entry, health)
    return entry, entity, health


async def _open(hass, entry):
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_IDENTITY}_{entry.entry_id}"
    )
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass = hass
    shown = await flow.async_step_init()
    return flow, shown


async def test_one_confirmation_keeps_all_listed_setups_and_excludes_new_requests(hass):
    first, second = [_candidate(hass, i) for i in (1, 2)]
    flow, shown = await _open(hass, first[0])
    assert "Panel 1" in shown["description_placeholders"]["panels"]
    assert "Panel 2" in shown["description_placeholders"]["panels"]
    later = _candidate(hass, 3)
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=[first[2], second[2]]),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        result = await flow.async_step_confirm_identity({})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for entry, entity, health in (first, second):
        assert entry.unique_id == health.discovery_id
        updated = er.async_get(hass).async_get(entity.entity_id)
        assert (updated.id, updated.device_id) == (entity.id, entity.device_id)
        assert updated.unique_id == f"{health.discovery_id}_relay1"
        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, f"{ISSUE_IDENTITY}_{entry.entry_id}"
            )
            is None
        )
    assert later[0].unique_id == "3" * 64


@pytest.mark.parametrize(
    "change", ["address", "target", "unavailable", "registry_conflict"]
)
async def test_changed_or_unavailable_member_prevents_any_batch_adoption(hass, change):
    first, second = [_candidate(hass, i) for i in (1, 2)]
    flow, _ = await _open(hass, first[0])
    calls = 0

    async def health(self):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "address":
                hass.config_entries.async_update_entry(
                    first[0], data={"address": "192.0.2.99"}
                )
            elif change == "target":
                accept_health(hass, first[0], _health("f" * 64, "1" * 64))
            elif change == "unavailable":
                from custom_components.panel_assistant.client import CannotConnectError

                raise CannotConnectError
            else:
                other = MockConfigEntry(
                    domain=DOMAIN, unique_id="f" * 64, data={"address": "elsewhere"}
                )
                other.add_to_hass(hass)
                er.async_get(hass).async_get_or_create(
                    "switch",
                    DOMAIN,
                    f"{second[2].discovery_id}_relay1",
                    config_entry=other,
                )
        return (first, second)[calls - 1][2]

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            health,
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        result = await flow.async_step_confirm_identity({})
        await hass.async_block_till_done()
    assert result["type"] in (FlowResultType.ABORT, FlowResultType.FORM)
    assert first[0].unique_id == "1" * 64
    assert second[0].unique_id == "2" * 64


async def test_snapshot_excludes_stale_issues_and_refuses_duplicate_targets(hass):
    first, second, stale = [_candidate(hass, i) for i in (1, 2, 3)]
    hass.config_entries.async_update_entry(stale[0], data={"address": "192.0.2.99"})
    shared = _health(first[2].discovery_id, second[0].unique_id)
    accept_health(hass, second[0], shared)
    flow, shown = await _open(hass, first[0])
    assert "Panel 3" not in shown["description_placeholders"]["panels"]
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(side_effect=[first[2], shared]),
    ):
        result = await flow.async_step_confirm_identity({})
    assert result["type"] is FlowResultType.ABORT
    assert first[0].unique_id == "1" * 64
    assert second[0].unique_id == "2" * 64


@pytest.mark.parametrize(
    "language", ["en", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"]
)
async def test_localized_identity_repair_names_panel_and_frozen_fleet(hass, language):
    from homeassistant.helpers.translation import async_get_translations
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    first, second = [_candidate(hass, i) for i in (1, 2)]
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"{ISSUE_IDENTITY}_{first[0].entry_id}"
    )
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues.{ISSUE_IDENTITY}"
    for field in ("title",):
        rendered = strings[f"{prefix}.{field}"].format(**issue.translation_placeholders)
        assert first[0].title in rendered
    _, shown = await _open(hass, first[0])
    rendered = strings[f"{prefix}.fix_flow.step.confirm_identity.description"].format(
        **shown["description_placeholders"]
    )
    assert first[0].title in rendered
    assert second[0].title in rendered


async def test_opening_real_repair_shows_fleet_before_explicit_confirmation(
    hass, hass_client
):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    first, second = [_candidate(hass, i) for i in (1, 2)]
    admin = await hass_client()
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=[first[2], second[2]]),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        response = await admin.post(
            "/api/repairs/issues/fix",
            json={
                "handler": DOMAIN,
                "issue_id": f"{ISSUE_IDENTITY}_{first[0].entry_id}",
            },
        )
        assert response.status == 200
        shown = await response.json()
        assert shown["type"] == "form"
        assert first[0].unique_id == "1" * 64
        assert second[0].unique_id == "2" * 64
        assert "Panel 1" in shown["description_placeholders"]["panels"]
        assert "Panel 2" in shown["description_placeholders"]["panels"]
        response = await admin.post(
            f"/api/repairs/issues/fix/{shown['flow_id']}", json={}
        )
        assert response.status == 200
        result = await response.json()
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
    assert first[0].unique_id == first[2].discovery_id
    assert second[0].unique_id == second[2].discovery_id

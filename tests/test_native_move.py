"""The Repairs issue moving a panel still on MQTT to Panel Assistant."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.client import (
    CannotConnectError,
    is_version_at_least,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.transport import (
    async_raise_cutover_incomplete_issue,
)

from .test_cutover import NATIVE, PANEL_ID, _issue, _mqtt, _record, _reload
from .test_native import _setup, panel_patches
from .test_transport import HEALTH, WsClientFactory

MOVE = "move_to_native_connection"
UPDATE_FIRST = "update_before_native_move"


@contextmanager
def _panel_version(version: str) -> Iterator[None]:
    """Health reports this ha-paneld version."""
    with patch("tests.test_native.HEALTH", replace(HEALTH, version=version)):
        yield


def _domain_issues(hass: HomeAssistant, entry_id: str) -> list[str]:
    """Return every move issue raised for the entry, by translation key."""
    return sorted(
        issue.translation_key or ""
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
        and issue_id.endswith(entry_id)
        and issue.translation_key in (MOVE, UPDATE_FIRST)
    )


async def _start_fix(client: Any, entry_id: str) -> dict[str, Any]:
    response = await client.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": f"{MOVE}_{entry_id}"},
    )
    assert response.status == 200
    result: dict[str, Any] = await response.json()
    return result


@pytest.mark.parametrize(
    ("version", "minimum", "expected"),
    [
        ("0.9.9-rc2", "0.9.8-rc1", True),
        ("0.9.8-rc1", "0.9.8-rc1", True),
        ("0.9.8", "0.9.8-rc1", True),
        ("0.9.8-rc10", "0.9.8-rc9", True),
        ("0.9.8-rc1", "0.9.8", False),
        ("0.9.7", "0.9.8-rc1", False),
        ("0.10.0", "0.9.8-rc1", True),
        ("build-1066", "0.9.8-rc1", None),
    ],
)
def test_version_order_puts_a_release_after_its_candidates(
    version: str, minimum: str, expected: bool | None
) -> None:
    assert is_version_at_least(version, minimum) is expected


@pytest.mark.parametrize("options", [{"authority": "mqtt"}, {}], ids=["mqtt", "shadow"])
async def test_a_panel_on_mqtt_is_offered_the_move_once(
    hass: HomeAssistant, hass_read_only_user: Any, options: dict[str, str]
) -> None:
    """Fixable, named, and never a second issue however often it is evaluated."""
    entry = await _setup(hass, hass_read_only_user.id, native=None, options=options)

    issue = _issue(hass, MOVE, entry.entry_id)
    assert issue is not None
    assert issue.is_fixable is True
    assert issue.is_persistent is False
    assert issue.translation_placeholders == {"panel": "alpha"}
    assert issue.data == {"entry_id": entry.entry_id}

    with panel_patches():
        await entry.runtime_data.coordinator.async_refresh()
        await _reload(hass, entry)
    assert _domain_issues(hass, entry.entry_id) == [MOVE]


async def test_a_panel_too_old_for_the_move_is_asked_to_update_first(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Then, once it reports a new enough release, the move replaces it."""
    with _panel_version("0.9.7"):
        entry = await _setup(hass, hass_read_only_user.id, native=None)

    issue = _issue(hass, UPDATE_FIRST, entry.entry_id)
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {
        "panel": "alpha",
        "version": "0.9.7",
        "required_version": "0.9.8-rc1",
    }
    assert _domain_issues(hass, entry.entry_id) == [UPDATE_FIRST]

    with panel_patches():
        await entry.runtime_data.coordinator.async_refresh()
    assert _domain_issues(hass, entry.entry_id) == [MOVE]


async def test_a_panel_whose_version_is_unknown_is_asked_nothing_yet(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    @contextmanager
    def offline() -> Iterator[None]:
        with (
            panel_patches(),
            patch(
                "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
                AsyncMock(side_effect=CannotConnectError),
            ),
        ):
            yield

    with patch("tests.test_native.panel_patches", offline):
        entry = await _setup(hass, hass_read_only_user.id, native=None)

    assert entry.runtime_data.coordinator.data is None
    assert _domain_issues(hass, entry.entry_id) == []


async def test_a_native_panel_is_offered_no_move(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    native = await _setup(hass, hass_read_only_user.id, native=None, options=NATIVE)
    assert _domain_issues(hass, native.entry_id) == []


async def test_native_entities_turned_off_offer_no_move(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    entry = await _setup(hass, hass_read_only_user.id, native=False)
    assert _domain_issues(hass, entry.entry_id) == []


async def test_a_failed_cutover_holds_the_offer_back(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    entry = await _setup(hass, hass_read_only_user.id, native=None)
    assert _domain_issues(hass, entry.entry_id) == [MOVE]

    async_raise_cutover_incomplete_issue(hass, entry, "move_back", "boom")
    with panel_patches():
        await entry.runtime_data.coordinator.async_refresh()
    assert _domain_issues(hass, entry.entry_id) == []


async def test_removing_the_entry_clears_its_issue(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    entry = await _setup(hass, hass_read_only_user.id, native=None)
    assert _domain_issues(hass, entry.entry_id) == [MOVE]

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _domain_issues(hass, entry.entry_id) == []


async def test_the_fix_moves_the_panel_as_the_control_option_does(
    hass: HomeAssistant,
    hass_read_only_user: Any,
    hass_client: Any,
    hass_ws_client: WsClientFactory,
) -> None:
    """Its entities move to Panel Assistant and the issue goes."""
    assert await async_setup_component(hass, "repairs", {})
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(
        hass, hass_read_only_user.id, native=None, options={"authority": "mqtt"}
    )
    client = await hass_client()

    form = await _start_fix(client, entry.entry_id)
    assert form["type"] == "form"
    assert form["step_id"] == "confirm_move"
    assert form["description_placeholders"] == {"panel": "alpha"}
    assert entry.options == {"authority": "mqtt"}

    with panel_patches():
        response = await client.post(
            f"/api/repairs/issues/fix/{form['flow_id']}", json={}
        )
        assert response.status == 200
        assert (await response.json())["type"] == "create_entry"
        await hass.async_block_till_done()

    assert entry.options == {"authority": "native"}
    assert _record(entry)["state"] == "complete"
    moved = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert moved is not None
    assert moved.platform == DOMAIN
    assert moved.unique_id != f"{PANEL_ID}_relay1"
    assert _domain_issues(hass, entry.entry_id) == []


async def test_the_fix_refuses_a_panel_that_went_back_to_an_older_release(
    hass: HomeAssistant, hass_read_only_user: Any, hass_client: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await _setup(hass, hass_read_only_user.id, native=None)
    client = await hass_client()
    form = await _start_fix(client, entry.entry_id)

    with _panel_version("0.9.7"), panel_patches():
        await entry.runtime_data.coordinator.async_refresh()
        # The issue has already been swapped; a flow left open still refuses.
        response = await client.post(
            f"/api/repairs/issues/fix/{form['flow_id']}", json={}
        )
    result = await response.json()
    assert result["type"] == "abort"
    assert result["reason"] == "update_first"
    assert result["description_placeholders"] == {
        "panel": "alpha",
        "required_version": "0.9.8-rc1",
    }
    assert entry.options == {}


async def test_the_fix_of_a_removed_panel_changes_nothing(
    hass: HomeAssistant, hass_read_only_user: Any, hass_client: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = await _setup(hass, hass_read_only_user.id, native=None)
    client = await hass_client()
    form = await _start_fix(client, entry.entry_id)

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    response = await client.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})

    result = await response.json()
    assert result["type"] == "abort"
    assert result["reason"] == "entry_removed"

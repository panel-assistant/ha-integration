"""Panel Assistant moves every panel still on MQTT to its own connection."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.client import (
    CannotConnectError,
    is_version_at_least,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.transport import (
    async_raise_cutover_incomplete_issue,
    mqtt_discovery_claim,
)

from .test_cutover import PANEL_ID, _issue, _mqtt, _record, _reload
from .test_native import _setup, panel_patches
from .test_transport import HEALTH

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


async def _poll(hass: HomeAssistant, entry: Any) -> None:
    """Poll the panel once and let any reload the poll caused finish."""
    with panel_patches():
        await entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()


def _assert_moved(hass: HomeAssistant, entry: Any) -> None:
    """Native authority, a completed cutover, and nothing left to ask."""
    assert entry.options["authority"] == "native"
    assert entry.runtime_data.authority == "native"
    assert _record(entry)["state"] == "complete"
    assert _domain_issues(hass, entry.entry_id) == []


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


@pytest.mark.parametrize("version", ["0.9.8-rc1", "0.9.7"])
async def test_a_panel_that_never_used_mqtt_is_left_alone(
    hass: HomeAssistant, hass_read_only_user: Any, version: str
) -> None:
    """A shadow entry without an MQTT device, as a new user's panel is.

    Nothing to move and nothing to ask; once MQTT knows the panel, the next
    poll moves it, or asks for the update a move needs.
    """
    with _panel_version(version):
        entry = await _setup(hass, hass_read_only_user.id, native=None)
        assert entry.options == {}
        assert _domain_issues(hass, entry.entry_id) == []
        await _reload(hass, entry)
        assert entry.options == {}
        assert _domain_issues(hass, entry.entry_id) == []

        _mqtt(hass, [("switch", "relay1", {})])
        await _poll(hass, entry)
    if version == "0.9.8-rc1":
        _assert_moved(hass, entry)
    else:
        assert entry.options == {}
        assert _domain_issues(hass, entry.entry_id) == [UPDATE_FIRST]


async def test_a_panel_set_to_mqtt_moves_without_a_device(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Choosing MQTT outright is MQTT history, even before MQTT announces it."""
    entry = await _setup(
        hass, hass_read_only_user.id, native=None, options={"authority": "mqtt"}
    )
    _assert_moved(hass, entry)


@pytest.mark.parametrize("options", [{"authority": "mqtt"}, {}], ids=["mqtt", "shadow"])
async def test_a_panel_on_mqtt_moves_at_setup_keeping_its_entities(
    hass: HomeAssistant, hass_read_only_user: Any, options: dict[str, str]
) -> None:
    """No click: the entity keeps its ID, the panel is told to withdraw its
    MQTT discovery, and a later poll or reload changes nothing.
    """
    mqtt = _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=None, options=options)

    _assert_moved(hass, entry)
    moved = er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"])
    assert moved is not None
    assert moved.platform == DOMAIN
    assert moved.unique_id != f"{PANEL_ID}_relay1"
    assert mqtt_discovery_claim(hass, entry) == "withdraw"

    await _poll(hass, entry)
    await _reload(hass, entry)
    _assert_moved(hass, entry)
    assert er.async_get(hass).async_get(mqtt["entity_ids"]["relay1"]) == moved


async def test_a_panel_too_old_for_the_move_is_asked_to_update_first(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """It stays on MQTT; once it reports a new enough release, it moves."""
    with _panel_version("0.9.7"):
        _mqtt(hass, [("switch", "relay1", {})])
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
    assert entry.options == {}

    await _poll(hass, entry)
    _assert_moved(hass, entry)


async def test_a_panel_whose_version_is_unknown_is_left_until_it_reports_one(
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
        _mqtt(hass, [("switch", "relay1", {})])
        entry = await _setup(hass, hass_read_only_user.id, native=None)

    assert entry.runtime_data.coordinator.data is None
    assert entry.options == {}
    assert _domain_issues(hass, entry.entry_id) == []

    await _poll(hass, entry)
    _assert_moved(hass, entry)


async def test_native_entities_turned_off_move_nothing(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    _mqtt(hass, [("switch", "relay1", {})])
    entry = await _setup(hass, hass_read_only_user.id, native=False)
    assert entry.options == {}
    assert _domain_issues(hass, entry.entry_id) == []


async def test_a_failed_cutover_holds_the_move_back(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    with _panel_version("0.9.7"):
        _mqtt(hass, [("switch", "relay1", {})])
        entry = await _setup(hass, hass_read_only_user.id, native=None)
    assert _domain_issues(hass, entry.entry_id) == [UPDATE_FIRST]

    async_raise_cutover_incomplete_issue(hass, entry, "move_back", "boom")
    await _poll(hass, entry)
    assert entry.options == {}
    assert _domain_issues(hass, entry.entry_id) == []


async def test_removing_the_entry_clears_its_issue(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    with _panel_version("0.9.7"):
        _mqtt(hass, [("switch", "relay1", {})])
        entry = await _setup(hass, hass_read_only_user.id, native=None)
    assert _domain_issues(hass, entry.entry_id) == [UPDATE_FIRST]

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _domain_issues(hass, entry.entry_id) == []


async def test_the_move_withdraws_the_offer_an_earlier_release_raised(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Panel Assistant 0.8 asked for a click; once moved, nothing is asking."""
    with _panel_version("0.9.7"):
        _mqtt(hass, [("switch", "relay1", {})])
        entry = await _setup(hass, hass_read_only_user.id, native=None)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{MOVE}_{entry.entry_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=MOVE,
    )

    await _poll(hass, entry)
    _assert_moved(hass, entry)
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"{MOVE}_{entry.entry_id}") is None
    )

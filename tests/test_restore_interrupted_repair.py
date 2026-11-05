"""An interrupted restore the panel reports is raised as a Repair until it is not."""

import json
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    parse_panel_install_status,
)
from custom_components.panel_assistant.const import DOMAIN

from .test_availability import _load

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

"""Apps left over from a panel's previous setup are pointed out after an install."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import device_setup
from custom_components.panel_assistant import repairs_previous_apps as previous
from custom_components.panel_assistant.const import CONF_INSTALL_APPS_PENDING, DOMAIN
from custom_components.panel_assistant.transport_protocol import (
    CAPABILITY_MANAGEMENT,
)

from .test_availability import _load
from .test_management import MANAGED, _connect, _http_answers
from .test_transport import WsClientFactory

LAUNCHER = {"pkg": "l.l", "label": "Launcher", "previous_setup": True}
COMPANION = {
    "pkg": "io.homeassistant.companion.android",
    "label": "Home Assistant",
    "previous_setup": True,
}
WEATHER = {"pkg": "com.example.weather", "label": "Weather", "previous_setup": False}
OURS = {"pkg": "io.panelassistant.android", "label": "Panel", "previous_setup": True}


class FakePanel:
    """A panel session answering the `packages` read as the app does."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.session = self.new_session()
        self.listeners: list[Any] = []

    @staticmethod
    def new_session() -> SimpleNamespace:
        return SimpleNamespace(capabilities=frozenset({CAPABILITY_MANAGEMENT}))

    def async_add_listener(self, listener: Any) -> Any:
        """The coordinator's seam: told after each update."""
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def updated(self) -> None:
        for listener in list(self.listeners):
            listener()

    async def manage(
        self, hass: HomeAssistant, session: Any, op: str, **fields: Any
    ) -> dict[str, Any]:
        # The app serves `packages` as a read with no arguments.
        self.requests.append((op, fields))
        return self.answer


def _answer(*packages: dict[str, Any], available: bool | None = True) -> dict:
    answer: dict[str, Any] = {"packages": list(packages)}
    if available is not None:
        answer["uninstall_available"] = available
    return answer


@pytest.fixture
async def panel(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> FakePanel:
    assert await async_setup_component(hass, DOMAIN, {})
    fake = FakePanel(_answer(LAUNCHER, COMPANION, WEATHER, OURS))
    monkeypatch.setattr(
        previous,
        "async_get_sessions",
        lambda _hass: {"e1": fake.session} if fake.session else {},
    )
    monkeypatch.setattr(previous, "async_manage", fake.manage)
    return fake


def _entry(hass: HomeAssistant, reason: str | None) -> MockConfigEntry:
    """An entry whose last owner operation was an install, an update or none."""
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="e1", title="Alpha", data={CONF_ADDRESS: "p.local"}
    )
    entry.add_to_hass(hass)
    if reason is not None:
        device_setup.async_mark_owed(hass, entry, reason)
    return entry


async def _session_opens(hass: HomeAssistant, panel: FakePanel) -> None:
    panel.session = panel.new_session()
    panel.updated()
    await hass.async_block_till_done()


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, previous.previous_apps_issue_id("e1")
    )


async def test_an_install_offers_its_leftovers_with_the_install_page(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = _entry(hass, device_setup.OWED_INSTALL)
    stop = previous.async_follow_previous_apps(hass, entry, panel)
    await _session_opens(hass, panel)
    # Later polls of the same session ask nothing more.
    panel.updated()
    await hass.async_block_till_done()

    assert panel.requests == [("packages", {})]
    issue = _issue(hass)
    assert issue is not None and not issue.is_fixable
    assert issue.translation_placeholders == {
        "panel": "Alpha",
        "apps": "Launcher, Home Assistant",
        "panel_install": "/panel-assistant/e1/install",
    }
    assert issue.learn_more_url
    stop()
    assert _issue(hass) is None


@pytest.mark.parametrize("reason", [None, device_setup.OWED_UPDATE, "skip"])
async def test_no_install_asks_nothing(
    hass: HomeAssistant, panel: FakePanel, reason: str | None
) -> None:
    entry = _entry(hass, None if reason == "skip" else reason)
    if reason == "skip":
        device_setup.async_mark_owed(hass, entry, device_setup.OWED_UPDATE)
        device_setup.async_stop_asking(hass, entry)
    previous.async_follow_previous_apps(hass, entry, panel)
    await _session_opens(hass, panel)
    assert panel.requests == []
    assert _issue(hass) is None


@pytest.mark.parametrize(
    "answer",
    [_answer(LAUNCHER, available=None), _answer(LAUNCHER, available=False)],
    ids=["older_panel", "no_uninstall"],
)
async def test_a_panel_that_cannot_remove_them_gets_no_offer(
    hass: HomeAssistant, panel: FakePanel, answer: dict[str, Any]
) -> None:
    panel.answer = answer
    entry = _entry(hass, device_setup.OWED_INSTALL)
    previous.async_follow_previous_apps(hass, entry, panel)
    await _session_opens(hass, panel)
    assert panel.requests and _issue(hass) is None
    # It may still learn to: the next session asks again.
    assert entry.data[CONF_INSTALL_APPS_PENDING] is True


async def test_apps_removed_later_withdraw_the_offer_for_good(
    hass: HomeAssistant, panel: FakePanel
) -> None:
    entry = _entry(hass, device_setup.OWED_INSTALL)
    previous.async_follow_previous_apps(hass, entry, panel)
    await _session_opens(hass, panel)
    assert _issue(hass) is not None

    panel.answer = _answer(WEATHER, OURS)
    await _session_opens(hass, panel)
    assert _issue(hass) is None
    assert CONF_INSTALL_APPS_PENDING not in entry.data

    panel.requests.clear()
    await _session_opens(hass, panel)
    assert panel.requests == []


async def test_a_real_session_is_asked_after_its_hello_and_answered(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """The request goes out on the session as the app's read takes it."""
    entry = await _load(hass, hass_read_only_user.id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_INSTALL_APPS_PENDING: True}
    )
    with _http_answers():
        session = await _connect(
            hass, hass_ws_client, hass_read_only_access_token, MANAGED
        )
        request = await session.manage()
        # A read: no arguments, so nothing the app could drop.
        assert request["op"] == "packages" and "args" not in request
        answered = await session.answer(
            request, "applied", result=_answer(LAUNCHER, WEATHER)
        )
        assert answered["success"], answered
        await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, previous.previous_apps_issue_id(entry.entry_id)
    )
    assert issue is not None
    assert issue.translation_placeholders["apps"] == "Launcher"

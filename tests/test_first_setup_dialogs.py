"""First-setup dialogs move on by themselves and leave nothing open behind them.

The install and the Continue setup dialogs used to wait on a Submit or Done
press the owner had no reason to make, and a finished install opened an
options flow the dialog never showed and nobody closed. Each test drives the
real config or options flow through Home Assistant's flow managers; the
install tests run the real receipt store and executor behind the harness.
"""

from __future__ import annotations

import asyncio
import contextlib
from itertools import pairwise
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import Event, HomeAssistant
from homeassistant.data_entry_flow import (
    EVENT_DATA_ENTRY_FLOW_PROGRESS_UPDATE,
    FlowResultType,
    UnknownFlow,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import install_executor
from custom_components.panel_assistant.const import CONF_TRANSPORT_USER_ID, DOMAIN
from custom_components.panel_assistant.install_jobs import (
    InstallPhase,
    async_get_install_job_manager,
)

from .install_executor_harness import Harness, seed_phase
from .test_install_unfinished import (  # noqa: F401 - shared autouse fixtures
    FLOW,
    PANEL_DID,
    SEEDED_IP,
    _add,
    _app_answers,
    _choose,
    _executor_health_names_the_panel,
    emulate_home_assistant_store_file,
    harness,
    install_release_catalog,
    pins,
)

SETUP_URL = "http://192.168.1.23:8888/setup"


def _progress_updates(hass: HomeAssistant) -> dict[str, list[float]]:
    """Record every progress-ring update Home Assistant sends, by flow."""
    seen: dict[str, list[float]] = {}

    def _record(event: Event) -> None:
        seen.setdefault(event.data["flow_id"], []).append(event.data["progress"])

    hass.bus.async_listen(EVENT_DATA_ENTRY_FLOW_PROGRESS_UPDATE, _record)
    return seen


async def _start_first_install(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Carry on a first install of a panel with no entry; its progress step.

    The job is seeded part-way so the harness's stand-in download is the one
    the receipt names; from there the real executor runs it to the end.
    """
    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    _executor_health_names_the_panel(monkeypatch)
    await seed_phase(hass, InstallPhase.DOWNLOADING)
    progress = await _choose(hass, await _add(hass, SEEDED_IP), "resume_install")
    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    assert progress["step_id"] == "install_progress"
    return progress


async def _until(condition: Any) -> None:
    """Let the loop run until ``condition()`` holds; fail rather than hang."""
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 - the ring has no event to wait on
            await asyncio.sleep(0.001)


async def test_a_first_install_ends_at_its_entry_with_no_flow_left_open(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The created entry hands the dialog no follow-on options flow.

    Home Assistant's dialog ignored the flow id it was handed and opened the
    panel's options menu instead, and the options flow made for it stayed in
    progress forever. The panel binds itself on its first hello instead.
    """
    with (
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
        patch(f"{FLOW}._INSTALL_PROGRESS_SECONDS", 0.001),
        _app_answers({SEEDED_IP: PANEL_DID}),
    ):
        progress = await _start_first_install(hass, harness, monkeypatch)
        await hass.async_block_till_done(wait_background_tasks=True)
        # The worker finished by itself; the dialog's own refetch moves on.
        result = await hass.config_entries.flow.async_configure(progress["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "next_flow" not in result
    assert hass.config_entries.flow.async_progress() == []
    assert hass.config_entries.options.async_progress() == []
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert CONF_TRANSPORT_USER_ID not in entry.data


async def test_the_install_progress_ring_rises_and_stops_with_the_install(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ring fills from the install's durable phase, only ever forward."""
    updates = _progress_updates(hass)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    with (
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
        patch(f"{FLOW}._INSTALL_PROGRESS_SECONDS", 0.001),
        _app_answers({SEEDED_IP: PANEL_DID}),
    ):
        progress = await _start_first_install(hass, harness, monkeypatch)
        flow_id = progress["flow_id"]
        await harness.stage_entered.wait()
        # Held while staging: the ring already shows part of the app's share.
        await _until(lambda: bool(updates.get(flow_id)))
        while_staging = list(updates[flow_id])
        harness.stage_release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        result = await hass.config_entries.flow.async_configure(flow_id)
        settled = list(updates[flow_id])
        await asyncio.sleep(0.05)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    values = updates[flow_id]
    assert values == settled, "the ring kept moving after the install finished"
    assert len(values) > len(while_staging), "the ring did not move after staging"
    assert all(0 < value < 1 for value in values)
    assert all(a < b for a, b in pairwise(values))


async def test_closing_the_install_dialog_stops_its_progress_ring(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed dialog leaves the install running but stops reporting to it."""
    updates = _progress_updates(hass)
    harness.stage_entered = asyncio.Event()
    harness.stage_release = asyncio.Event()
    with (
        patch(f"{FLOW}._INSTALL_PROGRESS_SECONDS", 0.001),
        _app_answers({SEEDED_IP: PANEL_DID}),
    ):
        progress = await _start_first_install(hass, harness, monkeypatch)
        flow_id = progress["flow_id"]
        await harness.stage_entered.wait()
        await _until(lambda: bool(updates.get(flow_id)))
        hass.config_entries.flow.async_abort(flow_id)
        at_close = list(updates[flow_id])
        harness.stage_release.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert updates[flow_id] == at_close
    (job,) = await (await async_get_install_job_manager(hass)).async_list()
    assert job.phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_closing_while_the_install_starts_leaves_no_progress_watch(
    hass: HomeAssistant,
    harness: Harness,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dialog closed while its install worker starts watches nothing after."""
    starting, release = asyncio.Event(), asyncio.Event()
    ensure = install_executor.InstallExecutor.async_ensure_job

    async def held(self: Any, job_id: str) -> Any:
        starting.set()
        await release.wait()
        return await ensure(self, job_id)

    monkeypatch.setattr(install_executor.InstallExecutor, "async_ensure_job", held)
    harness.expected_version = "0.1.0"
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    _executor_health_names_the_panel(monkeypatch)
    await seed_phase(hass, InstallPhase.DOWNLOADING)
    updates = _progress_updates(hass)
    with _app_answers({SEEDED_IP: PANEL_DID}):
        menu = await _add(hass, SEEDED_IP)
        flow = hass.config_entries.flow._progress[menu["flow_id"]]
        pressed = asyncio.create_task(_choose(hass, menu, "resume_install"))
        await starting.wait()
        hass.config_entries.flow.async_abort(menu["flow_id"])
        release.set()
        with contextlib.suppress(UnknownFlow):
            await pressed
        await hass.async_block_till_done(wait_background_tasks=True)

    assert flow._progress_ticker is None
    assert flow._progress_waiter is None
    assert updates.get(menu["flow_id"]) is None
    (job,) = await (await async_get_install_job_manager(hass)).async_list()
    assert job.phase is InstallPhase.HEALTHY_UNCLAIMED


@pytest.fixture
def unbound_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A panel installed but not yet signed in to Home Assistant."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", data={CONF_ADDRESS: "192.168.1.23"}
    )
    entry.add_to_hass(hass)
    return entry


async def _continue_setup(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    return await hass.config_entries.options.async_init(
        entry.entry_id, context={"source": "onboarding"}
    )


async def test_continue_setup_waits_and_closes_once_the_panel_signs_in(
    hass: HomeAssistant, unbound_entry: MockConfigEntry
) -> None:
    """Continue setup moves on by itself the moment the panel connects."""
    with (
        patch(f"{FLOW}.async_offer_ha_url", new_callable=AsyncMock) as handover,
        patch(f"{FLOW}._SETUP_POLL_SECONDS", 0.001),
    ):
        shown = await _continue_setup(hass, unbound_entry)
        assert shown["type"] is FlowResultType.SHOW_PROGRESS
        assert shown["step_id"] == "onboarding"
        assert shown["progress_action"] == "onboarding"
        assert shown["description_placeholders"] == {"setup_url": SETUP_URL}
        # A refetch of the dialog neither restarts the wait nor re-offers.
        again = await hass.config_entries.options.async_configure(shown["flow_id"])
        assert again["type"] is FlowResultType.SHOW_PROGRESS

        hass.config_entries.async_update_entry(
            unbound_entry,
            data={**unbound_entry.data, CONF_TRANSPORT_USER_ID: "signed-in"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        finished = await hass.config_entries.options.async_configure(shown["flow_id"])

    handover.assert_awaited_once()
    assert finished["type"] is FlowResultType.ABORT
    assert finished["reason"] == "panel_connected"
    assert hass.config_entries.options.async_progress() == []


async def test_continue_setup_on_a_signed_in_panel_says_so_at_once(
    hass: HomeAssistant, unbound_entry: MockConfigEntry
) -> None:
    """Nothing is left to wait for once the panel has signed in."""
    hass.config_entries.async_update_entry(
        unbound_entry, data={**unbound_entry.data, CONF_TRANSPORT_USER_ID: "user"}
    )
    with patch(f"{FLOW}.async_offer_ha_url", new_callable=AsyncMock) as handover:
        shown = await _continue_setup(hass, unbound_entry)

    assert shown["type"] is FlowResultType.ABORT
    assert shown["reason"] == "panel_connected"
    handover.assert_not_awaited()


async def test_continue_setup_says_when_its_wait_ran_out_and_waits_again(
    hass: HomeAssistant, unbound_entry: MockConfigEntry
) -> None:
    """At the deadline the dialog names the problem; Done starts a new wait."""
    with (
        patch(f"{FLOW}.async_offer_ha_url", new_callable=AsyncMock),
        patch(f"{FLOW}._SETUP_POLL_SECONDS", 0.001),
        patch(f"{FLOW}._SETUP_WATCH_SECONDS", 0),
    ):
        shown = await _continue_setup(hass, unbound_entry)
        await hass.async_block_till_done(wait_background_tasks=True)
        check = await hass.config_entries.options.async_configure(shown["flow_id"])
        assert check["type"] is FlowResultType.FORM
        assert check["step_id"] == "onboarding_check"
        assert check["errors"] == {"base": "panel_not_connected"}
        assert check["description_placeholders"] == {"setup_url": SETUP_URL}

        with patch(f"{FLOW}._SETUP_WATCH_SECONDS", 60):
            waiting = await hass.config_entries.options.async_configure(
                shown["flow_id"], {}
            )
            assert waiting["type"] is FlowResultType.SHOW_PROGRESS
            assert waiting["step_id"] == "onboarding"
            hass.config_entries.options.async_abort(shown["flow_id"])
            await hass.async_block_till_done(wait_background_tasks=True)


async def test_closing_continue_setup_during_the_address_offer_stops_the_wait(
    hass: HomeAssistant, unbound_entry: MockConfigEntry
) -> None:
    """Continue setup closed while the panel is told where Home Assistant is.

    The wait used to start after the close and ran on with no dialog.
    """
    offering, release = asyncio.Event(), asyncio.Event()

    async def offer(*_args: object) -> None:
        offering.set()
        await release.wait()

    menu = await hass.config_entries.options.async_init(unbound_entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    flow = hass.config_entries.options._progress[menu["flow_id"]]
    with patch(f"{FLOW}.async_offer_ha_url", side_effect=offer):
        pressed = asyncio.create_task(
            hass.config_entries.options.async_configure(
                menu["flow_id"], {"next_step_id": "onboarding"}
            )
        )
        await offering.wait()
        hass.config_entries.options.async_abort(menu["flow_id"])
        release.set()
        with contextlib.suppress(UnknownFlow):
            await pressed
        await hass.async_block_till_done(wait_background_tasks=True)

    assert flow._onboarding_waiter is not None
    assert flow._onboarding_waiter.cancelled()
    assert hass.config_entries.options.async_progress() == []


async def test_a_double_done_on_continue_setup_starts_one_wait(
    hass: HomeAssistant, unbound_entry: MockConfigEntry
) -> None:
    """Done pressed twice after the wait ran out starts one wait; closing stops it."""
    release = asyncio.Event()

    async def offered(*_args: object) -> None:
        await release.wait()

    offer = AsyncMock(side_effect=offered)
    with (
        patch(f"{FLOW}.async_offer_ha_url", offer),
        patch(f"{FLOW}._SETUP_WATCH_SECONDS", 0),
    ):
        menu = await hass.config_entries.options.async_init(unbound_entry.entry_id)
        flow = hass.config_entries.options._progress[menu["flow_id"]]
        await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "onboarding"}
        )
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        form = await hass.config_entries.options.async_configure(menu["flow_id"])
        assert form["step_id"] == "onboarding_check"
        release.clear()
        offer.reset_mock()
        with patch(f"{FLOW}._SETUP_WATCH_SECONDS", 3600):
            both = await asyncio.gather(
                hass.config_entries.options.async_configure(menu["flow_id"], {}),
                hass.config_entries.options.async_configure(menu["flow_id"], {}),
            )
            waiter = flow._onboarding_waiter
            hass.config_entries.options.async_abort(menu["flow_id"])
            release.set()
            await hass.async_block_till_done(wait_background_tasks=True)

    assert [result["type"] for result in both] == [FlowResultType.SHOW_PROGRESS] * 2
    offer.assert_awaited_once()
    assert waiter is not None and waiter.cancelled()

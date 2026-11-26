"""A healthy install finishes its setup where it reached the panel.

The panel's host guard refuses a name that is not its own. These tests drive
the real config flow, receipt store and executor, with the panel's setup read
answered over HTTP, refusals included.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.panel_assistant import install_executor
from custom_components.panel_assistant.client import (
    HaPaneldClient,
    PanelSetupState,
    RequestRefusedError,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_jobs import InstallPhase

from . import config_flow_helpers, install_executor_harness
from .config_flow_helpers import RELEASE
from .install_executor_harness import Harness, seed_phase
from .test_install_unfinished import (  # noqa: F401 - autouse name pinning
    FLOW,
    FLOW_LOG,
    SEEDED,
    SEEDED_IP,
    _add,
    _stored,
    pins,
)

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
emulate_home_assistant_store_file = (
    install_executor_harness.emulate_home_assistant_store_file
)


@pytest.fixture(autouse=True)
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The panel, ADB and download stand-ins behind the real executor."""
    return Harness(monkeypatch, expected_version=RELEASE.version)


# The panel's real setup read, kept before conftest stubs it to "done", so the
# tests below meet the panel's actual HTTP answer, refusals included.
_REAL_SETUP_STATE = HaPaneldClient.async_get_setup_state
_REAL_SETUP_COMPLETE = HaPaneldClient.async_get_setup_complete
HOST_REFUSED = "host not allowed\n"


def _setup_url(host: str) -> str:
    return f"http://{host}:8888/api/v1/setup"


@pytest.fixture
def panel_setup_http(
    monkeypatch: pytest.MonkeyPatch, aioclient_mock: AiohttpClientMocker
) -> AiohttpClientMocker:
    """The panel's setup read goes over HTTP; the test says what it answers."""
    monkeypatch.setattr(HaPaneldClient, "async_get_setup_state", _REAL_SETUP_STATE)
    monkeypatch.setattr(
        HaPaneldClient, "async_get_setup_complete", _REAL_SETUP_COMPLETE
    )
    monkeypatch.setattr(install_executor, "async_verify_installed_target", AsyncMock())
    monkeypatch.setattr(f"{FLOW}.async_offer_ha_url", AsyncMock())
    return aioclient_mock


def _setup_reads(http: AiohttpClientMocker) -> list[str]:
    return [str(url) for _method, url, *_ in http.mock_calls]


async def test_setup_finished_on_a_panel_refusing_the_typed_name_adds_it(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """Installed by a name the panel refuses: its setup is read where it was installed.

    The panel's host guard answers 403 to a name that is not its own, though
    the install reached it at the address that name resolved to. Reading setup
    by the typed name showed the setup form again on every Submit.
    """
    harness.expected_version = "0.1.0"
    panel_setup_http.get(_setup_url(SEEDED.address), status=403, text=HOST_REFUSED)
    panel_setup_http.get(_setup_url(SEEDED_IP), json={"complete": True})
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)

    with patch(
        "custom_components.panel_assistant.async_setup_entry", return_value=True
    ):
        result = await _add(hass, SEEDED.address)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: SEEDED.address}
    assert _setup_reads(panel_setup_http) == [_setup_url(SEEDED_IP)]
    consumed = await _stored(hass, receipt.job_id)
    assert consumed.phase is InstallPhase.CONSUMED


async def test_a_refused_setup_read_is_shown_and_logged_with_its_cause(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A panel refusing the setup read is named in the form and the log.

    Before, the refusal read as "not finished": the same form came back with
    no error and nothing logged, however often Submit was pressed. The dialog
    now waits for the panel's setup by itself; a refusal ends that wait at
    once, on a form that names it, and Submit there reads again.
    """
    harness.expected_version = "0.1.0"
    for host in (SEEDED.address, SEEDED_IP):
        panel_setup_http.get(_setup_url(host), status=403, text=HOST_REFUSED)
    await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)

    with caplog.at_level(logging.WARNING):
        shown = await _add(hass, SEEDED.address)
        submitted = await hass.config_entries.flow.async_configure(shown["flow_id"], {})

    for form in (shown, submitted):
        assert form["type"] is FlowResultType.FORM
        assert form["step_id"] == "panel_setup_check"
        assert form["errors"] == {"base": "panel_setup_refused"}
        assert form["description_placeholders"] == {
            "setup_url": f"http://{SEEDED_IP}:8888/setup",
            "refusal": "HTTP 403 host not allowed",
        }
    refusals = [
        record
        for record in caplog.records
        if record.name == FLOW_LOG
        and "Panel Assistant setup refused" in record.getMessage()
    ]
    assert [record.levelno for record in refusals] == [logging.WARNING] * 2
    for record in refusals:
        assert record.getMessage() == (
            "Panel Assistant setup refused: step=panel_setup_check "
            f"error=panel_setup_refused panel={SEEDED.address} "
            "cause=RequestRefusedError: HTTP 403 host not allowed"
        )
    assert hass.config_entries.async_entries(DOMAIN) == []

    panel_setup_http.clear_requests()
    panel_setup_http.get(_setup_url(SEEDED_IP), json={"complete": True})
    with patch(
        "custom_components.panel_assistant.async_setup_entry", return_value=True
    ):
        added = await hass.config_entries.flow.async_configure(shown["flow_id"], {})
        await hass.async_block_till_done()
    assert added["type"] is FlowResultType.CREATE_ENTRY


async def test_closing_the_setup_wait_keeps_the_install_for_the_next_add(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """The dialog waits for the panel's setup and moves on by itself when done.

    A form that only Submit moved on kept saying setup was unfinished after
    the panel had finished it. Closing the waiting dialog must keep the
    healthy install, so adding the same address again picks it up.
    """
    harness.expected_version = "0.1.0"
    panel_setup_http.get(_setup_url(SEEDED_IP), json={"complete": False})
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)

    waiting = await _add(hass, SEEDED.address)
    assert waiting["type"] is FlowResultType.SHOW_PROGRESS
    assert waiting["step_id"] == "panel_setup"
    assert waiting["progress_action"] == "panel_setup"
    assert waiting["description_placeholders"] == {
        "setup_url": f"http://{SEEDED_IP}:8888/setup"
    }

    # The owner closes the dialog before the panel's setup is done.
    hass.config_entries.flow.async_abort(waiting["flow_id"])
    await hass.async_block_till_done(wait_background_tasks=True)
    kept = await _stored(hass, receipt.job_id)
    assert kept.phase is InstallPhase.HEALTHY_UNCLAIMED
    assert hass.config_entries.async_entries(DOMAIN) == []
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    with (
        patch(f"{FLOW}._SETUP_POLL_SECONDS", 0.01),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        resumed = await _add(hass, SEEDED.address)
        assert resumed["type"] is FlowResultType.SHOW_PROGRESS
        assert resumed["step_id"] == "panel_setup"
        # The panel finishes its setup; the dialog moves on by itself.
        panel_setup_http.clear_requests()
        panel_setup_http.get(_setup_url(SEEDED_IP), json={"complete": True})
        await hass.async_block_till_done(wait_background_tasks=True)
        [moved_on] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert moved_on["step_id"] == "install_result"
        # The dialog, told the flow moved on, fetches where it went.
        added = await hass.config_entries.flow.async_configure(resumed["flow_id"])
        await hass.async_block_till_done()

    assert added["type"] is FlowResultType.CREATE_ENTRY
    assert added["data"] == {CONF_ADDRESS: SEEDED.address}
    # The panel binds itself when it first connects: no options dialog
    # follows, so the owner is not left on the panel's menu.
    assert "next_flow" not in added
    assert hass.config_entries.options.async_progress() == []
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    consumed = await _stored(hass, receipt.job_id)
    assert consumed.phase is InstallPhase.CONSUMED


async def test_a_setup_finished_before_the_wait_shows_goes_straight_on(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """A wait whose first read already finds the setup done shows nothing.

    A flow opened from a Repair starts at its install result. If the panel
    finishes its setup between that check and the wait's first read, the
    flow must add the panel, not open on an empty progress done.
    """
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    reads = AsyncMock(
        side_effect=[
            PanelSetupState(complete=False),
            PanelSetupState(complete=True),
            PanelSetupState(complete=True),
        ]
    )
    with (
        patch.object(HaPaneldClient, "async_get_setup_state", reads),
        patch("custom_components.panel_assistant.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "repair_finalize"},
            data={"job_id": receipt.job_id},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert reads.await_count == 3
    consumed = await _stored(hass, receipt.job_id)
    assert consumed.phase is InstallPhase.CONSUMED


async def test_closing_during_the_address_offer_stops_the_wait(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """A dialog closed while the panel is told where Home Assistant is stays closed.

    The wait used to start after the close and kept reading the panel's
    setup with no dialog left to move on. The offer now runs inside the
    wait, which the close cancels.
    """
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    reads = AsyncMock(return_value=PanelSetupState(complete=False))
    offering, release = asyncio.Event(), asyncio.Event()

    async def offer(*_args: object) -> None:
        offering.set()
        await release.wait()

    with (
        patch.object(HaPaneldClient, "async_get_setup_state", reads),
        patch(f"{FLOW}.async_offer_ha_url", side_effect=offer),
    ):
        opening = asyncio.create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "repair_finalize"},
                data={"job_id": receipt.job_id},
            )
        )
        await offering.wait()
        [progress] = hass.config_entries.flow.async_progress(include_uninitialized=True)
        flow = hass.config_entries.flow._progress[progress["flow_id"]]
        hass.config_entries.flow.async_abort(progress["flow_id"])
        release.set()
        with contextlib.suppress(UnknownFlow):
            await opening
        await hass.async_block_till_done(wait_background_tasks=True)

    assert flow._setup_waiter is not None
    assert flow._setup_waiter.cancelled()
    assert reads.await_count == 1
    assert hass.config_entries.flow.async_progress() == []
    assert (await _stored(hass, receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )


async def test_the_wait_reports_its_own_ending_not_an_earlier_refusal(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """A refusal before the wait does not outlive the wait's own answers."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    reads = AsyncMock(
        side_effect=[
            RequestRefusedError(403, "host not allowed"),
            PanelSetupState(complete=False),
        ]
    )
    with (
        patch.object(HaPaneldClient, "async_get_setup_state", reads),
        patch(f"{FLOW}._SETUP_WATCH_SECONDS", 0),
    ):
        shown = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "repair_finalize"},
            data={"job_id": receipt.job_id},
        )
        if shown["type"] is FlowResultType.SHOW_PROGRESS:
            await hass.async_block_till_done(wait_background_tasks=True)
            shown = await hass.config_entries.flow.async_configure(shown["flow_id"])
        hass.config_entries.flow.async_abort(shown["flow_id"])

    assert reads.await_count == 2
    assert shown["step_id"] == "panel_setup_check"
    assert shown["errors"] == {"base": "panel_setup_not_finished"}
    assert "refusal" not in shown["description_placeholders"]


async def test_a_refetched_refusal_form_still_names_the_refusal(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """The dialog may fetch the form again; the refusal is not lost by that."""
    harness.expected_version = "0.1.0"
    for host in (SEEDED.address, SEEDED_IP):
        panel_setup_http.get(_setup_url(host), status=403, text=HOST_REFUSED)
    await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)

    shown = await _add(hass, SEEDED.address)
    reads = len(panel_setup_http.mock_calls)
    fetched = await hass.config_entries.flow.async_configure(shown["flow_id"])
    hass.config_entries.flow.async_abort(shown["flow_id"])

    assert len(panel_setup_http.mock_calls) == reads
    for form in (shown, fetched):
        assert form["step_id"] == "panel_setup_check"
        assert form["errors"] == {"base": "panel_setup_refused"}
        assert form["description_placeholders"]["refusal"] == (
            "HTTP 403 host not allowed"
        )


async def test_a_double_submit_starts_one_wait(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """Submit pressed twice on the check form starts one wait, which closing stops."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    reads = AsyncMock(return_value=PanelSetupState(complete=False))
    release = asyncio.Event()

    async def offered(*_args: object) -> None:
        await release.wait()

    offer = AsyncMock(side_effect=offered)
    with (
        patch.object(HaPaneldClient, "async_get_setup_state", reads),
        patch(f"{FLOW}.async_offer_ha_url", offer),
        patch(f"{FLOW}._SETUP_WATCH_SECONDS", 0),
    ):
        shown = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "repair_finalize"},
            data={"job_id": receipt.job_id},
        )
        flow = hass.config_entries.flow._progress[shown["flow_id"]]
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        form = await hass.config_entries.flow.async_configure(shown["flow_id"])
        assert form["step_id"] == "panel_setup_check"
        release.clear()
        offer.reset_mock()
        with patch(f"{FLOW}._SETUP_WATCH_SECONDS", 3600):
            both = await asyncio.gather(
                hass.config_entries.flow.async_configure(shown["flow_id"], {}),
                hass.config_entries.flow.async_configure(shown["flow_id"], {}),
            )
            waiter = flow._setup_waiter
            hass.config_entries.flow.async_abort(shown["flow_id"])
            release.set()
            await hass.async_block_till_done(wait_background_tasks=True)

    assert [result["type"] for result in both] == [FlowResultType.SHOW_PROGRESS] * 2
    offer.assert_awaited_once()
    assert waiter is not None and waiter.cancelled()
    assert (await _stored(hass, receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )


async def test_closing_during_the_retry_read_starts_no_wait(
    hass: HomeAssistant,
    harness: Harness,
    panel_setup_http: AiohttpClientMocker,
) -> None:
    """A dialog closed while Submit re-reads the setup stays closed."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    reading, release = asyncio.Event(), asyncio.Event()
    hold = False

    async def read(*_args: object) -> PanelSetupState:
        if hold:
            reading.set()
            await release.wait()
        return PanelSetupState(complete=False)

    offer = AsyncMock()
    with (
        patch.object(HaPaneldClient, "async_get_setup_state", side_effect=read),
        patch(f"{FLOW}.async_offer_ha_url", offer),
        patch(f"{FLOW}._SETUP_WATCH_SECONDS", 0),
    ):
        shown = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "repair_finalize"},
            data={"job_id": receipt.job_id},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        form = await hass.config_entries.flow.async_configure(shown["flow_id"])
        assert form["step_id"] == "panel_setup_check"
        flow = hass.config_entries.flow._progress[shown["flow_id"]]
        offer.reset_mock()
        hold = True
        retry = asyncio.create_task(
            hass.config_entries.flow.async_configure(shown["flow_id"], {})
        )
        await reading.wait()
        hass.config_entries.flow.async_abort(shown["flow_id"])
        release.set()
        with contextlib.suppress(UnknownFlow):
            await retry
        await hass.async_block_till_done(wait_background_tasks=True)

    assert flow._setup_waiter is None
    offer.assert_not_awaited()
    assert hass.config_entries.flow.async_progress() == []
    assert (await _stored(hass, receipt.job_id)).phase is (
        InstallPhase.HEALTHY_UNCLAIMED
    )

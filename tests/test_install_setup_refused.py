"""A healthy install finishes its setup where it reached the panel.

The panel's host guard refuses a name that is not its own. These tests drive
the real config flow, receipt store and executor, with the panel's setup read
answered over HTTP, refusals included.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.panel_assistant import install_executor
from custom_components.panel_assistant.client import HaPaneldClient
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
    no error and nothing logged, however often Submit was pressed.
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
        assert form["step_id"] == "panel_setup"
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
            "Panel Assistant setup refused: step=panel_setup "
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

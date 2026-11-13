"""An install whose panel cannot be reached on port 8888 says so, and soon.

A firewall that lets ADB through but not the app's port 8888 used to hold the
dialog for up to twelve minutes with nothing in the log, then ask for a
verification no one could do. The executor test drives the real executor with a
panel that drops every request; the flow test drives the real config flow and
executor from the address form to the panel added once the port is open.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from custom_components.panel_assistant import install_executor
from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    NoAnswerError,
    PanelAddress,
)
from custom_components.panel_assistant.const import DEFAULT_TIMEOUT_SECONDS, DOMAIN
from custom_components.panel_assistant.install_artifacts import (
    InstallArtifact as CustodiedArtifact,
)
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
    InstallResultCode,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)

from . import config_flow_helpers, install_executor_harness
from .config_flow_helpers import (
    DESCRIPTOR,
    RELEASE,
    _choose_version,
    _start_step,
)
from .http_fakes import FakeResponse, FakeSession
from .install_executor_harness import (
    CREDENTIAL_ID,
    Harness,
    create_job,
    target,
)

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
emulate_home_assistant_store_file = (
    install_executor_harness.emulate_home_assistant_store_file
)

PANEL = target()
PANEL_IP = PANEL.pinned_address
CREDENTIAL = AdbCredential(signer=object(), generation_id=CREDENTIAL_ID)
CLEAN_PANEL = InstallTargetProbe(
    state=InstallTargetState.INSTALL_CANDIDATE,
    model=PANEL.model,
    serial=PANEL.adb_serial,
    primary_abi=PANEL.primary_abi,
    android_sdk=PANEL.android_sdk,
)
FLOW = "custom_components.panel_assistant.config_flow"
EXECUTOR_LOG = "custom_components.panel_assistant.install_executor"
JOBS_LOG = "custom_components.panel_assistant.install_jobs"
# How long a person may be left at the dialog when port 8888 never answers.
ONE_MINUTE = 60.0


def _waiting_lines(
    caplog: pytest.LogCaptureFixture, address: str
) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == EXECUTOR_LOG
        and "port 8888" in record.getMessage()
        and address in record.getMessage()
    ]


async def test_a_panel_that_drops_port_8888_ends_the_install_in_a_minute(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every request waits out its timeout; the wait still ends at the bound.

    The first unanswered request is logged at once, naming the panel and the
    port, so the log explains the wait while the dialog is still open.
    """
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    harness = Harness(monkeypatch)
    harness.health_error = NoAnswerError()
    clock = [0.0]
    logged_before_second_request: list[bool] = []
    answer = install_executor.HaPaneldClient.async_get_health

    async def dropped(self: object) -> object:
        if harness.health_calls == 1:
            logged_before_second_request.append(
                bool(_waiting_lines(caplog, receipt.target.address))
            )
        # A dropped request is answered by nothing until its timeout.
        clock[0] += DEFAULT_TIMEOUT_SECONDS
        return await answer(self)

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(install_executor.HaPaneldClient, "async_get_health", dropped)
    monkeypatch.setattr(install_executor.asyncio, "sleep", sleep)
    monkeypatch.setattr(install_executor, "monotonic", lambda: clock[0], raising=False)

    with caplog.at_level(logging.INFO, logger=EXECUTOR_LOG):
        completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)

    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert completed.result_subcode == "health:cannot_connect"
    # One request may still be under way when the minute runs out.
    assert ONE_MINUTE <= clock[0] <= ONE_MINUTE + DEFAULT_TIMEOUT_SECONDS + 2
    assert logged_before_second_request == [True]
    waiting = _waiting_lines(caplog, receipt.target.address)
    assert len(waiting) == 1, "the wait is logged once, not on every attempt"
    assert waiting[0].levelno == logging.INFO
    assert "wait=60s" in waiting[0].getMessage()


@pytest.fixture
def pins() -> Iterator[None]:
    """An IP literal pins to itself and stays pinned."""

    async def _pin(_hass: HomeAssistant, address: PanelAddress) -> PinnedPanelTarget:
        return PinnedPanelTarget(original=address, pinned=address)

    async def _revalidate(
        _hass: HomeAssistant, pinned: PinnedPanelTarget
    ) -> PinnedPanelTarget:
        return pinned

    with (
        patch(f"{FLOW}.async_pin_install_target", AsyncMock(side_effect=_pin)),
        patch(
            f"{FLOW}.async_revalidate_install_target",
            AsyncMock(side_effect=_revalidate),
        ),
    ):
        yield


@contextmanager
def _clean_panel() -> Iterator[None]:
    """Network ADB open, no app yet, and nothing answering on port 8888."""
    with (
        patch(
            f"{FLOW}.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            f"{FLOW}.async_probe_install_target", AsyncMock(return_value=CLEAN_PANEL)
        ),
        patch(f"{FLOW}.async_resolve_install_choice", AsyncMock(return_value=RELEASE)),
        patch(f"{FLOW}.async_get_adb_credential", AsyncMock(return_value=CREDENTIAL)),
        patch(
            f"{FLOW}.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
    ):
        yield


async def _add(hass: HomeAssistant, address: str) -> dict[str, Any]:
    form = await _start_step(hass, "add_panel")
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], {CONF_ADDRESS: address}
    )


def _install_issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [
        issue
        for (domain, _issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and (issue.translation_key or "").startswith("installer_")
    ]


async def _install_without_port_8888(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> tuple[Harness, dict[str, Any]]:
    """Install by address while nothing on the panel answers on port 8888.

    ADB installs and starts the app; Home Assistant never hears it on 8888.
    """
    harness = Harness(monkeypatch, expected_version=RELEASE.version)
    harness.health_error = NoAnswerError()
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)
    downloaded = harness.async_download

    async def download_the_chosen_release(*args: Any) -> CustodiedArtifact:
        # The stand-in download yields the release this dialog chose.
        custodied = await downloaded(*args)
        return replace(
            custodied, size=DESCRIPTOR.apk_size, sha256=DESCRIPTOR.apk_sha256
        )

    monkeypatch.setattr(
        install_executor,
        "async_download_install_artifact",
        download_the_chosen_release,
    )
    with _clean_panel():
        version = await _add(hass, PANEL_IP)
        preview = await _choose_version(hass, version)
        progress = await hass.config_entries.flow.async_configure(
            preview["flow_id"], {}
        )
        assert progress["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(progress["flow_id"])
    assert harness.events.count("launch") >= 1, "the app was installed and started"
    return harness, result


@pytest.mark.usefixtures("pins")
async def test_an_install_that_cannot_reach_port_8888_says_so(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The dialog, the log and the Repair name port 8888, not a verification."""
    with caplog.at_level(logging.INFO):
        _harness, result = await _install_without_port_8888(hass, monkeypatch)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_health_unreachable"
    copy = await async_get_translations(hass, "en", "config", {DOMAIN})
    shown = copy[f"component.{DOMAIN}.config.abort.install_health_unreachable"]
    assert "could not reach it on port 8888" in shown
    assert "Do not retry" not in shown
    assert _waiting_lines(caplog, PANEL_IP)
    assert any(
        record.name == JOBS_LOG
        and record.levelno == logging.WARNING
        and "subcode=health:cannot_connect" in record.getMessage()
        for record in caplog.records
    )
    (issue,) = _install_issues(hass)
    assert issue.translation_key == "installer_failure_health_unreachable"
    assert issue.translation_placeholders["panel"] == PANEL_IP
    help_page = parse_qs(urlsplit(issue.learn_more_url).query)["issue"]
    assert help_page == ["installer_failure_health_check_failed"]
    repairs = await async_get_translations(hass, "en", "issues", {DOMAIN})
    title = repairs[
        f"component.{DOMAIN}.issues.installer_failure_health_unreachable.title"
    ]
    assert "port 8888" in title


class _AnswersOnceThenSilent(FakeSession):
    """The port refuses once, then nothing answers on it any more."""

    def get(self, url: Any, **kwargs: Any) -> FakeResponse:
        if self.requests:
            self.error = TimeoutError()
        return super().get(url, **kwargs)


@pytest.mark.parametrize(
    ("answer", "unreachable"),
    [
        (lambda: FakeSession(error=TimeoutError()), True),
        (lambda: FakeSession(FakeResponse(403, b"host not allowed")), False),
        (lambda: FakeSession(FakeResponse(503, b"")), False),
        (lambda: FakeSession(FakeResponse(200, b"not health")), False),
        (lambda: _AnswersOnceThenSilent(FakeResponse(403, b"")), False),
    ],
    ids=["no_answer", "http_403", "http_503", "not_health", "http_403_then_silent"],
)
async def test_only_a_port_that_never_answers_is_called_unreachable(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    answer: Callable[[], FakeSession],
    unreachable: bool,
) -> None:
    """The real client tells the executor whether anything answered on 8888.

    A refusal such as the Host guard's 403, a failing app or a reply that is
    not health all came from the port, so they keep the verification result;
    only a connection that never got a response names port 8888.
    """
    manager = InstallJobManager(hass)
    receipt = await create_job(manager)
    Harness(monkeypatch)
    session = answer()
    monkeypatch.setattr(install_executor, "HaPaneldClient", HaPaneldClient)
    monkeypatch.setattr(
        install_executor, "async_get_clientsession", lambda _hass: session
    )
    monkeypatch.setattr(install_executor, "_HEALTH_RETRY_SECONDS", 0)

    completed = await InstallExecutor(hass, manager).async_wait(receipt.job_id)
    await hass.async_block_till_done()

    assert session.requests, "the panel's health was asked"
    assert completed.phase is InstallPhase.RECOVERY_REQUIRED
    assert completed.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert (completed.result_subcode == "health:cannot_connect") is unreachable
    (issue,) = _install_issues(hass)
    assert issue.translation_key == (
        "installer_failure_health_unreachable"
        if unreachable
        else "installer_failure_verification_required"
    )

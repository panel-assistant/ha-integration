"""An unfinished install never blocks its panel.

Each test drives the real config flow against the real receipt store and the
real executor; only the network, ADB and the panel itself are stand-ins.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_ZEROCONF
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.panel_assistant.adb_credentials import AdbCredential
from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelAddress,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_executor import (
    async_get_install_executor,
    async_resume_loaded_install_jobs,
)
from custom_components.panel_assistant.install_jobs import (
    InstallJobReceipt,
    InstallPhase,
    InstallResultCode,
    async_get_install_job_manager,
)
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)

from . import config_flow_helpers, install_executor_harness
from .config_flow_helpers import RELEASE, _choose_version, _start_step
from .install_executor_harness import CREDENTIAL_ID, Harness, seed_phase, target

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
emulate_home_assistant_store_file = (
    install_executor_harness.emulate_home_assistant_store_file
)

SEEDED = target()
SEEDED_IP = SEEDED.pinned_address
OTHER_IP = "192.168.250.77"
CREDENTIAL = AdbCredential(signer=object(), generation_id=CREDENTIAL_ID)
CLEAN_PANEL = InstallTargetProbe(
    state=InstallTargetState.INSTALL_CANDIDATE,
    model=SEEDED.model,
    serial=SEEDED.adb_serial,
    primary_abi=SEEDED.primary_abi,
    android_sdk=SEEDED.android_sdk,
)
FLOW = "custom_components.panel_assistant.config_flow"
FLOW_LOG = "custom_components.panel_assistant.config_flow"
EXECUTOR_LOG = "custom_components.panel_assistant.install_executor"


@pytest.fixture(autouse=True)
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The panel, ADB and download stand-ins behind the real executor."""
    return Harness(monkeypatch, expected_version=RELEASE.version)


@pytest.fixture(autouse=True)
def pins() -> Iterator[dict[str, str]]:
    """Resolve the seeded host name to its panel; an IP literal pins to itself."""
    table = {SEEDED.address: SEEDED_IP}

    async def _pin(_hass: HomeAssistant, address: PanelAddress) -> PinnedPanelTarget:
        return PinnedPanelTarget(
            original=address,
            pinned=PanelAddress(
                host=table.get(address.host, address.host), port=address.port
            ),
        )

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
        yield table


@contextmanager
def _clean_panel() -> Iterator[None]:
    """A panel with network ADB open and no app: the flow offers to install."""
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


async def _choose(hass: HomeAssistant, menu: dict[str, Any], option: str) -> dict:
    assert menu["type"] is FlowResultType.MENU
    assert option in menu["menu_options"]
    return await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": option}
    )


async def _stored(hass: HomeAssistant, job_id: str) -> InstallJobReceipt:
    return await (await async_get_install_job_manager(hass)).async_get(job_id)


def _install_issues(hass: HomeAssistant) -> list[str]:
    return [
        issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN
    ]


def _logged(caplog: pytest.LogCaptureFixture, logger: str, *parts: str) -> bool:
    return any(
        record.name == logger and all(part in record.getMessage() for part in parts)
        for record in caplog.records
    )


@pytest.mark.parametrize(
    "phase", [InstallPhase.DOWNLOADING, InstallPhase.HEALTHY_UNCLAIMED]
)
async def test_the_same_panel_by_another_name_is_offered_its_unfinished_install(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, phase: InstallPhase
) -> None:
    """Typing the panel's IP address after installing by host name is no fault."""
    receipt, _manager = await seed_phase(hass, phase)

    with caplog.at_level(logging.WARNING):
        result = await _add(hass, SEEDED_IP)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "install_unfinished"
    assert result["menu_options"] == ["resume_install", "cancel_install"]
    assert result["description_placeholders"] == {
        "address": SEEDED_IP,
        "existing_address": SEEDED.address,
        "version": receipt.artifact.version_name,
    }
    assert (await _stored(hass, receipt.job_id)).phase is phase
    assert _logged(
        caplog,
        FLOW_LOG,
        "unfinished install",
        f"requested={SEEDED_IP}",
        f"existing={SEEDED.address}",
        f"stage={phase.value}",
        f"job={receipt.job_id}",
    )


async def test_a_panel_whose_name_now_resolves_elsewhere_is_offered_its_install(
    hass: HomeAssistant, pins: dict[str, str]
) -> None:
    """A panel that moved network keeps its name and meets its old install."""
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    pins[SEEDED.address] = OTHER_IP

    result = await _add(hass, SEEDED.address)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "install_unfinished"
    assert result["description_placeholders"]["existing_address"] == SEEDED.address
    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_cancelling_an_unstarted_install_frees_the_panel_for_a_new_one(
    hass: HomeAssistant, harness: Harness
) -> None:
    """Cancel ends the old job as cancelled and the same dialog installs afresh."""
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)

    with _clean_panel():
        menu = await _add(hass, SEEDED_IP)
        version = await _choose(hass, menu, "cancel_install")
        ended = await _stored(hass, receipt.job_id)
        preview = await _choose_version(hass, version)
        progress = await hass.config_entries.flow.async_configure(
            preview["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert ended.phase is InstallPhase.CANCELLED
    assert ended.result_code is InstallResultCode.CANCELLED_BY_USER
    assert ended.cancel_requested
    assert harness.local_cleanup_job_ids, "the old job's download is removed first"
    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    jobs = await (await async_get_install_job_manager(hass)).async_list()
    fresh = [job for job in jobs if job.job_id != receipt.job_id]
    assert len(fresh) == 1
    assert fresh[0].target.address == SEEDED_IP
    assert fresh[0].target.adb_serial == SEEDED.adb_serial


async def test_cancelling_a_healthy_unclaimed_install_lets_it_go_without_a_repair(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The app stays installed; its receipt is ended by name and nothing is raised."""
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)

    with _clean_panel(), caplog.at_level(logging.WARNING):
        menu = await _add(hass, SEEDED_IP)
        result = await _choose(hass, menu, "cancel_install")
        await hass.async_block_till_done()

    ended = await _stored(hass, receipt.job_id)
    assert ended.phase is InstallPhase.RECOVERY_REQUIRED
    assert ended.result_code is InstallResultCode.VERIFICATION_REQUIRED
    assert ended.failure_stage is InstallPhase.HEALTHY_UNCLAIMED
    assert ended.result_subcode == "job:cancelled_by_owner"
    assert ended.health_checked_at is None
    assert _install_issues(hass) == []
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    assert _logged(
        caplog,
        EXECUTOR_LOG,
        "ended an unfinished install",
        "reason=cancelled_by_owner",
        f"job={receipt.job_id}",
    )


async def test_carrying_on_resumes_the_same_job_under_its_own_address(
    hass: HomeAssistant, harness: Harness
) -> None:
    """Carry on attaches to the install already started; no second job exists."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)

    menu = await _add(hass, SEEDED_IP)
    progress = await _choose(hass, menu, "resume_install")
    await hass.async_block_till_done()

    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    assert progress["step_id"] == "install_progress"
    assert progress["description_placeholders"] == {"address": SEEDED.address}
    jobs = await (await async_get_install_job_manager(hass)).async_list()
    assert [job.job_id for job in jobs] == [receipt.job_id]
    assert jobs[0].phase is InstallPhase.HEALTHY_UNCLAIMED
    assert harness.events.count("install") == 1


async def test_a_second_address_of_one_panel_meets_its_install_at_the_install_press(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The serial is known only at Install; that is where the same panel is seen."""
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)

    with _clean_panel(), caplog.at_level(logging.WARNING):
        preview = await _choose_version(hass, await _add(hass, OTHER_IP))
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})
        again = await _choose(hass, result, "cancel_install")

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "install_unfinished"
    assert result["menu_options"] == ["resume_install", "cancel_install"]
    assert result["description_placeholders"] == {
        "address": OTHER_IP,
        "existing_address": SEEDED.address,
        "version": receipt.artifact.version_name,
    }
    assert _logged(caplog, FLOW_LOG, "unfinished install", f"requested={OTHER_IP}")
    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.CANCELLED
    assert again["type"] is FlowResultType.FORM
    assert again["step_id"] == "choose_version"


@pytest.mark.parametrize(
    ("minutes", "ended"),
    [(14, False), (16, True)],
)
async def test_an_install_nobody_is_advancing_ends_before_it_can_refuse_the_panel(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    minutes: int,
    ended: bool,
) -> None:
    """After a quarter of an hour untouched, the old job is ended, not obeyed."""
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    freezer.tick(timedelta(minutes=minutes))

    with _clean_panel(), caplog.at_level(logging.WARNING):
        result = await _add(hass, SEEDED_IP)
        await hass.async_block_till_done()

    stored = await _stored(hass, receipt.job_id)
    if not ended:
        assert result["type"] is FlowResultType.MENU
        assert stored.phase is InstallPhase.DOWNLOADING
        return
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    assert stored.phase is InstallPhase.FAILED
    assert stored.result_code is InstallResultCode.TRANSPORT_FAILED
    assert stored.failure_stage is InstallPhase.DOWNLOADING
    assert stored.result_subcode == "job:stalled"
    assert _logged(
        caplog,
        EXECUTOR_LOG,
        "ended an unfinished install",
        f"panel={SEEDED.address}",
        "stage=downloading",
        "reason=stalled",
    )
    assert len(_install_issues(hass)) == 1


@pytest.mark.parametrize(("hours", "ended"), [(23, False), (25, True)])
async def test_a_healthy_install_nobody_claimed_ends_itself_after_a_day(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    hours: int,
    ended: bool,
) -> None:
    """No dialog and no new attempt is needed: time alone lets the receipt go."""
    receipt, _manager = await seed_phase(hass, InstallPhase.HEALTHY_UNCLAIMED)
    assert await async_resume_loaded_install_jobs(hass) == ()

    freezer.tick(timedelta(hours=hours))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    stored = await _stored(hass, receipt.job_id)
    if not ended:
        assert stored.phase is InstallPhase.HEALTHY_UNCLAIMED
        return
    assert stored.phase is InstallPhase.RECOVERY_REQUIRED
    assert stored.result_subcode == "job:unclaimed_expired"
    assert stored.failure_stage is InstallPhase.HEALTHY_UNCLAIMED
    assert _install_issues(hass) == []


async def test_a_stopped_worker_ends_its_job_by_itself_and_names_it(
    hass: HomeAssistant,
    harness: Harness,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A worker that died mid-copy leaves a job that ends on its own clock."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.stage_error = OSError("the copy broke")
    executor = await async_get_install_executor(hass)

    with pytest.raises(OSError, match="the copy broke"):
        await executor.async_wait(receipt.job_id)
    await hass.async_block_till_done()
    assert (await _stored(hass, receipt.job_id)).phase is InstallPhase.STAGING

    freezer.tick(timedelta(minutes=16))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    stored = await _stored(hass, receipt.job_id)
    assert stored.phase is InstallPhase.FAILED
    assert stored.result_code is InstallResultCode.TRANSPORT_FAILED
    assert stored.failure_stage is InstallPhase.STAGING
    assert stored.result_subcode == "job:worker_stopped"


async def test_adding_the_panel_again_after_its_worker_stopped_shows_its_result(
    hass: HomeAssistant, harness: Harness
) -> None:
    """Nobody is told to restart Home Assistant: the dead job ends at the press."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.stage_error = OSError("the copy broke")
    executor = await async_get_install_executor(hass)
    with pytest.raises(OSError, match="the copy broke"):
        await executor.async_wait(receipt.job_id)
    await hass.async_block_till_done()

    result = await _add(hass, SEEDED.address)
    with _clean_panel():
        again = await _add(hass, SEEDED.address)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_transport_failed"
    stored = await _stored(hass, receipt.job_id)
    assert stored.phase is InstallPhase.FAILED
    assert stored.result_subcode == "job:worker_stopped"
    assert again["type"] is FlowResultType.FORM
    assert again["step_id"] == "choose_version"


async def test_another_name_meets_no_install_whose_worker_stopped(
    hass: HomeAssistant, harness: Harness
) -> None:
    """A dead job is ended, not offered, when the panel is added by another name."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.stage_error = OSError("the copy broke")
    executor = await async_get_install_executor(hass)
    with pytest.raises(OSError, match="the copy broke"):
        await executor.async_wait(receipt.job_id)
    await hass.async_block_till_done()

    with _clean_panel():
        result = await _add(hass, SEEDED_IP)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    assert (await _stored(hass, receipt.job_id)).result_subcode == "job:worker_stopped"


async def test_the_same_name_resumes_a_safe_job_however_long_it_stood(
    hass: HomeAssistant, harness: Harness, freezer: FrozenDateTimeFactory
) -> None:
    """Asking by the name the install was started under still carries it on."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    freezer.tick(timedelta(hours=3))

    progress = await _add(hass, SEEDED.address)
    await hass.async_block_till_done()

    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    (job,) = await (await async_get_install_job_manager(hass)).async_list()
    assert job.job_id == receipt.job_id
    assert job.phase is InstallPhase.HEALTHY_UNCLAIMED


async def test_an_open_dialog_shows_the_result_when_its_worker_stops(
    hass: HomeAssistant, harness: Harness
) -> None:
    """The dialog that was watching the install ends with what happened to it."""
    harness.expected_version = "0.1.0"
    receipt, _manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    harness.stage_error = OSError("the copy broke")

    progress = await _add(hass, SEEDED.address)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(progress["flow_id"])

    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    assert harness.events.count("stage") == 1
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_transport_failed"
    stored = await _stored(hass, receipt.job_id)
    assert stored.phase is InstallPhase.FAILED
    assert stored.failure_stage is InstallPhase.STAGING
    assert stored.result_subcode == "job:worker_stopped"


async def test_an_unreadable_receipt_store_is_refused_with_its_cause_in_the_log(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The dialog still refuses; the log now says which check refused."""
    await seed_phase(hass, InstallPhase.DOWNLOADING)
    hass_storage[f"{DOMAIN}.install_jobs"]["data"]["format"] = "not-the-format"

    with caplog.at_level(logging.WARNING):
        result = await _add(hass, SEEDED_IP)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "install_receipt_error"}
    refusal = [
        record
        for record in caplog.records
        if record.name == FLOW_LOG and "setup refused" in record.getMessage()
    ]
    assert len(refusal) == 1
    message = refusal[0].getMessage()
    assert "step=add_panel" in message
    assert "error=install_receipt_error" in message
    assert f"panel={SEEDED_IP}" in message
    assert "cause=InstallJobStoreError" in message
    assert refusal[0].exc_info is not None, "the traceback names the failed check"


async def test_an_ordinary_refusal_is_logged_with_what_the_panel_answered(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Every refusal a person sees has a log line a helper can read."""
    unreachable = InstallTargetProbe(state=InstallTargetState.ADB_UNREACHABLE)
    with (
        patch(
            f"{FLOW}.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            f"{FLOW}.async_probe_install_target", AsyncMock(return_value=unreachable)
        ),
        patch(f"{FLOW}._async_host_answers", AsyncMock(return_value=False)),
        caplog.at_level(logging.WARNING),
    ):
        result = await _add(hass, OTHER_IP)

    assert result["errors"] == {"base": "panel_unreachable"}
    assert _logged(
        caplog,
        FLOW_LOG,
        "setup refused",
        "step=add_panel",
        "error=panel_unreachable",
        f"panel={OTHER_IP}",
        f"cause=adb_state=adb_unreachable pinned={OTHER_IP}",
    )


async def test_a_refusing_end_of_the_dialog_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """An install that ended badly is a refusal; a duplicate announcement is not."""
    harness_receipt, manager = await seed_phase(hass, InstallPhase.DOWNLOADING)
    claimed = await manager.async_claim(
        harness_receipt.job_id, harness_receipt.revision
    )
    await manager.async_transition(
        claimed.job_id,
        claimed.revision,
        InstallPhase.FAILED,
        result_code=InstallResultCode.TRANSPORT_FAILED,
        result_subcode="transport:refused",
    )
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=FLOW_LOG):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "repair_finalize"},
            data={"job_id": harness_receipt.job_id},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_transport_failed"
    (record,) = [
        r for r in caplog.records if r.name == FLOW_LOG and "setup refused" in r.message
    ]
    assert record.levelno == logging.WARNING
    assert "error=install_transport_failed" in record.getMessage()


async def test_an_announcement_that_is_no_panel_is_not_logged_as_a_refusal(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Announcements arrive all day; an uninteresting one stays out of the log."""
    announcement = ZeroconfServiceInfo(
        ip_address=ip_address(OTHER_IP),
        ip_addresses=[ip_address(OTHER_IP)],
        port=4444,
        hostname="Android.local.",
        type="_adb._tcp.local.",
        name="adb-SERIAL9._adb._tcp.local.",
        properties={},
    )

    with caplog.at_level(logging.DEBUG, logger=FLOW_LOG):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=announcement
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_panel"
    (record,) = [
        r for r in caplog.records if r.name == FLOW_LOG and "setup refused" in r.message
    ]
    assert record.levelno == logging.DEBUG

"""The device setup pass, run only inside an operation the owner started.

The pass finishes what the panel's first-run setup asked of Home Assistant:
the home-screen request it left pending (``home_pending``), the device admin
the app locks the screen with, and the panel model's own setup
(``vendor_provisioning.py``). It changes panel settings, so it runs only when
the owner asked for it: at the end of an install (the add-panel flow, once the
panel reports its setup complete), at the end of an update the owner started,
or when the owner confirms its Repair. Nothing else starts it: not the poll, a
session connecting, entry setup after a restart, or a timer.

An operation records that it owes the pass (``CONF_DEVICE_SETUP_OWED``) before
it starts it, and the pass removes that record when it finishes. A pass the
panel cannot finish within its bounded wait, or one a restart or reload
interrupted, becomes one Repair, which runs it again only when confirmed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from adb_shell.adb_device_async import AdbDeviceAsync
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import CONF_DEVICE_SETUP, CONF_DEVICE_SETUP_OWED, DOMAIN, help_url
from .panel_operations import claim_panel_operation, release_panel_operation

# How long an operation's pass waits for the panel to be ready: the same
# twelve tries, fifteen seconds apart, that the permission repair after an
# update allows the panel's ADB to answer again.
from .update_panel_install import (
    _PERMISSION_REPAIR_ATTEMPTS as SETUP_ATTEMPTS,
)
from .update_panel_install import (
    _PERMISSION_REPAIR_RETRY_SECONDS as SETUP_RETRY_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

ISSUE_DEVICE_SETUP = "device_setup_unfinished"
OWED_INSTALL = "install"
OWED_UPDATE = "update"
_DATA_RUNS = "device_setup_runs"


class SetupApprovalPending(Exception):
    """The panel asked someone at its screen to allow the pass's shell."""


def device_setup_issue_id(entry_id: str) -> str:
    """One Repair per panel."""
    return f"{ISSUE_DEVICE_SETUP}_{entry_id}"


def _learn_more_url() -> str:
    """The generic Repair destination, until the issue has its own page."""
    return help_url("repair", issue=ISSUE_DEVICE_SETUP)


@dataclass(slots=True)
class _Run:
    """The pass an operation started for one entry, while it runs."""

    task: asyncio.Task[None] | None = None
    # Raised by every request, so a pass that finishes for an earlier request
    # does not settle one made while it ran.
    generation: int = 0


def _runs(hass: HomeAssistant) -> dict[str, _Run]:
    runs: dict[str, _Run] = hass.data.setdefault(DOMAIN, {}).setdefault(_DATA_RUNS, {})
    return runs


def _running(hass: HomeAssistant, entry_id: str) -> _Run | None:
    run = _runs(hass).get(entry_id)
    return run if run is not None and run.task is not None else None


def _coordinator(entry: ConfigEntry) -> Any:
    """The loaded entry's panel coordinator, or None before setup or after unload."""
    return getattr(getattr(entry, "runtime_data", None), "coordinator", None)


@callback
def async_mark_owed(hass: HomeAssistant, entry: ConfigEntry, reason: str) -> None:
    """Record that the owner's install or update owes this panel the pass.

    Written before the pass starts, so a restart or reload before it finishes
    leaves the Repair rather than a pass nobody owes.
    """
    owed = entry.data.get(CONF_DEVICE_SETUP_OWED)
    if owed != OWED_INSTALL:
        # An install's pass includes the vendor setup an update's leaves out.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICE_SETUP_OWED: reason}
        )
    run = _running(hass, entry.entry_id)
    if run is not None:
        run.generation += 1


@callback
def async_start(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run the pass an operation recorded as owed, once, with a bounded wait."""
    if CONF_DEVICE_SETUP_OWED not in entry.data or _running(hass, entry.entry_id):
        # A running pass sees the newer request through its generation.
        return
    run = _runs(hass).setdefault(entry.entry_id, _Run())
    run.task = entry.async_create_background_task(
        hass,
        _async_run(hass, entry.entry_id, run),
        f"{DOMAIN} device setup for {entry.title}",
    )


@callback
def async_request(hass: HomeAssistant, entry: ConfigEntry, reason: str) -> None:
    """An owner-started install or update: record the pass, then run it."""
    async_mark_owed(hass, entry, reason)
    async_start(hass, entry)


@callback
def async_reconcile(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """At entry setup: a pass a restart or reload interrupted becomes the Repair.

    Sends nothing to the panel. An entry no operation owed the pass to has
    nothing to reconcile.
    """
    if CONF_DEVICE_SETUP_OWED in entry.data and not _running(hass, entry.entry_id):
        async_raise_issue(hass, entry)


@callback
def async_raise_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask the owner to confirm the pass the panel was not ready for."""
    from .device import panel_display_name

    ir.async_create_issue(
        hass,
        DOMAIN,
        device_setup_issue_id(entry.entry_id),
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_SETUP,
        learn_more_url=_learn_more_url(),
        translation_placeholders={
            "panel": panel_display_name(hass, entry),
            "learn_more_url": _learn_more_url(),
        },
        data={"entry_id": entry.entry_id},
    )


@callback
def _async_finished(
    hass: HomeAssistant, entry: ConfigEntry, record: dict[str, Any], settled: bool
) -> None:
    """Keep the record; a pass for the latest request also settles what is owed."""
    data = {**entry.data, CONF_DEVICE_SETUP: record}
    if settled:
        data.pop(CONF_DEVICE_SETUP_OWED, None)
        ir.async_delete_issue(hass, DOMAIN, device_setup_issue_id(entry.entry_id))
    if data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=data)


async def _async_run(hass: HomeAssistant, entry_id: str, run: _Run) -> None:
    """Try the pass a bounded number of times, then leave the Repair."""
    try:
        attempt = 0
        while attempt < SETUP_ATTEMPTS:
            if attempt:
                await asyncio.sleep(SETUP_RETRY_SECONDS)
            attempt += 1
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or CONF_DEVICE_SETUP_OWED not in entry.data:
                # Removed, or the Repair finished it meanwhile.
                return
            coordinator = _coordinator(entry)
            if coordinator is None:
                # Not loaded: what is owed stays recorded, and the entry's
                # next setup turns it into the Repair.
                return
            generation = run.generation
            try:
                record = await async_attempt(
                    hass,
                    entry,
                    coordinator,
                    vendor=entry.data.get(CONF_DEVICE_SETUP_OWED) != OWED_UPDATE,
                )
            except SetupApprovalPending:
                # Hardened mode: someone must be at the panel for every shell.
                # One prompt, then the Repair, never a prompt per retry.
                break
            if record is None:
                continue
            settled = generation == run.generation
            if hass.config_entries.async_get_entry(entry_id) is entry:
                _async_finished(hass, entry, record, settled)
            if settled:
                return
            # Another operation asked while this one ran: its pass gets the
            # full wait of its own.
            attempt = 0
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and CONF_DEVICE_SETUP_OWED in entry.data:
            _LOGGER.info(
                "The panel %s was not ready to finish its setup; a Repair asks "
                "the owner to run it",
                entry.title,
            )
            async_raise_issue(hass, entry)
    finally:
        if _runs(hass).get(entry_id) is run:
            del _runs(hass)[entry_id]


async def async_admin_claim(
    device: AdbDeviceAsync, target: Any, package: str
) -> tuple[bool, str]:
    """Claim the device admin if absent; return its readback and the HOME seen.

    Only a successful, matching readback proves the admin, on either route.
    """
    from secrets import token_hex

    from . import adb_protocol as adb
    from . import local_commands as typed
    from .app_identity import DEVICE_ADMIN_RECEIVER
    from .home_chooser import HOME_QUERY
    from .install_adb import _async_typed

    if target.local_commands is not None:
        return typed.admin_from_fields(await _async_typed(target, typed.VERB_ADMIN))
    listed = f"{package}/{DEVICE_ADMIN_RECEIVER.removeprefix(package)}:"

    def command(nonce: str) -> str:
        return "; ".join(
            (
                f"echo HAPANELD_ADMIN_BEGIN:{nonce}",
                adb.device_admin_claim_command(package),
                # Only a successful, matching readback proves admin.
                "policy=$(dumpsys device_policy 2>/dev/null); result=$?",
                f'case "$result:$policy" in 0:*"{listed}"*) echo active ;; '
                "*) echo unknown ;; esac",
                f'echo "home:$({HOME_QUERY})"',
                f"echo HAPANELD_ADMIN_END:{nonce}:0",
            )
        )

    nonce = token_hex(16)
    lines, status = adb._parse_single_section(
        await adb._async_shell(
            device, command(nonce), read_timeout=adb._READ_TIMEOUT_SECONDS
        ),
        prefix="ADMIN",
        nonce=nonce,
    )
    if status != 0 or len(lines) != 2:
        return False, ""
    return lines[0] == "active", lines[1].removeprefix("home:")


async def async_attempt(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Any,
    *,
    vendor: bool = True,
    approval: bool = False,
    refresh: bool = False,
) -> dict[str, Any] | None:
    """Run the pass once; return its record when it finished.

    It acts on the coordinator's latest health, which the regular poll keeps
    current between an operation's tries; ``refresh`` reads it again first.

    Finished means Android read the device admin back and the panel no longer
    reports its home-screen request pending: claimed here, or handed back by
    the owner meanwhile. ``vendor`` is False after an update, whose own grant
    repair has already applied the vendor setup. Waits on an on-panel
    approval only with ``approval`` (a confirmed Repair); otherwise a panel
    that asks for one raises SetupApprovalPending, so prompts never stack.
    """
    from . import adb_protocol as adb
    from . import local_adb
    from .adb_credentials import (
        AdbCredentialError,
        async_get_durable_adb_credential,
    )
    from .app_identity import EQUIVALENT_HOME_COMPONENTS, LAUNCH_COMPONENTS
    from .client import HaPaneldError
    from .install_adb import (
        _async_identity_root,
        _async_repair_app_permissions,
        _async_vendor_setup,
    )
    from .install_jobs import async_get_install_job_manager
    from .install_network import (
        InstallNetworkError,
        async_pin_entry_target,
        async_revalidate_install_target,
    )
    from .install_receipt import InstallJobError
    from .provisioning import InstallTargetState, async_probe_install_target
    from .repairs_permission import turned_off_reporter

    if refresh:
        await coordinator.async_refresh()
    snapshot = coordinator.data
    health = None if snapshot is None else snapshot.health
    if (
        health is None
        or not coordinator.last_update_success
        or not health.setup_complete
        or not coordinator.available
        or not health.discovery_id
        or health.discovery_id != entry.unique_id
        or health.package not in LAUNCH_COMPONENTS
        or coordinator.identity_mismatch
        or hass.is_stopping
        or not claim_panel_operation(hass, entry.entry_id)
    ):
        return None
    package = health.package
    assert package is not None
    address = entry.data[CONF_ADDRESS]
    session = local_adb.session_for(hass, entry)
    device: AdbDeviceAsync | None = None
    admin = False
    root = False
    try:
        async with asyncio.timeout(None if session is not None else 45):
            manager = await async_get_install_job_manager(hass)
            target = await local_adb.async_target(
                hass, entry, coordinator.client.address, approval=approval
            )
            signer = None
            if target is None:
                credential = await async_get_durable_adb_credential(hass)
                pinned = await async_pin_entry_target(
                    hass, coordinator.client.address, health, entry.unique_id
                )
                if pinned is None:
                    return None
                if await manager.async_find_active(address, pinned.pinned.stored_value):
                    return None
                probe = await async_probe_install_target(
                    pinned.pinned, credential.signer
                )
                target = probe.adb_target(pinned.pinned)
                if probe.state is not InstallTargetState.INSTALLED or target is None:
                    return None
                await async_revalidate_install_target(hass, pinned)
                if (
                    hass.config_entries.async_get_entry(entry.entry_id) is not entry
                    or entry.unique_id != health.discovery_id
                    or entry.data[CONF_ADDRESS] != address
                    or await manager.async_find_active(
                        address, pinned.pinned.stored_value
                    )
                ):
                    return None
                signer = credential.signer
            elif any(
                not receipt.is_terminal
                and address in (receipt.target.address, receipt.target.pinned_address)
                for receipt in await manager.async_list()
            ):
                return None
            if (
                hass.config_entries.async_get_entry(entry.entry_id) is not entry
                or entry.data[CONF_ADDRESS] != address
            ):
                return None
            device = await adb._async_connect(target, signer, authorize=False)
            root = await _async_identity_root(device, target) in (
                adb.AdbRootMode.ROOT_ADBD,
                adb.AdbRootMode.ROOT_SU,
            )
            if health.home_pending:
                # Only initial setup's durable pending intent permits a HOME
                # takeover here. Explicit handback clears it, across reloads.
                await _async_repair_app_permissions(
                    device,
                    target,
                    package,
                    LAUNCH_COMPONENTS[package][-1],
                    deferred_setup=True,
                    report_denials=turned_off_reporter(hass, entry.entry_id),
                )
            admin, home = await async_admin_claim(device, target, package)
            own_homes = {
                home
                for launch in LAUNCH_COMPONENTS[package]
                for home in EQUIVALENT_HOME_COMPONENTS[launch]
            }
            if admin and vendor and not health.home_pending and home in own_homes:
                # Native completion can finish HOME before this pass. Finish
                # vendor commissioning only while the app still owns HOME;
                # a deliberate handback is never undone.
                await _async_vendor_setup(device, target)
    except (
        AdbCredentialError,
        InstallNetworkError,
        InstallJobError,
        HaPaneldError,
        adb.InstallAdbError,
        adb._MalformedAdbResponse,
        adb._UnsafeAdbPacket,
        *adb._ADB_EXCEPTIONS,
        OSError,
        TimeoutError,
    ) as err:
        # Admin readback alone cannot finish a pass whose vendor I/O failed.
        _LOGGER.debug("The device setup pass could not finish on %s", entry.title)
        if not approval and local_adb.approval_pending(err):
            raise SetupApprovalPending from err
        return None
    finally:
        try:
            await adb._async_close(device)
        finally:
            release_panel_operation(hass, entry.entry_id)
    if not admin or hass.config_entries.async_get_entry(entry.entry_id) is not entry:
        return None
    # A home-screen request still pending after the claim is not finished.
    await coordinator.async_refresh()
    fresh = coordinator.data
    if (
        fresh is None
        or not coordinator.last_update_success
        or fresh.health.discovery_id != health.discovery_id
        or fresh.health.home_pending
    ):
        return None
    return {
        "did": health.discovery_id,
        "package": package,
        "version_code": health.version_code,
        "root": root,
    }


class DeviceSetupFlow(RepairsFlow):
    """Run the pass once, now, because the owner confirmed it."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        # Core passes issue metadata here; only the next step accepts consent.
        return await self.async_step_confirm_setup()

    async def async_step_confirm_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        from .device import panel_display_name

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None or entry.domain != DOMAIN:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(
                reason="entry_removed",
                description_placeholders={"learn_more_url": _learn_more_url()},
            )
        errors: dict[str, str] = {}
        if user_input is not None:
            coordinator = _coordinator(entry)
            if _running(self.hass, self._entry_id) is not None:
                errors["base"] = "panel_busy"
            elif coordinator is None:
                errors["base"] = "panel_not_ready"
            else:
                # The owner asked: a panel that wants approval on its screen
                # for each shell is waited on.
                record = await async_attempt(
                    self.hass,
                    entry,
                    coordinator,
                    vendor=entry.data.get(CONF_DEVICE_SETUP_OWED) != OWED_UPDATE,
                    approval=True,
                    refresh=True,
                )
                if record is not None:
                    if (
                        self.hass.config_entries.async_get_entry(entry.entry_id)
                        is entry
                    ):
                        _async_finished(self.hass, entry, record, True)
                    return self.async_create_entry(data={})
                errors["base"] = "panel_not_ready"
        return self.async_show_form(
            step_id="confirm_setup",
            data_schema=vol.Schema({}),
            description_placeholders={
                "panel": panel_display_name(self.hass, entry),
                "learn_more_url": _learn_more_url(),
            },
            errors=errors,
            last_step=True,
        )

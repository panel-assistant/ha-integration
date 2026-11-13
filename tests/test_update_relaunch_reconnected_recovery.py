"""A reconnected local-channel panel can recover a stopped updated app."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update_route
from custom_components.panel_assistant.adb_protocol import (
    InstallAdbError,
    InstallAdbErrorCode,
    LaunchOutcome,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.transport_sessions import (
    PanelSession,
    async_get_sessions,
)

from .test_update import _entity


async def _exercise_reconnected_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: frozenset[str],
    *,
    expect_recovery: bool,
) -> None:
    """Wait past the closed ADB lease, launch the signed build and prove HOME."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="alpha", entry_id="entry-id")
    entry.add_to_hass(hass)
    entity, client = _entity(hass)
    coordinator = entity.coordinator
    coordinator._entry_id = entry.entry_id
    client.address = "panel.local"
    coordinator.reachable = False
    original = coordinator.data
    coordinator.async_request_refresh = AsyncMock()
    sessions = async_get_sessions(hass)

    def connect(token: str, granted: frozenset[str]) -> PanelSession:
        session = PanelSession(
            entry.entry_id,
            "alpha",
            token,
            SimpleNamespace(subscriptions={}, send_message=MagicMock()),
            1,
            "owner",
            5,
            "0.9.9",
            1000,
            "c" * 64,
            granted,
            {},
            dt_util.utcnow(),
        )
        sessions.open(session)
        return session

    previous = connect("before-drop", frozenset({"management", "local_adb"}))
    previous.local_adb_identity = ("serial", "model", "arm64-v8a", 30)
    coordinator._admin_done = True
    sessions.close(previous, "connection_lost")
    reconnected = connect("reconnected", capabilities)
    assert reconnected.local_adb_identity is None
    # The replacement app never starts, ending the reconnected session too.
    sessions.close(reconnected, "connection_lost")
    now = 0.0
    monkeypatch.setattr(hass.loop, "time", lambda: now)

    async def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr(update_route.asyncio, "sleep", sleep)
    artifact = next(iter(entity._offer.release._candidates.values()))
    target = SimpleNamespace(
        serial="serial", model="model", primary_abi="arm64-v8a", android_sdk=30
    )
    credential = SimpleNamespace(signer=object())
    monkeypatch.setattr(
        update_route,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=credential),
    )
    monkeypatch.setattr(
        update_route, "async_pin_install_target", AsyncMock(return_value=object())
    )
    probes = []

    async def probe(*_args):
        probes.append(now)
        if now < 300:
            raise InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
        return target

    monkeypatch.setattr(update_route, "async_probe_adb_target", probe)
    monkeypatch.setattr(
        update_route,
        "async_preflight_install",
        AsyncMock(
            return_value=SimpleNamespace(target_installed=True, root_mode="root")
        ),
    )
    monkeypatch.setattr(
        update_route, "async_installed_artifact_size", AsyncMock(return_value=123)
    )
    launches = []

    async def launch(found, signer, descriptor, **_kwargs):
        launches.append((now, found, signer, descriptor))
        coordinator.data = replace(
            original,
            health=replace(
                original.health,
                version=artifact.version,
                build="1100",
                version_code=1100,
            ),
        )
        coordinator.reachable = True
        return LaunchOutcome.STARTED

    monkeypatch.setattr(update_route, "async_launch_installed_app", launch)
    if not expect_recovery:
        with pytest.raises(HomeAssistantError) as error:
            await entity._delivery.async_wait_for_build(
                hass, artifact, ("1000", original.health.package)
            )
        assert error.value.translation_key == "update_not_complete"
        # Tried once after the restart grace and once when the wait ends, each
        # giving up at its first probe: no lease is waited out without one.
        assert probes == [60, 240]
        assert launches == []
        return
    await entity._delivery.async_wait_for_build(
        hass, artifact, ("1000", original.health.package)
    )
    assert probes[0] == 240
    assert launches == [(300, target, credential.signer, artifact.descriptor)]
    assert entity._installed_code == 1100


async def test_update_relaunch_waits_for_reconnected_panels_recovery_lease(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A managed local channel waits until recovery can start the exact build."""
    await _exercise_reconnected_recovery(
        hass,
        monkeypatch,
        frozenset({"management", "local_adb"}),
        expect_recovery=True,
    )


@pytest.mark.parametrize(
    "capabilities",
    [frozenset({"local_adb"}), frozenset({"management"})],
    ids=["without-management", "without-local-adb"],
)
async def test_update_relaunch_without_local_management_gives_up_at_first_probe(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    capabilities: frozenset[str],
) -> None:
    """Other reconnected sessions do not defer the update failure for a lease."""
    await _exercise_reconnected_recovery(
        hass, monkeypatch, capabilities, expect_recovery=False
    )

"""Observable refusals of the authorized ADB update route."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import storage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import adb_credentials
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.adb_credentials import AdbCredentialError
from custom_components.panel_assistant.app_identity import LEGACY_PACKAGE_ID
from custom_components.panel_assistant.build_feed import BuildFeed, FeedBuild
from custom_components.panel_assistant.client import PanelHealth, normalize_address
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.feed_coordinator import BuildFeedCoordinator
from custom_components.panel_assistant.install_adb import InstallOutcome, LaunchOutcome
from custom_components.panel_assistant.install_network import PinnedPanelTarget
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.status import PanelStatus
from custom_components.panel_assistant.update import HaPaneldUpdateEntity
from custom_components.panel_assistant.update_coordinator import (
    PanelUpdateCoordinator,
    PanelUpdateSnapshot,
)


def _backup() -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", '{"discovery_id":"panel-a"}')
        archive.writestr("settings.json", "{}")
    return data.getvalue()


def _health(
    *,
    panel_id: str = "panel-a",
    package: str = LEGACY_PACKAGE_ID,
    discovery_id: str | None = None,
) -> PanelHealth:
    return PanelHealth(
        version="0.9.7-rc4",
        panel_id=panel_id,
        build="1000",
        config_hash="1a2b3c4d",
        package=package,
        discovery_id=discovery_id,
    )


@pytest.fixture
def route(
    hass: HomeAssistant, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """A signed feed and a reachable, authorized panel without an on-panel route."""
    hass.config.config_dir = str(tmp_path)
    apk = b"signed-apk"
    address = normalize_address("192.168.1.10")
    client = SimpleNamespace(
        address=address,
        async_backup_panel=AsyncMock(return_value=_backup()),
        async_get_version_code=AsyncMock(return_value=("0.9.7-rc4", 771)),
        async_stage_apk=AsyncMock(),
        async_commit_apk=AsyncMock(),
        async_start_panel_update=AsyncMock(),
    )
    coordinator = HaPaneldDataUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    coordinator.data = PanelSnapshot(
        health=_health(),
        status=PanelStatus(
            warning_count=0, capability_count=0, install_capability="none"
        ),
        status_error=None,
    )
    coordinator.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    updates = PanelUpdateCoordinator(hass, client)  # type: ignore[arg-type]
    updates.data = PanelUpdateSnapshot(operation=None, error=None)
    build = FeedBuild(
        version_code=772,
        version_name="0.9.7-rc4",
        apk_url=URL("https://feed.example/apks/update.apk"),
        apk_sha256=hashlib.sha256(apk).hexdigest(),
        apk_size=len(apk),
        commit="0" * 40,
        database_compatibility="hapaneld-db:v1:ha-paneld.db:11:14",
        min_sdk=26,
        package_id=LEGACY_PACKAGE_ID,
        published="2026-09-11T10:00:00Z",
    )
    feed = BuildFeedCoordinator(hass, URL("https://feed.example/maintainer.json"))
    feed.data = BuildFeed(channel="maintainer", builds=(build,))
    feed.last_update_success = True
    feed._verified_newest[LEGACY_PACKAGE_ID] = (build, apk)
    entity = HaPaneldUpdateEntity("entry-id", coordinator, updates, feed)
    entity.hass = hass
    entity.async_write_ha_state = MagicMock()
    entity._installed_code = 771
    entity._code_key = ("0.9.7-rc4", "1000")

    pinned = PinnedPanelTarget(address, address)
    credential = SimpleNamespace(signer=object(), generation_id="credential-a")
    probe = InstallTargetProbe(
        state=InstallTargetState.INSTALLED,
        serial="serial-a",
        model="model-a",
        primary_abi="arm64-v8a",
        android_sdk=30,
    )
    pinned_health = AsyncMock(return_value=_health())
    get_credential = AsyncMock(return_value=credential)
    pin = AsyncMock(return_value=pinned)
    revalidate = AsyncMock(return_value=pinned)
    probe_target = AsyncMock(return_value=probe)
    preflight = AsyncMock(
        return_value=SimpleNamespace(target_installed=True, root_mode="rootless")
    )
    adb_install = AsyncMock(return_value=InstallOutcome.INSTALLED)
    launch = AsyncMock(return_value=LaunchOutcome.STARTED)
    monkeypatch.setattr(panel_update, "async_get_clientsession", lambda _hass: object())
    monkeypatch.setattr(
        panel_update,
        "HaPaneldClient",
        lambda *_args: SimpleNamespace(async_get_health=pinned_health),
    )
    monkeypatch.setattr(
        panel_update, "async_get_durable_adb_credential", get_credential
    )
    monkeypatch.setattr(panel_update, "async_pin_install_target", pin)
    monkeypatch.setattr(panel_update, "async_revalidate_install_target", revalidate)
    monkeypatch.setattr(panel_update, "async_probe_install_target", probe_target)
    monkeypatch.setattr(panel_update, "async_preflight_install", preflight)
    monkeypatch.setattr(panel_update, "async_update_installed_apk", adb_install)
    monkeypatch.setattr(panel_update, "async_launch_installed_app", launch)
    return SimpleNamespace(
        entity=entity,
        client=client,
        pinned_health=pinned_health,
        get_credential=get_credential,
        pin=pin,
        revalidate=revalidate,
        probe_target=probe_target,
        preflight=preflight,
        adb_install=adb_install,
        launch=launch,
        credential=credential,
        pinned=pinned,
        probe=probe,
    )


@pytest.mark.parametrize("mismatch", ["panel_id", "package"])
async def test_pinned_http_peer_mismatch_withholds_offer_and_install(
    route: SimpleNamespace, mismatch: str
) -> None:
    """A pinned address must still answer as the configured panel and app."""
    other = {mismatch: "different-panel" if mismatch == "panel_id" else "other.app"}
    route.pinned_health.return_value = _health(**other)

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    route.client.async_backup_panel.assert_not_awaited()
    route.probe_target.assert_not_awaited()
    route.adb_install.assert_not_awaited()


async def test_pinned_peer_with_same_model_but_other_identity_gets_no_update(
    route: SimpleNamespace, hass: HomeAssistant
) -> None:
    """An address reassigned to a similar panel cannot admit an ADB update."""
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", unique_id="a" * 64, data={}
    )
    entry.add_to_hass(hass)
    route.pinned_health.return_value = _health(discovery_id="b" * 64)

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    route.probe_target.assert_not_awaited()
    route.adb_install.assert_not_awaited()


async def test_changed_health_identity_invalidates_cached_adb_offer(
    route: SimpleNamespace, hass: HomeAssistant
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="entry-id", unique_id="a" * 64, data={}
    )
    entry.add_to_hass(hass)
    route.entity.coordinator.data = replace(
        route.entity.coordinator.data, health=_health(discovery_id="a" * 64)
    )
    route.pinned_health.return_value = _health(discovery_id="a" * 64)
    await route.entity._async_refresh_route()
    assert route.entity.latest_version == "0.9.7-rc4 build 772"

    route.entity.coordinator.data = replace(
        route.entity.coordinator.data, health=_health(discovery_id="b" * 64)
    )
    assert route.entity.latest_version == route.entity.installed_version
    await route.entity._async_refresh_route()
    route.preflight.assert_awaited_once()
    route.adb_install.assert_not_awaited()


async def test_existing_keyless_shelly_offers_and_installs_from_ha(
    route: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, hass: HomeAssistant
) -> None:
    """An existing entry can use open ADB without prior HA key provisioning."""

    # The HA pytest fixture mocks Store writes; exercise the durable file path.
    async def write_data(store: storage.Store, data: dict[str, object]) -> None:
        await store.hass.async_add_executor_job(store._write_data, data)

    monkeypatch.setattr(
        panel_update,
        "async_get_adb_credential",
        adb_credentials.async_get_adb_credential,
    )
    monkeypatch.setattr(
        panel_update,
        "async_get_durable_adb_credential",
        adb_credentials.async_get_durable_adb_credential,
    )
    deliver = AsyncMock()
    monkeypatch.setattr(route.entity, "_async_deliver_adb", deliver)
    monkeypatch.setattr(panel_update, "async_clear_update_failure", AsyncMock())
    key_path = Path(hass.config.path(".storage/panel_assistant.adb_key"))
    await hass.async_add_executor_job(
        lambda: key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    )
    assert not await hass.async_add_executor_job(key_path.exists)

    with patch.object(storage.Store, "_async_write_data", write_data):
        await route.entity._async_refresh_route()
    assert route.entity.latest_version == "0.9.7-rc4 build 772"
    await route.entity.async_install(None, False)

    assert await hass.async_add_executor_job(key_path.exists)
    assert (await adb_credentials.async_get_durable_adb_credential(hass)).generation_id
    assert route.probe_target.await_args_list[0].args == (route.pinned.pinned,)
    deliver.assert_awaited_once()
    route.client.async_start_panel_update.assert_not_awaited()
    route.client.async_stage_apk.assert_not_awaited()


async def test_existing_protected_panel_without_key_offers_nothing(
    route: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    route.get_credential.side_effect = AdbCredentialError()
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNAUTHORIZED
    )
    create_credential = AsyncMock()
    monkeypatch.setattr(
        panel_update, "async_get_adb_credential", create_credential, raising=False
    )

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    create_credential.assert_not_awaited()
    route.client.async_backup_panel.assert_not_awaited()
    route.adb_install.assert_not_awaited()


async def test_corrupt_adb_store_does_not_gain_an_update_route(
    route: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    route.get_credential.side_effect = AdbCredentialError()
    create_credential = AsyncMock(side_effect=AdbCredentialError())
    monkeypatch.setattr(panel_update, "async_get_adb_credential", create_credential)

    await route.entity._async_refresh_route()

    assert route.entity.latest_version == route.entity.installed_version
    route.preflight.assert_not_awaited()
    route.adb_install.assert_not_awaited()


@pytest.mark.parametrize(
    "state",
    [InstallTargetState.INCOMPATIBLE, InstallTargetState.ADB_UNAUTHORIZED],
)
async def test_adb_probe_refusal_withholds_offer_and_install(
    route: SimpleNamespace, state: InstallTargetState
) -> None:
    route.probe_target.return_value = InstallTargetProbe(
        state=state,
        serial="serial-a",
        model="model-a",
        primary_abi="arm64-v8a",
        android_sdk=30,
    )

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    route.client.async_backup_panel.assert_not_awaited()
    route.preflight.assert_not_awaited()
    route.adb_install.assert_not_awaited()


async def test_installed_app_preflight_refusal_withholds_offer_and_install(
    route: SimpleNamespace,
) -> None:
    route.preflight.return_value = SimpleNamespace(
        target_installed=False, root_mode="rootless"
    )

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    route.client.async_backup_panel.assert_not_awaited()
    route.adb_install.assert_not_awaited()


@pytest.mark.parametrize("change", ["credential", "target"])
async def test_changed_authority_after_backup_refuses_adb_mutation(
    route: SimpleNamespace, change: str
) -> None:
    route.adb_install.side_effect = AssertionError("unauthorized ADB mutation")
    if change == "credential":
        changed = SimpleNamespace(signer=object(), generation_id="credential-b")
        route.get_credential.side_effect = [route.credential, changed]
    else:
        other = normalize_address("192.168.1.11")
        route.pin.side_effect = [route.pinned, PinnedPanelTarget(other, other)]

    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_unavailable"
    route.client.async_backup_panel.assert_awaited_once()
    assert route.revalidate.await_count == 2
    route.adb_install.assert_not_awaited()
    route.launch.assert_not_awaited()
    assert route.entity.extra_state_attributes == {}


async def test_adb_package_manager_refusal_reports_panel_action(
    route: SimpleNamespace,
) -> None:
    route.adb_install.return_value = InstallOutcome.REFUSED

    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "update_rejected"
    assert "Install tab" in str(caught.value)
    route.client.async_backup_panel.assert_awaited_once()
    route.adb_install.assert_awaited_once()
    route.launch.assert_not_awaited()
    assert route.entity.extra_state_attributes == {}


async def test_post_install_launch_refusal_does_not_report_success(
    route: SimpleNamespace,
) -> None:
    route.launch.return_value = LaunchOutcome.REFUSED

    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)

    assert caught.value.translation_key == "update_not_complete"
    route.client.async_backup_panel.assert_awaited_once()
    route.adb_install.assert_awaited_once()
    route.launch.assert_awaited_once()
    assert route.entity.extra_state_attributes == {}

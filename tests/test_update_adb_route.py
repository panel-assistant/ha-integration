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
from adb_shell.exceptions import DeviceAuthError
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import storage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.panel_assistant import adb_credentials, provisioning
from custom_components.panel_assistant import update as panel_update
from custom_components.panel_assistant.adb_credentials import (
    AdbCredentialError,
    AdbCredentialMissingError,
)
from custom_components.panel_assistant.app_identity import (
    LAUNCH_COMPONENTS,
    LEGACY_PACKAGE_ID,
)
from custom_components.panel_assistant.build_feed import BuildFeed, FeedBuild
from custom_components.panel_assistant.client import PanelHealth, normalize_address
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.coordinator import (
    HaPaneldDataUpdateCoordinator,
    PanelSnapshot,
)
from custom_components.panel_assistant.failure_repair import (
    adb_authorization_issue_id,
    async_clear_adb_authorization,
    async_failure_events,
    panel_failure_issue_id,
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
        launch_component=LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0],
        published="2026-09-11T10:00:00Z",
        protocol_min=3,
        protocol_max=3,
    )
    feed = BuildFeedCoordinator(hass, URL("https://feed.example/maintainer.json"))
    feed.data = BuildFeed(channel="maintainer", builds=(build,))
    feed.last_update_success = True
    feed._verified_apks[build.apk_sha256] = apk
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
    assert route.entity.latest_version == route.entity.installed_version
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
    key_path = Path(hass.config.path(".storage/panel_assistant.adb_key"))
    await hass.async_add_executor_job(
        lambda: key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    )
    assert not await hass.async_add_executor_job(key_path.exists)

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        title="Kitchen display",
        data={CONF_ADDRESS: "192.168.1.10"},
    )
    entry.add_to_hass(hass)
    route.probe_target.return_value = InstallTargetProbe(
        state=InstallTargetState.ADB_UNREACHABLE
    )
    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    assert not await hass.async_add_executor_job(key_path.exists)
    route.probe_target.return_value = route.probe
    route.probe_target.reset_mock()
    with patch.object(storage.Store, "_async_write_data", write_data):
        route.entity._handle_coordinator_update()
        await hass.async_block_till_done()
    assert route.entity.latest_version == "0.9.7-rc4 build 772"
    await route.entity.async_install(None, False)

    assert await hass.async_add_executor_job(key_path.exists)
    assert (await adb_credentials.async_get_durable_adb_credential(hass)).generation_id
    assert route.probe_target.await_args_list[0].args == (route.pinned.pinned,)
    deliver.assert_awaited_once()
    route.client.async_start_panel_update.assert_not_awaited()
    route.client.async_stage_apk.assert_not_awaited()


@pytest.mark.parametrize("stored_key", [False, True])
async def test_existing_protected_panel_requires_explicit_authorization(
    route: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    hass: HomeAssistant,
    stored_key: bool,
) -> None:
    """Background refresh and install admission never prompt a protected panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        title="Kitchen display",
        data={CONF_ADDRESS: "192.168.1.10"},
    )
    entry.add_to_hass(hass)
    if not stored_key:
        route.get_credential.side_effect = AdbCredentialMissingError()
    prompts: list[object] = []

    async def reject_untrusted_key(**kwargs: object) -> bool:
        if not kwargs["rsa_keys"]:
            raise DeviceAuthError("No key available")
        # A protected peer rejected the signature, including a global HA key
        # trusted by another panel. adb-shell calls this before offering a key.
        if callback := kwargs.get("auth_callback"):
            callback(device)
        prompts.append(kwargs["rsa_keys"])
        raise DeviceAuthError("Waiting for physical approval")

    device = SimpleNamespace(connect=reject_untrusted_key, close=AsyncMock())
    monkeypatch.setattr(provisioning, "AdbDeviceAsync", lambda *_a, **_kw: device)
    monkeypatch.setattr(
        panel_update,
        "async_probe_install_target",
        provisioning.async_probe_install_target,
    )
    create_credential = AsyncMock()
    monkeypatch.setattr(
        panel_update, "async_get_adb_credential", create_credential, raising=False
    )

    await route.entity._async_refresh_route()
    assert route.entity.latest_version == route.entity.installed_version
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, adb_authorization_issue_id(entry.entry_id)
    )
    assert issue is not None
    assert issue.translation_placeholders == {"panel": "Kitchen display"}
    assert issue.data == {"entry_id": entry.entry_id}
    assert (
        "ADB authorization"
        in route.entity.extra_state_attributes["update_unavailable_reason"]
    )
    with pytest.raises(HomeAssistantError) as caught:
        await route.entity.async_install(None, False)
    assert caught.value.translation_key == "adb_authorization_required"
    assert "Kitchen display" in str(caught.value)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, adb_authorization_issue_id(entry.entry_id)
        )
        is None
    )
    failure = ir.async_get(hass).async_get_issue(
        DOMAIN, panel_failure_issue_id("update:entry-id")
    )
    assert failure is not None
    assert failure.translation_key == "installer_failure_adb_authorization"
    assert failure.data["entry_id"] == entry.entry_id
    events = await async_failure_events(hass, panel_failure_issue_id("update:entry-id"))
    assert events[-1]["reason"] == str(caught.value)
    assert events[-1]["target_version"] == "0.9.7-rc4 build 772"
    async_clear_adb_authorization(hass, entry.entry_id)
    await route.entity._async_refresh_route()
    repeated = ir.async_get(hass).async_get_issue(
        DOMAIN, panel_failure_issue_id("update:entry-id")
    )
    assert repeated is not None
    assert repeated.translation_key == "installer_failure_adb_authorization"
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, adb_authorization_issue_id(entry.entry_id)
        )
        is None
    )
    assert prompts == []
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
    create_credential.assert_not_awaited()
    route.probe_target.assert_not_awaited()
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

    assert caught.value.translation_key == (
        "adb_authorization_required"
        if state is InstallTargetState.ADB_UNAUTHORIZED
        else "update_unavailable"
    )
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


@pytest.mark.parametrize("revoke_at", ["flush", "push", "verification", "submitted"])
async def test_adb_transaction_uses_current_consent_until_command_submission(
    route, hass, monkeypatch, revoke_at
):
    """Preparation is reversible; a submitted PM request must finish and verify."""
    from adb_shell.auth.sign_pythonrsa import PythonRSASigner

    from custom_components.panel_assistant import install_adb, update_policy
    from custom_components.panel_assistant.const import CONF_PRERELEASE_PANEL_BUILDS

    from .test_install_adb import (
        JOB_ID,
        NONCES,
        REMOTE_PATH,
        FakeDevice,
        _cleanup_output,
        _identity_root_output,
        _install_update_fake,
        _installed_package_output,
        _preflight_output,
        _remote_output,
        _single_output,
    )

    monkeypatch.setattr(update_policy, "INTEGRATION_VERSION", "0.7.0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-id",
        data={},
        options={CONF_PRERELEASE_PANEL_BUILDS: True},
    )
    entry.add_to_hass(hass)

    def revoke():
        hass.config_entries.async_update_entry(
            entry, options={CONF_PRERELEASE_PANEL_BUILDS: False}
        )

    original_executor = hass.async_add_executor_job

    async def executor(target, *args):
        result = await original_executor(target, *args)
        if revoke_at == "flush" and getattr(target, "__name__", "") == "flush":
            revoke()
        return result

    monkeypatch.setattr(hass, "async_add_executor_job", executor)
    build = route.entity._feed.data.builds[0]
    identity = {"model": "model-a", "serial": "serial-a", "sdk": 30}
    installed = {"retained_lines": [f"package:{LEGACY_PACKAGE_ID}"]}
    remote = {"size": build.apk_size, "sha256": build.apk_sha256}
    outputs = [
        _preflight_output(NONCES[0], **identity, **installed),
        _installed_package_output(NONCES[1]),
        _single_output("PATH", NONCES[2], ["absent"], 0),
        _remote_output(NONCES[3], **remote),
        _preflight_output(NONCES[4], **identity, **installed),
        _installed_package_output(NONCES[5]),
        _remote_output(NONCES[6], **remote),
        _single_output("INSTALL", NONCES[7], ["Success"], 0)
        if revoke_at == "submitted"
        else _identity_root_output(NONCES[7], **identity),
        _cleanup_output(NONCES[8]),
    ]

    class PolicyChangingDevice(FakeDevice):
        async def push(self, *args, **kwargs):
            await super().push(*args, **kwargs)
            if revoke_at == "push":
                revoke()

        async def streaming_shell(self, command, **kwargs):
            async for body in super().streaming_shell(command, **kwargs):
                if (
                    revoke_at == "verification"
                    and f"HAPANELD_ARTIFACT_BEGIN:{NONCES[6]}" in command
                ) or (revoke_at == "submitted" and "pm install -r " in command):
                    revoke()
                yield body

    device = PolicyChangingDevice(outputs)
    _install_update_fake(monkeypatch, device)
    route.credential.signer = object.__new__(PythonRSASigner)
    monkeypatch.setattr(
        panel_update,
        "async_update_installed_apk",
        install_adb.async_update_installed_apk,
    )
    verified = AsyncMock()
    monkeypatch.setattr(route.entity, "_async_wait_for_build", verified)
    if revoke_at == "submitted":
        await route.entity.async_install(None, False)
        assert any(f"pm install -r {REMOTE_PATH}" in cmd for cmd in device.commands)
        route.launch.assert_awaited_once()
        verified.assert_awaited_once()
    else:
        with pytest.raises(HomeAssistantError) as caught:
            await route.entity.async_install(None, False)
        assert not any("pm install " in cmd for cmd in device.commands)
        assert caught.value.translation_key == "update_unavailable"
        assert device.pushes[0][0][1] == REMOTE_PATH
        route.launch.assert_not_awaited()
        verified.assert_not_awaited()
    assert entry.options[CONF_PRERELEASE_PANEL_BUILDS] is False
    assert (
        f"rm -f /data/local/tmp/ha-paneld-install-{JOB_ID}.apk" in device.commands[-1]
    )
    assert device.closed

"""A permission the owner turned off is kept by an update and shown as a Repair.

The update's permission repair runs the real grant program against a stand-in
panel shell, so the Repair follows what Android's readback actually says.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import update_panel_install
from custom_components.panel_assistant.adb_protocol import (
    AdbInstallTarget,
    AdbRootMode,
)
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.entry_issues import async_delete_entry_issues
from custom_components.panel_assistant.release import (
    InstallDescriptor,
    ReleaseArtifact,
)
from custom_components.panel_assistant.repairs_permission import (
    ISSUE_PERMISSIONS_TURNED_OFF,
    async_reconcile_permission_issue,
    permission_issue_id,
    turned_off_issue_id,
)

from .install_adb_fakes import _install_fakes
from .test_install_adb import (  # noqa: F401 - fixtures resolve by parameter name
    descriptor,
    signer,
    target,
)
from .test_install_adb_grants import (
    _OWNER_DENIAL,
    _calls,
    _deny,
    _RoutingFakeDevice,
    _seed_granted_panel,
)
from .test_permission_repairs import HELD, _status


async def _update_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    entry: MockConfigEntry,
    target: AdbInstallTarget,  # noqa: F811
    signer: PythonRSASigner,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    state: Path,
) -> None:
    """Run the permission repair an update starts, over the stand-in panel."""
    artifact = ReleaseArtifact(
        "v1.0.3", "1.0.3", "app.apk", "", "", descriptor=descriptor
    )
    coordinator = SimpleNamespace(
        _entry=lambda: entry,
        available=True,
        identity_mismatch=False,
        data=SimpleNamespace(
            health=SimpleNamespace(
                version=artifact.version, package=descriptor.package_id
            )
        ),
        client=SimpleNamespace(address=SimpleNamespace(host="192.168.1.30", port=8888)),
    )
    monkeypatch.setattr(
        update_panel_install,
        "async_get_durable_adb_credential",
        AsyncMock(return_value=SimpleNamespace(signer=signer)),
    )
    _install_fakes(monkeypatch, [_RoutingFakeDevice(state)])
    admit = AsyncMock(
        return_value=(target, SimpleNamespace(signer=signer), AdbRootMode.ROOTLESS)
    )
    await update_panel_install.async_repair_update_permissions(
        hass,
        coordinator,  # type: ignore[arg-type]
        artifact,
        admit_adb_target=admit,
        panel_name=lambda: "Test display",
    )
    admit.assert_awaited_once()


async def test_an_update_keeps_the_owners_choice_and_raises_one_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test display", data={CONF_ADDRESS: "192.168.1.30"}
    )
    entry.add_to_hass(hass)
    _seed_granted_panel(tmp_path, descriptor.package_id)
    _deny(tmp_path, descriptor.package_id, "RECORD_AUDIO", _OWNER_DENIAL)
    (tmp_path / f"appop.{descriptor.package_id}.SYSTEM_ALERT_WINDOW").write_text(
        "deny\n"
    )
    registry = ir.async_get(hass)
    issue_id = turned_off_issue_id(entry.entry_id)

    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)

    # Android still holds the owner's answers: nothing was granted over them.
    assert not any(
        call.startswith(("pm grant", "appops set")) for call in _calls(tmp_path)
    )
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == ISSUE_PERMISSIONS_TURNED_OFF
    assert issue.is_fixable is False
    assert issue.is_persistent is True
    assert issue.translation_placeholders is not None
    assert (
        issue.translation_placeholders["features"]
        == "display over other apps and microphone"
    )
    assert [key for key in registry.issues if key[0] == DOMAIN] == [(DOMAIN, issue_id)]

    # An unread readback proves nothing, so the Repair stays.
    (tmp_path / "unreadable.permissions").write_text("permission denied\n")
    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # The owner turns both back on; the next update's readback clears it.
    (tmp_path / "unreadable.permissions").unlink()
    (
        tmp_path / f"permission.{descriptor.package_id}.android.permission.RECORD_AUDIO"
    ).write_text("true\n")
    (tmp_path / f"appop.{descriptor.package_id}.SYSTEM_ALERT_WINDOW").write_text(
        "allow\n"
    )
    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_removing_the_panel_removes_its_repair(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test display", data={CONF_ADDRESS: "192.168.1.30"}
    )
    entry.add_to_hass(hass)
    _seed_granted_panel(tmp_path, descriptor.package_id)
    _deny(tmp_path, descriptor.package_id, "CAMERA", _OWNER_DENIAL)
    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)
    issue_id = turned_off_issue_id(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    await async_delete_entry_issues(hass, entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_permission_the_owner_turned_off_is_never_asked_back(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signer: PythonRSASigner,  # noqa: F811
    target: AdbInstallTarget,  # noqa: F811
    descriptor: InstallDescriptor,  # noqa: F811
    tmp_path: Path,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Test display", data={CONF_ADDRESS: "192.168.1.30"}
    )
    entry.add_to_hass(hass)
    _seed_granted_panel(tmp_path, descriptor.package_id)
    _deny(tmp_path, descriptor.package_id, "RECORD_AUDIO", _OWNER_DENIAL)
    (tmp_path / f"appop.{descriptor.package_id}.WRITE_SETTINGS").write_text("deny\n")
    registry = ir.async_get(hass)
    allow_them = permission_issue_id(entry.entry_id)

    # The panel's poll reports what the owner turned off as missing.
    owner_choices = _status(
        {**HELD, "write_settings": "missing", "microphone": "missing"}
    )
    async_reconcile_permission_issue(hass, entry, owner_choices)
    assert registry.async_get_issue(DOMAIN, allow_them) is not None

    # Once an update has read the owner's choices, the next poll stops asking.
    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)
    assert registry.async_get_issue(DOMAIN, turned_off_issue_id(entry.entry_id))
    async_reconcile_permission_issue(hass, entry, owner_choices)
    assert registry.async_get_issue(DOMAIN, allow_them) is None

    # An update whose readback misses one grant keeps the owner's earlier choice.
    (tmp_path / "unreadable.WRITE_SETTINGS").write_text("permission denied\n")
    await _update_repair(hass, monkeypatch, entry, target, signer, descriptor, tmp_path)
    async_reconcile_permission_issue(hass, entry, owner_choices)
    assert registry.async_get_issue(DOMAIN, allow_them) is None
    (tmp_path / "unreadable.WRITE_SETTINGS").unlink()

    # A grant missing for any other reason still asks.
    async_reconcile_permission_issue(
        hass,
        entry,
        _status({**HELD, "write_settings": "missing", "overlay": "missing"}),
    )
    assert registry.async_get_issue(DOMAIN, allow_them) is not None

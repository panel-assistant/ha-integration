"""App 0.9.10 moves its classes into the new app's own Kotlin package.

Panel Assistant 0.7.1 must find that release, plan, store and resume its
install, and start and grant it by the classes it actually carries, while an
install job that 0.7.0 wrote for a build with the old classes still resumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.core import HomeAssistant
from yarl import URL

from custom_components.panel_assistant import release, update_policy
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_adb import (
    AdbRootMode,
    LaunchOutcome,
    async_launch_installed_app,
)
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
)
from custom_components.panel_assistant.install_plan import build_install_plan

from .test_install_adb import (  # noqa: F401
    NONCES,
    FakeDevice,
    _identity_root_output,
    _install_fakes,
    _notifications_output,
    _single_output,
    signer,
    target,
)
from .test_install_executor import (  # noqa: F401
    Harness,
    create_job,
    emulate_home_assistant_store_file,
)
from .test_install_plan import CREDENTIAL_ID, pinned_target, probe
from .test_release import (  # noqa: F401
    _canonical_descriptor,
    _descriptor_document,
    _FakeResponse,
    _FakeSession,
    _install_test_key,
    _metadata_response,
    _signature,
    signing_key,
)

TAG = "v0.9.10"
SHA = "c" * 64
ROOT = f"https://github.com/panel-assistant/android/releases/download/{TAG}"
NEW_APP_APK = f"panel-assistant-{TAG}-manual-setup-required.apk.bin"
BRIDGE_APK = f"ha-paneld-{TAG}-manual-setup-required.apk.bin"
MOVED = "io.panelassistant.android/io.panelassistant.android.MainActivity"
OLD_CLASSES = "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
MOVED_SERVICE = (
    "io.panelassistant.android/"
    "io.panelassistant.android.input.PanelAccessibilityService"
)


def _release_0_9_10(signing_key: rsa.RSAPrivateKey) -> _FakeSession:  # noqa: F811
    """The release as app 0.9.10 publishes it: no asset ends `.apk`."""
    descriptor = _canonical_descriptor(
        _descriptor_document(
            releaseTag=TAG,
            versionName=TAG[1:],
            versionCode=1117,
            apkName=NEW_APP_APK,
            apkSha256=SHA,
            packageId="io.panelassistant.android",
            launchComponent=MOVED,
        )
    )
    protocol = (
        json.dumps(
            {
                "artifacts": [{"apkSha256": SHA, "protocolMax": 4, "protocolMin": 4}],
                "schema": "io.github.maxlyth.hapaneld.protocol.v1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    bodies: dict[str, bytes] = {}
    for apk in (BRIDGE_APK, NEW_APP_APK):
        checksum = f"{SHA}  {apk}\n".encode()
        bodies |= {
            apk: b"",
            f"{apk}.sha256": checksum,
            f"{apk}.sha256.sig": _signature(signing_key, checksum),
            f"{apk}.idsig": b"",
        }
    for name, body in (
        (f"ha-paneld-{TAG}-install.json", descriptor),
        (f"ha-paneld-{TAG}-protocol.json", protocol),
    ):
        bodies |= {name: body, f"{name}.sig": _signature(signing_key, body)}
    document = {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": name, "browser_download_url": f"{ROOT}/{name}"} for name in bodies
        ],
    }
    responses = {str(release._LATEST_RELEASE_URL): _metadata_response(document)}
    responses |= {
        f"{ROOT}/{name}": _FakeResponse(200, body, URL(f"{ROOT}/{name}"))
        for name, body in bodies.items()
    }
    return _FakeSession(responses)


async def _seed_installed(manager: InstallJobManager, artifact: Any) -> str:
    """Persist a job that installed the APK and stopped before launching it."""
    receipt = await create_job(manager, artifact)
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    for phase in (
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.STAGING,
        InstallPhase.INSTALLING,
        InstallPhase.INSTALLED,
    ):
        fields: dict[str, object] = {}
        if phase is InstallPhase.DOWNLOADING:
            fields["preflight_root_mode"] = AdbRootMode.ROOTLESS.value
        if phase is InstallPhase.ARTIFACT_READY:
            fields["actual_apk_bytes"] = artifact.apk_size
        receipt = await manager.async_transition(
            receipt.job_id, receipt.revision, phase, **fields
        )
    return receipt.job_id


async def test_a_0_9_10_release_installs_and_starts_by_its_moved_classes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,  # noqa: F811
    signer: Any,  # noqa: F811
    target: Any,  # noqa: F811
) -> None:
    """Release, plan, durable job, resumed worker and ADB, end to end."""
    _install_test_key(monkeypatch, signing_key)

    bundle, bridge = await release.async_resolve_stable_bundle_and_bridge(
        _release_0_9_10(signing_key)  # type: ignore[arg-type]
    )

    resolved = bundle.artifact
    assert resolved.apk_name == NEW_APP_APK
    assert resolved.apk_url == f"{ROOT}/{NEW_APP_APK}"
    assert resolved.descriptor is not None
    assert resolved.descriptor.launch_component == MOVED
    assert (resolved.protocol_min, resolved.protocol_max) == (4, 4)
    # A bridge whose classes moved is never offered to a panel on the old app.
    assert bridge is None
    assert update_policy.build_allowed(resolved.version, 4, 4, allow_prerelease=False)

    plan = build_install_plan(pinned_target(), probe(), resolved, CREDENTIAL_ID)
    job_id = await _seed_installed(InstallJobManager(hass), plan.artifact)
    harness = Harness(monkeypatch, expected_version="0.9.10")
    harness.health_packages = ["io.panelassistant.android"]
    restarted = InstallJobManager(hass)

    completed = await InstallExecutor(hass, restarted).async_wait(job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    [(_, _, launched, _)] = harness.launch_arguments
    assert launched.apk_name == NEW_APP_APK
    assert launched.launch_component == MOVED

    # The descriptor the worker launches with drives the panel's own commands.
    fake = FakeDevice(
        [
            _identity_root_output(NONCES[0]),
            _single_output(
                "PACKAGE",
                NONCES[1],
                ["package:/data/app/io.panelassistant.android/base.apk"],
                0,
            ),
            _notifications_output(NONCES[2]),
            _single_output(
                "PERMISSIONS",
                NONCES[3],
                [
                    "null",
                    MOVED_SERVICE,
                    "1",
                    "WRITE_SETTINGS: allow",
                    "SYSTEM_ALERT_WINDOW: allow\nunsupported\nunsupported",
                ],
                0,
            ),
            _single_output("LAUNCH", NONCES[4], ["Status: ok"], 0),
        ]
    )
    _install_fakes(monkeypatch, [fake])

    outcome = await async_launch_installed_app(
        target, signer, launched, expected_root_mode=AdbRootMode.ROOTLESS
    )

    assert outcome is LaunchOutcome.STARTED
    assert f"am start -W -n {MOVED} -p io.panelassistant.android" in fake.commands[-1]
    assert "settings put secure enabled_accessibility_services" in fake.commands[-2]
    assert MOVED_SERVICE in fake.commands[-2]
    # The permission repair identifies the obsolete service for cleanup.
    obsolete = (
        "io.panelassistant.android/"
        "io.github.maxlyth.hapaneld.input.PanelAccessibilityService"
    )
    assert obsolete in fake.commands[-2]
    assert OLD_CLASSES not in "".join(fake.commands)
    assert "io.github.maxlyth.hapaneld." not in fake.commands[-1]


async def test_a_job_written_by_0_7_0_resumes_with_the_classes_it_stored(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The store below was written by Panel Assistant 0.7.0 for app 0.9.9."""
    written = json.loads(
        (
            Path(__file__).parent / "fixtures" / "install_jobs_written_by_0_7_0.json"
        ).read_text(encoding="utf-8")
    )
    hass_storage[f"{DOMAIN}.install_jobs"] = written["jobs"]
    [stored] = written["jobs"]["data"]["jobs"]
    harness = Harness(monkeypatch, expected_version="0.9.9")
    harness.health_packages = ["io.panelassistant.android"]

    completed = await InstallExecutor(hass, InstallJobManager(hass)).async_wait(
        stored["job_id"]
    )

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    [(_, _, launched, _)] = harness.launch_arguments
    assert launched.package_id == "io.panelassistant.android"
    assert launched.launch_component == OLD_CLASSES

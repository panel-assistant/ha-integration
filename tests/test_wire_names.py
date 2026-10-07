"""App 0.9.11 signs its release documents under the new id's schema names.

Panel Assistant 0.9.0 must find such a release, plan it, store and resume its
install job, and keep doing the same for every earlier release, which carries
the old names. A name it does not know makes that one release ineligible.
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from homeassistant.core import HomeAssistant
from yarl import URL

from custom_components.panel_assistant import release
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallPhase,
    install_plan_sha256,
)
from custom_components.panel_assistant.install_plan import build_install_plan

from .http_fakes import FakeResponse, FakeSession
from .test_install_executor import (  # noqa: F401
    Harness,
    emulate_home_assistant_store_file,
)
from .test_install_plan import CREDENTIAL_ID, pinned_target, probe
from .test_moved_classes import _seed_installed
from .test_release import (  # noqa: F401
    _canonical_descriptor,
    _descriptor_document,
    _install_test_key,
    _metadata_response,
    _signature,
    signing_key,
)

TAG = "v0.9.11"
SHA = "d" * 64
ROOT = f"https://github.com/panel-assistant/android/releases/download/{TAG}"
APK = f"panel-assistant-{TAG}-manual-setup-required.apk.bin"
LAUNCH = "io.panelassistant.android/io.panelassistant.android.MainActivity"
NEW = ("io.panelassistant.android.install.v1", "io.panelassistant.android.protocol.v1")
OLD = (
    "io.github.maxlyth.hapaneld.install.v1",
    "io.github.maxlyth.hapaneld.protocol.v1",
)


def _release(
    signing_key: rsa.RSAPrivateKey,  # noqa: F811
    descriptor_schema: str,
    protocol_schema: str,
) -> FakeSession:
    """One signed release whose two documents carry the given schema names."""
    descriptor = _canonical_descriptor(
        _descriptor_document(
            schema=descriptor_schema,
            releaseTag=TAG,
            versionName=TAG[1:],
            versionCode=1180,
            apkName=APK,
            apkSha256=SHA,
            packageId="io.panelassistant.android",
            launchComponent=LAUNCH,
        )
    )
    protocol = (
        json.dumps(
            {
                "artifacts": [{"apkSha256": SHA, "protocolMax": 4, "protocolMin": 4}],
                "schema": protocol_schema,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    checksum = f"{SHA}  {APK}\n".encode()
    bodies = {
        APK: b"",
        f"{APK}.sha256": checksum,
        f"{APK}.sha256.sig": _signature(signing_key, checksum),
        f"{APK}.idsig": b"",
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
        f"{ROOT}/{name}": FakeResponse(200, body, URL(f"{ROOT}/{name}"))
        for name, body in bodies.items()
    }
    return FakeSession(responses)


@pytest.mark.parametrize(
    "schemas", [NEW, OLD], ids=["0.9.11 new names", "0.9.10 old names"]
)
async def test_a_release_under_either_name_installs_and_its_job_resumes(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,  # noqa: F811
    schemas: tuple[str, str],
) -> None:
    """Release, plan, durable job and a resumed worker, end to end."""
    _install_test_key(monkeypatch, signing_key)

    bundle, _bridge = await release.async_resolve_stable_bundle_and_bridge(
        _release(signing_key, *schemas)  # type: ignore[arg-type]
    )

    resolved = bundle.artifact
    assert resolved.apk_name == APK
    assert resolved.descriptor is not None
    assert resolved.descriptor.schema == schemas[0]
    assert (resolved.protocol_min, resolved.protocol_max) == (4, 4)

    plan = build_install_plan(pinned_target(), probe(), resolved, CREDENTIAL_ID)
    assert plan.artifact.descriptor_schema == schemas[0]
    # A new plan is bound under the new plan name whichever release it installs.
    assert plan.plan_sha256 == install_plan_sha256(
        plan.target,
        plan.artifact,
        CREDENTIAL_ID,
        "io.panelassistant.android.install-plan.v1",
    )
    job_id = await _seed_installed(InstallJobManager(hass), plan.artifact)
    harness = Harness(monkeypatch, expected_version="0.9.11")
    harness.health_packages = ["io.panelassistant.android"]

    completed = await InstallExecutor(hass, InstallJobManager(hass)).async_wait(job_id)

    assert completed.phase is InstallPhase.HEALTHY_UNCLAIMED
    [(_, _, launched, _)] = harness.launch_arguments
    assert launched.schema == schemas[0]
    assert launched.launch_component == LAUNCH


@pytest.mark.parametrize(
    "schemas",
    [
        ("io.panelassistant.android.install.v2", NEW[1]),
        (NEW[0], "io.panelassistant.android.protocol.v2"),
        ("io.example.install.v1", NEW[1]),
    ],
    ids=["descriptor v2", "protocol v2", "foreign descriptor"],
)
async def test_a_release_under_an_unknown_name_is_ineligible(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,  # noqa: F811
    schemas: tuple[str, str],
) -> None:
    """Exactly what 0.8.0 does with a 0.9.11 release: it is skipped, never half-read."""
    _install_test_key(monkeypatch, signing_key)

    with pytest.raises(release.ReleaseResolutionError):
        await release.async_resolve_stable_bundle_and_bridge(
            _release(signing_key, *schemas)  # type: ignore[arg-type]
        )

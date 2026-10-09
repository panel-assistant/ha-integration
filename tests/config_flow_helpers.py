"""Shared constants, fixtures and helpers for the config flow tests."""

from dataclasses import replace
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.panel_assistant.client import (
    PanelAddress,
    PanelHealth,
)
from custom_components.panel_assistant.const import (
    DOMAIN,
)
from custom_components.panel_assistant.install_network import (
    PinnedPanelTarget,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.release import (
    InstallDescriptor,
    ReleaseArtifact,
)

HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
    protocol=(5, 5),
)
BETA_HEALTH = replace(HEALTH, version="0.9.0", panel_id="beta", build="1001")
DISCOVERY_ID = "a" * 64
DISCOVERY_HEALTH = replace(HEALTH, discovery_id=DISCOVERY_ID)
DESCRIPTOR = InstallDescriptor(
    schema="io.github.maxlyth.hapaneld.install.v1",
    release_tag="v0.9.7",
    version_name="0.9.7",
    version_code=907,
    apk_name="ha-paneld-v0.9.7-manual-setup-required.apk",
    apk_size=1234,
    apk_sha256="a" * 64,
    package_id="io.github.maxlyth.hapaneld",
    signer_certificate_sha256=(
        "ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339"
    ),
    min_sdk=26,
    supported_abis=("arm64-v8a", "armeabi-v7a"),
    database_compatibility="hapaneld-db:v1:ha-paneld.db:1:1",
    launch_component="io.github.maxlyth.hapaneld/.MainActivity",
)
RELEASE = ReleaseArtifact(
    tag="v0.9.7",
    version="0.9.7",
    apk_name=DESCRIPTOR.apk_name,
    apk_url=f"https://example.invalid/{DESCRIPTOR.apk_name}",
    sha256="a" * 64,
    descriptor=DESCRIPTOR,
    protocol_min=5,
    protocol_max=5,
)
LEGACY_RELEASE = replace(RELEASE, descriptor=None)


@pytest.fixture(autouse=True)
def install_release_catalog():
    """Keep native setup discovery offline with published stable and RC choices."""
    with patch(
        "custom_components.panel_assistant.config_flow.async_list_install_choices",
        AsyncMock(
            return_value=[
                {"tag": "v0.9.7", "prerelease": False},
                {"tag": "v0.9.7-rc3", "prerelease": True},
                {"tag": "v0.9.7-rc4", "prerelease": True},
            ]
        ),
    ) as catalog:
        yield catalog


@pytest.fixture(autouse=True)
def install_network_pin() -> SimpleNamespace:
    """Keep config-flow tests off DNS while exposing the pinned target calls."""

    async def _pin(_hass: HomeAssistant, address: PanelAddress) -> PinnedPanelTarget:
        return PinnedPanelTarget(
            original=address,
            pinned=PanelAddress(host="192.168.1.23", port=address.port),
        )

    async def _revalidate(
        _hass: HomeAssistant, target: PinnedPanelTarget
    ) -> PinnedPanelTarget:
        return target

    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(side_effect=_pin),
        ) as pin_mock,
        patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(side_effect=_revalidate),
        ) as revalidate_mock,
    ):
        yield SimpleNamespace(pin=pin_mock, revalidate=revalidate_mock)


def _probe(state: str, **facts: str | int | None) -> InstallTargetProbe:
    """Return the provisioning probe shape consumed by the config flow."""
    return InstallTargetProbe(state=InstallTargetState(state), **facts)


async def _start_step(hass: HomeAssistant, step_id: str) -> dict:
    """Start the user flow and select one of its menu options."""
    menu = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        menu["flow_id"], {"next_step_id": step_id}
    )


async def _connect_found(hass: HomeAssistant, found: dict) -> dict:
    """Choose to connect the running panel offered by the found_panel menu."""
    assert found["type"] is FlowResultType.MENU
    assert found["step_id"] == "found_panel"
    return await hass.config_entries.flow.async_configure(
        found["flow_id"], {"next_step_id": "connect_found"}
    )


async def _choose_version(hass: HomeAssistant, form: dict, tag: str = "stable") -> dict:
    """Pick a release on the choose_version form reached after a clean probe."""
    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "choose_version"
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], {"release_candidate": tag}
    )


def _zeroconf_info(
    *,
    discovery_id: object = DISCOVERY_ID,
    port: int | None = 8888,
    friendly_name: object = None,
    host: str = "192.168.1.23",
) -> ZeroconfServiceInfo:
    """Return a local advertisement with only the stable test contract."""
    address = ip_address(host)
    properties: dict[str, object] = {"did": discovery_id}
    if friendly_name is not None:
        properties["name"] = friendly_name
    return ZeroconfServiceInfo(
        ip_address=address,
        ip_addresses=[address],
        port=port,
        hostname="alpha.local.",
        type="_ha-paneld._tcp.local.",
        name="alpha._ha-paneld._tcp.local.",
        properties=properties,
    )

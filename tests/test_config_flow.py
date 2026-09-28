"""Tests for the ha-paneld config flow."""

import asyncio
import contextlib
from collections.abc import Iterator
from dataclasses import replace
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import FlowType
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.adb_credentials import (
    AdbCredential,
    AdbCredentialError,
)
from custom_components.panel_assistant.app_identity import (
    LEGACY_PACKAGE_ID,
    SUCCESSOR_PACKAGE_ID,
)
from custom_components.panel_assistant.browser_delivery import (
    DATA_BROWSER_DELIVERY,
    async_register_browser_delivery,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidAddressError,
    InvalidResponseError,
    PanelAddress,
    PanelHealth,
    PanelSetupState,
    normalize_address,
)
from custom_components.panel_assistant.config_flow import (
    HaPaneldConfigFlow,
    _install_candidate_placeholders,
)
from custom_components.panel_assistant.const import (
    CONF_TRANSPORT_USER_ID,
    DOMAIN,
    help_url,
)
from custom_components.panel_assistant.install_adb import (
    AdbRootMode,
    InstallAdbError,
    InstallAdbErrorCode,
)
from custom_components.panel_assistant.install_executor import InstallExecutor
from custom_components.panel_assistant.install_jobs import (
    InstallArtifact,
    InstallJobReceipt,
    InstallJobStoreError,
    InstallPhase,
    InstallResultCode,
    InstallTarget,
)
from custom_components.panel_assistant.install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
    PinnedPanelTarget,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
    InstallTargetState,
)
from custom_components.panel_assistant.release import (
    InstallDescriptor,
    ReleaseArtifact,
    ReleaseResolutionError,
)
from custom_components.panel_assistant.transport import (
    async_binding_request,
    async_record_binding_request,
)

HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="alpha",
    build="1000",
    config_hash="1a2b3c4d",
)
BETA_HEALTH = PanelHealth(
    version="0.9.0",
    panel_id="beta",
    build="1001",
    config_hash="1a2b3c4d",
)
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
)
LEGACY_RELEASE = replace(RELEASE, descriptor=None)


@pytest.fixture(autouse=True)
def install_release_catalog():
    """Keep native setup discovery offline with published stable and RC choices."""
    with patch(
        "custom_components.panel_assistant.release_catalog.async_list_install_releases",
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


async def test_user_starts_with_install_first_menu(hass: HomeAssistant) -> None:
    """Offer one address-first path for network panels, then browser USB."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["add_panel", "install_usb"]


async def test_first_user_flow_registers_browser_delivery_before_any_panel(
    hass: HomeAssistant, hass_client
) -> None:
    """First-panel delivery cannot depend on successful entry creation."""
    assert not hass.config_entries.async_entries(DOMAIN)
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert DATA_BROWSER_DELIVERY in hass.data.get(DOMAIN, {})
    service = hass.data[DOMAIN][DATA_BROWSER_DELIVERY]
    async_register_browser_delivery(hass)
    assert hass.data[DOMAIN][DATA_BROWSER_DELIVERY] is service
    client = await hass_client()
    response = await client.get(f"/api/panel_assistant/usb/release/{'a' * 32}/apk")
    assert response.status == 404
    assert await response.json() == {"error": "browser_release_not_found"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_usb_step_links_local_admin_panel_without_creating_entry(
    hass: HomeAssistant,
) -> None:
    """The installer opens through HA so credentials stay at the HA origin."""
    from homeassistant.components import frontend

    result = await _start_step(hass, "install_usb")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "install_usb"
    assert result["description_placeholders"] == {
        "usb_install_url": "/panel-assistant-usb"
    }
    panel = hass.data[frontend.DATA_PANELS]["panel-assistant-usb"]
    assert panel.require_admin is True
    assert panel.config["installer_url"] == "https://install.panel-assistant.io/"
    assert not hass.config_entries.async_entries(DOMAIN)
    returned = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert returned["type"] is FlowResultType.MENU
    assert returned["step_id"] == "user"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_connect_existing_starts_with_address_form(
    hass: HomeAssistant,
) -> None:
    """The secondary path retains the existing manual address form."""
    result = await _start_step(hass, "add_panel")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"


async def test_connect_existing_success(hass: HomeAssistant) -> None:
    """Existing attach keeps its normalized endpoint identity and display title."""
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        AsyncMock(return_value=HEALTH),
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: " PANEL.local "}
        )
        result = await _connect_found(hass, found)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "alpha"
    assert result["data"] == {CONF_ADDRESS: "panel.local"}
    assert result["options"] == {"authority": "native"}
    assert result["result"].unique_id is None


async def test_connect_existing_duplicate_address_is_rejected(
    hass: HomeAssistant,
) -> None:
    """The address remains identity when the mutable panel name changes."""
    health_mock = AsyncMock(side_effect=[HEALTH, HEALTH, BETA_HEALTH])
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        first_form = await _start_step(hass, "add_panel")
        first = await _connect_found(
            hass,
            await hass.config_entries.flow.async_configure(
                first_form["flow_id"], {CONF_ADDRESS: "PANEL.local"}
            ),
        )
        second_form = await _start_step(hass, "add_panel")
        second = await hass.config_entries.flow.async_configure(
            second_form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"
    assert first["result"].data[CONF_ADDRESS] == "panel.local"
    # Creating the first entry schedules its initial coordinator refresh between
    # the two config-flow validations.
    assert health_mock.await_count == 3


async def test_connect_existing_expected_errors(hass: HomeAssistant) -> None:
    """Address and health failures remain actionable connect-form errors."""
    invalid_form = await _start_step(hass, "add_panel")
    with patch(
        "custom_components.panel_assistant.config_flow.normalize_address",
        side_effect=InvalidAddressError,
    ):
        invalid = await hass.config_entries.flow.async_configure(
            invalid_form["flow_id"], {CONF_ADDRESS: "bad"}
        )

    # Only a non-standard port is connect-only; on 8888 a health failure is
    # followed by the read-only ADB probe instead of a connect error.
    unavailable_form = await _start_step(hass, "add_panel")
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(),
        ) as probe_mock,
    ):
        unavailable = await hass.config_entries.flow.async_configure(
            unavailable_form["flow_id"], {CONF_ADDRESS: "panel.local:8123"}
        )

    assert invalid["step_id"] == "add_panel"
    assert invalid["errors"] == {"base": "invalid_address"}
    assert unavailable["step_id"] == "add_panel"
    assert unavailable["errors"] == {"base": "cannot_connect"}
    probe_mock.assert_not_awaited()


async def test_connect_existing_unexpected_error(hass: HomeAssistant) -> None:
    """Unexpected attach failures do not escape the flow."""
    form = await _start_step(hass, "add_panel")
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["errors"] == {"base": "unknown"}


async def test_zeroconf_requires_fresh_health_confirmation_before_entry_creation(
    hass: HomeAssistant,
) -> None:
    """A valid announcement creates no entry until its health identity is rechecked."""
    health_mock = AsyncMock(return_value=DISCOVERY_HEALTH)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        assert form["type"] is FlowResultType.FORM
        assert form["step_id"] == "confirm_discovery"
        assert form["description_placeholders"] == {
            "address": "192.168.1.23",
            "panel_name": "alpha",
        }
        assert not hass.config_entries.async_entries(DOMAIN)

        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "alpha"
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}
    assert result["options"] == {"authority": "native"}
    assert result["result"].unique_id == DISCOVERY_ID
    assert health_mock.await_count >= 2


async def test_zeroconf_offers_and_stores_an_ipv6_only_panel(
    hass: HomeAssistant,
) -> None:
    """A panel that advertises only an IPv6 address is offered and stored bracketed."""
    health_mock = AsyncMock(return_value=DISCOVERY_HEALTH)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(host="fd00:5041::10"),
        )
        assert form["type"] is FlowResultType.FORM
        assert form["step_id"] == "confirm_discovery"
        assert form["description_placeholders"]["address"] == "[fd00:5041::10]"

        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "[fd00:5041::10]"}
    stored = normalize_address(result["data"][CONF_ADDRESS])
    assert stored.base_url.host == "fd00:5041::10"


async def _start_discovery(hass, info, panel_id):
    """Drive one zeroconf step to its confirmation form for a named panel."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(
                return_value=replace(
                    DISCOVERY_HEALTH,
                    panel_id=panel_id,
                    discovery_id=info.properties["did"],
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
        )


def _title_of(hass, flow_id):
    for flow in hass.config_entries.flow.async_progress():
        if flow["flow_id"] == flow_id:
            return flow["context"]["title_placeholders"]["name"]
    raise AssertionError("flow is no longer in progress")


async def test_zeroconf_prefers_the_advertised_friendly_name(
    hass: HomeAssistant,
) -> None:
    """The card should read as the human named the panel, not as its identifier."""
    info = _zeroconf_info(friendly_name="Alpha panel")
    form = await _start_discovery(hass, info, "alpha_panel")

    assert _title_of(hass, form["flow_id"]) == "Alpha panel"
    assert form["description_placeholders"]["panel_name"] == "Alpha panel"


@pytest.mark.parametrize(
    "advertised",
    ["", "   ", "a" * 65, "two\nlines", "bell\x07", 17, b"bytes"],
)
async def test_zeroconf_refuses_unusable_advertised_names(
    hass: HomeAssistant, advertised: object
) -> None:
    """TXT records are LAN-writable, so only bounded printable text may be shown."""
    info = _zeroconf_info(friendly_name=advertised)
    form = await _start_discovery(hass, info, "alpha")

    assert _title_of(hass, form["flow_id"]) == "alpha"


async def test_zeroconf_qualifies_both_sides_of_a_duplicate_friendly_name(
    hass: HomeAssistant,
) -> None:
    """Two panels sharing a name are only distinguishable once both carry their id."""
    first = await _start_discovery(
        hass, _zeroconf_info(friendly_name="Spare panel"), "spare_one"
    )
    assert _title_of(hass, first["flow_id"]) == "Spare panel"

    second = await _start_discovery(
        hass,
        _zeroconf_info(
            discovery_id="b" * 64, friendly_name="Spare panel", host="192.168.1.24"
        ),
        "spare_two",
    )

    assert _title_of(hass, second["flow_id"]) == "Spare panel (spare_two)"
    assert _title_of(hass, first["flow_id"]) == "Spare panel (spare_one)"


async def test_zeroconf_qualifies_a_name_an_existing_entry_already_uses(
    hass: HomeAssistant,
) -> None:
    """An already configured panel owns its name just as a pending discovery does."""
    MockConfigEntry(
        domain=DOMAIN, title="Beta panel", data={CONF_ADDRESS: "192.168.1.99"}
    ).add_to_hass(hass)

    form = await _start_discovery(
        hass, _zeroconf_info(friendly_name="Beta panel"), "beta_panel"
    )

    assert _title_of(hass, form["flow_id"]) == "Beta panel (beta_panel)"


async def test_confirmed_discovery_entry_keeps_the_name_its_card_promised(
    hass: HomeAssistant,
) -> None:
    """A card offering one name and an entry landing under another is a mismatch."""
    info = _zeroconf_info(friendly_name="Gamma panel")
    form = await _start_discovery(hass, info, "gamma_panel")

    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=replace(DISCOVERY_HEALTH, panel_id="gamma_panel")),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gamma panel"


async def test_zeroconf_titles_each_discovery_with_its_own_panel(
    hass: HomeAssistant,
) -> None:
    """Without per-flow placeholders every discovered panel renders the same card."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )

    flow = hass.config_entries.flow.async_progress()[0]
    assert flow["context"]["title_placeholders"] == {"name": DISCOVERY_HEALTH.panel_id}


@pytest.mark.parametrize(
    ("discovery_id", "port"),
    [
        ("A" * 64, 8888),
        ("a" * 63, 8888),
        (b"a" * 64, 8888),
        ("a" * 64, 9999),
        ("a" * 64, None),
    ],
)
async def test_zeroconf_rejects_malformed_or_wrong_port_before_health_contact(
    hass: HomeAssistant, discovery_id: object, port: int | None
) -> None:
    """Untrusted mDNS metadata cannot invoke the panel health endpoint."""
    health_mock = AsyncMock()
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(discovery_id=discovery_id, port=port),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    health_mock.assert_not_awaited()


async def test_zeroconf_rejects_health_identity_mismatch_without_entry(
    hass: HomeAssistant,
) -> None:
    """The mDNS token alone can never establish panel identity."""
    health_mock = AsyncMock(
        return_value=replace(DISCOVERY_HEALTH, discovery_id="b" * 64)
    )
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert not hass.config_entries.async_entries(DOMAIN)
    health_mock.assert_awaited_once()


async def test_zeroconf_rechecks_identity_after_confirmation(
    hass: HomeAssistant,
) -> None:
    """A panel changing identity while the user reads the form is not added."""
    health_mock = AsyncMock(
        side_effect=[DISCOVERY_HEALTH, replace(DISCOVERY_HEALTH, discovery_id="b" * 64)]
    )
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery"
    assert not hass.config_entries.async_entries(DOMAIN)
    assert health_mock.await_count == 2


async def test_zeroconf_keeps_existing_entry_identity_without_contacting_panel(
    hass: HomeAssistant,
) -> None:
    """Discovery never rewrites an existing config entry or endpoint."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DISCOVERY_ID,
        data={CONF_ADDRESS: "192.168.1.23"},
    )
    entry.add_to_hass(hass)
    health_mock = AsyncMock()
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.unique_id == DISCOVERY_ID
    assert entry.data == {CONF_ADDRESS: "192.168.1.23"}
    health_mock.assert_not_awaited()


async def test_install_rejects_invalid_address_before_network_calls(
    hass: HomeAssistant,
) -> None:
    """An invalid target returns to the install form without probing it."""
    health_mock = AsyncMock()
    probe_mock = AsyncMock()
    form = await _start_step(hass, "add_panel")
    with (
        patch(
            "custom_components.panel_assistant.config_flow.normalize_address",
            side_effect=InvalidAddressError,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "bad"}
        )

    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "invalid_address"}
    assert result["description_placeholders"] == {
        "panel_access_url": help_url("panel-access"),
        "panel_help_url": help_url("panel-unreachable"),
    }
    health_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()


@pytest.mark.parametrize(
    ("code", "expected_error"),
    [
        (InstallNetworkErrorCode.INVALID_HOST, "invalid_install_address"),
        (InstallNetworkErrorCode.RESOLUTION_FAILED, "install_resolution_failed"),
        (InstallNetworkErrorCode.RESOLUTION_TIMEOUT, "install_resolution_timeout"),
        (InstallNetworkErrorCode.TOO_MANY_RESULTS, "install_too_many_addresses"),
        (InstallNetworkErrorCode.UNSAFE_TARGET, "unsafe_install_target"),
        (InstallNetworkErrorCode.PINNED_TARGET_REMOVED, "install_target_changed"),
    ],
)
async def test_install_network_refusal_precedes_all_panel_contact(
    hass: HomeAssistant,
    code: InstallNetworkErrorCode,
    expected_error: str,
) -> None:
    """Unsafe or unresolved targets cannot reach HTTP or ADB from the install flow."""
    health_mock = AsyncMock()
    probe_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_pin_install_target",
            AsyncMock(side_effect=InstallNetworkError(code)),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": expected_error}
    health_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()


async def test_install_uses_pinned_address_for_health_and_adb(
    hass: HomeAssistant, install_network_pin: SimpleNamespace
) -> None:
    """The display identity stays original while both protocols use the LAN pin."""
    health_mock = AsyncMock(side_effect=CannotConnectError)
    probe_mock = AsyncMock(return_value=_probe("adb_unreachable"))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient"
        ) as client_class,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        client_class.return_value.async_get_health = health_mock
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "Panel.local"}
        )

    assert result["errors"] == {"base": "adb_unreachable"}
    install_network_pin.pin.assert_awaited_once()
    assert install_network_pin.pin.await_args.args[1].stored_value == "panel.local"
    assert client_class.call_args.args[1].stored_value == "192.168.1.23"
    assert probe_mock.await_args.args[0].stored_value == "192.168.1.23"
    assert health_mock.await_count == 1


async def test_install_existing_panel_requires_confirmation(
    hass: HomeAssistant,
) -> None:
    """A healthy installation is connected only after an explicit confirmation."""
    probe_mock = AsyncMock()
    health_mock = AsyncMock(return_value=HEALTH)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        confirm = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: " PANEL.local "}
        )

        assert confirm["type"] is FlowResultType.MENU
        assert confirm["step_id"] == "found_panel"
        assert confirm["menu_options"] == ["connect_found", "add_panel"]
        assert confirm["description_placeholders"] == {
            "address": "panel.local",
            "version": "0.9.0",
        }
        assert not hass.config_entries.async_entries(DOMAIN)
        probe_mock.assert_not_awaited()

        result = await _connect_found(hass, confirm)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "alpha"
    assert result["data"] == {CONF_ADDRESS: "panel.local"}
    assert result["result"].unique_id is None
    # Entry setup performs the third health refresh after the two flow checks.
    assert health_mock.await_count == 3


async def test_install_existing_panel_rechecks_health_before_create(
    hass: HomeAssistant,
) -> None:
    """Confirmation refuses an installation that stopped answering meanwhile."""
    health_mock = AsyncMock(side_effect=[HEALTH, CannotConnectError])
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await _start_step(hass, "add_panel")
        confirm = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        result = await _connect_found(hass, confirm)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "cannot_connect"}
    assert health_mock.await_count == 2
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_existing_panel_revalidates_pin_before_confirmation(
    hass: HomeAssistant,
) -> None:
    """A healthy endpoint cannot be connected through a changed DNS target."""
    health_mock = AsyncMock(return_value=HEALTH)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await _start_step(hass, "add_panel")
        confirm = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        with patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(
                side_effect=InstallNetworkError(
                    InstallNetworkErrorCode.PINNED_TARGET_REMOVED
                )
            ),
        ):
            result = await _connect_found(hass, confirm)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "install_target_changed"}
    assert health_mock.await_count == 1
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_existing_panel_handles_unexpected_confirmation_failure(
    hass: HomeAssistant,
) -> None:
    """An unexpected revalidation failure creates no entry."""
    health_mock = AsyncMock(side_effect=[HEALTH, RuntimeError("unexpected")])
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await _start_step(hass, "add_panel")
        confirm = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        result = await _connect_found(hass, confirm)

    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "unknown"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_found_panel_back_option_returns_to_prefilled_address_form(
    hass: HomeAssistant,
) -> None:
    """Going back from a found panel keeps the typed address and connects nothing."""
    health_mock = AsyncMock(return_value=HEALTH)
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: " PANEL.local "}
        )
        assert found["step_id"] == "found_panel"
        back = await hass.config_entries.flow.async_configure(
            found["flow_id"], {"next_step_id": "add_panel"}
        )

    assert back["type"] is FlowResultType.FORM
    assert back["step_id"] == "add_panel"
    assert back["errors"] == {}
    [address_key] = back["data_schema"].schema
    assert address_key == CONF_ADDRESS
    assert address_key.description == {"suggested_value": "panel.local"}
    assert health_mock.await_count == 1
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_connect_found_failure_returns_to_address_form_and_can_retry(
    hass: HomeAssistant,
) -> None:
    """A failed connect lands on the prefilled address form, never a dead end."""
    # Found, failed connect, found again, connect, then the new entry's refresh.
    health_mock = AsyncMock(
        side_effect=[HEALTH, CannotConnectError, HEALTH, HEALTH, HEALTH]
    )
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        form = await _start_step(hass, "add_panel")
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        failed = await _connect_found(hass, found)

        assert failed["type"] is FlowResultType.FORM
        assert failed["step_id"] == "add_panel"
        assert failed["errors"] == {"base": "cannot_connect"}
        [address_key] = failed["data_schema"].schema
        assert address_key.description == {"suggested_value": "panel.local"}
        assert not hass.config_entries.async_entries(DOMAIN)

        again = await hass.config_entries.flow.async_configure(
            failed["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        result = await _connect_found(hass, again)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "panel.local"}


@pytest.mark.parametrize("healthy", [True, False], ids=["healthy", "unreachable"])
async def test_install_rejects_an_http_port_as_an_adb_port(
    hass: HomeAssistant, install_network_pin: SimpleNamespace, healthy: bool
) -> None:
    """A non-standard port is connect-only: health alone, never pin, job or ADB."""
    health_mock = (
        AsyncMock(return_value=HEALTH)
        if healthy
        else AsyncMock(side_effect=CannotConnectError)
    )
    probe_mock = AsyncMock()
    manager_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient"
        ) as client_class,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            manager_mock,
        ),
    ):
        client_class.return_value.async_get_health = health_mock
        client_class.return_value.async_get_setup_complete = AsyncMock(
            return_value=True
        )
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local:5555"}
        )

    if healthy:
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "found_panel"
        assert result["description_placeholders"] == {
            "address": "panel.local:5555",
            "version": "0.9.0",
        }
    else:
        assert result["step_id"] == "add_panel"
        assert result["errors"] == {"base": "cannot_connect"}
    health_mock.assert_awaited_once()
    assert client_class.call_args.args[1].stored_value == "panel.local:5555"
    install_network_pin.pin.assert_not_awaited()
    manager_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()


async def test_install_existing_duplicate_address_is_rejected(
    hass: HomeAssistant,
) -> None:
    """The install path shares the exact existing endpoint identity."""
    health_mock = AsyncMock(side_effect=[HEALTH, HEALTH, BETA_HEALTH])
    with patch(
        "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
        health_mock,
    ):
        connect = await _start_step(hass, "add_panel")
        first = await _connect_found(
            hass,
            await hass.config_entries.flow.async_configure(
                connect["flow_id"], {CONF_ADDRESS: "PANEL.local"}
            ),
        )
        install = await _start_step(hass, "add_panel")
        duplicate = await hass.config_entries.flow.async_configure(
            install["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert duplicate["type"] is FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"


async def test_install_unavailable_duplicate_address_is_rejected_before_probe(
    hass: HomeAssistant,
) -> None:
    """An unavailable configured endpoint retains its config-entry identity."""
    MockConfigEntry(
        domain=DOMAIN,
        title="Configured panel",
        data={CONF_ADDRESS: "panel.local"},
    ).add_to_hass(hass)
    health_mock = AsyncMock(side_effect=CannotConnectError)
    probe_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        install = await _start_step(hass, "add_panel")
        duplicate = await hass.config_entries.flow.async_configure(
            install["flow_id"], {CONF_ADDRESS: "PANEL.local"}
        )

    assert duplicate["type"] is FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"
    health_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()


@pytest.mark.parametrize("health_error", [CannotConnectError, InvalidResponseError])
@pytest.mark.parametrize("choice", [{}, {"release_candidate": "stable"}])
async def test_install_candidate_readiness_is_non_mutating_until_confirmation(
    hass: HomeAssistant, health_error: type[Exception], choice: dict[str, str]
) -> None:
    """Both absent and invalid health fall through to a clean ADB classification."""
    probe_mock = AsyncMock(
        return_value=_probe(
            "install_candidate",
            model="WF1589T",
            serial="serial-123",
            primary_abi="arm64-v8a",
            android_sdk=31,
        )
    )
    release_mock = AsyncMock(return_value=RELEASE)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=health_error),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "Panel.local"}
        )
        assert choose["step_id"] == "choose_version"
        release_mock.assert_not_awaited()
        # An omitted choice takes the schema default, the newest stable release.
        confirm = await hass.config_entries.flow.async_configure(
            choose["flow_id"], choice
        )

        assert confirm["type"] is FlowResultType.FORM
        assert confirm["step_id"] == "confirm_install_candidate"
        assert confirm["description_placeholders"] == {
            "address": "panel.local",
            "model": "WF1589T",
            "serial": "serial-123",
            "abi": "arm64-v8a",
            "sdk": "31",
            "version": "0.9.7",
            "tag": "v0.9.7",
            "sha256": "a" * 64,
        }
        assert not hass.config_entries.async_entries(DOMAIN)
        probe_mock.assert_awaited_once()
        assert len(probe_mock.await_args.args) == 1
        assert probe_mock.await_args.args[0].stored_value == "192.168.1.23"
        release_mock.assert_awaited_once()

        refreshed = await hass.config_entries.flow.async_configure(confirm["flow_id"])
        assert (
            refreshed["description_placeholders"] == confirm["description_placeholders"]
        )

        assert refreshed["type"] is FlowResultType.FORM

    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_classification_error_can_retry_to_candidate(
    hass: HomeAssistant,
) -> None:
    """The same address form can recover from a transient classification failure."""
    probe_mock = AsyncMock(
        side_effect=[
            _probe("adb_unreachable"),
            _probe(
                "install_candidate",
                model="WF1589T",
                serial="serial-123",
                primary_abi="arm64-v8a",
                android_sdk=31,
            ),
        ]
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        refused = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        recovered = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )

    assert refused["errors"] == {"base": "adb_unreachable"}
    assert recovered["type"] is FlowResultType.FORM
    assert recovered["step_id"] == "confirm_install_candidate"
    assert probe_mock.await_count == 2


async def test_install_candidate_without_identity_fails_closed(
    hass: HomeAssistant,
) -> None:
    """A backend cannot promote package absence without complete target facts."""
    release_mock = AsyncMock()
    incomplete = _probe(
        "install_candidate",
        model=None,
        serial=None,
        primary_abi=None,
        android_sdk=None,
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=incomplete),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["errors"] == {"base": "retained_or_ambiguous"}
    release_mock.assert_not_awaited()
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("state", "expected_error"),
    [
        ("adb_unreachable", "adb_unreachable"),
        ("retained_or_ambiguous", "retained_or_ambiguous"),
        ("incompatible", "incompatible"),
    ],
)
async def test_install_classification_refusals_return_to_address_form(
    hass: HomeAssistant, state: str, expected_error: str
) -> None:
    """Every unsafe target state is specific, actionable, and retryable."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=_probe(state)),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": expected_error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_unauthorized_requires_physical_approval_without_creating_key(
    hass: HomeAssistant,
) -> None:
    """The read-only first probe cannot create or offer Home Assistant's ADB key."""
    signer_mock = AsyncMock()
    probe_mock = AsyncMock(return_value=_probe("adb_unauthorized"))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "Panel.local"}
        )
        assert choose["step_id"] == "choose_version"
        assert choose["description_placeholders"] == {"address": "panel.local"}
        signer_mock.assert_not_awaited()
        result = await _choose_version(hass, choose)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_adb"
    assert result["description_placeholders"] == {"address": "panel.local"}
    assert result["errors"] is None
    signer_mock.assert_not_awaited()
    probe_mock.assert_awaited_once()
    assert len(probe_mock.await_args.args) == 1
    assert probe_mock.await_args.args[0].stored_value == "192.168.1.23"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_authorization_retry_uses_persistent_signer(
    hass: HomeAssistant,
) -> None:
    """A retry offers the shared signer and remains actionable until approved."""
    signer = object()
    signer_mock = AsyncMock(return_value=signer)
    probe_mock = AsyncMock(
        side_effect=[_probe("adb_unauthorized"), _probe("adb_unauthorized")]
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        authorize = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_adb"
    assert result["errors"] == {"base": "adb_still_unauthorized"}
    signer_mock.assert_awaited_once_with(hass)
    assert len(probe_mock.await_args_list[0].args) == 1
    assert probe_mock.await_args_list[0].args[0].stored_value == "192.168.1.23"
    assert probe_mock.await_args_list[1].args[0].stored_value == "192.168.1.23"
    assert probe_mock.await_args_list[1].args[1] is signer
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_authorization_revalidates_pin_before_loading_key(
    hass: HomeAssistant,
) -> None:
    """DNS drift blocks the authorization attempt before a key can be offered."""
    signer_mock = AsyncMock()
    probe_mock = AsyncMock(return_value=_probe("adb_unauthorized"))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        authorize = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        with patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(
                side_effect=InstallNetworkError(
                    InstallNetworkErrorCode.PINNED_TARGET_REMOVED
                )
            ),
        ) as revalidate_mock:
            result = await hass.config_entries.flow.async_configure(
                authorize["flow_id"], {}
            )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_adb"
    assert result["errors"] == {"base": "install_target_changed"}
    revalidate_mock.assert_awaited_once()
    signer_mock.assert_not_awaited()
    assert probe_mock.await_count == 1
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("model", "display"),
    [
        ("WF1589T", "WF1589T"),
        (
            "![Panel](https://example.invalid) <img> &amp; `x` \\ *_{}",
            "&#33;&#91;Panel&#93;&#40;https&#58;&#47;&#47;example&#46;invalid&#41; "
            "&#60;img&#62; &#38;amp; &#96;x&#96; &#92; &#42;&#95;&#123;&#125;",
        ),
    ],
)
def test_install_confirmation_device_facts_are_literal(
    model: str, display: str
) -> None:
    """Escape only presentation, including entities and Markdown metacharacters."""
    probe = _probe(
        "install_candidate",
        model=model,
        serial="serial_123",
        primary_abi="arm64-v8a",
        android_sdk=31,
    )
    assert _install_candidate_placeholders(probe) == {
        "model": display,
        "serial": "serial&#95;123",
        "abi": "arm64-v8a",
        "sdk": "31",
    }
    assert probe.model == model and probe.serial == "serial_123"


async def test_install_authorization_approval_reaches_release_preview(
    hass: HomeAssistant,
) -> None:
    """Physical approval reclassifies with the signer before resolving a release."""
    signer = object()
    signer_mock = AsyncMock(return_value=signer)
    candidate = _probe(
        "install_candidate",
        model="WF1589T",
        serial="serial-123",
        primary_abi="arm64-v8a",
        android_sdk=31,
    )
    probe_mock = AsyncMock(side_effect=[_probe("adb_unauthorized"), candidate])
    release_mock = AsyncMock(return_value=RELEASE)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        authorize = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_install_candidate"
    assert result["description_placeholders"] == {
        "address": "panel.local",
        "model": "WF1589T",
        "serial": "serial-123",
        "abi": "arm64-v8a",
        "sdk": "31",
        "version": "0.9.7",
        "tag": "v0.9.7",
        "sha256": "a" * 64,
    }
    signer_mock.assert_awaited_once_with(hass)
    assert probe_mock.await_args_list[0].args[0].stored_value == "192.168.1.23"
    assert probe_mock.await_args_list[1].args[0].stored_value == "192.168.1.23"
    assert probe_mock.await_args_list[1].args[1] is signer
    release_mock.assert_awaited_once()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_authorization_credential_storage_error_is_actionable(
    hass: HomeAssistant,
) -> None:
    """Failure to durably load the ADB identity does not probe or create an entry."""
    probe_mock = AsyncMock(return_value=_probe("adb_unauthorized"))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(side_effect=AdbCredentialError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        authorize = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_adb"
    assert result["errors"] == {"base": "adb_credential_error"}
    assert probe_mock.await_count == 1
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("failure_at", "expected_error"),
    [
        ("signer", "unknown"),
        ("probe", "unknown"),
    ],
)
async def test_install_authorization_failures_create_no_config_entry(
    hass: HomeAssistant, failure_at: str, expected_error: str
) -> None:
    """Authorization retry failures stay in the trust checkpoint without an entry."""
    signer = object()
    signer_mock = AsyncMock(
        side_effect=RuntimeError("signer") if failure_at == "signer" else None,
        return_value=signer,
    )
    candidate = _probe(
        "install_candidate",
        model="WF1589T",
        serial="serial-123",
        primary_abi="arm64-v8a",
        android_sdk=31,
    )
    second_probe: object = RuntimeError("probe") if failure_at == "probe" else candidate
    probe_mock = AsyncMock(side_effect=[_probe("adb_unauthorized"), second_probe])
    release_mock = AsyncMock(
        side_effect=ReleaseResolutionError if failure_at == "release" else None,
        return_value=RELEASE,
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        authorize = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authorize_adb"
    assert result["errors"] == {"base": expected_error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_install_candidate_release_resolution_failure_is_non_mutating(
    hass: HomeAssistant,
) -> None:
    """An install candidate is not presented without an exact verified release."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(
                return_value=_probe(
                    "install_candidate",
                    model="WF1589T",
                    serial="serial-123",
                    primary_abi="arm64-v8a",
                    android_sdk=31,
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(side_effect=ReleaseResolutionError),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    assert result["errors"] == {"base": "cannot_resolve_release"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_unexpected_release_failure_is_not_misclassified(
    hass: HomeAssistant,
) -> None:
    """Only the resolver's expected refusal becomes a release availability error."""
    clean = _probe(
        "install_candidate",
        model="WF1589T",
        serial="serial-123",
        primary_abi="arm64-v8a",
        android_sdk=31,
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=clean),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(side_effect=RuntimeError("unexpected")),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )

    assert result["step_id"] == "choose_version"
    assert result["errors"] == {"base": "unknown"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("health_error", "probe_result"),
    [
        (RuntimeError("health"), _probe("install_candidate")),
        (CannotConnectError(), RuntimeError("probe")),
        (
            CannotConnectError(),
            SimpleNamespace(state=SimpleNamespace(value="future_state")),
        ),
    ],
)
async def test_install_unexpected_failures_are_safe(
    hass: HomeAssistant, health_error: Exception, probe_result: object
) -> None:
    """Unknown failures and future states neither escape nor create an entry."""
    probe_mock = (
        AsyncMock(side_effect=probe_result)
        if isinstance(probe_result, Exception)
        else AsyncMock(return_value=probe_result)
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=health_error),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_panel"
    assert result["errors"] == {"base": "unknown"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    "step_method",
    [
        "async_step_authorize_adb",
        "async_step_connect_found",
        "async_step_confirm_install_candidate",
    ],
)
async def test_stale_confirmation_submission_fails_closed(
    hass: HomeAssistant, step_method: str
) -> None:
    """A confirmation cannot succeed after its retained flow state is lost."""
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    result = await getattr(flow, step_method)({})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


TARGET = InstallTarget(
    address="panel.local",
    pinned_address="192.168.1.23",
    adb_serial="serial-123",
    model="WF1589T",
    primary_abi="arm64-v8a",
    android_sdk=31,
)
ARTIFACT = InstallArtifact(
    descriptor_schema=DESCRIPTOR.schema,
    release_tag=DESCRIPTOR.release_tag,
    version_name=DESCRIPTOR.version_name,
    version_code=DESCRIPTOR.version_code,
    apk_name=DESCRIPTOR.apk_name,
    apk_sha256=DESCRIPTOR.apk_sha256,
    apk_size=DESCRIPTOR.apk_size,
    package_id=DESCRIPTOR.package_id,
    signer_certificate_sha256=DESCRIPTOR.signer_certificate_sha256,
    min_sdk=DESCRIPTOR.min_sdk,
    supported_abis=DESCRIPTOR.supported_abis,
    database_compatibility=DESCRIPTOR.database_compatibility,
    launch_component=DESCRIPTOR.launch_component,
)
CANDIDATE = InstallTargetProbe(
    state=InstallTargetState.INSTALL_CANDIDATE,
    model=TARGET.model,
    serial=TARGET.adb_serial,
    primary_abi=TARGET.primary_abi,
    android_sdk=TARGET.android_sdk,
)
CREDENTIAL = AdbCredential(signer=object(), generation_id="b" * 64)


def _rc_release() -> ReleaseArtifact:
    tag = "v0.9.7-rc3"
    apk = f"ha-paneld-{tag}-manual-setup-required.apk"
    return replace(
        RELEASE,
        tag=tag,
        version=tag[1:],
        apk_name=apk,
        descriptor=replace(
            DESCRIPTOR, release_tag=tag, version_name=tag[1:], apk_name=apk
        ),
    )


@pytest.mark.parametrize(
    "tag",
    [
        None,
        False,
        3,
        " ",
        "v0.9.7",
        "v0.9.7-rc03",
        "v0.9.7-rc3\n",
        "v0.9.7-rc" + "3" * 60,
        "v0.9.8-rc1",
    ],
)
async def test_invalid_rc_selection_precedes_all_contact(
    hass: HomeAssistant, tag: object
) -> None:
    """A malformed or unoffered RC is refused before any release or key work."""
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ) as probe,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(),
        ) as rc,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(),
        ) as stable,
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(),
        ) as credential,
    ):
        choose = await flow.async_step_add_panel({CONF_ADDRESS: "panel.local"})
        assert choose["step_id"] == "choose_version"
        # Called directly: HA's selector would reject most of these before the
        # step, so this proves the step's own guard.
        result = await flow.async_step_choose_version({"release_candidate": tag})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    assert result["errors"] == {"release_candidate": "invalid_release_candidate"}
    assert probe.await_count == 1
    rc.assert_not_awaited()
    stable.assert_not_awaited()
    credential.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()
    assert flow._pending_release is None


async def test_rc_selection_requires_exact_translated_consent_and_frozen_plan(
    hass: HomeAssistant,
) -> None:
    selected = _rc_release()
    receipt = _receipt(InstallPhase.APPROVED)
    manager = _manager_for(receipt)
    stable = AsyncMock()
    rc = AsyncMock(return_value=selected)
    credential = AsyncMock(return_value=CREDENTIAL)
    progress = AsyncMock(
        return_value={"type": FlowResultType.ABORT, "reason": "install_worker_stopped"}
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ) as probe,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            stable,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            rc,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch.object(HaPaneldConfigFlow, "_async_show_install_progress", progress),
    ):
        form = await _start_step(hass, "add_panel")
        assert "release_candidate" not in form["data_schema"].schema
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        assert "release_candidate" in choose["data_schema"].schema
        rc.assert_not_awaited()
        preview = await _choose_version(hass, choose, selected.tag)
        assert preview["step_id"] == "confirm_install_rc"
        assert preview["description_placeholders"]["tag"] == selected.tag
        assert preview["description_placeholders"]["sha256"] == selected.sha256
        credential.assert_not_awaited()
        manager.async_create_or_join.assert_not_awaited()
        await hass.config_entries.flow.async_configure(preview["flow_id"], {})
    stable.assert_not_awaited()
    rc.assert_awaited_once()
    assert rc.await_args.args[1] == selected.tag
    assert probe.await_count == 2
    manager.async_create_or_join.assert_awaited_once()
    planned = manager.async_create_or_join.await_args.args[1]
    assert planned.release_tag == selected.tag
    assert planned.apk_sha256 == selected.sha256
    progress.assert_awaited_once_with(receipt)


async def test_rc_resolution_failure_never_falls_back_or_creates_credential(
    hass: HomeAssistant,
) -> None:
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(side_effect=ReleaseResolutionError),
        ) as rc,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(),
        ) as stable,
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(),
        ) as credential,
    ):
        form = await _start_step(hass, "add_panel")
        result = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
            "v0.9.7-rc3",
        )
    assert result["step_id"] == "choose_version"
    assert result["errors"] == {"base": "cannot_resolve_release"}
    rc.assert_awaited_once()
    stable.assert_not_awaited()
    credential.assert_not_awaited()


async def test_installed_panel_only_connects_without_release_work(
    hass: HomeAssistant, install_release_catalog: AsyncMock
) -> None:
    """A healthy panel is connected without asking for, or resolving, a release."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(),
        ) as adb,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(),
        ) as rc,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(),
        ) as stable,
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        assert result["step_id"] == "found_panel"
        result = await _connect_found(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "panel.local"}
    adb.assert_not_awaited()
    rc.assert_not_awaited()
    stable.assert_not_awaited()
    install_release_catalog.assert_not_awaited()


@pytest.mark.parametrize("active_tag", ["v0.9.7", "v0.9.7-rc3"])
async def test_active_job_resumes_with_its_own_release(
    hass: HomeAssistant, active_tag: str, install_release_catalog: AsyncMock
) -> None:
    """An active job resumes its original release; no version is ever asked."""
    receipt = replace(
        _receipt(InstallPhase.APPROVED),
        artifact=replace(
            ARTIFACT,
            release_tag=active_tag,
            version_name=active_tag[1:],
            apk_name=f"ha-paneld-{active_tag}-manual-setup-required.apk",
        ),
    )
    manager = _manager_for(receipt)
    manager.async_find_active.return_value = receipt
    progress = AsyncMock(
        return_value={"type": FlowResultType.ABORT, "reason": "install_worker_stopped"}
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(),
        ) as health,
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(),
        ) as adb,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(),
        ) as rc,
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(),
        ) as stable,
        patch.object(HaPaneldConfigFlow, "_async_show_install_progress", progress),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_worker_stopped"
    progress.assert_awaited_once_with(receipt)
    assert progress.await_args.args[0].artifact.release_tag == active_tag
    health.assert_not_awaited()
    adb.assert_not_awaited()
    rc.assert_not_awaited()
    stable.assert_not_awaited()
    install_release_catalog.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()


def _receipt(
    phase: InstallPhase,
    *,
    result_code: InstallResultCode | None = None,
) -> InstallJobReceipt:
    """Build the receipt shape consumed by flow-only tests."""
    return InstallJobReceipt(
        job_id="1" * 32,
        revision=12,
        executor_generation=1,
        created_at="2026-09-03T00:00:00+00:00",
        updated_at="2026-09-03T00:01:00+00:00",
        phase=phase,
        cancel_requested=phase is InstallPhase.CANCELLED,
        attempt=1,
        target=TARGET,
        artifact=ARTIFACT,
        plan_sha256="c" * 64,
        adb_credential_id=CREDENTIAL.generation_id,
        preflight_root_mode=(
            None
            if phase
            in {
                InstallPhase.APPROVED,
                InstallPhase.AUTHORIZING,
                InstallPhase.PREFLIGHT,
            }
            else "rootless"
        ),
        actual_apk_bytes=(
            ARTIFACT.apk_size
            if phase
            in {
                InstallPhase.ARTIFACT_READY,
                InstallPhase.REVALIDATING,
                InstallPhase.STAGING,
                InstallPhase.INSTALLING,
                InstallPhase.INSTALLED,
                InstallPhase.LAUNCHING,
                InstallPhase.HEALTH_CHECK,
                InstallPhase.HEALTHY_UNCLAIMED,
                InstallPhase.CONSUMED,
            }
            else None
        ),
        health_checked_at=(
            "2026-09-03T00:00:30+00:00"
            if phase in {InstallPhase.HEALTHY_UNCLAIMED, InstallPhase.CONSUMED}
            else None
        ),
        result_code=result_code,
        consumed_entry_id=(
            "01M1JA9YX70TNCXNDNPJRC7QFC" if phase is InstallPhase.CONSUMED else None
        ),
    )


def _manager_for(receipt: InstallJobReceipt) -> SimpleNamespace:
    """Return the narrow receipt authority used by the flow."""
    return SimpleNamespace(
        async_find_active=AsyncMock(return_value=None),
        async_create_or_join=AsyncMock(return_value=(receipt, True)),
        async_get=AsyncMock(return_value=receipt),
        async_transition=AsyncMock(return_value=receipt),
    )


def _executor_for() -> SimpleNamespace:
    """Return the narrow detached-worker API the progress steps use."""
    return SimpleNamespace(
        async_ensure_job=AsyncMock(return_value=None),
        async_wait=AsyncMock(),
    )


def _direct_result_flow(
    hass: HomeAssistant,
    receipt: InstallJobReceipt,
    executor: SimpleNamespace,
    *,
    flow_id: str = "flow-finalizer",
) -> HaPaneldConfigFlow:
    """Construct a finalization flow without involving unrelated UI setup."""
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = flow_id
    flow._pending_job_id = receipt.job_id
    flow._install_executor = executor
    return flow


async def test_descriptorless_unauthorized_release_never_loads_a_credential(
    hass: HomeAssistant,
) -> None:
    """Legacy releases remain exact preview-only before any ADB key is offered."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    credential_mock = AsyncMock()
    durable_mock = AsyncMock()
    signer_mock = AsyncMock()
    verify_mock = AsyncMock()
    probe_mock = AsyncMock(return_value=_probe("adb_unauthorized"))
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=LEGACY_RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            durable_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            signer_mock,
        ),
        patch(
            "custom_components.panel_assistant.install_executor.async_verify_installed_target",
            verify_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert preview["step_id"] == "release_preview_only"
    assert preview["description_placeholders"] == {
        "address": "panel.local",
        "version": RELEASE.version,
        "tag": RELEASE.tag,
        "sha256": RELEASE.sha256,
    }
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "choose_version"
    credential_mock.assert_not_awaited()
    durable_mock.assert_not_awaited()
    signer_mock.assert_not_awaited()
    verify_mock.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()
    assert len(probe_mock.await_args.args) == 1


async def test_release_preview_only_returns_to_choose_version(
    hass: HomeAssistant,
) -> None:
    """A legacy release is not a dead end: another release can still be chosen."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    credential_mock = AsyncMock()
    stable_mock = AsyncMock(return_value=LEGACY_RELEASE)
    rc_mock = AsyncMock(return_value=_rc_release())
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            stable_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            rc_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        assert preview["step_id"] == "release_preview_only"
        back = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

        assert back["type"] is FlowResultType.FORM
        assert back["step_id"] == "choose_version"
        assert back["errors"] is None
        assert back["description_placeholders"] == {"address": "panel.local"}
        rc_preview = await _choose_version(hass, back, "v0.9.7-rc3")

    assert rc_preview["step_id"] == "confirm_install_rc"
    assert rc_preview["description_placeholders"]["tag"] == "v0.9.7-rc3"
    stable_mock.assert_awaited_once()
    rc_mock.assert_awaited_once()
    credential_mock.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()


@pytest.mark.parametrize(
    "changed_probe",
    [
        replace(CANDIDATE, state=InstallTargetState.RETAINED_OR_AMBIGUOUS),
        replace(CANDIDATE, serial="serial-456"),
        replace(CANDIDATE, model="other-model"),
        replace(CANDIDATE, primary_abi="armeabi-v7a"),
        replace(CANDIDATE, android_sdk=30),
    ],
    ids=["unsafe_state", "serial", "model", "abi", "sdk"],
)
async def test_signed_confirmation_rejects_every_target_identity_drift(
    hass: HomeAssistant, changed_probe: InstallTargetProbe
) -> None:
    """Confirmation binds state, serial, model, ABI, and SDK to the preview."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    probe_mock = AsyncMock(side_effect=[CANDIDATE, changed_probe])
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_install_candidate"
    assert result["errors"] == {"base": "install_candidate_changed"}
    manager.async_create_or_join.assert_not_awaited()


@pytest.mark.parametrize("authorize_first", [False, True], ids=["trusted", "authorize"])
async def test_failed_install_with_identical_bytes_is_adopted(
    hass: HomeAssistant, authorize_first: bool
) -> None:
    """A silent installed panel reaches the chosen RC and its exact bytes win."""
    installed = replace(CANDIDATE, state=InstallTargetState.MIGRATION_CANDIDATE)
    probes = (
        [_probe("adb_unauthorized"), installed, installed]
        if authorize_first
        else [installed, installed]
    )
    rc = _rc_release()
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    progress = AsyncMock(
        return_value={"type": FlowResultType.ABORT, "reason": "proof-complete"}
    )
    preflight = SimpleNamespace(target_installed=True, root_mode=AdbRootMode.ROOTLESS)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(side_effect=probes),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(return_value=rc),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_signer",
            AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_preflight_install",
            AsyncMock(return_value=preflight),
        ) as preflight_mock,
        patch(
            "custom_components.panel_assistant.config_flow.async_installed_artifact_size",
            AsyncMock(return_value=rc.descriptor.apk_size),
        ) as installed_bytes,
        patch.object(HaPaneldConfigFlow, "_async_show_install_progress", progress),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        preview = await _choose_version(hass, choose, rc.tag)
        if authorize_first:
            assert preview["step_id"] == "authorize_adb"
            preview = await hass.config_entries.flow.async_configure(
                preview["flow_id"], {}
            )
        assert preview["step_id"] == "confirm_install_rc"
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert result == {"type": FlowResultType.ABORT, "reason": "proof-complete"}
    preflight_mock.assert_awaited_once()
    assert preflight_mock.await_args.kwargs["admit_installed_target"] is True
    installed_bytes.assert_awaited_once()
    assert installed_bytes.await_args.kwargs["expected_root_mode"] is (
        AdbRootMode.ROOTLESS
    )
    manager.async_create_or_join.assert_awaited_once()
    assert manager.async_create_or_join.await_args.args[1].release_tag == rc.tag


async def test_same_version_at_different_bytes_is_not_overwritten(
    hass: HomeAssistant,
) -> None:
    """Version text never substitutes for byte identity on the adopt path."""
    installed = replace(CANDIDATE, state=InstallTargetState.MIGRATION_CANDIDATE)
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    rc = _rc_release()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(side_effect=[installed, installed]),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_rc_release",
            AsyncMock(return_value=rc),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_preflight_install",
            AsyncMock(
                return_value=SimpleNamespace(
                    target_installed=True, root_mode=AdbRootMode.ROOTLESS
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_installed_artifact_size",
            AsyncMock(return_value=None),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        preview = await _choose_version(hass, choose, rc.tag)
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert result["step_id"] == "confirm_install_rc"
    assert result["errors"] == {"base": "installed_artifact_mismatch"}
    manager.async_create_or_join.assert_not_awaited()


async def test_confirmation_late_duplicate_guard_precedes_all_contact(
    hass: HomeAssistant,
) -> None:
    """An entry created after preview prevents every confirmation side effect."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    revalidate_mock = AsyncMock()
    credential_mock = AsyncMock()
    probe_mock = AsyncMock(return_value=CANDIDATE)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            revalidate_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: "panel.local"}).add_to_hass(
            hass
        )
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    revalidate_mock.assert_not_awaited()
    credential_mock.assert_not_awaited()
    assert probe_mock.await_count == 1


async def test_signed_confirmation_pin_drift_precedes_credentials_and_job(
    hass: HomeAssistant,
) -> None:
    """The original LAN pin remains part of the explicit install authorization."""
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    credential_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(return_value=CANDIDATE),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        with patch(
            "custom_components.panel_assistant.config_flow.async_revalidate_install_target",
            AsyncMock(
                side_effect=InstallNetworkError(
                    InstallNetworkErrorCode.PINNED_TARGET_REMOVED
                )
            ),
        ):
            result = await hass.config_entries.flow.async_configure(
                preview["flow_id"], {}
            )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "install_target_changed"}
    credential_mock.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()


async def test_install_progress_removal_detaches_without_cancelling_worker(
    hass: HomeAssistant,
) -> None:
    """The flow passes only its shielded waiter to HA progress lifecycle."""
    receipt = _receipt(InstallPhase.APPROVED)
    manager = _manager_for(receipt)
    worker_gate = asyncio.Event()
    worker = hass.async_create_task(worker_gate.wait())

    async def _wait(_job_id: str) -> InstallJobReceipt:
        await asyncio.shield(worker)
        return receipt

    executor = _executor_for()
    executor.async_ensure_job.return_value = worker
    executor.async_wait.side_effect = _wait
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(side_effect=[CANDIDATE, CANDIDATE]),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            AsyncMock(return_value=RELEASE),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        preview = await _choose_version(
            hass,
            await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            ),
        )
        progress = await hass.config_entries.flow.async_configure(
            preview["flow_id"], {}
        )
        hass.config_entries.flow.async_abort(progress["flow_id"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    executor.async_ensure_job.assert_awaited_once_with(receipt.job_id)
    assert not worker.cancelled()
    worker_gate.set()
    await worker


async def test_completed_flow_waiter_advances_through_progress_done() -> None:
    """HA receives the required progress-done transition before install_result."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    flow = HaPaneldConfigFlow()
    flow.flow_id = "flow-progress"
    flow._pending_job_id = receipt.job_id
    waiter: asyncio.Future[InstallJobReceipt] = asyncio.Future()
    waiter.set_result(receipt)
    flow._progress_waiter = waiter  # type: ignore[assignment]

    result = await flow.async_step_install_progress()

    assert result["type"] is FlowResultType.SHOW_PROGRESS_DONE
    assert result["step_id"] == "install_result"
    assert flow._progress_waiter is None


async def test_nonrestartable_worker_does_not_create_progress_callback_loop(
    hass: HomeAssistant,
) -> None:
    """A locally cancelled executor job requires restart without a new waiter."""
    receipt = _receipt(InstallPhase.DOWNLOADING)
    manager = _manager_for(receipt)
    executor = _executor_for()
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = "flow-stopped-worker"
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        result = await flow._async_show_install_progress(receipt)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_worker_stopped"
    executor.async_wait.assert_not_awaited()
    assert flow._progress_waiter is None


async def test_nonrestartable_worker_refresh_failure_is_privacy_safe(
    hass: HomeAssistant,
) -> None:
    """Unexpected receipt refresh failure neither escapes nor creates a waiter."""
    receipt = _receipt(InstallPhase.DOWNLOADING)
    manager = _manager_for(receipt)
    manager.async_get.side_effect = RuntimeError("unexpected")
    executor = _executor_for()
    flow = HaPaneldConfigFlow()
    flow.hass = hass
    flow.flow_id = "flow-stopped-worker-refresh"
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
    ):
        result = await flow._async_show_install_progress(receipt)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_failed"
    executor.async_wait.assert_not_awaited()
    assert flow._progress_waiter is None


@pytest.mark.parametrize(
    "phase",
    [
        InstallPhase.APPROVED,
        InstallPhase.AUTHORIZING,
        InstallPhase.PREFLIGHT,
        InstallPhase.DOWNLOADING,
        InstallPhase.ARTIFACT_READY,
        InstallPhase.REVALIDATING,
        InstallPhase.INSTALLED,
        InstallPhase.HEALTH_CHECK,
    ],
)
async def test_existing_job_reattaches_before_any_panel_or_release_contact(
    hass: HomeAssistant, phase: InstallPhase
) -> None:
    """Opening the flow is sufficient to resume a safe zero-entry job."""
    receipt = _receipt(phase)
    manager = _manager_for(receipt)
    manager.async_find_active.return_value = receipt
    wait_gate = asyncio.Event()

    async def _wait(_job_id: str) -> InstallJobReceipt:
        await wait_gate.wait()
        return receipt

    executor = _executor_for()
    worker = hass.async_create_task(wait_gate.wait())
    executor.async_ensure_job.return_value = worker
    executor.async_wait.side_effect = _wait
    health_mock = AsyncMock()
    probe_mock = AsyncMock()
    release_mock = AsyncMock()
    credential_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_executor",
            AsyncMock(return_value=executor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            health_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            probe_mock,
        ),
        patch(
            "custom_components.panel_assistant.release_catalog.async_resolve_stable_release",
            release_mock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            credential_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        progress = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        hass.config_entries.flow.async_abort(progress["flow_id"])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert progress["type"] is FlowResultType.SHOW_PROGRESS
    manager.async_find_active.assert_awaited_once_with(
        TARGET.address, TARGET.pinned_address
    )
    executor.async_ensure_job.assert_awaited_once_with(receipt.job_id)
    health_mock.assert_not_awaited()
    probe_mock.assert_not_awaited()
    release_mock.assert_not_awaited()
    credential_mock.assert_not_awaited()
    wait_gate.set()
    await worker


@pytest.mark.parametrize(
    ("phase", "code", "reason"),
    [
        (
            InstallPhase.CANCELLED,
            InstallResultCode.CANCELLED_BY_USER,
            "install_cancelled",
        ),
        (
            InstallPhase.CANCELLED,
            InstallResultCode.CANCELLED_AFTER_STAGING_CLEANUP,
            "install_cancelled_after_staging_cleanup",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.AUTHORIZATION_FAILED,
            "install_authorization_failed",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.PREFLIGHT_REJECTED,
            "install_preflight_rejected",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.ARTIFACT_REJECTED,
            "install_artifact_rejected",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.TRANSPORT_FAILED,
            "install_transport_failed",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.INSTALL_FAILED,
            "install_package_failed",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.LAUNCH_FAILED,
            "install_launch_failed",
        ),
        (
            InstallPhase.FAILED,
            InstallResultCode.HEALTH_CHECK_FAILED,
            "install_health_check_failed",
        ),
        (
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.AMBIGUOUS_MUTATION,
            "install_ambiguous_mutation",
        ),
        (
            InstallPhase.RECOVERY_REQUIRED,
            InstallResultCode.VERIFICATION_REQUIRED,
            "install_verification_required",
        ),
        (
            InstallPhase.CONSUMED,
            InstallResultCode.ENTRY_CREATED,
            "already_configured",
        ),
    ],
)
async def test_every_terminal_install_result_is_privacy_safe_and_creates_no_entry(
    hass: HomeAssistant,
    phase: InstallPhase,
    code: InstallResultCode,
    reason: str,
) -> None:
    """Terminal receipts expose only stable result categories."""
    receipt = _receipt(phase, result_code=code)
    manager = _manager_for(receipt)
    flow = _direct_result_flow(hass, receipt, _executor_for())
    with patch(
        "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
        AsyncMock(return_value=manager),
    ):
        result = await flow.async_step_install_result()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert not hass.config_entries.async_entries(DOMAIN)


_FINAL = "custom_components.panel_assistant.install_executor"


def _finalizing_executor(
    hass: HomeAssistant, manager: SimpleNamespace
) -> InstallExecutor:
    """The real process executor, which owns final verification and its lease."""
    return InstallExecutor(hass, manager)  # type: ignore[arg-type]


@contextlib.contextmanager
def _final_proof(
    manager: SimpleNamespace,
    *,
    revalidate: AsyncMock | None = None,
    durable: AsyncMock | None = None,
    verify: AsyncMock | None = None,
    health: AsyncMock | None = None,
) -> Iterator[None]:
    """Patch every panel contact the executor's final proof makes."""
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            f"{_FINAL}.async_revalidate_install_target",
            revalidate or AsyncMock(),
        ),
        patch(
            f"{_FINAL}.async_get_durable_adb_credential",
            durable or AsyncMock(return_value=CREDENTIAL),
        ),
        patch(f"{_FINAL}.async_verify_installed_target", verify or AsyncMock()),
        patch(
            f"{_FINAL}.HaPaneldClient.async_get_health",
            health
            or AsyncMock(return_value=replace(HEALTH, version=ARTIFACT.version_name)),
        ),
    ):
        yield


@pytest.mark.parametrize(
    ("failure", "expected_type"),
    [
        ("dns_transport", FlowResultType.FORM),
        ("pin_drift", FlowResultType.ABORT),
        ("credential_error", FlowResultType.ABORT),
        ("credential_drift", FlowResultType.ABORT),
        ("adb_transport", FlowResultType.FORM),
        ("adb_identity", FlowResultType.ABORT),
        ("adb_root", FlowResultType.ABORT),
        ("adb_package", FlowResultType.ABORT),
        ("health_transport", FlowResultType.FORM),
        ("health_invalid", FlowResultType.ABORT),
        ("version_drift", FlowResultType.ABORT),
    ],
)
async def test_final_verification_retry_and_recovery_boundaries(
    hass: HomeAssistant, failure: str, expected_type: FlowResultType
) -> None:
    """Only temporary transport loss keeps a healthy receipt retryable."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    revalidate = AsyncMock(return_value=install_network_pin)
    durable = AsyncMock(return_value=CREDENTIAL)
    verify = AsyncMock()
    health = AsyncMock(return_value=replace(HEALTH, version=ARTIFACT.version_name))

    if failure == "dns_transport":
        revalidate.side_effect = InstallNetworkError(
            InstallNetworkErrorCode.RESOLUTION_TIMEOUT
        )
    elif failure == "pin_drift":
        revalidate.side_effect = InstallNetworkError(
            InstallNetworkErrorCode.PINNED_TARGET_REMOVED
        )
    elif failure == "credential_error":
        durable.side_effect = AdbCredentialError
    elif failure == "credential_drift":
        durable.return_value = AdbCredential(signer=object(), generation_id="d" * 64)
    elif failure == "adb_transport":
        verify.side_effect = InstallAdbError(InstallAdbErrorCode.TARGET_UNREACHABLE)
    elif failure == "adb_identity":
        verify.side_effect = InstallAdbError(InstallAdbErrorCode.TARGET_CHANGED)
    elif failure == "adb_root":
        verify.side_effect = InstallAdbError(InstallAdbErrorCode.ROOT_MODE_CHANGED)
    elif failure == "adb_package":
        verify.side_effect = InstallAdbError(
            InstallAdbErrorCode.INSTALLED_PACKAGE_MISSING
        )
    elif failure == "health_transport":
        health.side_effect = CannotConnectError
    elif failure == "health_invalid":
        health.side_effect = InvalidResponseError
    elif failure == "version_drift":
        health.return_value = HEALTH

    with _final_proof(
        manager, revalidate=revalidate, durable=durable, verify=verify, health=health
    ):
        result = await flow.async_step_install_result()

    assert result["type"] is expected_type
    assert not executor.is_finalizer_active(receipt.job_id)
    if expected_type is FlowResultType.FORM:
        assert result["errors"] == {"base": "install_finalization_retry"}
        manager.async_transition.assert_not_awaited()
    else:
        assert result["reason"] == "install_recovery_required"
        subcode = {
            "pin_drift": "network:pinned_target_removed",
            "credential_error": "credential:unavailable",
            "credential_drift": "finalization:credential_changed",
            "adb_identity": "adb:target_changed",
            "adb_root": "adb:root_mode_changed",
            "adb_package": "adb:installed_package_missing",
            "health_invalid": "health:invalid_response",
            "version_drift": "health:identity_mismatch",
        }[failure]
        manager.async_transition.assert_awaited_once_with(
            receipt.job_id,
            receipt.revision,
            InstallPhase.RECOVERY_REQUIRED,
            result_code=InstallResultCode.VERIFICATION_REQUIRED,
            result_subcode=subcode,
        )
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("receipt_package", "observed_package", "creates"),
    [
        (SUCCESSOR_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, True),
        (SUCCESSOR_PACKAGE_ID, LEGACY_PACKAGE_ID, False),
        (SUCCESSOR_PACKAGE_ID, None, False),
        (LEGACY_PACKAGE_ID, None, True),
        (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID, False),
    ],
    ids=[
        "successor",
        "successor-receipt-legacy-app",
        "successor-receipt-silent-app",
        "legacy-predating-package-report",
        "legacy-receipt-successor-app",
    ],
)
async def test_final_verification_requires_the_installed_package(
    hass: HomeAssistant,
    receipt_package: str,
    observed_package: str | None,
    creates: bool,
) -> None:
    """Flow finalization applies the executor's package rule, not the version alone.

    Both apps are built from one tree, so the legacy app answering at the very
    version a successor receipt installed is not that install completing.
    """
    receipt = replace(
        _receipt(InstallPhase.HEALTHY_UNCLAIMED),
        artifact=replace(ARTIFACT, package_id=receipt_package),
    )
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    health = replace(HEALTH, version=ARTIFACT.version_name, package=observed_package)

    with _final_proof(manager, health=AsyncMock(return_value=health)):
        result = await flow.async_step_install_result()

    # A verified receipt keeps its lease for the entry's addition; a refused
    # one has already handed it back.
    assert executor.is_finalizer_active(receipt.job_id) is creates
    if creates:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        manager.async_transition.assert_not_awaited()
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "install_recovery_required"
        manager.async_transition.assert_awaited_once_with(
            receipt.job_id,
            receipt.revision,
            InstallPhase.RECOVERY_REQUIRED,
            result_code=InstallResultCode.VERIFICATION_REQUIRED,
            result_subcode="health:identity_mismatch",
        )


def _held_verification() -> tuple[AsyncMock, asyncio.Event, asyncio.Event]:
    """An ADB verification that waits until the test lets it finish."""
    started = asyncio.Event()
    allow = asyncio.Event()

    async def _verify(*_args: object, **_kwargs: object) -> None:
        started.set()
        await allow.wait()

    return AsyncMock(side_effect=_verify), started, allow


async def test_two_finalizers_produce_only_one_create_result(
    hass: HomeAssistant,
) -> None:
    """The process-wide lease prevents two dialogs creating duplicate entries."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow_one = _direct_result_flow(hass, receipt, executor, flow_id="flow-one")
    flow_two = _direct_result_flow(hass, receipt, executor, flow_id="flow-two")
    verify_mock, verify_started, allow_verify = _held_verification()

    with _final_proof(manager, verify=verify_mock):
        first_task = hass.async_create_task(flow_one.async_step_install_result())
        await verify_started.wait()
        second = await flow_two.async_step_install_result()
        allow_verify.set()
        first = await first_task

    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert first["title"] == HEALTH.panel_id
    assert first["data"] == {CONF_ADDRESS: TARGET.address}
    assert flow_one.unique_id is None
    assert second["type"] is FlowResultType.FORM
    assert second["errors"] == {"base": "install_finalization_busy"}
    assert executor._finalizers[receipt.job_id].owner == "flow-one"
    verified_target = verify_mock.await_args.args[0]
    assert verified_target.address.stored_value == TARGET.pinned_address
    assert verified_target.serial == TARGET.adb_serial
    assert verified_target.model == TARGET.model
    assert verified_target.primary_abi == TARGET.primary_abi
    assert verified_target.android_sdk == TARGET.android_sdk
    assert verify_mock.await_args.kwargs["expected_root_mode"].value == "rootless"
    assert verify_mock.await_args.kwargs["package_id"] == ARTIFACT.package_id


async def test_removal_during_verification_defers_finalizer_release(
    hass: HomeAssistant,
) -> None:
    """Closing one dialog cannot expose its lease while verification still runs."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    first = _direct_result_flow(hass, receipt, executor, flow_id="flow-removed")
    second = _direct_result_flow(hass, receipt, executor, flow_id="flow-other")
    verify_mock, verify_started, allow_verify = _held_verification()

    with _final_proof(manager, verify=verify_mock):
        first_task = hass.async_create_task(first.async_step_install_result())
        await verify_started.wait()
        first.async_remove()
        assert executor._finalizers[receipt.job_id].owner == "flow-removed"
        other_result = await second.async_step_install_result()
        allow_verify.set()
        first_result = await first_task

    assert other_result["type"] is FlowResultType.FORM
    assert other_result["errors"] == {"base": "install_finalization_busy"}
    assert first_result["type"] is FlowResultType.ABORT
    assert first_result["reason"] == "install_worker_stopped"
    assert not executor.is_finalizer_active(receipt.job_id)
    manager.async_transition.assert_not_awaited()


async def test_removal_during_lease_acquisition_cannot_leak_finalizer(
    hass: HomeAssistant,
) -> None:
    """A flow removed while its lease is being taken never contacts the panel."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    acquire_started = asyncio.Event()
    allow_acquire = asyncio.Event()
    reads = 0

    async def _get(_job_id: str) -> InstallJobReceipt:
        nonlocal reads
        reads += 1
        if reads == 2:
            # The executor's read under its lease lock.
            acquire_started.set()
            await allow_acquire.wait()
        return receipt

    manager.async_get.side_effect = _get
    durable = AsyncMock(return_value=CREDENTIAL)
    verify = AsyncMock()
    health = AsyncMock(return_value=replace(HEALTH, version=ARTIFACT.version_name))
    with _final_proof(manager, durable=durable, verify=verify, health=health):
        task = hass.async_create_task(flow.async_step_install_result())
        await acquire_started.wait()
        flow.async_remove()
        allow_acquire.set()
        result = await task

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "install_worker_stopped"
    assert not executor.is_finalizer_active(receipt.job_id)
    # A removed flow never reaches the panel, so it can never record a verdict.
    durable.assert_not_awaited()
    verify.assert_not_awaited()
    health.assert_not_awaited()
    manager.async_transition.assert_not_awaited()


async def test_cancelled_finalization_releases_the_lease_for_a_retry(
    hass: HomeAssistant,
) -> None:
    """Caller cancellation mid-proof cannot orphan the lease."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    verify_mock, verify_started, allow_verify = _held_verification()

    with _final_proof(manager, verify=verify_mock):
        first_task = hass.async_create_task(flow.async_step_install_result())
        await verify_started.wait()
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        assert not executor.is_finalizer_active(receipt.job_id)

        allow_verify.set()
        retry = await flow.async_step_install_result()

    assert retry["type"] is FlowResultType.CREATE_ENTRY
    assert executor._finalizers[receipt.job_id].owner == flow.flow_id
    manager.async_transition.assert_not_awaited()


async def test_same_flow_double_submit_runs_one_finalizer(
    hass: HomeAssistant,
) -> None:
    """Concurrent submissions in one dialog cannot share its executor lease."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    verify, verify_started, allow_verify = _held_verification()

    with _final_proof(manager, verify=verify):
        first_task = hass.async_create_task(flow.async_step_install_result())
        await verify_started.wait()
        second_task = hass.async_create_task(flow.async_step_install_result())
        await asyncio.sleep(0)
        allow_verify.set()
        first_result, second_result = await asyncio.gather(first_task, second_task)
        flow.async_remove()

    assert first_result["type"] is FlowResultType.CREATE_ENTRY
    assert second_result["type"] is FlowResultType.FORM
    assert second_result["errors"] == {"base": "install_finalization_busy"}
    assert verify.await_count == 1
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_final_duplicate_guard_rechecks_after_health_contact(
    hass: HomeAssistant,
) -> None:
    """An entry added during final verification wins before create_entry is returned."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)

    async def _health_then_duplicate() -> PanelHealth:
        MockConfigEntry(
            domain=DOMAIN, data={CONF_ADDRESS: receipt.target.address}
        ).add_to_hass(hass)
        return replace(HEALTH, version=ARTIFACT.version_name)

    with _final_proof(manager, health=AsyncMock(side_effect=_health_then_duplicate)):
        result = await flow.async_step_install_result()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not executor.is_finalizer_active(receipt.job_id)
    manager.async_transition.assert_not_awaited()


@pytest.mark.parametrize("persist_fails", [False, True], ids=["success", "failure"])
async def test_on_create_uses_actual_entry_id_and_never_fails_existing_entry(
    hass: HomeAssistant, persist_fails: bool
) -> None:
    """Receipt cleanup cannot undo an entry already added by Home Assistant."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    with _final_proof(manager):
        created = await flow.async_step_install_result()
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert executor.is_finalizer_active(receipt.job_id)
    if persist_fails:
        manager.async_transition.side_effect = InstallJobStoreError
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: TARGET.address})
    entry.add_to_hass(hass)
    result: dict = {"result": entry}

    returned = await flow.async_on_create_entry(result)  # type: ignore[arg-type]

    assert returned is result
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    manager.async_transition.assert_awaited_once_with(
        receipt.job_id,
        receipt.revision,
        InstallPhase.CONSUMED,
        result_code=InstallResultCode.ENTRY_CREATED,
        consumed_entry_id=entry.entry_id,
    )
    assert not executor.is_finalizer_active(receipt.job_id)


async def test_fresh_install_continues_into_panel_setup_after_entry_creation(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """The install lease is released before the owner signs in from a browser."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    manager = _manager_for(receipt)
    executor = _finalizing_executor(hass, manager)
    flow = _direct_result_flow(hass, receipt, executor)
    with _final_proof(manager):
        created = await flow.async_step_install_result()
    assert created["type"] is FlowResultType.CREATE_ENTRY
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ADDRESS: TARGET.address})
    entry.add_to_hass(hass)

    with patch(
        "custom_components.panel_assistant.config_flow.async_offer_ha_url",
        new_callable=AsyncMock,
    ) as handover:
        returned = await flow.async_on_create_entry({"result": entry})  # type: ignore[arg-type]

    assert not executor.is_finalizer_active(receipt.job_id)
    handover.assert_awaited_once()
    assert returned["next_flow"][0] is FlowType.OPTIONS_FLOW
    next_flow = hass.config_entries.options.async_get(returned["next_flow"][1])
    assert next_flow["handler"] == entry.entry_id
    assert next_flow["step_id"] == "onboarding"
    assert next_flow["context"]["source"] == "onboarding"


async def test_fresh_install_confirms_only_the_panels_requested_account(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """Browser sign-in alone never binds; the named account needs admin consent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: "192.168.1.23"},
        unique_id=DISCOVERY_ID,
        options={"authority": "native"},
    )
    entry.add_to_hass(hass)
    signed_in = await hass.auth.async_create_user("Current administrator")
    other = await hass.auth.async_create_user("Other account")
    async_record_binding_request(hass, DISCOVERY_ID, signed_in.id)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_offer_ha_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_setup_complete",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
    ):
        opened = await hass.config_entries.options.async_init(
            entry.entry_id, context={"source": "onboarding"}
        )
        assert opened["type"] is FlowResultType.EXTERNAL_STEP
        assert opened["url"] == "http://192.168.1.23:8888/setup"
        assert CONF_TRANSPORT_USER_ID not in entry.data
        done = await hass.config_entries.options.async_configure(opened["flow_id"], {})
        assert done["type"] is FlowResultType.EXTERNAL_STEP_DONE
        menu = await hass.config_entries.options.async_configure(opened["flow_id"])
        assert menu["type"] is FlowResultType.MENU
        assert menu["description_placeholders"] == {
            "panel": "alpha",
            "user": "Current administrator",
        }
        # A different account cannot be smuggled in under the displayed name.
        async_record_binding_request(hass, DISCOVERY_ID, other.id)
        changed = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "onboarding_bind"}
        )
        assert changed["type"] is FlowResultType.MENU
        assert changed["description_placeholders"]["user"] == "Other account"
        assert CONF_TRANSPORT_USER_ID not in entry.data
        confirmed = await hass.config_entries.options.async_configure(
            opened["flow_id"], {"next_step_id": "onboarding_bind"}
        )
    assert confirmed["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_TRANSPORT_USER_ID] == other.id
    assert entry.options == {"authority": "native"}


async def test_abandoned_fresh_install_can_resume_from_its_entry_options(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """Closing the Add dialog cannot strand an unbound panel at Repairs alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: "192.168.1.23"},
        options={"authority": "native"},
    )
    entry.add_to_hass(hass)
    menu = await hass.config_entries.options.async_init(entry.entry_id)

    assert menu["type"] is FlowResultType.MENU
    assert menu["step_id"] == "init"
    assert menu["menu_options"] == ["onboarding", "transport"]
    with patch(
        "custom_components.panel_assistant.config_flow.async_offer_ha_url",
        new_callable=AsyncMock,
    ) as handover:
        opened = await hass.config_entries.options.async_configure(
            menu["flow_id"], {"next_step_id": "onboarding"}
        )
    assert opened["type"] is FlowResultType.EXTERNAL_STEP
    assert opened["url"] == "http://192.168.1.23:8888/setup"
    handover.assert_awaited_once()


async def test_flow_removal_releases_lease_and_only_cancels_local_waiter(
    hass: HomeAssistant,
) -> None:
    """Removal cleans up flow ownership without touching an executor worker."""
    receipt = _receipt(InstallPhase.HEALTHY_UNCLAIMED)
    executor = _finalizing_executor(hass, _manager_for(receipt))
    flow = _direct_result_flow(hass, receipt, executor)
    assert await executor._async_acquire_finalizer(receipt.job_id, flow.flow_id)
    flow._finalizer_job_id = receipt.job_id
    waiter_gate = asyncio.Event()
    worker_gate = asyncio.Event()
    flow._progress_waiter = hass.async_create_task(waiter_gate.wait())
    worker = hass.async_create_task(worker_gate.wait())

    flow.async_remove()
    await asyncio.sleep(0)

    assert flow._progress_waiter is None
    assert not executor.is_finalizer_active(receipt.job_id)
    assert not worker.cancelled()
    worker_gate.set()
    await worker


async def test_no_target_state_can_fall_through_to_a_generic_refusal(
    hass: HomeAssistant,
) -> None:
    """Every state the probe can return says something specific and true.

    The maps read the state as a bare string, so adding a member to
    `InstallTargetState` cannot fail to compile and cannot be caught by
    dropping a default the way an unthreaded package id was. This is that
    check: it enumerates the enum itself, so a state added later without a
    message shows up here instead of as `unknown` in front of a person.
    """
    for state in InstallTargetState:
        with (
            patch(
                "custom_components.panel_assistant.config_flow"
                ".HaPaneldClient.async_get_health",
                AsyncMock(side_effect=CannotConnectError),
            ),
            patch(
                "custom_components.panel_assistant.config_flow"
                ".async_probe_install_target",
                AsyncMock(return_value=_probe(state.value)),
            ),
            patch(
                "custom_components.panel_assistant.release_catalog"
                ".async_resolve_stable_release",
                AsyncMock(return_value=RELEASE),
            ),
        ):
            form = await _start_step(hass, "add_panel")
            result = await hass.config_entries.flow.async_configure(
                form["flow_id"], {CONF_ADDRESS: "panel.local"}
            )

        if result["type"] is FlowResultType.FORM and result.get("errors"):
            assert result["errors"] != {"base": "unknown"}, state.value


async def test_a_silent_old_app_is_not_given_a_different_package(
    hass: HomeAssistant,
) -> None:
    """A chosen successor cannot adopt silent legacy bytes or install beside them."""
    installed = replace(CANDIDATE, state=InstallTargetState.MIGRATION_CANDIDATE)
    successor_apk = "panel-assistant-v0.9.7-manual-setup-required.apk"
    successor = replace(
        RELEASE,
        apk_name=successor_apk,
        descriptor=replace(
            DESCRIPTOR,
            apk_name=successor_apk,
            package_id="io.panelassistant.android",
            launch_component=(
                "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
            ),
        ),
    )
    manager = _manager_for(_receipt(InstallPhase.APPROVED))
    bytes_mock = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.async_get_install_job_manager",
            AsyncMock(return_value=manager),
        ),
        patch(
            "custom_components.panel_assistant.config_flow"
            ".HaPaneldClient.async_get_health",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_probe_install_target",
            AsyncMock(side_effect=[installed, installed]),
        ),
        patch(
            "custom_components.panel_assistant.release_catalog"
            ".async_resolve_stable_release",
            AsyncMock(return_value=successor),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_get_durable_adb_credential",
            AsyncMock(return_value=CREDENTIAL),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_preflight_install",
            AsyncMock(
                return_value=SimpleNamespace(
                    target_installed=False, root_mode=AdbRootMode.ROOTLESS
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.async_installed_artifact_size",
            bytes_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )
        preview = await _choose_version(hass, choose)
        result = await hass.config_entries.flow.async_configure(preview["flow_id"], {})

    assert preview["step_id"] == "confirm_install_candidate"
    assert result["errors"] == {"base": "installed_without_health"}
    bytes_mock.assert_not_awaited()
    manager.async_create_or_join.assert_not_awaited()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_the_authorization_retry_also_names_every_state_it_can_see(
    hass: HomeAssistant,
) -> None:
    """The second state map is a separate copy, so it needs its own proof.

    Reaching it means the first probe said the key was not trusted and the
    retry, now holding Home Assistant's key, saw something else. That map reads
    the state as a bare string too, so the same omission is possible there and
    invisible in the same way.
    """
    for state in InstallTargetState:
        if state in {
            InstallTargetState.INSTALL_CANDIDATE,
            InstallTargetState.INSTALLED,
            InstallTargetState.MIGRATION_CANDIDATE,
        }:
            continue
        probe_mock = AsyncMock(
            side_effect=[_probe("adb_unauthorized"), _probe(state.value)]
        )
        with (
            patch(
                "custom_components.panel_assistant.config_flow"
                ".HaPaneldClient.async_get_health",
                AsyncMock(side_effect=CannotConnectError),
            ),
            patch(
                "custom_components.panel_assistant.config_flow.async_get_adb_signer",
                AsyncMock(return_value=object()),
            ),
            patch(
                "custom_components.panel_assistant.config_flow"
                ".async_probe_install_target",
                probe_mock,
            ),
            patch(
                "custom_components.panel_assistant.release_catalog"
                ".async_resolve_stable_release",
                AsyncMock(return_value=RELEASE),
            ),
        ):
            form = await _start_step(hass, "add_panel")
            authorize = await _choose_version(
                hass,
                await hass.config_entries.flow.async_configure(
                    form["flow_id"], {CONF_ADDRESS: "panel.local"}
                ),
            )
            result = await hass.config_entries.flow.async_configure(
                authorize["flow_id"], {}
            )

        assert result["step_id"] == "authorize_adb", state.value
        assert result["errors"] != {"base": "unknown"}, state.value


async def test_adopting_a_panel_mid_setup_tells_it_where_home_assistant_is(
    hass: HomeAssistant,
) -> None:
    """The adoption site's positive path.

    "Install or adoption" means a panel discovered mid-setup is handed the
    address too, not just one this integration installed. The shared conftest
    stub reports setup finished, so this test overrides it — otherwise it would
    assert against a panel with nothing left to ask.
    """
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    hand_over = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
            AsyncMock(
                return_value=PanelSetupState(complete=False, accepts_handover=True)
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_hand_over_ha_url",
            hand_over,
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        assert form["step_id"] == "confirm_discovery"
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    hand_over.assert_awaited_once_with("http://192.0.2.5:8123")


async def test_adopting_an_older_panel_sends_it_no_key_it_would_refuse(
    hass: HomeAssistant,
) -> None:
    """The version gate at the adoption site."""
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    hand_over = AsyncMock()
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
            AsyncMock(
                return_value=PanelSetupState(complete=False, accepts_handover=False)
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_hand_over_ha_url",
            hand_over,
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    hand_over.assert_not_awaited()


@contextlib.contextmanager
def _panel_probes(health: PanelHealth) -> Iterator[None]:
    """Answer the flow's health and status probes for one panel.

    Entry creation happens inside this too: a real entry left to set itself up
    unpatched would reach for the panel over the network.
    """
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=health),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
    ):
        yield


async def _discover_panel(hass: HomeAssistant, health: PanelHealth) -> dict:
    """Drive one zeroconf discovery through its confirmation to the next step."""
    with _panel_probes(health):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        if form["type"] is not FlowResultType.FORM:
            return form
        return await hass.config_entries.flow.async_configure(form["flow_id"], {})


async def _choose(
    hass: HomeAssistant, health: PanelHealth, menu: dict, choice: str
) -> dict:
    """Take one option on the menu a discovery stopped at."""
    with _panel_probes(health):
        return await hass.config_entries.flow.async_configure(
            menu["flow_id"], {"next_step_id": choice}
        )


async def test_a_panel_that_asked_to_connect_is_confirmed_in_the_flow(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """The panel's account is confirmed while the administrator adding it is here.

    This is the whole point of the step: the panel has already asked and been
    refused for want of a confirmation, so asking for it now makes onboarding one
    consent instead of a Repairs item nobody knows to look for.
    """
    user = await hass.auth.async_create_user("Panel account")
    async_record_binding_request(hass, DISCOVERY_ID, user.id)

    menu = await _discover_panel(hass, DISCOVERY_HEALTH)

    assert menu["type"] is FlowResultType.MENU
    assert menu["step_id"] == "confirm_user"
    assert menu["description_placeholders"] == {
        "panel": "alpha",
        "user": "Panel account",
    }
    assert not hass.config_entries.async_entries(DOMAIN)

    result = await _choose(hass, DISCOVERY_HEALTH, menu, "bind_user")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_ADDRESS: "192.168.1.23",
        CONF_TRANSPORT_USER_ID: user.id,
    }
    # The request is answered, so a later flow does not offer a decision that has
    # already been taken.
    assert async_binding_request(hass, DISCOVERY_ID) is None


async def test_an_administrator_who_declines_still_gets_the_panel(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """Declining adds the panel unbound; it never silently binds the account."""
    user = await hass.auth.async_create_user("Panel account")
    async_record_binding_request(hass, DISCOVERY_ID, user.id)
    menu = await _discover_panel(hass, DISCOVERY_HEALTH)

    result = await _choose(hass, DISCOVERY_HEALTH, menu, "skip_binding")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}


async def test_a_panel_nobody_asked_about_is_added_with_nothing_extra(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """The offer is keyed to the panel that asked, not to any request at all."""
    user = await hass.auth.async_create_user("Panel account")
    async_record_binding_request(hass, "b" * 64, user.id)

    result = await _discover_panel(hass, DISCOVERY_HEALTH)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}


async def test_confirming_binds_only_the_account_the_menu_showed(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """The account bound is the one the menu named, never whoever asked most recently.

    This is the sharp edge of the whole step. An administrator reads one account name
    and submits; if a request that arrived in between could be bound instead, the
    consent would be for an account nobody was shown, which is the property the
    Repairs flow exists to protect.
    """
    shown = await hass.auth.async_create_user("Panel account")
    other = await hass.auth.async_create_user("Someone else")
    async_record_binding_request(hass, DISCOVERY_ID, shown.id)
    menu = await _discover_panel(hass, DISCOVERY_HEALTH)
    assert menu["description_placeholders"] == {
        "panel": "alpha",
        "user": "Panel account",
    }

    # Someone else asks while the menu is open, so the request no longer names the
    # account the administrator is looking at.
    async_record_binding_request(hass, DISCOVERY_ID, other.id)
    result = await _choose(hass, DISCOVERY_HEALTH, menu, "bind_user")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}
    assert result["data"].get(CONF_TRANSPORT_USER_ID) != other.id


async def test_an_account_that_is_no_longer_usable_is_not_bound(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """A deactivation while the menu is open is not confirmed by that submission."""
    user = await hass.auth.async_create_user("Panel account")
    async_record_binding_request(hass, DISCOVERY_ID, user.id)
    menu = await _discover_panel(hass, DISCOVERY_HEALTH)
    assert menu["step_id"] == "confirm_user"

    await hass.auth.async_deactivate_user(user)
    result = await _choose(hass, DISCOVERY_HEALTH, menu, "bind_user")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}


async def test_an_already_confirmed_panel_is_left_alone(
    hass: HomeAssistant,
    hass_admin_user: Any,
) -> None:
    """A panel that already has an account is never asked about again."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        data={CONF_ADDRESS: "192.168.1.23", CONF_TRANSPORT_USER_ID: "already-bound"},
        unique_id=DISCOVERY_ID,
    )
    entry.add_to_hass(hass)
    squatter = await hass.auth.async_create_user("Someone else")
    async_record_binding_request(hass, DISCOVERY_ID, squatter.id)

    result = await _discover_panel(hass, DISCOVERY_HEALTH)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_TRANSPORT_USER_ID] == "already-bound"


async def test_a_non_administrator_cannot_start_the_flow_that_confirms(
    hass: HomeAssistant,
    hass_admin_user: Any,
    hass_client: Any,
    hass_read_only_access_token: str,
) -> None:
    """Confirmation stays an administrator action: Core refuses the flow itself."""
    assert await async_setup_component(hass, "config", {})
    user = await hass.auth.async_create_user("Panel account")
    async_record_binding_request(hass, DISCOVERY_ID, user.id)
    client = await hass_client(hass_read_only_access_token)

    response = await client.post(
        "/api/config/config_entries/flow", json={"handler": DOMAIN}
    )

    assert response.status == 401
    assert not hass.config_entries.async_entries(DOMAIN)

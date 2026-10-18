"""Tests for the config flow network ADB install path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.adb_credentials import (
    AdbCredentialError,
)
from custom_components.panel_assistant.client import (
    CannotConnectError,
    InvalidAddressError,
    InvalidResponseError,
    PanelAddress,
    PanelSetupState,
)
from custom_components.panel_assistant.config_flow import (
    HaPaneldConfigFlow,
    _install_candidate_placeholders,
)
from custom_components.panel_assistant.const import (
    DOMAIN,
    INSTALL_RECOVERY_PLACEHOLDERS,
    help_url,
)
from custom_components.panel_assistant.install_network import (
    InstallNetworkError,
    InstallNetworkErrorCode,
)
from custom_components.panel_assistant.provisioning import (
    InstallTargetProbe,
)
from custom_components.panel_assistant.release import (
    ReleaseResolutionError,
)

from . import config_flow_helpers
from .config_flow_helpers import (
    BETA_HEALTH,
    HEALTH,
    RELEASE,
    _choose_version,
    _connect_found,
    _probe,
    _start_step,
)
from .install_recovery_text import assert_names_recovery_page

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
install_network_pin = config_flow_helpers.install_network_pin


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
        **INSTALL_RECOVERY_PLACEHOLDERS,
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
        client_class.return_value.async_get_setup_state = AsyncMock(
            return_value=PanelSetupState(complete=True)
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
    health_mock = AsyncMock(
        side_effect=[HEALTH, HEALTH, BETA_HEALTH, BETA_HEALTH, BETA_HEALTH]
    )
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


@pytest.mark.parametrize(
    "state",
    ["adb_unreachable", "installed", "migration_candidate", "retained_or_ambiguous"],
)
async def test_install_unavailable_duplicate_without_clean_probe_is_rejected(
    hass: HomeAssistant,
    state: str,
) -> None:
    """An unavailable endpoint is not proof that its app was removed."""
    MockConfigEntry(
        domain=DOMAIN,
        title="Configured panel",
        data={CONF_ADDRESS: "panel.local"},
    ).add_to_hass(hass)
    health_mock = AsyncMock(side_effect=CannotConnectError)
    probe_mock = AsyncMock(return_value=_probe(state))
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
    health_mock.assert_awaited_once()
    probe_mock.assert_awaited_once()


@pytest.mark.parametrize("health_error", [CannotConnectError, InvalidResponseError])
@pytest.mark.parametrize(
    "choice", [{}, {"release_candidate": "stable"}, {"release_candidate": "v0.9.7"}]
)
async def test_install_candidate_readiness_is_non_mutating_until_confirmation(
    hass: HomeAssistant,
    health_error: type[Exception],
    choice: dict[str, str],
    install_release_catalog: AsyncMock,
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
    if choice.get("release_candidate") == "v0.9.7":
        install_release_catalog.return_value = [
            {"tag": "v0.9.8-rc1", "prerelease": True},
            {"tag": RELEASE.tag, "prerelease": False},
        ]
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        choose = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "Panel.local"}
        )
        assert choose["step_id"] == "choose_version"
        release_mock.assert_not_awaited()
        # An omitted choice takes the recommended PA-channel release.
        confirm = await hass.config_entries.flow.async_configure(
            choose["flow_id"], choice
        )

        assert confirm["type"] is FlowResultType.FORM
        assert confirm["step_id"] == "confirm_install_candidate"
        assert confirm["description_placeholders"] == {
            **INSTALL_RECOVERY_PLACEHOLDERS,
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
        release_mock.assert_awaited_once_with(
            hass, "v0.9.7" if choice.get("release_candidate") == "v0.9.7" else None
        )

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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
            release_mock,
        ),
    ):
        form = await _start_step(hass, "add_panel")
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "panel.local"}
        )

    assert result["errors"] == {"base": "retained_or_ambiguous"}
    await assert_names_recovery_page(hass, "config.error.retained_or_ambiguous", result)
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
    assert result["description_placeholders"] == {
        "address": "panel.local",
        **INSTALL_RECOVERY_PLACEHOLDERS,
    }
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
    offered_keys: list[object] = []
    physically_approved = False

    async def probe_protected_panel(
        _address: PanelAddress,
        candidate_signer: object | None = None,
        *,
        authorize: bool = False,
    ) -> InstallTargetProbe:
        if authorize and candidate_signer is signer:
            offered_keys.append(candidate_signer)
            if physically_approved:
                return candidate
        return _probe("adb_unauthorized")

    probe_mock = AsyncMock(side_effect=probe_protected_panel)
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
        assert offered_keys == []
        pending = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )
        assert pending["step_id"] == "authorize_adb"
        assert pending["errors"] == {"base": "adb_still_unauthorized"}
        assert offered_keys == [signer]
        physically_approved = True
        result = await hass.config_entries.flow.async_configure(
            authorize["flow_id"], {}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_install_candidate"
    assert result["description_placeholders"] == {
        **INSTALL_RECOVERY_PLACEHOLDERS,
        "address": "panel.local",
        "model": "WF1589T",
        "serial": "serial-123",
        "abi": "arm64-v8a",
        "sdk": "31",
        "version": "0.9.7",
        "tag": "v0.9.7",
        "sha256": "a" * 64,
    }
    assert offered_keys == [signer, signer]
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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
            "custom_components.panel_assistant.config_flow.async_resolve_install_choice",
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

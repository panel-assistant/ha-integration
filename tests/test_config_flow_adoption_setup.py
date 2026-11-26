"""Adding a panel found mid-setup waits for its own setup, then adds it.

The setup step used to be a form whose Submit repeated the same error and
which never noticed the panel finish; it now waits and moves on by itself.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelSetupState,
)
from custom_components.panel_assistant.const import (
    DOMAIN,
)

from . import config_flow_helpers
from .config_flow_helpers import (
    DISCOVERY_HEALTH,
    _zeroconf_info,
)

# Bind the shared autouse fixtures here so pytest applies them to this module.
install_release_catalog = config_flow_helpers.install_release_catalog
install_network_pin = config_flow_helpers.install_network_pin


@contextlib.contextmanager
def _setup_reads(setup_state: AsyncMock) -> Iterator[None]:
    """Answer both of the client's setup reads from one panel state.

    The shared conftest stubs the two reads separately, both as finished. A
    test that makes only one say "not finished" describes a panel that answers
    the same request two ways, and the flow then goes back and forth between
    waiting for the setup and finding it unfinished without end.
    """
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
            setup_state,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_complete",
            AsyncMock(side_effect=lambda: setup_state.return_value.complete),
        ),
    ):
        yield


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
    setup_state = AsyncMock(
        return_value=PanelSetupState(complete=False, accepts_handover=True)
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        # The discovery card's own serial recheck would hold the background
        # wait below for its full delay.
        patch("custom_components.panel_assistant.discovery._SERIAL_RECHECK_SECONDS", 0),
        _setup_reads(setup_state),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_hand_over_ha_url",
            hand_over,
        ),
        patch(
            "custom_components.panel_assistant.config_flow._SETUP_WATCH_SECONDS", 0.05
        ),
        patch(
            "custom_components.panel_assistant.config_flow._SETUP_POLL_SECONDS", 0.01
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        assert form["step_id"] == "confirm_discovery"
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

        # A step with text and a link that moves on by itself, not a bare
        # external step (F3-02) and not a form the owner must resubmit.
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "panel_setup"
        assert result["progress_action"] == "panel_setup"
        assert result["description_placeholders"]["setup_url"].startswith("http")
        assert not hass.config_entries.async_entries(DOMAIN)
        assert hand_over.await_args.args == ("http://192.0.2.5:8123", [])
        offered = hand_over.await_count

        # The wait gives up while the panel's setup is still not finished.
        await hass.async_block_till_done(wait_background_tasks=True)
        again = await hass.config_entries.flow.async_configure(result["flow_id"])
        assert again["type"] is FlowResultType.FORM
        assert again["step_id"] == "panel_setup_check"
        assert again["errors"] == {"base": "panel_setup_not_finished"}
        # Refetching the dialog offers the address no second time.
        assert hand_over.await_count == offered

        setup_state.return_value = PanelSetupState(complete=True, accepts_handover=True)
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert done["type"] is FlowResultType.CREATE_ENTRY, done


async def test_adopting_a_panel_moves_on_once_its_setup_is_finished(
    hass: HomeAssistant,
) -> None:
    """A dialog left open while the panel's setup finishes adds the panel.

    It used to keep saying the setup was not finished until Submit was
    pressed again.
    """
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    setup_state = AsyncMock(
        return_value=PanelSetupState(complete=False, accepts_handover=True)
    )
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        # The discovery card's own serial recheck would hold the background
        # wait below for its full delay.
        patch("custom_components.panel_assistant.discovery._SERIAL_RECHECK_SECONDS", 0),
        _setup_reads(setup_state),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_hand_over_ha_url",
            AsyncMock(),
        ),
        patch("custom_components.panel_assistant.config_flow._SETUP_POLL_SECONDS", 0),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        setup_state.return_value = PanelSetupState(complete=True, accepts_handover=True)
        await hass.async_block_till_done(wait_background_tasks=True)
        # The finished wait tells the dialog, which refetches the flow.
        done = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert done["type"] is FlowResultType.CREATE_ENTRY, done
    assert hass.config_entries.flow.async_progress() == []


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
        # The discovery card's own serial recheck would hold the background
        # wait below for its full delay.
        patch("custom_components.panel_assistant.discovery._SERIAL_RECHECK_SECONDS", 0),
        _setup_reads(
            AsyncMock(
                return_value=PanelSetupState(complete=False, accepts_handover=False)
            )
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
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "panel_setup"
        # Closing the dialog stops the wait.
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done(wait_background_tasks=True)

    assert not hass.config_entries.async_entries(DOMAIN)
    hand_over.assert_not_awaited()


_FLOW_MODULE = "custom_components.panel_assistant.config_flow"


async def test_a_panel_whose_setup_reads_disagree_does_not_spin_the_flow(
    hass: HomeAssistant,
) -> None:
    """One read says finished, the other not: the flow pauses between rounds.

    The wait reads one, the step after it the other, and each sends the flow
    back to the other without suspending. A repeated wait pauses first, so
    the dialog answers and the loop turns once per poll, not without end.
    """
    await hass.config.async_update(internal_url="http://192.0.2.5:8123")
    reads = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_status",
            AsyncMock(side_effect=CannotConnectError),
        ),
        patch("custom_components.panel_assistant.discovery._SERIAL_RECHECK_SECONDS", 0),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_state",
            AsyncMock(
                return_value=PanelSetupState(complete=False, accepts_handover=True)
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_setup_complete",
            reads,
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_hand_over_ha_url",
            AsyncMock(),
        ),
        patch(
            "custom_components.panel_assistant.config_flow._SETUP_POLL_SECONDS", 0.05
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_info(),
        )
        async with asyncio.timeout(5):
            result = await hass.config_entries.flow.async_configure(form["flow_id"], {})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await asyncio.sleep(0.3)
        # About one round per 0.05 s poll, never thousands.
        assert reads.await_count < 20
        hass.config_entries.flow.async_abort(result["flow_id"])
    assert not hass.config_entries.async_entries(DOMAIN)

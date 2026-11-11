"""What a panel that has not connected says about its Home Assistant account."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import HomeAssistant
from yarl import URL

from .client import HaPaneldClient, HaPaneldError
from .status import PanelStatus

_LOGGER = logging.getLogger(__name__)

# What a panel that cannot reach Home Assistant gives as its reason, or gives
# no reason at all. Any other reason means Home Assistant answered it: a
# refused account or a refused hello is not a network fault.
_UNREACHED = {None, "transport_failure", "route_unavailable", "credential_unavailable"}


class SavedAccount:
    """The account one panel reports while it answers polls without a session."""

    def __init__(self, hass: HomeAssistant, connected: Callable[[], bool]) -> None:
        """Start with nothing asked; `connected` says the panel has since connected."""
        self._hass = hass
        self._connected = connected
        # Whether the panel has been asked at all, by a read that came back.
        self.read = False
        # The Home Assistant address the panel is trying: it holds an account
        # and no other server answers there. None without an account.
        self.trying: str | None = None
        self._compared: str | None = None
        self._elsewhere = False

    async def async_read(
        self, client: HaPaneldClient, panel: str, status: PanelStatus | None
    ) -> None:
        """Ask the panel; a read that fails keeps the last answer and never raises."""
        from .foreign_ha import async_other_ha_serves
        from .ha_url import _connection_origin

        try:
            saved = await client.async_get_configured_ha_url()
        except HaPaneldError:
            self.read = True
            return
        origin = _connection_origin(saved) if saved is not None else None
        if origin is not None and origin != self._compared:
            elsewhere = await async_other_ha_serves(self._hass, origin)
            self._compared, self._elsewhere = origin, elsewhere
        if self._connected():
            # The panel connected while it was being asked.
            return
        refusal = status.transport_refusal if status is not None else None
        trying = (
            None
            if origin is None or self._elsewhere or refusal not in _UNREACHED
            else origin
        )
        if trying is not None and trying != self.trying:
            url = URL(trying)
            _LOGGER.warning(
                "%s is signed in but has not connected to Home Assistant: it is "
                "trying %s. Allow the panel to reach %s on TCP port %s",
                panel,
                trying,
                url.host,
                url.port,
            )
        self.trying = trying
        self.read = True

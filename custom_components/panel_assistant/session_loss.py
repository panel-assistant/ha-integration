"""Log a panel's lost session when it outlasts a reconnect, and its return.

A session that drops and comes back within a reconnect is a recovered fault
and logs nothing. One that stays away longer is logged once at WARNING,
naming the panel and why the session ended, and its return at INFO.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .transport_sessions import async_get_sessions

_LOGGER = logging.getLogger(f"{__package__}.coordinator")

# How long a panel's session may stay ended before the loss is logged: the
# app's reconnect backoff ceiling (`PanelAssistantTransportOwner.backoffMaxMs`),
# by which time a panel whose path works has reconnected.
SESSION_LOST_GRACE = timedelta(seconds=60)


class SessionLossLog:
    """Follow one panel's session and log a lasting loss once."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        panel_name: Callable[[], str],
        restarting: Callable[[], bool],
    ) -> None:
        """Watch `entry_id`; `restarting` says Panel Assistant is restarting it."""
        self._hass = hass
        self._entry_id = entry_id
        self._panel_name = panel_name
        self._restarting = restarting
        self._timer: Callable[[], None] | None = None
        # Whether the current loss was logged, so its end is logged too.
        self._logged = False

    @callback
    def changed(self) -> None:
        """Re-answer after the panel's session opened, closed or changed."""
        sessions = async_get_sessions(self._hass)
        if sessions.get(self._entry_id) is not None:
            self.close()
            if self._logged:
                self._logged = False
                _LOGGER.info(
                    "%s restored its connection to Home Assistant", self._panel_name()
                )
            return
        latest = sessions.latest(self._entry_id)
        if (
            latest is None
            or latest.closed_at is None
            or self._timer is not None
            or self._logged
        ):
            return
        self._timer = async_call_later(self._hass, SESSION_LOST_GRACE, self._lost)

    @callback
    def close(self) -> None:
        """Stop waiting: the panel returned or its entry unloads."""
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _lost(self, _now: datetime) -> None:
        self._timer = None
        sessions = async_get_sessions(self._hass)
        notice = sessions.restart_notice(self._entry_id)
        if notice is not None or self._restarting():
            # The panel said it is restarting, or an update Panel Assistant
            # started is restarting it: it has until that bound.
            remaining = (
                max(0.0, notice.deadline - self._hass.loop.time())
                if notice is not None
                else 0.0
            )
            self._timer = async_call_later(
                self._hass, remaining + SESSION_LOST_GRACE.total_seconds(), self._lost
            )
            return
        latest = sessions.latest(self._entry_id)
        if latest is None or latest.closed_at is None:
            return
        self._logged = True
        _LOGGER.warning(
            "%s lost its connection to Home Assistant (%s) and has not "
            "reconnected for %d seconds; its entities stay unavailable until it does",
            self._panel_name(),
            latest.close_reason,
            (dt_util.utcnow() - latest.closed_at).total_seconds(),
        )

"""Installation-wide Core lifecycle and measured return times."""

from __future__ import annotations

import asyncio
from statistics import median
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HassJob, HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

DATA_LIFECYCLE = "lifecycle"
_REASONS = ("restart", "host_reboot", "core_update", "unknown")
_MAX_DURATION_MS = 3_600_000
_MAX_AGE = 30 * 86400


class CoreLifecycle:
    """One bounded Store; shutdown never waits for a panel or network I/O."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.lifecycle")
        self.history: dict[str, list[dict[str, int | float]]] = {}
        self.pending: dict[str, Any] | None = None
        self.reason = "unknown"
        self.phase = "ready" if hass.state is CoreState.running else "starting"
        self.started_at: float | None = None

    async def async_load(self) -> None:
        """Restore only plausible, recent measured samples and a shutdown stamp."""
        raw = await self.store.async_load()
        now = dt_util.utcnow().timestamp()
        if not isinstance(raw, dict):
            return
        history = raw.get("history")
        if isinstance(history, dict):
            for reason in _REASONS:
                samples = history.get(reason)
                if not isinstance(samples, list):
                    continue
                self.history[reason] = [
                    item
                    for item in samples[-8:]
                    if isinstance(item, dict)
                    and type(item.get("duration_ms")) is int
                    and 0 < item["duration_ms"] <= _MAX_DURATION_MS
                    and type(item.get("at")) in (int, float)
                    and 0 <= now - item["at"] <= _MAX_AGE
                ]
        pending = raw.get("pending")
        if (
            isinstance(pending, dict)
            and pending.get("reason") in _REASONS
            and type(pending.get("at")) in (int, float)
            and 0 <= now - pending["at"] <= _MAX_DURATION_MS / 1000
        ):
            self.pending = pending
            self.reason = pending["reason"]
            self.started_at = self.hass.loop.time() - (now - pending["at"])
        # A discarded stamp must not become a future measurement.
        self._save()

    @callback
    def _save(self) -> None:
        self.store.async_delay_save(
            lambda: {"history": self.history, "pending": self.pending}, 30
        )

    @callback
    def snapshot(self) -> dict[str, Any]:
        """Project time at emission; receivers advance it on their own clock."""
        now = dt_util.utcnow().timestamp()
        samples = [
            sample
            for sample in self.history.get(self.reason, [])
            if 0 <= now - sample["at"] <= _MAX_AGE
        ]
        return {
            "phase": self.phase,
            "reason": self.reason,
            "elapsed_ms": (
                max(0, int((self.hass.loop.time() - self.started_at) * 1000))
                if self.started_at is not None
                else None
            ),
            "expected_ms": (
                int(median(sample["duration_ms"] for sample in samples))
                if samples
                else None
            ),
        }

    @callback
    def _broadcast(self) -> None:
        from .transport import async_get_sessions

        async_get_sessions(self.hass).broadcast_lifecycle(self.snapshot())

    @callback
    def _stop_reason(self) -> str:
        # Supervisor's existing coordinator owns refreshing this cache. Never
        # call Supervisor while shutdown is in progress.
        coordinator = self.hass.data.get("hassio_jobs_coordinator")
        if getattr(coordinator, "last_update_success", False) is not True:
            return "unknown"
        jobs = getattr(coordinator, "current_jobs", ())
        for name, reason in (
            ("home_assistant_core_update", "core_update"),
            ("home_assistant_core_restart", "restart"),
        ):
            if any(job.name == name and job.done is False for job in jobs):
                return reason
        return "unknown"

    async def async_shutdown(self) -> None:
        """Enqueue every notice, then give Core's writers one scheduler turn."""
        self.reason = self._stop_reason()
        self.phase = "shutting_down"
        self.started_at = self.hass.loop.time()
        self.pending = {"reason": self.reason, "at": dt_util.utcnow().timestamp()}
        self._broadcast()
        self._save()
        # This is a scheduler yield, not a delivery, flush or acknowledgement wait.
        await asyncio.sleep(0)

    @callback
    def started(self, _event: Event[Any]) -> None:
        """Measure shutdown-to-STARTED once and announce actual Core readiness."""
        now = dt_util.utcnow().timestamp()
        if self.pending is not None:
            duration = int((now - self.pending["at"]) * 1000)
            monotonic_ms = (
                (self.hass.loop.time() - self.started_at) * 1000
                if self.started_at is not None
                else -1
            )
            if (
                0 < duration <= _MAX_DURATION_MS
                and abs(duration - monotonic_ms) <= 5000
            ):
                samples = self.history.setdefault(self.reason, [])
                samples.append({"duration_ms": duration, "at": now})
                del samples[:-8]
        self.pending = None
        self.phase = "ready"
        self._save()
        self._broadcast()


async def async_setup_lifecycle(hass: HomeAssistant) -> None:
    """Load before hello registration; the owner is independent of entries."""
    lifecycle = CoreLifecycle(hass)
    await lifecycle.async_load()
    hass.data[DOMAIN][DATA_LIFECYCLE] = lifecycle
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lifecycle.started)
    hass.async_add_shutdown_job(HassJob(lifecycle.async_shutdown))

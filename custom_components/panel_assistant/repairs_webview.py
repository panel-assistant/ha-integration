"""The WebView leg of an owner-started panel update, with one combined Repair."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from secrets import token_hex

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .client import CannotConnectError, InvalidResponseError
from .const import CONF_TRANSPORT_USER_ID, DOMAIN
from .panel_operations import claim_panel_operation, release_panel_operation
from .status import PanelStatus
from .transport_sessions import (
    PanelSession,
    async_get_sessions,
    async_manage,
    session_manages,
)
from .webview import ACTIVE_STATES, WebViewStatus, parse_webview

_LOGGER = logging.getLogger(__name__)
# How long the first read waits for a restarting app to reconnect, leaving
# the rest of the step's budget for the HTTP fallback.
RECONNECT_WAIT = 90.0

# Retained only to retire issues created by older integration versions.
ISSUE_WEBVIEW = "panel_webview"


def webview_issue_id(entry_id: str) -> str:
    """The retired standalone issue ID, also used during entry cleanup."""
    return f"{ISSUE_WEBVIEW}_{entry_id}"


async def async_reconcile_webview_issue(
    hass: HomeAssistant, entry: ConfigEntry, status: PanelStatus
) -> None:
    """Recover attempted provider work into the combined Repair, without installing.

    An eligible candidate alone is not a job: only installing a new panel app or
    retrying its unfinished bundle authorizes a provider attempt. First-install
    and older integration attempts already have a durable panel transaction.
    """
    # The coordinator imports this module before device helpers are initialised.
    from .repairs_failure import (
        async_failure_events,
        async_record_update_failure,
        panel_failure_issue_id,
    )

    ir.async_delete_issue(hass, DOMAIN, webview_issue_id(entry.entry_id))
    observation = status.webview
    if (
        CONF_TRANSPORT_USER_ID not in entry.data
        or observation is None
        # A running install is not a failure; only an ended one needs the owner.
        or not (
            observation.needs_attention
            or observation.transaction_state == "submitted_unknown"
        )
        or observation.transaction_state in ACTIVE_STATES
        or observation.transaction_state is None
        # Unpublished WebView files: the app update completed with a WARNING.
        or observation.download_unpublished
    ):
        return
    if not claim_panel_operation(hass, entry.entry_id):
        return
    try:
        issue_id = panel_failure_issue_id(f"update:{entry.entry_id}")
        events = await async_failure_events(hass, issue_id)
        previous = next(
            (event for event in reversed(events) if event.get("kind") == "update"), None
        )
        if previous is not None:
            return
        await async_record_update_failure(
            hass,
            entry.entry_id,
            entry.title,
            None,
            HomeAssistantError(
                "The app is installed; its WebView update is unfinished",
                translation_domain=DOMAIN,
                translation_key="update_webview_unfinished",
            ),
            webview_pending=True,
        )
    finally:
        release_panel_operation(hass, entry.entry_id)


async def async_update_webview(
    hass: HomeAssistant, entry_id: str, *, allow_legacy: bool = False
) -> str | None:
    """Observe once, submit an exact eligible pin once, then only observe.

    The caller owns the existing panel-operation claim. A missing WebView status
    is compatible with older apps; known work requires the same paired identity.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return "entry_removed"
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    user_id, did = entry.data.get(CONF_TRANSPORT_USER_ID), entry.unique_id
    if not user_id:
        return None if allow_legacy else "webview_unavailable"
    if coordinator is None:
        return "webview_unavailable"

    def session() -> PanelSession | None:
        current = async_get_sessions(hass).get(entry_id)
        if (
            hass.config_entries.async_get_entry(entry_id) is not entry
            or not user_id
            or entry.data.get(CONF_TRANSPORT_USER_ID) != user_id
            or entry.unique_id != did
            or getattr(getattr(entry, "runtime_data", None), "coordinator", None)
            is not coordinator
            or (
                current is not None
                and (current.did != did or current.user_id != user_id)
            )
        ):
            raise ValueError("panel_unavailable")
        return current if session_manages(current) else None

    async def observe() -> WebViewStatus:
        current = session()
        if current is None:
            raise ConnectionError("panel_reconnecting")
        observed = parse_webview(await async_manage(hass, current, "webview.observe"))
        session()
        return observed

    submitted = False
    try:
        async with asyncio.timeout(120):
            # The successor app owns the profile. Its authenticated native read
            # also works on panels whose HTTP listener is unreachable.
            try:
                # The app just restarted into its new build; give its session
                # time to come back before falling back to HTTP.
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(RECONNECT_WAIT):
                        while session() is None:  # noqa: ASYNC110 - no event to wait on
                            await asyncio.sleep(1)
                status = await observe()
            except (
                HomeAssistantError,
                CannotConnectError,
                InvalidResponseError,
                ConnectionError,
            ):
                if not allow_legacy:
                    raise
                fresh = await coordinator.client.async_get_status()
                session()
                if fresh.webview is None:
                    # Older apps omit the additive contract entirely.
                    return None
                if (
                    not fresh.webview.needs_attention
                    and fresh.webview.transaction_state
                    not in ACTIVE_STATES | {"submitted_unknown"}
                ):
                    return None
                raise
            if (
                status.transaction_state not in ACTIVE_STATES | {"submitted_unknown"}
                and status.supported
                and status.eligible
                and status.candidate_sha256
            ):
                current = session()
                if current is None:
                    raise ConnectionError("panel_reconnecting")
                submitted = True
                status = parse_webview(
                    await async_manage(
                        hass,
                        current,
                        "webview.install",
                        request_id=f"pa-update-{token_hex(16)}",
                        artifact_sha256=status.candidate_sha256,
                    )
                )
                session()
            while status.transaction_state in ACTIVE_STATES:
                await asyncio.sleep(1)
                try:
                    status = await observe()
                except (
                    HomeAssistantError,
                    CannotConnectError,
                    InvalidResponseError,
                    ConnectionError,
                ):
                    session()
                    continue
            if status.download_unpublished:
                # The WebView files are not published for this panel yet; the
                # app update itself is complete.
                _LOGGER.warning(
                    "%s could not download its WebView update %s because the "
                    "files are not published yet; the WebView was not installed",
                    entry.title,
                    status.candidate_sha256,
                )
                return "webview_download_deferred"
            return (
                "webview_deferred"
                if status.transaction_state == "submitted_unknown"
                or status.needs_attention
                else None
            )
    except Exception as err:
        # An accepted request may have lost its answer. The durable panel record
        # owns the outcome; an explicit retry starts with observation, never POST.
        # Before any submission, or once this panel left the entry, nothing is
        # pending that the entry still owns.
        retired = isinstance(err, ValueError) and str(err) == "panel_unavailable"
        return (
            "webview_deferred" if submitted and not retired else "webview_unavailable"
        )

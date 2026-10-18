"""The update's panel calls, carried by the panel's own session.

A panel whose stored address does not answer, but whose session grants
management, is asked the same five things the update asks over HTTP, as
``manage`` requests named after the REST paths they mirror. Large bodies move
through the blob view, opened by the panel. Answers are the HTTP bodies, so the
same parsers and the same refusals apply; only the carrier differs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import client as panel_client
from . import transport_protocol, transport_sessions
from .blob import BLOB_PATH, async_get_blobs
from .client import (
    CannotConnectError,
    HaPaneldError,
    InvalidResponseError,
    StagedApk,
    StagingUnavailableError,
    UpdateApprovalRequiredError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
    parse_staged_apk,
    parse_update_start_response,
)
from .const import MAX_STATUS_RESPONSE_BYTES
from .status import PanelStatus, parse_status_response

APK_TYPE: Final = "application/vnd.android.package-archive"
OP_STATUS: Final = "status"
OP_BACKUP: Final = "backup"
OP_STAGE: Final = "install.apk"
OP_COMMIT: Final = "install.apk.commit"
OP_DISCARD: Final = "install.apk.discard"


class UpdateCarrier(Protocol):
    """The panel calls one update transaction makes, over whichever carrier."""

    async def async_backup_panel(self) -> bytes:
        """Return the panel's own settings backup."""

    async def async_stage_apk(self, apk: bytes) -> StagedApk:
        """Hand the panel app bytes to inspect before anything installs."""

    async def async_commit_apk(self, token: str) -> None:
        """Install exactly the staged upload."""

    async def async_discard_apk(self, token: str) -> None:
        """Delete a staged upload."""

    async def async_get_status(self, *, home_proof: bool = False) -> PanelStatus:
        """Read the panel's status."""


def _body(result: object) -> bytes:
    return json.dumps(result or {}).encode()


def _refusal(result: object, key: str) -> object:
    return result.get(key) if isinstance(result, dict) else None


class SessionCarrier:
    """Ask the entry's current session, whichever session that is at each call.

    The app replaces itself during an update, so the session that staged the
    build is not the one that proves it.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id

    async def _request(
        self, op: str, wait_seconds: float, args: dict[str, Any]
    ) -> transport_sessions.CommandOutcome:
        session = transport_sessions.async_get_sessions(self._hass).get(self._entry_id)
        if session is None or not transport_sessions.session_manages(session):
            raise CannotConnectError
        try:
            outcome = await transport_sessions.async_manage_outcome(
                self._hass, session, op, wait_seconds, args=args
            )
        except HomeAssistantError as err:
            if err.translation_key == transport_protocol.ERR_APPROVAL_PENDING:
                raise UpdateApprovalRequiredError from err
            raise CannotConnectError from err
        if outcome.outcome == transport_protocol.OUTCOME_REFUSED:
            # Each caller maps the HTTP error body it carries. A build that does
            # not know the name answers unknown_command with none, which every
            # caller refuses as a rejection, at once.
            return outcome
        if outcome.outcome != transport_protocol.OUTCOME_APPLIED:
            raise CannotConnectError
        return outcome

    async def async_get_status(self, *, home_proof: bool = False) -> PanelStatus:
        """Read the status body, with the bound HTTP applies before parsing."""
        outcome = await self._request(
            OP_STATUS,
            transport_protocol.MANAGE_TIMEOUT,
            {"home_proof": True} if home_proof else {},
        )
        if outcome.outcome != transport_protocol.OUTCOME_APPLIED:
            raise UpdateRejectedError
        body = json.dumps(outcome.result or {})
        if len(body.encode()) > MAX_STATUS_RESPONSE_BYTES:
            raise InvalidResponseError
        return parse_status_response(body)

    async def async_backup_panel(self) -> bytes:
        """Have the panel upload its backup, and keep it only if it is the one named."""
        blobs = async_get_blobs(self._hass)
        token, received = blobs.upload(self._entry_id, panel_client._MAX_BACKUP_BYTES)
        try:
            outcome = await self._request(
                OP_BACKUP,
                panel_client._BACKUP_TIMEOUT_SECONDS,
                {
                    "allow_plaintext": "1",
                    "include_companion": "false",
                    "blob_token": token,
                },
            )
            if outcome.outcome != transport_protocol.OUTCOME_APPLIED:
                if _refusal(outcome.result, "error") == "busy":
                    raise UpdateBusyError
                raise HaPaneldError
            reference = _refusal(outcome.result, "blob")
            if (
                not isinstance(reference, dict)
                or not received.done()
                or received.cancelled()
                or received.exception() is not None
            ):
                raise HaPaneldError
            data = received.result()
            if (
                reference.get("url") != f"{BLOB_PATH}{token}"
                or reference.get("size") != len(data)
                or reference.get("sha256") != hashlib.sha256(data).hexdigest()
            ):
                raise HaPaneldError
            return data
        finally:
            blobs.discard(token)

    async def async_stage_apk(self, apk: bytes) -> StagedApk:
        """Offer the exact verified bytes for the panel to fetch and inspect."""
        blobs = async_get_blobs(self._hass)
        token, reference = blobs.download(self._entry_id, apk, APK_TYPE)
        try:
            outcome = await self._request(
                OP_STAGE, panel_client._UPLOAD_TIMEOUT_SECONDS, {"blob": reference}
            )
        finally:
            blobs.discard(token)
        if outcome.outcome == transport_protocol.OUTCOME_APPLIED:
            return parse_staged_apk(_body(outcome.result))
        error = _refusal(outcome.result, "error")
        if error == "disabled":
            raise UploadDisabledError
        if error == "upload-busy":
            raise UpdateBusyError
        if error in ("no-root", "stopping"):
            raise StagingUnavailableError
        raise UpdateRejectedError

    async def async_commit_apk(self, token: str) -> None:
        """Install the staged upload, after the panel's approval where it asks."""
        outcome = await self._request(
            OP_COMMIT, transport_protocol.COMMAND_TIMEOUT, {"token": token}
        )
        if outcome.outcome == transport_protocol.OUTCOME_APPLIED:
            parse_update_start_response(_body(outcome.result))
            return
        if _refusal(outcome.result, "status") == "disabled":
            raise UploadDisabledError
        raise UpdateRejectedError

    async def async_discard_apk(self, token: str) -> None:
        """Delete a staged upload that must not be installed."""
        await self._request(
            OP_DISCARD, transport_protocol.COMMAND_TIMEOUT, {"token": token}
        )

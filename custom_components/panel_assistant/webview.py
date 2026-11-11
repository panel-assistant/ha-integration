"""Observe and explicitly request the panel-owned pinned WebView operation.

The signed Android app owns provider selection, download, package admission and
the durable transaction. Home Assistant never supplies a URL or APK trust root.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from typing import Any

from .client import HaPaneldClient, InvalidResponseError

_SHA256 = re.compile(r"[0-9a-f]{64}")
ACTIVE_STATES = frozenset({"downloading", "verified", "installing"})
ATTENTION_STATES = frozenset({"submitted_unknown", "deferred", "rejected"})
TRANSACTION_STATES = ACTIVE_STATES | ATTENTION_STATES | {"installed"}
# The panel's refusals of a new attempt that a Retry cannot change.
FINAL_REFUSALS = frozenset(
    {
        "current",
        "no_candidate",
        "insufficient_storage",
        "no_install_route",
        "unsupported_install_class",
        "unsupported_sdk",
        "unsupported_abi",
        "different_provider",
        "play_managed",
        "signer_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class WebViewStatus:
    """Small additive projection; no provider URL or mutable install authority."""

    supported: bool
    eligible: bool
    reason: str | None
    candidate_sha256: str | None
    transaction_state: str | None
    transaction_reason: str | None
    request_id: str | None
    # Why a download failed (`not_found`, `unresolved`, `unreachable`, ...);
    # None from an app older than 1.0.3, which reported only `download_failed`.
    transaction_cause: str | None = None

    @property
    def refused(self) -> bool:
        """The panel will not start this WebView update now, whatever its owner retries.

        Only a reason the panel decided from facts it read counts: an observation
        it could not make (unknown version or installer, unreadable receipt) may
        change, so it still leaves the owner a Retry.
        """
        return (
            not self.eligible
            and self.reason in FINAL_REFUSALS
            and self.transaction_state not in ACTIVE_STATES
        )

    @property
    def needs_attention(self) -> bool:
        """A known candidate or deferred operation needs its owner's attention."""
        if self.refused:
            return False
        return (
            self.supported
            and self.candidate_sha256 is not None
            and (self.eligible or self.transaction_state in ATTENTION_STATES)
        )

    @property
    def download_failed(self) -> bool:
        """The panel's last WebView attempt ended because the file did not arrive."""
        return (
            self.transaction_state == "deferred"
            and self.transaction_reason == "download_failed"
        )

    @property
    def download_unpublished(self) -> bool:
        """The WebView file is not published yet: the server answered HTTP 404.

        An app older than 1.0.3 names no cause, and is read as before. The app
        update is complete and only the WebView is missing, so this is a WARNING
        log line, never a Repair. Any other download failure is a Repair.
        """
        return self.download_failed and self.transaction_cause in (None, "not_found")

    @property
    def settled(self) -> bool:
        """No WebView work is running or left for the owner to finish."""
        return (
            self.transaction_state not in ACTIVE_STATES
            and (
                self.transaction_state != "submitted_unknown"
                # Only the panel's own current provider proves how it ended.
                or (self.reason == "current" and self.candidate_sha256 is not None)
            )
            and (not self.needs_attention or self.download_unpublished)
        )

    def as_dict(self) -> dict[str, Any]:
        """Return only bounded diagnostic facts."""
        return asdict(self)


WEBVIEW_HOST = "panel-assistant.io"

_CAUSES = {
    "not_found": f"{WEBVIEW_HOST} answered that the file does not exist",
    "unresolved": f"the name {WEBVIEW_HOST} could not be resolved",
    "unreachable": f"{WEBVIEW_HOST} could not be reached",
    "timed_out": f"the download from {WEBVIEW_HOST} timed out",
    "too_large": "the server sent more bytes than the file's size",
    "insecure_redirect": "the server redirected to a plain HTTP address",
    "no_space": "the panel has too little free storage to stage it",
}


def download_cause_text(cause: str | None) -> str:
    """Name a WebView download failure the way a log line reads it."""
    if cause is not None and cause.startswith("http_") and cause[5:].isdigit():
        return f"{WEBVIEW_HOST} answered HTTP {cause[5:]}"
    return _CAUSES.get(cause or "", "the download failed")


def _text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise InvalidResponseError
    return value


def parse_webview(value: object) -> WebViewStatus:
    """Accept the optional v1 contract without keeping arbitrary remote fields."""
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value["schema"] != 1
    ):
        raise InvalidResponseError
    if (
        type(value.get("supported")) is not bool
        or type(value.get("eligible")) is not bool
    ):
        raise InvalidResponseError
    candidate = value.get("candidate")
    digest = None
    if candidate is not None:
        if not isinstance(candidate, dict) or not isinstance(
            candidate.get("sha256"), str
        ):
            raise InvalidResponseError
        digest = candidate["sha256"]
        if _SHA256.fullmatch(digest) is None:
            raise InvalidResponseError
    transaction = value.get("transaction")
    state = reason = request_id = cause = None
    if transaction is not None:
        if (
            not isinstance(transaction, dict)
            or not isinstance(transaction.get("state"), str)
            or transaction.get("state") not in TRANSACTION_STATES
        ):
            raise InvalidResponseError
        state = transaction["state"]
        reason = _text(transaction.get("reason"))
        request_id = _text(transaction.get("request_id"))
        cause = _text(transaction.get("cause"))
    return WebViewStatus(
        value["supported"],
        value["eligible"],
        _text(value.get("reason")),
        digest,
        state,
        reason,
        request_id,
        cause,
    )


async def async_provision_webview(client: HaPaneldClient, request_id: str) -> None:
    """One optional first-install request; lost replies never trigger a replay.

    A persisted transaction belongs to the panel. A resumed installer only reads
    it; a failed/unknown operation waits for an explicit owner-started Repair.
    Unsupported older apps and all optional failures still permit app setup.
    """
    try:
        async with asyncio.timeout(120):
            status = parse_webview(await client.async_get_webview())
            if (
                status.transaction_state is None
                and status.supported
                and status.eligible
                and status.candidate_sha256
            ):
                status = parse_webview(
                    await client.async_install_webview(
                        request_id, status.candidate_sha256
                    )
                )
            while status.transaction_state in ACTIVE_STATES:
                await asyncio.sleep(1)
                try:
                    status = parse_webview(await client.async_get_webview())
                except Exception:
                    # A known active replacement can restart the native HTTP
                    # owner. Keep reading until the existing deadline; never POST.
                    continue
    except Exception:
        # A timeout can follow package submission. The app's durable record, not
        # a second POST or app-job failure, owns the unknown outcome.
        return

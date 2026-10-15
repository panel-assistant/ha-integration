"""One-use byte transfers between Panel Assistant and a panel it cannot reach.

A panel that holds its session but whose address Home Assistant cannot open
moves large bodies itself: it fetches an app Panel Assistant registered, and
uploads a backup Panel Assistant asked for, through the one view here, with its
own Home Assistant credential. Frames on the session are too small for either.

Each transfer is minted per request, belongs to one panel entry, is accepted
only from that entry's bound panel user, works once and expires after ten
minutes. Anything else is answered as if the token did not exist.

The sidebar's browser moves bytes to and from a panel the same way, through
the embed view rather than the view here: such a transfer is bound to the
administrator whose embed session minted it, and only the embed view can use
it (see ``embed.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from aiohttp import web
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.http import HomeAssistantView

from .const import CONF_TRANSPORT_USER_ID, DOMAIN

DATA_BLOBS: Final = "blobs"
BLOB_PATH: Final = "/api/panel_assistant/blob/"
BLOB_LIFETIME_SECONDS: Final = 600.0
# The largest backup the HTTP route accepts from a panel.
MAX_UPLOAD_BYTES: Final = 64 * 1024 * 1024
_CHUNK_BYTES: Final = 64 * 1024

# Read through the module, so a test can move the clock.
_now = time.monotonic


class BlobTransferError(Exception):
    """An upload that started and could not be received whole."""


@dataclass(slots=True)
class _Transfer:
    entry_id: str
    expires: float
    data: bytes | None = None
    received: asyncio.Future[bytes] | None = None
    content_type: str = "application/octet-stream"
    max_bytes: int = 0
    used: bool = field(default=False)
    # Set for a browser's transfer: the administrator it belongs to.
    user_id: str | None = None
    # Forgets the transfer once it expires, whether or not anything else runs.
    sweep: CALLBACK_TYPE | None = None


class BlobRegistry:
    """The transfers Panel Assistant has minted and not yet seen finish."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transfers: dict[str, _Transfer] = {}

    def _mint(self, transfer: _Transfer) -> str:
        token = secrets.token_urlsafe(32)

        @callback
        def _expired(_: datetime) -> None:
            transfer.sweep = None
            if self._transfers.get(token) is transfer:
                self.discard(token)

        self._transfers[token] = transfer
        transfer.sweep = async_call_later(
            self._hass,
            BLOB_LIFETIME_SECONDS,
            HassJob(_expired, cancel_on_shutdown=True),
        )
        return token

    @callback
    def download(
        self,
        entry_id: str,
        data: bytes,
        content_type: str,
        user_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Register bytes the panel may fetch once; return the token and reference.

        With ``user_id``, the bytes are for that administrator's browser.
        """
        token = self._mint(
            _Transfer(
                entry_id=entry_id,
                expires=_now() + BLOB_LIFETIME_SECONDS,
                data=data,
                content_type=content_type,
                user_id=user_id,
            )
        )
        return token, {
            "url": f"{BLOB_PATH}{token}",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "type": content_type,
        }

    @callback
    def upload(
        self,
        entry_id: str,
        max_bytes: int = MAX_UPLOAD_BYTES,
        user_id: str | None = None,
    ) -> tuple[str, asyncio.Future[bytes]]:
        """Open a slot the panel may fill once; the future resolves with its bytes.

        With ``user_id``, the slot is for that administrator's browser.
        """
        received: asyncio.Future[bytes] = self._hass.loop.create_future()
        token = self._mint(
            _Transfer(
                entry_id=entry_id,
                expires=_now() + BLOB_LIFETIME_SECONDS,
                received=received,
                max_bytes=max_bytes,
                user_id=user_id,
            )
        )
        return token, received

    @callback
    def take(self, token: str, entry_id: str, user_id: str) -> bytes | None:
        """Return, once, the bytes a browser uploaded for one entry."""
        transfer = self._transfers.get(token)
        received = transfer.received if transfer is not None else None
        if (
            transfer is None
            or received is None
            or transfer.entry_id != entry_id
            or transfer.user_id != user_id
            or not received.done()
            or received.cancelled()
            or received.exception() is not None
        ):
            return None
        del self._transfers[token]
        # The pending sweep holds the transfer, and with it the bytes, until it fires.
        if transfer.sweep is not None:
            transfer.sweep()
            transfer.sweep = None
        return received.result()

    @callback
    def discard(self, token: str) -> None:
        """Forget a transfer whose caller finished or failed."""
        transfer = self._transfers.pop(token, None)
        if transfer is not None and transfer.sweep is not None:
            transfer.sweep()
            transfer.sweep = None
        received = transfer.received if transfer is not None else None
        if received is None:
            return
        if not received.done():
            received.cancel()
        elif not received.cancelled():
            # Mark a failed upload's error as seen; its caller already gave up.
            received.exception()

    def claim(
        self, token: str, user_id: str, *, upload: bool, entry_id: str | None = None
    ) -> _Transfer | None:
        """Use a live transfer of the right direction, for its own user only.

        Without ``entry_id`` the caller is a panel, and the transfer must be
        its entry's; with it the caller is that entry's sidebar browser, and
        the transfer must be one minted for that browser's administrator.
        """
        transfer = self._transfers.get(token)
        if (
            transfer is None
            or transfer.used
            or transfer.expires <= _now()
            or (transfer.received is not None) != upload
        ):
            return None
        if entry_id is None:
            entry = self._hass.config_entries.async_get_entry(transfer.entry_id)
            if (
                transfer.user_id is not None
                or entry is None
                or entry.data.get(CONF_TRANSPORT_USER_ID) != user_id
            ):
                return None
        elif transfer.user_id != user_id or transfer.entry_id != entry_id:
            return None
        transfer.used = True
        if not upload:
            # A download is sent once from the transfer returned here, so its
            # bytes need not stay behind.
            self.discard(token)
        return transfer


def _not_found() -> web.Response:
    return web.Response(status=404)


def send(transfer: _Transfer) -> web.Response:
    """Answer a claimed download with its bytes."""
    if transfer.data is None:
        return _not_found()
    return web.Response(
        body=transfer.data,
        content_type=transfer.content_type,
        headers={"Cache-Control": "no-store"},
    )


async def receive(request: web.Request, transfer: _Transfer) -> web.Response:
    """Receive one bounded upload into a claimed slot."""
    received = transfer.received
    if received is None:
        return _not_found()
    body = bytearray()
    try:
        # Read in chunks: request.read() is capped by the server's own
        # request size limit, which a panel backup can exceed.
        async for chunk in request.content.iter_chunked(_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > transfer.max_bytes:
                raise BlobTransferError("too large")
    except BlobTransferError as err:
        if not received.done():
            received.set_exception(err)
        return web.Response(status=413)
    except Exception as err:
        if not received.done():
            received.set_exception(BlobTransferError("upload interrupted"))
        raise web.HTTPBadRequest from err
    data = bytes(body)
    if received.cancelled():
        # Its caller gave up while the bytes arrived.
        return _not_found()
    received.set_result(data)
    return web.json_response(
        {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    )


class BlobView(HomeAssistantView):
    """Serve or receive one transfer for the panel that owns it."""

    url = BLOB_PATH + "{token}"
    name = "api:panel_assistant:blob"
    requires_auth = True

    def __init__(self, registry: BlobRegistry) -> None:
        self.registry = registry

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        """Send registered bytes once."""
        transfer = self.registry.claim(token, request["hass_user"].id, upload=False)
        return _not_found() if transfer is None else send(transfer)

    async def put(self, request: web.Request, token: str) -> web.Response:
        """Receive one bounded upload into its waiting slot."""
        transfer = self.registry.claim(token, request["hass_user"].id, upload=True)
        return _not_found() if transfer is None else await receive(request, transfer)


@callback
def async_get_blobs(hass: HomeAssistant) -> BlobRegistry:
    """Return the domain's transfers, registering the view the first time."""
    data = hass.data.setdefault(DOMAIN, {})
    registry: BlobRegistry | None = data.get(DATA_BLOBS)
    if registry is None:
        registry = data[DATA_BLOBS] = BlobRegistry(hass)
        hass.http.register_view(BlobView(registry))
    return registry

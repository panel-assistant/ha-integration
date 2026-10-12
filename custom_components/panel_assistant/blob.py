"""One-use byte transfers between Panel Assistant and a panel it cannot reach.

A panel that holds its session but whose address Home Assistant cannot open
moves large bodies itself: it fetches an app Panel Assistant registered, and
uploads a backup Panel Assistant asked for, through the one view here, with its
own Home Assistant credential. Frames on the session are too small for either.

Each transfer is minted per request, belongs to one panel entry, is accepted
only from that entry's bound panel user, works once and expires after ten
minutes. Anything else is answered as if the token did not exist.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Final

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
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


class BlobRegistry:
    """The transfers Panel Assistant has minted and not yet seen finish."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._transfers: dict[str, _Transfer] = {}

    def _mint(self, transfer: _Transfer) -> str:
        now = _now()
        for token in [t for t, item in self._transfers.items() if item.expires <= now]:
            self.discard(token)
        token = secrets.token_urlsafe(32)
        self._transfers[token] = transfer
        return token

    @callback
    def download(
        self, entry_id: str, data: bytes, content_type: str
    ) -> tuple[str, dict[str, Any]]:
        """Register bytes the panel may fetch once; return the token and reference."""
        token = self._mint(
            _Transfer(
                entry_id=entry_id,
                expires=_now() + BLOB_LIFETIME_SECONDS,
                data=data,
                content_type=content_type,
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
        self, entry_id: str, max_bytes: int = MAX_UPLOAD_BYTES
    ) -> tuple[str, asyncio.Future[bytes]]:
        """Open a slot the panel may fill once; the future resolves with its bytes."""
        received: asyncio.Future[bytes] = self._hass.loop.create_future()
        token = self._mint(
            _Transfer(
                entry_id=entry_id,
                expires=_now() + BLOB_LIFETIME_SECONDS,
                received=received,
                max_bytes=max_bytes,
            )
        )
        return token, received

    @callback
    def discard(self, token: str) -> None:
        """Forget a transfer whose caller finished or failed."""
        transfer = self._transfers.pop(token, None)
        received = transfer.received if transfer is not None else None
        if received is None:
            return
        if not received.done():
            received.cancel()
        elif not received.cancelled():
            # Mark a failed upload's error as seen; its caller already gave up.
            received.exception()

    def claim(self, token: str, user_id: str, *, upload: bool) -> _Transfer | None:
        """Use a live transfer of the right direction, for its panel's user only."""
        transfer = self._transfers.get(token)
        if (
            transfer is None
            or transfer.used
            or transfer.expires <= _now()
            or (transfer.received is not None) != upload
        ):
            return None
        entry = self._hass.config_entries.async_get_entry(transfer.entry_id)
        if entry is None or entry.data.get(CONF_TRANSPORT_USER_ID) != user_id:
            return None
        transfer.used = True
        return transfer


def _not_found() -> web.Response:
    return web.Response(status=404)


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
        if transfer is None or transfer.data is None:
            return _not_found()
        return web.Response(
            body=transfer.data,
            content_type=transfer.content_type,
            headers={"Cache-Control": "no-store"},
        )

    async def put(self, request: web.Request, token: str) -> web.Response:
        """Receive one bounded upload into its waiting slot."""
        transfer = self.registry.claim(token, request["hass_user"].id, upload=True)
        if transfer is None or transfer.received is None:
            return _not_found()
        received = transfer.received
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
        return self.json(
            {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )


@callback
def async_get_blobs(hass: HomeAssistant) -> BlobRegistry:
    """Return the domain's transfers, registering the view the first time."""
    data = hass.data.setdefault(DOMAIN, {})
    registry: BlobRegistry | None = data.get(DATA_BLOBS)
    if registry is None:
        registry = data[DATA_BLOBS] = BlobRegistry(hass)
        hass.http.register_view(BlobView(registry))
    return registry

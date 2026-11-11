"""Home Assistant downloads a panel's WebView update and hands it over its session.

A panel that may reach only Home Assistant cannot download its WebView update
itself. Home Assistant fetches the exact file the panel names, from the one
host that publishes them, checks its size and SHA-256, and offers it as a
one-use blob the panel fetches with its own credential. The panel checks the
same hash against the update compiled into it, so Home Assistant only carries
the bytes; it never chooses them.

Files are kept while a panel uses them and for an hour after, so panels that
update one after another download the file once.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import aiohttp
from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from .blob import async_get_blobs
from .const import DOMAIN
from .webview import WEBVIEW_HOST

_LOGGER = logging.getLogger(__name__)

DATA_SUPPLY: Final = "webview_supply"
APK_TYPE: Final = "application/vnd.android.package-archive"
# The largest WebView file a panel may name; the biggest published is 262 MB.
MAX_WEBVIEW_BYTES: Final = 512 * 1024 * 1024
# Room left on Home Assistant's disk after the file is written.
_FREE_MARGIN_BYTES: Final = 256 * 1024 * 1024
_KEEP_SECONDS: Final = 3600.0
_DOWNLOAD_SECONDS: Final = 1800.0
_CHUNK_BYTES: Final = 1024 * 1024
_DIRECTORY: Final = "panel_assistant_webview"


def webview_url(sha256: str) -> str:
    """The only address a WebView file is fetched from: fixed host, pinned hash."""
    return f"https://{WEBVIEW_HOST}/downloads/webview/{sha256}.apk"


class SupplyError(Exception):
    """Home Assistant could not supply the file; ``cause`` reads like the app's."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


@dataclass(slots=True)
class _File:
    users: int = 0
    ready: asyncio.Future[str] | None = None
    sweep: CALLBACK_TYPE | None = None


@dataclass(slots=True)
class WebViewSupply:
    """The WebView files Home Assistant holds for its panels."""

    hass: HomeAssistant
    directory: str
    files: dict[str, _File] = field(default_factory=dict)
    # One per file: fetching it and removing an expired copy never overlap.
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    swept: bool = False

    @contextlib.asynccontextmanager
    async def async_lease(self, sha256: str, size: int) -> AsyncIterator[str]:
        """Hold the verified file for one panel's update; raise SupplyError."""
        held = self.files.setdefault(sha256, _File())
        held.users += 1
        if held.sweep is not None:
            held.sweep()
            held.sweep = None
        try:
            if held.ready is None or (
                held.ready.done()
                and (held.ready.cancelled() or held.ready.exception() is not None)
            ):
                # The first panel downloads; the others wait for its result.
                held.ready = self.hass.loop.create_future()
                ready = held.ready
                try:
                    async with self.locks.setdefault(sha256, asyncio.Lock()):
                        path = await self._async_fetch(sha256, size)
                    ready.set_result(path)
                except BaseException as err:
                    ready.set_exception(
                        err if isinstance(err, SupplyError) else SupplyError("failed")
                    )
                    ready.exception()
                    raise
            yield await asyncio.shield(held.ready)
        finally:
            held.users -= 1
            if held.users == 0:

                @callback
                def _expired(_now: datetime) -> None:
                    held.sweep = None
                    self.hass.async_create_background_task(
                        self._async_forget(sha256), f"{DOMAIN} forget WebView file"
                    )

                held.sweep = async_call_later(
                    self.hass,
                    _KEEP_SECONDS,
                    HassJob(_expired, cancel_on_shutdown=True),
                )

    async def _async_forget(self, sha256: str) -> None:
        """Remove an unused file; a panel that leases it meanwhile downloads anew."""
        async with self.locks.setdefault(sha256, asyncio.Lock()):
            held = self.files.get(sha256)
            if held is None or held.users:
                return
            del self.files[sha256]
            await self.hass.async_add_executor_job(self._remove, sha256)

    def _path(self, sha256: str) -> str:
        return os.path.join(self.directory, f"{sha256}.apk")

    def _remove(self, sha256: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._path(sha256))

    def _prepare(self, size: int) -> None:
        """Make the private directory, clear a previous run's files, check space."""
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        if not self.swept:
            self.swept = True
            for name in os.listdir(self.directory):
                if name.removesuffix(".part").removesuffix(".apk") not in self.files:
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(self.directory, name))
        if shutil.disk_usage(self.directory).free < size + _FREE_MARGIN_BYTES:
            raise SupplyError("no_space")

    def _matches(self, path: str, sha256: str, size: int) -> bool:
        digest = hashlib.sha256()
        try:
            if os.path.getsize(path) != size:
                return False
            with open(path, "rb") as file:
                while chunk := file.read(_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == sha256

    async def _async_fetch(self, sha256: str, size: int) -> str:
        """Download, check and keep the file; return its path."""
        if not 0 < size <= MAX_WEBVIEW_BYTES:
            raise SupplyError("too_large")
        path = self._path(sha256)
        partial = f"{path}.part"
        try:
            await self.hass.async_add_executor_job(self._prepare, size)
        except OSError as err:
            raise SupplyError("failed") from err
        if await self.hass.async_add_executor_job(self._matches, path, sha256, size):
            return path
        writer = _Writer(partial)
        try:
            await self.hass.async_add_executor_job(writer.open)
            async with asyncio.timeout(_DOWNLOAD_SECONDS):
                await self._async_download(sha256, size, writer)
            await self.hass.async_add_executor_job(writer.close)
            if writer.size != size or writer.digest.hexdigest() != sha256:
                raise SupplyError("mismatch")
            await self.hass.async_add_executor_job(os.replace, partial, path)
        except TimeoutError as err:
            raise SupplyError("timed_out") from err
        except OSError as err:
            raise SupplyError("failed") from err
        finally:
            await self.hass.async_add_executor_job(writer.discard)
        return path

    async def _async_download(self, sha256: str, size: int, writer: _Writer) -> None:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                webview_url(sha256),
                timeout=aiohttp.ClientTimeout(sock_connect=15, sock_read=60),
            ) as response:
                if response.status == 404:
                    raise SupplyError("not_found")
                if response.status != 200:
                    raise SupplyError(f"http_{response.status}")
                declared = response.headers.get(aiohttp.hdrs.CONTENT_LENGTH, "")
                if declared.isdigit() and int(declared) > size:
                    raise SupplyError("too_large")
                buffer = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    buffer.extend(chunk)
                    if writer.size + len(buffer) > size:
                        raise SupplyError("too_large")
                    if len(buffer) >= _CHUNK_BYTES:
                        await self.hass.async_add_executor_job(
                            writer.write, bytes(buffer)
                        )
                        buffer.clear()
                if buffer:
                    await self.hass.async_add_executor_job(writer.write, bytes(buffer))
        except aiohttp.ClientConnectorDNSError as err:
            raise SupplyError("unresolved") from err
        except aiohttp.ClientConnectorError as err:
            raise SupplyError("unreachable") from err
        except (TimeoutError, aiohttp.ServerTimeoutError) as err:
            raise SupplyError("timed_out") from err
        except aiohttp.ClientError as err:
            raise SupplyError("failed") from err


class _Writer:
    """The partial file, hashed as it is written; used only from the executor."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.digest = hashlib.sha256()
        self.size = 0
        self._file: Any = None

    def open(self) -> None:
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._file = os.fdopen(descriptor, "wb")

    def write(self, data: bytes) -> None:
        self._file.write(data)
        self.digest.update(data)
        self.size += len(data)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def discard(self) -> None:
        self.close()
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.path)


@callback
def async_get_webview_supply(hass: HomeAssistant) -> WebViewSupply:
    """Return the domain's WebView files."""
    data = hass.data.setdefault(DOMAIN, {})
    supply: WebViewSupply | None = data.get(DATA_SUPPLY)
    if supply is None:
        # Outside the configuration directory, so a backup taken while a
        # panel updates never carries a 262 MB WebView. One directory per
        # configuration, so a restart finds and clears its own leftovers.
        config = hashlib.sha256(hass.config.config_dir.encode()).hexdigest()[:16]
        supply = data[DATA_SUPPLY] = WebViewSupply(
            hass, os.path.join(tempfile.gettempdir(), f"{_DIRECTORY}-{config}")
        )
    return supply


@contextlib.asynccontextmanager
async def async_offer_webview(
    hass: HomeAssistant, entry_id: str, sha256: str, size: int
) -> AsyncIterator[dict[str, Any]]:
    """Yield the blob reference a panel's ``webview.install`` carries.

    The file stays held until the caller's update step ends; the blob itself
    works once and expires, as every blob does.
    """
    async with async_get_webview_supply(hass).async_lease(sha256, size) as path:
        _token, reference = async_get_blobs(hass).download_file(
            entry_id, path, size, sha256, APK_TYPE
        )
        yield reference

"""Authenticated, bounded delivery of verified browser installation releases."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from ipaddress import IPv4Address, ip_address
from typing import Any

from aiohttp import web
from homeassistant.auth.models import User
from homeassistant.components.http.decorators import require_admin
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView

from .adb_credentials import AdbCredentialError, async_get_adb_credential
from .browser_artifacts import async_read_browser_artifact
from .browser_release_cache import (
    BrowserReleaseCache,
    BrowserReleaseCacheError,
    BrowserReleaseCacheErrorCode,
)
from .build_feed import FeedInstallBundle
from .client import HaPaneldClient, InvalidAddressError, PanelAddress, normalize_address
from .const import DEFAULT_PORT, DOMAIN, SOURCE_USB_INSTALL
from .display_provisioning import async_read_recommended_sizing
from .ha_url import async_offer_ha_url
from .install_network import is_allowed_install_address
from .release import is_feed_build_tag, is_install_release_tag, unique_json_object
from .release_catalog import async_list_install_choices

DATA_BROWSER_DELIVERY = "browser_delivery"
_BODY_LIMIT = 1024
_BODY_TIMEOUT = 5.0
_DOWNLOAD_TIMEOUT = 60.0
_CHUNK_SIZE = 64 * 1024
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_ID = re.compile(r"[0-9a-f]{32}")
# The whole handover answers within this, because the admin's window gives up
# on the request at 35 s and would lose the display size the installer needs.
_HANDOVER_TIMEOUT = 30.0
# The installer relays the handover as soon as it has started the app, which
# may not be answering on its port yet. Keep asking for this long: the last
# read, the address post and the sizing read after it take up to 5 s each.
_ANSWER_WAIT = 10.0
_ANSWER_RETRY = 1.0
# Adding the panel may go on after the handover has answered; this bounds only
# a registration that never ends.
_REGISTRATION_TIMEOUT = 30.0

_LOGGER = logging.getLogger(__name__)


def _error(code: str, status: int) -> web.Response:
    return web.json_response({"error": code}, status=status, headers=_HEADERS)


def _cache_error(error: BrowserReleaseCacheError) -> web.Response:
    status = 404 if error.code == BrowserReleaseCacheErrorCode.NOT_FOUND else 503
    return _error(error.code.value, status)


async def _json_object(request: web.Request) -> dict[str, Any]:
    if (
        request.query
        or request.content_type != "application/json"
        or request.headers.get("Content-Encoding", "identity") != "identity"
    ):
        raise ValueError
    body = bytearray()
    async with asyncio.timeout(_BODY_TIMEOUT):
        async for chunk in request.content.iter_chunked(_BODY_LIMIT + 1):
            body.extend(chunk)
            if len(body) > _BODY_LIMIT:
                raise ValueError
    value = json.loads(body, object_pairs_hook=unique_json_object(ValueError))
    if not isinstance(value, dict):
        raise ValueError
    return value


async def _selection(request: web.Request) -> str | None:
    value = await _json_object(request)
    if set(value) - {"release_candidate"}:
        raise ValueError
    if not value:
        return None
    tag = value["release_candidate"]
    if not (is_install_release_tag(tag) or is_feed_build_tag(tag)):
        raise ValueError
    assert isinstance(tag, str)
    return tag


class BrowserDelivery:
    """Own request admission and drain requests before cache shutdown."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.cache = BrowserReleaseCache(hass, async_get_clientsession(hass))
        self._requests: set[asyncio.Task[Any]] = set()
        self._closed = False

    @asynccontextmanager
    async def async_admit(self) -> AsyncIterator[None]:
        if self._closed:
            raise BrowserReleaseCacheError(BrowserReleaseCacheErrorCode.CLOSED)
        if len(self._requests) >= 2:
            raise BrowserReleaseCacheError(BrowserReleaseCacheErrorCode.BUSY)
        task = asyncio.current_task()
        assert task is not None
        self._requests.add(task)
        try:
            yield
        finally:
            self._requests.discard(task)

    async def async_stop(self, event: Event) -> None:
        """Cancel active work, drain its cleanup, then close artifact custody."""
        self._closed = True
        tasks = tuple(self._requests)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.cache.async_close()


class BrowserReleaseCatalogView(HomeAssistantView):
    """List fixed-repository installation choices for an authenticated admin."""

    url = "/api/panel_assistant/usb/releases"
    name = "api:panel_assistant:usb:releases"
    requires_auth = True

    def __init__(self, service: BrowserDelivery) -> None:
        self.service = service

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        if request.query:
            return _error("browser_release_invalid_request", 400)
        try:
            async with self.service.async_admit():
                releases = await async_list_install_choices(self.service.hass)
                return web.json_response({"releases": releases}, headers=_HEADERS)
        except BrowserReleaseCacheError as error:
            return _cache_error(error)
        except Exception:
            return _error("browser_release_catalog_failed", 503)


class BrowserReleaseView(HomeAssistantView):
    """Prepare a fixed-repository signed release for the authenticated admin."""

    url = "/api/panel_assistant/usb/release"
    name = "api:panel_assistant:usb:release"
    requires_auth = True

    def __init__(self, service: BrowserDelivery) -> None:
        self.service = service

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        user: User = request["hass_user"]
        try:
            async with self.service.async_admit():
                try:
                    tag = await _selection(request)
                except ValueError, TimeoutError, ConnectionError:
                    return _error("browser_release_invalid_request", 400)
                record = await self.service.cache.async_prepare(user.id, rc_tag=tag)
                if isinstance(record.bundle, FeedInstallBundle):
                    return web.json_response(
                        {
                            "id": record.id,
                            "tag": record.bundle.artifact.tag,
                            "feed": base64.b64encode(record.bundle.feed).decode(
                                "ascii"
                            ),
                            "feed_signature": base64.b64encode(
                                record.bundle.feed_signature
                            ).decode("ascii"),
                            "apk_size": record.artifact.size,
                            "apk_sha256": record.artifact.sha256,
                        },
                        headers=_HEADERS,
                    )
                metadata = record.bundle.metadata
                return web.json_response(
                    {
                        "id": record.id,
                        "tag": record.bundle.artifact.tag,
                        "checksum": base64.b64encode(metadata.checksum).decode("ascii"),
                        "checksum_signature": base64.b64encode(
                            metadata.checksum_signature
                        ).decode("ascii"),
                        "descriptor": base64.b64encode(
                            metadata.descriptor_bytes
                        ).decode("ascii"),
                        "descriptor_signature": base64.b64encode(
                            metadata.descriptor_signature
                        ).decode("ascii"),
                        "apk_size": record.artifact.size,
                        "apk_sha256": record.artifact.sha256,
                    },
                    headers=_HEADERS,
                )
        except BrowserReleaseCacheError as error:
            return _cache_error(error)
        except Exception:
            return _error("browser_release_prepare_failed", 503)


class BrowserApkView(HomeAssistantView):
    """Serve only the current admin's opaque, leased browser artifact."""

    url = "/api/panel_assistant/usb/release/{bundle_id}/apk"
    name = "api:panel_assistant:usb:apk"
    requires_auth = True

    def __init__(self, service: BrowserDelivery) -> None:
        self.service = service

    @require_admin
    async def get(self, request: web.Request, bundle_id: str) -> web.StreamResponse:
        user: User = request["hass_user"]
        if request.query or _ID.fullmatch(bundle_id) is None:
            return _error("browser_release_not_found", 404)
        response: web.StreamResponse | None = None
        headers_sent = False
        try:
            async with (
                self.service.async_admit(),
                self.service.cache.async_lease(user.id, bundle_id) as record,
            ):
                async with asyncio.timeout(_DOWNLOAD_TIMEOUT):
                    data = await async_read_browser_artifact(
                        self.service.hass, bundle_id, record.artifact
                    )
                    response = web.StreamResponse(headers=_HEADERS)
                    response.content_type = "application/vnd.android.package-archive"
                    response.content_length = len(data)
                    await response.prepare(request)
                    headers_sent = True
                    for offset in range(0, len(data), _CHUNK_SIZE):
                        await response.write(data[offset : offset + _CHUNK_SIZE])
                    await response.write_eof()
                    return response
        except BrowserReleaseCacheError as error:
            if not headers_sent and (response is None or not response.prepared):
                return _cache_error(error)
        except asyncio.CancelledError:
            if (
                response is not None
                and (headers_sent or response.prepared)
                and request.transport
            ):
                request.transport.close()
            raise
        except Exception:
            if not headers_sent and (response is None or not response.prepared):
                return _error("browser_release_download_failed", 503)
        # Once headers have been sent, truncation signals failure to the client.
        if request.transport:
            request.transport.close()
        assert response is not None
        return response


def _usb_panel_address(value: object) -> PanelAddress:
    """Admit only a home-network IPv4 panel on the app's own port."""
    if not isinstance(value, str):
        raise ValueError
    try:
        address = normalize_address(value)
        host = ip_address(address.host)
    except (InvalidAddressError, ValueError) as error:
        raise ValueError from error
    # A panel on the home network (the installer page's `isPrivate`), never a
    # host this server could be steered to reach.
    if (
        address.port != DEFAULT_PORT
        or not isinstance(host, IPv4Address)
        or not is_allowed_install_address(host)
    ):
        raise ValueError
    return address


async def _async_register_usb_panel(hass: HomeAssistant, address: PanelAddress) -> str:
    """Add the USB-installed panel to Panel Assistant, as a network install does.

    Without its entry the app is refused as an unknown panel until someone adds
    it by hand. Best-effort like the handover: the install already succeeded,
    and the person can still add the panel; the answer is for support logs.
    """
    try:
        async with asyncio.timeout(_REGISTRATION_TIMEOUT):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_USB_INSTALL},
                data={CONF_ADDRESS: address.stored_value},
            )
    except Exception:
        _LOGGER.exception("Could not add the USB-installed panel")
        return "failed"
    if result["type"] is FlowResultType.CREATE_ENTRY:
        if result.get("description") == "another_home_assistant":
            return "added_to_another_home_assistant"
        return "added"
    if result["type"] is FlowResultType.ABORT:
        return str(result.get("reason"))
    return "pending"


class BrowserSetupHandoverView(HomeAssistantView):
    """Tell a USB-installed panel that Home Assistant set it up.

    The installer page reads the panel's address over USB and has no Home
    Assistant credential; this admin's own Home Assistant window relays the
    address here. The handover itself is the one every other install path
    uses, so the panel's wizard skips the same steps whichever way it arrived.
    """

    url = "/api/panel_assistant/usb/handover"
    name = "api:panel_assistant:usb:handover"
    requires_auth = True

    def __init__(self, service: BrowserDelivery) -> None:
        self.service = service

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        try:
            async with self.service.async_admit():
                try:
                    value = await _json_object(request)
                    if set(value) != {"address"}:
                        raise ValueError
                    address = _usb_panel_address(value["address"])
                except ValueError, TimeoutError, ConnectionError:
                    return _error("browser_handover_invalid_request", 400)
                client = HaPaneldClient(
                    async_get_clientsession(self.service.hass), address
                )
                loop = asyncio.get_running_loop()
                started = loop.time()
                deadline = started + _HANDOVER_TIMEOUT
                attempts = 0
                async with asyncio.timeout_at(deadline):
                    while True:
                        attempts += 1
                        outcome = await async_offer_ha_url(self.service.hass, client)
                        if (
                            outcome != "setup_state_unreadable"
                            or loop.time() - started >= _ANSWER_WAIT
                        ):
                            break
                        await asyncio.sleep(_ANSWER_RETRY)
                    # The installer still holds USB: it applies the display and
                    # text size the panel's profile recommends before it opens
                    # the panel's setup, as the network installer does over ADB.
                    # A panel already set up keeps the size its owner chose.
                    sizing = (
                        None
                        if outcome == "setup_complete"
                        else await async_read_recommended_sizing(client)
                    )
                # Adding the panel sets its entry up before the flow returns,
                # which can outlast the handover's answer. It is not stopped
                # then, since that would leave a half-added panel: the answer
                # says it is still going, and the log says how it ended.
                registering = self.service.hass.async_create_task(
                    _async_register_usb_panel(self.service.hass, address),
                    "panel_assistant USB install registration",
                )
                await asyncio.wait(
                    {registering}, timeout=max(0.0, deadline - loop.time())
                )
                if registering.done():
                    registration = registering.result()
                else:
                    registration = "in_progress"

                    def _log_late(task: asyncio.Task[str]) -> None:
                        if not task.cancelled():
                            _LOGGER.info(
                                "USB install registration of %s finished: %s",
                                address.host,
                                task.result(),
                            )

                    registering.add_done_callback(_log_late)
                _LOGGER.info(
                    "USB install handover to %s: %s after %d read(s) in %.1f s; "
                    "registration: %s",
                    address.host,
                    outcome,
                    attempts,
                    loop.time() - started,
                    registration,
                )
                return web.json_response(
                    {
                        "outcome": outcome,
                        "registration": registration,
                        "display": None
                        if sizing is None
                        else {
                            "density": sizing.density,
                            "font_scale": sizing.font_scale,
                        },
                    },
                    headers=_HEADERS,
                )
        except BrowserReleaseCacheError as error:
            return _cache_error(error)
        except Exception:
            return _error("browser_handover_failed", 503)


class BrowserAdbKeyView(HomeAssistantView):
    """Panel Assistant's public ADB key, for the USB installer to offer.

    The installer presents this key at the panel's "Allow USB debugging"
    prompt, so the one approval the owner gives over USB authorizes Panel
    Assistant itself, as approving a network install does. Only the public
    half leaves Home Assistant; the key is created here if it does not exist.
    """

    url = "/api/panel_assistant/usb/adb_key"
    name = "api:panel_assistant:usb:adb_key"
    requires_auth = True

    def __init__(self, service: BrowserDelivery) -> None:
        self.service = service

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        try:
            credential = await async_get_adb_credential(self.service.hass)
        except AdbCredentialError:
            return _error("browser_adb_key_unavailable", 503)
        return web.json_response(
            {"public_key": credential.signer.GetPublicKey()}, headers=_HEADERS
        )


@callback
def async_register_browser_delivery(hass: HomeAssistant) -> None:
    """Register once per domain, without requiring a panel config entry."""
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_BROWSER_DELIVERY in data:
        return
    service = BrowserDelivery(hass)
    hass.http.register_view(BrowserReleaseCatalogView(service))
    hass.http.register_view(BrowserReleaseView(service))
    hass.http.register_view(BrowserApkView(service))
    hass.http.register_view(BrowserSetupHandoverView(service))
    hass.http.register_view(BrowserAdbKeyView(service))
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, service.async_stop)
    data[DATA_BROWSER_DELIVERY] = service

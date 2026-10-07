"""Speech played in step on several panels, over Sendspin.

One Sendspin server serves every panel of this Home Assistant. It lives on Home
Assistant's own http app at ``SENDSPIN_PATH``: it opens no port of its own and
advertises nothing, so no other Sendspin device in the house finds it. Its own
Noise handshake is the authentication, which is why the view needs no Home
Assistant credentials.

A panel that offers ``voice_stream`` in hello sends its Sendspin public key. The
hello reply carries this server's id and a long-term key minted for that panel,
which the panel then dials with: it is admitted as paired without any pairing
step, over the session it already proved. A panel gets the same key on every
hello, so a reply it missed never locks it out; the key belongs to the panel's
config entry and is forgotten when the entry is removed.

Announcements that Home Assistant hands to several panels as separate entity
calls are gathered for ``COALESCE_WINDOW``, fetched and decoded once, and
played to every panel whose Sendspin player is ready as one stream with one
start time. A panel that is not ready gets the announcement by URL as before.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Final

import av
from aiohttp import web
from aiosendspin.noise import (
    Identity,
    InMemoryServerPairingStore,
    ServerPairingRecord,
    b64url_decode,
    b64url_encode,
    generate_psk,
    psk_id_for,
)
from aiosendspin.server import AudioFormat, SendspinClient, SendspinServer
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_VOICE_STREAM: Final = "voice_stream"
STORAGE_KEY: Final = f"{DOMAIN}.voice_stream"
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 1.0

SENDSPIN_PATH: Final = "/api/panel_assistant/sendspin"
SERVER_NAME: Final = "Panel Assistant"
# Speech is mono; the server resamples and encodes for each player's format.
VOICE_FORMAT: Final = AudioFormat(sample_rate=48_000, bit_depth=16, channels=1)
# Audio fed per commit; Sendspin caps a chunk at 150 ms.
SLICE_MS: Final = 100
# At most this much audio is queued ahead of the clock; players hold the rest.
MAX_AHEAD_US: Final = 2_000_000
# Home Assistant starts every entity of one service call together, so the calls
# of one announcement arrive within a few ms of the first. The clip is fetched
# and decoded while the window is open.
COALESCE_WINDOW: Final = 0.15


def decode_clip(data: bytes) -> bytes:
    """Decode audio bytes to ``VOICE_FORMAT`` PCM. Blocking: run in an executor."""
    out = bytearray()
    with av.open(BytesIO(data), "r") as container:
        resampler = av.AudioResampler(
            format="s16", layout="mono", rate=VOICE_FORMAT.sample_rate
        )
        for frame in container.decode(container.streams.audio[0]):
            for res in resampler.resample(frame):
                out += bytes(res.planes[0])[: res.samples * 2]
        for res in resampler.resample(None):
            out += bytes(res.planes[0])[: res.samples * 2]
    return bytes(out)


class _PairingStore(InMemoryServerPairingStore):
    """The server's pairing records, kept in a Home Assistant ``Store``.

    Provisioning is synchronous because the hello reply that carries the key is
    sent synchronously; the base class keeps its records in a plain dict that
    the server's handshake reads.
    """

    def __init__(
        self,
        store: Store[dict[str, Any]],
        server_key: bytes,
        records: Mapping[str, Any],
    ) -> None:
        super().__init__()
        self._ha_store = store
        self._server_key = server_key
        self._records = {
            client_id: ServerPairingRecord.from_dict(record)
            for client_id, record in records.items()
        }

    def data(self) -> dict[str, Any]:
        """Return what is persisted: the server's key and every record."""
        return {
            "server_key": b64url_encode(self._server_key),
            "records": {cid: r.to_dict() for cid, r in self._records.items()},
        }

    async def _save(self) -> None:
        self._schedule_save()

    @callback
    def _schedule_save(self) -> None:
        self._ha_store.async_delay_save(self.data, SAVE_DELAY)

    @callback
    def provision(self, client_id: str, owner: str) -> bytes:
        """Return the panel's long-term key, minting it only the first time."""
        record = self._records.get(client_id)
        if record is None or record.owner != owner:
            psk = generate_psk()
            record = ServerPairingRecord(
                psk_id=psk_id_for(psk),
                psk=psk,
                client_id=client_id,
                pair_methods=[],
                owner=owner,
            )
            self._records[client_id] = record
            self._schedule_save()
        return record.psk

    @callback
    def revoke(self, owner: str) -> list[str]:
        """Forget every record an entry owned; return their client ids."""
        gone = [cid for cid, r in self._records.items() if r.owner == owner]
        for cid in gone:
            del self._records[cid]
        if gone:
            self._schedule_save()
        return gone


class SendspinView(HomeAssistantView):
    """The Sendspin WebSocket; the Noise handshake authenticates, not Home Assistant."""

    url = SENDSPIN_PATH
    name = "api:panel_assistant:sendspin"
    requires_auth = False

    def __init__(self, server: SendspinServer) -> None:
        """Serve this server's client connections."""
        self._server = server

    async def get(self, request: web.Request) -> web.StreamResponse:
        """Hand the connection to the Sendspin server."""
        return await self._server.on_client_connect(request)


@dataclass(slots=True)
class _Batch:
    urls: tuple[str, ...]
    done: asyncio.Future[frozenset[str]]
    members: list[str] = field(default_factory=list)


class VoiceStream:
    """The Sendspin voice server and the batching of announcements."""

    def __init__(
        self, hass: HomeAssistant, server: SendspinServer, pairing: _PairingStore
    ) -> None:
        """Wrap a server whose pairing records live in ``pairing``."""
        self._hass = hass
        self._server = server
        self._pairing = pairing
        self._batches: dict[Hashable, _Batch] = {}
        # One clip at a time per panel: a newer one cuts the older off.
        self._playing: dict[str, asyncio.Task[None]] = {}
        # Held from cutting off what plays to taking ownership, so overlapping
        # starts take turns and the last to take it is what plays.
        self._start_lock = asyncio.Lock()

    @property
    def server(self) -> SendspinServer:
        """Return the Sendspin server."""
        return self._server

    @callback
    def grant(self, client_id: str, owner: str) -> dict[str, str]:
        """Return what a panel's hello reply needs to dial this server."""
        return {
            "path": SENDSPIN_PATH,
            "server_id": self._server.id,
            "psk": b64url_encode(self._pairing.provision(client_id, owner)),
        }

    async def async_revoke(self, owner: str) -> None:
        """Forget a removed entry's panels and drop their connections."""
        for client_id in self._pairing.revoke(owner):
            client = self._server.get_client(client_id)
            if client is not None and client.connection is not None:
                await client.connection.disconnect(retry_connection=False)
            await self._server.remove_client(client_id)

    def ready(self, client_id: str | None) -> bool:
        """Return whether a panel's Sendspin player can play a stream now."""
        if client_id is None:
            return False
        client = self._server.get_client(client_id)
        # The server gives a player role only to a client admitted for
        # playback, which here means one holding the key its hello carried.
        return (
            client is not None
            and client.is_connected
            and client.available
            and bool(client.roles_by_family("player"))
        )

    async def async_announce(
        self, key: Hashable, client_id: str | None, urls: tuple[str, ...]
    ) -> bool:
        """Join the announcement ``key``; return whether this panel streams it.

        A panel that is not ready returns at once, to play by URL. Otherwise it
        waits for the window to close; the stream then starts in the background,
        and the caller tells the panel to play it.
        """
        if client_id is None or not self.ready(client_id):
            return False
        batch = self._batches.get(key)
        if batch is None:
            batch = _Batch(urls, self._hass.loop.create_future())
            self._batches[key] = batch
            self._hass.async_create_background_task(
                self._async_run_batch(key, batch), f"{DOMAIN} voice announcement"
            )
        batch.members.append(client_id)
        return client_id in await asyncio.shield(batch.done)

    async def _async_run_batch(self, key: Hashable, batch: _Batch) -> None:
        streamed: frozenset[str] = frozenset()
        try:
            pcm = self._hass.async_create_task(self._async_prepare(batch.urls))
            await asyncio.sleep(COALESCE_WINDOW)
            # A same-key call after this opens a new batch, out of step but heard.
            self._forget(key, batch)
            streamed = await self._async_start(batch.members, await pcm)
        except Exception:
            _LOGGER.warning("Could not stream an announcement", exc_info=True)
        finally:
            self._forget(key, batch)
            if not batch.done.done():
                batch.done.set_result(streamed)

    def _forget(self, key: Hashable, batch: _Batch) -> None:
        """Unregister ``batch``, leaving a newer batch of the same key open."""
        if self._batches.get(key) is batch:
            del self._batches[key]

    async def async_play_reply(self, client_id: str | None, url: str) -> bool:
        """Stream a voice reply to the answering panel; False to play it by URL."""
        if client_id is None or not self.ready(client_id):
            return False
        try:
            return bool(
                await self._async_start([client_id], await self._async_prepare((url,)))
            )
        except Exception:
            _LOGGER.warning("Could not stream a voice reply", exc_info=True)
            return False

    async def _async_prepare(self, urls: Iterable[str]) -> bytes:
        """Fetch and decode, chime and speech back to back in one run."""
        session = async_get_clientsession(self._hass)
        parts = []
        for url in urls:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.read()
            parts.append(await self._hass.async_add_executor_job(decode_clip, data))
        return b"".join(parts)

    async def _async_start(
        self, client_ids: Iterable[str], pcm: bytes
    ) -> frozenset[str]:
        """Start one stream to the panels that are ready; return which they are."""
        ids = [cid for cid in dict.fromkeys(client_ids) if self.ready(cid)]
        if not ids or not pcm:
            return frozenset()
        async with self._start_lock:
            # Cut off what these panels were playing, and let its cleanup end
            # its stream and group first, or it would stop the new one.
            olds = {t for cid in ids if (t := self._playing.get(cid)) and not t.done()}
            for old in olds:
                old.cancel()
            if olds:
                await asyncio.wait(olds)
            clients = [c for cid in ids if (c := self._server.get_client(cid))]
            task = self._hass.async_create_background_task(
                self._async_stream(clients, pcm), f"{DOMAIN} voice stream"
            )
            for cid in ids:
                self._playing[cid] = task

        def _done(_task: asyncio.Task[None]) -> None:
            for cid in ids:
                if self._playing.get(cid) is task:
                    del self._playing[cid]

        task.add_done_callback(_done)
        return frozenset(ids)

    async def _async_stream(self, clients: list[SendspinClient], pcm: bytes) -> None:
        group = clients[0].group
        for client in clients[1:]:
            await group.add_client(client)
        # A member left in this group by an earlier stream is not ours now.
        for client in list(group.clients):
            if client not in clients:
                await group.remove_client(client)
        stream = group.start_stream()
        step = VOICE_FORMAT.sample_rate * SLICE_MS // 1000 * 2
        try:
            start_us: int | None = None
            for offset in range(0, len(pcm), step):
                stream.prepare_audio(pcm[offset : offset + step], VOICE_FORMAT)
                at = await stream.commit_audio()
                if start_us is None:
                    start_us = at  # one start time for the whole group
                await stream.sleep_to_limit_buffer(MAX_AHEAD_US)
            end_us = (start_us or 0) + len(pcm) // 2 * 1_000_000 // (
                VOICE_FORMAT.sample_rate
            )
            # stream/end makes players clear their buffers, so wait on the
            # server's clock until the last sample has played.
            await asyncio.sleep(max(0, end_us - stream.now_us()) / 1e6)
        except Exception:
            _LOGGER.warning("A voice stream stopped early", exc_info=True)
        finally:
            stream.stop()  # stream/end, at once when cut off
            await group.stop()

    async def async_stop(self, _event: Event | None = None) -> None:
        """Stop every stream and close every connection."""
        for task in list(self._playing.values()):
            task.cancel()
        await self._server.close()


async def async_setup_voice_stream(hass: HomeAssistant) -> None:
    """Start the one Sendspin server and serve it on Home Assistant's http app."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, STORAGE_KEY, private=True
    )
    data = await store.async_load() or {}
    if "server_key" in data:
        identity = Identity.from_private_bytes(b64url_decode(data["server_key"]))
        pairing = _PairingStore(store, identity.private_bytes, data.get("records", {}))
    else:
        identity = Identity.generate()
        pairing = _PairingStore(store, identity.private_bytes, {})
        # Now, not later: the server's id is what every panel verifies.
        await store.async_save(pairing.data())
    server = SendspinServer(
        hass.loop,
        identity,
        SERVER_NAME,
        async_get_clientsession(hass),
        pairing_store=pairing,
        allow_unencrypted=False,
        allow_noncompliant_clients=False,
    )
    voice = VoiceStream(hass, server, pairing)
    hass.data.setdefault(DOMAIN, {})[DATA_VOICE_STREAM] = voice
    hass.http.register_view(SendspinView(server))
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, voice.async_stop)


def async_get_voice_stream(hass: HomeAssistant) -> VoiceStream | None:
    """Return the voice server, once it has started."""
    voice: VoiceStream | None = hass.data.get(DOMAIN, {}).get(DATA_VOICE_STREAM)
    return voice

"""Speech played in step on several panels, over Sendspin inside each session.

One Sendspin server serves every panel of this Home Assistant. It has no
listener of its own and advertises nothing: each panel granted
``voice_stream_session`` gets a Sendspin connection carried inside its Panel
Assistant session. What the server writes reaches the panel as ``sendspin``
events on its hello subscription, base64 because Home Assistant writes every
message to a client as text; what the panel writes arrives as
``voice_stream_frame`` commands, in order. Sendspin's own Noise handshake runs
inside that connection with the key the hello reply carried, so the panel is
admitted as paired without any pairing step. A panel gets the same key on every
hello; the key belongs to the panel's config entry and is forgotten when the
entry is removed.

Panel Assistant decides what each panel plays. Every stream it starts has a
``stream_id``. The message that tells a panel to play names that id and is sent
before the stream's first frame on that session, every frame sent while the
stream is the panel's carries the id, and ``voice_stream_end`` closes it:
``played`` at drain, ``preempted`` when something newer replaces it or the
panel asks to stop, ``failed`` otherwise. Panel Assistant finishes the
announcement or reply itself at that point.

Announcements that Home Assistant hands to several panels as separate entity
calls are gathered for ``COALESCE_WINDOW``, fetched and decoded once at their
own rate, and played to every panel whose Sendspin player is ready as one
stream. A panel that is not ready gets the announcement by URL as before.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from collections.abc import Callable, Hashable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Final, cast

import av
from aiohttp import ClientWebSocketResponse, WSMessage, WSMsgType
from aiosendspin.noise import (
    Identity,
    InMemoryServerPairingStore,
    ServerPairingRecord,
    b64url_decode,
    b64url_encode,
    generate_psk,
    psk_id_for,
)
from aiosendspin.server import AudioFormat, SendspinServer
from aiosendspin.server.connection import SendspinConnection
from aiosendspin.server.group import SendspinGroup
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_VOICE_STREAM: Final = "voice_stream"
STORAGE_KEY: Final = f"{DOMAIN}.voice_stream"
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 1.0

SERVER_NAME: Final = "Panel Assistant"
EVENT_SENDSPIN: Final = "sendspin"
EVENT_VOICE_STREAM_END: Final = "voice_stream_end"
OUTCOME_PLAYED: Final = "played"
OUTCOME_PREEMPTED: Final = "preempted"
OUTCOME_FAILED: Final = "failed"
# Audio fed per commit; Sendspin caps a chunk at 150 ms.
SLICE_MS: Final = 100
# At most this much audio is queued ahead of the clock; players hold the rest.
MAX_AHEAD_US: Final = 2_000_000
# Home Assistant starts every entity of one service call together, so the calls
# of one announcement arrive within a few ms of the first. The clip is fetched
# and decoded while the window is open.
COALESCE_WINDOW: Final = 0.15
# Frames from a panel waiting for the server. A frame is never dropped, since
# Noise counts them: a panel that outruns this loses its Sendspin connection.
MAX_INBOX: Final = 256
# How long the end of a stream may take to reach the session before the panel
# is told it ended anyway.
DRAIN_TIMEOUT: Final = 2.0
# Used when a clip does not state its rate.
FALLBACK_RATE: Final = 48_000


def decode_clips(clips: Iterable[bytes]) -> tuple[bytes, int]:
    """Decode clips back to back to mono PCM; return it and its rate.

    Each clip keeps its own rate, or the lowest of them all when they differ,
    so nothing is upsampled: the server converts for each player. Blocking:
    run in an executor.
    """
    clips = list(clips)
    rates = []
    for data in clips:
        with av.open(BytesIO(data), "r") as container:
            rates.append(container.streams.audio[0].rate or FALLBACK_RATE)
    rate = min(rates, default=FALLBACK_RATE)
    out = bytearray()
    for data in clips:
        with av.open(BytesIO(data), "r") as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            for frame in container.decode(container.streams.audio[0]):
                for res in resampler.resample(frame):
                    out += bytes(res.planes[0])[: res.samples * 2]
            for res in resampler.resample(None):
                out += bytes(res.planes[0])[: res.samples * 2]
    return bytes(out), rate


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


class SessionSocket:
    """A panel's Sendspin connection, carried by its Panel Assistant session.

    The Sendspin server takes it for a WebSocket: what it sends becomes a
    ``sendspin`` event, and what the panel sends is fed in by the session.
    """

    def __init__(self, client_id: str, send: Callable[[dict[str, Any]], None]) -> None:
        """Carry ``client_id``'s connection; ``send`` sends one session event."""
        self.client_id = client_id
        self._send = send
        self._inbox: asyncio.Queue[WSMessage] = asyncio.Queue(MAX_INBOX)
        self._closed = False
        # The stream this panel was told to play, while it is its own.
        self.stream_id: str | None = None

    @property
    def closed(self) -> bool:
        """Return whether the connection has ended."""
        return self._closed

    @property
    def close_code(self) -> int | None:
        """Return the close code: normal once closed."""
        return 1000 if self._closed else None

    def exception(self) -> BaseException | None:
        """Return no exception: the session ends a connection cleanly."""
        return None

    @callback
    def send_event(self, event: dict[str, Any]) -> None:
        """Send one event on the panel's session."""
        self._send(event)

    async def send_str(self, data: str) -> None:
        """Send a Sendspin text message."""
        self._frame(data.encode(), text=True)

    async def send_bytes(self, data: bytes) -> None:
        """Send a Sendspin binary message."""
        self._frame(data, text=False)

    def _frame(self, data: bytes, *, text: bool) -> None:
        if self._closed:
            raise ConnectionResetError("The panel's session has ended")
        self._send(
            {
                "kind": EVENT_SENDSPIN,
                "frame": base64.b64encode(data).decode(),
                "text": text,
                "stream_id": self.stream_id,
            }
        )

    @callback
    def feed(self, data: bytes, *, text: bool) -> None:
        """Hand the server one message from the panel, in session order."""
        if self._closed:
            return
        message = (
            WSMessage(WSMsgType.TEXT, data.decode(), "")
            if text
            else WSMessage(WSMsgType.BINARY, data, "")
        )
        try:
            self._inbox.put_nowait(message)
        except asyncio.QueueFull:
            _LOGGER.warning("A panel's Sendspin frames are not being read; closing")
            self.close_now()

    async def receive(self) -> WSMessage:
        """Return the panel's next message, or a close once the session ends."""
        if self._closed and self._inbox.empty():
            return WSMessage(WSMsgType.CLOSED, None, "")
        return await self._inbox.get()

    async def close(self) -> bool:
        """End the connection; the session itself stays."""
        was_open = not self._closed
        self.close_now()
        return was_open

    @callback
    def close_now(self) -> None:
        """End the connection now, waking a pending receive."""
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._inbox.put_nowait(WSMessage(WSMsgType.CLOSED, None, ""))
            except asyncio.QueueFull:
                self._inbox.get_nowait()
            else:
                return


@dataclass(slots=True)
class Listener:
    """One panel's part in an announcement or a reply.

    ``play`` sends the message that tells the panel to play the stream it
    names, and returns whether it was sent. ``ended`` hears the stream's
    outcome once it has ended for this panel.
    """

    client_id: str | None
    play: Callable[[str], bool]
    listen_after: bool = False
    ended: Callable[[str], None] | None = None


@dataclass(slots=True)
class _Member:
    listener: Listener
    socket: SessionSocket


@dataclass(slots=True, eq=False)
class _Stream:
    stream_id: str
    members: dict[str, _Member] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    group: SendspinGroup | None = None
    # What the panels still in it are told if it is cut off.
    outcome: str = OUTCOME_PREEMPTED


@dataclass(slots=True)
class _Batch:
    urls: tuple[str, ...]
    done: asyncio.Future[set[str]]
    members: list[Listener] = field(default_factory=list)


class VoiceStream:
    """The Sendspin voice server, its session connections and its streams."""

    def __init__(
        self, hass: HomeAssistant, server: SendspinServer, pairing: _PairingStore
    ) -> None:
        """Wrap a server whose pairing records live in ``pairing``."""
        self._hass = hass
        self._server = server
        self._pairing = pairing
        self._batches: dict[Hashable, _Batch] = {}
        # Each session's connection, and each panel's latest one.
        self._by_token: dict[str, SessionSocket] = {}
        self._by_client: dict[str, SessionSocket] = {}
        self._links: set[asyncio.Task[None]] = set()
        # One stream at a time per panel: a newer one replaces the older.
        self._playing: dict[str, _Stream] = {}
        # Held while panels change streams, so overlapping starts take turns
        # and the last to take it is what plays.
        self._start_lock = asyncio.Lock()

    @property
    def server(self) -> SendspinServer:
        """Return the Sendspin server."""
        return self._server

    @callback
    def grant(self, client_id: str, owner: str) -> dict[str, str]:
        """Return what a panel's hello reply needs to connect inside its session."""
        return {
            "server_id": self._server.id,
            "psk": b64url_encode(self._pairing.provision(client_id, owner)),
        }

    @callback
    def attach(
        self, token: str, client_id: str, send: Callable[[dict[str, Any]], None]
    ) -> None:
        """Open the Sendspin connection a granted session carries."""
        self.detach(token)
        socket = SessionSocket(client_id, send)
        self._by_token[token] = socket
        self._by_client[client_id] = socket
        self._link(socket)

    def _link(self, socket: SessionSocket) -> None:
        # The server-dialled form takes any socket; nothing is dialled here.
        connection = SendspinConnection(
            self._server, wsock_client=cast(ClientWebSocketResponse, socket)
        )
        task = self._hass.async_create_background_task(
            connection.handle_client(), f"{DOMAIN} voice stream connection"
        )
        self._links.add(task)
        task.add_done_callback(self._links.discard)

    @callback
    def detach(self, token: str) -> None:
        """Close the connection a session carried, as the session ends."""
        socket = self._by_token.pop(token, None)
        if socket is None:
            return
        socket.close_now()
        if self._by_client.get(socket.client_id) is socket:
            del self._by_client[socket.client_id]

    @callback
    def feed(self, token: str, data: bytes, *, text: bool) -> None:
        """Hand the server a frame a session carried from its panel."""
        socket = self._by_token.get(token)
        if socket is None:
            return
        if socket.closed:
            # The server ended the last connection; the panel is dialling again.
            fresh = SessionSocket(socket.client_id, socket.send_event)
            self._by_token[token] = fresh
            if self._by_client.get(socket.client_id) is socket:
                self._by_client[socket.client_id] = fresh
            self._link(fresh)
            socket = fresh
        socket.feed(data, text=text)

    async def async_stop_stream(self, token: str, stream_id: str) -> None:
        """Take a session's panel off the stream it names, at its own request."""
        socket = self._by_token.get(token)
        if socket is None:
            return
        async with self._start_lock:
            stream = self._playing.get(socket.client_id)
            if stream is not None and stream.stream_id == stream_id:
                await self._async_leave(stream, socket.client_id, OUTCOME_PREEMPTED)

    async def async_revoke(self, owner: str) -> None:
        """Forget a removed entry's panels and drop their connections."""
        for client_id in self._pairing.revoke(owner):
            client = self._server.get_client(client_id)
            if client is not None and client.connection is not None:
                await client.connection.disconnect(retry_connection=False)
            await self._server.remove_client(client_id)

    def ready(self, client_id: str | None) -> bool:
        """Return whether a panel's Sendspin player can play a stream now."""
        if client_id is None or client_id not in self._by_client:
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
        self, key: Hashable, listener: Listener, urls: tuple[str, ...]
    ) -> bool:
        """Join the announcement ``key``; return whether the panel streams it.

        A panel that is not ready returns False at once, to play by URL.
        Otherwise it waits for the window to close; if the panel streams,
        ``listener.play`` has been called by the time this returns.
        """
        if not self.ready(listener.client_id):
            return False
        batch = self._batches.get(key)
        if batch is None:
            batch = _Batch(urls, self._hass.loop.create_future())
            self._batches[key] = batch
            self._hass.async_create_background_task(
                self._async_run_batch(key, batch), f"{DOMAIN} voice announcement"
            )
        batch.members.append(listener)
        return listener.client_id in await asyncio.shield(batch.done)

    async def _async_run_batch(self, key: Hashable, batch: _Batch) -> None:
        streamed: set[str] = set()
        try:
            prepared = self._hass.async_create_task(self._async_prepare(batch.urls))
            await asyncio.sleep(COALESCE_WINDOW)
            # A same-key call after this opens a new batch, out of step but heard.
            self._forget(key, batch)
            streamed = await self._async_start(batch.members, *await prepared)
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

    async def async_play_reply(self, listener: Listener, url: str) -> bool:
        """Stream a voice reply to the answering panel; False means by URL."""
        if not self.ready(listener.client_id):
            return False
        try:
            streamed = await self._async_start(
                [listener], *await self._async_prepare((url,))
            )
        except Exception:
            _LOGGER.warning("Could not stream a voice reply", exc_info=True)
            return False
        return listener.client_id in streamed

    async def _async_prepare(self, urls: Iterable[str]) -> tuple[bytes, int]:
        """Fetch and decode, chime and speech back to back in one run."""
        session = async_get_clientsession(self._hass)
        clips = []
        for url in urls:
            async with session.get(url) as response:
                response.raise_for_status()
                clips.append(await response.read())
        return await self._hass.async_add_executor_job(decode_clips, clips)

    async def _async_start(
        self, listeners: Iterable[Listener], pcm: bytes, rate: int
    ) -> set[str]:
        """Start one stream to the listeners whose panels are ready.

        Each is told to play before the stream sends anything. Return the
        client ids of the panels it plays to.
        """
        listeners = [
            listener
            for listener in listeners
            if listener.client_id is not None and self.ready(listener.client_id)
        ]
        if not listeners or not pcm:
            return set()
        async with self._start_lock:
            # Take these panels off what they were playing first, so its end
            # reaches each of them before the new stream does.
            for listener in listeners:
                assert listener.client_id is not None
                if (old := self._playing.get(listener.client_id)) is not None:
                    await self._async_leave(old, listener.client_id, OUTCOME_PREEMPTED)
            stream = _Stream(secrets.token_urlsafe(12))
            for listener in listeners:
                cid = listener.client_id
                assert cid is not None
                socket = self._by_client.get(cid)
                if (
                    cid in stream.members
                    or socket is None
                    or not self.ready(cid)
                    or not listener.play(stream.stream_id)
                ):
                    continue
                socket.stream_id = stream.stream_id
                stream.members[cid] = _Member(listener, socket)
                self._playing[cid] = stream
            if not stream.members:
                return set()
            members = set(stream.members)
            started: asyncio.Future[None] = self._hass.loop.create_future()
            stream.task = self._hass.async_create_background_task(
                self._async_stream(stream, pcm, rate, started),
                f"{DOMAIN} voice stream",
            )
            # Until it has started, under the lock, so nothing changes its
            # panels while it is forming its group.
            await asyncio.wait(
                {started, stream.task}, return_when=asyncio.FIRST_COMPLETED
            )
        return members

    async def _async_stream(
        self,
        stream: _Stream,
        pcm: bytes,
        rate: int,
        started: asyncio.Future[None],
    ) -> None:
        """Play ``pcm`` to the stream's panels, then tell each how it ended."""
        outcome = OUTCOME_PLAYED
        group: SendspinGroup | None = None
        try:
            clients = [
                client
                for cid in stream.members
                if (client := self._server.get_client(cid)) is not None
            ]
            if not clients:
                raise RuntimeError("no panel of the stream is connected")
            group = stream.group = clients[0].group
            for client in clients[1:]:
                await group.add_client(client)
            # A member left in this group by an earlier stream is not ours now.
            for client in list(group.clients):
                if client not in clients:
                    await group.remove_client(client)
            push = group.start_stream()
            fmt = AudioFormat(sample_rate=rate, bit_depth=16, channels=1)
            step = rate * SLICE_MS // 1000 * 2
            start_us: int | None = None
            for offset in range(0, len(pcm), step):
                push.prepare_audio(pcm[offset : offset + step], fmt)
                at = await push.commit_audio()
                if start_us is None:
                    start_us = at
                    started.set_result(None)
                await push.sleep_to_limit_buffer(MAX_AHEAD_US)
            end_us = (start_us or 0) + len(pcm) // 2 * 1_000_000 // rate
            # stream/end makes players clear their buffers, so wait on the
            # server's clock until the last sample has played.
            await asyncio.sleep(max(0, end_us - push.now_us()) / 1e6)
        except asyncio.CancelledError:
            outcome = stream.outcome
        except Exception:
            _LOGGER.warning("A voice stream stopped early", exc_info=True)
            outcome = OUTCOME_FAILED
        finally:
            if group is not None:
                await group.stop()  # stream/end, at once when cut off
            for cid in list(stream.members):
                await self._async_end(stream, cid, outcome)

    async def _async_leave(self, stream: _Stream, cid: str, outcome: str) -> None:
        """Take one panel off a stream, ending it for that panel alone."""
        if cid not in stream.members:
            return
        task = stream.task
        if len(stream.members) == 1 and task is not None and not task.done():
            stream.outcome = outcome
            task.cancel()
            await asyncio.wait({task})
            return
        client = self._server.get_client(cid)
        if stream.group is not None and client is not None:
            await stream.group.remove_client(client)  # stream/end to it alone
        await self._async_end(stream, cid, outcome)

    async def _async_end(self, stream: _Stream, cid: str, outcome: str) -> None:
        """Tell one panel its stream ended, once the stream's end has reached it."""
        member = stream.members.pop(cid, None)
        if member is None:
            return
        if self._playing.get(cid) is stream:
            del self._playing[cid]
        if (client := self._server.get_client(cid)) is not None:
            with suppress(TimeoutError):
                async with asyncio.timeout(DRAIN_TIMEOUT):
                    await client.wait_role_drained("player")
        socket = member.socket
        if socket.closed:
            outcome = OUTCOME_FAILED
        elif socket.stream_id == stream.stream_id:
            socket.send_event(
                {
                    "kind": EVENT_VOICE_STREAM_END,
                    "stream_id": stream.stream_id,
                    "outcome": outcome,
                    "listen_after": member.listener.listen_after,
                }
            )
            socket.stream_id = None
        if member.listener.ended is not None:
            member.listener.ended(outcome)

    async def async_stop(self, _event: Event | None = None) -> None:
        """Stop every stream and close every connection."""
        for stream in set(self._playing.values()):
            if stream.task is not None:
                stream.task.cancel()
        for token in list(self._by_token):
            self.detach(token)
        await self._server.close()


async def async_setup_voice_stream(hass: HomeAssistant) -> None:
    """Start the one Sendspin server; sessions carry its connections."""
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
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, voice.async_stop)


def async_get_voice_stream(hass: HomeAssistant) -> VoiceStream | None:
    """Return the voice server, once it has started."""
    voice: VoiceStream | None = hass.data.get(DOMAIN, {}).get(DATA_VOICE_STREAM)
    return voice

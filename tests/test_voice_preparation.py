"""Oversized or stalled speech uses the existing URL playback and completion path."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant import voice_stream as voice

from .test_transport import OTHER_DID, WsClientFactory
from .test_voice import _satellite, entry  # noqa: F401
from .test_voice_stream import (
    CLIPS,
    LINGERING,
    Speaker,
    Wire,
    _announce,
    _ClipView,
    _event,
    _fetched,
    _pcm,
    _played,
    _second_entry,
    _voice_panel,
    base,  # noqa: F401
    wires,  # noqa: F401
)


@LINGERING
@pytest.mark.parametrize("budget", ["encoded", "decoded"])
async def test_grouped_clips_share_one_preparation_budget_and_fall_back(
    budget: str,
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    hass_read_only_user: Any,
    entry: MockConfigEntry,  # noqa: F811
    base: str,  # noqa: F811
    wires: list[tuple[Wire, Speaker]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two individually small clips overflow together; both panels still finish."""
    sizes = [
        len(CLIPS[name]) if budget == "encoded" else len(_pcm(name))
        for name in ("chime", "speech")
    ]
    limit = max(sizes) + 1
    assert max(sizes) < limit < sum(sizes)
    key = "MAX_ENCODED_BYTES" if budget == "encoded" else "MAX_PCM_BYTES"
    monkeypatch.setattr(voice, key, limit, raising=False)
    other = await _second_entry(hass, hass_read_only_user.id)
    panel_a, wire_a, _ = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    panel_b, wire_b, _ = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires, did=OTHER_DID
    )
    call = _announce(
        hass,
        [_satellite(hass, entry), _satellite(hass, other)],
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce_media_id=f"{base}/test_voice_stream/chime",
    )
    try:
        a, b = await _event(panel_a), await _event(panel_b)
        assert not a.get("stream") and not b.get("stream")
        assert a["url"] == b["url"] == f"{base}/test_voice_stream/speech"
        assert "/test_voice_stream/chime" in a["preannounce_url"]
        assert sorted(_fetched(hass)) == ["chime", "speech"]
        assert not call.done()
        await _played(panel_a, a["announce_id"])
        await _played(panel_b, b["announce_id"])
        async with asyncio.timeout(5):
            await call
        assert wire_a.ends == wire_b.ends == []
    finally:
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)


@LINGERING
async def test_stalled_preparation_falls_back_and_next_announcement_streams(
    hass: HomeAssistant,
    hass_ws_client: WsClientFactory,
    hass_read_only_access_token: str,
    entry: MockConfigEntry,  # noqa: F811
    base: str,  # noqa: F811
    wires: list[tuple[Wire, Speaker]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    view: _ClipView = hass.data[_ClipView.name]
    release = asyncio.Event()
    view.held["speech"] = release
    monkeypatch.setattr(voice, "PREPARATION_TIMEOUT", 0.05, raising=False)
    call = _announce(
        hass,
        _satellite(hass, entry),
        base,
        media_id=f"{base}/test_voice_stream/speech",
        preannounce=False,
    )
    try:
        async with asyncio.timeout(2):
            event = await _event(panel)
        assert not event.get("stream")
        assert event["url"] == f"{base}/test_voice_stream/speech"
        await _played(panel, event["announce_id"])
        await call
        assert wire.ends == []
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
    monkeypatch.setattr(voice, "PREPARATION_TIMEOUT", 15.0)
    following = _announce(
        hass,
        _satellite(hass, entry),
        base,
        media_id=f"{base}/test_voice_stream/chime",
        preannounce=False,
    )
    assert (await _event(panel))["stream"] is True
    async with asyncio.timeout(5):
        await following
    await speaker.ended(1)
    assert wire.ends[-1]["outcome"] == "played"


def test_decoder_refuses_large_output_before_accessing_its_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single oversized output cannot allocate a second unbounded PCM buffer."""

    class Frame:
        samples = voice.MAX_PCM_BYTES // 2 + 1

        @property
        def planes(self) -> None:
            pytest.fail("oversized output plane was materialized")

    class Container:
        streams = SimpleNamespace(audio=[SimpleNamespace(rate=48_000)])

        def __enter__(self) -> Container:
            return self

        def __exit__(self, *_args: Any) -> None:
            pass

        def decode(self, _stream: Any) -> list[Frame]:
            return [Frame()]

    monkeypatch.setattr(voice.av, "open", lambda *_a, **_k: Container())
    monkeypatch.setattr(
        voice.av,
        "AudioResampler",
        lambda **_k: SimpleNamespace(
            resample=lambda frame: [] if frame is None else [frame]
        ),
    )
    with pytest.raises(ValueError, match="PCM"):
        voice.decode_clips([b"encoded"])


def test_executor_decoder_observes_the_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expiry inside the decoder stops work even after its async waiter leaves."""
    clock = iter([0.0, 0.0, 0.0, 0.0, 2.0])
    monkeypatch.setattr(voice, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError, match="preparation"):
        voice.decode_clips([CLIPS["speech"]], deadline=1.0)

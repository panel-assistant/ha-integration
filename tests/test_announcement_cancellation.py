import asyncio
from unittest.mock import patch

import pytest
from homeassistant.components.assist_satellite.entity import AssistSatelliteEntity
from homeassistant.core import Context
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script

from custom_components.panel_assistant.voice import satellites
from custom_components.panel_assistant.voice_stream import async_get_voice_stream
from tests.test_voice import _satellite, entry  # noqa: F401
from tests.test_voice_stream import (
    LINGERING,
    _event,
    _voice_panel,
    base,  # noqa: F401
    wires,  # noqa: F401
)


@LINGERING
@pytest.mark.parametrize("phase", ["before_pa", "during_prepare", "normal"])
async def test_script_stop_ownership(
    hass,
    hass_ws_client,
    hass_read_only_access_token,
    entry,  # noqa: F811
    base,  # noqa: F811
    wires,  # noqa: F811
    phase,
):
    panel, wire, speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    sat = satellites(hass)[entry.entry_id]
    stream = async_get_voice_stream(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    original_prepare = stream._async_prepare
    original_resolve = AssistSatelliteEntity._resolve_announcement_media_id

    async def preparing(urls):
        entered.set()
        if phase == "during_prepare":
            await release.wait()
        return await original_prepare(urls)

    async def resolving(entity, *args, **kw):
        if phase == "before_pa":
            entered.set()
            await release.wait()
        return await original_resolve(entity, *args, **kw)

    script = Script(
        hass,
        cv.SCRIPT_SCHEMA(
            [
                {
                    "service": "assist_satellite.announce",
                    "target": {"entity_id": _satellite(hass, entry)},
                    "data": {
                        "media_id": f"{base}/test_voice_stream/speech",
                        "preannounce": False,
                    },
                }
            ]
        ),
        "ownership probe",
        "script",
    )
    with (
        patch.object(stream, "_async_prepare", side_effect=preparing),
        patch.object(
            AssistSatelliteEntity, "_resolve_announcement_media_id", resolving
        ),
    ):
        run = asyncio.create_task(script.async_run(context=Context()))
        async with asyncio.timeout(5):
            await entered.wait()
        if phase != "normal":
            await script.async_stop()
            with pytest.raises(asyncio.CancelledError):
                await run
            assert not script.is_running
            assert sat.state.value == "idle"
            assert not sat._announcements

        release.set()
        if phase in ("before_pa", "during_prepare"):
            await hass.async_block_till_done()
            assert not speaker.streams
            assert not sat._announcements
        else:
            async with asyncio.timeout(10):
                await _event(panel)
                await speaker.ended(1)
                if phase == "normal":
                    await run
            assert len(speaker.streams) == 1
            assert wire.ends[-1]["outcome"] == "played"
            assert not sat._announcements
            assert sat.state.value == "idle"

    await script.async_unload()


@LINGERING
async def test_stopped_script_withdraws_only_its_member_of_a_shared_batch(
    hass,
    hass_ws_client,
    hass_read_only_access_token,
    hass_read_only_user,
    entry,  # noqa: F811
    base,  # noqa: F811
    wires,  # noqa: F811
):
    from .test_transport import OTHER_DID
    from .test_voice_stream import _listener, _second_entry

    await _second_entry(hass, hass_read_only_user.id)
    _, _, canceled_speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires
    )
    _, _, live_speaker = await _voice_panel(
        hass, hass_ws_client, hass_read_only_access_token, wires, did=OTHER_DID
    )
    stream = async_get_voice_stream(hass)
    satellite = satellites(hass)[entry.entry_id]
    entered, release = asyncio.Event(), asyncio.Event()
    prepare = stream._async_prepare

    async def held(urls):
        entered.set()
        await release.wait()
        return await prepare(urls)

    url = f"{base}/test_voice_stream/speech"
    script = Script(
        hass,
        cv.SCRIPT_SCHEMA(
            [
                {
                    "service": "assist_satellite.announce",
                    "target": {"entity_id": _satellite(hass, entry)},
                    "data": {"media_id": url, "preannounce": False},
                }
            ]
        ),
        "shared cancellation",
        "script",
    )
    with patch.object(stream, "_async_prepare", side_effect=held):
        running = asyncio.create_task(script.async_run(context=Context()))
        await entered.wait()
        told = []
        key = (satellite._context.id, url, None)
        other = asyncio.create_task(
            stream.async_announce(key, _listener(live_speaker, told), (url,))
        )
        # Let the second call join before canceling the Script; the shared batch
        # remains held in production preparation until release below.
        await asyncio.sleep(0)
        await script.async_stop()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert not satellite._announcements
        release.set()
        assert await other
        await live_speaker.ended(1)
        assert len(told) == len(live_speaker.streams) == 1
        assert not canceled_speaker.streams
    await script.async_unload()

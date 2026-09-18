"""Choosing and handing over the Home Assistant address a panel should use."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.panel_assistant.client import (
    CannotConnectError,
    PanelAddress,
    PanelSetupState,
)
from custom_components.panel_assistant.ha_url import (
    async_offer_ha_url,
    async_panel_facing_url,
)


class _FakePanel:
    """A panel that records what it was handed, without a socket."""

    def __init__(self, state: PanelSetupState | Exception) -> None:
        self._state = state
        self.handed_over: list[str] = []
        self.hand_over_error: Exception | None = None
        self.state_reads = 0

    async def async_get_setup_state(self) -> PanelSetupState:
        self.state_reads += 1
        if isinstance(self._state, Exception):
            raise self._state
        return self._state

    async def async_hand_over_ha_url(self, ha_url: str) -> None:
        if self.hand_over_error is not None:
            raise self.hand_over_error
        self.handed_over.append(ha_url)


async def _set_urls(
    hass: HomeAssistant, *, internal: str | None, external: str | None
) -> None:
    await hass.config.async_update(internal_url=internal, external_url=external)


def _core_api(*, local_ip: str, use_ssl: bool) -> Any:
    """The parts of `hass.config.api` that `get_url`'s internal branch reads.

    A stand-in rather than a real `ApiConfig`, because the two attributes below
    are the whole of what the decision depends on, and building a real one drags
    in an HTTP component this test has no use for.
    """

    class _Api:
        def __init__(self) -> None:
            self.local_ip = local_ip
            self.use_ssl = use_ssl
            self.port = 8123

    return _Api()


# --------------------------------------------------------------------------
# URL selection
# --------------------------------------------------------------------------


async def test_an_internal_url_is_preferred_because_the_panel_is_on_that_network(
    hass: HomeAssistant,
) -> None:
    """A panel is a device on the home network, so the local address wins."""
    await _set_urls(
        hass, internal="http://192.0.2.5:8123", external="https://ha.example.com"
    )
    assert async_panel_facing_url(hass) == "http://192.0.2.5:8123"


async def test_an_external_url_is_never_handed_to_a_panel(
    hass: HomeAssistant,
) -> None:
    """A public address is worse than asking, so nothing is handed over.

    The panel would verify it — Home Assistant really does answer there — and
    then route its whole dashboard session out to the internet and back, going
    dark whenever WAN or DNS did. Against today's behaviour that is a
    regression, because today the operator supplies a local address.
    """
    await _set_urls(hass, internal=None, external="https://ha.example.com")
    assert async_panel_facing_url(hass) is None


async def test_an_internal_only_installation_hands_over_that_address(
    hass: HomeAssistant,
) -> None:
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    assert async_panel_facing_url(hass) == "http://192.0.2.5:8123"


async def test_a_container_address_is_handed_over_because_nothing_here_can_tell(
    hass: HomeAssistant,
) -> None:
    """The container trap, driven through Core's own synthesis rather than around it.

    With no internal URL configured, Home Assistant builds one from the address
    it detects for itself, which inside a bridge-networked container is the
    container's own address. It is a correct answer to the question Home
    Assistant can ask, and it is useless to a panel — but nothing on this side
    can distinguish it from a real LAN address, so it IS what gets handed over.
    Rejecting it is the panel's job, and the panel's tests own that half.

    Setting `internal_url` here instead would return before reaching the
    fallback this test is named for, making it a duplicate of the test above.
    So the fallback is exercised: no internal URL, plain HTTP, a detected IP.
    """
    await _set_urls(hass, internal=None, external=None)
    hass.config.api = _core_api(local_ip="172.17.0.2", use_ssl=False)

    assert async_panel_facing_url(hass) == "http://172.17.0.2:8123"


async def test_terminating_tls_on_core_hands_over_nothing_rather_than_the_wan_address(
    hass: HomeAssistant,
) -> None:
    """The blocker this module was held for, driven through the guard that causes it.

    Core refuses to synthesize an internal address from the detected local IP
    whenever TLS is terminated on Core itself. That is the DuckDNS plus
    Let's Encrypt shape — external URL set because the guide says to, internal
    left on automatic — and with `allow_external` open it would fall through to
    the public address and hand a wall panel its own WAN URL.
    """
    await _set_urls(hass, internal=None, external="https://ha.example.com")
    hass.config.api = _core_api(local_ip="192.0.2.50", use_ssl=True)

    assert async_panel_facing_url(hass) is None


async def test_plain_http_core_still_offers_its_detected_address(
    hass: HomeAssistant,
) -> None:
    """The same shape without TLS keeps working, so the fix is not a blanket refusal."""
    await _set_urls(hass, internal=None, external="https://ha.example.com")
    hass.config.api = _core_api(local_ip="192.0.2.50", use_ssl=False)

    assert async_panel_facing_url(hass) == "http://192.0.2.50:8123"


async def test_neither_a_cloud_nor_an_external_url_is_reachable_from_this_call(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both remote doors are shut at the call, which is the only place they can be.

    A cloud or external URL is reachable only through ``get_url``'s own
    branches, so asserting the arguments is what proves neither can be returned;
    no downstream check would catch one. Routing a wall panel's dashboard out of
    the building to reach a server inside it is the shared defect.
    """
    captured: dict[str, Any] = {}

    def _fake_get_url(_hass: HomeAssistant, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "http://192.0.2.5:8123"

    monkeypatch.setattr(
        "custom_components.panel_assistant.ha_url.get_url", _fake_get_url
    )
    async_panel_facing_url(hass)
    assert captured["allow_cloud"] is False
    assert captured["allow_external"] is False
    assert captured["allow_internal"] is True


async def test_no_url_at_all_is_an_ordinary_outcome_not_an_error(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installation with nothing to offer leaves the panel asking, as before."""

    def _raise(_hass: HomeAssistant, **_kwargs: Any) -> str:
        raise NoURLAvailableError

    monkeypatch.setattr("custom_components.panel_assistant.ha_url.get_url", _raise)
    assert async_panel_facing_url(hass) is None


# --------------------------------------------------------------------------
# Whether to hand anything over at all
# --------------------------------------------------------------------------


async def test_a_panel_mid_setup_that_accepts_a_handover_is_given_the_url(
    hass: HomeAssistant,
) -> None:
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    panel = _FakePanel(PanelSetupState(complete=False, accepts_handover=True))
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == ["http://192.0.2.5:8123"]


async def test_an_older_panel_is_never_sent_the_handover(hass: HomeAssistant) -> None:
    """The version gate.

    A panel that does not advertise handover support would refuse the unknown
    key, and its config admission is atomic — so the request would not merely
    fail to hand over, it would be rejected whole. Absence of the advertisement
    therefore has to stop the send, not be discovered by trying.
    """
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    panel = _FakePanel(PanelSetupState(complete=False, accepts_handover=False))
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == []


async def test_a_panel_that_has_finished_setup_is_left_alone(
    hass: HomeAssistant,
) -> None:
    """There is no question left to save, and its address is not ours to change."""
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    panel = _FakePanel(PanelSetupState(complete=True, accepts_handover=True))
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == []


async def test_nothing_is_sent_when_there_is_no_url_to_send(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_hass: HomeAssistant, **_kwargs: Any) -> str:
        raise NoURLAvailableError

    monkeypatch.setattr("custom_components.panel_assistant.ha_url.get_url", _raise)
    panel = _FakePanel(PanelSetupState(complete=False, accepts_handover=True))
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == []


async def test_a_panel_that_will_not_say_is_not_guessed_at(hass: HomeAssistant) -> None:
    """An unreadable setup state is not an invitation to post the key anyway."""
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    panel = _FakePanel(CannotConnectError())
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == []


async def test_a_refused_handover_never_raises(hass: HomeAssistant) -> None:
    """The handover saves a question; it must not be able to fail an install."""
    await _set_urls(hass, internal="http://192.0.2.5:8123", external=None)
    panel = _FakePanel(PanelSetupState(complete=False, accepts_handover=True))
    panel.hand_over_error = CannotConnectError()
    await async_offer_ha_url(hass, panel)  # type: ignore[arg-type]
    assert panel.handed_over == []


# --------------------------------------------------------------------------
# What actually goes on the wire
# --------------------------------------------------------------------------


async def test_the_handover_posts_the_marker_and_the_url_together(
    hass: HomeAssistant,
) -> None:
    """One atomic request carries both, and carries no credential.

    The marker must arrive with the URL rather than after it, because config
    admission is atomic: two requests could leave a panel holding an address
    with no record of who gave it, which is the state the wizard cannot explain.
    """
    from custom_components.panel_assistant.client import HaPaneldClient

    client = HaPaneldClient(Mock(), PanelAddress(host="panel.local", port=8888))
    posted: dict[str, Any] = {}

    async def _fake_post(
        url: Any, form: dict[str, str], *_args: Any, **_kwargs: Any
    ) -> tuple[int, bytes]:
        posted["url"] = str(url)
        posted["form"] = form
        return 200, b"{}"

    client._async_post_bounded = _fake_post  # type: ignore[assignment,method-assign]
    await client.async_hand_over_ha_url("http://192.0.2.5:8123")

    assert posted["url"].endswith("/api/v1/config")
    assert posted["form"] == {
        "ha_setup_handover": "true",
        "ha_url_handover": "http://192.0.2.5:8123",
    }
    assert not any(
        key in posted["form"]
        for key in ("ha_token", "ha_refresh_token", "token", "password")
    )


async def test_the_panel_owns_the_verdict_so_a_refusal_is_not_a_delivery_failure(
    hass: HomeAssistant,
) -> None:
    """A 200 saying the address did not answer is a successful write.

    The panel stores the marker and the attempted address either way, and
    reports the verdict on its setup state. Treating that as a client error
    would make a correctly recorded failure look like a broken panel.
    """
    from custom_components.panel_assistant.client import HaPaneldClient

    client = HaPaneldClient(Mock(), PanelAddress(host="panel.local", port=8888))

    async def _fake_post(*_args: Any, **_kwargs: Any) -> tuple[int, bytes]:
        return 200, b'{"verified":false,"reason":"unreachable"}'

    client._async_post_bounded = _fake_post  # type: ignore[assignment,method-assign]
    await client.async_hand_over_ha_url("http://172.17.0.2:8123")


async def test_a_panel_that_rejects_the_write_is_reported_as_unreachable(
    hass: HomeAssistant,
) -> None:
    from custom_components.panel_assistant.client import HaPaneldClient

    client = HaPaneldClient(Mock(), PanelAddress(host="panel.local", port=8888))

    async def _fake_post(*_args: Any, **_kwargs: Any) -> tuple[int, bytes]:
        return 400, b"ha_url_handover: unknown setting\n"

    client._async_post_bounded = _fake_post  # type: ignore[assignment,method-assign]
    with pytest.raises(CannotConnectError):
        await client.async_hand_over_ha_url("http://192.0.2.5:8123")


# --------------------------------------------------------------------------
# Reading the panel's setup state
# --------------------------------------------------------------------------


def _client_returning(body: bytes) -> Any:
    from custom_components.panel_assistant.client import HaPaneldClient

    client = HaPaneldClient(Mock(), PanelAddress(host="panel.local", port=8888))
    client._async_get_bounded = AsyncMock(return_value=body)  # type: ignore[method-assign]
    return client


async def test_an_older_panels_setup_state_reads_as_not_accepting_a_handover() -> None:
    """No handover object at all is the old shape, and it is not an error."""
    state = await _client_returning(b'{"complete":false}').async_get_setup_state()
    assert state.complete is False
    assert state.accepts_handover is False
    assert state.handover_url is None
    assert state.handover_reason is None


async def test_a_current_panel_advertises_that_it_accepts_a_handover() -> None:
    state = await _client_returning(
        b'{"complete":false,"handover":{"supported":true,"source":false,'
        b'"url":"","reason":""}}'
    ).async_get_setup_state()
    assert state.accepts_handover is True
    assert state.handover_url is None
    assert state.handover_reason is None


async def test_a_failed_handover_is_reported_with_its_address_and_reason() -> None:
    """This is what the wizard renders as a correction rather than a blank field."""
    state = await _client_returning(
        b'{"complete":false,"handover":{"supported":true,"source":true,'
        b'"url":"http://172.17.0.2:8123","reason":"unreachable"}}'
    ).async_get_setup_state()
    assert state.accepts_handover is True
    assert state.handover_url == "http://172.17.0.2:8123"
    assert state.handover_reason == "unreachable"


async def test_support_must_be_stated_exactly_and_is_not_inferred() -> None:
    """Anything other than a literal true is not an advertisement of support."""
    for value in (b'"true"', b"1", b"null", b"{}", b"[]"):
        body = b'{"complete":false,"handover":{"supported":' + value + b"}}"
        state = await _client_returning(body).async_get_setup_state()
        assert state.accepts_handover is False, value


async def test_a_handover_that_is_not_an_object_is_not_trusted() -> None:
    for value in (b'"yes"', b"7", b"null", b"[]"):
        body = b'{"complete":false,"handover":' + value + b"}"
        state = await _client_returning(body).async_get_setup_state()
        assert state.accepts_handover is False, value


async def test_setup_complete_still_reads_through_the_richer_state() -> None:
    """The narrow caller keeps working, so one read answers both questions."""
    client = _client_returning(b'{"complete":true,"handover":{"supported":true}}')
    assert await client.async_get_setup_complete() is True

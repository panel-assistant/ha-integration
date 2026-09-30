"""Home Assistant addresses for setup and authenticated connection recovery.

Native handshakes also advertise bounded local and external alternatives. The
panel checks this instance's public identity before sending credentials on an
alternate. The narrower setup handover policy below remains independent.

When this integration installs or adopts a panel it already knows where Home
Assistant is, so making the panel's owner type that address into the panel's
setup wizard asks a question with a known answer. This module picks the address
to hand over.

The choice is deliberately narrow, because the panel is on the other side of the
network from the party making it:

* **Internal only.** A panel is a device on the home network, so a local
  address is the only kind worth handing it.
* **Never the external URL.** ``allow_external=False``, and this is load
  bearing rather than tidy. Core's ``_get_internal_url`` refuses to synthesize
  an address from the detected local IP whenever TLS is terminated on Core
  itself, so on the common DuckDNS-plus-Let's-Encrypt setup — external URL set,
  internal left on automatic — ``get_url`` would fall straight through to the
  public WAN address. The panel's probe would then verify it, because Home
  Assistant really does answer there, and promote it with nothing shown to the
  operator. That panel would route its whole authenticated dashboard session
  out to the internet and back, and go dark whenever WAN or DNS did. It is
  strictly worse than asking, which is why nothing is handed over instead.
* **Never the cloud URL.** ``allow_cloud=False``, for the same reason one step
  further out: a Nabu Casa address routes a wall panel's dashboard through a
  remote relay to reach a server in the same building, and it stops working the
  moment the subscription does.
* **Never mDNS.** This reads Home Assistant's own configured or detected
  address, not anything discovered on the network. Discovery is what the panel
  already does for itself when nobody hands it anything.

Returning nothing is therefore a normal, safe outcome, not a failure: the panel
asks exactly as it does today.

What this module cannot do is tell whether the address it picked is reachable
*from the panel*. ``get_url`` answers from Home Assistant's own network position,
and the common failure is invisible from here: with no internal URL configured,
Home Assistant synthesizes one from ``hass.config.api.local_ip``, which inside a
bridge-networked container is the container's address. It is correct, and it is
useless to a panel. That is why the panel verifies what it is handed and shows a
correction when it does not answer, rather than this module trying to guess.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from ipaddress import ip_address

from aiohttp import web
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.helpers.network import NoURLAvailableError, get_url
from yarl import URL

from .client import HaPaneldClient, HaPaneldError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
DATA_INSTANCE_ID = "ha_instance_id"


def async_panel_facing_url(hass: HomeAssistant) -> str | None:
    """Return the best Home Assistant address for a panel, or None if there is none.

    None is an ordinary outcome, not an error: an installation with no internal
    URL, no usable detected address and no external URL has nothing to hand over,
    and the panel then asks as it always did.
    """
    try:
        return get_url(
            hass,
            allow_internal=True,
            allow_external=False,
            allow_cloud=False,
            allow_ip=True,
            require_ssl=False,
            require_standard_port=False,
        )
    except NoURLAvailableError:
        return None


async def async_offer_ha_url(hass: HomeAssistant, client: HaPaneldClient) -> None:
    """Hand a panel Home Assistant's address, if this panel is one that wants it.

    Used by every path that installs or adopts a panel, so that "Home Assistant
    started this" means the same thing at each of them. It is best-effort by
    design: the handover saves the panel's owner a question, and nothing about an
    install or an adoption should fail because that convenience did not land.

    Three reasons to say nothing at all:

    * **The panel has finished setup.** There is no question left to save, and a
      configured panel's address is not ours to change.
    * **The panel does not advertise handover support.** Its config admission
      refuses an unknown key *and* does so atomically, so posting to an older
      panel would reject the whole request rather than ignore one field. The
      advertisement is the version gate; see ``PanelSetupState``.
    * **Home Assistant has no address to offer.** Nothing to hand over, so the
      panel asks as it always did.

    Whether the address actually works is the panel's decision, not this one: it
    verifies what it is handed from its own network and shows a correction when
    it does not answer.
    """
    try:
        state = await client.async_get_setup_state()
    except HaPaneldError:
        _LOGGER.debug("Panel did not report its setup state; not handing over a URL")
        return
    if state.complete or not state.accepts_handover:
        return
    ha_url = async_panel_facing_url(hass)
    if ha_url is None:
        _LOGGER.debug(
            "No Home Assistant URL is available to hand a panel; it will ask instead"
        )
        return
    try:
        await client.async_hand_over_ha_url(ha_url)
    except HaPaneldError:
        _LOGGER.debug("Panel did not accept the Home Assistant address %s", ha_url)


async def async_setup_connection_info(hass: HomeAssistant) -> None:
    """Load Core's durable identity before accepting any panel handshake."""
    own_id = await instance_id.async_get(hass)
    hass.data[DOMAIN][DATA_INSTANCE_ID] = own_id
    hass.http.register_view(InstanceView(own_id))


class InstanceView(HomeAssistantView):
    """Identify this server before a panel sends credentials to a learned route.

    This public identity check prevents accidental cross-instance fallback on a
    trusted LAN. It is not cryptographic authentication of an HTTP server.
    """

    url = "/api/panel_assistant/instance"
    name = "api:panel_assistant:instance"
    requires_auth = False

    def __init__(self, own_id: str) -> None:
        """Keep the same identity that the authenticated hello advertises."""
        self.own_id = own_id

    @callback
    def get(self, request: web.Request) -> web.Response:
        """Return only our identity, never an identity supplied by the caller."""
        return web.json_response(
            {"instance_id": self.own_id}, headers={"Cache-Control": "no-store"}
        )


def _connection_origin(value: str) -> str | None:
    """Admit bounded credential-free origins; discovery and redirects add none."""
    if len(value) > 2048 or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        url = URL(value)
        host = url.host
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.user is not None
            or url.password is not None
            or URL(value, encoded=True).raw_path not in {"", "/"}
            or "?" in value
            or "#" in value
            or "%" in host
            or "\\" in value
            or url.port is None
            or not 0 < url.port <= 65535
            or host.lower().rstrip(".") == "localhost"
        ):
            return None
        try:
            address = ip_address(host)
        except ValueError:
            pass
        else:
            if (
                address.is_loopback
                or address.is_unspecified
                or address.is_multicast
                or address.is_link_local
            ):
                return None
        if url.is_default_port():
            url = url.with_port(None)
        return str(url.origin())
    except ValueError, UnicodeError:
        return None


def async_connection_urls(hass: HomeAssistant) -> list[str]:
    """Offer Core-owned local routes first, then external and cloud routes.

    The configured panel URL remains the panel's preferred route. These hints
    grant no authority until its credential-free instance check succeeds.
    """
    candidates = [hass.config.internal_url]
    # Core's helper returns only one internal route. Keep its directly detected
    # address too, so a configured hostname can survive DNS failure. Never
    # invent a plaintext route or bypass certificate validation for TLS Core.
    if (api := hass.config.api) is not None and not api.use_ssl:
        candidates.append(
            str(URL.build(scheme="http", host=api.local_ip, port=api.port))
        )
    candidates.append(hass.config.external_url)
    with suppress(NoURLAvailableError, ValueError):
        candidates.append(get_url(hass, require_cloud=True))
    result: list[str] = []
    for value in candidates:
        if (
            value is not None
            and (origin := _connection_origin(value)) is not None
            and origin not in result
        ):
            result.append(origin)
    return result[:4]

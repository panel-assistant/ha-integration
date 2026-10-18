"""A saved foreign account is an advisory, never permission to move a panel."""

import asyncio
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import psutil
import pytest
from aiohttp import ClientSession, CookieJar, web
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_socket import socket_allow_hosts
from yarl import URL

from custom_components.panel_assistant.client import HaPaneldClient, PanelAddress
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.foreign_ha import (
    async_connected_to_other_ha,
    async_raise_foreign_ha_issue,
    foreign_ha_issue_id,
)
from custom_components.panel_assistant.ha_url import DATA_INSTANCE_ID
from custom_components.panel_assistant.transport_binding import (
    async_raise_binding_issue,
    async_record_binding_request,
)

from .config_flow_helpers import (
    DISCOVERY_HEALTH,
    DISCOVERY_ID,
    _connect_found,
    _start_step,
    install_network_pin,  # noqa: F401
    install_release_catalog,  # noqa: F401
)
from .test_availability import STATUS_ENTITY, _connect, _load

OWN_ID = "1" * 32
OTHER_ID = "2" * 32
PANEL_CONFIG = "http://192.168.1.23:8888/api/v1/config"
OTHER_ORIGIN = "https://another-ha.example"
IDENTITY = OTHER_ORIGIN + "/api/panel_assistant/instance"


def _config(url=OTHER_ORIGIN, configured=True):
    return {"settings": {"ha_url": url}, "ha_auth": {"configured": configured}}


def _client(hass: HomeAssistant) -> HaPaneldClient:
    hass.data.setdefault(DOMAIN, {})[DATA_INSTANCE_ID] = OWN_ID
    return HaPaneldClient(
        async_get_clientsession(hass), PanelAddress("192.168.1.23", 8888)
    )


@pytest.mark.parametrize(
    ("saved_url", "internal", "external", "identity", "expected"),
    [
        (OTHER_ORIGIN, None, None, OTHER_ID, True),
        ("https://ANOTHER-HA.example:443/", None, None, OTHER_ID, True),
        (OTHER_ORIGIN, None, None, OWN_ID, False),
        (OTHER_ORIGIN, OTHER_ORIGIN, None, OWN_ID, False),
        (OTHER_ORIGIN, None, OTHER_ORIGIN, OWN_ID, False),
        # Matching a configured address is not evidence of matching identity.
        (OTHER_ORIGIN, OTHER_ORIGIN, OTHER_ORIGIN, OTHER_ID, True),
    ],
    ids=[
        "foreign",
        "normalized-origin",
        "own-alias",
        "internal",
        "external",
        "same-address-different-server",
    ],
)
async def test_saved_server_is_compared_by_identity_without_a_write(
    hass: HomeAssistant,
    aioclient_mock,
    saved_url,
    internal,
    external,
    identity,
    expected,
) -> None:
    await hass.config.async_update(internal_url=internal, external_url=external)
    aioclient_mock.get(PANEL_CONFIG, json=_config(saved_url))
    aioclient_mock.get(IDENTITY, json={"instance_id": identity})

    assert await async_connected_to_other_ha(hass, _client(hass)) is expected
    assert [request[0] for request in aioclient_mock.mock_calls] == ["GET", "GET"]
    assert (await _client(hass).async_get_configured_ha_url()) == saved_url


async def test_authenticated_local_binding_question_retires_foreign_warning(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Study display",
        unique_id=DISCOVERY_ID,
        data={CONF_ADDRESS: "192.168.1.23"},
    )
    entry.add_to_hass(hass)
    assert async_raise_foreign_ha_issue(hass, entry) is True
    registry = ir.async_get(hass)
    assert (
        registry.async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id))
        is not None
    )

    async_raise_binding_issue(hass, entry, "authenticated-local-user")

    assert registry.async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id)) is None
    assert any(
        issue.translation_key == "panel_user_mismatch"
        for issue in registry.issues.values()
    )


async def test_loaded_device_warning_clears_on_session_and_unload(
    hass: HomeAssistant,
    hass_read_only_user,
    hass_ws_client,
    hass_read_only_access_token,
) -> None:
    entry = await _load(hass, hass_read_only_user.id)
    assert async_raise_foreign_ha_issue(hass, entry) is True
    await hass.async_block_till_done()
    assert hass.states.get(STATUS_ENTITY).state == "another_home_assistant"

    client = await _connect(hass, hass_ws_client, hass_read_only_access_token, entry)
    await hass.async_block_till_done()
    assert hass.states.get(STATUS_ENTITY).state == "online"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id)) is None

    await client.close()
    await hass.async_block_till_done()
    assert hass.states.get(STATUS_ENTITY).state == "online"
    assert registry.async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id)) is None

    assert async_raise_foreign_ha_issue(hass, entry) is True
    await hass.config_entries.async_unload(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id)) is None


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"settings": {"ha_url": OTHER_ORIGIN}},
        _config(configured=False),
        _config(configured="true"),
        _config(url=None),
        _config(url=""),
        _config(url="ftp://another-ha.example"),
        _config(url="https://user:password@another-ha.example"),
        _config(url=OTHER_ORIGIN + "/dashboard"),
        _config(url=OTHER_ORIGIN + "?token=secret"),
        _config(url="http://127.0.0.1:8123"),
    ],
)
async def test_missing_credentials_or_unusable_address_never_probes_a_server(
    hass: HomeAssistant,
    aioclient_mock,
    document,
) -> None:
    aioclient_mock.get(PANEL_CONFIG, json=document)
    aioclient_mock.get(IDENTITY, json={"instance_id": OTHER_ID})
    assert await async_connected_to_other_ha(hass, _client(hass)) is False
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        {"text": "not json"},
        {"json": []},
        {"json": {}},
        {"json": {"instance_id": None}},
        {"json": {"instance_id": "A" * 32}},
        {"json": {"instance_id": "2" * 31}},
        {"text": '{"instance_id":"' + OTHER_ID + '","padding":"' + "x" * 512 + '"}'},
        {"text": '{"instance_id":"' + OTHER_ID + '","instance_id":"' + OWN_ID + '"}'},
        {"status": 503},
        {"status": 302, "headers": {"Location": "https://redirect.example/probe"}},
        {"exc": TimeoutError()},
    ],
    ids=[
        "invalid-json",
        "array",
        "missing-id",
        "null-id",
        "uppercase-id",
        "short-id",
        "oversized",
        "duplicate-id",
        "unavailable",
        "redirect",
        "timeout",
    ],
)
async def test_unknown_identity_is_silent_and_redirects_are_not_followed(
    hass: HomeAssistant,
    aioclient_mock,
    response,
) -> None:
    aioclient_mock.get(PANEL_CONFIG, json=_config())
    aioclient_mock.get(IDENTITY, **response)
    assert await async_connected_to_other_ha(hass, _client(hass)) is False
    assert aioclient_mock.call_count == 2
    assert not ir.async_get(hass).issues


@pytest.mark.parametrize(
    "response", [{"status": 503}, {"text": "invalid"}, {"exc": TimeoutError()}]
)
async def test_config_failure_is_unknown_not_an_adoption_failure(
    hass: HomeAssistant,
    aioclient_mock,
    response,
) -> None:
    aioclient_mock.get(PANEL_CONFIG, **response)
    assert await async_connected_to_other_ha(hass, _client(hass)) is False
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize("redirect", [False, True], ids=["direct", "redirect"])
async def test_identity_probe_sends_no_credentials_and_does_not_follow_redirects(
    hass: HomeAssistant,
    socket_enabled: None,
    redirect: bool,
) -> None:
    # A real HTTP peer sees the headers after aiohttp has merged session state.
    # Loopback is rejected by production normalization, so bind a local
    # interface address without performing DNS or contacting another machine.
    host = next(
        address.address
        for addresses in psutil.net_if_addrs().values()
        for address in addresses
        if address.family.name == "AF_INET"
        and not ip_address(address.address).is_loopback
    )
    socket_allow_hosts([host, "127.0.0.1"])
    seen = []
    saved = {}

    async def config(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, dict(request.headers)))
        return web.json_response(saved)

    async def identity(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, dict(request.headers)))
        if redirect and request.path.endswith("/instance"):
            return web.Response(
                status=302, headers={"Location": "/redirected-identity"}
            )
        response = web.json_response({"instance_id": OTHER_ID})
        response.set_cookie("foreign-cookie", "must-not-return")
        return response

    app = web.Application()
    app.router.add_get("/api/v1/config", config)
    app.router.add_get("/api/panel_assistant/instance", identity)
    app.router.add_get("/redirected-identity", identity)
    async with (
        TestServer(app, host=host) as server,
        ClientSession(
            headers={"Authorization": "Bearer panel-only-secret"},
            cookie_jar=CookieJar(unsafe=True),
        ) as panel_session,
    ):
        origin = str(server.make_url("/")).rstrip("/")
        saved.update(_config(origin))
        shared = async_get_clientsession(hass)
        shared.cookie_jar.update_cookies({"core-cookie": "secret"}, URL(origin))
        panel_session.cookie_jar.update_cookies({"panel-cookie": "secret"}, URL(origin))
        hass.data.setdefault(DOMAIN, {})[DATA_INSTANCE_ID] = OWN_ID
        client = HaPaneldClient(panel_session, PanelAddress(host, server.port))

        for _ in range(2):
            assert await async_connected_to_other_ha(hass, client) is not redirect

    probes = [headers for _, path, headers in seen if path.endswith("/instance")]
    assert len(probes) == 2
    config_headers = [headers for _, path, headers in seen if path.endswith("/config")]
    assert all(
        headers["Authorization"] == "Bearer panel-only-secret"
        for headers in config_headers
    )
    assert all("panel-cookie=secret" in headers["Cookie"] for headers in config_headers)
    assert all(
        "Authorization" not in headers and "Cookie" not in headers for headers in probes
    )
    assert all(method == "GET" for method, _, _ in seen)
    assert all(path != "/redirected-identity" for _, path, _ in seen)
    assert saved == _config(origin)


@pytest.mark.parametrize(
    ("response", "warning"),
    [
        ({"json": {"instance_id": OTHER_ID}}, True),
        ({"json": {"instance_id": OWN_ID}}, False),
        ({"status": 503}, False),
    ],
    ids=["foreign-warning", "own-alias", "unavailable-silent"],
)
async def test_adoption_completes_and_links_only_positive_evidence_to_device(
    hass: HomeAssistant,
    aioclient_mock,
    response,
    warning,
) -> None:
    _client(hass)
    aioclient_mock.get(PANEL_CONFIG, json=_config())
    aioclient_mock.get(IDENTITY, **response)
    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.async_unload_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.ha_url.instance_id.async_get",
            AsyncMock(return_value=OWN_ID),
        ),
    ):
        form = await _start_step(hass, "add_panel")
        _client(hass)
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "192.168.1.23"}
        )
        result = await _connect_found(hass, found)
        await hass.config_entries.async_unload(result["result"].entry_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: "192.168.1.23"}
    entry = result["result"]
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, foreign_ha_issue_id(entry.entry_id)
    )
    if warning:
        assert result["description"] == "another_home_assistant"
        assert issue is not None
        assert issue.translation_key == "another_home_assistant"
        assert issue.data == {"entry_id": entry.entry_id}
        assert issue.is_persistent is False
        assert issue.translation_placeholders == {"panel": entry.title}
    else:
        assert result.get("description") != "another_home_assistant"
        assert issue is None
    assert all(call[0] == "GET" for call in aioclient_mock.mock_calls)


@pytest.mark.parametrize("local_evidence", ["connected", "binding-request"])
async def test_delayed_adoption_probe_cannot_recreate_warning_after_local_contact(
    hass: HomeAssistant,
    aioclient_mock,
    local_evidence,
) -> None:
    aioclient_mock.get(PANEL_CONFIG, json=_config())
    aioclient_mock.get(IDENTITY, json={"instance_id": OTHER_ID})
    entered = asyncio.Event()
    release = asyncio.Event()
    read_config = HaPaneldClient.async_get_configured_ha_url

    async def delayed_read(client):
        result = await read_config(client)
        entered.set()
        await release.wait()
        return result

    with (
        patch(
            "custom_components.panel_assistant.config_flow.HaPaneldClient.async_get_health",
            AsyncMock(return_value=DISCOVERY_HEALTH),
        ),
        patch(
            "custom_components.panel_assistant.async_setup_entry",
            AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.panel_assistant.async_unload_entry",
            AsyncMock(return_value=True),
        ),
        patch.object(HaPaneldClient, "async_get_configured_ha_url", delayed_read),
    ):
        form = await _start_step(hass, "add_panel")
        _client(hass)
        found = await hass.config_entries.flow.async_configure(
            form["flow_id"], {CONF_ADDRESS: "192.168.1.23"}
        )
        task = asyncio.create_task(_connect_found(hass, found))
        await asyncio.wait_for(entered.wait(), timeout=2)
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        if local_evidence == "connected":
            entry.runtime_data = SimpleNamespace(
                coordinator=SimpleNamespace(
                    connected=True, async_update_listeners=Mock()
                )
            )
        else:
            async_record_binding_request(hass, DISCOVERY_ID, "local-user")
        release.set()
        result = await task
        await hass.config_entries.async_unload(entry.entry_id)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result.get("description") != "another_home_assistant"
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, foreign_ha_issue_id(entry.entry_id))
        is None
    )
    assert [request[0] for request in aioclient_mock.mock_calls] == ["GET", "GET"]

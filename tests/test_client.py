"""Tests for the read-only ha-paneld client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from custom_components.panel_assistant.client import (
    CannotConnectError,
    HaPaneldClient,
    InvalidAddressError,
    InvalidResponseError,
    PanelHealth,
    StagedApk,
    StagingUnavailableError,
    UpdateApprovalRequiredError,
    UpdateBusyError,
    UpdateRejectedError,
    UploadDisabledError,
    is_newer_stable_version,
    normalize_address,
    parse_diag_version,
    parse_health_response,
    parse_panel_install_status,
    parse_staged_apk,
)
from custom_components.panel_assistant.const import (
    MAX_STATUS_CAPABILITIES,
    MAX_STATUS_RESPONSE_BYTES,
    MAX_STATUS_WARNING_LENGTH,
    MAX_STATUS_WARNINGS,
)
from custom_components.panel_assistant.status import parse_status_response

from .http_fakes import FakeResponse, FakeSession

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures"
HEALTH_FIXTURE = FIXTURE_DIRECTORY / "health.txt"
STATUS_FIXTURE = FIXTURE_DIRECTORY / "status.json"


def _session(
    *, status: int = 200, body: bytes = b"", error: Exception | None = None
) -> FakeSession:
    return FakeSession(FakeResponse(status, body, split=True), error=error)


def test_normalize_address() -> None:
    """Addresses are stored without schemes and with explicit non-default ports."""
    assert normalize_address(" PANEL.local ").stored_value == "panel.local"
    assert normalize_address("192.0.2.1:9999").stored_value == "192.0.2.1:9999"
    assert normalize_address("[fd00::1]").stored_value == "[fd00::1]"
    assert normalize_address("[fd00::1]:9999").stored_value == "[fd00::1]:9999"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://panel.local",
        "panel.local/path",
        "user@panel.local",
        "panel local",
        "panel.local:",
        "panel.local:0",
    ],
)
def test_reject_invalid_address(value: str) -> None:
    """URLs, paths, credentials and whitespace are not panel addresses."""
    with pytest.raises(InvalidAddressError):
        normalize_address(value)


def test_parse_health_fixture() -> None:
    """The replay fixture follows the exact Android health contract."""
    health = parse_health_response(HEALTH_FIXTURE.read_text(encoding="utf-8"))
    assert health.version == "0.9.0"
    assert health.panel_id == "alpha"
    assert health.build == "1000"
    assert health.config_hash == "1a2b3c4d"
    assert health.ha_state == "normal"
    assert health.ha_source == "mqtt"
    assert health.ha_subscription_refused is False


def test_restart_health_token_is_bounded_optional_status() -> None:
    base = "ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd"
    assert parse_health_response(
        base + " pa_restarting=app,update,45000\n"
    ).restart == ("app", "update", 45000)
    for token in (
        "app,update,0",
        "app,unknown,45000",
        "panel,reboot,300001",
        "app,update,1x",
    ):
        assert parse_health_response(base + f" pa_restarting={token}\n").restart is None


def test_parser_ignores_future_tokens_and_values() -> None:
    """Additive health tokens do not invalidate an otherwise stable response."""
    health = parse_health_response(
        "ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd "
        "ha=future_state ha_src=future_source future=value ha_refused=1\n"
    )
    assert health.panel_id == "test"
    assert health.ha_state is None
    assert health.ha_source is None
    assert health.ha_subscription_refused is True


def test_parser_accepts_current_android_network_suffix() -> None:
    """Current unconsumed Android health fields remain additive and harmless."""
    health = parse_health_response(
        "ha-paneld 0.9.7-rc3 panel=test_panel build=1725312345678 "
        "cfg=0123abcd ha=normal ha_net=healthy ha_resp=warning "
        "ha_net_p95=4200 ha_net_n=30 ha_net_miss=3 ha_net_age=4000\n"
    )

    assert health == PanelHealth(
        version="0.9.7-rc3",
        panel_id="test_panel",
        build="1725312345678",
        config_hash="0123abcd",
        ha_state="normal",
    )


def test_parser_accepts_the_bounded_panel_assistant_discovery_id() -> None:
    """The new Android identity is consumed only after its strict grammar passes."""
    discovery_id = "a" * 64

    health = parse_health_response(
        "ha-paneld 0.9.7-rc4 panel=test_panel build=1725312345678 "
        f"cfg=0123abcd did={discovery_id}\n"
    )

    assert health.discovery_id == discovery_id


@pytest.mark.parametrize("discovery_id", ["A" * 64, "a" * 63, "a" * 65, "not-a-id"])
def test_parser_rejects_malformed_panel_assistant_discovery_id(
    discovery_id: str,
) -> None:
    """Malformed identity tokens cannot reach the config flow or diagnostics."""
    with pytest.raises(InvalidResponseError):
        parse_health_response(
            "ha-paneld 0.9.7-rc4 panel=test_panel build=1725312345678 "
            f"cfg=0123abcd did={discovery_id}\n"
        )


def test_parser_accepts_metadata_boundaries_and_build_fallback() -> None:
    """The response envelope, version limit and Android fallback remain valid."""
    version = f"1.2.3-{'a' * 57}"
    health = parse_health_response(
        f"ha-paneld {version} panel=test build={version} cfg=0123abcd"
    )
    assert health == PanelHealth(version, "test", version, "0123abcd")

    legacy_panel_id = "a" * 469
    legacy_health = parse_health_response(
        f"ha-paneld 0.0.0 panel={legacy_panel_id} build=0 cfg=0123abcd"
    )
    assert legacy_health == PanelHealth("0.0.0", legacy_panel_id, "0", "0123abcd")

    maximum_install_time = str(2**63 - 1)
    timestamp_health = parse_health_response(
        f"ha-paneld 1.2.3 panel=test build={maximum_install_time} cfg=0123abcd"
    )
    assert timestamp_health.build == maximum_install_time


@pytest.mark.parametrize(
    "duplicate",
    [
        "panel=other",
        "build=5678",
        "cfg=89abcdef",
        f"did={'a' * 64} did={'a' * 64}",
        "ha=normal ha=starting",
        "ha_src=mqtt ha_src=socket",
        "ha_refused=1 ha_refused=1",
        "ha_net=healthy ha_net=warning",
    ],
)
def test_parser_rejects_duplicate_known_fields(duplicate: str) -> None:
    """Consumed fields cannot carry an order-dependent second value."""
    body = f"ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd {duplicate}"

    with pytest.raises(InvalidResponseError):
        parse_health_response(body)


@pytest.mark.parametrize("suffix", ["future", "future=", "=value"])
def test_parser_rejects_malformed_suffix_tokens(suffix: str) -> None:
    """Every additive suffix remains an explicit key-value token."""
    body = f"ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd {suffix}"

    with pytest.raises(InvalidResponseError):
        parse_health_response(body)


@pytest.mark.parametrize(
    "body",
    [
        "ha-paneld 1.2.3 panel=Test-Panel build=1234 cfg=0123abcd",
        f"ha-paneld 1.2.3 panel={'a' * 470} build=1234 cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test build=arbitrary cfg=0123abcd",
        f"ha-paneld 1.2.3 panel=test build=1.2.3-{'a' * 58} cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test build=9223372036854775808 cfg=0123abcd",
    ],
)
def test_parser_rejects_metadata_outside_android_bounds(body: str) -> None:
    """Registry-facing metadata must match the current Android producer bounds."""
    with pytest.raises(InvalidResponseError):
        parse_health_response(body)


@pytest.mark.parametrize(
    "version",
    ["1.2.3", "1.2.3-rc4", "1.2", "01.2.3", "0.9.8+abc", f"1.2.3-{'a' * 58}"],
)
def test_parser_accepts_any_version_name_the_producer_can_report(version: str) -> None:
    """A dev build from the signed feed names itself freely, and still connects.

    The build feed admits exactly this version-name shape, so a panel running
    one reports a name that is not SemVer. The browser installer has always
    accepted it; refusing it here made Home Assistant unable to read a panel it
    can already install.
    """
    health = parse_health_response(
        f"ha-paneld {version} panel=test build=1234 cfg=0123abcd"
    )

    assert health.version == version


@pytest.mark.parametrize(
    "version",
    ["1.2.3!", "1.2.3 ", "-1.2.3", "a" * 65, "", "1.2.3\u2014rc1"],
)
def test_parser_still_refuses_a_version_no_producer_can_report(version: str) -> None:
    """The union widened to the producer's bound, not past it."""
    with pytest.raises(InvalidResponseError):
        parse_health_response(f"ha-paneld {version} panel=test build=1234 cfg=0123abcd")


@pytest.mark.parametrize(
    "body",
    [
        "ha-paneld\t1.2.3 panel=test build=1234 cfg=0123abcd",
        "ha-paneld  1.2.3 panel=test build=1234 cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test\x7f build=1234 cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test build=1234\x1b cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd future=value\x00",
        "ha-paneld 1.2.3 panel=test build=1234 cfg=0123abcd\nsecond=line",
    ],
)
def test_parser_rejects_control_bearing_lines(body: str) -> None:
    """Control characters cannot reach HA diagnostics or registry metadata."""
    with pytest.raises(InvalidResponseError):
        parse_health_response(body)


@pytest.mark.parametrize(
    "body",
    [
        "ok",
        "other 1.2.3 panel=test build=1234 cfg=0123abcd",
        "ha-paneld 1.2.3 panel=test build=1234",
        "ha-paneld 1.2.3 panel=test build=1234 cfg=not-a-hash",
    ],
)
def test_reject_invalid_health(body: str) -> None:
    """Malformed or non-ha-paneld bodies do not establish identity."""
    with pytest.raises(InvalidResponseError):
        parse_health_response(body)


async def test_client_fetches_canonical_endpoint() -> None:
    """The client uses the versioned health route without redirects."""
    session = _session(body=HEALTH_FIXTURE.read_bytes())
    client = HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]

    health = await client.async_get_health()

    assert health.panel_id == "alpha"
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/health"
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {"Cache-Control": "no-cache"}


@pytest.mark.parametrize("status", [301, 403, 500])
async def test_client_rejects_non_success(status: int) -> None:
    """Only an HTTP 200 response validates a panel."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(status=status), normalize_address("panel.local")
    )
    with pytest.raises(CannotConnectError):
        await client.async_get_health()


async def test_client_maps_network_failure() -> None:
    """Network failures use the expected config-flow exception."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(error=ClientConnectionError()),
        normalize_address("panel.local"),
    )
    with pytest.raises(CannotConnectError):
        await client.async_get_health()


async def test_client_rejects_oversized_response() -> None:
    """The client refuses responses beyond the Android readiness bound."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(body=b"x" * 513), normalize_address("panel.local")
    )
    with pytest.raises(InvalidResponseError):
        await client.async_get_health()


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        ("0.9.10", "0.9.9", True),
        ("0.9.10", "0.9.10-rc3", True),
        ("0.9.10", "0.9.10", False),
        ("0.9.9", "0.9.10", False),
        ("0.9.10", "invalid", False),
        ("0.9.11-rc1", "0.9.10", False),
        ("invalid", "0.9.10", False),
    ],
)
def test_stable_update_comparison_never_offers_a_downgrade(
    candidate: str, installed: str, expected: bool
) -> None:
    """A stable target can replace the equivalent release candidate only."""
    assert is_newer_stable_version(candidate, installed) is expected


async def test_client_uses_the_cached_exact_tag_in_the_panel_update_request() -> None:
    """The integration sends only a cached Android-owned tag to the updater."""
    session = _session(body=b'{"status":"started"}')
    client = HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]

    await client.async_start_panel_update("v0.9.10")

    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/install/component"
    assert kwargs["data"] == {
        "name": "paneld",
        "action": "update",
        "version": "v0.9.10",
    }


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (200, b'{"status":"busy"}', UpdateBusyError),
        (200, b'{"status":"other"}', UpdateRejectedError),
        (
            202,
            b'{"error":"approval-required","approval_id":"opaque"}',
            UpdateApprovalRequiredError,
        ),
        (202, b'{"error":"other"}', UpdateRejectedError),
        (403, b'{"status":"denied"}', UpdateRejectedError),
    ],
)
async def test_client_rejects_nonstarted_panel_update(
    status: int, body: bytes, error: type[Exception]
) -> None:
    """Hardened-mode and busy outcomes never become a successful update start."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(status=status, body=body), normalize_address("panel.local")
    )

    with pytest.raises(error):
        await client.async_start_panel_update("v0.9.10")


@pytest.mark.parametrize("tag", ["", "tag/escape", "tag space", "x" * 65])
async def test_client_refuses_noncanonical_update_tags(tag: str) -> None:
    """Only an Android-validated release tag can reach the destructive endpoint."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(), normalize_address("panel.local")
    )

    with pytest.raises(InvalidResponseError):
        await client.async_start_panel_update(tag)


def test_parse_panel_install_status_keeps_only_progress_ownership() -> None:
    """Operation prose remains on the panel, outside Home Assistant state."""
    status = parse_panel_install_status(
        b'{"running":true,"component":"ha-paneld","message":"untrusted prose"}'
    )

    assert status.running is True
    assert status.component == "ha-paneld"


@pytest.mark.parametrize(
    "body",
    [
        b'{"running":true,"running":false,"component":"ha-paneld"}',
        b'{"running":true,"component":"ha-paneld","progress":NaN}',
        b'{"running":true,"component":"ha-paneld","progress":-Infinity}',
    ],
)
def test_panel_json_with_a_duplicate_key_or_non_finite_number_is_refused(
    body: bytes,
) -> None:
    """Order-dependent or non-standard panel JSON is refused, never resolved."""
    with pytest.raises(InvalidResponseError):
        parse_panel_install_status(body)


def test_status_parser_projects_every_device_card_fact() -> None:
    """The device projection fills the card and stays presentation-only."""
    status = parse_status_response(
        '{"warnings":[],"capabilities":[],"panel_assistant_device":'
        '{"name":"Alpha panel","manufacturer":"Acme","model":"AP-1",'
        '"hw_version":"Android 14 · TQ3A","area":"Study"}}'
    )

    device = status.panel_assistant_device
    assert device is not None
    assert device.name == "Alpha panel"
    assert device.manufacturer == "Acme"
    assert device.model == "AP-1"
    assert device.hw_version == "Android 14 · TQ3A"
    assert device.area == "Study"


@pytest.mark.parametrize("area", ["null", " NuLl "])
def test_status_parser_omits_legacy_literal_null_area(area: str) -> None:
    """A legacy panel's bad area never reaches cards or diagnostics."""
    body = json.dumps(
        {
            "warnings": [],
            "capabilities": [],
            "panel_assistant_device": {"name": "Alpha panel", "area": area},
        }
    )

    device = parse_status_response(body).panel_assistant_device
    assert device is not None
    assert device.name == "Alpha panel"
    assert device.area is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"warnings":[],"capabilities":[]}', None),
        ('{"warnings":[],"capabilities":[],"panel_assistant_device":{}}', None),
    ],
)
def test_status_parser_treats_a_silent_panel_as_no_device_facts(
    body: str, expected: None
) -> None:
    """Older panels omit the object and a bounded panel may have nothing safe to say."""
    assert parse_status_response(body).panel_assistant_device is expected


@pytest.mark.parametrize(
    "device",
    [
        {"serial_number": "abc"},
        {"name": ""},
        {"name": "   Alpha"},
        {"name": "Alpha   "},
        {"name": "a" * 129},
        {"name": "Two\nLines"},
        {"name": "Bell\u0007"},
        {"name": 17},
        {"name": None},
        {"manufacturer": ["Acme"]},
    ],
)
def test_status_parser_refuses_a_device_field_the_panel_should_have_dropped(
    device: dict[str, object],
) -> None:
    """The panel omits what it cannot state safely, so a bad value breaks contract."""
    body = json.dumps(
        {"warnings": [], "capabilities": [], "panel_assistant_device": device}
    )

    with pytest.raises(InvalidResponseError):
        parse_status_response(body)


@pytest.mark.parametrize(
    "cached_update",
    [
        {
            "state": "available",
            "current_version": "0.9.8",
            "target_version": "0.9.10",
            "tag": "v0.9.10",
        },
        {"state": "none"},
        {"state": "future", "tag": "bad/tag"},
    ],
)
def test_status_parser_ignores_the_retired_panel_update_projection(
    cached_update: dict[str, str],
) -> None:
    """A body from an app up to 0.9.8 that still offers its own update parses."""
    status = parse_status_response(
        json.dumps(
            {
                "warnings": [],
                "capabilities": [],
                "panel_assistant_update": cached_update,
            }
        )
    )

    assert status.warning_count == 0
    assert "panel_assistant_update" not in status.as_dict()


def test_parse_current_status_fixture_projects_only_safe_fields() -> None:
    """The current Android shape is retained without free-form or opaque data."""
    document = json.loads(STATUS_FIXTURE.read_text(encoding="utf-8"))
    document["database_observation_nonce"] = "0123456789abcdef0123456789abcdef"
    document["future_section"] = {"nested": {"value": "ignored"}}
    status = parse_status_response(json.dumps(document))
    diagnostics = status.as_dict()

    assert diagnostics["warning_count"] == 1
    assert diagnostics["capability_count"] == 2
    assert diagnostics["renderer"]["state"] == "rendered"  # type: ignore[index]
    assert diagnostics["storage_health"]["used_percent"] == 50.0  # type: ignore[index]
    assert diagnostics["camera"]["stream_port"] == 8554  # type: ignore[index]
    assert diagnostics["power_safety"]["reason_codes"] == [  # type: ignore[index]
        "doze_not_exempt",
        "stay_on_disabled",
    ]

    serialized = json.dumps(diagnostics)
    assert "192.0.2.10" not in serialized
    assert "192.0.2.11" not in serialized
    assert "rtsp://" not in serialized
    acknowledgement = "a" * 64
    assert acknowledgement not in serialized
    assert "0123456789abcdef0123456789abcdef" not in serialized
    assert "future_section" not in serialized
    for component in (
        "renderer",
        "storage_health",
        "camera",
        "power_safety",
    ):
        assert "summary" not in diagnostics[component]  # type: ignore[operator]
        assert "action" not in diagnostics[component]  # type: ignore[operator]


@pytest.mark.parametrize(
    "addition",
    [
        {},
        {"zigbee_gateway": None},
        {"zigbee_gateway": {"state": "healthy", "future": "ignored"}},
        {"storage_health": {"state": "healthy"}},
        {"power_safety": {"state": "safe"}},
        {"renderer": {"mode": "builtin", "state": "rendered"}},
        {"camera": {"state": "absent"}},
    ],
)
def test_status_parser_accepts_historical_additive_shapes(
    addition: dict[str, object],
) -> None:
    """Sections added after 0.9.0 remain optional and unknown fields are ignored."""
    document: dict[str, object] = {"warnings": [], "capabilities": []}
    document.update(addition)

    status = parse_status_response(json.dumps(document))

    assert status.warning_count == 0
    assert status.capability_count == 0


@pytest.mark.parametrize("capability", ["api", "none"])
def test_status_parser_retains_panel_install_capability(capability: str) -> None:
    """The update route reads a bounded panel decision, not display rows."""
    status = parse_status_response(
        json.dumps(
            {"warnings": [], "capabilities": [], "install_capability": capability}
        )
    )

    assert status.install_capability == capability
    assert status.as_dict()["install_capability"] == capability


def test_status_parser_treats_missing_install_capability_as_unknown() -> None:
    """Older panels never claim a route they did not report."""
    status = parse_status_response('{"warnings":[],"capabilities":[]}')

    assert status.install_capability is None


def test_status_parser_accepts_android_zigbee_and_signed_setting_bounds() -> None:
    """Current multicore CPU and signed OEM settings remain valid evidence."""
    document = {
        "warnings": [],
        "capabilities": [],
        "zigbee_gateway": {
            "state": "healthy",
            "role": "End Device",
            "gateway_cpu_percent": 1000,
            "guard_cpu_percent": 101,
        },
        "power_safety": {
            "state": "unknown",
            "screen_off_timeout_ms": -1,
            "stay_on_while_plugged_in": -1,
        },
    }

    diagnostics = parse_status_response(json.dumps(document)).as_dict()

    assert diagnostics["zigbee_gateway"]["gateway_cpu_percent"] == 1000  # type: ignore[index]
    assert diagnostics["zigbee_gateway"]["role"] == "End Device"  # type: ignore[index]
    assert diagnostics["power_safety"]["screen_off_timeout_ms"] == -1  # type: ignore[index]


@pytest.mark.parametrize(
    ("failure", "failure_operation", "auto_vacuum"),
    [
        ("busy", "catalog-maintenance", "incremental"),
        (None, None, "none"),
    ],
)
def test_status_parser_retains_storage_attribution_fields(
    failure: str | None, failure_operation: str | None, auto_vacuum: str
) -> None:
    """The failed operation and auto-vacuum mode reach storage diagnostics."""
    body = json.dumps(
        {
            "warnings": [],
            "capabilities": [],
            "storage_health": {
                "state": "database_failure" if failure else "healthy",
                "failure": failure,
                "failure_operation": failure_operation,
                "auto_vacuum": auto_vacuum,
            },
        }
    )

    storage = parse_status_response(body).as_dict()["storage_health"]

    assert storage["failure_operation"] == failure_operation  # type: ignore[index]
    assert storage["auto_vacuum"] == auto_vacuum  # type: ignore[index]


@pytest.mark.parametrize(
    ("component", "field"),
    [
        ("camera", "delivered_fps"),
        ("storage_health", "used_percent"),
    ],
)
def test_status_parser_rejects_huge_integer_numbers(component: str, field: str) -> None:
    """Bounded JSON integers cannot escape the invalid-response contract."""
    huge_integer = 10**400
    body = json.dumps(
        {
            "warnings": [],
            "capabilities": [],
            component: {"state": "ok", field: huge_integer},
        }
    )
    assert len(str(huge_integer)) == 401
    assert len(body.encode()) <= MAX_STATUS_RESPONSE_BYTES

    with pytest.raises(InvalidResponseError):
        parse_status_response(body)


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "{}",
        '{"warnings":{},"capabilities":[]}',
        '{"warnings":[],"capabilities":{}}',
        '{"warnings":[],"warnings":[],"capabilities":[]}',
        '{"warnings":[],"capabilities":[],"future":NaN}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"healthy","used_percent":1e999}}',
        '{"warnings":[],"capabilities":[],"camera":{"state":"live","clients":true}}',
        '{"warnings":[],"capabilities":[],"camera":{"state":"live","stream_port":70000}}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"ok","used_percent":101}}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"ok","failure_operation":"DROP;TABLE"}}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"ok","failure_operation":7}}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"ok","auto_vacuum":null}}',
        '{"warnings":[],"capabilities":[],"storage_health":{"state":"ok","auto_vacuum":"/data/db"}}',
        '{"warnings":[],"capabilities":[],"zigbee_gateway":{"state":"healthy","gateway_cpu_percent":1001}}',
        '{"warnings":[],"capabilities":[],"renderer":{"mode":"builtin","state":"https://panel.local"}}',
        '{"warnings":[],"capabilities":[],"power_safety":{"state":"ok","reason_codes":[false]}}',
        json.dumps(
            {
                "warnings": ["x" * (MAX_STATUS_WARNING_LENGTH + 1)],
                "capabilities": [],
            }
        ),
    ],
)
def test_status_parser_rejects_malformed_known_data(body: str) -> None:
    """Malformed JSON and invalid known fields never reach diagnostics."""
    with pytest.raises(InvalidResponseError):
        parse_status_response(body)


@pytest.mark.parametrize(
    ("field", "count"),
    [
        ("warnings", MAX_STATUS_WARNINGS + 1),
        ("capabilities", MAX_STATUS_CAPABILITIES + 1),
    ],
)
def test_status_parser_rejects_excessive_original_arrays(
    field: str, count: int
) -> None:
    """The original variable-length arrays have explicit entry limits."""
    document: dict[str, object] = {"warnings": [], "capabilities": []}
    value: object = "warning" if field == "warnings" else {"name": "capability"}
    document[field] = [value] * count

    with pytest.raises(InvalidResponseError):
        parse_status_response(json.dumps(document))


def test_status_parser_ignores_large_or_deep_unknown_fields() -> None:
    """Only the body cap and known-field bounds constrain additive data."""
    wide = {
        "warnings": [],
        "capabilities": [],
        "future": [None] * 512,
    }
    deep: dict[str, object] = {"value": "x" * 4096}
    for _index in range(50):
        deep = {"nested": deep}

    for document in (
        wide,
        {"warnings": [], "capabilities": [], "future": deep},
    ):
        status = parse_status_response(json.dumps(document))
        assert status.as_dict()["warning_count"] == 0


async def test_client_fetches_canonical_status_endpoint() -> None:
    """Status polling is a plain read with no refresh or nonce query."""
    session = _session(body=STATUS_FIXTURE.read_bytes())
    client = HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]

    status = await client.async_get_status()

    assert status.warning_count == 1
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/status"
    assert url.query_string == ""
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {"Cache-Control": "no-cache"}


async def test_client_requests_fresh_home_proof_only_when_asked() -> None:
    """Install completion pays for foreground observation on its own request."""
    body = (
        b'{"warnings":[],"capabilities":[],"home_ui":'
        b'{"state":"blocked","reason":"chooser","evidence":"resolver"}}'
    )
    session = _session(body=body)
    client = HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]

    status = await client.async_get_status(home_proof=True)

    assert status.home_ui == {
        "state": "blocked",
        "reason": "chooser",
        "evidence": "resolver",
    }
    assert session.requests
    url, _ = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/status?home_proof=1"


@pytest.mark.parametrize(
    "home_ui",
    [
        {"state": "ready", "reason": "activity"},
        {"state": "healthy", "reason": "activity", "evidence": "foreground"},
        {"state": "ready", "reason": "not a token", "evidence": "foreground"},
    ],
)
def test_status_rejects_incomplete_or_invalid_home_proof(home_ui: object) -> None:
    """A malformed positive proof cannot be silently interpreted as ready."""
    with pytest.raises(InvalidResponseError):
        parse_status_response(
            json.dumps({"warnings": [], "capabilities": [], "home_ui": home_ui})
        )


async def test_client_claims_the_panel_update_only_when_asked() -> None:
    """The owner header rides only on a status poll that claims the update."""
    session = _session(body=STATUS_FIXTURE.read_bytes())
    client = HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]

    await client.async_get_status(update_owner=True)

    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/status"
    assert kwargs["headers"] == {
        "Cache-Control": "no-cache",
        "X-Panel-Assistant-Update-Owner": "1",
    }


async def test_client_rejects_invalid_status_utf8() -> None:
    """Invalid UTF-8 is rejected before status parsing."""
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(body=b"\xff"), normalize_address("panel.local")
    )

    with pytest.raises(InvalidResponseError):
        await client.async_get_status()


async def test_client_rejects_oversized_valid_status_document() -> None:
    """A valid additive document beyond 64 KiB is rejected by the body guard."""
    assert MAX_STATUS_RESPONSE_BYTES == 64 * 1024
    body = json.dumps(
        {
            "warnings": [],
            "capabilities": [],
            "future": "x" * MAX_STATUS_RESPONSE_BYTES,
        }
    ).encode()
    assert len(body) > MAX_STATUS_RESPONSE_BYTES
    client = HaPaneldClient(  # type: ignore[arg-type]
        _session(body=body), normalize_address("panel.local")
    )

    with pytest.raises(InvalidResponseError):
        await client.async_get_status()


# --- maintainer build feed: diagnostics, backup, staged upload ---------------


_DIAG = "ha-paneld diagnostics — 0.9.7-rc3 (build 707)\n[captured] ...\n"
_SIGNER = "ac6193307fb0b70113aae205d7549406f96e063bc5491b67b1d5694a34b0e339"
_PREVIEW = {
    "ok": True,
    "token": "tok-1",
    "package": "io.github.maxlyth.hapaneld",
    "version": "0.9.7-rc4",
    "signer": _SIGNER,
}


def _client(session: FakeSession) -> HaPaneldClient:
    return HaPaneldClient(session, normalize_address("panel.local"))  # type: ignore[arg-type]


def test_parse_diag_version_reads_name_and_build() -> None:
    """The diagnostics header carries the running version name and build number."""
    assert parse_diag_version(_DIAG.encode()) == ("0.9.7-rc3", 707)
    assert parse_diag_version(_DIAG.replace("\n", "\r\n", 1).encode()) == (
        "0.9.7-rc3",
        707,
    )


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[captured] ...\n",
        "\nha-paneld diagnostics — 0.9.7-rc3 (build 707)\n".encode(),
        "other diagnostics — 0.9.7-rc3 (build 707)\n".encode(),
        b"ha-paneld diagnostics - 0.9.7-rc3 (build 707)\n",
        "ha-paneld diagnostics — 0.9.7-rc3 (build 0707)\n".encode(),
        "ha-paneld diagnostics — 0.9.7-rc3 (build 707) extra\n".encode(),
        "ha-paneld diagnostics — 0.9.7 rc3 (build 707)\n".encode(),
        b"\xff\xfe\n",
    ],
)
def test_parse_diag_version_refuses_other_first_lines(body: bytes) -> None:
    """A missing or non-matching first line is not a build number."""
    with pytest.raises(InvalidResponseError):
        parse_diag_version(body)


async def test_client_reads_the_version_code_from_diag() -> None:
    """The build number comes from the versioned diagnostics route."""
    session = _session(body=_DIAG.encode())

    assert await _client(session).async_get_version_code() == ("0.9.7-rc3", 707)
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/diag"
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("ready", [True, False])
async def test_client_reads_legacy_panel_install_privilege(ready: bool) -> None:
    """Older panels report the same privileged-route observation as `shot`."""
    session = _session(body=json.dumps({"shot": ready}).encode())

    assert await _client(session).async_get_legacy_install_capability() is ready
    assert session.requests
    assert str(session.requests[-1][0]) == "http://panel.local:8888/api/v1/info"


def test_parse_staged_apk_accepts_the_fixed_preview() -> None:
    """A preview is ok:true plus four printable strings."""
    staged = parse_staged_apk(json.dumps(_PREVIEW).encode())

    assert staged == StagedApk(
        token="tok-1",
        package="io.github.maxlyth.hapaneld",
        version="0.9.7-rc4",
        signer=_SIGNER,
    )


@pytest.mark.parametrize(
    "replacement",
    [
        {"ok": False},
        {"ok": "true"},
        {"ok": None},
        {"token": None},
        {"package": 7},
        {"version": ""},
        {"signer": "x" * 257},
        {"version": "0.9.7 rc4"},
        {"package": "io.githubé"},
        {"signer": "abc\x00"},
        {"token": "tok/1"},
    ],
)
def test_parse_staged_apk_refuses_anything_else(replacement: dict[str, Any]) -> None:
    """Missing, empty, long, non-printable or non-string fields are refused."""
    document = {**_PREVIEW, **replacement}
    document = {key: value for key, value in document.items() if value is not None}

    with pytest.raises(InvalidResponseError):
        parse_staged_apk(json.dumps(document).encode())


def test_parse_staged_apk_refuses_a_non_object() -> None:
    """Only a JSON object is a preview."""
    with pytest.raises(InvalidResponseError):
        parse_staged_apk(b"[]")


async def test_stage_uploads_raw_bytes_and_returns_the_preview() -> None:
    """The APK is posted as the raw request body to the staging route."""
    session = _session(body=json.dumps(_PREVIEW).encode())
    apk = b"PK\x03\x04apk"

    staged = await _client(session).async_stage_apk(apk)

    assert staged.token == "tok-1"
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/install/apk"
    assert kwargs["data"] == apk
    assert isinstance(kwargs["data"], bytes)
    assert kwargs["allow_redirects"] is False


async def test_migration_upload_binds_signed_digest_to_existing_staging_route() -> None:
    session = _session(body=json.dumps(_PREVIEW).encode())
    staged = await _client(session).async_stage_apk(b"apk", migration_sha256="a" * 64)
    assert staged.token == "tok-1"
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url.with_query(None)) == "http://panel.local:8888/api/v1/install/apk"
    assert dict(url.query) == {"migration": "successor", "sha256": "a" * 64}
    assert kwargs["data"] == b"apk"
    assert kwargs["allow_redirects"] is False


async def test_bridge_capability_is_read_from_the_panel() -> None:
    session = _session(
        body=b'{"package":"io.panelassistant.android","version":"0.9.10"}'
    )
    assert await _client(session).async_get_successor_capability() == (
        "io.panelassistant.android",
        "0.9.10",
        None,
        False,
    )
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/successor"
    assert kwargs["allow_redirects"] is False


async def test_bridge_reports_trusted_installed_successor_for_retry() -> None:
    session = _session(
        body=b'{"package":"io.panelassistant.android","version":"0.9.10","installed_version_code":2000}'
    )
    assert await _client(session).async_get_successor_capability() == (
        "io.panelassistant.android",
        "0.9.10",
        2000,
        False,
    )


async def test_bridge_reports_untrusted_installed_successor_for_refusal() -> None:
    session = _session(
        body=b'{"package":"io.panelassistant.android","version":"0.9.10","installed_untrusted":true}'
    )
    assert (await _client(session).async_get_successor_capability())[-1] is True


async def test_installed_only_retry_never_requests_a_download() -> None:
    session = _session(body=b'{"ok":true,"outcome":"Launched"}')
    await _client(session).async_offer_installed_successor()
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url.with_query(None)) == "http://panel.local:8888/api/v1/successor/offer"
    assert dict(url.query) == {"installed_only": "1"}
    assert kwargs["data"] == {}


@pytest.mark.parametrize(
    "body", [b"[]", b"null", b"{}", b"bad", b'{"package":7,"version":"v"}']
)
async def test_malformed_bridge_capability_is_refused(body: bytes) -> None:
    with pytest.raises(InvalidResponseError):
        await _client(_session(body=body)).async_get_successor_capability()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, UploadDisabledError),
        (503, StagingUnavailableError),
        (409, UpdateBusyError),
        (400, UpdateRejectedError),
        (507, UpdateRejectedError),
        (500, CannotConnectError),
    ],
)
async def test_stage_maps_panel_refusals(status: int, error: type[Exception]) -> None:
    """Each staging refusal keeps its own meaning."""
    with pytest.raises(Exception) as raised:
        await _client(_session(status=status, body=b"{}")).async_stage_apk(b"apk")

    assert type(raised.value) is error


async def test_commit_accepts_a_started_install() -> None:
    """Committing names only the staged token."""
    session = _session(body=b'{"status":"started"}')

    await _client(session).async_commit_apk("tok-1")

    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/install/apk/commit"
    assert kwargs["data"] == {"token": "tok-1"}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (200, b'{"status":"busy"}', UpdateBusyError),
        (
            202,
            b'{"error":"approval-required","approval_id":"opaque"}',
            UpdateApprovalRequiredError,
        ),
        (403, b"{}", UploadDisabledError),
        (400, b"{}", UpdateRejectedError),
        (500, b"{}", CannotConnectError),
    ],
)
async def test_commit_maps_panel_refusals(
    status: int, body: bytes, error: type[Exception]
) -> None:
    """Only a started install is success."""
    with pytest.raises(Exception) as raised:
        await _client(_session(status=status, body=body)).async_commit_apk("tok-1")

    assert type(raised.value) is error


async def test_discard_posts_the_token() -> None:
    """A discard names the staged token on the discard route."""
    session = _session(status=404, body=b"{}")

    await _client(session).async_discard_apk("tok-1")

    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/install/apk/discard"
    assert kwargs["data"] == {"token": "tok-1"}


async def test_backup_returns_bytes_and_allows_plaintext() -> None:
    """The backup is requested as a plaintext archive and returned verbatim."""
    session = _session(body=b"PK\x03\x04zip")

    assert await _client(session).async_backup_panel() == b"PK\x03\x04zip"
    assert session.requests
    url, kwargs = session.requests[-1]
    assert str(url) == "http://panel.local:8888/api/v1/backup"
    assert kwargs["data"]["allow_plaintext"] == "1"
    assert kwargs["data"] == {"allow_plaintext": "1", "include_companion": "false"}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (
            202,
            b'{"error":"approval-required","approval_id":"opaque"}',
            UpdateApprovalRequiredError,
        ),
        (409, b"{}", UpdateBusyError),
        (400, b"{}", UpdateRejectedError),
        (500, b"{}", CannotConnectError),
        (200, b"", CannotConnectError),
    ],
)
async def test_backup_maps_panel_refusals(
    status: int, body: bytes, error: type[Exception]
) -> None:
    """Only a non-empty 200 is a backup."""
    with pytest.raises(Exception) as raised:
        await _client(_session(status=status, body=body)).async_backup_panel()

    assert type(raised.value) is error


def test_the_health_line_carries_the_application_id_the_panel_runs() -> None:
    """Both apps are one build, so the reported package is what tells them apart."""
    base = "ha-paneld 0.9.8 panel=landing build=812 cfg=01234567"
    for package in ("io.github.maxlyth.hapaneld", "io.panelassistant.android"):
        assert parse_health_response(f"{base} pkg={package}\n").package == package
    # A build older than the migration reports none, and is not rejected for it.
    assert parse_health_response(f"{base}\n").package is None
    # Checked for shape only: an unfamiliar id must not make the whole line
    # unreadable, because that would take the panel offline over one token.
    assert parse_health_response(f"{base} pkg=com.example.other\n").package == (
        "com.example.other"
    )
    for malformed in ("pkg=notapackage", "pkg=.leading", "pkg=com..double"):
        with pytest.raises(InvalidResponseError):
            parse_health_response(f"{base} {malformed}\n")
    # One token, once: a second pkg= is a contradictory line.
    with pytest.raises(InvalidResponseError):
        parse_health_response(f"{base} pkg=io.panelassistant.android pkg=a.b\n")


def test_the_health_line_carries_the_build_number_beside_the_version() -> None:
    """Two builds of one release differ only in their build number."""
    base = "ha-paneld 0.9.8-rc2 panel=landing build=1790 cfg=01234567"
    assert parse_health_response(f"{base} pkg=a.b vc=904\n").version_code == 904
    # A build older than the token reports none, and is not rejected for it.
    assert parse_health_response(f"{base}\n").version_code is None
    # Presentation only: an unusable value is dropped, never the whole line.
    for malformed in ("vc=0", "vc=-1", "vc=09", "vc=abc", "vc=2147483648"):
        assert parse_health_response(f"{base} {malformed}\n").version_code is None
    assert parse_health_response(f"{base} vc=2147483647\n").version_code == 2**31 - 1
    # One token, once: a second vc= is a contradictory line.
    with pytest.raises(InvalidResponseError):
        parse_health_response(f"{base} vc=904 vc=905\n")


async def test_health_read_remembers_the_actual_http_peer(socket_enabled: None) -> None:
    """A completed short HTTP response still supplies legacy hostname evidence."""
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    async def health(request: web.Request) -> web.Response:
        return web.Response(body=HEALTH_FIXTURE.read_bytes())

    app = web.Application()
    app.router.add_get("/api/v1/health", health)
    async with TestServer(app, host="127.0.0.1") as server, ClientSession() as session:
        client = HaPaneldClient(session, normalize_address(f"127.0.0.1:{server.port}"))
        await client.async_get_health()
        assert client.health_peer == (str(client.health_url), "127.0.0.1")


@pytest.mark.parametrize(
    "status,body,error",
    [
        (200, b"\xff\xd8fresh\xff\xd9", None),
        (403, b"refused", CannotConnectError),
        (200, b"x" * (2 * 1024 * 1024 + 1), InvalidResponseError),
    ],
    ids=["fresh-jpeg", "panel-refusal", "oversized-response"],
)
async def test_camera_snapshot_is_fresh_bounded_and_refuses_redirects(
    status: int,
    body: bytes,
    error: type[Exception] | None,
) -> None:
    """Camera media uses the same bounded current-panel HTTP authority as health."""
    session = _session(status=status, body=body)
    client = _client(session)
    if error is not None:
        with pytest.raises(error):
            await client.async_get_camera_snapshot()
    else:
        assert await client.async_get_camera_snapshot() == body
    assert session.requests
    assert session.requests[-1][1]["allow_redirects"] is False
    assert session.requests[-1][0].path == "/api/v1/camera/snapshot.jpg"

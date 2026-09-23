"""The vendored transport contract: conformance, and English for every surface."""

import ast
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol

from custom_components.panel_assistant import release, transport
from custom_components.panel_assistant.contract import CONTRACT, catalogue_entry
from custom_components.panel_assistant.native import NOT_RENDERED

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "panel_assistant"
VECTORS = json.loads(
    (
        Path(__file__).parent / "fixtures" / "panel_assistant_transport_v1_vectors.json"
    ).read_text(encoding="utf-8")
)
ANDROID_PRODUCER = json.loads(
    (Path(__file__).parent / "fixtures" / "android_producer_v1.json").read_text(
        encoding="utf-8"
    )
)
ENGLISH: dict[str, Any] = json.loads(
    (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
)
SCHEMAS = {
    transport.COMMAND_HELLO: transport.HELLO_SCHEMA,
    transport.COMMAND_REPORT_STATE: transport.REPORT_STATE_SCHEMA,
    transport.COMMAND_REPORT_EVENT: transport.REPORT_EVENT_SCHEMA,
    transport.COMMAND_COMMAND_RESULT: transport.COMMAND_RESULT_SCHEMA,
}

_HA_VECTOR_REVISION = "f8f3bc883ebfa82ac957706dad363be4b42a1010"
_ANDROID_PRODUCER_REVISION = "2836b4e9863c876b53536ddcea8f93b135e378bd"


def test_shared_vectors_name_the_ha_source_revision_vendored_by_android() -> None:
    """The HA-authored vectors name the source revision Android vendors."""
    assert VECTORS["sourceRevision"] == _HA_VECTOR_REVISION


def test_reply_vectors_carry_the_actual_parser_inputs() -> None:
    """Kotlin receives each result together with the capabilities it offered."""
    for vector in VECTORS["results"]:
        offered = vector["offeredCapabilities"]
        assert isinstance(offered, list)
        assert len(offered) == len(set(offered))
        if "request" in vector:
            assert offered == vector["request"]["capabilities"]


def test_android_producer_fixture_names_its_exact_source_revision() -> None:
    """The consumer pin names the committed Android source that produced it."""
    assert ANDROID_PRODUCER["sourceRevision"] == _ANDROID_PRODUCER_REVISION


def test_android_install_descriptors_pass_the_independent_release_parser() -> None:
    """Real producer output crosses the signed-release trust boundary unchanged."""
    for document in ANDROID_PRODUCER["installDescriptors"]:
        body = (
            json.dumps(
                document, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
            + "\n"
        ).encode("ascii")
        descriptor = release._parse_install_descriptor(
            body,
            tag=document["releaseTag"],
            apk_name=document["apkName"],
            apk_sha256=document["apkSha256"],
        )
        assert descriptor.version_code == document["versionCode"]
        assert descriptor.package_id == document["packageId"]


@pytest.mark.parametrize(
    "vector",
    ANDROID_PRODUCER["transportMessages"],
    ids=lambda vector: vector["name"],
)
def test_real_android_transport_messages_pass_ha_schemas(
    vector: dict[str, Any],
) -> None:
    """The independent HA boundary accepts messages made by the real producer."""
    message = vector["message"]
    validated = SCHEMAS[message["type"]](message)
    assert "id" not in validated
    if message["type"] == transport.COMMAND_HELLO:
        local_digest = hashlib.sha256(
            (INTEGRATION / "panel_assistant_transport_v1.json").read_bytes()
        ).hexdigest()
        assert message["contract_digest"] != local_digest


def _descriptor(entry: dict[str, Any], index: int = 1) -> dict[str, Any]:
    """Return the validated descriptor a panel sends for a catalogue entry."""
    family = entry["family"]
    fields = {
        key: value
        for key, value in entry.items()
        if key not in ("value", "attributes", "color_modes")
    }
    if family is not None:
        fields |= {
            "channel": f"{family}{index}",
            "index": index,
            "unique_suffix": entry["unique_suffix"].format(index=index),
        }
    if entry["platform"] == "event":
        fields["options"] = ["keycode_home"]
    descriptor: dict[str, Any] = transport.DESCRIPTOR_SCHEMA(fields)
    return descriptor


def _entry(channel: str) -> dict[str, Any]:
    for entry in CONTRACT["channels"]:
        if channel in (entry["channel"], f"{entry['family']}1"):
            return entry  # type: ignore[no-any-return]
    raise AssertionError(channel)


def test_contract_code_lists_are_the_integrations_own() -> None:
    """Python holds no second copy of a code list that disagrees with the file."""
    assert CONTRACT["protocol"] == {
        "min": transport.PROTOCOL_MIN,
        "max": transport.PROTOCOL_MAX,
    }
    assert set(CONTRACT["commands"]) == set(SCHEMAS)
    assert CONTRACT["sync"] == [
        transport.SYNC_FULL_BEGIN,
        transport.SYNC_DELTA,
        transport.SYNC_FULL_END,
    ]
    assert CONTRACT["observation_states"] == [
        transport.STATE_KNOWN,
        transport.STATE_UNAVAILABLE,
    ]
    assert set(CONTRACT["hello_errors"]) == {
        transport.ERR_PROTOCOL_UNSUPPORTED,
        transport.ERR_UNKNOWN_PANEL,
        transport.ERR_PANEL_USER_MISMATCH,
        transport.ERR_PANEL_IDENTITY_UNAVAILABLE,
        transport.ERR_ENTRY_REMOVED,
    }
    # Reserved: a removal is a hello refusal, never a session end.
    assert "entry_removed" in CONTRACT["session_closed_reasons"]
    assert set(CONTRACT["request_errors"]) == {
        transport.ERR_SESSION_UNKNOWN,
        transport.ERR_UNKNOWN_CHANNEL,
        transport.ERR_INVALID_VALUE,
    }
    assert {
        transport.REASON_SUPERSEDED,
        transport.REASON_ENTRY_UNLOADED,
        transport.REASON_USER_REMOVED,
        transport.REASON_BINDING_CHANGED,
        transport.REASON_AUTHORITY_CHANGED,
    } <= set(CONTRACT["session_closed_reasons"])
    assert CONTRACT["authorities"] == list(transport.AUTHORITIES)
    assert set(transport.AUTHORITY_GRANTS) == set(transport.AUTHORITIES)
    assert (
        frozenset().union(*transport.AUTHORITY_GRANTS.values())
        <= transport.KNOWN_CAPABILITIES
    )
    assert CONTRACT["outcomes"] == list(transport.OUTCOMES)
    assert set(CONTRACT["outcomes"]) > transport.OUTCOMES_WITH_CODE
    assert set(CONTRACT["outcome_codes"]) == transport.OUTCOME_CODES
    assert CONTRACT["max_family_index"] == transport.MAX_FAMILY_INDEX
    assert {entry["platform"] for entry in CONTRACT["channels"]} == set(
        transport.PLATFORMS
    )


@pytest.mark.parametrize(
    "entry", CONTRACT["channels"], ids=lambda entry: entry["translation_key"]
)
def test_every_catalogue_entry_is_a_known_descriptor(entry: dict[str, Any]) -> None:
    """A descriptor built from the catalogue validates and resolves to its entry."""
    indices = [1, CONTRACT["max_family_index"]] if entry["family"] else [1]
    for index in indices:
        assert catalogue_entry(_descriptor(entry, index)) is entry
    if entry["family"]:
        with pytest.raises(vol.Invalid):
            _descriptor(entry, CONTRACT["max_family_index"] + 1)


@pytest.mark.parametrize(
    "change",
    [
        {"platform": "binary_sensor"},
        {"translation_key": "other"},
        {"unique_suffix": "other"},
        {"channel": "other"},
    ],
)
def test_a_known_channel_in_another_shape_is_unknown(change: dict[str, str]) -> None:
    """The channel, platform, key and suffix must all match the catalogue."""
    descriptor = _descriptor(_entry("relay1")) | change
    assert catalogue_entry(descriptor) is None
    family = _descriptor(_entry("relay1")) | {"channel": "relay2"}
    assert catalogue_entry(family) is None


_SAMPLES: dict[str, list[Any]] = {
    "number": [21.5],
    "option": [],
    "timestamp": ["2026-09-14T08:00:00+00:00"],
    "text": ["192.0.2.4"],
}


@pytest.mark.parametrize(
    "entry",
    [entry for entry in CONTRACT["channels"] if entry["platform"] == "sensor"],
    ids=lambda entry: entry["translation_key"],
)
def test_sensor_values_are_typed_as_the_catalogue_declares(
    entry: dict[str, Any],
) -> None:
    """The validator reads the descriptor exactly as the catalogue's value type."""
    descriptor = _descriptor(entry)
    validate = transport._VALUE_VALIDATORS["sensor"]
    kind = entry["value"]
    samples = {**_SAMPLES, "option": list(entry["options"] or ())}
    for accepted in samples[kind]:
        assert validate(accepted, descriptor) == accepted
    for other_kind, values in samples.items():
        if other_kind == kind or (kind == "text" and other_kind == "number"):
            continue
        for value in values:
            # A text sensor carries any bounded string, including a timestamp.
            if kind == "text" and isinstance(value, str):
                continue
            with pytest.raises(transport.ValueRejected):
                validate(value, descriptor)


@pytest.mark.parametrize(
    "vector", VECTORS["messages"], ids=lambda vector: vector["name"]
)
def test_message_conformance_vectors(vector: dict[str, Any]) -> None:
    """Each vector message validates, or fails, exactly as the vector says."""
    message = vector["message"]
    schema = SCHEMAS[message["type"]]
    if vector["valid"]:
        schema(message)
    else:
        with pytest.raises(vol.Invalid):
            schema(message)


def _embed_key(value: Any) -> str:
    """Return one wire-format embed key, or reject it like the panel does."""
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise vol.Invalid("invalid embed key")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except ValueError as err:
        raise vol.Invalid("invalid embed key") from err
    if len(decoded) != 32:
        raise vol.Invalid("invalid embed key")
    return value


def _hello_result_conforms(
    result: dict[str, Any], offered_capabilities: list[str]
) -> None:
    """Check a hello result as a panel reads it, raising on what it refuses.

    A panel granted ``mqtt_withdraw`` requires a valid ``mqtt_discovery``; one
    not granted it, which is what an older integration answers, does without.
    """
    integration = vol.Schema({vol.Required("version"): str}, extra=vol.ALLOW_EXTRA)
    channels = vol.Schema(
        {
            vol.Required("accepted"): int,
            vol.Required("unknown"): [str],
        },
        extra=vol.ALLOW_EXTRA,
    )
    embed = vol.Schema(
        {
            vol.Required("key_id"): vol.Match(r"[0-9a-f]{16}"),
            vol.Required("key"): _embed_key,
        },
        extra=vol.ALLOW_EXTRA,
    )
    vol.Schema(
        {
            vol.Required("protocol"): vol.All(
                int, vol.Range(transport.PROTOCOL_MIN, transport.PROTOCOL_MAX)
            ),
            vol.Required("session"): transport._session_token,
            vol.Required("authority"): vol.In(transport.AUTHORITIES),
            vol.Required("capabilities"): [vol.In(transport.KNOWN_CAPABILITIES)],
            vol.Optional("mqtt_discovery"): vol.In(transport.MQTT_DISCOVERIES),
            vol.Optional("embed"): object,
            vol.Required("integration"): integration,
            vol.Required("channels"): channels,
        },
        extra=vol.ALLOW_EXTRA,
    )(result)
    if not set(result["capabilities"]) <= set(offered_capabilities):
        raise vol.Invalid(
            "the integration granted a capability the panel did not offer"
        )
    if (
        transport.CAPABILITY_MQTT_WITHDRAW in result["capabilities"]
        and "mqtt_discovery" not in result
    ):
        raise vol.Invalid("a granted mqtt_withdraw needs mqtt_discovery")
    if transport.CAPABILITY_EMBED_PROOF in result["capabilities"]:
        if "embed" not in result:
            raise vol.Invalid("an embed grant needs its key")
        embed(result["embed"])


@pytest.mark.parametrize(
    "vector", VECTORS["results"], ids=lambda vector: vector["name"]
)
def test_hello_result_conformance_vectors(vector: dict[str, Any]) -> None:
    """Each hello result vector is read, or refused, exactly as it says."""
    if "request" in vector:
        transport.HELLO_SCHEMA(vector["request"])
    if vector["valid"]:
        _hello_result_conforms(vector["result"], vector["offeredCapabilities"])
    else:
        with pytest.raises(vol.Invalid):
            _hello_result_conforms(vector["result"], vector["offeredCapabilities"])


def test_the_hello_result_vectors_cover_the_negotiated_withdrawal() -> None:
    """A granted withdrawal with its answer is among the vectors."""
    assert any(
        vector["valid"]
        and transport.CAPABILITY_MQTT_WITHDRAW in vector["result"]["capabilities"]
        and transport.CAPABILITY_MQTT_WITHDRAW in vector["request"]["capabilities"]
        and vector["result"]["mqtt_discovery"] == transport.MQTT_DISCOVERY_WITHDRAW
        for vector in VECTORS["results"]
    )


@pytest.mark.parametrize(
    "vector", VECTORS["observations"], ids=lambda vector: vector["name"]
)
def test_observation_conformance_vectors(vector: dict[str, Any]) -> None:
    """Each vector value is accepted, or rejected, for its catalogue channel."""
    descriptor = _descriptor(_entry(vector["channel"]))
    validate = transport._VALUE_VALIDATORS[descriptor["platform"]]
    if vector["accepted"]:
        validate(vector["value"], descriptor)
    else:
        with pytest.raises(transport.ValueRejected):
            validate(vector["value"], descriptor)


# ---------------------------------------------------------------------------
# English text for every surface this integration ships.


@pytest.mark.parametrize(
    "entry",
    [entry for entry in CONTRACT["channels"] if entry["channel"] not in NOT_RENDERED],
    ids=lambda entry: entry["translation_key"],
)
def test_every_rendered_channel_has_english_text(entry: dict[str, Any]) -> None:
    """Name, option states and attribute names resolve under the entity tree."""
    node = ENGLISH["entity"].get(entry["platform"], {}).get(entry["translation_key"])
    assert isinstance(node, dict)
    name = node.get("name")
    assert isinstance(name, str) and name.strip()
    assert ("{index}" in name) == (entry["family"] is not None)
    for code in entry["options"] or ():
        if entry["platform"] == "light":
            states = node.get("state_attributes", {}).get("effect", {}).get("state", {})
        else:
            states = node.get("state", {})
        assert str(states.get(code, "")).strip(), code
    for attribute in entry["attributes"]:
        names = node.get("state_attributes", {}).get(attribute, {})
        assert str(names.get("name", "")).strip(), attribute


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            constants[target.id] = value.value
    # Constants that alias another constant, such as an issue key naming an error.
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(
            getattr(node, "value", None), ast.Name
        ):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            source = node.value
            assert isinstance(source, ast.Name)
            if isinstance(target, ast.Name) and source.id in constants:
                constants[target.id] = constants[source.id]
    return constants


def _resolve(node: ast.expr, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _raised_translation_keys() -> dict[str, set[str]]:
    """Return every translation key the integration raises or reports, by category."""
    keys: dict[str, set[str]] = {"exceptions": set(), "issues": set()}
    for path in sorted(INTEGRATION.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name == "_update_error" and node.args:
                key = _resolve(node.args[0], constants)
                assert key is not None, f"{path.name}:{node.lineno}"
                keys["exceptions"].add(key)
                continue
            for keyword in node.keywords:
                if keyword.arg != "translation_key":
                    continue
                if name == "send_error":
                    # Codes the panel renders from its own catalogue.
                    continue
                key = _resolve(keyword.value, constants)
                if key is None and isinstance(keyword.value, ast.Name):
                    # A helper's own parameter; its call sites are collected above.
                    continue
                assert key is not None, f"{path.name}:{node.lineno}"
                category = "issues" if name == "async_create_issue" else "exceptions"
                keys[category].add(key)
    return keys


def test_every_raised_exception_and_issue_has_english_text() -> None:
    """A translated error or Repairs issue can never ship without its words."""
    keys = _raised_translation_keys()

    assert "authority_mismatch" in keys["exceptions"]
    assert "health_update_failed" in keys["exceptions"]
    assert "update_busy" in keys["exceptions"]
    assert keys["issues"] == {
        "cutover_incomplete",
        "cutover_blocked_by_customised_entities",
        "merged_panel_identity",
        "merged_mqtt_device",
        "panel_update_required",
        "panel_migration_incomplete",
    }
    # Which binding issue is raised depends on whether the panel already has an
    # account, so that call passes a variable and the scan above sees no literal.
    # The tuple it chooses from is the whole set, and each one still needs words.
    issues = keys["issues"] | set(transport.BINDING_ISSUES)
    assert "panel_user_mismatch" in issues
    assert "panel_awaiting_confirmation" in issues
    for key in keys["exceptions"]:
        assert str(ENGLISH["exceptions"].get(key, {}).get("message", "")).strip(), key
    for key in issues:
        assert str(ENGLISH["issues"].get(key, {}).get("title", "")).strip(), key


# Codes a native command raises, held in variables the scan above cannot follow.
COMMAND_ERROR_KEYS = sorted(
    {*CONTRACT["outcome_codes"], "panel_unavailable", "approval_pending"}
)


def test_command_error_keys_are_the_integrations_own() -> None:
    """The translated command errors are the outcome codes and two of its own."""
    assert set(COMMAND_ERROR_KEYS) == transport.COMMAND_ERRORS
    assert {
        transport.ERR_PANEL_UNAVAILABLE,
        transport.ERR_APPROVAL_PENDING,
        transport.ERR_AUTHORITY_MISMATCH,
        transport.ERR_NOT_COMMANDABLE,
    } <= transport.COMMAND_ERRORS


@pytest.mark.parametrize("key", COMMAND_ERROR_KEYS)
def test_every_command_error_has_english_text(key: str) -> None:
    """A command outcome can never raise an untranslated error."""
    assert str(ENGLISH["exceptions"].get(key, {}).get("message", "")).strip(), key


def test_the_options_flow_has_text_in_english() -> None:
    """The authority step, its abort and every choice resolve in English."""
    options = ENGLISH["options"]
    step = options["step"]["transport"]
    assert step["title"].strip() and step["description"].strip()
    assert step["data"]["authority"].strip()
    assert options["abort"]["native_entities_disabled"].strip()
    choices = ENGLISH["selector"]["authority"]["options"]
    assert sorted(choices) == sorted(transport.AUTHORITIES)
    assert all(label.strip() for label in choices.values())


def test_every_repairs_step_and_abort_has_english_text() -> None:
    """The binding fix flow's steps and aborts resolve under its issue."""
    tree = ast.parse((INTEGRATION / "repairs.py").read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    steps = {
        node.name.removeprefix("async_step_")
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("async_step_")
        and node.name != "async_step_init"
    }
    aborts = {value for name, value in constants.items() if name.startswith("ABORT_")}
    # Each fix flow's steps and aborts resolve under its own issue, so a step
    # added to one flow cannot be satisfied by another flow's text.
    flows = {
        "panel_user_mismatch": (
            {"confirm_bind", "confirm_rebind"},
            {"entry_removed", "user_unavailable"},
        ),
        "panel_migration_incomplete": ({"confirm_migration"}, {"migration_unfinished"}),
    }

    assert steps == {step for owned, _ in flows.values() for step in owned}
    assert aborts == {reason for _, owned in flows.values() for reason in owned}
    for issue, (owned_steps, owned_aborts) in flows.items():
        flow = ENGLISH["issues"][issue]["fix_flow"]
        assert set(flow["step"]) == owned_steps
        assert set(flow["abort"]) == owned_aborts
        for step in owned_steps:
            assert flow["step"][step]["description"].strip()
        for reason in owned_aborts:
            assert flow["abort"][reason].strip()

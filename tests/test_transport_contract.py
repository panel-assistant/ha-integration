"""The vendored transport contract: conformance, and English for every surface."""

import ast
import json
from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol

from custom_components.panel_assistant import transport
from custom_components.panel_assistant.contract import CONTRACT, catalogue_entry
from custom_components.panel_assistant.native import NOT_RENDERED

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "panel_assistant"
VECTORS = json.loads(
    (
        Path(__file__).parent / "fixtures" / "panel_assistant_transport_v1_vectors.json"
    ).read_text(encoding="utf-8")
)
ENGLISH: dict[str, Any] = json.loads(
    (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
)
SCHEMAS = {
    transport.COMMAND_HELLO: transport.HELLO_SCHEMA,
    transport.COMMAND_REPORT_STATE: transport.REPORT_STATE_SCHEMA,
    transport.COMMAND_REPORT_EVENT: transport.REPORT_EVENT_SCHEMA,
}


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
    assert set(CONTRACT["commands"]) >= set(SCHEMAS)
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
    }
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
    } <= set(CONTRACT["session_closed_reasons"])
    assert {transport.AUTHORITY_MQTT, transport.AUTHORITY_SHADOW} <= set(
        CONTRACT["authorities"]
    )
    assert transport.SERVED_CAPABILITIES <= transport.KNOWN_CAPABILITIES
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
    assert keys["issues"] == {"panel_user_mismatch"}
    for key in keys["exceptions"]:
        assert str(ENGLISH["exceptions"].get(key, {}).get("message", "")).strip(), key
    for key in keys["issues"]:
        assert str(ENGLISH["issues"].get(key, {}).get("title", "")).strip(), key


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
    flow = ENGLISH["issues"]["panel_user_mismatch"]["fix_flow"]

    assert steps == {"confirm_bind", "confirm_rebind"}
    assert aborts == {"entry_removed", "user_unavailable"}
    for step in steps:
        assert flow["step"][step]["description"].strip()
    for reason in aborts:
        assert flow["abort"][reason].strip()

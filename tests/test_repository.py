"""Repository and distribution contract tests."""

import ast
import importlib.util
import json
import re
import string
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from custom_components.panel_assistant import app_identity
from custom_components.panel_assistant.client import parse_health_response

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "panel_assistant"
RELEASE_VERSION_SCRIPT = ROOT / ".github" / "scripts" / "verify_release_version.py"
PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
MARKDOWN_LINK = re.compile(
    r"(?<!\\)(?P<image>!?)\[[^\]\r\n]*\]\((?P<target>[^()\r\n]+)\)"
)
FROZEN_TRANSLATION_TOKENS = (
    "ha-paneld",
    "Panel Assistant",
    "CPU",
    "LED",
    "SoC",
    "Wi-Fi",
    "WebView",
    "Zigbee",
    "MQTT",
    "Home Assistant",
    "Android Debug Bridge (ADB)",
    "Android",
    "ADB",
    "HTTP",
    "IPv4",
    "IPv6",
    "APK",
    "DNS",
    "IP",
    "RC",
    "SDK",
    "ABI",
    "SHA-256",
    "URL",
    "v0.9.7-rc3",
    "8888",
    "5555",
)
TIER_A_LANGUAGES = ("de", "es", "fr", "it", "zh-Hans")
TIER_B_LANGUAGES = ("nl", "pl", "uk")
# These product names are protected only where they name the product/mode.
NATIVE_PRODUCT_LITERALS = {
    ("entity", "select", "companion_update_channel", "name"): "Companion",
    ("entity", "switch", "companion_auto_update", "name"): "Companion",
    ("entity", "update", "update_companion", "name"): "Companion",
    ("exceptions", "refused_hardened", "message"): "Hardened",
}
# These visible strings still use English fallback in Tier B. Keep the exact
# debt explicit so a newly untranslated key cannot silently join the exemption.
TIER_B_ENGLISH_FALLBACK = {
    "entity.binary_sensor.auto_sleep_activity.name",
    "entity.binary_sensor.proximity.name",
    "entity.button.reboot.name",
    "entity.button.reload.name",
    "entity.camera.camera.name",
    "entity.event.button.name",
    "entity.image.camera_snapshot.name",
    "entity.light.button_led.name",
    "entity.light.buttons.name",
    "entity.light.led.name",
    "entity.light.led.state_attributes.effect.state.blink",
    "entity.light.led.state_attributes.effect.state.none",
    "entity.light.led.state_attributes.effect.state.pulse",
    "entity.light.led.state_attributes.effect.state.strobe",
    "entity.light.screen.name",
    "entity.media_player.media.name",
    "entity.number.volume.name",
    "entity.select.companion_update_channel.name",
    "entity.select.companion_update_channel.state.prerelease",
    "entity.select.companion_update_channel.state.stable",
    "entity.select.cpu_governor.name",
    "entity.select.cpu_governor.state.auto",
    "entity.select.cpu_governor.state.efficiency",
    "entity.select.cpu_governor.state.performance",
    "entity.select.navbar.name",
    "entity.select.navbar.state.always_on",
    "entity.select.navbar.state.native",
    "entity.select.navbar.state.off",
    "entity.select.navbar.state.swipe_reveal",
    "entity.select.update_channel.name",
    "entity.select.update_channel.state.prerelease",
    "entity.select.update_channel.state.stable",
    "entity.sensor.diag_boot.name",
    "entity.sensor.diag_cpu.name",
    "entity.sensor.diag_ip.name",
    "entity.sensor.diag_memory.name",
    "entity.sensor.diag_soc_temp.name",
    "entity.sensor.diag_wifi_outages_24h.name",
    "entity.sensor.diag_wifi_outages_24h.state_attributes.is_lower_bound.name",
    "entity.sensor.diag_wifi_rssi.name",
    "entity.sensor.diag_wifi_ssid.name",
    "entity.sensor.humidity.name",
    "entity.sensor.illuminance.name",
    "entity.sensor.proximity_level.name",
    "entity.sensor.room_humidity.name",
    "entity.sensor.room_temp.name",
    "entity.sensor.status.state.restarting_reboot",
    "entity.sensor.status.state.restarting_recovery",
    "entity.sensor.status.state.restarting_settings",
    "entity.sensor.status.state.restarting_update",
    "entity.sensor.storage_health.name",
    "entity.sensor.storage_health.state.critical",
    "entity.sensor.storage_health.state.database_failure",
    "entity.sensor.storage_health.state.healthy",
    "entity.sensor.storage_health.state.unchecked",
    "entity.sensor.storage_health.state.warning",
    "entity.sensor.storage_health.state_attributes.auto_vacuum.name",
    "entity.sensor.storage_health.state_attributes.checked_at_epoch_seconds.name",
    "entity.sensor.storage_health.state_attributes.database_files_bytes.name",
    "entity.sensor.storage_health.state_attributes.database_sidecar_bytes.name",
    "entity.sensor.storage_health.state_attributes.failure_category.name",
    "entity.sensor.storage_health.state_attributes.failure_operation.name",
    "entity.sensor.storage_health.state_attributes.freelist_count.name",
    "entity.sensor.storage_health.state_attributes.main_database_bytes.name",
    "entity.sensor.storage_health.state_attributes.page_count.name",
    "entity.sensor.storage_health.state_attributes.page_size_bytes.name",
    "entity.sensor.storage_health.state_attributes.quick_check.name",
    "entity.sensor.storage_health.state_attributes.schema_version.name",
    "entity.sensor.storage_health.state_attributes.storage_pressure.name",
    "entity.sensor.storage_health.state_attributes.total_bytes.name",
    "entity.sensor.storage_health.state_attributes.usable_bytes.name",
    "entity.sensor.storage_health.state_attributes.used_percent.name",
    "entity.sensor.storage_health.state_attributes.wal_bytes.name",
    "entity.sensor.temperature.name",
    "entity.switch.auto_brightness.name",
    "entity.switch.auto_sleep.name",
    "entity.switch.camera_enabled.name",
    "entity.switch.companion_auto_update.name",
    "entity.switch.kiosk_lock.name",
    "entity.switch.network_adb.name",
    "entity.switch.prevent_idle_dim.name",
    "entity.switch.relay.name",
    "entity.switch.self_update.name",
    "entity.switch.silence_boot_chime.name",
    "entity.switch.touch_sound.name",
    "entity.switch.wake_on_wave.name",
    "entity.switch.watchdog.name",
    "entity.switch.webview_auto_update.name",
    "entity.switch.zigbee_router.name",
    "entity.text.home_dashboard.name",
    "entity.text.navigate.name",
    "entity.update.update_companion.name",
    "exceptions.approval_denied.message",
    "exceptions.approval_pending.message",
    "exceptions.approval_timeout.message",
    "exceptions.authority_mismatch.message",
    "exceptions.expired.message",
    "exceptions.failed.message",
    "exceptions.hardware_unavailable.message",
    "exceptions.invalid_value.message",
    "exceptions.not_commandable.message",
    "exceptions.panel_identity_conflict.message",
    "exceptions.panel_unavailable.message",
    "exceptions.refused_hardened.message",
    "exceptions.unknown_channel.message",
    "exceptions.unknown_command.message",
    "exceptions.voice_announcement_disconnected.message",
    "exceptions.voice_announcement_timeout.message",
    "exceptions.voice_assistant_off.message",
    "exceptions.voice_panel_not_connected.message",
    "exceptions.voice_wake_words_rejected.message",
}


def _translation_leaves(
    value: Any, prefix: tuple[str, ...] = ()
) -> dict[tuple[str, ...], str]:
    """Return every string leaf under its exact translation-key path."""
    if isinstance(value, str):
        return {prefix: value}
    assert isinstance(value, dict)
    leaves: dict[tuple[str, ...], str] = {}
    for key, child in value.items():
        assert isinstance(key, str)
        leaves.update(_translation_leaves(child, (*prefix, key)))
    return leaves


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting silently shadowed duplicate keys."""
    value: dict[str, Any] = {}
    for key, child in pairs:
        assert key not in value
        value[key] = child
    return value


def _load_translation_catalogue(path: Path) -> dict[str, Any]:
    """Load a catalogue through the strict JSON object boundary."""
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object
    )
    assert isinstance(value, dict)
    return value


def _translation_shape(value: Any) -> Any:
    """Return the complete object shape while discarding translated text."""
    if isinstance(value, str):
        return None
    assert isinstance(value, dict)
    return {key: _translation_shape(child) for key, child in value.items()}


def _placeholders(value: str) -> Counter[str]:
    """Return valid placeholders and reject every unmatched or malformed brace."""
    placeholders = PLACEHOLDER.findall(value)
    remainder = PLACEHOLDER.sub("", value)
    assert "{" not in remainder and "}" not in remainder
    return Counter(placeholders)


def _markdown_links(value: str) -> list[tuple[bool, str]]:
    """Return link kinds and targets, rejecting malformed Markdown delimiters."""
    links = [
        (bool(match.group("image")), match.group("target"))
        for match in MARKDOWN_LINK.finditer(value)
    ]
    assert value.count("](") == len(links)
    for opening, closing in (("[", "]"), ("(", ")")):
        depth = 0
        for character in value:
            if character == opening:
                depth += 1
            elif character == closing:
                depth -= 1
                assert depth >= 0
        assert depth == 0
    return links


def _literal_count(value: str, literal: str) -> int:
    """Protect literals from ASCII supersets while allowing adjacent CJK text."""
    return len(
        re.findall(rf"(?<![a-zA-Z0-9_]){re.escape(literal)}(?![a-zA-Z0-9_])", value)
    )


def _load_release_version_module():
    spec = importlib.util.spec_from_file_location(
        "verify_release_version", RELEASE_VERSION_SCRIPT
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_and_hacs_versions_match_repository_policy() -> None:
    """Distribution metadata has the deliberate independent version floor."""
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert manifest == {
        "codeowners": ["@maxlyth"],
        "after_dependencies": ["assist_pipeline"],
        "config_flow": True,
        "dependencies": [
            "http",
            "media_source",
            "panel_custom",
            "stream",
            "websocket_api",
        ],
        # Core buckets dhcp hostname matchers by first character.
        "dhcp": [{"hostname": f"{c}*"} for c in string.ascii_lowercase + string.digits],
        "documentation": "https://github.com/panel-assistant/ha-integration",
        "domain": "panel_assistant",
        "integration_type": "device",
        "iot_class": "local_polling",
        "issue_tracker": "https://github.com/panel-assistant/ha-integration/issues",
        "name": "Panel Assistant",
        "requirements": ["adb-shell[async]>=0.4.4"],
        "version": "0.7.1-rc1",
        "zeroconf": ["_adb._tcp.local.", "_ha-paneld._tcp.local."],
    }
    assert hacs == {"homeassistant": "2026.8.3", "name": "Panel Assistant"}


def test_release_version_guard_accepts_current_and_prerelease_versions(
    tmp_path: Path,
) -> None:
    """Tag admission uses exact raw equality for stable and prerelease versions."""
    verifier = _load_release_version_module()

    verifier.verify_release_version("0.7.1-rc1", INTEGRATION / "manifest.json")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version":"0.3.0b2"}', encoding="utf-8")
    verifier.verify_release_version("0.3.0b2", manifest)


@pytest.mark.parametrize(
    "version",
    ["1.0.0", "1.0.1", "1.2.0b0", "2.0.0b12", "0.9.0", "0.7.0-rc1", "1.0.0-rc12"],
)
def test_release_version_guard_accepts_semantic_versions(
    tmp_path: Path, version: str
) -> None:
    """Stable and beta releases share unpadded major.minor.patch numbering."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
    _load_release_version_module().verify_release_version(version, manifest)


@pytest.mark.parametrize(
    "version",
    [
        "1.0",
        "01.0.0",
        "1.00.0",
        "1.0.00",
        "1.0.0b",
        "1.0.0b01",
        "1.0.0-rc.1",
        "1.0.0-rc0",
        "1.0.0-rc01",
        "1.0.0rc1",
        "1.0.0-rc",
        "1.0.0-beta1",
        "1.0.0.dev0",
        "1.0.0+7",
        "1.0.0\n",
    ],
)
def test_release_version_guard_rejects_nonrelease_versions(
    tmp_path: Path, version: str
) -> None:
    """Exact equality alone must not admit malformed or development tags."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"major\.minor\.patch"):
        _load_release_version_module().verify_release_version(version, manifest)


@pytest.mark.parametrize(
    ("tag", "version", "message"),
    [
        ("v0.2.1", "0.2.1", "must not use a v prefix"),
        ("V0.2.1", "0.2.1", "must not use a v prefix"),
        ("0.1.1", "0.2.1", "does not match manifest version"),
        ("", "0.2.1", "release tag must be non-empty"),
        ("0.2.1", "", "manifest version must be a non-empty string"),
        ("0.2.1", 1, "manifest version must be a non-empty string"),
    ],
)
def test_release_version_guard_rejects_invalid_pairs(
    tmp_path: Path,
    tag: str,
    version: str | int,
    message: str,
) -> None:
    """Tag admission rejects prefixes, drift and malformed manifest versions."""
    verifier = _load_release_version_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": version}), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        verifier.verify_release_version(tag, manifest)


@pytest.mark.parametrize("tag", ["-h", "--help"])
def test_release_version_guard_cli_does_not_parse_tag_as_option(tag: str) -> None:
    """A valid option-shaped Git tag must reach the equality check and fail."""
    result = subprocess.run(
        [sys.executable, str(RELEASE_VERSION_SCRIPT), "--", tag],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "does not match manifest version '0.7.1-rc1'" in result.stderr


def test_hacs_workflow_runs_release_version_guard_for_tags() -> None:
    """The HACS workflow checks a tag before invoking external validation."""
    workflow = (ROOT / ".github" / "workflows" / "hacs.yml").read_text(encoding="utf-8")
    guard = 'run: python .github/scripts/verify_release_version.py -- "$RELEASE_TAG"'

    assert "if: startsWith(github.ref, 'refs/tags/')" in workflow
    assert "RELEASE_TAG: ${{ github.ref_name }}" in workflow
    assert workflow.index(guard) < workflow.index("name: Run HACS validation")


def test_repository_carries_exactly_one_manifest() -> None:
    """The HACS default-store checks walk the whole repository for *manifest.json.

    They refuse a repository holding more than one, so test fixtures must not
    store a component manifest under that name; the runtime harness renames the
    probe's on the way into its throwaway Core.
    """
    manifests = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*manifest.json")
        if ".venv" not in path.parts and "node_modules" not in path.parts
    )
    assert manifests == ["custom_components/panel_assistant/manifest.json"]
    assert (
        ROOT / "tests" / "runtime" / "probe_component" / "manifest.json.probe"
    ).is_file()


def test_hacs_repository_foundation() -> None:
    """Local files cover the HACS checks that do not require GitHub metadata."""
    integration_directories = sorted(
        path.name for path in (ROOT / "custom_components").iterdir() if path.is_dir()
    )

    assert integration_directories == ["panel_assistant"]
    assert (ROOT / "README.md").is_file()
    assert (
        (ROOT / "LICENSE")
        .read_text(encoding="utf-8")
        .startswith("Apache License\nVersion 2.0")
    )
    _assert_square_png(INTEGRATION / "brand" / "icon.png", 256)
    _assert_square_png(INTEGRATION / "brand" / "icon@2x.png", 512)


def _assert_square_png(path: Path, edge: int) -> None:
    """Home Assistant serves ``brand/`` images verbatim, so the sizes must be exact."""
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert raw[12:16] == b"IHDR"
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    assert (width, height) == (edge, edge)


def test_runtime_translations_are_complete() -> None:
    """The custom-component translation does not depend on Core placeholders."""
    strings = _load_translation_catalogue(INTEGRATION / "strings.json")
    english = _load_translation_catalogue(INTEGRATION / "translations" / "en.json")

    assert english == strings
    assert "[%key:" not in json.dumps(english)


def _subtree(catalogue: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    node: Any = catalogue
    for key in path:
        node = node.get(key, {})
    assert isinstance(node, dict)
    return node


def _without(catalogue: dict[str, Any], paths: set[tuple[str, ...]]) -> dict[str, Any]:
    """Return a copy of a catalogue with these subtrees and any emptied parents."""
    pruned: dict[str, Any] = json.loads(json.dumps(catalogue))
    for path in paths:
        if _subtree(pruned, path[:-1]).pop(path[-1], None) is None:
            continue
        for depth in range(len(path) - 1, 0, -1):
            if not _subtree(pruned, path[:depth]):
                _subtree(pruned, path[: depth - 1]).pop(path[depth - 1])
    return pruned


@pytest.mark.parametrize("language", ("en", *TIER_A_LANGUAGES, *TIER_B_LANGUAGES))
def test_shipped_translation_catalogues_preserve_machine_contracts(
    language: str,
) -> None:
    """Tier A is complete; Tier B's fixed fallback debt cannot grow unnoticed."""
    english_catalogue = _load_translation_catalogue(
        INTEGRATION / "translations" / "en.json"
    )
    english = _translation_leaves(english_catalogue)
    assert len(english) == 697
    assert {path.stem for path in (INTEGRATION / "translations").glob("*.json")} == {
        "en",
        *TIER_A_LANGUAGES,
        *TIER_B_LANGUAGES,
    }
    debt = (
        {tuple(key.split(".")) for key in TIER_B_ENGLISH_FALLBACK}
        if language in TIER_B_LANGUAGES
        else set()
    )
    assert debt <= english.keys()
    target_catalogue = _load_translation_catalogue(
        INTEGRATION / "translations" / f"{language}.json"
    )
    target = _translation_leaves(target_catalogue)
    assert english.keys() - target.keys() == debt, language
    assert _translation_shape(target_catalogue) == _translation_shape(
        _without(english_catalogue, debt)
    ), language
    assert all(value.strip() for value in target.values()), language
    assert all("[%key:" not in value for value in target.values()), language
    for key, target_text in target.items():
        source_text = english[key]
        assert _placeholders(target_text) == _placeholders(source_text), (language, key)
        assert _markdown_links(target_text) == _markdown_links(source_text), (
            language,
            key,
        )
        assert target_text.count("`") == source_text.count("`"), (language, key)
        for token in FROZEN_TRANSLATION_TOKENS:
            required_count = _literal_count(source_text, token)
            if required_count:
                assert _literal_count(target_text, token) >= required_count, (
                    language,
                    key,
                    token,
                )
        if token := NATIVE_PRODUCT_LITERALS.get(key):
            assert _literal_count(target_text, token) >= _literal_count(
                source_text, token
            ), (language, key, token)


@pytest.mark.parametrize("language", TIER_A_LANGUAGES)
@pytest.mark.parametrize("category", ("entity", "exceptions"))
async def test_home_assistant_serves_native_tier_a_translations(
    hass: HomeAssistant, language: str, category: str
) -> None:
    """Native names, states, attributes and errors reach HA without fallback."""
    source = _load_translation_catalogue(INTEGRATION / "translations" / "en.json")
    target = _load_translation_catalogue(
        INTEGRATION / "translations" / f"{language}.json"
    )
    prefix = f"component.panel_assistant.{category}."
    expected = {
        prefix + ".".join(path): text
        for path, text in _translation_leaves(target[category]).items()
    }
    assert _translation_shape(target[category]) == _translation_shape(source[category])
    actual = await async_get_translations(hass, language, category, {"panel_assistant"})
    assert actual == expected


# Recovery text tells a person what NOT to do to a panel that may be half
# installed. A translation that turns "do not uninstall or reset the app" into
# a positive order is a reversed safety instruction, and a sentence-level
# "contains a negation" check misses it because the first clause still says
# "do not retry". These patterns match the positive command form of each
# destructive verb in the machine-drafted locales; the prohibition forms
# (imperfective in Polish, "не" plus the verb in Ukrainian, "niet" after the
# verb in Dutch) do not match.
_POSITIVE_DESTRUCTIVE_COMMANDS = {
    "nl": r"\b(verwijder|reset)\b(?!.*\bniet\b)",
    "pl": r"\b(odinstalować|zresetować)\b",
    "uk": r"\b(видаліть|скиньте|деінсталюйте)\b",
}


def test_destructive_recovery_instructions_stay_prohibitions_in_drafted_locales() -> (
    None
):
    english = _translation_leaves(
        _load_translation_catalogue(INTEGRATION / "translations" / "en.json")
    )
    prohibited = {
        path
        for path, text in english.items()
        if re.search(r"\bDo not [^.]*\b(uninstall|reset)\b", text)
    }
    assert len(prohibited) == 8
    for locale, positive in _POSITIVE_DESTRUCTIVE_COMMANDS.items():
        target = _translation_leaves(
            _load_translation_catalogue(INTEGRATION / "translations" / f"{locale}.json")
        )
        for path in sorted(prohibited):
            for sentence in re.split(r"(?<=[.;!?])\s+", target[path]):
                assert not re.search(positive, sentence, re.IGNORECASE), (
                    locale,
                    path,
                    sentence,
                )


# Meaning a reviewer found lost in the machine drafts of refusal text: the
# installer refuses on a *signed release* (not a consent or permission), a retry
# is allowed *once*, and it is the *panel* that has not accepted this Home
# Assistant instance (not a committee or an example). Each pattern must be
# present in the translation of the string it guards.
_REFUSAL_MEANING = {
    ("config", "error", "install_plan_rejected"): {
        "nl": r"\brelease\b",
        "pl": r"wydani",
        "uk": r"випуск",
    },
    ("config", "error", "retained_or_ambiguous"): {
        "nl": r"één keer",
        "pl": r"\b(jeden|tylko) raz\b",
        "uk": r"один раз",
    },
    ("config", "error", "adb_still_unauthorized"): {
        "nl": r"\bPaneel\b.*\binstantie\b",
        "pl": r"\bPanel\b.*\binstancj",
        "uk": r"\bПанель\b.*\bекземпляр",  # noqa: RUF001 -- genuine Ukrainian
    },
}


def test_installer_refusal_text_keeps_its_meaning_in_drafted_locales() -> None:
    for path, patterns in _REFUSAL_MEANING.items():
        for locale, pattern in patterns.items():
            target = _translation_leaves(
                _load_translation_catalogue(
                    INTEGRATION / "translations" / f"{locale}.json"
                )
            )
            assert re.search(pattern, target[path], re.IGNORECASE | re.DOTALL), (
                locale,
                path,
                target[path],
            )


def test_installed_artifact_mismatch_is_explained_in_all_nine_locales() -> None:
    """The byte refusal must never fall back to an untranslated key."""
    locale_paths = sorted((INTEGRATION / "translations").glob("*.json"))
    assert len(locale_paths) == 9
    for locale_path in locale_paths:
        message = _load_translation_catalogue(locale_path)["config"]["error"][
            "installed_artifact_mismatch"
        ]
        assert message.strip(), locale_path.name


@pytest.mark.parametrize(
    "catalogue",
    [
        "strings.json",
        "translations/en.json",
        "translations/de.json",
        "translations/es.json",
        "translations/fr.json",
        "translations/it.json",
        "translations/nl.json",
        "translations/pl.json",
        "translations/uk.json",
        "translations/zh-Hans.json",
    ],
)
@pytest.mark.parametrize("step", ["confirm_install_candidate", "confirm_install_rc"])
def test_install_confirmation_keeps_each_fact_on_its_own_line(
    catalogue: str, step: str
) -> None:
    """A readable native confirmation must not collapse into one paragraph."""
    description = _load_translation_catalogue(INTEGRATION / catalogue)["config"][
        "step"
    ][step]["description"]
    fact_lines = [line for line in description.splitlines() if line.startswith("- ")]
    assert [_placeholders(line) for line in fact_lines] == [
        Counter({"{" + field + "}": 1})
        for field in (
            "address",
            "model",
            "serial",
            "abi",
            "sdk",
            "version",
            "tag",
            "sha256",
        )
    ]


def test_rc_selection_and_confirmation_are_keyed_and_explicit() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    steps = strings["config"]["step"]
    version = steps["choose_version"]
    warning = steps["confirm_install_rc"]["description"]
    assert version["data"] == {"release_candidate": "Version"}
    assert "Test versions may contain bugs" in version["description"]
    assert "not a stable release" in warning
    assert "may contain bugs" in warning
    assert "{version}" in warning and "{tag}" in warning and "{sha256}" in warning


def test_setup_starts_from_the_address_and_never_strands_a_running_panel() -> None:
    """One network path; a panel that already runs ha-paneld can connect or go back."""
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    steps = strings["config"]["step"]
    assert set(steps["user"]["menu_options"]) == {"add_panel", "install_usb"}
    assert steps["add_panel"]["data"] == {"address": "Panel hostname or IP address"}
    assert set(steps["found_panel"]["menu_options"]) == {"connect_found", "add_panel"}
    for removed in ("install_or_upgrade", "connect_existing", "confirm_existing"):
        assert removed not in steps


def test_runtime_harness_health_fixture_uses_production_grammar() -> None:
    """Keep the real-Core fixture admissible by the shipping health parser."""
    source = (ROOT / "tests" / "runtime" / "core_negative_harness.py").read_text(
        encoding="utf-8"
    )
    health_bodies = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, bytes)
        and node.value.startswith(b"ha-paneld ")
    ]

    assert len(health_bodies) == 1
    health = parse_health_response(health_bodies[0].decode("ascii"))
    assert health.panel_id == "runtime_negative"


def test_readme_links_to_hacs_and_panel_preparation() -> None:
    """The short introduction retains the links needed to try the integration."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "https://my.home-assistant.io/redirect/hacs_repository/"
        "?owner=panel-assistant&repository=ha-integration&category=integration"
    ) in readme
    assert "https://github.com/panel-assistant/ha-integration" in readme
    assert "https://panel-assistant.io/go/panel-access" in readme


def test_install_flow_copy_covers_first_time_handoffs() -> None:
    """Visible setup copy explains panel preparation, reattachment, and recovery."""
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    config = strings["config"]
    steps = config["step"]
    install = steps["add_panel"]["description"]
    progress = config["progress"]["installing"]
    all_errors = " ".join(config["error"].values())

    assert "network ADB on port 5555" in install
    assert "Root access is not required" in install
    assert "[model-specific panel access guide]({panel_access_url})" in install
    assert "https://" not in install
    assert "Leave this dialog open to finish automatically" in progress
    assert "Settings → Devices & services → Add integration" in progress
    assert "enter the same address" in progress
    assert "follow the browser setup link in the Add dialog" in progress
    assert "dedicated recovery workflow" not in all_errors

    abort = config["abort"]
    mapped_reasons = {
        "install_cancelled_after_staging_cleanup",
        "install_authorization_failed",
        "install_preflight_rejected",
        "install_artifact_rejected",
        "install_transport_failed",
        "install_package_failed",
        "install_launch_failed",
        "install_health_check_failed",
        "install_ambiguous_mutation",
        "install_verification_required",
    }
    assert mapped_reasons <= abort.keys()
    assert "stored ADB credential" in abort["install_authorization_failed"]
    assert "approval on the panel" not in abort["install_authorization_failed"]
    assert "connection, download or staging step" in abort["install_transport_failed"]
    assert (
        "could not complete or verify the launch step" in abort["install_launch_failed"]
    )
    assert "Do not retry automatic installation" in abort["install_launch_failed"]
    assert "Do not retry automatic installation" in abort["install_ambiguous_mutation"]


def test_the_address_screen_links_to_panel_specific_help() -> None:
    """A panel that will not answer needs somewhere to go, per model."""
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    description = strings["config"]["step"]["add_panel"]["description"]
    assert "[help for reaching a panel]({panel_help_url})" in description
    flow = (INTEGRATION / "config_flow.py").read_text(encoding="utf-8")
    assert '"panel_help_url": help_url("panel-unreachable"),' in flow


def test_outward_links_are_redirects_stamped_with_this_version() -> None:
    """Pages move; the site's redirect does not, and it is told who is asking."""
    from custom_components.panel_assistant.const import (
        INTEGRATION_BUILD,
        INTEGRATION_VERSION,
        help_url,
    )

    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == INTEGRATION_VERSION
    link = help_url("panel-unreachable")
    assert link.startswith("https://panel-assistant.io/go/panel-unreachable?")
    assert f"v={INTEGRATION_VERSION}" in link
    assert f"build={INTEGRATION_BUILD}" in link
    assert "model=TPA10" in help_url("panel-unreachable", model="TPA10")
    source = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    assert source.count("panel-assistant.io") == 1, "one place names the site"


def test_the_two_identity_modules_state_exactly_the_same_values() -> None:
    """Python and JavaScript each hold their own copy of the identity contract.

    They cannot import from one another, and every value in them is a literal
    on purpose: a component here is what a panel is actually sent, and one
    application id names classes in either Kotlin package depending on the
    build, so neither side may derive one. Literals in two files drift
    silently, and a drifted successor component names a class that does not
    exist on the panel.

    The two are compared by value, not by substring: `.MainActivity2` contains
    `.MainActivity`, so a membership test reads a drifted component as present.
    The JavaScript module is evaluated rather than parsed, so this sees what
    the installer will actually send.
    """
    module = (INTEGRATION / "frontend" / "src" / "app-identity.mjs").resolve()
    script = (
        f"import * as m from {json.dumps(module.as_uri())};"
        "console.log(JSON.stringify({"
        "accepted: m.ACCEPTED_PACKAGE_IDS,"
        "legacy: m.LEGACY_PACKAGE_ID,"
        "successor: m.SUCCESSOR_PACKAGE_ID,"
        "launch: m.LAUNCH_COMPONENTS,"
        "legacyLaunch: m.LEGACY_LAUNCH_COMPONENT,"
        "accessibility: Object.fromEntries(Object.values(m.LAUNCH_COMPONENTS).flat()"
        ".map(c => [c, m.accessibilityComponentFor(c)])),"
        "equivalent: Object.fromEntries(Object.values(m.LAUNCH_COMPONENTS).flat()"
        ".map(c => [c, m.accessibilityComponentsFor(c)]))"
        "}));"
    )
    javascript = json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )

    # The order is load-bearing: it fixes the PACKAGE{i}/RETAINED{i}/RESIDUE{i}
    # frame indices that both sides build and parse.
    assert javascript["accepted"] == list(app_identity.ACCEPTED_PACKAGE_IDS)
    assert javascript["legacy"] == app_identity.LEGACY_PACKAGE_ID
    assert javascript["successor"] == app_identity.SUCCESSOR_PACKAGE_ID
    assert javascript["launch"] == {
        package_id: list(components)
        for package_id, components in app_identity.LAUNCH_COMPONENTS.items()
    }
    assert javascript["legacyLaunch"] == app_identity.LEGACY_LAUNCH_COMPONENT
    # Both installers write the accessibility service into one device-wide list.
    assert javascript["accessibility"] == dict(app_identity.ACCESSIBILITY_COMPONENTS)
    assert javascript["equivalent"] == {
        package_id: list(spellings)
        for package_id, spellings in (
            app_identity.EQUIVALENT_ACCESSIBILITY_COMPONENTS.items()
        )
    }

    # Every launcher names its own id, and the shorthand appears only where the
    # class lives in that id's package: the old app's, never a successor's.
    for package_id, components in javascript["launch"].items():
        for component in components:
            assert component.startswith(f"{package_id}/")
            assert component.startswith(f"{package_id}/.") == (
                package_id == app_identity.LEGACY_PACKAGE_ID
            )

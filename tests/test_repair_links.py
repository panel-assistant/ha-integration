"""Repair links and short copy, as consumed by Home Assistant's frontend."""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueEntry
from homeassistant.helpers.translation import async_get_translations
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.const import DOMAIN


@pytest.mark.parametrize(
    ("key", "action"),
    [
        ("volume_number_removed", "automations_url"),
        ("panel_permissions", "permissions_url"),
        ("no_update_route", "recovery_url"),
        ("adb_update_authorization", "approval_url"),
        ("installer_failure", "repair_url"),
        *[
            (f"installer_failure_{reason}", "repair_url")
            for reason in (
                "authorization_failed",
                "preflight_rejected",
                "artifact_rejected",
                "transport_failed",
                "install_failed",
                "launch_failed",
                "health_check_failed",
                "ambiguous_mutation",
                "verification_required",
                "update",
                "adb_authorization",
                "retry_hold",
            )
        ],
    ],
)
async def test_saved_repairs_get_links_without_raising_the_fault_again(
    hass: HomeAssistant, key: str, action: str
) -> None:
    """An upgrade must also fix existing, possibly dismissed Repairs."""
    registry = ir.async_get(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "saved_repair",
        is_fixable=key != "volume_number_removed" and key != "no_update_route",
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders={
            "panel": "Kitchen",
            "entity": "number.old_volume",
            "users": "automation.evening",
        },
        data={"key": "saved_repair"},
    )
    before = registry.async_ignore(DOMAIN, "saved_repair", True)
    assert await async_setup_component(hass, DOMAIN, {})
    issue = registry.async_get_issue(DOMAIN, "saved_repair")
    assert issue is not None
    assert_repair_links(issue)
    assert issue.translation_placeholders[action]
    assert issue.created == before.created
    assert issue.dismissed_version == before.dismissed_version
    assert issue.data == before.data
    assert issue.is_fixable == before.is_fixable
    assert issue.severity == before.severity
    for name, value in before.translation_placeholders.items():
        assert issue.translation_placeholders[name] == value


def assert_repair_links(issue: IssueEntry) -> None:
    """Check an issue emitted by a real caller, including native flow help."""
    assert issue.learn_more_url
    url = urlsplit(issue.learn_more_url)
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "panel-assistant.io",
        "/go/repair",
    )
    query = parse_qs(url.query)
    assert query["issue"] == [issue.translation_key]
    assert query["v"] and query["build"]
    placeholders = issue.translation_placeholders or {}
    if issue.is_fixable:
        # Core merges registry placeholders into every form/menu/progress/abort.
        assert placeholders["learn_more_url"] == issue.learn_more_url
    for key, value in placeholders.items():
        if key.endswith("_url"):
            assert value.startswith(("/", "https://")), (key, value)


async def test_every_issue_has_a_repair_map_entry(hass: HomeAssistant) -> None:
    """Published issue keys must have their own documentation destination."""
    assert await async_setup_component(hass, DOMAIN, {})
    strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues."
    issues = {key.removeprefix(prefix).split(".")[0] for key in strings}
    # The bundled catalogue is the public issue-key contract. The pinned map is
    # refreshed from the site's topic table, just like the existing topic check.
    pinned = json.loads(
        (Path(__file__).parent / "fixtures/go_topics.json").read_text()
    ).get("repair", {})
    # Owner-requested removal links directly to the existing removal-risks topic.
    # Fault Repairs use /go/repair and require an issue-specific destination.
    # The unfinished-WebView wording of the update Repair shares its page.
    # Root-loss guidance lives in the Repair; its optional app status extension
    # uses the generic /go/repair destination until its dedicated page is added.
    fault_issues = issues - {
        "remove_from_panel",
        "installer_failure_update_webview",
        "installer_failure_update_slow_transfer",
        "panel_root_lost",
        # Uses the generic /go/repair destination until its page is added.
        "not_signed_in",
        "cannot_reach_home_assistant",
    }
    assert fault_issues and fault_issues == set(pinned)
    assert len(set(pinned.values())) == len(fault_issues)
    assert all("#" in destination for destination in pinned.values())


async def test_repair_descriptions_have_short_linked_fix_steps(
    hass: HomeAssistant,
) -> None:
    """The published English copy contract is rendered by Core's real loader."""
    assert await async_setup_component(hass, DOMAIN, {})
    strings = await async_get_translations(hass, "en", "issues", {DOMAIN})
    bodies = {
        key: value
        for key, value in strings.items()
        if key.endswith(".description")
        or ".fix_flow.progress." in key
        or ".fix_flow.abort." in key
    }
    assert bodies
    for key, text in bodies.items():
        steps = re.findall(r"^\d\. (.+)$", text, re.MULTILINE)
        # An owner-started action is a confirmation, rather than a panel fault.
        heading = "To remove" if ".issues.remove_from_panel." in key else "To fix"
        assert f"\n\n{heading}:\n" in text and 1 <= len(steps) <= 3, key
        assert re.search(r"\[[^]]+\]\([^)]+\)", steps[0]), key
        if ".fix_flow." in key:
            assert "[Learn more]({learn_more_url})" in text, key


@pytest.mark.parametrize(
    "language",
    ["cs", "de", "en", "es", "fr", "it", "nl", "pl", "pt-BR", "uk", "zh-Hans"],
)
async def test_retired_standalone_webview_repair_is_absent_from_core(
    hass: HomeAssistant, language: str
) -> None:
    """Only the combined update Repair owns unfinished browser-engine work."""
    assert await async_setup_component(hass, DOMAIN, {})
    strings = await async_get_translations(hass, language, "issues", {DOMAIN})
    prefix = f"component.{DOMAIN}.issues."
    assert strings
    assert not any(key.startswith(f"{prefix}panel_webview.") for key in strings)
    assert any(key.startswith(f"{prefix}installer_failure_update.") for key in strings)

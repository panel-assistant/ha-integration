"""Repairs retains removal custody until package absence is verified."""

from typing import Any

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.panel_assistant.app_identity import LEGACY_PACKAGE_ID, PACKAGE_ID
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.repairs_removal import (
    async_request_removal,
    removal_issue_id,
)

from .test_removal import FakePanel, _choose_removal, _entry, panel  # noqa: F401


@pytest.mark.parametrize(
    "fault",
    [
        "exit-error",
        "silent-error",
        "service-error",
        "stderr-service-error",
        "malformed",
        "legacy-exit-error",
        "legacy-silent-error",
        "legacy-service-error",
        "legacy-stderr-service-error",
        "none",
    ],
)
async def test_repair_requires_verified_package_absence(
    hass: HomeAssistant,
    panel: tuple[FakePanel, TestServer],  # noqa: F811
    hass_client: Any,
    fault: str,
) -> None:
    """A PM outage after uninstall cannot discard the owner's pending retry."""
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {})
    fake, server = panel
    if fault != "none":
        failed_package = (
            LEGACY_PACKAGE_ID if fault.startswith("legacy-") else PACKAGE_ID
        )
        fake.set(
            installed=[PACKAGE_ID, failed_package], fail_uninstall=[failed_package]
        )
        program = fake.directory / "fake_panel"
        code = program.read_text()
        # The same real shell now sees PM become unavailable after mutation.
        exit_code = 20 if "exit-error" in fault or "silent" in fault else 0
        for command in ('"pm path "*)', '"pm list "*)'):
            code = code.replace(
                command,
                command
                + '\n    if [ -e "$s/pm_broken" ]; then\n'
                + (
                    ""
                    if "silent" in fault
                    else "        echo invalid-output\n"
                    if fault == "malformed"
                    else "        echo 'Error: Could not access the Package Manager.'"
                    + (" >&2" if "exit-error" in fault or "stderr" in fault else "")
                    + "\n"
                )
                + f"        exit {exit_code}\n    fi",
            )
        code = code.replace(
            'rm "$s/fail_uninstall/$p";',
            'touch "$s/pm_broken"; rm "$s/fail_uninstall/$p";',
        )
        program.write_text(code)
    entry = _entry(hass, server)
    await async_request_removal(hass, entry)
    issue_id = removal_issue_id(entry.entry_id)
    admin = await hass_client()
    response = await admin.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue_id}
    )
    assert response.status == 200
    form = await _choose_removal(admin, await response.json())
    response = await admin.post(f"/api/repairs/issues/fix/{form['flow_id']}", json={})
    assert response.status == 200
    result = await response.json()
    await hass.async_block_till_done()

    if fault == "none":
        assert result["type"] == "create_entry"
        assert fake.installed == []
        assert hass.config_entries.async_get_entry(entry.entry_id) is None
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    else:
        assert result["type"] == "form"
        assert PACKAGE_ID in fake.installed
        if fault.startswith("legacy-"):
            assert LEGACY_PACKAGE_ID in fake.installed
            assert PACKAGE_ID not in fake.read()["stopped"]
        assert hass.config_entries.async_get_entry(entry.entry_id) is entry
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

"""Exercise HOME custody through the installer shell program."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from custom_components.panel_assistant.adb_protocol import _install_command
from custom_components.panel_assistant.previous_home import (
    snapshot_previous_home,
    transfer_previous_home,
)

# Panels own these files as root or the shell user; the private CI runs as root.
needs_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="needs root to own files as the panel does"
)


def _shell(
    program: str,
    tmp_path: Path,
    *,
    receiver_success: bool = True,
    query_response: str = (
        "io.panelassistant.android/.platform.PreservePreviousHomeReceiver"
    ),
    query_status: int = 0,
    package_status: int = 0,
    expected_status: int = 0,
    home: str = "com.vendor.launcher/com.vendor.launcher.OriginalHome",
) -> str:
    log = shlex.quote(str(tmp_path / "captured"))
    # Installing can invalidate Android's HOME preference. Only the pre-install
    # snapshot can supply this exact original component to the receiver.
    query = (
        'if [ "$1 $2" = "package query-receivers" ]; then '
        f"printf '%s\\n' {shlex.quote(query_response)}; return {query_status}; fi; "
    )
    prelude = (
        "cmd() { " + query + f"echo {shlex.quote(home)}; }}; "
        "pm() { cmd() { "
        + query
        + "echo android/ResolverActivity; }; echo Success; }; "
        # Real package summaries can omit an explicit receiver without filters.
        "dumpsys() { printf '%s\\n' 'Package [io.panelassistant.android]'; "
        f"return {package_status}; }}; "
        f"am() {{ printf '%s\\n' \"$*\" >> {log}; "
        f'echo "Broadcast completed: result={1 if receiver_success else 0}"; }}; '
    )
    result = subprocess.run(
        ["sh", "-c", prelude + program], capture_output=True, text=True, check=False
    )
    assert result.returncode == expected_status, result.stderr
    return result.stdout


@needs_root
def test_install_records_original_component_before_install_can_clear_home(
    tmp_path: Path,
) -> None:
    apk = str(tmp_path / "install.apk")
    output = _shell(_install_command("0" * 32, apk, 34), tmp_path)
    assert "HAPANELD_INSTALL_END:" + "0" * 32 + ":0" in output
    assert (tmp_path / "captured").is_file()
    assert (
        "--es previous_home_component "
        "com.vendor.launcher/com.vendor.launcher.OriginalHome"
        in (tmp_path / "captured").read_text()
    )
    assert not Path(apk + ".previous-home").exists()


@needs_root
def test_failed_transfer_keeps_original_for_retry(tmp_path: Path) -> None:
    apk = str(tmp_path / "install.apk")
    output = _shell(
        _install_command("0" * 32, apk, 34), tmp_path, receiver_success=False
    )
    assert "HAPANELD_INSTALL_END:" + "0" * 32 + ":1" in output
    record = Path(apk + ".previous-home")
    assert (
        record.read_text() == "com.vendor.launcher/com.vendor.launcher.OriginalHome\n"
    )
    _shell(transfer_previous_home(apk), tmp_path)
    assert not record.exists()
    assert (tmp_path / "captured").read_text().count(
        "previous_home_component com.vendor.launcher/com.vendor.launcher.OriginalHome"
    ) == 2


@needs_root
def test_capture_retry_never_replaces_existing_original(tmp_path: Path) -> None:
    apk = str(tmp_path / "install.apk")
    record = Path(apk + ".previous-home")
    record.write_text("com.original/com.original.Home\n")
    record.chmod(0o600)
    _shell(snapshot_previous_home(apk), tmp_path)
    assert record.read_text() == "com.original/com.original.Home\n"


def test_capture_refuses_symlink_without_touching_target(tmp_path: Path) -> None:
    apk = str(tmp_path / "install.apk")
    target = tmp_path / "unrelated"
    target.write_text("preserve me")
    Path(apk + ".previous-home").symlink_to(target)
    output = _shell(_install_command("0" * 32, apk, 34), tmp_path)
    assert "Success" not in output
    assert target.read_text() == "preserve me"


def _snapshot(carrier: str, apk: str) -> str:
    if carrier == "network":
        return snapshot_previous_home(apk)
    source = (
        Path(__file__).parents[1]
        / "custom_components/panel_assistant/frontend/src/previous-home.mjs"
    )
    script = (
        f"import {{snapshotPreviousHome}} from {json.dumps(source.as_uri())}; "
        f"console.log(JSON.stringify(snapshotPreviousHome({json.dumps(apk)})));"
    )
    return json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


@pytest.mark.parametrize("carrier", ["network", "usb"])
def test_snapshot_refuses_a_symlinked_record(tmp_path: Path, carrier: str) -> None:
    apk = str(tmp_path / "install.apk")
    target = tmp_path / "unrelated"
    target.write_text("preserve me")
    Path(apk + ".previous-home").symlink_to(target)
    _shell(_snapshot(carrier, apk), tmp_path, expected_status=1)
    assert target.read_text() == "preserve me"


@pytest.mark.parametrize("carrier", ["network", "usb"])
@pytest.mark.parametrize(
    "home",
    [
        "io.panelassistant.android/.DashboardActivity",
        "io.github.maxlyth.hapaneld/.DashboardActivity",
        "android/com.android.internal.app.ResolverActivity",
        "com.android.settings/.FallbackHome",
    ],
)
def test_snapshot_never_records_a_home_that_cannot_be_handed_back(
    tmp_path: Path, carrier: str, home: str
) -> None:
    apk = str(tmp_path / "install.apk")
    _shell(_snapshot(carrier, apk), tmp_path, home=home)
    assert Path(apk + ".previous-home").read_text() == "none\n"


@needs_root
def test_usb_install_preserves_original_home_before_install_can_clear_it(
    tmp_path: Path,
) -> None:
    """The USB shell transfers the original HOME after package install resets it."""
    source = (
        Path(__file__).parents[1]
        / "custom_components/panel_assistant/frontend/src/install-contract.mjs"
    )
    script = (
        f"import {{buildInstall}} from {json.dumps(source.as_uri())}; "
        f"console.log(JSON.stringify(buildInstall('{'0' * 32}', '{'1' * 32}', 34)));"
    )
    actual = json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    apk = tmp_path / ("ha-paneld-install-" + "1" * 32 + ".apk")
    output = _shell(actual.replace("/data/local/tmp", str(tmp_path)), tmp_path)
    assert "HAPANELD_INSTALL_END:" + "0" * 32 + ":0" in output
    assert (tmp_path / "captured").is_file()
    assert (
        "--es previous_home_component "
        "com.vendor.launcher/com.vendor.launcher.OriginalHome"
        in (tmp_path / "captured").read_text()
    )
    assert not Path(str(apk) + ".previous-home").exists()


@needs_root
def test_cancelled_job_discards_only_its_snapshot(tmp_path: Path) -> None:
    from custom_components.panel_assistant.previous_home import discard_previous_home

    apk = str(tmp_path / "install.apk")
    _shell(snapshot_previous_home(apk), tmp_path)
    unrelated = tmp_path / "another-job.previous-home"
    unrelated.write_text("keep")
    _shell(discard_previous_home(apk), tmp_path)
    assert not Path(apk + ".previous-home").exists()
    assert unrelated.read_text() == "keep"


@needs_root
def test_usb_retry_cannot_launch_before_original_home_is_durable(
    tmp_path: Path,
) -> None:
    source = (
        Path(__file__).parents[1]
        / "custom_components/panel_assistant/frontend/src/install-contract.mjs"
    )
    job_id = "1" * 32
    script = (
        f"import {{buildLaunch}} from {json.dumps(source.as_uri())}; "
        "console.log(JSON.stringify(buildLaunch('" + "0" * 32 + "', "
        "'io.panelassistant.android', "
        "'io.panelassistant.android/io.panelassistant.android.MainActivity', 34, '"
        + job_id
        + "')));"
    )
    program = json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    apk = str(tmp_path / ("ha-paneld-install-" + job_id + ".apk"))
    _shell(snapshot_previous_home(apk), tmp_path)
    output = _shell(
        program.replace("/data/local/tmp", str(tmp_path)),
        tmp_path,
        receiver_success=False,
    )
    assert "HAPANELD_LAUNCH_END:" + "0" * 32 + ":1" in output
    assert "start -W" not in (tmp_path / "captured").read_text()
    assert Path(apk + ".previous-home").exists()


@needs_root
@pytest.mark.parametrize(
    "query_response,query_status,package_status,legacy",
    [
        (
            "io.panelassistant.android/.platform.PreservePreviousHomeReceiver",
            0,
            0,
            False,
        ),
        ("No receivers found", 0, 0, True),
        ("No receivers found", 1, 0, False),
        ("", 0, 0, False),
        ("Unknown command: query-receivers", 0, 0, False),
        ("No receivers found", 0, 1, False),
    ],
)
@pytest.mark.parametrize("carrier", ["network", "usb"])
def test_failed_capture_requires_direct_receiver_absence_before_discarding_custody(
    tmp_path: Path,
    query_response: str,
    query_status: int,
    package_status: int,
    legacy: bool,
    carrier: str,
) -> None:
    apk = str(tmp_path / "install.apk")
    record = Path(apk + ".previous-home")
    record.write_text("com.original/com.original.Home\n")
    record.chmod(0o600)
    program = transfer_previous_home(apk)
    if carrier == "usb":
        source = (
            Path(__file__).parents[1]
            / "custom_components/panel_assistant/frontend/src/previous-home.mjs"
        )
        script = (
            f"import {{transferPreviousHome}} from {json.dumps(source.as_uri())}; "
            f"console.log(JSON.stringify(transferPreviousHome({json.dumps(apk)})));"
        )
        program = json.loads(
            subprocess.run(
                ["node", "--input-type=module", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
    _shell(
        program,
        tmp_path,
        receiver_success=False,
        query_response=query_response,
        query_status=query_status,
        package_status=package_status,
        expected_status=0 if legacy else 1,
    )
    assert record.exists() is not legacy
    if not legacy:
        assert record.read_text() == "com.original/com.original.Home\n"

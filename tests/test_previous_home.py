"""Exercise HOME custody through the installer shell program."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from custom_components.panel_assistant.adb_protocol import _install_command
from custom_components.panel_assistant.previous_home import (
    snapshot_previous_home,
    transfer_previous_home,
)


def _shell(program: str, tmp_path: Path, *, receiver_success: bool = True) -> str:
    log = shlex.quote(str(tmp_path / "captured"))
    # Installing can invalidate Android's HOME preference. Only the pre-install
    # snapshot can supply this exact original component to the receiver.
    prelude = (
        "cmd() { echo com.vendor.launcher/com.vendor.launcher.OriginalHome; }; "
        "pm() { cmd() { echo android/ResolverActivity; }; echo Success; }; "
        "dumpsys() { printf '%s\\n' 'Package [io.panelassistant.android]' "
        "'PreservePreviousHomeReceiver'; }; "
        f"am() {{ printf '%s\\n' \"$*\" >> {log}; "
        f'echo "Broadcast completed: result={1 if receiver_success else 0}"; }}; '
    )
    result = subprocess.run(
        ["sh", "-c", prelude + program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


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


def test_usb_and_network_install_use_identical_custody_program() -> None:
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
    assert actual == _install_command(
        "0" * 32, "/data/local/tmp/ha-paneld-install-" + "1" * 32 + ".apk", 34
    )


def test_cancelled_job_discards_only_its_snapshot(tmp_path: Path) -> None:
    from custom_components.panel_assistant.previous_home import discard_previous_home

    apk = str(tmp_path / "install.apk")
    _shell(snapshot_previous_home(apk), tmp_path)
    unrelated = tmp_path / "another-job.previous-home"
    unrelated.write_text("keep")
    _shell(discard_previous_home(apk), tmp_path)
    assert not Path(apk + ".previous-home").exists()
    assert unrelated.read_text() == "keep"


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

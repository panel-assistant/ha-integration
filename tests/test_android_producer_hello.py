"""Recorded Android hello acceptance and explicit cross-repository admission.

Run live parity before admission (ordinary pytest stays offline)::

    python tests/test_android_producer_hello.py --android-repository PATH \
        --android-ref integration/100

The ref is read from Git, never from a potentially dirty Android checkout.
"""

import argparse
import asyncio
import base64
import copy
import json
import subprocess
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "android_producer_v1.json"
ANDROID_FIXTURE = (
    "app/src/test/resources/panel-assistant-contract/android_producer_v1.json"
)
# Integration commit whose committed export was copied, not the export's older
# sourceRevision (which identifies the producer implementation).
ANDROID_INTEGRATION_REVISION = "723f96bce2121f721ad7fc7e8d46c5b2cc89867d"


def check_android_producer_parity(repository: Path, ref: str) -> str:
    """Refuse admission when the named Android ref exports different bytes."""
    revision = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        text=True,
    ).strip()
    exported = subprocess.check_output(
        ["git", "-C", str(repository), "show", f"{revision}:{ANDROID_FIXTURE}"]
    )
    if exported != FIXTURE.read_bytes():
        raise ValueError(f"Android producer fixture differs at {ref} ({revision})")
    return revision


async def test_recorded_android_hello_grants_authenticated_local_shell(
    hass: Any,
    hass_ws_client: Any,
    hass_read_only_user: Any,
    hass_read_only_access_token: str,
) -> None:
    """The unchanged producer hello opens the local command channel on the wire."""
    from custom_components.panel_assistant import local_adb

    from .test_native import _setup
    from .test_transport import _receive
    from .test_transport_commands import Panel

    entry = await _setup(hass, hass_read_only_user.id)
    document = json.loads(FIXTURE.read_bytes())
    hello = copy.deepcopy(
        next(
            vector["message"]
            for vector in document["transportMessages"]
            if vector["message"]["type"] == "panel_assistant/hello"
        )
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(hello)
    response = await _receive(client)
    assert response["success"], response
    panel = Panel(client, response)
    assert "local_adb" in panel.result["capabilities"]
    task = asyncio.create_task(local_adb.async_shell(hass, entry, "id", 1000))
    event = (await _receive(client))["event"]
    assert event["kind"] == "manage"
    assert event["op"] == "adb.shell"
    assert event["args"] == {"command": "id", "timeout_ms": 1000}
    output = b"uid=0(root)\n"
    await panel.answer(
        event["command_id"],
        "applied",
        result={
            "stdout_b64": base64.b64encode(output).decode(),
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
        },
    )
    assert await task == output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-repository", required=True, type=Path)
    parser.add_argument("--android-ref", default="integration/100")
    args = parser.parse_args()
    print(check_android_producer_parity(args.android_repository, args.android_ref))

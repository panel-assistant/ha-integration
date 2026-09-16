"""The sidebar's proof, held to the vectors the panel is tested against."""

import base64
import hmac
import json
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.panel_assistant import embed_proof

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "panel_assistant_embed_v1.json").read_text(
        encoding="utf-8"
    )
)
PROOF: dict[str, Any] = VECTORS["proof"]
KEY: dict[str, str] = PROOF["key"]
GRAMMAR = re.compile(
    r"^v1;k=[0-9a-f]{16};n=[1-9][0-9]{0,18};u=[0-9a-f]{32};m=[A-Za-z0-9_-]{43}$"
)


def _key() -> bytes:
    return base64.urlsafe_b64decode(KEY["key"] + "=" * (-len(KEY["key"]) % 4))


def _body(request: dict[str, Any]) -> bytes:
    if "body_repeat" in request:
        repeat = request["body_repeat"]
        return bytes(repeat["byte"], "utf-8") * repeat["count"]
    body: str = request["body"]
    return body.encode("utf-8")


class ReplayWindow:
    """The panel's replay window: the highest counter and the ones seen below it."""

    def __init__(self, bits: int) -> None:
        self._bits = bits
        self._highest: int | None = None
        self._seen: set[int] = set()

    def accept(self, counter: int) -> bool:
        if self._highest is None or counter > self._highest:
            self._highest = counter
            self._seen = {n for n in self._seen if n > counter - self._bits}
        elif counter <= self._highest - self._bits or counter in self._seen:
            return False
        self._seen.add(counter)
        return True


def _verify(
    header: str | list[str],
    request: dict[str, Any],
    *,
    key_state: str = "held",
    accepted_before: list[int] | None = None,
) -> str:
    """Verify a proof as the panel does, and return its reason or ``accepted``."""
    if isinstance(header, list):
        # Two header lines.
        return "malformed"
    if len(header.encode("utf-8")) > PROOF["max_header_bytes"]:
        return "malformed"
    if not GRAMMAR.fullmatch(header):
        return "malformed"
    fields = dict(part.split("=", 1) for part in header.split(";")[1:])
    counter = int(fields["n"])
    if counter > 2**63 - 1:
        return "malformed"
    body = _body(request)
    if len(body) > PROOF["max_body_bytes"]:
        return "malformed"
    if key_state == "none" or fields["k"] != KEY["key_id"]:
        return "unknown_key"
    text = embed_proof.canonical(
        did=KEY["did"],
        key_id=fields["k"],
        counter=counter,
        user_id=fields["u"],
        method=request["method"],
        target=request["target"],
        digest=embed_proof.body_digest(body),
    )
    if not hmac.compare_digest(embed_proof.mac(_key(), text), fields["m"]):
        return "bad_mac"
    window = ReplayWindow(PROOF["window_bits"])
    for earlier in accepted_before or ():
        assert window.accept(earlier)
    if not window.accept(counter):
        return "replayed"
    return "accepted"


def test_constants_match_the_vectors() -> None:
    assert VECTORS["headers"]["proof"] == embed_proof.PROOF_HEADER
    assert PROOF["label"] == embed_proof.PROOF_LABEL
    assert PROOF["max_body_bytes"] == embed_proof.MAX_PROVEN_BODY
    assert len(_key()) == embed_proof.KEY_BYTES
    assert set(PROOF["refusal"]["reasons"]) == {
        "malformed",
        "unknown_key",
        "bad_mac",
        "replayed",
    }


def test_a_new_key_has_the_wire_form() -> None:
    key_id, key = embed_proof.new_key()
    assert re.fullmatch(r"[0-9a-f]{16}", key_id)
    assert len(key) == 32
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", embed_proof.encode_key(key))
    assert base64.urlsafe_b64decode(embed_proof.encode_key(key) + "=") == key
    assert embed_proof.new_key() != (key_id, key)


@pytest.mark.parametrize("vector", PROOF["valid"], ids=lambda vector: vector["note"])
def test_valid_vectors_are_signed_exactly(vector: dict[str, Any]) -> None:
    request = vector["request"]
    body = _body(request)
    fields = {
        "did": KEY["did"],
        "key_id": KEY["key_id"],
        "counter": vector["counter"],
        "user_id": KEY["user_id"],
        "method": request["method"],
        "target": request["target"],
    }
    text = embed_proof.canonical(**fields, digest=embed_proof.body_digest(body))
    assert text == vector["canonical"]
    assert (
        embed_proof.header(
            key_id=KEY["key_id"],
            counter=vector["counter"],
            user_id=KEY["user_id"],
            mac=embed_proof.mac(_key(), text),
        )
        == vector["header"]
    )
    assert embed_proof.sign(_key(), **fields, body=body) == vector["header"]
    assert len(vector["header"].encode()) <= PROOF["max_header_bytes"]
    assert _verify(vector["header"], request) == "accepted"


@pytest.mark.parametrize("vector", PROOF["refused"], ids=lambda vector: vector["note"])
def test_refused_vectors_are_refused_for_their_reason(vector: dict[str, Any]) -> None:
    assert (
        _verify(
            vector["header"],
            vector["request"],
            key_state=vector.get("key_state", "held"),
            accepted_before=vector.get("accepted_before"),
        )
        == vector["reason"]
    )


def test_the_replay_window_sequence() -> None:
    window = ReplayWindow(PROOF["window_bits"])
    outcomes = [
        (counter, "accepted" if window.accept(counter) else "replayed")
        for counter, _ in PROOF["window"]
    ]
    assert outcomes == [tuple(step) for step in PROOF["window"]]

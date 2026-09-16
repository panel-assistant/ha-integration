"""The proof that a request to a panel came from its sidebar's administrator.

A panel session that offers ``embed_proof`` receives a key in its hello reply.
The sidebar proxy signs each small enough request it forwards under that key,
naming the panel, the request and the administrator, so the panel can tell such
a request from any other client on its network. Both ends compute exactly this
text and MAC; the shared vectors hold them to it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

PROOF_HEADER: Final = "X-Panel-Assistant-Proof"
PROOF_LABEL: Final = "panel-assistant-embed-proof-v1"
# A larger body, such as an app upload, is forwarded unproven.
MAX_PROVEN_BODY: Final = 1024 * 1024
MAX_COUNTER: Final = 2**63 - 1
KEY_BYTES: Final = 32
NO_BODY: Final = "-"


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_key() -> tuple[str, bytes]:
    """Return a fresh key ID and key."""
    return secrets.token_hex(8), secrets.token_bytes(KEY_BYTES)


def encode_key(key: bytes) -> str:
    """Return a key as the hello reply carries it."""
    return _base64url(key)


def body_digest(body: bytes) -> str:
    """Return the body's SHA-256 in lowercase hex, or a dash for no body."""
    return hashlib.sha256(body).hexdigest() if body else NO_BODY


def canonical(
    *,
    did: str,
    key_id: str,
    counter: int,
    user_id: str,
    method: str,
    target: str,
    digest: str,
) -> str:
    """Return the text a proof's MAC covers."""
    return "\n".join(
        (PROOF_LABEL, did, key_id, str(counter), user_id, method, target, digest)
    )


def mac(key: bytes, text: str) -> str:
    """Return the MAC of a canonical text under a key."""
    return _base64url(hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest())


def header(*, key_id: str, counter: int, user_id: str, mac: str) -> str:
    """Return the proof header's value."""
    return f"v1;k={key_id};n={counter};u={user_id};m={mac}"


def sign(
    key: bytes,
    *,
    did: str,
    key_id: str,
    counter: int,
    user_id: str,
    method: str,
    target: str,
    body: bytes,
) -> str:
    """Return the proof header for one request, exactly as it is sent."""
    text = canonical(
        did=did,
        key_id=key_id,
        counter=counter,
        user_id=user_id,
        method=method,
        target=target,
        digest=body_digest(body),
    )
    return header(key_id=key_id, counter=counter, user_id=user_id, mac=mac(key, text))

"""HMAC request authentication for the gateway (B3) — pure, stdlib-only.

Machine callers (the WordPress lead webhook, phone shortcuts, tools) sign each
request with a **per-caller** shared secret. Per PRD-b3 Amendment 1, the signed
message is a tuple — timestamp, caller id, and a hash of the raw body — so a
valid signature is pinned to (a) the exact bytes, (b) a moment in time, and
(c) the caller who made it: one caller's signature cannot be presented as
another's, and a captured request ages out of the skew window.

Wire format (all three headers required on every authenticated route):
    X-AIA-Timestamp: <unix seconds>
    X-AIA-Caller:    <caller id, e.g. "wordpress" | "shortcut" | "tools">
    X-AIA-Signature: <hex hmac-sha256 of the signing string>

Signing string (newline-joined, hashing the body keeps the MAC input a fixed
size regardless of payload):
    f"{timestamp}\n{caller}\n{sha256(raw_body).hexdigest()}"

There is deliberately **no nonce cache** (PRD-b3 Amendment 1 / 70- decision
2026-08-08): a signed request can only be replayed byte-identically, and both
routes already no-op an identical payload (`capture_messages.content_hash` UNIQUE
pre-LLM; `prospects_wp_idx` UNIQUE). Timestamp + rate limit is sufficient. The
re-add condition is per-route: any future non-idempotent route needs one first.

This module is dependency-free (stdlib `hmac`/`hashlib` only) so the
security-critical verify path is unit-tested without FastAPI, a DB, or keychain
— the pure-core / thin-surface split shared with the B2 approval gate. The
FastAPI wiring (caller→secret resolution, size caps, header plumbing) lives in
`agents/gateway/app.py`.
"""

from __future__ import annotations

import hashlib
import hmac

# Header names (lowercased — HTTP headers are case-insensitive, and this is how
# they're looked up in the app).
TIMESTAMP_HEADER = "x-aia-timestamp"
CALLER_HEADER = "x-aia-caller"
SIGNATURE_HEADER = "x-aia-signature"

# Max clock skew (seconds) between the caller's timestamp and server time. A
# request older/newer than this is rejected as a possible replay. 300s tolerates
# ordinary clock drift without keeping a wide replay window open.
MAX_SKEW_SECONDS = 300


def _signing_string(timestamp: int, caller: str, body: bytes) -> str:
    """The exact string the MAC covers: `"{ts}\\n{caller}\\n{sha256(body)}"`."""
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{caller}\n{body_hash}"


def sign(secret: str, timestamp: int, caller: str, body: bytes) -> str:
    """Compute the `X-AIA-Signature` value for a request.

    Signs the (timestamp, caller, body-hash) tuple so all three are bound into
    the MAC. Returns raw hex (no prefix), which callers reproduce exactly.
    """
    message = _signing_string(timestamp, caller, body).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_request(
    *,
    secret: str,
    timestamp: str | None,
    caller: str | None,
    signature: str | None,
    body: bytes,
    now: int,
    max_skew: int = MAX_SKEW_SECONDS,
) -> bool:
    """Return True iff the request is authentic and fresh.

    False (never an exception) for every failure mode — a missing header, a
    non-integer timestamp, a timestamp outside the skew window, or a signature
    mismatch — so the caller maps a single False to a 401 with no detail leak.
    The comparison is constant-time (`hmac.compare_digest`) to avoid leaking the
    expected signature through timing. The caller id is resolved to `secret`
    upstream; it is passed here too because it is part of the signed message.
    """
    if not timestamp or not signature or not caller:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(now - ts) > max_skew:
        return False
    expected = sign(secret, ts, caller, body)
    return hmac.compare_digest(expected, signature)

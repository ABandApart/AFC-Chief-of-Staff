"""Unit tests for the gateway (B3).

Two layers:
  - **Pure HMAC** (`agents.gateway.auth`) — the security-critical verify path,
    tested stdlib-only (no FastAPI/DB/keychain): valid, tampered, missing
    headers, replay window, and the (timestamp, caller, body) binding.
  - **Endpoint** (`agents.gateway.app`) — via FastAPI's ASGI test transport:
    signed→202, unsigned→401, unknown-caller→401, body validation→422, size
    cap→413, and the ack-then-process handoff (ingest mocked). Guarded by
    `importorskip('fastapi')` so the default builder env (where fastapi *is*
    dev-synced) runs them and any fastapi-less env skips cleanly.

Auth follows PRD-b3 Amendment 1: per-caller secret, signature over the
(timestamp, caller, body-hash) tuple, no nonce cache. The real cognee ingest +
the external `curl` through the tunnel are the barry-agent runtime smoke.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agents.gateway import auth

SECRET = "test-secret-do-not-use"
CALLER = "tools"
NOW = 1_700_000_000


# --- pure HMAC ------------------------------------------------------------


def _signed(body: bytes, ts: int = NOW, secret: str = SECRET, caller: str = CALLER) -> str:
    return auth.sign(secret, ts, caller, body)


def test_valid_signature_verifies():
    body = b'{"text":"hi"}'
    sig = _signed(body)
    assert auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller=CALLER, signature=sig, body=body, now=NOW
    )


def test_tampered_body_fails():
    sig = _signed(b'{"text":"hi"}')
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller=CALLER,
        signature=sig, body=b'{"text":"HACKED"}', now=NOW,
    )


def test_wrong_secret_fails():
    body = b"payload"
    sig = auth.sign("other-secret", NOW, CALLER, body)
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller=CALLER, signature=sig, body=body, now=NOW
    )


def test_caller_is_bound_into_signature():
    # A signature made for one caller must not verify as another (the caller id
    # is part of the signed message — one caller's sig can't be replayed as
    # another's). PRD-b3 A1.
    body = b"payload"
    sig = auth.sign(SECRET, NOW, "tools", body)
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller="shortcut", signature=sig, body=body, now=NOW
    )


def test_missing_headers_fail():
    body = b"payload"
    sig = _signed(body)
    assert not auth.verify_request(
        secret=SECRET, timestamp=None, caller=CALLER, signature=sig, body=body, now=NOW
    )
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller=CALLER, signature=None, body=body, now=NOW
    )
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW), caller=None, signature=sig, body=body, now=NOW
    )


def test_non_integer_timestamp_fails():
    body = b"payload"
    sig = _signed(body)
    assert not auth.verify_request(
        secret=SECRET, timestamp="not-a-number", caller=CALLER, signature=sig, body=body, now=NOW
    )


def test_stale_timestamp_is_replay_rejected():
    body = b"payload"
    ts = NOW - (auth.MAX_SKEW_SECONDS + 1)
    sig = _signed(body, ts=ts)
    # Signature is valid, but the timestamp is outside the skew window.
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(ts), caller=CALLER, signature=sig, body=body, now=NOW
    )


def test_within_skew_window_accepted():
    body = b"payload"
    ts = NOW - (auth.MAX_SKEW_SECONDS - 1)
    sig = _signed(body, ts=ts)
    assert auth.verify_request(
        secret=SECRET, timestamp=str(ts), caller=CALLER, signature=sig, body=body, now=NOW
    )


def test_timestamp_is_bound_into_signature():
    # A signature made for one timestamp must not verify under another (the
    # timestamp is part of the signed message, not just a sidecar header).
    body = b"payload"
    sig = _signed(body, ts=NOW)
    assert not auth.verify_request(
        secret=SECRET, timestamp=str(NOW + 1), caller=CALLER, signature=sig, body=body, now=NOW + 1
    )


# --- endpoints (FastAPI ASGI test transport) ------------------------------

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agents.gateway import app as gw  # noqa: E402


@pytest.fixture
def client(mocker):
    # Resolve known callers to a test secret without touching keychain (unknown
    # callers still resolve to None → 401), and never run the real cognee ingest.
    mocker.patch.object(
        gw, "_secret_for",
        side_effect=lambda caller: SECRET if caller in gw.HMAC_SECRET_ITEMS else None,
    )
    ingest_mock = mocker.patch.object(gw.ingest, "ingest_note")
    mocker.patch.object(gw.cognee_setup, "configure_cognee")
    roy_kent_mock = mocker.patch.object(
        gw.roy_kent, "process_lead",
        return_value={"status": "processed", "prospect_id": 1, "fit": None},
    )
    with TestClient(gw.app) as c:
        c.ingest_mock = ingest_mock  # type: ignore[attr-defined]
        c.roy_kent_mock = roy_kent_mock  # type: ignore[attr-defined]
        yield c


def _sign_headers(body: bytes, caller: str = CALLER) -> dict[str, str]:
    import time

    ts = int(time.time())
    return {
        auth.TIMESTAMP_HEADER: str(ts),
        auth.CALLER_HEADER: caller,
        auth.SIGNATURE_HEADER: auth.sign(SECRET, ts, caller, body),
        # HMAC signs the raw bytes; the content-type only tells FastAPI to
        # JSON-decode them for the Pydantic model (real callers send this).
        "content-type": "application/json",
    }


def test_health_needs_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ingest_signed_returns_202_and_dispatches(client):
    body = b'{"text":"hello from the API","source_ref":"abc","source_type":"api"}'
    r = client.post("/ingest", content=body, headers=_sign_headers(body))
    assert r.status_code == 202
    assert r.json()["status"] == "accepted"
    # Ack-then-process: the background task ran the ingest core once.
    client.ingest_mock.assert_awaited_once()
    _, kwargs = client.ingest_mock.await_args
    assert kwargs["source_ref"] == "abc"
    assert kwargs["source_type"] == "api"


def test_ingest_unsigned_is_401_and_never_ingests(client):
    body = b'{"text":"hi","source_ref":"x","source_type":"api"}'
    r = client.post("/ingest", content=body)  # no signature headers
    assert r.status_code == 401
    client.ingest_mock.assert_not_awaited()


def test_ingest_unknown_caller_is_401(client):
    # A well-formed signature from a caller with no keychain secret is rejected
    # before the body is trusted (per-caller secrets, PRD-b3 A1).
    body = b'{"text":"hi","source_ref":"x","source_type":"api"}'
    headers = _sign_headers(body, caller="stranger")
    r = client.post("/ingest", content=body, headers=headers)
    assert r.status_code == 401
    client.ingest_mock.assert_not_awaited()


def test_ingest_tampered_body_is_401(client):
    body = b'{"text":"hi","source_ref":"x","source_type":"api"}'
    headers = _sign_headers(body)
    r = client.post("/ingest", content=b'{"text":"TAMPERED","source_ref":"x","source_type":"api"}',
                    headers=headers)
    assert r.status_code == 401
    client.ingest_mock.assert_not_awaited()


def test_ingest_empty_text_is_422(client):
    body = b'{"text":"   ","source_ref":"x","source_type":"api"}'
    r = client.post("/ingest", content=body, headers=_sign_headers(body))
    assert r.status_code == 422
    client.ingest_mock.assert_not_awaited()


def test_ingest_bad_source_type_is_422(client):
    body = b'{"text":"hi","source_ref":"x","source_type":"evil"}'
    r = client.post("/ingest", content=body, headers=_sign_headers(body))
    assert r.status_code == 422
    client.ingest_mock.assert_not_awaited()


LEADS_BODY = (
    b'{"wordpress_profile_id":"wp-1","name":"Jane Prospect","source_form":"scorecard",'
    b'"raw_profile":{"answers":{"q1":"we cannot keep up with our own pipeline"}}}'
)


def test_leads_webhook_signed_returns_202_and_dispatches(client):
    r = client.post(
        "/webhook/leads", content=LEADS_BODY,
        headers=_sign_headers(LEADS_BODY, caller="wordpress"),
    )
    assert r.status_code == 202
    assert r.json()["wordpress_profile_id"] == "wp-1"
    # Ack-then-process: the background task ran Roy Kent's pipeline once.
    client.roy_kent_mock.assert_called_once()
    (payload,), _ = client.roy_kent_mock.call_args
    assert payload["wordpress_profile_id"] == "wp-1"
    assert payload["source_form"] == "scorecard"


def test_leads_webhook_unsigned_is_401(client):
    r = client.post("/webhook/leads", content=b"{}")
    assert r.status_code == 401
    client.roy_kent_mock.assert_not_called()


def test_leads_webhook_bad_source_form_is_422(client):
    body = b'{"wordpress_profile_id":"wp-1","name":"Jane","source_form":"evil"}'
    r = client.post("/webhook/leads", content=body, headers=_sign_headers(body, caller="wordpress"))
    assert r.status_code == 422
    client.roy_kent_mock.assert_not_called()


def test_leads_webhook_missing_name_is_422(client):
    body = b'{"wordpress_profile_id":"wp-1","source_form":"scorecard"}'
    r = client.post("/webhook/leads", content=body, headers=_sign_headers(body, caller="wordpress"))
    assert r.status_code == 422
    client.roy_kent_mock.assert_not_called()


def test_oversized_body_is_413(client):
    # A body past the per-route cap (32KB for /ingest) is rejected via the
    # Content-Length pre-check, before any ingest.
    big_text = "x" * (gw.INGEST_MAX_BYTES + 100)
    body = json.dumps({"text": big_text, "source_ref": "x", "source_type": "api"}).encode()
    r = client.post("/ingest", content=body, headers=_sign_headers(body))
    assert r.status_code == 413
    client.ingest_mock.assert_not_awaited()


# --- read cap (lying / absent Content-Length) -----------------------------


class _FakeStreamRequest:
    """Minimal stand-in for a Starlette Request: just an async body stream, no
    Content-Length. Exercises the read cap that a header pre-check can't cover."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def stream(self):
        for c in self._chunks:
            yield c


def test_read_body_capped_aborts_when_stream_exceeds_cap():
    # No Content-Length header at all, yet the streamed bytes exceed the cap →
    # 413. This is the "a lying Content-Length must not stream past" guarantee.
    req = _FakeStreamRequest([b"a" * 6, b"b" * 6])  # 12 bytes, cap 8
    with pytest.raises(HTTPException) as ei:
        asyncio.run(gw._read_body_capped(req, 8))
    assert ei.value.status_code == 413


def test_read_body_capped_returns_body_under_cap():
    req = _FakeStreamRequest([b"hello ", b"world"])
    body = asyncio.run(gw._read_body_capped(req, 1024))
    assert body == b"hello world"


# --- MCP tool layer: REST transport (Track I, Task 4) ---------------------

from unittest.mock import AsyncMock  # noqa: E402


def test_tools_recall_signed_dispatches(client, mocker):
    # Same brain_tools core as MCP, reached via dispatch; here dispatch is mocked
    # so we test the route wiring (auth, JSON, caller/transport attribution).
    disp = mocker.patch.object(
        gw.mcp_tools, "dispatch",
        new=AsyncMock(return_value={"answer": "A", "scope": "untrusted"}),
    )
    body = b'{"query":"what do we know"}'
    r = client.post("/tools/recall", content=body, headers=_sign_headers(body))
    assert r.status_code == 200
    assert r.json() == {"answer": "A", "scope": "untrusted"}
    name, args, ctx = disp.await_args.args
    assert name == "recall" and args == {"query": "what do we know"}
    assert ctx.transport == "gateway_rest" and ctx.caller == "tools"  # HMAC caller


def test_tools_unsigned_is_401(client):
    r = client.post("/tools/recall", content=b'{"query":"x"}')  # no signature
    assert r.status_code == 401


def test_tools_unknown_tool_is_404(client):
    # Real dispatch → ToolError('not_found') → 404 envelope. No DB/cognee touched.
    body = b"{}"
    r = client.post("/tools/does_not_exist", content=body, headers=_sign_headers(body))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_tools_bad_json_is_422(client):
    body = b"not json at all"  # validly signed, but not a JSON object
    r = client.post("/tools/recall", content=body, headers=_sign_headers(body))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "schema"


def test_tools_toolerror_maps_to_http_status(client, mocker):
    mocker.patch.object(
        gw.mcp_tools, "dispatch",
        new=AsyncMock(side_effect=gw.ToolError("too_large", "text exceeds 32KB")),
    )
    body = b'{"text":"x","source_ref":"r"}'
    r = client.post("/tools/ingest_note", content=body, headers=_sign_headers(body))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "too_large"

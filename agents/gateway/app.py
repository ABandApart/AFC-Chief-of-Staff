"""Gateway API (B3) — the authenticated inbound edge.

A small FastAPI app on `127.0.0.1:8788`. Machine callers that can join the
tailnet reach it over Tailscale; the WordPress webhook (which cannot) is fronted
by a Cloudflare Tunnel (PRD-b3 Amendment 2). No inbound ports are opened and
Postgres never leaves the local socket.

Routes (v1):
  - `GET  /health`         — liveness, no auth, static literal (no version, no
                             DB state — an unauthenticated probe must not report
                             internal state).
  - `POST /ingest`         — HMAC-authed; the primary API ingestion channel.
                             Ack-then-process: validate + enqueue, return 202
                             fast, run `ingest_note` in the background.
  - `POST /webhook/leads`  — HMAC-authed route + validation land here; the
                             Phase-6 WordPress handler is not built yet (501).

Auth (PRD-b3 Amendment 1): per-caller HMAC. Each caller carries an
`X-AIA-Caller` id resolved to its own keychain secret, and signs the
(timestamp, caller, body-hash) tuple (`agents/gateway/auth`). A WordPress
compromise therefore rotates one key and revokes one caller, not all of them.
Verification is cheapest-first so an unauthenticated caller cannot make the box
do work before authenticating; there is no nonce cache (see `auth` / the 70-
decision log).

Trust boundaries:
  - **B3 (exposure):** every route except `/health` requires a valid per-caller
    HMAC signature. A missing/failed signature → 401 and nothing touches the graph.
  - **B1 (data):** `/ingest` content crosses into the graph via the same
    `ingest_note` core as Discord/Granola — it is data, not instructions, and
    cognee treats it as inert. The signature authenticates the *caller*, not the
    trustworthiness of the *content*.

Run (barry-agent, where cognee + gateway groups are synced):
    uv run python -m agents.gateway.app
Under launchd via `launchd/com.aiadaptive.cos.gateway.plist`.
"""

from __future__ import annotations

import logging
import time

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from agents._lib import cognee_setup, creds, ingest
from agents._lib.brain_tools import InvocationContext, ToolError
from agents.gateway import auth
from agents.mcp import tools as mcp_tools

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8788

# Per-caller HMAC secrets (PRD-b3 A1 item 3). caller id -> keychain item. A
# caller whose id is absent here is unknown → 401, before the body is read.
# Adding a caller = one keychain item + one row here.
HMAC_SECRET_ITEMS = {
    "wordpress": "gateway-hmac-wordpress",
    "shortcut": "gateway-hmac-shortcut",
    "tools": "gateway-hmac-tools",
}

# Per-route body caps (PRD-b3 A1 item 1). 32KB is a very long note; the lead
# webhook payload is smaller still. Enforced twice: a Content-Length pre-check
# (reject before reading) *and* a streaming read cap (a lying or absent
# Content-Length must not be able to stream past it).
INGEST_MAX_BYTES = 32 * 1024
LEADS_MAX_BYTES = 16 * 1024

# source_type values the ingest endpoint accepts. The API is the primary
# ingestion channel, so callers self-identify; anything off the list is a 422
# (a typo'd or unexpected caller shouldn't quietly land in the graph).
ALLOWED_SOURCE_TYPES = frozenset({"api", "tool", "shortcut", "wordpress"})


def _secret_for(caller: str | None) -> str | None:
    """The keychain HMAC secret for a known caller, or None for an unknown one.

    Patched in tests to avoid a keychain. None → the app raises 401 without
    revealing whether the caller or the signature was the problem.
    """
    if caller is None or caller not in HMAC_SECRET_ITEMS:
        return None
    return creds.keychain_get(HMAC_SECRET_ITEMS[caller])


# cognee is configured once, lazily, on the first ingest — importing this module
# (and running the endpoint tests) must not require the cognee group.
_cognee_configured = False


async def _read_body_capped(request: Request, max_bytes: int) -> bytes:
    """Read the request body, aborting with 413 once it exceeds `max_bytes`.

    This is the real size enforcement: it holds even when `Content-Length` lies
    or is absent, because it counts the bytes actually streamed. The read result
    is cached on the request (`_body`) so the route's Pydantic model parses the
    very same bytes that were authenticated.
    """
    if hasattr(request, "_body"):
        return request._body
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    body = b"".join(chunks)
    request._body = body  # cache so the route re-reads the authenticated bytes
    return body


def require_hmac(max_body_bytes: int):
    """Build a FastAPI dependency that enforces per-caller HMAC + a body cap.

    Verification order is cheapest-first (PRD-b3 A1) so an attacker cannot make
    the box do work before authenticating:
      1. Content-Length > cap        → 413 (before reading the body)
      2. unknown/missing X-AIA-Caller → 401
      3. read the body under the cap  → 413 on a lying/absent Content-Length
      4. bad timestamp or signature   → 401
    Body schema validation (422) then happens in the route's Pydantic model.
    """

    async def _dependency(request: Request) -> None:
        # 1. Content-Length pre-check — reject an oversized body before reading.
        length_header = request.headers.get("content-length")
        if length_header is not None:
            try:
                length = int(length_header)
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="invalid content-length"
                ) from None
            if length > max_body_bytes:
                raise HTTPException(status_code=413, detail="request body too large")

        # 2. Unknown caller → 401 (same message as a bad signature: no leak).
        caller = request.headers.get(auth.CALLER_HEADER)
        secret = _secret_for(caller)
        if secret is None:
            raise HTTPException(status_code=401, detail="invalid or missing signature")

        # 3. Read the body under the cap (catches a lying/absent Content-Length).
        body = await _read_body_capped(request, max_body_bytes)

        # 4. Timestamp freshness + signature (constant-time) → 401 on failure.
        ok = auth.verify_request(
            secret=secret,
            timestamp=request.headers.get(auth.TIMESTAMP_HEADER),
            caller=caller,
            signature=request.headers.get(auth.SIGNATURE_HEADER),
            body=body,
            now=int(time.time()),
        )
        if not ok:
            raise HTTPException(status_code=401, detail="invalid or missing signature")

        # Authenticated: record the caller so tool routes can attribute the audit.
        request.state.caller = caller

    return _dependency


class IngestBody(BaseModel):
    text: str
    source_ref: str
    source_type: str = "api"

    @field_validator("text")
    @classmethod
    def _text_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty")
        return v

    @field_validator("source_type")
    @classmethod
    def _source_type_allowed(cls, v: str) -> str:
        if v not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(ALLOWED_SOURCE_TYPES)}")
        return v


# Docs/OpenAPI disabled: this is a machine-only API behind a tunnel, and B3's
# goal is no unauthenticated public surface beyond /health. The interactive docs
# and schema would otherwise be reachable without a signature.
app = FastAPI(
    title="AFC Richmond Gateway",
    version="1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health() -> dict[str, str]:
    # Static literal only — no version, no DB status, no queue depth (A1).
    return {"status": "ok"}


@app.post("/ingest", status_code=202, dependencies=[Depends(require_hmac(INGEST_MAX_BYTES))])
async def ingest_endpoint(body: IngestBody, background: BackgroundTasks) -> dict[str, str]:
    """Ack-then-process: enqueue the ingest and return 202 immediately.

    The graph write (cognify) is slow and must not block the caller (a webhook
    that waits will time out and retry). We validate synchronously, hand the
    work to a background task, and acknowledge.
    """
    background.add_task(
        _process_ingest,
        text=body.text,
        source_ref=body.source_ref,
        source_type=body.source_type,
    )
    logger.info("ingest accepted: ref=%s type=%s", body.source_ref, body.source_type)
    return {"status": "accepted", "source_ref": body.source_ref}


def _ensure_cognee() -> None:
    """Configure cognee once per process (lazy — importing this module must not
    require the cognee group; only cognee-touching calls trigger it)."""
    global _cognee_configured
    if not _cognee_configured:
        cognee_setup.configure_cognee()
        _cognee_configured = True


async def _process_ingest(*, text: str, source_ref: str, source_type: str) -> None:
    """Background worker: configure cognee once, then run the shared ingest core.

    Errors are logged, never surfaced (the caller already got its 202). B1
    holds: `ingest_note` treats the text as inert data.
    """
    try:
        _ensure_cognee()
        result = await ingest.ingest_note(
            text, source_ref=source_ref, source_type=source_type
        )
        logger.info("ingest %s (%s): %s", source_ref, source_type, result)
    except Exception:
        logger.exception("background ingest failed: ref=%s", source_ref)


@app.post(
    "/webhook/leads",
    status_code=202,
    dependencies=[Depends(require_hmac(LEADS_MAX_BYTES))],
)
async def leads_webhook(request: Request) -> dict[str, str]:
    """Authenticated route for the Phase-6 WordPress lead webhook.

    B3 lands the route + HMAC auth now; the handler (parse payload → `prospects`)
    is Phase 6. Until then an authenticated call gets an explicit 501 so a
    misconfigured caller fails loudly rather than appearing to succeed.
    """
    raise HTTPException(
        status_code=501,
        detail="lead webhook handler not implemented yet (Phase 6)",
    )


# --- MCP tool layer: remote (REST) transport (Track I, Task 4) ---------------

# Cap for tool bodies — accommodates ingest_note (32KB); reads are far smaller
# and brain_tools bounds their args regardless.
TOOLS_MAX_BYTES = 32 * 1024

# ToolError.code → HTTP status (the common error envelope, PRD-mcp-tool-layer).
_TOOL_ERROR_STATUS = {
    "unauthorized": 401, "schema": 422, "bad_request": 422, "too_large": 413,
    "not_found": 404, "over_ceiling": 429, "unavailable": 503,
}

# Tools that reach cognee (graph search / cognify) need configure_cognee() first.
_COGNEE_TOOLS = frozenset({"recall", "ingest_note"})


@app.post("/tools/{tool}", dependencies=[Depends(require_hmac(TOOLS_MAX_BYTES))])
async def tools_endpoint(tool: str, request: Request) -> object:
    """Remote transport for the MCP tool layer — the SAME `brain_tools` core and
    dispatch as the stdio server, behind the SAME per-caller HMAC as the rest of
    the gateway. The request body is the tool's JSON arguments; the authenticated
    caller (set by `require_hmac`) attributes the audit."""
    try:
        arguments = await request.json()
    except Exception:
        arguments = None
    if not isinstance(arguments, dict):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "schema",
                               "message": "body must be a JSON object",
                               "retryable": False}},
        )

    ctx = InvocationContext(
        caller=getattr(request.state, "caller", "unknown"), transport="gateway_rest"
    )
    if tool in _COGNEE_TOOLS:
        _ensure_cognee()
    try:
        return await mcp_tools.dispatch(tool, arguments, ctx)
    except ToolError as e:
        return JSONResponse(
            status_code=_TOOL_ERROR_STATUS.get(e.code, 400),
            content={"error": {"code": e.code, "message": e.message,
                               "retryable": e.retryable}},
        )


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("gateway starting on http://%s:%d", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()

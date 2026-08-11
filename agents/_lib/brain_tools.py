"""The gated core for the MCP tool layer (Track I, `PRD-mcp-tool-layer.md`).

One module, one gate per boundary — the transports (`agents/mcp/server.py`
local stdio, and the Gateway REST routes) are thin: they parse/authorize and
call these functions and nothing else. Defining the gates here, once, is what
lets the shell be untrusted and swappable.

Every tool is exactly one of:
  - a **read that returns data** (B1) — bounded, over the `brain_reader` RO role
    and `v_*` views (migration 0008), never a base table, never raw SQL;
  - the **one-way `ingest_note`** knowledge promotion (`source_type='tool'`
    pinned server-side; content is inert data, B1);
  - a **gated action** — `enqueue_approval` writes a `pending` row to
    `approval_queue`; nothing world-affecting happens until a human clicks
    Approve in `#approvals` (B2).

Retrieval is only ever `retrieval.recall` (B1 scopes, default UNTRUSTED — never a
raw graph search). No tool sends/publishes, writes an operational table outside
the approval queue, or authors a control-plane file (B4). Every call writes one
`tool_invocations` audit row (migration 0009).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from psycopg.rows import dict_row

from agents._lib import approvals, db, ingest, retrieval

logger = logging.getLogger(__name__)

# --- bounds / enums ---------------------------------------------------------

DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_SINCE_HOURS = 168
INGEST_MAX_BYTES = 32 * 1024
MAX_QUERY_CHARS = 2000
MAX_SUMMARY_CHARS = 500
MAX_SOURCE_REF_CHARS = 200

# tool `scope` arg → retrieval Scope. Only these two are exposed (no TARGET).
SCOPE_MAP = {"untrusted": retrieval.Scope.UNTRUSTED, "playbooks": retrieval.Scope.TRUSTED}

# spend windows → a SAFE, hardcoded SQL lower-bound (never caller input).
SPEND_WINDOWS = {
    "today": "date_trunc('day', now())",
    "7d": "now() - interval '7 days'",
    "30d": "now() - interval '30 days'",
}

# Playbook filename allowlist: lowercase kebab, no dots/slashes → no traversal.
_PLAYBOOK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PLAYBOOKS_DIR = Path(__file__).resolve().parents[2] / "playbooks"


class ToolError(Exception):
    """A structured tool failure the transports map to the error envelope."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class InvocationContext:
    """Who is calling and over which transport — audit context, not a tool arg."""

    caller: str
    transport: str = "mcp_stdio"


# --- audit ------------------------------------------------------------------


def _args_hash(args: dict) -> str:
    canon = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _write_audit(
    ctx: InvocationContext,
    tool: str,
    args_hash: str,
    outcome: str,
    error_code: str | None,
    latency_ms: int,
    agent_run_id: int | None = None,
) -> None:
    """Best-effort audit write. A failed audit never breaks the tool it records."""
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tool_invocations
                    (transport, caller, tool, args_hash, outcome, error_code,
                     latency_ms, agent_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (ctx.transport, ctx.caller, tool, args_hash, outcome, error_code,
                 latency_ms, agent_run_id),
            )
    except Exception:
        logger.exception("tool_invocations audit write failed (tool=%s)", tool)


@contextmanager
def _audit(tool: str, ctx: InvocationContext, args: dict):
    """Time the call and record exactly one audit row (ok or error) on exit."""
    started = time.monotonic()
    outcome, error_code = "ok", None
    try:
        yield
    except ToolError as e:
        outcome, error_code = "error", e.code
        raise
    except Exception:
        outcome, error_code = "error", "internal"
        raise
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        _write_audit(ctx, tool, _args_hash(args), outcome, error_code, latency_ms)


# --- read helper (brain_reader RO role, v_* views only) ---------------------


def _ro_query(sql: str, params: tuple = ()) -> list[dict]:
    with db.ro_connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ToolError("schema", "limit must be an integer") from None
    return max(1, min(MAX_LIMIT, limit))


def _clamp(value: int | None, lo: int, hi: int, default: int) -> int:
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolError("schema", "expected an integer") from None
    return max(lo, min(hi, value))


# --- READ tools (B1; RO role; bounded) --------------------------------------


async def recall(query: str, scope: str = "untrusted", *, ctx: InvocationContext) -> dict:
    """Graph recall via `retrieval.recall` — scoped; never a raw graph search."""
    with _audit("recall", ctx, {"query": query, "scope": scope}):
        if not query or len(query) > MAX_QUERY_CHARS:
            raise ToolError("schema", f"query must be 1..{MAX_QUERY_CHARS} chars")
        if scope not in SCOPE_MAP:
            raise ToolError("schema", f"scope must be one of {sorted(SCOPE_MAP)}")
        res = await retrieval.recall(
            query, scope=SCOPE_MAP[scope], agent="recall", trigger_kind="interactive"
        )
        return {"answer": res.answer, "scope": scope}


def list_open_followups(limit: int | None = DEFAULT_LIMIT, *, ctx: InvocationContext) -> dict:
    with _audit("list_open_followups", ctx, {"limit": limit}):
        rows = _ro_query(
            "SELECT id, owner, action, escalation_level, deadline, created_at "
            "FROM v_open_followups LIMIT %s",
            (_clamp_limit(limit),),
        )
        return {"rows": rows}


def list_pending_task_candidates(
    limit: int | None = DEFAULT_LIMIT, *, ctx: InvocationContext
) -> dict:
    with _audit("list_pending_task_candidates", ctx, {"limit": limit}):
        rows = _ro_query(
            "SELECT id, proposed_action, source_type, source_ref, confidence, created_at "
            "FROM v_pending_task_candidates LIMIT %s",
            (_clamp_limit(limit),),
        )
        return {"rows": rows}


def get_prospect(prospect_id: int, *, ctx: InvocationContext) -> dict:
    with _audit("get_prospect", ctx, {"prospect_id": prospect_id}):
        try:
            pid = int(prospect_id)
        except (TypeError, ValueError):
            raise ToolError("schema", "prospect_id must be an integer") from None
        rows = _ro_query("SELECT * FROM v_prospect WHERE id = %s", (pid,))
        if not rows:
            raise ToolError("not_found", f"no prospect with id {pid}")
        return {"prospect": rows[0]}


def list_new_prospects(
    since_hours: int | None = 24, limit: int | None = DEFAULT_LIMIT, *, ctx: InvocationContext
) -> dict:
    with _audit("list_new_prospects", ctx, {"since_hours": since_hours, "limit": limit}):
        rows = _ro_query(
            "SELECT * FROM v_new_prospects "
            "WHERE received_at > now() - make_interval(hours => %s) LIMIT %s",
            (_clamp(since_hours, 1, MAX_SINCE_HOURS, 24), _clamp_limit(limit)),
        )
        return {"rows": rows}


def spend_summary(window: str, *, ctx: InvocationContext) -> dict:
    with _audit("spend_summary", ctx, {"window": window}):
        if window not in SPEND_WINDOWS:
            raise ToolError("schema", f"window must be one of {sorted(SPEND_WINDOWS)}")
        # SPEND_WINDOWS values are hardcoded SQL, never caller input.
        rows = _ro_query(
            "SELECT function_label, count(*) AS calls, "
            "coalesce(sum(usd_cost), 0) AS usd "
            "FROM v_spend_summary "
            f"WHERE started_at >= {SPEND_WINDOWS[window]} "
            "GROUP BY function_label ORDER BY usd DESC"
        )
        total = sum((Decimal(str(r["usd"])) for r in rows), Decimal("0"))
        return {"total": str(total), "by_function": rows}


def get_playbook(name: str, *, ctx: InvocationContext) -> dict:
    """Read a trusted, git-tracked playbook by name (B4 — read-only, never a write)."""
    with _audit("get_playbook", ctx, {"name": name}):
        if not name or not _PLAYBOOK_NAME_RE.fullmatch(name):
            raise ToolError("bad_request", "invalid playbook name")
        path = _PLAYBOOKS_DIR / f"{name}.md"
        if not path.is_file():
            raise ToolError("not_found", f"no playbook named {name!r}")
        return {"name": name, "text": path.read_text(encoding="utf-8")}


# --- PROMOTE (one-way knowledge write, B1) ----------------------------------


async def ingest_note(text: str, source_ref: str, *, ctx: InvocationContext) -> dict:
    """Promote a note into the graph. `source_type` is pinned to 'tool' here."""
    with _audit("ingest_note", ctx, {"source_ref": source_ref}):
        if not text:
            raise ToolError("schema", "text is required")
        if len(text.encode("utf-8")) > INGEST_MAX_BYTES:
            raise ToolError("too_large", "text exceeds 32KB")
        if not source_ref or len(source_ref) > MAX_SOURCE_REF_CHARS:
            raise ToolError("schema", f"source_ref must be 1..{MAX_SOURCE_REF_CHARS} chars")
        result = await ingest.ingest_note(text, source_ref=source_ref, source_type="tool")
        status = "duplicate" if result == "repost" else "ingested"
        return {"status": status, "note_hash": ingest.message_hash(text)}


# --- ACT (gated by the approval queue, B2) ----------------------------------


def enqueue_approval(
    item_type: str,
    summary: str,
    payload: dict,
    idempotency_key: str | None = None,
    *,
    ctx: InvocationContext,
) -> dict:
    """Enqueue a world-affecting action for human approval. No side effect here."""
    audit_args = {"item_type": item_type, "idempotency_key": idempotency_key}
    with _audit("enqueue_approval", ctx, audit_args):
        # item_type must map to a registered handler — this layer never invents one.
        if item_type not in approvals.HANDLERS:
            raise ToolError(
                "bad_request",
                f"unregistered item_type {item_type!r}; known: {sorted(approvals.HANDLERS)}",
            )
        if not summary or len(summary) > MAX_SUMMARY_CHARS:
            raise ToolError("schema", f"summary must be 1..{MAX_SUMMARY_CHARS} chars")
        if not isinstance(payload, dict):
            raise ToolError("schema", "payload must be an object")
        # idempotency_key is accepted per the API but not yet enforced (v1) —
        # dedup on (item_type, idempotency_key) needs an approval_queue column.
        approval_id = approvals.request_approval(
            item_type=item_type, payload=payload, summary=summary
        )
        return {"approval_id": approval_id, "status": "pending"}

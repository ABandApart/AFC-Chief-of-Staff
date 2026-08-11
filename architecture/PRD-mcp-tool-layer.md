# MCP Tool Layer — Gated Interactive Access to the Brain — PRD & Build Spec

<doc:meta>
  <doc:phase>Interactive front-end enabler — the boundary an interactive brain-agent (and Claude Code / Hermes) reaches the brain through</doc:phase>
  <doc:theme>The tool layer is the invariant; the agent shell is the variable. Reads are data (B1); actions go through the approval gate (B2); knowledge promotion is one-way (ingest_note); no skill authoring (B4).</doc:theme>
  <doc:duration>~3–4 days (excludes retrieval.py, which is a separate prerequisite)</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — authorized by ADR-0001. **Track I** in `70-build-order.md`; builds after **Phase 3.8 (`retrieval.py`)**, parallel to Phase 4. B2 (approval gate) and B3 (tunnel) are both **met** as of 2026-08-10, so the remote transport is in-scope (no longer deferred). Operator go/no-go on building it pending.</doc:status>
  <doc:depends_on>ADR-0001; `_lib/retrieval.py` (PREREQUISITE — specified, not built); `_lib/graph_recall.recall`; `_lib/ingest.ingest_note`; `_lib/approvals`; `_lib/db` pool; `agents/gateway` (HMAC, `tools` caller)</doc:depends_on>
  <doc:blocks>the interactive brain-agent shell; any external agent (Claude Code, Hermes) driving the brain</doc:blocks>
</doc:meta>

## TL;DR

A **shared gated core** (`agents/_lib/brain_tools.py`) that exposes a small,
typed, allowlisted set of brain operations, surfaced over two transports:

- **local stdio MCP server** (`agents/mcp/server.py`) for on-box clients
  (our own agent loop, Claude Code, a local Hermes) — the default, no network
  surface;
- the **existing Gateway REST app** for remote clients — reusing the
  already-provisioned `tools` HMAC caller and the tunnel/tailnet
  (`PRD-b3-tunnel.md`).

Every tool is one of: a **read that returns data** (B1), a **gated action** that
lands in `#approvals` (B2), or the **one-way `ingest_note`** knowledge promotion.
No tool exposes raw SQL, writes operational tables directly, sends/publishes, or
authors control-plane files (B4). All LLM-touching tools run under
`labeled()`/`agent_run`, so their spend lands in `agent_runs`.

## Goal & Non-Goals

<goals>

**Goal:** any agent shell can *recall from the graph, read bounded operational
state, propose an action for approval, and promote a note into memory* — through
one boundary whose gates are enforced server-side, so the shell is untrusted and
swappable.

**Non-goals:**
- **No raw SQL** to any client. Reads are named, parameterized, bounded.
- **No direct `cognee.search`.** All retrieval goes through
  `_lib/retrieval.py` scopes; the graph is reached only via `recall`.
- **No autonomous side effects.** Nothing sends email, publishes to Buffer,
  writes a Drive doc, or mutates an operational state machine except by
  enqueuing an approval that a human clicks (B2).
- **No control-plane authoring.** Skills/loops/playbooks stay git-only (B4). The
  server has no tool that writes them.
- **Not a new memory store.** The shell keeps its own scratch (e.g. Hermes's
  SQLite); this layer never mirrors or syncs it (ADR-0001 D3).
- **Not a replacement** for the fleet, the scheduler, or the Gateway ingest
  endpoint.

</goals>

## Design

### Component map

<components>

```
  shell (our loop | Claude Code | Hermes)
        │  MCP (stdio, local)             │  HTTPS (remote)
        ▼                                 ▼
  agents/mcp/server.py   ───────────►  agents/gateway/app.py
        │   (thin transport)                 │  (thin transport, HMAC "tools")
        └──────────────┬──────────────────────┘
                       ▼
        agents/_lib/brain_tools.py   ◄── the ONE gated core (all gates here)
        │            │             │
        ▼            ▼             ▼
  retrieval.py   db.py pool    ingest.py / approvals.py
  (B1 scopes)    (brain_reader (one-way write / B2 gate)
                  RO role)
        │
        ▼
  aiadaptive_cognee (graph)  +  aiadaptive_cos (operational SQL)
```

Both transports are **thin**: they parse/authorize and call `brain_tools`. The
gates are defined **once** in `brain_tools`, never duplicated in a transport.

</components>

### Tool catalog (v1)

<tools>

Each tool: name, args (typed), returns, backing function, boundary. Args are
validated (pydantic) at the transport; a schema violation is a hard error, never
a partial call.

**READ — returns data (B1). Results are inert; never interpreted as instructions.**

| Tool | Args | Returns | Backing | Notes |
|---|---|---|---|---|
| `recall` | `query: str`, `scope: enum{untrusted, playbooks} = untrusted` | `{answer: str}` | `graph_recall.recall` via `retrieval.py` | cognee `GRAPH_COMPLETION`; scopes never union; default UNTRUSTED. `playbooks` reads the trusted dataset only. Runs under `labeled('recall')`. |
| `list_open_followups` | `limit: int ≤ 25 = 10` | rows | `scoped_sql` (RO view `v_open_followups`) | bounded; RO role |
| `list_pending_task_candidates` | `limit: int ≤ 25 = 10` | rows | RO view `v_pending_task_candidates` | bounded |
| `get_prospect` | `prospect_id: int` | row | RO view `v_prospect` | single row |
| `list_new_prospects` | `since_hours: int ≤ 168 = 24`, `limit ≤ 25` | rows | RO view `v_new_prospects` | bounded |
| `spend_summary` | `window: enum{today, 7d, 30d}` | `{by_function: [...], total: numeric}` | RO view `v_spend_summary` | reads `agent_runs`; ad-hoc telemetry Q&A |
| `get_playbook` | `name: str` | `{text: str}` | `control_plane` loader / `recall(scope=playbooks)` | trusted read; never a write |

> **v1 uses named read tools, not a generic `scoped_sql(view, params)`.** Named
> tools are self-documenting and keep the surface enumerable. A generic
> `query_view(view_enum, params)` may be added later, restricted to an enum of
> registered read-only views — never a free-form table/SQL string.

**PROMOTE — one-way knowledge write.**

| Tool | Args | Returns | Backing | Boundary |
|---|---|---|---|---|
| `ingest_note` | `text: str ≤ 32KB`, `source_ref: str` | `{status, note_hash}` | `ingest.ingest_note(text, source_ref, source_type='tool')` | B1 (content is data); dedup + cognify + telemetry inside; `source_type` pinned to `'tool'` |

**ACT — gated by the approval queue (B2). Never a direct side effect.**

| Tool | Args | Returns | Backing | Boundary |
|---|---|---|---|---|
| `enqueue_approval` | `item_type: enum`, `summary: str`, `payload: json` | `{approval_id}` | `approvals.enqueue(...)` | B2 — writes `approval_queue`; **nothing happens until a human ✅ in `#approvals`**. The existing distribution/send agents act on approved rows, not this layer. `item_type` bound to the registered enum; high-consequence types require the typed-confirmation path (`PRD-b2-approval-gate.md`). |

**Explicitly absent (non-goals, restated as denials):** raw SQL, writes to
operational tables outside `enqueue_approval`/`ingest_note`, direct
`cognee.search`, any send/publish/Drive/email action, and any write to
`.claude/skills`, `loops/`, or `playbooks/` (B4).

</tools>

### The gated core — `agents/_lib/brain_tools.py`

<core>

- One module, pure-ish: each public function is a tool. It owns the gate logic
  (scope selection, `LIMIT`/truncation, enum validation, `source_type='tool'`
  pinning, approval enqueue). Transports call these and nothing else.
- **Retrieval only via `retrieval.py`.** `brain_tools` never imports
  `cognee.search`; a CI grep (the existing B1 check) fails the build on a raw
  call. This is the PREREQUISITE dependency (below).
- **Reads use a separate read-only connection** (`brain_reader` role, below),
  not the read-write pool — defense in depth, so a bug in a read tool cannot
  write.
- **Telemetry:** LLM-touching tools (`recall`, `ingest_note`) run under
  `labeled(<tool>, 'infrastructure', trigger_kind='interactive')`. A per-call
  **audit line** (tool, caller, arg-hash, latency, ok/err) is written to
  `#system` and/or a `tool_invocations` table — the shell's *reasoning* tokens
  are out of scope (ADR-0001 accepted limitation), but every brain-touching call
  is logged.

</core>

### Database — read-only role + views

<database>

- New Postgres role **`brain_reader`**: `SELECT` only, on a fixed set of `v_*`
  views in `aiadaptive_cos` — no base-table grants, no write, no DDL. The MCP
  server's read connection authenticates as `brain_reader`.
- The `v_*` views encode the bounds (`LIMIT` defaults are in the tool; the views
  restrict columns and join shape). No view exposes credential columns, raw HMAC
  material, or full `agent_runs` rows beyond the spend summary.
- The graph DB (`aiadaptive_cognee`) is **never** reached via SQL from this
  layer — only via `recall`.
- Migration: add the role + views (new migration number, after the current head).

</database>

### Transports

<transports>

**Local (default): stdio MCP.** `agents/mcp/server.py` using the official MCP
Python SDK (FastMCP-style registration). Entry point:
`uv run python -m agents.mcp.server`. Trust = the OS account (`barry-agent`); no
network surface. Registered in each client's MCP config (Claude Code `.mcp.json`,
Hermes MCP config). New optional dependency group `mcp`; cognee/gateway imports
stay lazy.

**Remote: Gateway REST.** For a shell that cannot run on-box (a serverless
Hermes), add read routes to `agents/gateway/app.py` — `POST /recall`,
`POST /query/<named_view>` — backed by the **same `brain_tools` functions**,
authenticated by the existing per-caller HMAC using the already-provisioned
**`tools`** caller (`gateway-hmac-tools`) and `source_type='tool'`. No new auth
model; reuses tunnel/tailnet posture (`PRD-b3-tunnel.md`). `enqueue_approval`
and `ingest_note` over REST reuse the existing ack-then-process pattern.

> Remote MCP-over-HTTP is deliberately **not** exposed in v1; remote clients use
> the REST surface so the MCP server stays local-only (smaller attack surface).

</transports>

## Trust boundaries (mapping)

<trust_boundaries>

| ID | How this layer honors it |
|---|---|
| **B1** | Reads return data; the server never treats tool args or recalled text as commands. Retrieval only through `retrieval.py` scopes (default UNTRUSTED, no scope union). `ingest_note` content stays inert. |
| **B2** | The only path to a world-affecting act is `enqueue_approval` → `#approvals` → human ✅. This layer never sends/publishes. |
| **B3** | Local transport opens no ports. Remote transport rides the existing HMAC + tunnel; Postgres never leaves the socket. |
| **B4** | No tool writes skills/loops/playbooks. `get_playbook` is read-only; playbook provenance stays git→publish. |
| **TB-accounts** | Server runs as `barry-agent`; reads as `brain_reader`. Code authored in barry-admin, pulled — unchanged. |

</trust_boundaries>

## Acceptance criteria

<acceptance>

1. `recall` returns a synthesized answer via `GRAPH_COMPLETION`, scoped through
   `retrieval.py`, with spend recorded in `agent_runs` under `trigger_kind='interactive'`.
2. Each read tool returns bounded rows via a `v_*` view over the `brain_reader`
   role; a write attempt on that connection fails at the DB.
3. `ingest_note` promotes a note (dedup honored: an exact re-post is a no-op) and
   records a `cognify_run` ledger row.
4. `enqueue_approval` creates an `approval_queue` row and performs **no** side
   effect until a human approves; an unregistered `item_type` is rejected.
5. No tool can issue raw SQL, call `cognee.search` directly, or write a
   control-plane file. The CI B1 grep passes.
6. stdio MCP server registers and is drivable from Claude Code end-to-end
   (validate with the on-box, trusted client **before** any Hermes).
7. Gateway `/recall` returns the same answer as the MCP `recall` for the same
   query, authenticated with the `tools` HMAC caller; an unsigned request → 401.
8. Every brain-touching tool call writes an audit line (tool, caller, arg-hash,
   latency, outcome).

</acceptance>

## Build tasks

<build_tasks>

0. **PREREQUISITE — build `agents/_lib/retrieval.py`** (the specified B1 scope
   wrapper): `Scope` enum, `recall`-side enforcement, no scope union, default
   UNTRUSTED, CI grep against raw `cognee.search`. Blocks everything below.
1. Migration: `brain_reader` role + `v_*` read-only views.
2. `agents/_lib/brain_tools.py` — the gated core (all tools, all gates, audit).
3. `agents/mcp/server.py` — stdio MCP transport over the core; `mcp` dep group.
4. Gateway read routes (`/recall`, `/query/<view>`) reusing the core.
5. Audit logging (`tool_invocations` table and/or `#system` line).
6. Tests: unit (gates, bounds, enum rejection, RO-role write denial), and an
   end-to-end drive from Claude Code.
7. Register with Claude Code; validate ACs; **only then** consider Hermes as an
   alternate shell.

</build_tasks>

## API Specification (MCP + Gateway REST)

Both surfaces are thin wrappers over the same `brain_tools` functions. The **MCP
tool name** and the **REST path** below map 1:1 to a single core function, so a
tool added once appears on both transports. Shapes are the wire contract;
validation is pydantic at the transport, gates are in the core.

### Conventions

<api_conventions>

- **MCP transport:** JSON-RPC over stdio (MCP `tools/list` + `tools/call`). Each
  tool advertises a JSON-Schema `inputSchema`; the return is MCP `content`
  (a `text` block carrying the JSON result, or `isError: true` with a message).
- **REST transport:** JSON over HTTPS to the Gateway (`127.0.0.1:8788`, fronted
  per B3). **Every** authenticated request carries the B3 headers
  (`X-AIA-Timestamp`, `X-AIA-Caller: tools`, `X-AIA-Signature`) — reused verbatim
  from `agents/gateway/auth`. Read routes are `POST` (not `GET`) so the body is
  signed by the same HMAC scheme (the signature covers a hash of the body); a
  `GET` with query params could not be signed the same way and would put query
  content in logs. Bodies are capped (reads 8 KB, `ingest` 32 KB).
- **Errors** (both transports) use a common envelope:
  `{ "error": { "code": "<slug>", "message": "<human>", "retryable": <bool> } }`.
  Codes: `unauthorized` (401), `bad_request`/`schema` (422), `too_large` (413),
  `over_ceiling` (429, soft breaker tripped), `not_found` (404),
  `unavailable` (503, provider/cognee down). Reads never 500 on empty results —
  an empty answer is `{ "answer": "No matching facts." }`.
- **Telemetry:** LLM-touching calls (`recall`, `ingest_note`) run under
  `labeled(<tool>, 'infrastructure', trigger_kind='interactive')`; every call
  writes an audit row (below) regardless of transport.
- **Idempotency:** `ingest_note` is content-hash idempotent (a re-post no-ops);
  `enqueue_approval` is **not** idempotent unless the caller passes an
  `idempotency_key` (dedup on `(item_type, idempotency_key)`).

</api_conventions>

### READ — `recall`

<api_tool name="recall">

- **MCP** `recall` — `inputSchema`:
  ```json
  { "type": "object",
    "properties": {
      "query": { "type": "string", "minLength": 1, "maxLength": 2000 },
      "scope": { "enum": ["untrusted", "playbooks"], "default": "untrusted" }
    },
    "required": ["query"], "additionalProperties": false }
  ```
- **REST** `POST /tools/recall` — body `{ "query": "...", "scope": "untrusted" }`
- **Returns** `{ "answer": "string", "scope": "untrusted" }`
- **Backing** `brain_tools.recall` → `retrieval.recall(query, scope)` →
  `graph_recall.recall` (cognee `GRAPH_COMPLETION`). Scopes never union; default
  `UNTRUSTED`; `playbooks` reads the trusted dataset only. **Never** raw
  `cognee.search`.
- **Boundary** B1 — the answer is data; the server never re-interprets it.

</api_tool>

### READ — bounded operational views

<api_tool name="reads">

All backed by the `brain_reader` RO role over a `v_*` view; all bounded.

| MCP tool / REST path | Input | Returns |
|---|---|---|
| `list_open_followups` · `POST /tools/list_open_followups` | `{ limit?: int≤25=10 }` | `{ rows: [{ id, subject, escalation_level, due_at }] }` |
| `list_pending_task_candidates` · `POST /tools/list_pending_task_candidates` | `{ limit?: int≤25=10 }` | `{ rows: [{ id, summary, confidence, source }] }` |
| `get_prospect` · `POST /tools/get_prospect` | `{ prospect_id: int }` | `{ prospect: { id, name, icp_fit_score, status, … } }` or `not_found` |
| `list_new_prospects` · `POST /tools/list_new_prospects` | `{ since_hours?: int≤168=24, limit?: int≤25=10 }` | `{ rows: [...] }` |
| `spend_summary` · `POST /tools/spend_summary` | `{ window: "today"\|"7d"\|"30d" }` | `{ total: "numeric-string", by_function: [{ function, calls, usd }] }` |
| `get_playbook` · `POST /tools/get_playbook` | `{ name: string }` | `{ name, text }` or `not_found` |

- **Boundary** B1; RO role (a write on this connection fails at the DB). Views
  expose no credential/HMAC columns and no full `agent_runs` rows beyond the
  spend rollup.

</api_tool>

### PROMOTE — `ingest_note`

<api_tool name="ingest_note">

- **MCP** `ingest_note` — `inputSchema`:
  ```json
  { "type": "object",
    "properties": {
      "text": { "type": "string", "minLength": 1, "maxLength": 32768 },
      "source_ref": { "type": "string", "maxLength": 200 }
    },
    "required": ["text", "source_ref"], "additionalProperties": false }
  ```
- **REST** `POST /tools/ingest_note` — body `{ "text": "...", "source_ref": "..." }`
  (this is the **same core** as the existing `POST /ingest`; the `/tools/`
  alias exists so the shell has a uniform namespace. `source_type` is pinned to
  `'tool'` server-side and is **not** a caller field.)
- **Returns** `{ "status": "ingested"|"duplicate", "note_hash": "sha256hex" }`
  (ack-then-process: the graph write runs in the background; `status` reports
  the dedup decision, not cognify completion).
- **Backing** `ingest.ingest_note(text, source_ref, source_type='tool')` —
  message-hash dedup + `cognify` + M1 telemetry inside.
- **Boundary** B1 — content is inert data. One-way; **never** a table sync
  (ADR-0001 D3).

</api_tool>

### ACT — `enqueue_approval`

<api_tool name="enqueue_approval">

- **MCP** `enqueue_approval` — `inputSchema`:
  ```json
  { "type": "object",
    "properties": {
      "item_type": { "type": "string" },
      "summary": { "type": "string", "maxLength": 500 },
      "payload": { "type": "object" },
      "idempotency_key": { "type": "string", "maxLength": 100 }
    },
    "required": ["item_type", "summary", "payload"], "additionalProperties": false }
  ```
- **REST** `POST /tools/enqueue_approval` — same body.
- **Returns** `{ "approval_id": int, "status": "pending" }`
- **Backing** `approvals.request_approval(item_type, payload, summary)` — writes
  one `pending` row to `approval_queue`. `item_type` is validated against the
  **registered handler enum**; an unregistered type → `bad_request`. **No side
  effect occurs until a human clicks Approve in `#approvals`** (B2); the existing
  handler for that `item_type`, not this layer, performs the action.
- **Boundary** B2 — this is the *only* path to a world-affecting act. High-
  consequence `item_type`s require the typed-confirmation path
  (`PRD-b2-approval-gate.md` A1). The operator allowlist + 2FA still govern who
  may approve.

</api_tool>

### Audit — `tool_invocations`

<api_audit>

Every call on either transport writes one row (new migration):

```sql
CREATE TABLE tool_invocations (
  id           bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  transport    text NOT NULL CHECK (transport IN ('mcp_stdio','gateway_rest')),
  caller       text NOT NULL,               -- 'local:barry-agent' | HMAC caller id ('tools')
  tool         text NOT NULL,
  args_hash    text NOT NULL,               -- sha256 of the canonicalized args (no raw PII in the log)
  outcome      text NOT NULL CHECK (outcome IN ('ok','error')),
  error_code   text,
  latency_ms   integer NOT NULL,
  agent_run_id bigint REFERENCES agent_runs(id)  -- set when the tool made an LLM call
);
```

Anomalies (repeated `error`, `over_ceiling`, unknown `item_type`) also post to
`#system`. The shell's own **reasoning** tokens are out of scope (ADR-0001
accepted limitation); every brain-touching call is nonetheless recorded here.

</api_audit>

### Transport wiring summary

<api_wiring>

| | Local stdio MCP | Gateway REST |
|---|---|---|
| Entry | `uv run python -m agents.mcp.server` | routes added to `agents/gateway/app.py` |
| Client config | Claude Code `.mcp.json` / Hermes MCP config (`command`, `args`) | HTTP tool / MCP-over-HTTP bridge in the shell |
| Auth | OS account (`barry-agent`); no network | per-caller HMAC, caller `tools` (`gateway-hmac-tools`) |
| Reachability | on-box only | tunnel/tailnet (B3, **verified 2026-08-10**) |
| Use when | our loop, Claude Code, a local Hermes | serverless/off-box shell |

Prefer **stdio** for on-box shells (smallest surface, no network). The REST
surface exists for shells that cannot run on the mini; it reuses the B3 edge
rather than exposing MCP-over-HTTP directly.

</api_wiring>

## Open questions

<open_questions>

1. **MCP SDK/framework** — official `mcp` Python SDK (FastMCP) vs a hand-rolled
   stdio server. Lean official.
2. **Audit sink** — dedicated `tool_invocations` table vs `#system` lines vs
   both. Recommend a table for queryability, `#system` for anomalies.
3. **`query_view` generalization** — defer the enum-restricted generic reader to
   v2, or include it now? Recommend defer; named tools first.
4. **Reasoning-token attribution** — a third-party shell's own tokens stay out of
   `agent_runs`. Accept, or require a litellm-style callback for shells that
   support one (Hermes routes via litellm)? Recommend accept for v1; revisit if
   Hermes is adopted as the standing shell.
5. **Ceiling for interactive calls** — should `assert_under_ceiling` gate
   interactive `recall`/`ingest_note` like scheduled work, or get its own soft
   budget? Recommend a dedicated `interactive` soft ceiling.

</open_questions>

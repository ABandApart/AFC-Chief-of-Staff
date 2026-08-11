# ADR-0001: Federate an interactive agent over the brain; do not migrate to Hermes, do not translate storage

<doc:meta>
  <doc:type>Architecture Decision Record</doc:type>
  <doc:status>PROPOSED (decisions) — **roadmap sequencing ADOPTED 2026-08-10**: `retrieval.py` next, Track I after, Hermes optional (`70-build-order.md` decision log). The go/no-go on *building* Track I, and on ever adopting Hermes, remains the operator's.</doc:status>
  <doc:date>2026-08-10</doc:date>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:decision_drivers>capability gap (interactive multi-step reasoning over the brain); Hermes evaluation; the local-first, client-confidential posture; the two-plane security model</doc:decision_drivers>
  <doc:depends_on>25-target-state.md (two planes, B1–B4), 20-architecture-overview.md (cognee boundary), 30-memory-layer.md (retrieval scopes), PRD-b3-tunnel.md (Gateway/HMAC)</doc:depends_on>
  <doc:supersedes>none</doc:supersedes>
  <doc:related>PRD-mcp-tool-layer.md (the build spec this decision authorizes)</doc:related>
</doc:meta>

## TL;DR

We evaluated **Hermes** (NousResearch) as a replacement for — or addition to —
the AFC Richmond agent fleet, and we evaluated how a Hermes-like interactive
agent would connect to the Postgres/cognee brain. Three decisions:

1. **Do not wholesale-migrate** the governed fleet to Hermes. Hermes is a
   generalist autonomous *agent runtime*; AFC Richmond is a governed,
   deterministic *multi-agent application*. They are different categories, and
   migrating would dissolve the governance (B1/B2/B4, the telemetry ledger, the
   two-plane memory) that the system was deliberately built around.
2. **Federate, don't fuse.** Add the missing capability — an *interactive,
   multi-step agent over the brain* — as a **new front-end that reaches the brain
   through a gated tool boundary**, not by importing Hermes's memory/skill model
   into the governed plane. Build the boundary once (`PRD-mcp-tool-layer.md`); the
   *shell* that drives it (our own loop, Claude Code, or Hermes) becomes a cheap,
   reversible choice.
3. **Connect at the API boundary, never the storage boundary.** Reject any
   SQLite↔Postgres translation or bidirectional sync. Hermes's SQLite and our
   Postgres/cognee are two different systems of record, not two encodings of one
   dataset. The bridge is a gated **tool call**, plus a one-way `ingest_note`
   write for knowledge Hermes wants to promote.

## Context

<context>

- **The capability gap.** The fleet (`40-action-layer.md`) is a set of fixed,
  single-shot, scheduled agents that coordinate only through Postgres. There is
  no agent that can *hold a conversation, retrieve from the brain, reason across
  the graph and operational state in multiple steps, and propose an action* —
  the "self-assembling pre-call brief" and graph-grounded drafting that
  `25-target-state.md` §9 names as highest-leverage are not expressible today.
- **"Ted" is not an orchestrator.** Orchestration in AFC Richmond is
  *infrastructure*: the scheduler daemon fires loop manifests; agents decouple
  through the DB. "Ted" is the 6-hourly health/anomaly/cost watchdog. So
  "migrate the orchestrator to Hermes" has no literal referent — the choice is
  really "replace the governed fleet + its substrate" vs "add a new front-end."
- **Hermes, characterized.** A single persistent autonomous agent loop:
  agent-curated memory, **runtime self-authored skills**, dynamic subagents, a
  multi-channel gateway (Telegram/Slack/WhatsApp/Signal/Email + voice), MCP
  support, seven terminal backends, provider-agnostic model switching. State
  lives in **SQLite + FTS5**. MIT, young, fast-moving. It ships
  `hermes claw migrate` — it is the successor to the **OpenClaw** lineage this
  project retired in 2026-05 for latency, privacy, cost, and control.
- **Our posture.** Local-first, client-confidential data, air-gapped-ish, with
  email/Drive ingest planned. The two planes must never share a store
  (`25-target-state.md` §1): **memory** (ingested, untrusted, fuzzy) vs
  **control** (git-authored, trusted, deterministic). That separation *is* the
  security model.

</context>

## Decision 1 — Do not wholesale-migrate to Hermes

<decision id="D1">

Keep the governed fleet, its scheduler, its telemetry ledger, its two-plane
cognee memory, and the approval gate as the system of record and action. Hermes
does not provide, and structurally works against, the properties we depend on:

| Property we rely on | Hermes's grain |
|---|---|
| **B1** memory ≠ instructions | Blends agent-curated memory with executable skills |
| **B4** control plane only via git | Self-authors skills at runtime |
| Per-call cost ledger (`agent_runs`) | No first-class cost attribution |
| Postgres/cognee GraphRAG two-plane memory | SQLite/FTS5 single store |
| Deterministic outbound path (zero-LLM outreach), hard **B2** approval gate | Autonomy-first |
| Validated, decoupled, launchd-supervised reliability | Young single-loop runtime |

Wholesale migration is high-effort and low-return for the parts that already
work, and it re-opens the OpenClaw-lineage decision we deliberately closed.

</decision>

## Decision 2 — Federate via a gated tool boundary

<decision id="D2">

Add the interactive capability as a **new front-end** that talks to the brain
through a **curated, typed, gated tool layer** — the same gates the fleet uses:

- reads are **data** (B1): `recall` (cognee `GRAPH_COMPLETION`) and read-only,
  bounded `scoped_sql`;
- world-affecting acts go through **`enqueue_approval`** → `#approvals` (B2);
- knowledge is promoted only through **`ingest_note`** (one-way, cognify + dedup
  + telemetry);
- **no tool authors skills or playbooks** — the control plane stays git-only (B4).

**The tool layer is the invariant; the shell is the variable.** Because the
gates live server-side, the driving shell (our own Claude tool-use loop first;
Claude Code or Hermes later) cannot bypass them, so "build vs adopt Hermes"
stops being a one-way door. Spec: `PRD-mcp-tool-layer.md`.

The recommended first shell is a **thin loop of our own** (keeps the `agent_runs`
ledger whole and the surface minimal). Hermes remains an *optional alternate
shell* against the same boundary if multi-channel/voice later earns its keep;
its own reasoning tokens would fall outside our ledger — an accepted limitation
of any third-party shell.

</decision>

## Decision 3 — Connect at the API boundary, not the storage boundary

<decision id="D3">

**Reject a SQLite↔Postgres translation/sync layer.** It mis-models the problem:
the two stores are different systems of record, not one dataset in two formats.

Four reasons it fails:
1. **Two authorities → drift.** Bidirectional sync creates two owners of truth
   and the conflict-resolution burden that follows.
2. **cognee is not relational.** It is a library with a *semantic* API
   (`cognee.search(GRAPH_COMPLETION)`). No SQLite schema is an analog of
   graph + vector + GraphRAG; SQL-dialect translation yields raw tables and
   discards cognee's entire value. The only meaningful graph access is `recall`.
3. **Different retrieval paradigms.** FTS5 (keyword) and pgvector+graph
   (semantic) answer different questions; they are not translatable.
4. **It breaks B1 and B4** and makes the injection surface *bidirectional* —
   Hermes's self-authored, untrusted memory would flow into the governed brain.

**Ranked connection options** (full detail in `PRD-mcp-tool-layer.md`):

| | Mechanism | GraphRAG? | Gates hold? | Verdict |
|---|---|---|---|---|
| **A** | MCP server over the brain (gated tools) | Yes (`recall`) | Server-side | **Chosen** |
| **B** | Gateway REST as HTTP tools (reuses `tools` HMAC caller) | Yes (add `/recall`) | HMAC + server-side | Chosen for remote shells |
| **C** | Read-only Postgres tool (role + views) | No (raw SQL) | Only via read-only role | Escape hatch only |
| **D** | Repoint Hermes's own state to Postgres | n/a | n/a | Rejected (deep fork) |
| **E** | SQLite↔Postgres sync/translation | No | No | **Rejected** |

A and B converge: one **shared gated core** (`_lib/brain_tools`) exposed over two
transports — local **stdio MCP** for on-box clients, and the existing
**Gateway REST** (HMAC `tools` caller, already provisioned) for remote ones. The
one legitimate write of Hermes-learned knowledge into the brain is
`ingest_note` — the front door, as data, one-way — never a table sync.

</decision>

## Consequences

<consequences>

**Positive**
- The governance model (B1/B2/B4, `agent_runs`, two-plane memory) is preserved
  intact; the new capability is additive.
- The gated tool layer is reusable by *any* shell — our loop, Claude Code,
  Hermes — with no re-architecture, so the shell decision stays reversible.
- The interactive capability (the biggest gap in AFC today) becomes buildable
  against primitives that already exist (`graph_recall.recall`, `ingest_note`,
  `approvals`, the Gateway, the db pool).

**Negative / costs**
- We build and own the tool layer and (initially) the shell loop, rather than
  adopting Hermes's ready-made TUI/gateway/subagents.
- A third-party shell (Hermes/Claude Code) leaves its *reasoning* tokens outside
  the `agent_runs` ledger; only tool-boundary spend is captured.
- Multi-channel/voice and dynamic subagents are deferred, not delivered, by this
  decision.

**Hard prerequisite**
- `agents/_lib/retrieval.py` — the scoped B1 retrieval wrapper referenced by
  `25-target-state.md` §3 and `30-memory-layer.md` — is **specified but not yet
  built**. It is load-bearing for D2/D3 (no tool may call `cognee.search`
  directly). It must be built before or with the tool layer.

**Unblocked 2026-08-10 — B3 verified.** barry-agent runtime-tested the Cloudflare
Tunnel end-to-end (`PRD-b3-tunnel.md`). The **remote transport** in D3 is now
available: an off-box shell reaches the brain via the Gateway REST surface using
the already-provisioned `tools` HMAC caller, with no new exposure work. The local
stdio transport remains the default and needs no tunnel.

**Follow-ups**
- `PRD-mcp-tool-layer.md` — the build spec (authorized by this ADR); now carries
  the concrete MCP + Gateway REST API specification.
- `PRD-hermes-optional-shell.md` — the optional Hermes adoption plan (D2), added
  as **Track H**.
- Decision-log entries in `70-build-order.md` and routes in `00-INDEX.md` —
  **done 2026-08-10** (roadmap sequencing adopted: `retrieval.py` → Track I →
  Hermes optional).
- Revisit the Hermes-as-shell option only after the boundary exists and the
  interactive capability has proven its value with our own loop.

</consequences>

## Options considered and rejected

<alternatives>

- **Full migration to Hermes** — rejected (D1): dissolves governance, re-opens a
  closed decision, low return.
- **Storage translation / bidirectional sync** — rejected (D3): mis-models two
  systems of record as one; breaks B1/B4; loses GraphRAG.
- **Direct Postgres access for the shell** — accepted only as a locked-down
  read-only escape hatch (option C), never the primary path, never a write path,
  and it forfeits GraphRAG.
- **Do nothing** — rejected: the interactive-reasoning gap is real and named as
  highest-leverage in the target state.

</alternatives>

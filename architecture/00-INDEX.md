# AI Adaptive Chief of Staff — Architecture

<doc:meta>
  <doc:title>AI Adaptive Chief of Staff Architecture</doc:title>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:version>0.2.0-draft</doc:version>
  <doc:status>design</doc:status>
  <doc:compiled_at>2026-05-14</doc:compiled_at>
</doc:meta>

<doc:abstract>
A persistent operational layer for AI Adaptive built on four separable layers — channel, action, memory, and telemetry — that share one Postgres-backed brain. Channel is Discord. Action is Claude Code + launchd-scheduled scripts running on a Mac mini. Memory is local PostgreSQL 17 with pgvector for selective vectorization (pivoted from hosted Supabase — see `70-build-order.md` decision log). Telemetry tracks cost, prevents runaway spend, and reports against the north star: sustainable long-term contract engagements. The system implements the Chief of Staff three-tier handoff model (autonomous / prep-for-review / human-only), serves eight defined workflows that each tie to a key result, maps to a Ted Lasso–named agent roster, and is built incrementally over a phased plan.
</doc:abstract>

<doc:north_star>
**Sustainable long-term contract engagements.** Three key results:

- **KR1**: New contract engagements per quarter
- **KR2**: Dollar value per engagement
- **KR3**: Project → maintenance conversion rate

Every workflow ties to at least one KR. Every architectural element traces to a workflow. See `90-workflows.md`.
</doc:north_star>

<doc:routes>

For questions about **why** the system is designed this way (principles, three-tier model, value-stream mapping):
→ [`10-strategy.md`](./10-strategy.md)

For questions about **what** the system looks like overall (four layers, agents, data flow):
→ [`20-architecture-overview.md`](./20-architecture-overview.md)

For **where the system is heading** (proposed cognee pivot, control plane, ingest/output channels, trust boundaries):
→ [`25-target-state.md`](./25-target-state.md) — ADOPTED; the pivot executed

For **how the pivot was done** (three tracks, workstreams MW1–MW7, mitigations M1/M2, rollback, and the **2026-11-01 keep/kill review gate**):
→ [`26-cognee-migration-plan.md`](./26-cognee-migration-plan.md) — EXECUTED

For **whether to migrate to Hermes, and how an interactive agent connects to the brain** (federate-don't-migrate; API boundary, not storage translation):
→ [`ADR-0001-hermes-federation-and-brain-boundary.md`](./ADR-0001-hermes-federation-and-brain-boundary.md) — PROPOSED (roadmap sequencing adopted 2026-08-10)

For the **gated tool layer** an interactive shell (our loop / Claude Code / Hermes) reaches the brain through (MCP + Gateway REST API spec):
→ [`PRD-mcp-tool-layer.md`](./PRD-mcp-tool-layer.md) — Track I, build after `retrieval.py`

For the **optional Hermes shell** (adoption plan, sandboxing to preserve B1/B2/B4):
→ [`PRD-hermes-optional-shell.md`](./PRD-hermes-optional-shell.md) — Track H, OPTIONAL

For questions about **where data lives and how it is queried** (Postgres schema, vectorization rules, hybrid search):
→ [`30-memory-layer.md`](./30-memory-layer.md)

For the **outreach CRM** (evidence table, five-touch sequencing, capacity cap, BCC loop closure, Trent Crimm, staleness model, ingest hardening H1–H7):
→ [`35-outreach-crm.md`](./35-outreach-crm.md)

For **inbound lead handling** (why inbound never runs the cold arc; the design is deliberately OPEN, gated on measuring volume):
→ [`36-inbound-leads.md`](./36-inbound-leads.md)

For the **outreach workflow as a picture** (six diagrams — read this before 35-):
→ [`37-outreach-workflow.md`](./37-outreach-workflow.md)

For questions about **how agents run** (Roy Kent, Tartt, Nate Shelley, Keeley, Trent Crimm, Briefing, Higgins, Ted, the outreach loops, scheduler/launchd, Discord bot supervision):
→ [`40-action-layer.md`](./40-action-layer.md)

For questions about **how humans interact with the system** (Discord channels, Task Tinder buttons, approval gates, outcome capture):
→ [`50-channel-layer.md`](./50-channel-layer.md)

For questions about **the content pipeline specifically** (state machine, Buffer integration, rate limiting, icp_signals enrichment):
→ [`60-content-pipeline.md`](./60-content-pipeline.md)

For questions about **build order and dependencies** (what to build first, what each phase enables):
→ [`70-build-order.md`](./70-build-order.md)

For **how work is expected to be done here** — the spec-driven convention, what a
spec must contain (S1–S6), and what to do when a spec meets reality mid-build:
→ [`70-build-order.md`](./70-build-order.md) §"Working convention", summarised in
the repo root [`CLAUDE.md`](../CLAUDE.md) that every session loads

For questions about **how the system measures itself** (agent_runs, cost helper, runaway-prevention guards, weekly dashboard):
→ [`80-telemetry-layer.md`](./80-telemetry-layer.md)

For questions about **what the system does for the business** (the eight workflows, KR alignment, demo narrative):
→ [`90-workflows.md`](./90-workflows.md)

</doc:routes>

<doc:naming_conventions>

- **Strategy layer files** (10-): timeless principles, why-decisions, value-stream mapping. Edit rarely.
- **Architecture overview** (20-): structural overview bridging strategy and implementation. Edit on major topology changes.
- **Implementation layer files** (30- through 60-): schemas, code patterns, integration specifics. Edit as the system evolves.
- **Build order** (70-): phasing and dependencies. Edit when scope or priorities change.
- **Telemetry layer** (80-): how the system measures and protects itself. Edit when metrics or guards change.
- **Workflows** (90-): audience-facing demonstration of value. Edit when workflows are added, removed, or materially refined.

XML tags wrap structured meta-context inside the markdown. Markdown handles narrative and hierarchy.

</doc:naming_conventions>

<doc:terminology>

| Term | Definition |
|------|------------|
| **Brain** | The Postgres database (local PostgreSQL 17 on the Mac mini) — the canonical memory layer |
| **Agent** | A scheduled or event-triggered job that reads/writes the brain |
| **Skill** | A reusable Claude Code prompt/script invoked by an agent |
| **Gemba point** | An explicit human decision gate in the value stream |
| **Tier 1/2/3** | The three-tier handoff model — autonomous / prep / human-only |
| **Workflow** | A defined sequence of work the system performs, tied to a KR |
| **KR** | Key result — measurable outcome supporting the north star |
| **Mac mini** | The execution host; runs all scheduled agents and the Discord bot |
| **Laptop** | A reader/builder workspace; runs ad-hoc Claude Code sessions |
| **agent_runs** | The telemetry ledger; one row per LLM call |
| **Cost helper** | Single Python module that wraps all LLM calls and writes agent_runs |
| **Guard G1/G2/G3** | ~~Per-run token cap~~ / ~~per-day spend ceiling~~ / anomaly detection. **G1 and G2 were removed in the 3.7 pivot** — replaced by a soft post-hoc breaker plus bounded queries. G3 (Ted) remains. |
| **MW1–MW7** | The cognee migration workstreams (`26-`). Renamed from `W1–W7` on 2026-08-08; **`W` now means a business workflow only.** |
| **Target** | A company in a function state at a moment — the outreach unit of work (`outreach_targets`); distinct from a `prospects` person-row |
| **Evidence** | A typed, sourced, dated fact about a target (`outreach_evidence`); `first_seen_at` is created by our own polling and cannot be bought retroactively |
| **Packet** | The assembled per-touch work payload — deterministic query, no LLM; the operator writes the observation sentence (Tier 3) |

</doc:terminology>

<doc:four_layers>

The system has four architectural layers, each separable from the others:

| Layer | Responsibility | Substrate | Document |
|-------|----------------|-----------|----------|
| Channel | Surface to humans, capture input | Discord, Claude Code CLI | `50-channel-layer.md` |
| Action | Execute work, call LLMs, talk to external APIs | Python on Mac mini, launchd | `40-action-layer.md` |
| Memory | Store everything the system knows | Local Postgres 17 + pgvector | `30-memory-layer.md` |
| Telemetry | Measure, prevent runaway spend, report | agent_runs ledger, Higgins, Ted | `80-telemetry-layer.md` |

The layers communicate only through the brain. No direct cross-layer dependencies.

</doc:four_layers>

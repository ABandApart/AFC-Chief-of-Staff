# AI Adaptive Chief of Staff — Architecture

<doc:meta>
  <doc:title>AI Adaptive Chief of Staff Architecture</doc:title>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:version>0.2.0-draft</doc:version>
  <doc:status>design</doc:status>
  <doc:compiled_at>2026-05-14</doc:compiled_at>
</doc:meta>

<doc:abstract>
A persistent operational layer for AI Adaptive built on four separable layers — channel, action, memory, and telemetry — that share one Postgres-backed brain. Channel is Discord. Action is Claude Code + launchd-scheduled scripts running on a Mac mini. Memory is hosted Supabase with pgvector for selective vectorization. Telemetry tracks cost, prevents runaway spend, and reports against the north star: sustainable long-term contract engagements. The system implements the Chief of Staff three-tier handoff model (autonomous / prep-for-review / human-only), serves seven defined workflows that each tie to a key result, maps to a Ted Lasso–named agent roster, and is built incrementally over a phased plan.
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

For questions about **where data lives and how it is queried** (Postgres schema, vectorization rules, hybrid search):
→ [`30-memory-layer.md`](./30-memory-layer.md)

For questions about **how agents run** (Roy Kent, Tartt, Nate Shelley, Keeley cluster, Sam, Briefing, Higgins, Ted, launchd, Discord bot supervision):
→ [`40-action-layer.md`](./40-action-layer.md)

For questions about **how humans interact with the system** (Discord channels, Task Tinder buttons, approval gates, outcome capture):
→ [`50-channel-layer.md`](./50-channel-layer.md)

For questions about **the content pipeline specifically** (state machine, Buffer integration, rate limiting, icp_signals enrichment):
→ [`60-content-pipeline.md`](./60-content-pipeline.md)

For questions about **build order and dependencies** (what to build first, what each phase enables):
→ [`70-build-order.md`](./70-build-order.md)

For questions about **how the system measures itself** (agent_runs, cost helper, runaway-prevention guards, weekly dashboard):
→ [`80-telemetry-layer.md`](./80-telemetry-layer.md)

For questions about **what the system does for the business** (the seven workflows, KR alignment, demo narrative):
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
| **Brain** | The Supabase Postgres database — the canonical memory layer |
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
| **Guard G1/G2/G3** | Per-run token cap / per-day spend ceiling / anomaly detection |

</doc:terminology>

<doc:four_layers>

The system has four architectural layers, each separable from the others:

| Layer | Responsibility | Substrate | Document |
|-------|----------------|-----------|----------|
| Channel | Surface to humans, capture input | Discord, Claude Code CLI | `50-channel-layer.md` |
| Action | Execute work, call LLMs, talk to external APIs | Python on Mac mini, launchd | `40-action-layer.md` |
| Memory | Store everything the system knows | Supabase Postgres + pgvector | `30-memory-layer.md` |
| Telemetry | Measure, prevent runaway spend, report | agent_runs ledger, Higgins, Ted | `80-telemetry-layer.md` |

The layers communicate only through the brain. No direct cross-layer dependencies.

</doc:four_layers>

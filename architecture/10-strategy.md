# Strategy Layer

<doc:layer>strategy</doc:layer>
<doc:stability>high — edit rarely</doc:stability>
<doc:depends_on>none</doc:depends_on>
<doc:referenced_by>20-architecture-overview.md, 40-action-layer.md, 70-build-order.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file captures the **why** behind the architecture: the design principles, the three-tier handoff model, the north star, and how the system maps onto value-stream thinking with explicit gemba points. The implementation can change; this layer should not.

---

## North Star

<north_star>

**Sustainable long-term contract engagements.**

Three key results:

- **KR1**: New contract engagements per quarter
- **KR2**: Dollar value per engagement
- **KR3**: Project → maintenance conversion rate

The architecture exists to serve these KRs. Every workflow (see `90-workflows.md`) ties to at least one KR. Every agent serves at least one workflow. Architecture that doesn't ladder up to the north star is overhead and gets cut.

</north_star>

---

## Design Principles

<principles:list>

<principle id="P1" name="Separable layers">
Channel, action, memory, and telemetry are independent layers. Any one can be replaced without rebuilding the others. Channel today is Discord; tomorrow it could be iMessage. Action today is Claude Code + Python; tomorrow it could include OpenClaw. Memory today is Supabase Postgres; the schema is portable to any Postgres elsewhere. Telemetry is its own layer alongside the other three; it observes the system rather than living inside it.
</principle>

<principle id="P2" name="Three-tier handoff">
Every workflow declares which work is Tier 1 (Claude owns it), Tier 2 (Claude prepares, human decides), or Tier 3 (human only). Most agent systems blur this; explicit declaration prevents drift toward either over-automation or under-automation.
</principle>

<principle id="P3" name="Memory is architecture">
If the system doesn't remember yesterday, it can't help with tomorrow. Persistence is not a feature added later — it is the substrate everything else attaches to. The Postgres schema is the single source of truth.
</principle>

<principle id="P4" name="Selective vectorization">
Embeddings are expensive in cost, in storage, and in cognitive overhead when reasoning about retrieval. Vectorize only what benefits from semantic recall (facts, content items, interest signals, transcripts). Leave structured/state data (follow-ups, pipeline stages, approval queues) as plain relational tables with appropriate indexes.
</principle>

<principle id="P5" name="Close loops, don't open them">
Every automation ends with a verifiable outcome — a stored fact, a posted message, a state transition recorded. No background process should silently consume input without producing trackable output. This is borrowed directly from Logan Currie's COS architecture and is non-negotiable.
</principle>

<principle id="P6" name="Learning compounds — but earn it">
Systems that get smarter from behavior beat static configurations. But learning loops add complexity. V1 discards rejected items; learning from rejection is a v2 feature once the discard path is operational.
</principle>

<principle id="P7" name="Model diversification">
Use Gemini for high-volume narrow-scope work (Tartt summarization, embedding). Use Claude for reasoning-heavy work (Keeley Content drafting, Sam evaluation). The two providers fail independently; an outage on one shouldn't stop the other.
</principle>

<principle id="P8" name="Privilege separation">
Build in the admin account, run in the agent account, version-control everything in between. The git-gate is a code review checkpoint, an audit trail, and a privilege boundary in one.
</principle>

<principle id="P9" name="Token discipline as a first-class concern">
Every LLM call passes through a single cost-emission helper that enforces per-run token caps and per-day spend ceilings, and records to a ledger that feeds anomaly detection. Agents do not call LLM SDKs directly. The runaway-spend failure mode is the most likely failure mode of a system like this; the architecture treats it as a primary risk, not an afterthought. See `80-telemetry-layer.md`.
</principle>

</principles:list>

---

## The Three-Tier Handoff Model

<model:three_tier>

The three-tier model is the spine of the system. Every agent, every job, every workflow is classified into exactly one tier. This forces the question "where should the human be?" to be answered explicitly rather than emergently.

<tier id="1" name="Claude owns entirely">
**Description**: Claude executes without review. Output is recorded; the human may inspect later but does not gate the work.

**Used for**: Transcript processing, fact extraction, source scraping, calendar/email syncing, embedding generation, briefing assembly, follow-up escalation flagging, dashboard updates.

**Risk profile**: Low-stakes, high-volume, reversible. If Claude gets it wrong, the cost is a minor annoyance or a re-run.

**Examples in this system**: Tartt's daily content collection. Briefing assembly. Discord fact extraction from capture channel.
</tier>

<tier id="2" name="Claude preps, human decides">
**Description**: Claude does the preparation, packaging, and recommendation. The human reviews and decides. The decision is recorded.

**Used for**: Content drafts, outreach messages, task candidates from meeting transcripts, source trust adjustments, schedule changes that affect commitments.

**Risk profile**: Decisions with reputational, relational, or financial consequence. Reversal is possible but costly.

**Examples in this system**: Task Tinder (Claude proposes tasks; human accepts or declines). Approval queue (Keeley Content drafts; human approves before Buffer publishes).
</tier>

<tier id="3" name="Human only">
**Description**: The system does not act. It may surface context or prepare reference material, but the work itself is yours.

**Used for**: Strategic decisions, client engagement choices, ICP refinement, pricing, personnel decisions, anything involving estate or family judgment.

**Risk profile**: High-stakes, often irreversible, requires judgment Claude cannot reliably substitute for.

**Examples in this system**: Roundtable topic selection. Final positioning statements. Decisions about which clients to take on.
</tier>

</model:three_tier>

---

## Value-Stream Mapping

<value_stream>

The system supports four primary value streams. Each has a defined entry point, transformation, and exit, with explicit gemba points where human attention is required.

<stream id="VS1" name="Content discovery to publication">
Tartt discovers (T1) → Keeley Strategy triages (T1) → Keeley Content drafts (T1) → Sam evaluates (T1) → **GEMBA: Human approves** (T2 → T3 boundary) → Keeley Distribution publishes to Buffer (T1) → Engagement measured (T1) → Interest signals updated (T1).

Single gemba point: approval. Everything else is automated.
</stream>

<stream id="VS2" name="Task identification to completion">
Meeting transcripts / emails / Discord captures (input) → Task extraction (T1) → **GEMBA: Task Tinder swipe** (T2) → Accepted tasks enter follow_ups with escalation (T1 monitoring) → Reminders in briefing (T1) → **GEMBA: Human completes work** (T3) → Status updated (T1).

Two gemba points: accepting the task and doing the work. Everything else is automated.
</stream>

<stream id="VS3" name="Knowledge capture to retrieval">
Capture (Discord, transcripts, emails) → Extraction to facts (T1) → Embedding + storage (T1) → Hybrid search on demand (T1) → Surfaced in briefings or Claude Code sessions (T1).

Zero gemba points in the capture path. The retrieval-and-use path is where humans engage.
</stream>

<stream id="VS4" name="Morning briefing assembly">
Overnight: data sweep (T1) → fact synthesis (T1) → priority extraction (T1) → 6am: briefing posted to Discord (T1) → **GEMBA: Human reads, decides daily focus** (T3) → Optional task acceptance via Task Tinder (T2).

One gemba point: reading and deciding focus. Briefing assembly is fully automated.
</stream>

</value_stream>

---

## Anti-Patterns

<anti_patterns>

Patterns this architecture explicitly rejects, and why.

<anti_pattern id="AP1" name="Everything in one daemon">
Rejected because: A single always-on process owning channel, action, and memory creates a large security surface and a single failure point. The OpenClaw model is powerful but does not fit a security-conscious solo operator.
</anti_pattern>

<anti_pattern id="AP2" name="Vectorize everything">
Rejected because: pgvector storage and embedding generation are not free. Vectorizing structured state data (follow_ups, pipeline stages) provides no retrieval benefit over indexed SQL queries. See `30-memory-layer.md` for the explicit vectorize/don't-vectorize list.
</anti_pattern>

<anti_pattern id="AP3" name="Skill marketplaces">
Rejected because: Third-party skill ecosystems are a supply-chain attack surface. All capabilities in this system are code we write (or that Claude Code writes under our review) and commit to git.
</anti_pattern>

<anti_pattern id="AP4" name="Implicit human approval">
Rejected because: "Send it if it looks good" or "publish on a delay so I can cancel" puts the burden on the human to intervene. The architecture requires explicit positive approval (Discord button click) before any Tier 2/3 boundary is crossed.
</anti_pattern>

<anti_pattern id="AP5" name="Sync between systems">
Rejected because: Multiple sources of truth create drift. The brain is the only persistence. Discord messages, Buffer posts, calendar entries reference the brain by ID; the brain does not mirror them.
</anti_pattern>

</anti_patterns>

---

## How This Maps to AI Adaptive's Business

<business_alignment>

The architecture is not abstract; it directly supports AI Adaptive operations:

- **Newsletter cadence** (The Adaptive): Content pipeline (VS1) produces drafts; you approve and publish. Interest signals (VS3) inform topic selection.
- **Executive roundtables**: Tartt surfaces relevant signals from across sources; briefings cluster them for roundtable preparation.
- **Customer discovery outreach**: Task Tinder surfaces follow-up commitments from discovery calls. People records track relationship cadence.
- **Lead Engine plugin ecosystem**: External; the brain is a peer system, not a replacement. WordPress remains the public-facing customer surface.
- **Productization advisory work**: The brain stores client context, past advisory frameworks (CFI, ALC, SVRS), and meeting outputs for cross-client pattern recognition.

The architecture is a substrate for *how AI Adaptive operates*, not a product AI Adaptive sells. That distinction matters for scope discipline.

</business_alignment>

# Architecture Overview

<doc:layer>bridge — strategy to implementation</doc:layer>
<doc:stability>medium — edit on major topology changes</doc:stability>
<doc:depends_on>10-strategy.md</doc:depends_on>
<doc:referenced_by>30-memory-layer.md, 40-action-layer.md, 50-channel-layer.md, 60-content-pipeline.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file is the structural overview that bridges strategy and implementation. It shows the four layers, the agents that live in the action layer, and how data flows through the system. Schema specifics, code patterns, and integration details live in the implementation files (30-, 40-, 50-, 60-, 80-).

---

## Four-Layer Topology

<topology>

```
┌──────────────────────────────────────────────────────────────────┐
│                      CHANNEL LAYER                               │
│  Discord (mobile + desktop)                                      │
│   ├─ #briefing         morning summary, top reading              │
│   ├─ #task-tinder      button-reactable candidate tasks          │
│   ├─ #approvals        content drafts awaiting your ✅           │
│   ├─ #capture          you → bot → facts.embeddings              │
│   ├─ #dashboard        weekly performance digest (Higgins)       │
│   └─ #system           health, errors, rate-limit alerts (Ted)   │
│                                                                  │
│  Claude Code CLI (laptop + Mac mini)                             │
│   └─ Ad-hoc sessions reading/writing the brain                   │
└─────────────────────┬────────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────────┐
│                      ACTION LAYER                                │
│  MAC MINI (executor, agent account, launchd-supervised)          │
│                                                                  │
│   Operational agents (run AI Adaptive day-to-day):               │
│   ├─ Roy Kent        inbound ICP qualifier — Claude Haiku        │
│   ├─ Tartt           content discovery — Gemini Flash, 5am       │
│   ├─ Nate Shelley    ICP signal clusterer — Claude Sonnet, wkly  │
│   ├─ Keeley Strategy ICP/positioning triage — Claude Sonnet      │
│   ├─ Keeley Content  drafting — Claude Sonnet                    │
│   ├─ Sam             output evaluation gate — Claude Haiku       │
│   ├─ Keeley Distrib. Buffer publishing — no LLM                  │
│   ├─ Briefing        morning assembly — Claude Sonnet, 6am       │
│   ├─ Meeting proc.   transcript extraction — Claude Haiku        │
│   ├─ Fact extraction Discord/email facts — Claude Haiku          │
│   └─ Discord bot     always-on Python service (router)           │
│                                                                  │
│   Telemetry agents (observe the system):                         │
│   ├─ Ted             health + cost anomaly detection (6h)        │
│   └─ Higgins         weekly dashboard — Claude Sonnet, Mon 7am   │
│                                                                  │
│   Build agents (for new work; out of scope for v1):              │
│   └─ Beard, Roy Kent PM, Roy Kent Sr Dev, McAdoo, Dani, Jamie    │
│                                                                  │
│  All LLM calls go through the cost-emission helper (see 80-)     │
│                                                                  │
│  LAPTOP (reader/builder; no scheduled jobs)                      │
│   └─ Claude Code sessions, planning, drafting, building          │
└─────────────────────┬────────────────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────────────────┐
│                      MEMORY LAYER                                │
│  HOSTED SUPABASE (Postgres + pgvector + RLS)                     │
│                                                                  │
│   Vectorized tables (semantic recall matters):                   │
│   ├─ facts                  atomic claims with provenance        │
│   ├─ content_items          discovered articles, videos, papers  │
│   ├─ interest_signals       topic vectors with weight + decay    │
│   ├─ icp_signals            ICP pain points from all sources     │
│   └─ meeting_transcripts    compiled meeting outputs             │
│                                                                  │
│   Structured tables (status, dates, owners):                     │
│   ├─ follow_ups             open commitments with escalation     │
│   ├─ task_candidates        Task Tinder queue                    │
│   ├─ tasks                  accepted tasks                       │
│   ├─ decisions              key choices with rationale           │
│   ├─ people                 relationship context                 │
│   ├─ prospects              inbound qualified leads (W1)         │
│   ├─ sources                trust scores for Tartt inputs        │
│   └─ dashboard              metrics, cadence flags               │
│                                                                  │
│   Pipeline tables (state machines):                              │
│   ├─ content_pipeline       discovered → published state         │
│   ├─ approval_queue         pending Discord approval gates       │
│   └─ buffer_posts           Buffer API status tracking           │
│                                                                  │
│   Telemetry tables:                                              │
│   ├─ agent_runs             every LLM call, cost, status         │
│   └─ outcomes               attributed business outcomes (KR1)   │
└──────────────────────────────────────────────────────────────────┘
            ▲
            │  read by
            │
┌───────────┴──────────────────────────────────────────────────────┐
│                      TELEMETRY LAYER                             │
│  Lives across action and memory; observability for the swarm.    │
│                                                                  │
│   ├─ agent_runs ledger    (memory layer table; see above)        │
│   ├─ Cost-emission helper (every LLM call goes through this)     │
│   ├─ Guard G1: per-run token cap                                 │
│   ├─ Guard G2: per-day spend ceiling per agent                   │
│   ├─ Guard G3: anomaly detection (Ted, Python, no LLM)           │
│   ├─ Ted: reactive monitoring (6-hourly)                         │
│   └─ Higgins: reflective reporting (weekly)                      │
└──────────────────────────────────────────────────────────────────┘
```

</topology>

---

## Layer Responsibilities

<layer id="channel">

**Channel layer responsibility**: Surface the system to humans and capture human input.

**What it does**:
- Posts proactively (briefing, approval requests, task candidates)
- Accepts input (capture messages, button reactions)
- Provides ad-hoc query interface (Claude Code sessions)

**What it does NOT do**:
- Hold any state (Discord history is reference, not source of truth)
- Make decisions about content (no logic in the bot beyond routing)
- Talk to LLMs directly (LLMs are invoked by action-layer agents)

</layer>

<layer id="action">

**Action layer responsibility**: Execute work — read from the brain, call out to services and LLMs, write back to the brain.

**What it does**:
- Runs scheduled jobs (launchd) and event-triggered jobs (Discord events)
- Calls LLM APIs (Gemini, Claude, OpenAI for embeddings)
- Calls external APIs (Buffer, YouTube, ArXiv, RSS feeds)
- Maintains the Discord bot as a long-running process

**What it does NOT do**:
- Persist anything outside the brain (no local caches that diverge from Supabase)
- Make decisions classified as Tier 2 or 3 without human gate
- Self-modify code without commit-and-deploy through the git-gate

</layer>

<layer id="memory">

**Memory layer responsibility**: Be the single source of truth for everything the system knows.

**What it does**:
- Stores structured data (people, follow-ups, decisions)
- Stores vectorized data (facts, content, interests, transcripts)
- Provides hybrid search (full-text + vector similarity)
- Enforces row-level security for multi-context scoping

**What it does NOT do**:
- Make decisions (no Postgres functions that take action)
- Talk to external services (writes are inbound from action-layer agents only)
- Cache derived data unless explicitly needed for query performance

</layer>

---

## The Ted Lasso Agent Roster (Operational + Telemetry Subset)

<agent_roster>

The full roster includes build agents (Beard, the Roys, McAdoo, Dani, Jamie). For v1 of this architecture, only the operational and telemetry agents are in scope. Build agents share the memory layer but run on different schedules with different tool scopes.

<agent name="Roy Kent" tier="1" llm="Claude Haiku" trigger="webhook from WordPress">
**Role**: Inbound ICP qualifier. When a prospect submits a form on aiAdaptive.co, Roy reads the profile, scores ICP fit, emits icp_signals from scorecard pain-point answers, and creates task_candidates for high-fit prospects.
**Reads**: prospects (existing matches), decisions (ICP criteria), interest_signals.
**Writes**: prospects (new row with fit score), icp_signals, task_candidates (for high-fit only).
**Why Haiku**: Qualification against stored criteria is rubric-based reasoning, not deep synthesis. Haiku is right-sized.
**Workflow served**: W1.
</agent>

<agent name="Tartt" tier="1" llm="Gemini 2.5 Flash" trigger="launchd 5am daily">
**Role**: Content discovery from RSS, HN, ArXiv, YouTube, newsletters.
**Reads**: sources (with trust scores), interest_signals.
**Writes**: content_items (with embeddings), icp_signals (pain points extracted from summaries), sources.last_polled_at.
**Why Gemini Flash**: High-volume narrow-scope summarization where Flash's price and speed dominate. Article text is supplied; no model "knowledge currency" needed.
**Workflows served**: W3 (primary), W2 (contributes icp_signals).
</agent>

<agent name="Nate Shelley" tier="1" llm="Claude Sonnet" trigger="launchd weekly (Sunday 8pm)">
**Role**: Synthesize the past 7 days of icp_signals into clustered themes. Identifies top ICP pain points with frequency and source diversity.
**Reads**: icp_signals (last 7 days).
**Writes**: A weekly synthesis posted to #briefing channel and stored as a fact for retrieval.
**Why Sonnet**: Clustering and synthesis from many signals is a reasoning-depth task. Runs weekly, so cost is bounded.
**Workflow served**: W2.
</agent>

<agent name="Keeley Strategy" tier="1" llm="Claude Sonnet" trigger="event-driven on content_item">
**Role**: ICP and positioning fit triage. Decides whether a discovered content item is relevant enough to draft against. Also marks which pain points the article addresses (icp_signals enrichment).
**Reads**: content_items, interest_signals, decisions (ICP/positioning).
**Writes**: content_pipeline (promotes item from discovered → triaged, or marks declined), icp_signals (pain points addressed).
**Workflows served**: W3 (primary), W2 (contributes icp_signals).
</agent>

<agent name="Keeley Content" tier="1" llm="Claude Sonnet" trigger="event-driven on triaged item">
**Role**: Generative content drafting — outline, draft, refine.
**Reads**: content_items (triaged), facts (for grounding), interest_signals, icp_signals (current themes from Nate Shelley).
**Writes**: content_pipeline (draft text, stage = drafted).
**Workflow served**: W3.
</agent>

<agent name="Sam Obisanya" tier="1" llm="Claude Haiku" trigger="event-driven on draft completion">
**Role**: Output evaluation gate. Reviews Keeley Content drafts against style, positioning, and ICP fit before they reach the human approval queue.
**Reads**: content_pipeline (drafts), interest_signals, decisions (style/positioning).
**Writes**: content_pipeline status transitions (passes to approval_queue or returns to draft).
**Why this agent**: Compresses human decision time. The human only sees drafts that already passed automated review.
**Workflow served**: W3.
</agent>

<agent name="Keeley Distribution" tier="1" llm="none" trigger="event-driven on approved item">
**Role**: Buffer API client with rate limiting. Takes approved drafts and schedules them.
**Reads**: content_pipeline (approved items), buffer_posts (for rate-limit state).
**Writes**: buffer_posts (with Buffer post ID), content_pipeline (stage = scheduled/published).
**Why no LLM**: This is deterministic API plumbing. Tokens spent here are wasted.
**Workflow served**: W3.
</agent>

<agent name="Briefing" tier="1" llm="Claude Sonnet" trigger="launchd 6am daily">
**Role**: Synthesize overnight changes into a morning briefing posted to #briefing.
**Reads**: prospects (new from W1), follow_ups (with escalation), content_items (top-ranked), facts (new this period), task_candidates (top 5), dashboard, icp_signals (top theme this week).
**Writes**: dashboard (briefing_posted_at), #briefing channel.
**Workflow served**: W5 (consolidates W1, W2, W3, W4).
</agent>

<agent name="Meeting processor" tier="1" llm="Claude Haiku" trigger="filesystem watch on Granola export">
**Role**: Extract facts, follow-ups, decisions, task_candidates, and icp_signals from meeting transcripts.
**Reads**: people, prospects, decisions.
**Writes**: meeting_transcripts, facts, follow_ups, decisions, task_candidates, icp_signals, optionally prospects (if call participant was inbound).
**Workflows served**: W4 (primary), W2 (icp_signals), W6 (facts).
</agent>

<agent name="Fact extraction" tier="1" llm="Claude Haiku" trigger="event-driven on Discord capture + email">
**Role**: Extract atomic claims from #capture messages and (later) emails. Tag ICP-relevant pain mentions as icp_signals.
**Reads**: people, decisions (for context).
**Writes**: facts, icp_signals (when ICP-pain present).
**Workflows served**: W6 (primary), W2 (icp_signals).
</agent>

<agent name="Discord bot" tier="varies" llm="none" trigger="always-on event listener">
**Role**: Route messages between Discord and the brain. Handle button reactions for Task Tinder and approvals. Trigger fact extraction on #capture messages.
**Reads**: brain on query/reaction, posts on schedule from other agents.
**Writes**: facts (via fact extraction agent), task_candidates updates (from #task-tinder reactions), approval_queue updates (from #approvals reactions), outcomes (from /outcome slash command).
**Why no LLM in the bot itself**: The bot is a router. LLM calls are made by the agents the bot routes to.
</agent>

<agent name="Ted" tier="1" llm="Claude Haiku (alerting only)" trigger="launchd every 6 hours">
**Role**: Health monitoring plus real-time cost guarding. Computes anomaly detection on agent_runs (pure Python, no LLM). Calls Haiku only when summarizing complex alerts.
**Reads**: dashboard, agent_runs (for G3 anomaly detection), follow_ups (escalation), system error logs.
**Writes**: dashboard cadence flags, alerts to #system.
**Workflow served**: W7 (telemetry; feeds Higgins).
</agent>

<agent name="Higgins" tier="1" llm="Claude Sonnet" trigger="launchd Mon 7am weekly">
**Role**: Weekly performance dashboard. Reports KR movement, spend by function, workflow throughput, recorded outcomes.
**Reads**: agent_runs, content_pipeline, tasks, follow_ups, prospects, icp_signals, outcomes, dashboard.
**Writes**: #dashboard channel.
**Why Sonnet**: Synthesizing operational data into a readable, prioritized narrative. Runs weekly; cost is bounded.
**Workflow served**: W7.
</agent>

</agent_roster>

---

## Primary Data Flows

<data_flows>

<flow id="DF1" name="Content discovery → publication">
Triggered: launchd 5am.

1. Tartt polls sources → fetches new items via feedparser/HN API/ArXiv API/YouTube transcript API
2. Tartt extracts article text (trafilatura), summarizes (Gemini Flash), embeds (Gemini embedding-004)
3. Insert into `content_items` with embedding; score against `interest_signals` via cosine similarity
4. Top-N items pass to Keeley Strategy (event-driven): triage for ICP/positioning fit
5. Triaged items pass to Keeley Content: draft generation
6. Drafts pass to Sam: evaluation gate
7. Sam-approved drafts → `approval_queue`, posted to Discord #approvals with ✅/❌ buttons
8. Human ✅ → Keeley Distribution: Buffer API call with rate-limit handling
9. Buffer webhook → `buffer_posts` status updated to published

Detail: `60-content-pipeline.md`
</flow>

<flow id="DF2" name="Task identification → completion">
Triggered: meeting transcript arrival, email sync, or Discord capture.

1. Source-specific extractor (meeting_processor.py / email_triage.py / discord_capture.py) parses input
2. Candidate tasks written to `task_candidates` with source, evidence, confidence score
3. Briefing agent (or Task Tinder dedicated job) posts top candidates to #task-tinder with ✅/❌/⏰ buttons
4. Reaction event → Discord bot updates `task_candidates.status`
5. ✅ promotes candidate to `tasks` (also writes to `follow_ups` for escalation tracking)
6. Tasks visible in next briefing; escalation runs nightly to flag overdue items

Detail: `50-channel-layer.md`
</flow>

<flow id="DF3" name="Knowledge capture → retrieval">
Triggered: Discord #capture message, meeting transcript, email.

1. Capture source writes raw input to `00-inbox` equivalent staging table or directly to source-specific table
2. Fact extraction job (Claude Haiku for cost) pulls discrete claims
3. Each fact embedded (Gemini embedding-004) and written to `facts` with source provenance
4. Retrieval on demand via hybrid search: `ts_rank_cd(tsvector, query)` + `1 - (embedding <=> query_embedding)` weighted

Detail: `30-memory-layer.md`
</flow>

<flow id="DF4" name="Morning briefing assembly">
Triggered: launchd 6am daily.

1. Briefing agent queries: follow_ups where escalation_level >= 1, top 5 content_items from last 24h, new facts from last 24h, top 5 task_candidates, dashboard cadence flags
2. Synthesizes with Claude Sonnet into a structured briefing
3. Posts to Discord #briefing
4. Updates dashboard.briefing_posted_at

Detail: `40-action-layer.md`
</flow>

</data_flows>

---

## Trust Boundaries

<trust_boundaries>

<boundary id="TB1" name="Admin → agent account (git-gate)">
Code is written in the admin macOS account, committed to a git repo, then pulled into the agent account for execution. This boundary is enforced by macOS account separation; the agent account cannot write back to the admin repo.
</boundary>

<boundary id="TB2" name="Agent account → Supabase (credentials)">
The agent account holds the Supabase service-role key in macOS Keychain. The key never appears in committed code or environment files in the repo. Reads from non-privileged contexts (Claude Code sessions on the laptop) use the anon key + RLS.
</boundary>

<boundary id="TB3" name="Laptop → Supabase (RLS-scoped reads)">
The laptop can query the brain using the anon key. RLS policies restrict what the anon key can see and prevent writes that should require service-role.
</boundary>

<boundary id="TB4" name="Supabase → external APIs (none)">
Supabase does not call out to external services. All external API calls (Buffer, Gemini, Claude, RSS sources) originate on the Mac mini. This prevents lateral expansion of the trust boundary.
</boundary>

</trust_boundaries>

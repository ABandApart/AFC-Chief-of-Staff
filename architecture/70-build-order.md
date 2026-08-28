# Build Order

<doc:layer>implementation — phasing</doc:layer>
<doc:stability>medium — edit when scope or priorities shift</doc:stability>
<doc:depends_on>all prior files</doc:depends_on>
<doc:referenced_by>none</doc:referenced_by>

## Purpose

This file defines the phased implementation plan. Each phase produces a working, useful slice of the system. No phase requires later phases to be valuable on its own.

The phasing is borrowed in spirit from Logan Currie's COS getting-started document, adapted to this stack and scope.

---

## Working convention: spec-driven development

<working_convention>

**Adopted 2026-08-12 (operator).** Every build increment starts from a written
spec, and the spec states an **outcome** — what is true when this is done —
rather than a list of steps. Code follows the spec; when reality contradicts it,
**the spec gets corrected, not silently worked around.**

This is already how the good parts of this system were built (`PRD-b3-tunnel.md`,
`PRD-mcp-tool-layer.md`, `36-inbound-leads.md`), and the places it lapsed are the
places that cost real time. It is written down here so it is a standard rather
than a habit.

### What a spec must contain

| # | Requirement | The failure it prevents |
|---|-------------|-------------------------|
| **S1** | **Outcome, stated observably.** What is true when this is done, phrased so someone else could check it. Not "build the poller" — "each target's open reqs carry a first-seen date that advances only forward." | Work that is "finished" but nobody can say whether it worked. |
| **S2** | **Non-goals.** What is deliberately out, and why. | Scope drift, and re-litigating settled exclusions three sessions later. |
| **S3** | **Verification.** The test, query, or runtime check that demonstrates S1. Named before the build, not invented after. | Verification shaped to fit whatever got built. |
| **S4** | **Settled vs open, separated.** Open questions named as open — `36-inbound-leads.md` §1/§5 is the model: binding rules in one section, undecided options in another, and no pretending the second is the first. | Guessing at an unmade decision and burying the guess in code. |
| **S5** | **No forward references to artifacts that do not exist.** If a spec says a thing is "unchanged from v0.2.0," v0.2.0 must be in the repo. | `35-` §2 declared `outreach_touches`, `outreach_watch_signals`, and `outreach_events` unchanged from a 0.2.0 whose DDL was never committed — they had to be reconstructed from prose scattered across five sections. |
| **S6** | **Dated status.** So staleness is visible rather than assumed-current. | `35-` §15 listing "B3 tunnel" as a build step months after B3 shipped. |

### What happens at build time

- **Before starting an increment**, confirm a spec exists that satisfies S1 and
  S3. If it does not, say so and write one first — a short one is fine; an absent
  one is not.
- **When the spec collides with reality**, stop and record the deviation *in the
  spec or the decision log*, with the reasoning. Two from Track O increment 1:
  `stage NOT NULL` could not survive an inbound lead that has no knowable funding
  stage, and the packet ready-guard's ordering was wrong once you notice `now()`
  is transaction time. Both are written down; neither was quietly patched.
- **When a spec is silent**, that is an open decision (S4), not licence to pick.
  Surface it.

### What this is not

Not a gate on every commit, and not a reason to write a document before a
one-line fix. The unit is the **increment** — a slice big enough to have an
outcome worth stating. Below that, the decision log is enough.

</working_convention>

---

## Phase Overview

> **Target-state re-sequencing (2026-07-28, PROPOSED).** The memory substrate
> is pivoting from flat `facts` to a cognee entity graph, and a git-authored
> control plane (skills/loops/playbooks) is being added. Two new phases — **3.6
> Control plane** and **3.7 Memory migration** — insert before Phase 4, so every
> agent from Phase 4 on builds against graph memory + the control plane. Phases
> 4–13 keep their scope and order; what changes underneath them is the memory
> API (cognee `search`/`cognify` instead of the RRF SQL) and the telemetry model
> (labeling + soft ceiling + reconciliation, per mitigation M1 — G1/G2 pre-flight
> gates and per-agent keys are deprecated). New ingest/output channels (email,
> Drive) and external exposure (tunnel, B3) are added as a later track. See
> `25-target-state.md` and the full plan in `26-cognee-migration-plan.md`. All of
> this is gated on the cognee go/no-go (spike verdict: proceed-with-mitigations,
> `SPIKE-cognee-eval-2026-07.md`).

> **Track O — Outreach CRM (2026-08-08, ADOPTED).** A parallel track, not a
> renumbering: the cold-outreach engine specified in `35-outreach-crm.md` §15
> (17 steps, ~17 days; closed loop with evidence and BCC at ~11 days). SQL-first
> (`outreach_*` tables in `aiadaptive_cos`, migration 0007). Depends on Task
> Tinder (Phase 5) for its decision cards, and on B3 only for the phone Shortcut
> — **not** for BCC send-capture, which is a pull channel (same exemption as the
> Granola poller). **Sequencing imperative: the evidence poller (O-step 6)
> starts as early as possible regardless of everything else** — `first_seen_at`
> accrues only forward, and posting-age data cannot be bought retroactively.
> Inbound handling is deliberately open (`36-inbound-leads.md`) and is NOT part
> of this track.

<phase_overview>

| Phase | Theme | Duration | Outcome |
|-------|-------|----------|---------|
| 1 | Foundation | Week 1 | Brain reachable, repo structure, credentials in place |
| 2 | Telemetry primitives | Week 2 | agent_runs, cost helper, G1 + G2 guards live; outcomes table scaffolded |
| 3 | Capture and recall | Weeks 3–4 | Discord bot live with #capture and #briefing |
| **3.6** | **Control plane** *(new)* | ~2–3 days | skills/loops/playbooks conventions in git; one scheduler daemon owns loops |
| **3.7** | **Memory migration (cognee)** *(new)* | ~2 weeks | graph memory replaces flat `facts`; telemetry re-plumbed (M1); embeddings normalized (M2) |
| 4 | Discovery | Weeks 5–6 | Tartt running, content_items populated, reading recommendations in briefing |
| 5 | Task Tinder | Week 7 | Candidate task surfacing and accept/decline working |
| 6 | Inbound prospect intake (W1) | Week 8 | Roy Kent live; WordPress webhook; prospects in briefing |
| 7 | Discovery call processing (W4) | Week 9 | Meeting processor extracts follow-ups, tasks, icp_signals |
| 8 | Content pipeline (no Buffer) | Weeks 10–11 | Keeley cluster + Sam producing drafts to #approvals |
| 9 | Buffer integration | Week 12 | Keeley Distribution live; approvals publish to Buffer |
| 10 | ICP intelligence (W2 synthesis) | Week 13 | Nate Shelley running weekly; signal-of-the-week in briefing |
| 11 | Dashboard (W7) | Week 14 | Higgins live; #dashboard channel; G3 anomaly detection in Ted |
| 12 | Hardening | Week 15 | Backups verified, alerts tuned, runbook documented |
| 13 | Engagement feedback (v2) | Weeks 16+ | Closing the loop: learn from what's published |
| **14** | **Exposure — tunnel (B3)** *(new)* | ~2–3 days | authenticated API via Cloudflare/Tailscale tunnel; DB stays local |
| **15** | **Email channel** *(new)* | ~3–4 days | inbound ingest (B1) + drafted replies (B2) |
| **16** | **Google Drive channel** *(new)* | ~3–4 days | ingest shared docs (B1) + document output (B2) |
| **Track O** | **Outreach CRM** *(new, parallel — 2026-08-08)* | ~17 days (~11 to closed loop) | Evidence poller + scoring + Selector sequencing + deterministic packets + BCC capture + Trent Crimm watchlist. Steps and dependencies in `35-outreach-crm.md` §15. **After Phase 6** (operator, 2026-08-11 — was "after Phase 5"); B3 needed only for the Shortcut. |
| **3.8** | **`retrieval.py` — B1 scope wrapper** *(new — 2026-08-10)* | ~1 day | The specified-not-built retrieval scope enforcement (`Scope` enum, default `UNTRUSTED`, scopes never union, CI grep forbids raw `cognee.search`). Load-bearing prereq for Track I **and** the cognee keep/kill fallback (`retrieval.py` must stay the only call site). Build **next**. |
| **Track I** | **Interactive boundary — MCP tool layer** *(new, optional-but-recommended — 2026-08-10)* | ~3–4 days | Gated `_lib/brain_tools` core over local stdio MCP + Gateway REST; read/promote/act tools; `brain_reader` RO role + `v_*` views. Lets our own loop / Claude Code / Hermes drive the brain without bypassing B1/B2/B4. Spec: `PRD-mcp-tool-layer.md` (ADR-0001). Depends on 3.8 + B2 + B3 (all met after 3.8). Runs parallel to Phase 4. |
| **Track H** | **Hermes optional shell** *(new, OPTIONAL — 2026-08-10)* | ~2–3 days integration | Adopt Hermes as an *additional* interactive front-end against the Track I boundary (multi-channel + voice + subagents), sandboxed to preserve B1/B2/B4 (self-authored skills disabled). **Not on any critical path**; build only if the interactive capability, proven first with our own loop, justifies it. Spec: `PRD-hermes-optional-shell.md` (ADR-0001 D2). |

Total to a fully-functional v1 (Phases 1–12): ~15 weeks of evenings/weekends,
plus ~2–3 weeks for the memory migration (3.6 + 3.7) if the pivot is taken.
Track O runs alongside from Phase 5 onward.
Phases 14–16 (channels & exposure) are a later, additive track — each behind the
trust boundaries B1/B2, with the tunnel (14) before any external channel.

> **▶ ACTIVE build order (updated 2026-08-10 — supersedes the numeric
> sequence above; see the decision log).** Done through Phase 3.7 + **Track C ch.1
> (Granola, live)** + **B2 (approval gate — live, smoke-verified 2026-08-03)** +
> **B3 (tunnel/ingest API — VERIFIED 2026-08-10, barry-agent)**.
> Next, driven by the operator's lead-gen + market-intelligence priority, with the
> interactive-boundary work (ADR-0001) inserted immediately:
> **~~B2 ✅~~ → ~~B3 ✅~~ → ~~`retrieval.py` (3.8) ✅~~ → ~~Track I ✅~~ →
> ~~Phase 4 (Tartt discovery) ✅ 2026-08-11~~ → ~~Phase 5 (Task Tinder) ✅ 2026-08-11~~ →
> ~~Phase 6 (Roy Kent, lead-gen) ✅ 2026-08-11, runtime-verified~~ →
> **Track O (Outreach CRM) ← IN PROGRESS** (increment 1 of 5 landed 2026-08-12:
> schema + evidence poller + `Scope.TARGET`; the poller loop ships disabled and
> wants real targets seeded + activating, since `first_seen_at` only accrues
> forward) → re-evaluate Phase 10 (Nate/ICP) readiness → Phase 8/9 (content
> pipeline) → … → Phase 7 (meeting processor) later. Hermes (optional shell)
> only if the interactive capability earns it.**
> **(Reordered 2026-08-11 — twice.)** First: **Phase 5 (Task Tinder) restored to
> its slot right after Phase 4** — it closes the `task_candidates` round-trip that
> Phase 4 exists to trial (the 2026-08-03 reprioritization named Phase 5 as that
> trial but omitted it from the sequence). Then, **operator directive: Phase 6
> moved before Track O** (was Phase 10 → Track O → Phase 8/9 → Phase 6 → Phase 7;
> now Phase 6 → Track O → re-evaluate Phase 10). Phase 6's DF3-mentioned
> `outreach_targets` seed is **deferred to Track O** (operator, 2026-08-11):
> WordPress inbound leads are already-qualified, Track O's list is unqualified
> outbound prospecting — different tables, different intent. Google Drive
> (Track C ch.2) is **paused**. Specs: `ADR-0001-hermes-federation-and-brain-boundary.md`,
> `PRD-mcp-tool-layer.md`, `PRD-hermes-optional-shell.md`, `PRD-phase-4-discovery.md`,
> `40-action-layer.md`, `36-inbound-leads.md`.

**Why Phase 2 (telemetry primitives) comes second**: every LLM call from Phase 3 onward must go through the cost helper. Building telemetry first means it's never retrofitted; every agent is born observable and rate-limited. This is a small phase (≤1 week) but blocking on everything that follows. *(Target-state note: Phase 3.7 MW1 re-plumbs this from pre-flight gates + per-agent keys to labeling + a soft post-hoc ceiling + monthly reconciliation, because cognee owns the call site — see `26-cognee-migration-plan.md`.)*

**Why the memory migration (3.7) comes before Phase 4**: Tartt and every agent after it read and write the brain. Pivoting the memory substrate first means the agents are built once, against the graph API, rather than built on flat `facts` and rewritten. The current corpus (~2 facts) makes the data migration itself trivial — the cost is the code, not the data.

**Why W1 (Phase 6) comes before W3 content pipeline**: prospects are the highest-leverage workflow for KR1, and the implementation depends only on Phase 3 (Discord bot for surfacing) and Phase 5 (Task Tinder for action proposals). Putting it before the content pipeline gets value to the north star sooner.

**Why W2 ICP intelligence (Phase 10) comes late**: Nate Shelley depends on icp_signals volume. Signals come from agents built in Phase 4 (Tartt), 6 (Roy Kent), 7 (Meeting processor), and 8 (Keeley Strategy). Until those are emitting, weekly clustering has nothing to cluster.

</phase_overview>

---

## Phase 1: Foundation

<phase id="1" name="Foundation">

**Goal**: Brain reachable, repo structure, credentials in place. Nothing automated yet.

**Tasks**:

1. Provision Supabase Pro project. Enable pgvector, pg_trgm extensions.
2. Initialize private git repo `aiadaptive-cos` on GitHub.
3. Create directory structure:
   ```
   aiadaptive-cos/
   ├── architecture/      # the .md files in this document
   ├── migrations/        # numbered .sql files
   ├── agents/            # one subdir per agent
   │   └── _lib/          # shared modules (cost helper lives here)
   ├── cli/               # operator-facing CLI helpers
   ├── scripts/           # backup, deploy, etc.
   └── README.md
   ```
4. Write `migrations/0001_initial_schema.sql` from `30-memory-layer.md`. Apply via Supabase SQL editor.
5. Set up macOS account separation if not already done:
   - `admin` account: where you build and commit
   - `agent` account: where everything runs
6. Configure Keychain entries on `agent` account for all credentials in `40-action-layer.md`'s credential inventory.
7. Clone repo to `~/agents/` on the `agent` account. Set up `uv` and per-agent venvs.
8. Verify connectivity: from `agent` account, run a small Python script that connects to Supabase and queries `select now()`.

**Done when**: You can run a Python script on the Mac mini that reads from and writes to Supabase, and the same repo exists on the laptop for ad-hoc Claude Code sessions.

**Risk**: Low. This is plumbing.

</phase>

---

## Phase 2: Telemetry Primitives

<phase id="2" name="Telemetry primitives">

**Goal**: Cost helper, agent_runs ledger, G1 (per-run cap) and G2 (per-day ceiling) live. Every subsequent agent uses the helper from day one — no retrofitting.

**Tasks**:

1. Apply schema migration adding `agent_runs` and `outcomes` tables (from `30-memory-layer.md` telemetry tables block).
2. Implement `~/agents/_lib/runs.py`:
   - `agent_run` context manager
   - `RunContext` with `call_gemini`, `call_anthropic`, `call_embedding` methods
   - Price table constant (per `80-telemetry-layer.md`)
   - Pre-call ceiling check (G2)
   - Token cap enforcement at call site (G1)
   - Row write on context exit
3. Write a unit test that drives the helper through:
   - Successful call (row written, cost computed)
   - Token cap exceeded (row written with `token_cap_exceeded` status)
   - Daily ceiling exceeded (raises before call; no row written for the refused call)
   - Provider error (row written with `failed` status)
4. Implement `~/agents/cli/spend.py` for ad-hoc cost queries: spend by agent, by function, by day. Used to validate the ledger is populating correctly.
5. Document a sample integration in the README: how a new agent imports and uses the helper.

**Done when**: A test agent in `~/agents/test/` makes 5 Gemini calls and 5 Claude calls, the agent_runs table shows all 10 rows with correct cost computation, and a deliberately-too-large prompt is rejected by G1 with a clean failed row.

**Risk**: Medium. Getting the price table right and handling provider SDK differences (streaming, error shapes) takes care. Worth the time — every later phase depends on this.

</phase>

---

## Phase 3: Capture and Recall

<phase id="3" name="Capture and recall">

**Goal**: Discord bot live. You can capture thoughts to the brain and ask Claude Code to recall them.

**Tasks**:

1. Create Discord server and channels per `50-channel-layer.md` server_layout.
2. Register Discord bot application; generate bot token; store in Keychain.
3. Implement `~/agents/discord-bot/`:
   - `run.py` connects and registers cogs
   - `cogs/capture.py` listens to #capture, reacts ⏳, calls fact extraction (via the cost helper), replaces with ✅
   - Fact extraction module uses cost helper with Claude Haiku
   - Embedding calls go through cost helper with Gemini text-embedding-004
   - Writes to `facts` table
4. Implement `~/agents/cli/recall.py`: hybrid search from `30-memory-layer.md`. Used from laptop Claude Code sessions.
5. Implement briefing skeleton (static "good morning" with system status; no real synthesis yet) posting to #briefing on a launchd 6am schedule.
6. Write launchd plists for Discord bot (KeepAlive) and briefing skeleton (6am). Load both.
7. Implement `/outcome` slash command in the Discord bot — opens a modal, writes to `outcomes` table. Capture starts now even though Higgins won't query it for weeks.

**Done when**: You capture a thought in Discord, see it acknowledged, and ten minutes later can ask Claude Code on your laptop "what did I capture about X" and get the right fact back. Cost shows up in `agent_runs`.

**Risk**: Medium. discord.py setup has known footguns (intents, bot permissions). Budget extra time.

</phase>

---

## Phase 4: Discovery (Tartt)

<phase id="4" name="Discovery">

**Goal**: Tartt running daily. Top reading shows up in your briefing. ICP-signal extraction begins populating data for W2.

**Tasks**:

1. Implement `~/agents/tartt/`:
   - Source fetchers (feedparser, HN Algolia, ArXiv API, YouTube transcripts, newsletter RSS)
   - `extract.py`: trafilatura wrapper
   - `summarize.py`: Gemini Flash via cost helper, structured output (summary + why-it-matters + ICP pain points)
   - `embed.py`: Gemini text-embedding-004 via cost helper
   - `score.py`: cosine similarity against interest_signals
   - `run.py`: orchestrates pipeline; idempotent on retry
2. Seed `sources` table with initial curated list.
3. Seed `interest_signals` with starting topics: "AI for SMB", "productization", "consulting frameworks", "AI market dynamics", "cognitive science applied to learning", "agile/lean". Embeddings generated via Gemini.
4. Write launchd plist for Tartt at 5am. Load it.
5. Upgrade briefing skeleton to real briefing (Claude Sonnet via cost helper) including top 5 content_items.
6. icp_signals starts populating from Tartt's pain-point extraction. Table is queryable but not yet synthesized.

**Done when**: Tartt runs at 5am, populates content_items overnight, your 6am briefing includes 5 relevant reading recommendations, and icp_signals has rows from Tartt's pain-point extraction.

**Risk**: Medium-high. Source fetching has many edge cases. Plan to iterate on error handling for a week after first run.

</phase>

---

## Phase 5: Task Tinder

<phase id="5" name="Task Tinder">

**Goal**: Candidate tasks surfaced in Discord with accept/decline buttons.

**Tasks**:

1. Implement task extraction:
   - `~/agents/extractors/discord_tasks.py`: extracts "I'll do X" / "Remind me to Y" patterns from #capture messages via cost helper
   - (Meeting-transcript task extraction comes in Phase 7)
2. Implement `~/agents/discord-bot/cogs/task_tinder.py`:
   - Polls `task_candidates` every 15 minutes for new high-confidence pending candidates
   - Posts each with ✅/❌/⏰ buttons
   - Button handlers per `50-channel-layer.md`
   - ✅ creates `tasks` and `follow_ups` rows
3. Briefing agent surfaces "Pending in Task Tinder: N candidates" line.
4. Nightly job resets deferred candidates back to pending once `decided_at` passes.

**Done when**: A captured #capture message containing "I'll send Alex the doc tomorrow" produces a task_candidate, which appears in #task-tinder with buttons, and ✅ promotes it to tasks + follow_ups.

**Risk**: Medium. Extraction quality determines noise. Tune confidence thresholds based on first week.

</phase>

---

## Phase 6: Inbound Prospect Intake (W1)

<phase id="6" name="W1 Inbound prospect intake">

**Goal**: Roy Kent live. Inbound prospects from WordPress show up in next morning's briefing with ICP fit scores. icp_signals collects pain points from scorecard responses.

**Tasks**:

1. Apply schema migration for `prospects` table.
2. Implement WordPress Lead Engine outbound webhook on new CRM profile and scorecard submission. Webhook posts JSON to Mac mini at a known endpoint.
3. Implement `~/agents/roy-kent/`:
   - Lightweight HTTP server (FastAPI) on the Mac mini listening for webhooks
   - On payload: dedup against prospects.wordpress_profile_id, then call Claude Haiku via cost helper with ICP criteria prompt
   - Write prospects row; emit icp_signals from scorecard pain-text answers; create task_candidates if `icp_fit_score >= 0.7`
4. Update Briefing agent to include "New prospects" section (W1 contribution).
5. Configure WordPress firewall/access so the webhook endpoint is reachable but rate-limited.

**Done when**: A test scorecard submission on aiAdaptive.co produces a prospects row, icp_signals row(s), optionally a task_candidate, and appears in the next morning's briefing.

**Risk**: Medium. Webhook reliability and WordPress hook integration depend on existing Lead Engine internals.

</phase>

---

## Phase 7: Discovery Call Processing (W4)

<phase id="7" name="W4 Discovery call processing">

**Goal**: Meeting transcripts from Granola become facts, follow-ups, decisions, task_candidates, and icp_signals automatically.

**Tasks**:

1. Implement `~/agents/meeting-processor/`:
   - Filesystem watcher on Granola export folder
   - On new transcript: call Claude Haiku via cost helper with structured-extraction prompt
   - Outputs: meeting_transcripts row, facts rows, follow_ups rows (yours), task_candidates rows, icp_signals rows, decisions rows, optionally a link to prospects (if call participant matched)
2. Implement people-record linking: meeting participant names matched against `people` table via trigram similarity; new people created if no match.
3. Update Briefing agent to surface discovery-call summaries in a "Discovery follow-ups" section.

**Done when**: A discovery call transcript appears in Granola's export folder; within 30 minutes, the brain has the meeting, facts, follow-ups, and icp_signals from it, and the next briefing surfaces what you owe.

**Risk**: Medium. Speaker disambiguation and commitment extraction quality determine value. Iterate.

</phase>

---

## Phase 8: Content Pipeline (No Buffer Yet)

<phase id="8" name="Content pipeline minus publish">

**Goal**: Keeley cluster + Sam produce drafts to #approvals. Approvals are recorded but don't publish anywhere yet.

**Tasks**:

1. Implement `~/agents/keeley-strategy/`: event-driven on new high-scoring content_items. Cost helper + Claude Sonnet. Writes content_pipeline rows. Also emits icp_signals for pain points addressed by triaged articles.
2. Implement `~/agents/keeley-content/`: event-driven on `triaged` rows. Cost helper + Claude Sonnet. Writes draft_text.
3. Implement `~/agents/sam/`: event-driven on `drafted` rows. Cost helper + Claude Haiku. Writes sam_evaluation JSON. Transitions to `sam_passed` or back to `triaged` (max 2 cycles).
4. Implement `~/agents/discord-bot/cogs/approvals.py`:
   - Watches for content_pipeline rows transitioning to `sam_passed`
   - Creates approval_queue row; posts to #approvals with ✅/❌/✏️ buttons
   - Button handlers per `50-channel-layer.md`
5. Approved rows transition to `approved` but go no further (Keeley Distribution doesn't exist yet).

**Done when**: A Tartt-discovered article flows Strategy → Content → Sam → #approvals; you ✅ and the row marks `approved`.

**Risk**: High. First multi-agent coordination test. Plan for debugging.

</phase>

---

## Phase 9: Buffer Integration

<phase id="9" name="Buffer integration">

**Goal**: Approved drafts publish to Buffer.

**Tasks**:

1. Generate Buffer access token; store in Keychain.
2. Implement `~/agents/keeley-distribution/`:
   - Event-driven on approval_queue rows transitioning to `approved`
   - BufferRateLimiter (per `60-content-pipeline.md`)
   - Channel routing based on draft format
   - Creates Buffer update via API; writes buffer_posts row
3. Implement `~/agents/buffer-status/`: launchd every 30 minutes; polls Buffer for scheduled posts; updates buffer_posts.posted_at and content_pipeline.stage.
4. End-to-end test with a real post.

**Done when**: You ✅ a draft in Discord and 30 minutes later see it scheduled in Buffer. After Buffer publishes, the brain reflects `published`.

**Risk**: Medium. Buffer API quirks; first integration takes 2-3 iterations.

</phase>

---

## Phase 10: ICP Intelligence (W2 Synthesis)

<phase id="10" name="W2 ICP intelligence synthesis">

**Goal**: Nate Shelley running weekly. Top ICP signal themes surface in Higgins's dashboard and Monday morning's briefing.

**Tasks**:

1. Implement `~/agents/nate-shelley/`:
   - launchd Sunday 8pm weekly
   - Pulls icp_signals from last 7 days
   - Clusters via cosine similarity (simple agglomerative; HDBSCAN later if volume warrants)
   - Calls Claude Sonnet via cost helper to produce synthesis of top 5 clusters
   - Writes synthesis as a fact with `domain='icp-intelligence'`
   - Updates icp_signals.cluster_id for clustered rows
   - Posts summary to #briefing channel
2. Update Briefing agent to surface "ICP signal of the week" section.
3. Add a CLI tool `~/agents/cli/icp.py` for ad-hoc query of recent icp_signals and clusters.

**Done when**: Sunday 8pm Nate Shelley runs, produces a synthesis of last week's ICP signals, posts to #briefing, and Monday's briefing includes the top theme.

**Risk**: Medium. Cluster quality depends on signal volume from prior phases. If volume is too low (<10/week), the synthesis is noisy — that's a signal to improve emission in upstream agents, not to give up on Nate.

</phase>

---

## Phase 11: Dashboard (W7) and G3

<phase id="11" name="W7 Dashboard and G3 anomaly detection">

**Goal**: Higgins live. Weekly dashboard in #dashboard. Ted's G3 anomaly detection running.

**Tasks**:

1. Implement `~/agents/higgins/`:
   - launchd Monday 7am weekly
   - Reads agent_runs, content_pipeline, tasks, prospects, icp_signals, outcomes, follow_ups, sources
   - Calls Claude Sonnet via cost helper to synthesize the weekly dashboard format (per `80-telemetry-layer.md`)
   - Posts to #dashboard
2. Implement `~/agents/ted/` with full v1 scope:
   - Health checks (existing scope)
   - G3 anomaly detection (pure Python on agent_runs; no LLM)
   - Ceiling proximity alerts (G2 80% threshold)
   - Failure-count alerts
   - Pinned status message in #system
   - Claude Haiku only for alert summarization
3. Implement `/dashboard` slash command for ad-hoc snapshot.
4. First weekly dashboard review with you reading and adjusting metric thresholds.

**Done when**: First Monday after deployment, Higgins posts a complete dashboard. Ted is detecting anomalies and ceiling proximity. You can read the dashboard and decide whether the system is earning its keep.

**Risk**: Low-medium. Most pieces are reads against existing tables. The G3 statistics need at least 7 days of agent_runs data to produce stable baselines.

</phase>

---

## Phase 12: Hardening

<phase id="12" name="Hardening">

**Goal**: System is monitored, backed up, and resilient to common failures. Runbook documented.

**Tasks**:

1. Implement backup script (`brain_backup.sh`) per `30-memory-layer.md`. Verify backups restore to a test database (separate Postgres cluster or hosted instance, whichever the brain lives on at Phase 12).
2. Audit log paths for sensitive data leakage. Rotate logs older than 30 days.
3. Add `pip-audit` to pre-push git hooks on the admin account.
4. Document the runbook: how to restart agents, investigate failures, roll back a bad deploy.
5. Tune G2 ceilings based on 4+ weeks of real spend data.
6. Tune G3 threshold (2× rolling median) if false-positive rate is high.

**Done when**: A simulated failure (Tartt crash, Supabase blip, Discord bot kill) produces an alert in #system within 6 hours and the runbook tells you exactly how to recover.

**Risk**: Low. Engineering hygiene; well-defined.

</phase>

---

## Phase 13: Engagement Feedback (v2)

<phase id="13" name="Engagement feedback">

**Goal**: Close the learning loop. Engagement data feeds back into interest_signals and source trust scores.

**Tasks**:

1. Implement engagement polling from Buffer (or platform-specific APIs).
2. Schema migration: add `engagement` JSONB to `buffer_posts`.
3. Implement engagement → signal weight update:
   - Per published post, trace back to source content_item and interest_signals scored highly against it
   - Compute engagement quality
   - Bump signal weights for engaging posts; decay otherwise
4. Implement source trust score updates.

**When to start**: After 6+ weeks of published posts.

**Done when**: interest_signals.weight observably changes in response to engagement; Tartt's surfaced items shift accordingly.

**Risk**: Medium. Engagement-weight math is easy to get wrong. Start simple.

</phase>

---

## Phase Independence

<phase_independence>

Each phase is valuable independent of later phases:

- After Phase 2: Telemetry primitives. Useful only as foundation; no standalone value yet.
- After Phase 3: Searchable thought capture and a simple briefing. That alone is useful.
- After Phase 4: Daily reading recommendation engine. Useful even without anything else.
- After Phase 5: Task surfacing from captures via mobile swipe.
- After Phase 6: Inbound prospect intake. KR1 starts to be served.
- After Phase 7: Discovery call follow-through.
- After Phase 8: Content draft generator with quality gating (copy-paste to publish).
- After Phase 9: Full publish pipeline.
- After Phase 10: ICP intelligence synthesis.
- After Phase 11: Full dashboard with KR reporting.

If circumstances force you to stop at any phase, the prior phases continue to work. This is intentional.

</phase_independence>

---

## What Comes After v1

<post_v1>

The architecture is designed to absorb the following without restructuring:

- **Build agents** (Beard, the Roys, McAdoo, Dani, Jamie): same brain, different schedules, different tool scopes. They write to a separate set of tables (project_plans, sprint_status, code_reviews) but read from facts and decisions.
- **Multi-user RLS**: add `context_id` to tables, enable RLS policies, distribute anon keys scoped per context. Family calendar use case becomes feasible.
- **Newsletter assembly**: specialized version of the content pipeline that aggregates multiple content_items and approved drafts into a newsletter draft. Reuses Keeley Content and Sam.
- **Roundtable preparation**: Tartt runs an ad-hoc cluster on a roundtable topic; briefs you ahead of the session.
- **CRM integration**: people table grows; a CRM sync agent connects to HubSpot or whatever you settle on. Tasks and follow-ups link to people.
- **Embedded reading metric**: revisit metric #4 (dwell rate) once Buffer engagement is providing real published-post dwell data.

These are not commitments. They are evidence that the architecture has headroom.

</post_v1>

---

## Decision Log for This Architecture

<decision_log>

> **ID note (2026-08-08).** Entries below dated 2026-07-28 use the original
> `W1–W7` labels for the cognee migration workstreams. Those were renamed
> **`MW1–MW7`** to free the `W` prefix for business workflows
> (`26-cognee-migration-plan.md`). Historical entries are left verbatim — they
> are dated records — so read `W<n>` in a July entry as `MW<n>`.

| Decision | Rationale | Recorded |
|----------|-----------|----------|
| Hosted Supabase over self-hosted | Hosted security is sufficient; brain reachable from any service | 2026-05-14 |
| **Reversed**: local Postgres 17 on Mac mini for Phase 1–5 | Phase 1–5 do not require external reachability. Local wins on latency (<1ms vs ~50ms), privacy (data never leaves the box), cost (free vs $25/mo Pro tier), and offline-resilience. Phase 6 (Roy Kent WordPress webhook) is the first phase that needs external reach; the decision will be revisited then with three options on the table: Tailscale/Cloudflare tunnel + local, migrate to hosted Supabase via `pg_dump`/`pg_restore`, or pick a different hosted provider. Postgres-to-Postgres migration is mechanically simple, so the option remains cheap. | 2026-05-19 |
| **Per-agent Anthropic API keys** | One Anthropic API key per agent (`anthropic-key-ted`, `anthropic-key-keeley-strategy`, `anthropic-key-keeley-content`, `anthropic-key-roy-kent`, `anthropic-key-nate-shelley`, `anthropic-key-higgins`, …) rather than a single shared `anthropic-api-key`. Reason: provider-side spend attribution per agent. Anthropic's usage dashboard groups by key, so per-agent keys give a Anthropic-native view of cost that complements the per-call `agent_runs.agent_name` ledger. Implication for Phase 2 (telemetry primitives): the cost helper's `call_anthropic` method must look up the right key by agent name. Phase 2 PRD will encode this as a dispatcher (e.g. `KEY_BY_AGENT` mapping in `_lib/runs.py`). Agents that don't yet have a dedicated key (sam, briefing, capture, meeting-processor) get keys when their respective phases come up. | 2026-05-19 |
| Postgres over SQLite | pgvector, FKs, future multi-client, transactional consistency | 2026-05-14 |
| Selective vectorization | Cost and clarity; structured data doesn't benefit from embeddings | 2026-05-14 |
| Discord as sole mobile channel | One bot, one event model, full history searchable | 2026-05-14 |
| Gemini Flash for Tartt summarization | Volume/cost fit; news-currency claim was reframed | 2026-05-14 |
| Gemini text-embedding-004 (768d) | Consolidates Tartt stack | 2026-05-14 |
| Discard rejected items in v1 | Learning is v2 work; ship the discard path first | 2026-05-14 |
| Python for Discord bot | Stack consistency | 2026-05-14 |
| No Linux VM required | macOS account separation + git-gate sufficient; no third-party skills | 2026-05-14 |
| One gemba point in content pipeline | Sam automates pre-review; human gates final publish only | 2026-05-14 |
| Telemetry as fourth architectural layer | Observability deserves first-class treatment, not afterthought | 2026-05-14 |
| Wide-net icp_signals pattern over dedicated agent | Side-effect emission from many agents; one weekly clusterer (Nate Shelley) | 2026-05-14 |
| Single cost-emission helper enforced for all LLM calls | Single source of truth for spend tracking and runaway prevention | 2026-05-14 |
| Three guards: per-run cap, per-day ceiling, anomaly detection | Layered protection at single-call / stuck-agent / regression time scales | 2026-05-14 |
| Ted does G3 anomaly detection (not Higgins) | Computation is pure Python on agent_runs; no LLM cost; should run reactively | 2026-05-14 |
| Three metrics per agent | Discipline: token-discipline + effectiveness + outcome | 2026-05-14 |
| Defer dwell rate metric | Discord doesn't surface link clicks; revisit when Buffer engagement is in place | 2026-05-14 |
| North star: sustainable long-term contract engagements | All workflows tie to KR1, KR2, or KR3 | 2026-05-14 |
| Workflows as the architecture's testable unit | Every architectural element must serve a workflow that ties to a KR | 2026-05-14 |
| **Embedding model: `gemini-embedding-001` @ 768 dims (not `text-embedding-004`)** | The architecture docs (incl. `30-memory-layer.md` and the original decision "Gemini text-embedding-004 (768d)") name `text-embedding-004`, but that model returns **404 on embedContent** for our Gemini key / `v1beta` (confirmed live 2026-06-17, Phase 3.2). The served embedding models on this key are `gemini-embedding-001` (+ `gemini-embedding-2*`). We use **`gemini-embedding-001` with `output_dimensionality=768`** to match the `vector(768)` columns (it defaults to 3072). It only ships pre-normalized at 3072, so the cost helper **L2-normalizes** the 768-dim Matryoshka truncation. All embedding callers (capture, recall, future Tartt) go through `runs.call_embedding`, so this is a one-line change if the model ever changes again. | 2026-06-17 |
| **Cross-profile build coordination via `/Users/Shared/afc-richmond/`** | barry-admin (build/commit) and barry-agent (runtime/credentials) coordinate through world-rw markdown files: `CURRENT-PHASE.txt` + per-phase `PHASE-*.md` with `[BARRY-ADMIN]`/`[BARRY-AGENT]`/`[USER]` tags and `⏸ HUMAN ACTION` callouts naming the profile. A Claude agent in barry-agent monitors the active phase file and executes runtime tasks. Git auth is HTTPS+gh on both profiles (the shared SSH key is passphrase-protected → autonomous agents can't `ssh-add`). Cross-profile keychain writes via `sudo -u` fail (search-path) — each profile writes its own keychain in its own session. | 2026-06-17 |
| **2026-07 refactor of Phases 1–3.4** (see `architecture/PROPOSAL-2026-07-05-refactor-review.md`) | Full-system review before Phase 3.5. Shipped in one pass: **(R1)** `agent_runs.usd_cost` widened to NUMERIC(14,8) via migration 0002 — 4dp truncated cheap embedding calls to $0.0000 and carried ~5% error on Haiku calls; **(R2)** PRICE_TABLE validated *before* the paid API call; **(R3)** cached keychain reads (`_lib/creds.py`), shared connection pool (`_lib/db.py`), cached SDK clients — a 3-fact capture previously opened 7+ connections and subprocesses; **(R4)** batch, transactional fact inserts; **(R5)** G1 gated by a local chars/3 estimate, real `count_tokens` only within 80% of cap; **(R6)** hybrid search re-ranked with Reciprocal Rank Fusion (raw ts_rank_cd + cosine were on incomparable scales), canonical implementation moved to `_lib/search.py`; **(R7)** `expires_at` now enforced in recall; **(R8)** near-duplicate facts (cosine ≥ 0.95) skipped at capture; **(R9)** fact extraction via forced tool call (schema-validated, no fence-stripping); **(R10)** G2 "today" = local midnight, plus a $20/day `GLOBAL_DAILY_CEILING`; **(R12)** `/recall` slash command (shared search core) and `/outcome` fact autocomplete (fact link moved from modal free-text to a command param; FK constraint replaces the existence pre-check). Bot handles SIGTERM (pulled forward from 3.5). Nightly `pg_dump` pulled forward from Phase 12 (script pending — runtime task). Planned-phase recommendations (LISTEN/NOTIFY over polling, Batch API + prompt caching, context budgets, ack-then-process webhook + tunnel) live in the proposal and apply at their phases. | 2026-07-05 |
| **Refactor-phase handback fixes** (runtime validation findings, `PHASE-REFACTOR-2026-07.md`) | barry-agent's re-validation went green but surfaced six findings; triage: **(F1)** identical re-posts could still write facts — extraction is non-deterministic, so a re-post minted a new vague fact past the 0.95 per-fact cosine bar. Fixed with **message-level dedup**: capture hashes the normalized raw text (sha256, whitespace-collapsed + casefolded) and short-circuits before any LLM call on a seen hash; hashes live in `capture_messages` (migration 0003, applied 2026-07-06). The hash is only recorded when extraction found facts, so a no-facts 🤔 message can be retried verbatim. **(F2)** that same vague fact scored 0.551 vs pure gibberish, defeating `DEFAULT_MIN_SIM` 0.55 — floor raised to **0.57** (relevant matches ~0.65+, so margin remains). **(F3)** one-shot CLIs hit `PythonFinalizationError` pool-teardown noise on Python 3.14 — `atexit.register(close_pool)` in `_lib/db.py`. **(F4)** observability: `/recall` now logs query + result count on success; `/outcome` logs FK-rejections. **(F5)** Postgres user-LaunchAgent dies on reboot until barry-admin logs in (bit us 2026-07-06 morning) — LaunchDaemon migration folded into Phase 3.5 as a human action. **(F6)** `/outcome`'s required-choice-then-optional-fact parameter order confused on first use — accepted as-is (Discord command UX constraint; fine once known). | 2026-07-06 |
| **Cognee data-architecture spike (in flight — no decision yet)** | Before committing further to the hand-built `facts`/vectorized-tables memory model, we're evaluating a pivot to a [cognee](https://github.com/topoteretes/cognee)-style entity **knowledge graph** (graph + vector + relational, cognee 1.4.0, targeting the existing local Postgres). A 1-day **throwaway spike** on branch **`spike/cognee`** (NOT merged; harness + scoping doc `architecture/SPIKE-cognee-eval-2026-07.md` live only there) measures five gating questions with green/yellow/red thresholds: **Q1** does cognee's graph run on local Postgres or fall back to Kuzu (early signal: Apache AGE isn't installable on our box — only `vector`/`pg_trgm`); **Q2** cognify $/doc (we'd be dropping the pre-flight cost gate here — capture is ~$0.001 today); **Q3** whether contextvar+litellm-callback telemetry labels survive cognee's async internals (the agreed replacement for per-agent keys + G1/G2 gates); **Q4** keeping `gemini-embedding-001`@768 + L2-norm; **Q5** 16GB RAM footprint. Any red → **fall back to "Option C"** (add an `entities` + join table in the existing Postgres, keep the cost helper — ~3–5 days) rather than the full ~9–12-day pivot. Runtime run handed to barry-agent via `/Users/Shared/afc-richmond/SPIKE-cognee.md`. Full findings + go/no-go will be recorded as a follow-up decision-log entry. Phase 4 (Tartt) is held until the call is made. | 2026-07-28 |
| **Spike verdict + target-state architecture drafted** | The 2026-07-28 spike (3 runs) returned **proceed-with-mitigations**, no red: **Q1** graph runs on local Postgres via provider `postgres` — no Apache AGE, single-Postgres premise holds; **Q2** ~$0.005/short note, dashboard-confirmed (Anthropic $0.13 vs ~$0.11 est across all runs); **Q3** 100% telemetry-label coverage **only** via mitigation **M1** (route cognee's Anthropic through litellm's GenericAPIAdapter — the native adapter bypasses the callback); **Q4** `gemini-embedding-001`@768 accepted but **not** L2-normalized → mitigation **M2** (renormalize on write, or use pgvector cosine `<=>` only); **Q5** 459 MB peak, ample 16 GB headroom. On that basis the **target-state architecture** was designed and documented (`25-target-state.md`): the memory-plane/control-plane split, cognee graph memory, a git-authored control plane (skills/loops/playbooks) with a one-way playbook→memory publish, ingest/output channels (email, Drive), and four trust boundaries (data / approval / exposure / provenance). The **migration plan** (`26-cognee-migration-plan.md`) breaks it into three tracks — control plane (~2–3d), cognee pivot W1–W7 (~9–12d, M1/M2 baked in), channels & exposure (per-channel). The **telemetry model changes** with the pivot: pre-flight refusal (G1/G2) and per-agent keys are deprecated in favor of contextvar labeling + a litellm callback + a soft post-hoc ceiling + monthly provider reconciliation (cognee owns the call site, so the wrapping gate can't hold). Build order re-sequenced: new Phases **3.6** (control plane) and **3.7** (memory migration) before Phase 4; new Phases **14–16** (tunnel/email/Drive) as a later additive track. **Go decision (spend the effort + accept Track-C external exposure) remains the operator's**; the plan is drafted so the decision has a concrete path attached. | 2026-07-28 |
| **Cognee pivot — GO** | Operator gave the go-ahead (2026-07-28). The system pivots to the cognee entity-graph memory per `25-target-state.md` + `26-cognee-migration-plan.md`. Track A (control plane) is builder-side complete — Phases 3.6 skills/loops/playbooks + scheduler daemon. Track B (memory migration) opens as **Phase 3.7**, W1–W7, with mitigations M1 (telemetry via litellm routing) and M2 (embedding normalization) baked in. W1 (telemetry re-plumb) starts additive: `agents/_lib/telemetry_context.py` (contextvar labeling + litellm callback → `agent_runs`) lands first, non-breaking; the deprecation of pre-flight refusal (G1/G2) + per-agent keys and the reconcile CLI follow in the same workstream before W2. Checklist: `CHECKLIST-phase-3.7.md`. | 2026-07-28 |
| **Cognee migration — builder-side complete (W1–W6)** | Phase 3.7 W1–W6 landed builder-side; only runtime deploy (W7, barry-agent) and the M2 recall-quality check remain. **Telemetry (W1):** pre-flight refusal (G1 token cap, G2 hard gate) and per-agent Anthropic keys removed; replaced by contextvar labeling + a litellm callback (`_lib/telemetry_context`, M1 — 100% cognee-call coverage, proven in W2's 18 `cognify_run` rows), a **soft daily breaker** (`assert_under_ceiling`, blocks the next invocation; new `cognee` $5 ceiling), single `anthropic-api-key`, and a monthly `cli/reconcile` (ledger vs provider bill, flags >15%). **Memory (W2–W5):** cognee 1.4.0 stands up on a dedicated `aiadaptive_cognee` Postgres DB (all 3 stores, M1 routing `LLM_PROVIDER=custom`/`anthropic/claude-haiku-4-5`, embedder `gemini-embedding-001`@768); 8 DataPoints (`_lib/ontology.py`); **mode-1 capture** — free-text `cognee.add`+`cognify`, cognee does extraction/entity-resolution (message-hash dedup kept), factored into channel-agnostic `_lib/ingest.py` for the primary API channel; **graph recall** — `cognee.search(GRAPH_COMPLETION)` (`_lib/graph_recall.py`) replaced RRF hybrid search (`_lib/search.py` removed); trusted `playbooks` dataset publish (`cli/publish_playbooks.py`, B1, hash-idempotent). **Operator decisions (2026-07-28):** mode-1 capture accepted as final (no hybrid; Discord is not the primary ingestion channel — API + tools are), `/outcome` fact-link **dropped** (not rewired — graph facts are auto-extracted nodes, no stable id), and the 2 pre-pivot `facts` rows **dropped** (not migrated). Migrations 0004–0006 retire the `facts` table + all fact-link columns. **Backups fixed:** `pg_backup.sh` now dumps both DBs (the graph was previously unprotected). **W6 docs:** `30-memory-layer.md` (two-store graph model), `80-telemetry-layer.md` (labeling + breaker + reconcile) rewritten. Suite 93/93. **M2** (cognee recall quality with un-normalized 768-dim Gemini vectors) is a runtime check at W7. Checklist: `CHECKLIST-phase-3.7.md`. | 2026-07-28 |
| **Cognee migration — DEPLOYED to production (W7)** | Phase 3.7 is live (`main`@`e8369d3`, barry-agent, handback in `/Users/Shared/afc-richmond/PHASE-3.7-W7.md`). Bot restarted onto **mode-1 cognee capture + graph recall** (PID 82622); `configure_cognee()` + the M1 litellm callback wired at startup; gateway clean on `websockets 15.0.1` (closed the W2 open item). Load-bearing order held: restart onto new code → smoke capture→recall while `facts` still present → **then** migrations 0005 + 0006 (drop `facts` + all fact-link columns; **19 tables**; pre-drop safety dump of the 2 rows). Trusted `playbooks` dataset seeded (2, hash-idempotent). `/outcome` records with no fact-link; `cli.reconcile` ledger view clean. **M2 gate PASSED** — operator judged recall usefully accurate on real queries; the un-normalized 768-dim Gemini vectors caused no visible retrieval problem, so no normalization/distance change was needed. Both DBs now back up (the graph was previously unprotected — fixed this phase). **Bundled runtime work also closed:** 3.5 3c log-line check; 3.6 scheduler cutover (`com.aiadaptive.cos.scheduler` live, PID 85122, running `morning-briefing` + `nightly-backup`; old calendar plists disabled). **Phase 3 line (capture/recall/telemetry) is complete and on the graph.** Open for operator (non-blocking): H4 key rotation; monthly reconcile with dashboard figures; minor cleanup (w2_smoke leftover, disabled plist files). **Next: Phase 4 (Tartt) vs Track C (API ingestion channel)** — operator's call; the ingest core (`_lib/ingest.py`) is already channel-agnostic for the API path. | 2026-07-28 |
| **Track C started — Granola meeting channel (builder-side)** | Operator chose Track C next, first channels **Granola + Google Drive**. Built channel 1 (**Granola meeting ingest, mode-1**): a scheduled poller (`agents/granola/run.py`) reads notes via Granola's public REST API (`public-api.granola.ai/v1`, Bearer `granola-api-key`), assembles title/date/attendees + summary + transcript, and ingests each into a new untrusted **`granola`** cognee dataset through the shared `ingest_note` core (extended with backward-compatible `dataset`/`label_agent`/`label_function` overrides). `agents/_lib/granola_client.py` (stdlib urllib, no new dep); migration **0007** `channel_state` (per-channel poll watermark, reused by Drive later); `granola` $3/day ceiling; loop `loops/granola-poll.md` **ships disabled** (activate after the key is provisioned + a manual run is green). Suite 103. **Design corrections vs the old docs:** (1) the Phase-7 "Granola export folder" filesystem-watch model is **obsolete** — Granola encrypted its local cache (~Apr 2026) and now ships an official API, so this is an API poller; this channel supersedes Phase-7 ingestion. (2) **Trust boundaries:** Granola is a *pull* channel (Mac→API), untrusted content so **B1** applies, but it needs **neither B3 (no inbound exposure) nor B2 (no outbound action)** — same for Drive *ingest*. So the "B3 tunnel before any external channel" rule (which targets inbound-reachable channels) does not gate Granola/Drive-pull; B3/email/output stay deferred. **Next increments:** structured `Meeting` DataPoint (the hybrid — gate on a runtime `add_data_points` probe), then Google Drive ingest (OAuth installed-app + refresh token, the one net-new infra piece). Handoff: `/Users/Shared/afc-richmond/PHASE-TRACK-C-GRANOLA.md`; checklist `CHECKLIST-track-c-granola.md`. | 2026-07-29 |
| **Provider plan revised: local embeddings; Gemini→news only** | The first Granola poll (barry-agent, 2026-08-03) failed on two blockers, both now resolved builder-side. **(1) Gemini free-tier embed cap.** The cognee pipeline's only Gemini use was embeddings, and a 76-note first poll hit the free 100/day cap (429). Operator's standing plan: **Gemini for news ingestion only** (Tartt); all generative LLM already runs on Anthropic. Since Anthropic has no embeddings API, embeddings moved to **local FastEmbed** (`BAAI/bge-base-en-v1.5` @768, in-process ONNX — no key, no rate limit, and client transcripts never leave the box). `cognee_setup.py` reworked (no gemini key; `EMBEDDING_PROVIDER=fastembed`), `pyproject` cognee group → `cognee[postgres,fastembed]`, lock updated (adds onnxruntime + fastembed, **no torch**). This **retires M2** (bge vectors are normalized) and makes cognee embeddings free/unmetered (no ledger row). **Voyage** (`voyage-3.5`) documented as the fallback. Provider allocation table now in `80-telemetry-layer.md`. **(2) Granola blockers fixed:** transcript speaker field was `speaker.{source,attribution}` not `.name` (docs were fuller than the live tier) → `granola_client` maps `attribution` (me→owner, them→"Them") with `name` preferred if present; added a **per-run note cap** (bounds Anthropic spend/time per cycle) and **go-forward watermark seeding** (operator chose go-forward — first run seeds to now, `--backfill` opts into history). Suite 109. **Runtime remaining:** barry-agent re-syncs (`--group cognee`, downloads the bge model), prunes the half-ingested `granola` dataset from the failed poll, re-runs, and confirms **zero Gemini calls** in the ledger. | 2026-08-03 |
| **Granola structured `Meeting` hybrid — Step 1 (probe-gated)** | The channel went live mode-1 (validated: 5 real meetings, zero Gemini, `/recall` green, loop activated `331871b`). Step 1 of the hybrid adds a typed **`Meeting`** node with **`Person`** participants from the API's structured fields via `add_data_points`, alongside the mode-1 text — for *relational* recall ("which meetings was X in") that free-text extraction is weak at. `agents/_lib/meeting_graph.py` (`build_meeting_datapoint` + `add_meeting_graph`); **entity resolution = deterministic `uuid5` ids** (person→email, meeting→note-id) so the same person/meeting upserts to one node. Skips Organization + typed Fact/Decision (v1); the typed node and mode-1 content coexist unlinked. The never-run W3 `ontology_shape.py` probe was enhanced into the **gate** (traversal + resolution + dataset placement) and `tests/test_meeting_graph.py` added (deterministic ids, participant mapping). Suite 116. **Step 2 (wire `add_meeting_graph` into the live poller) is deferred until barry-agent runs the probe** — `add_data_points`' id-dedup behavior is the load-bearing assumption. Also fixed a scheduler test that the `granola-poll` activation had left red. Handoff: `PHASE-TRACK-C-MEETING-HYBRID.md`. | 2026-08-03 |
| **Meeting hybrid — Step 2 wired + `configure_cognee` cache footgun fixed** | The probe (barry-agent) **passed the gate**: `add_data_points` keys nodes on the caller-set `id` and upserts — the same Person across two meetings resolved to **one** node linked to both (entity resolution confirmed). But the as-committed probe first hit `LLMAPIKeyNotSetError (422)`: cognee's `get_*_config()` are `@lru_cache`d and `configure_cognee`'s `os.environ.setdefault` **can't override a config already read+cached** (with the `openai`/no-key default) before configure runs — tripped when an entrypoint imports `cognee.low_level` (via `ontology`) at module load. Fix: `configure_cognee()` now calls `_clear_cognee_config_caches()` (clears `get_llm/relational/vectordb/graph_config` after setting env, best-effort per getter) — order-independent config, and a latent footgun removed for every cognee entrypoint (the live bot only dodged it by import luck). With the gate green, **Step 2 wired**: `agents/granola/run.py` calls `meeting_graph.add_meeting_graph(note)` after a *captured* note, guarded (a typed-insert failure never fails the note — mode-1 is durable). Suite 116. **Runtime remaining (M3):** re-run the probe as-committed to confirm the fix, then reset `aiadaptive_cognee` (clears probe test nodes) + re-poll `--since` so the 5 real meetings gain typed nodes. | 2026-08-03 |
| **Meeting hybrid — COMPLETE (validated on real data)** | M3 done (barry-agent + barry-admin): config fix confirmed (0× 422), `aiadaptive_cognee` reset (barry-admin drop+recreate + `TRUNCATE capture_messages` so the re-poll re-ingested), playbooks re-published `--force`, and `--since 2026-07-20` re-polled the 5 real meetings → **5 `Meeting` + 3 `Person` typed nodes** alongside mode-1 (Entity 345 / DocumentChunk 19 / TextSummary 19), **anthropic-only, zero Gemini**. **Entity resolution holds on real data:** Barry Baldwin = **1 node across all 5 meetings**; a dedup re-poll left Meeting/Person counts unchanged (update-not-duplicate). Operator confirmed `/recall` returns typed participants. **Track C Channel 1 (Granola) is now fully live end-to-end: mode-1 text + typed Meeting/Person layer, local-embedded, go-forward.** `CURRENT-PHASE.txt` → `PHASE-TRACK-C-MEETING-HYBRID.md`. **Two non-blocking follow-ups (barry-agent notes):** (1) Meeting node `name` is empty — the title lives in `properties.title`; set `name` too if a cognee retrieval path keys off it. (2) inconsistent Granola attendee identities can split one person into two nodes ("Jamie" vs "Jamielcc22") — a source-data caveat, not a resolution bug (Barry proves the id logic). | 2026-08-03 |
| **Roadmap reprioritized — B2/B3 → Phase 4, meeting-processor deferred** | Operator reprioritized around **lead generation + market intelligence**, informed by discovery + content-pipeline feedback. **Google Drive (Track C ch.2) paused.** New order: **B2 (approval gate) + B3 (tunnel/ingest API) next → Phase 4 (Tartt discovery) → Phase 10 (Nate/ICP, graph-native) → Phase 8/9 (content pipeline) → lead-gen (Phase 6) → … → Phase 7 (meeting processor) later.** Rationale accepted (resolves the earlier conflict analysis): **Phase 7 is premature until clients exist** (no discovery-call transcripts to process yet), and discovery + content are the *sole* ICP inputs until then — so Nate reads those, not Phase-7 signals. Operator has **no concerns pulling B2/B3 ahead** (they were the hidden critical path for content-outbound + inbound lead-gen). **Phase 4 content decision:** ingest in Postgres+pgvector in a **hybrid config like the Granola content** (mode-1 + typed `ContentItem`) using the **local bge embedder** — no Gemini embeddings (kills the content-side free-tier exposure); Gemini stays only for news *summarization*, Anthropic for cognify, local for embed. Specs drafted: `PRD-b2-approval-gate.md`, `PRD-b3-tunnel.md`, `PRD-phase-4-discovery.md`. **Phase 5 (Task Tinder)** becomes the trial of the `task_candidates` data structure, fed by both discovery and the content pipeline. | 2026-08-03 |
| **B2 approval gate — COMPLETE (live, smoke-verified)** | The reusable human-approval gate is live (`main`@`07911b9`, barry-admin build + barry-agent runtime smoke, handback in `/Users/Shared/afc-richmond/PHASE-B2-APPROVAL-GATE.md`). Any agent taking a world-affecting action enqueues `approvals.request_approval(item_type, payload, summary)` (a `pending` row in the existing `approval_queue`, migration 0001 — no schema change) instead of acting; the `approvals` cog posts it to `#approvals` with **Approve / Reject / Edit** buttons, and a handler registered for that `item_type` runs **only** after a human clicks Approve (or Edit→approve-with-changes). Design: `agents/_lib/approvals.py` is Discord-free (handler registry, a pure `next_status` state machine + `merge_edit`, guarded DB writes, a JSONB envelope `{summary, payload, edit_field}` so the handler only ever receives the inner payload); `agents/discord_bot/cogs/approvals.py` is the Discord surface (**persistent Views**, `timeout=None` + stable per-row `custom_id`s, re-attached on startup so cards survive a bot restart; a **10s poller** posts rows enqueued by separate agent processes). **Idempotency is the DB's** — `decide()` does `UPDATE … WHERE status='pending'`, so a double-click executes exactly once. **Trust rule:** the human click is the only authority — no `item_type` handler runs without a Discord transition; ingested content can populate a payload but never approve it. Runtime smoke (barry-agent, 2026-08-03) confirmed all four PRD behaviors green including the load-bearing **buttons-survive-restart** check (persistent View re-attached, pre-restart card still executed). Suite 116→128; ruff unchanged; `noop_echo` is a demo handler only (nothing outbound shipped). **B2 unblocks all outbound** (Phase 8/9 publish, Drive output, email replies). **Next: B3** (tunnel/ingest API) — its open decision (auth = HMAC ± Cloudflare Access) needs an operator Cloudflare account/tunnel cred, a human action flagged in `PRD-b3-tunnel.md`. | 2026-08-03 |
| **Review remediation — nine items actioned** (`REVIEW-2026-08-08-architecture-eval.md`) | Security: **B2 gains an operator-identity allowlist** on every button handler, unauthorized clicks refused + logged, typed confirmation for outbound `item_type`s — **2FA on the operator's Discord account is now an architectural requirement**, since every other boundary funnels to that click (`PRD-b2-approval-gate.md` A1; the shipped cog needs the change). **Retrieval scoping becomes a mechanism**: one `agents/_lib/retrieval.py` wrapper, `Scope` enum defaulting to `UNTRUSTED`, scopes never union, CI grep forbids raw `cognee.search` — B1 stops depending on developer memory (`30-memory-layer.md`). **Exposure splits by audience**: Cloudflare Tunnel keeps machine callers that cannot join the tailnet (the WordPress webhook); **Tailscale Serve becomes the default for every human surface** including NocoDB, because Cloudflare terminates TLS and routing the prospect DB through a third party contradicts the local-first posture the rest of the design pays for — R4 drops High→Low (`PRD-b3-tunnel.md` A2). | 2026-08-08 |
| **HMAC hardening — replay downgraded, size cap promoted** | **Correcting the review's own SEC-1**, which rated replay High. A signed request cannot be mutated without the secret, so the only available replay is byte-identical — and byte-identical replays already no-op on both routes via `capture_messages.content_hash` (UNIQUE, pre-LLM) and `prospects_wp_idx` (UNIQUE). Replay is therefore **Low**, timestamp + rate limit is sufficient, and **a nonce cache is explicitly not recommended** (new stateful infrastructure to defend an already-idempotent endpoint) — with a falsifiable re-add condition: any future non-idempotent route needs one before it ships. What the review should have led with instead: **(1) body size cap** — the only uncovered item, since one oversized note is a giant cognify inside a single invocation and the breaker is post-hoc; **(2) `hmac.compare_digest`** — a naive `==` leaks the signature bytewise, a key-recovery path categorically worse than replay; **(3) per-caller secrets** — WordPress compromise is likely and today one secret authenticates everyone. Verification order is cheapest-first so an unauthenticated caller cannot make the box work. `/health` returns a static literal (no version, no DB state). | 2026-08-08 |
| **Content pipeline collapsed — Sam retired** | Triage + drafting + evaluation merge into **one Sonnet call** (agent `Keeley`, replacing Keeley Strategy, Keeley Content, Sam). Seven pipeline states become four; the max-2 re-draft loop is gone. Rationale: at ~5 drafts/week the three-call chain paid two extra round trips to re-establish context the first call already had, and Sam's pass/fail was re-derived by the operator seconds later at a gate they must open anyway — the evaluation duplicated the human's work one step earlier rather than reducing it. `self_check` is retained as **reviewer context on the approval card**, not as a gate. Phase 8 is unbuilt so this costs no migration (`sam_evaluation` → `self_check` in the Phase-8 migration). **Falsifiable re-add: >30% rejection rate over the first 20 drafts reinstates a separate evaluator.** Pipeline failure mode moves from *stalling* (rows piling at `drafted`) to *declining* (visible in the briefing). | 2026-08-08 |
| **Cognee keep/kill gate set — 2026-11-01** | The pivot is deployed and the capability argument holds, but the standing cost does not decay (LLM spend per note, a pinned litellm callback contract, nondeterministic recall, two DBs, a config surface re-verified each upgrade). Applying the architecture's own "every element traces to a workflow" rule with a number: **metric = organic recall invocations/week, trailing 4 weeks, excluding smoke/CI** (SQL in `26-`). **≥10/week → keep. <10/week → fall back to Option C** (`entities` + join table, ~3–5 days, already scoped). A middle branch keeps cognee for capture/recall but stops graph-grounding the drafting path if an A/B over ~10 drafts shows no difference. Plus a qualitative test: five real questions, GraphRAG vs `pg_trgm`+`ILIKE`. **Two things must be preserved or the fallback quietly expires**: the ontology's cognee-or-pydantic fallback base must keep passing without cognee installed, and `retrieval.py` must remain the only call site — with the wrapper, Option C is a reimplementation behind one interface rather than a rewrite across every agent. | 2026-08-08 |
| **External witness for the box (dead-man's switch)** | Every monitor lived on the machine it monitored: Ted watches the agents, launchd watches Ted's process — but `KeepAlive` cannot detect a running-but-wedged process and reports nothing if the machine is off. Fix: **each critical loop pings an external check on success** (healthchecks.io free tier), six checks with per-loop periods; **absence of a ping is the alert**, which is what survives a dead box, a wedged scheduler, or a network failure. Push-based deliberately — a poller hitting `/health` would see a healthy gateway while nothing is being scheduled. Rules: ping only on the success path (never `finally`), `/fail` on caught exceptions, **alert to email/push, not Discord** (the failing system is where the alert would not appear), one check per loop so the alert names the failure. Second layer: Ted writes `dashboard.last_ted_run_at`, the briefing renders it and reddens past two missed cycles — so a silently-broken switch is still caught by human attention. No second local watchdog: the box being off is not observable from the box. | 2026-08-08 |
| **Migration workstreams renamed `W1–W7` → `MW1–MW7`** | The prefix collided with business workflows in `90-workflows.md` (W1–W8), so "W5" meant both "recall rewrite" and "daily briefing" depending on the file — ambiguous in any cross-file conversation or agent prompt. **`M1–M7` was considered and rejected**: `M1`/`M2` are already `26-`'s mitigation IDs, which would have moved the collision inside a single document. The `W` prefix is now reserved permanently for business workflows. Dated decision-log entries above are left verbatim as historical records; read `W<n>` in a July entry as `MW<n>`. | 2026-08-08 |
| **40-action-layer refreshed to as-built** | The file still described the pre-pivot system and 20- points to it as the authoritative agent reference, so a fresh reader or agent session would have rebuilt the deprecated design. Corrected: per-agent Anthropic keys + `KEY_BY_AGENT` → **one key**; cost helper "enforces" caps/ceilings → **labels and records**, with G1/G2 gone and a soft post-hoc breaker plus **bounded queries** as the replacement for the per-run token cap; the dropped `facts` table → graph/retrieval-wrapper reads; Gemini embeddings → **local FastEmbed bge@768, no key, no ledger row**; per-agent venvs and the `agent` account → one uv env with optional groups, `barry-agent`. Also added: Tartt **batches 10–15 extracts per Gemini call** (~10× fewer calls, which matters while the free-tier *request* cap binds before the token cap), and the Track O loops + Trent Crimm specs. | 2026-08-08 |
| **Outreach CRM adopted as Track O** (`35-outreach-crm.md` v0.3.0, map `37-`, inbound open in `36-`) | The FractionalOS Outreach Engine (trigger-driven 5-touch cold outreach, 25-point rubric, 12–15 capacity cap) implemented as a SQL-first operational subsystem in `aiadaptive_cos` (migration 0007: `outreach_targets/evidence/touches/packets/events`). Unit of work is a *company in a function state at a moment* — deliberately NOT overloading person-level `prospects`; the two scores (Roy Kent ICP fit 0..1, outreach moment score 0..25) are never merged. With no bot mediating writes, **every invariant is a DB CHECK or trigger**, incl. audit history (`outreach_events` via trigger — survives NocoDB raw UPDATEs). | 2026-08-08 |
| **No generated prose in the outbound outreach path** | Packet assembly is a deterministic query: typed dated evidence + precomputed arithmetic + a bounded graph traversal from the target's node (never `GRAPH_COMPLETION`). The operator writes the observation sentence (Tier 3) and sends personally, so outreach never crosses B2. This eliminates prompt injection into outbound mail and cross-client leakage *by construction* (risk R2 retired) and drops outreach LLM spend to Trent Crimm's classification only ($0.30/day). The dominant risk becomes **staleness** (R19): evidence carries first/last-seen dates, freshness tiers gate display, and stale facts are excluded from the arithmetic and block `ready`. | 2026-08-08 |
| **NocoDB as the outreach work surface** | Grid UI over the operational SQL for import, packet reading, and manual edits. Constraints: dedicated Postgres role (no UPDATE on derived views), **shared views disabled** (unauthenticated-by-default + CVE-2026-47379 plaintext-comparison timing leak, fixed 2026.5.1 — that is the version floor), Cloudflare Access at the **hostname** so share routes can never bypass auth. Tailscale Serve is the fallback posture. | 2026-08-08 |
| **BCC-to-brain is a pull channel** | Send-capture = a dedicated plus-addressing mailbox + IMAP poller matching a per-touch token from `Delivered-To` — the Granola-poller shape, so it needs **neither B3 nor the Track C email channel** (~1 day, not ~5–7). Token-exact matching is mandatory: heuristic matching silently corrupts touch-of-first-reply, the method's key metric. LinkedIn sends are a *permanent* fallback (Shortcut/NocoDB), not transitional. | 2026-08-08 |
| **E1 experiment: re-engagement allowance of 3 above the 15-cold cap** | Watchlist re-engagements (departure trigger = highest-converting message in the method) must not be blocked by cold targets mid-arc. Falsified if re-engagement conversion is not materially above cold — or, more tellingly, if the allowance is never hit in 2 quarters, which would mean detection is the binding constraint. **Departure detection itself stays OPEN (OQ1)**: no LinkedIn scraper will be built (account risk); Sales Navigator alert-forwarding or PredictLeads News Events are the candidates; careers-page proxy + quarterly manual sweep ship regardless. | 2026-08-08 |
| **B3 tunnel — VERIFIED in production** | barry-agent runtime-tested the Cloudflare Tunnel end-to-end (2026-08-10): an external caller reached the authenticated `/ingest` through the tunnel, HMAC verified, `ingest_note` ran, and **Postgres never left the local socket**. B3 (`PRD-b3-tunnel.md`) is now live, not drafted. Consequence for the roadmap: the **remote transport** for the interactive boundary is unblocked — a serverless/off-box shell can reach the brain via the Gateway REST surface using the already-provisioned `tools` HMAC caller. Machine-caller exposure rides Cloudflare; human surfaces default to Tailscale Serve (the A2 split holds). | 2026-08-10 |
| **Interactive boundary adopted — ADR-0001 (federate, don't migrate; boundary, not translation)** | Evaluated **Hermes** (NousResearch) against the governed fleet. Decision (`ADR-0001-hermes-federation-and-brain-boundary.md`): **(D1)** do not wholesale-migrate — Hermes is a generalist autonomous *runtime*; AFC is a governed *application*, and migration would dissolve B1/B2/B4, the `agent_runs` ledger, and the two-plane memory (and re-open the OpenClaw-lineage decision retired 2026-05). **(D2)** add the missing capability — an interactive, multi-step agent *over the brain* — as a **new front-end reaching the brain through a gated tool layer** (`PRD-mcp-tool-layer.md`); *the tool layer is the invariant, the shell is the variable*, so build-vs-adopt-Hermes stops being a one-way door. **(D3)** connect at the **API boundary, never storage** — a SQLite↔Postgres translation/sync is rejected (two systems of record, not two encodings; cognee isn't relational; FTS5≠graph/vector; a sync would breach B1/B4 bidirectionally). The one legitimate write of shell-learned knowledge into the brain is one-way `ingest_note`. **Sequencing:** `retrieval.py` (Phase 3.8) **next** — it is the B1 read-side enforcement the whole thing rests on and is overdue independent of this — then **Track I** (MCP tool layer) parallel to Phase 4. Validate first with Claude Code (trusted, on-box). | 2026-08-10 |
| **Phase 5 (Task Tinder) — COMPLETE + runtime-verified** | The `task_candidates`→`tasks` round-trip is live (`main`@`2397882`, 2026-08-11), closing the trial Phase 4 exists for. `_lib/task_tinder.py` (accept/decline/defer state machine + `decide`/`promote`) + `agents/discord_bot/cogs/task_tinder.py` (#task-tinder cards, 60s poller, persistent Views, operator-identity guard SEC-2). **CPX-4 decision (operator):** keep the two tables — an Accept creates a `follow_up` (chase-able commitment) AND a linked `tasks` row (`tasks.follow_up_id`). barry-agent verified live end-to-end: **Accept** → task #1 + follow_up #1 (both open, candidate→accepted); **Decline** → no task; **persistence** → re-attached View works across a bot restart. Briefing shows a `🗂️ Pending in Task Tinder: N` line (gated to ≥1) alongside the Phase-4 `📚 Reading` section. Discovery `task_candidates` (Tartt, conf=interest_score, ≥0.55) are the first producer; capture/meetings will be the next. | 2026-08-11 |
| **Phase 4 (Tartt discovery) — COMPLETE + runtime-verified** | Content discovery is live (`main`@`9990fe1`, 2026-08-11). Built as 5 tasks: source poller on per-source watermarks (0010 seed) → fetch (trafilatura) + Gemini-Flash summarize → typed `ContentItem` graph node (local bge, `uuid5(url)` dedup) + `content_items` operational tracker (0012) → interest scoring (max cosine of ContentItem-summary vs InterestSignal vectors, read from `aiadaptive_cognee`; P4-1 probe confirmed the layout) with interest-gated mode-1 cognify → briefing reading-recs + discovery `task_candidates`. barry-agent runtime-verified the producer half green: scoring discriminates (0.40 gate), ContentItems reach the graph, `/recall` works on the new `content` dataset (the one design unknown — **closed**, operator-confirmed in Discord), and **the trial guarantee holds — Gemini summarizes, ZERO Gemini embeddings** (local bge; deep pass Anthropic Haiku). A transient-Gemini-503 per-item resilience fix landed (`9990fe1`). Consumer surfaces (task_candidates population + briefing "📚 Reading") are built + unit-tested; their runtime confirm is folded into Phase 5 (which consumes task_candidates) and the next daily briefing. The Gemini free-tier **quality trial** now runs — revisit summary depth before scaling sources. Non-blocking: 3 disposable P4-1 probe nodes left in `aiadaptive_cognee`. | 2026-08-11 |
| **Roadmap reordered — Phase 5 restored, Track O pulled in after Phase 10** | **⤷ SUPERSEDED same day — see "Roadmap re-sequenced — Phase 6 moved before Track O" below; the Phase-10→Track-O placement was revised to Phase-6→Track-O→(re-evaluate Phase 10).** Active order is now `Phase 4 ✅ → Phase 5 (Task Tinder) → Phase 10 (Nate/ICP) → Track O (Outreach CRM) → Phase 8/9 → Phase 6 → Phase 7`. **(1) Phase 5** goes right after Phase 4: Phase 4's whole purpose (per `PRD-phase-4-discovery.md`) is the *first real trial of the `task_candidates` structure*, and Phase 5 (accept→`tasks`/`follow_ups`) closes that round-trip — the 2026-08-03 reprioritization named Phase 5 as the trial but dropped it from the listed sequence; this restores it. **(2) Track O** pulled in immediately after Phase 10 (operator, 2026-08-11). **Placement is sound** — Track O's spec says "after Phase 5", which Phase 10 satisfies, and it does *not* hard-depend on Phase 10 (its S1 score is its own; Trent Crimm runs "ahead of Nate"). **Prerequisites / flags to build inside or just-before Track O (none block the slot):** (a) **`Scope.TARGET` bounded traversal is unbuilt** — `retrieval.py` raises `NotImplementedError`; the outreach packet (§7) is a bounded N-hop traversal, so this must be implemented for Track O. (b) **H1–H7 ingest hardening is unbuilt** — Track O §11 declares these shared across every channel; the eval's SEC-6 wanted H2/H5 in `_lib/ingest.py` *before Phase 4* and that slipped, so they're now overdue and should retroactively cover Phase 4's untrusted content ingest too. (c) **outreach_targets seed from `prospects` (icp_fit_score≥0.7) needs Phase 6** — but Phase 6 is *after* Track O here; Track O's manual target import (D3) covers the gap, so it's a soft dependency, not a blocker. (d) **NocoDB + exposure infra** (dedicated role, shared-views-off, ≥2026.5.1, **Tailscale Serve** per PRD-b3 A2 / eval SEC-4 rather than Cloudflare Access) + the **BCC-to-brain** IMAP pull channel are new operator infra (~1–2 days). (e) **CPX-4 queue-table design** (task_candidates → tasks → follow_ups + outreach_touches overlap) is a decision that affects **both Phase 5 and Track O** — resolve the tasks/follow_ups split (keep vs collapse) before wiring the promotion, since Phase 5 is the first phase to exercise it. | 2026-08-11 |
| **Track I (MCP tool layer) — COMPLETE + runtime-verified** | The gated interactive boundary (`PRD-mcp-tool-layer.md`) is live (`main`@`53cb7e7`, 2026-08-11). Built builder-side as `retrieval.py` (3.8) → `brain_reader` RO role + `v_*` views (0008) → `brain_tools` gated core + `tool_invocations` audit (0009) → stdio MCP server (`agents/mcp/`, `mcp` dep group) → Gateway REST `/tools/{tool}` (reuses the `tools` HMAC caller). barry-agent runtime-drove **all 9 tools from a live Claude Code stdio client** across every gate class: `tools/list`=9; `v_*` reads over the `brain_reader` login (`spend_summary`/`list_*` — RO containment proven: view SELECT ok, base tables denied); `recall` (untrusted-scoped, GraphRAG); `ingest_note` (ingested→duplicate dedup); `enqueue_approval` (posts an inert `#approvals` card, nothing ships until a human ✅, B2). Audit logs every call on **both** transports (`mcp_stdio`+`gateway_rest`). Two gaps the runtime drive found were fixed: audit table ownership (0011 — was barry-admin-owned, app is barry_agent) and the stdio server not configuring cognee (53cb7e7). Local Postgres auth is `trust`, so `brain_reader` needs only LOGIN (no password); the RO DSN lives in barry-agent's keychain. **Consequence:** our own loop / Claude Code / a future Hermes can now drive the brain through one server-enforced boundary (B1 reads, B2 acts, one-way `ingest_note`, no B4 authoring). Track H (Hermes) remains optional. | 2026-08-11 |
| **Hermes planned as an OPTIONAL shell (Track H)** | Hermes is *not* adopted; it is **planned as an optional phase** (`PRD-hermes-optional-shell.md`, ADR-0001 D2) — an additional interactive front-end (multi-channel gateway: Slack/WhatsApp/Signal/Email/Telegram + voice; dynamic subagents) that drives the **same Track I boundary** as any other shell, so its gates are enforced server-side. Constraints to preserve governance: **self-authored skills disabled** (B4 stays git-only), reads are data (B1), actions go through `#approvals` (B2), Hermes keeps its own SQLite scratch (never synced to the brain — ADR-0001 D3). **Known limitation:** a third-party shell's *reasoning* tokens fall outside `agent_runs`; only tool-boundary spend is captured (route via litellm callback if Hermes becomes the standing shell). **Not on any critical path** — build only if the interactive capability, proven first with our own thin loop, earns the multi-channel/voice upside. | 2026-08-10 |
| **Spec-driven development adopted as the standing convention** | Operator directive (2026-08-12): every build increment starts from a written spec stating an **outcome** — what is true when it is done, phrased so someone else could check it — and code follows the spec; when reality contradicts it, the spec is corrected rather than quietly worked around. Requirements S1–S6 and the build-time rules are in §"Working convention" above; the operator asked to be **held to this**, so the repo-root `CLAUDE.md` (loaded into every session) instructs: confirm a spec exists before starting an increment, say so and offer to write one when it does not, record deviations rather than patching around them, and treat spec silence on a load-bearing point as an open decision rather than licence to pick. Written down because the lapses were the expensive part, not the practice: `35-` §2 declared three tables "unchanged from 0.2.0" when that DDL was never committed (reconstructed from prose across five sections during Track O increment 1), `35-` §15 still listed B3 as a build step months after it shipped, and `stage NOT NULL` met an inbound lead with no knowable funding stage only at build time. The good examples were already in the repo — `PRD-b3-tunnel.md`, `PRD-mcp-tool-layer.md`, and especially `36-inbound-leads.md`, which separates binding rules from undecided options and refuses to pretend the second are the first. Deliberately scoped to the **increment**, not the commit: below that the decision log is enough. | 2026-08-12 |
| **Track O increment 1b — four more ATS adapters (10 of 14 targets reachable)** | The first real target list exposed that Greenhouse/Lever/Ashby were the right adapters for the ICP the *spec* described — venture-backed startups — and the wrong ones for the population actually being pursued: **10 of 14 targets were unreachable**, so the posting-age clock was running for three companies. Every host in the list was probed before any code was written; four expose public JSON feeds with stable per-posting ids and are now supported — **Workable** (`apply.workable.com/api/v1/widget/accounts/<t>`, id = `shortcode`), **BambooHR** (`<t>.bamboohr.com/careers/list`), **TeamTailor** (`<t>.teamtailor.com/jobs.json`, JSON Feed 1.1), **Rippling** (`ats.rippling.com/api/v1/board/<t>/jobs`). BambooHR and TeamTailor identify the board by **subdomain** rather than path segment, so `detect_board` now reads both. **Hireology, BreatheHR and SaaSHR/UKG stay unsupported by decision, not omission**: probing found no public JSON (BreatheHR's `.json` route returns 401, the others serve HTML), and a scraped page carries no stable id — a drifting `dedup_key` silently resets posting age, which is the single thing this subsystem exists to protect. Two sub-decisions: parsers now take the board token, because BambooHR's payload omits job URLs and they must be reconstructed; and Workable/TeamTailor's own publish dates are captured as `payload.posted_at` for display but **never used as `first_seen_at`** — a provider date can reset on an edit or repost, and mixing the two would make "open 56 days" mean different things on different rows (whether the packet arithmetic should prefer it belongs to the packet spec, not a poller). Verified against live boards: **10 of 14 detected, 14 open roles reachable** (was 3 and 2). Suite 386→411. Remaining gaps are data, not code: ELM Learning's `careers_url` points at a Lever handle that does not exist (all three plausible variants 404), and four targets are on platforms with no public feed. | 2026-08-14 |
| **Track O increment 1 — schema, evidence poller, `Scope.TARGET`** | First of five planned increments (`main`@`f54a4e5`, 2026-08-12). **Migration 0013**: the six `outreach_*` tables with every `35-` §9 invariant as a DB constraint or trigger — no bot mediates these writes (NocoDB issues raw UPDATEs), so an application-level promise would be unenforced; `outreach_s1()` with the non-monotonic bands intact (day-60 hinge, R6-authoritative); `v_outreach_scored` / `v_outreach_evidence_display` / `v_outreach_capacity`; and `outreach_log_event()` audit triggers capturing `session_user` so a NocoDB edit is attributable (R17). **Evidence poller** (`agents/outreach/`, `loops/outreach-evidence.md`, ships disabled) — the step the spec says to start before everything else, since `first_seen_at` accrues only forward. It reads **ATS JSON APIs (Greenhouse/Lever/Ashby)** rather than scraping: generic HTML yields no stable per-role id, so `dedup_key` would drift on any layout change and silently reset the posting age that T10's mechanic and S4's top band both rest on. No LLM in the path (`40-action-layer.md` Outreach_loops). A failed fetch returns `ok=False` and the poller then writes **nothing** — treating "could not look" as "nothing there" would close every open req at once, the mirror of R19. **`Scope.TARGET` implemented** (`retrieval.py`): `get_graph_engine().get_neighborhood()`, a recursive CTE over the node/edge tables. `cognee.search`'s `neighborhood_depth` was the wrong tool — it requires vector-search seed ids and ends in an LLM completion, exactly the coupling H4 forbids. Hops bounded (default 2, max 3); H3 dataset filtering excludes playbooks and any future client dataset, and **fails open with a logged warning** when a node carries no dataset metadata (dropping unlabeled nodes would empty every packet). **Roy Kent gains the `outreach_targets` hand-off** deferred at Phase 6, on the same ≥0.7 gate; a free-mail-only lead gets no target row rather than a fabricated domain that would collide with the next gmail lead. **Two documented deviations from `35-` §2:** (1) `stage` is nullable **for `inbound_enquiry` only** — a scorecard cannot report a funding stage and a fabricated one would score silently through S2; the sequence CHECK makes it mandatory again before a target can enter an arc. (2) the ready-guard orders packets by `(assembled_at DESC, id DESC)` — `now()` is *transaction* time, so a same-transaction regeneration produces identical timestamps and the guard would otherwise pick arbitrarily and block sends whose current packet is ready (found by exercising it against real Postgres). Suite 271→371; every constraint exercised with a passing and a violating case, and the D1 rules / `first_seen_at` immutability / close-detection / reopen / audit capture driven end-to-end against the live DB. **Deferred to later increments** (each gated on operator infra or a `35-` §16 open decision): packet assembly + intake cards + `outreach-daily` (needs the real Selector template copy), NocoDB (install + role), BCC/IMAP send capture (mailbox decision #2), enrichment + Trent Crimm (accounts #3 + the R21 retention check). | 2026-08-12 |
| **Roadmap re-sequenced — Phase 6 moved before Track O** | Operator directive: do Phase 6 (lead-gen/Roy Kent) before Track O, with Track O immediately after, and Phase 10 re-evaluated once Track O lands. Active order: `Phase 5 ✅ → Phase 6 → Track O → re-evaluate Phase 10 → Phase 8/9 → Phase 7`. | 2026-08-11 |
| **Phase 6 (Roy Kent) — COMPLETE + runtime-verified** | Inbound WordPress lead qualification is live (`main`@`0d28577`, `agents/roy_kent/qualify.py`): dedups on `prospects.wordpress_profile_id`, writes the `prospects` row *before* qualification (a Haiku failure never loses the lead — matches the spec's error-handling contract), scores ICP fit via Claude Haiku (forced-tool structured output against `decisions` domain='icp', falling back to a hardcoded rubric since none is recorded yet), H2/H5-hardens and embeds scorecard pain-text into one `icp_signals` row per statement, and raises a `task_candidates` row at `icp_fit_score >= 0.7` (the existing Task Tinder poller picks it up with no new wiring — confirmed live). Gateway `POST /webhook/leads` replaces its 501 stub with the real ack-then-process handler (HMAC + the `wordpress` caller were already live from B3). Briefing gains a "🤝 New prospects" section. No migration needed — `prospects`/`icp_signals`/`task_candidates` already existed (0001). Suite 249→271, ruff clean (same 6 pre-existing errors only). **Scope decision (operator, 2026-08-11): `outreach_targets` seeding is deferred entirely to Track O** — WordPress inbound leads are already-qualified prospects, while Track O's outbound list is unqualified people to reach into; they don't belong in the same table's write path, and Phase 6 writes only the Roy_Kent spec's stated outputs (`prospects`/`icp_signals`/`task_candidates`). This also resolves the outreach_targets `stage`-NOT-NULL / missing-audit-trigger blocker that a premature partial build would have hit. **barry-agent runtime smoke (2026-08-11, PID 54745) — all green:** good-fit synthetic lead → `qualified`, fit **0.850**, segment "Small B2B Consulting Shop", 1 `icp_signals` row (the short "yes" answer correctly filtered), 1 `task_candidates` row (`inbound_lead`, conf 0.850), 2 `agent_runs` rows (anthropic $0.0017 + gemini ~$0, both success); bad-fit synthetic lead (85k-employee enterprise) → fit **0.000**, no candidate — confirms the gate isn't a 0/1-everything failure mode; idempotent resend of the same payload → no duplicate `prospects`/`task_candidates` rows; the `inbound_lead` candidate surfaced correctly as a `#task-tinder` card (Roy-Kent→Task-Tinder handoff proven); briefing "🤝 New prospects" section renders correctly alongside the existing reading-recs and Task-Tinder-pending lines (verified via compose-reproduce, not posted live). Full detail + smoke-data cleanup command: `/Users/Shared/afc-richmond/PHASE-6-ROY-KENT.md`. **Carried-forward, non-blocking:** real WordPress webhook still unwired (operator/WordPress-side infra — synthetic smoke is the only inbound path today); `raw_profile.answers` pain-text contract unverified against a real payload; no `decisions` domain='icp' seed row (fallback rubric only); no inbound sequencing/nurture built (`36-inbound-leads.md` §4 still open); no re-qualification job for a Haiku-failure-left-unscored lead. | 2026-08-11 |
| **Track O — `outreach-rescore` spec written before the build; two of its three stated jobs do not exist** | `35-` §14 and `40-action-layer.md` describe this loop in eight words — *"recompute S1, band-change events, stale-signal cards"* — which is the entire specification in the repo and satisfies neither S1 nor S3. Written up first (`loops/outreach-rescore.md`, ships `enabled: false`, command module not yet built) per the working convention. Holding the eight words against the build found that **two of the three describe work that does not exist**: (1) **"recompute S1" is a no-op** — S1 is a `STABLE` function evaluated live inside `v_outreach_scored`, never materialised, so there is nothing stored to recompute; the phrase survives from a design where S1 was a column, which §4 itself explains Postgres forbids (generated columns must be IMMUTABLE, S1 depends on `CURRENT_DATE`). (2) **Half of "band-change events" is already automatic** — a `candidate` rising into `work` is carded by the intake cog's 120s poll (`list_undelivered`) with nothing weekly involved; what is missing is the *record* of a transition and every direction that does not end in a new card. (3) **"Stale-signal cards" is the one genuinely unbuilt piece** — `signals_stale` is already computed in the view and **nothing anywhere reads it**, so §4's "30-day cadence → Task Tinder card" has a detector and no consumer. The spec carries forward `outreach-daily`'s **no-automatic-banding** reasoning unchanged and states it as a non-goal, because this is precisely the loop that would be tempted to band and Gate 4 still does not exist. **Four open decisions surfaced rather than picked** (S4): where the previous band comes from (recompute-as-of via `outreach_s1(trigger_date, now-7)`, stateless but misattributing a mid-week S2–S5 edit, vs storing the last swept band, which costs a migration that must drop and recreate the `SELECT t.*` view); what surface a stale-signal card uses, given Task Tinder's Accept/Reject/Defer **cannot express a four-value S2–S5 judgement**; whether a Gate 1 card outlives the band that raised it (`decide()` gates only on `status='candidate'` and never re-checks `treatment`); and whether an eight-target sweep earns eight cards. | 2026-08-17 |
| **Track O — the 14-target cohort accepted as calibration data, not scored prospects** | All 14 targets carry the **identical `trigger_date` of 2026-06-10 across seven different `trigger_kind` values** (funding announced, executive departure, product launch, restructuring, second raise, market expansion, request open 45+ days), with `trigger_source_url` NULL on every row. Fourteen companies did not have seven different kinds of trigger event on one day: the date came in verbatim from the operator's `Outreach_test_data_2.csv` at the 2026-08-13 import and was never an observed event. **Operator decision (2026-08-17): accept as-is and do not re-base** — this cohort exercises the machinery, and the next real import is the first genuinely scored cohort. The consequence is recorded because it looks exactly like a failure: one shared `trigger_date` means the whole set moves in **lockstep**, and it sat at day 68 — the last day of the 58–68 hinge, where `outreach_s1()` returns 5 — on 2026-08-17. On **2026-08-18** all fourteen fall to S1=3, losing two points at once, and **eight cross a band boundary**: AIIR Consulting #18 goes `work`→`watch` (21→19) and seven go `watch`→`drop` (15→13), leaving **no target in `work` at all**. The intake cog then polls every 120s and finds nothing, permanently — S1 only falls from here (day 90 on 2026-09-08 takes another two points). This also corrects the 2026-08-14 handback's prediction that LifeLabs #17 and Sales Gravy #6, both at 19, would "tip to `work` on their own as S1 ages": they were *inside* the hinge and are leaving it, so they tip **down**, to 17. Nothing in this cohort rises again. Separately noted: AIIR's live Gate 1 card stays clickable at 19 because `decide()` never re-checks `treatment` — the decision remains available, only its stated justification expires. | 2026-08-17 |

| **Track O — `trigger_date` re-anchored on pipeline acceptance, not market event (0023)** | Operator decision (2026-08-27). The five-touch arc anchors on `trigger_date` (`packet.touch_windows` = `trigger_date + N days`), and the imported cohort's fake batch date **2026-06-10** put every arc's schedule in the **past**: AIIR, admitted to `in_sequence` on 2026-08-27, had touches 1–4 already expired (June/July windows). So `trigger_date` is redefined from "when the market trigger happened" to "when the operator accepted the firm into the working pipeline" — a real, dated decision. Promotion (`_lib/outreach_discovery.promote`) now takes the discovery's `reviewed_at` date and defaults `trigger_kind` to a new CHECK value `operator_selected`; a real market trigger overrides when Part 2 classifies one. **This does not reopen R0.3's fabricated-date failure** — R0.3 guarded against a stamp on *unobserved* data (an import artefact), whereas an acceptance date is genuinely observed. One-time correction on the shared DB: the 14 fake-dated targets moved to the current date, and AIIR's five touches re-materialised from the new anchor (all active, none expired). Migration 0023 (schema: the new trigger_kind). Note this does NOT by itself produce new Gate 1 cards for the cohort — their human-judged S2–S5 sum to ≤14 for the non-AIIR firms, so even fresh (S1=5) they cap at 19, below the 20 'work' threshold; new cards need stronger S4/S5 judgement or newly-sourced firms. | 2026-08-27 |
| **Track O — `outreach-rescore` built (deterministic core)** | The weekly re-score sweep (`35-` §14) is built, `agents/outreach/rescore.py`, ships disabled. Two jobs, both pure SQL, and it **never changes a target** (outcome 3): it records band crossings and surfaces stale judgements. O1 recompute-as-of is implemented as the honest thing — a target's band now vs a week ago (`outreach_s1` at two as-of dates + stored S2–S5), one `outreach_events` row per crossing (`op='RESCORE'`) naming both bands, both scores, and **both as-of dates** so the mid-week-S2–S5-edit misattribution is visible rather than hidden. O4: the sweep cards **no** band change — an upward crossing into `work` is already carded by the intake poll, a downward one is record-only. Stale-signal raising (O2) is interim: a `task_candidates` re-check pointing at `cli/outreach_score`, idempotent via a `NOT EXISTS` pending-candidate guard (raised at most once, outcome 2); the **bespoke S2–S5 modal** O2 settled on is the next slice. Verified: the hinge edges against live `outreach_s1` (day 58 → S1=5, day 69 → S1=3 — the transition the widened 58–68 window exists to stop a weekly sweep stepping over), crossing detection, the never-touch-a-target guard, and stale idempotency. Suite 787 → 796. | 2026-08-27 |
| **Track O Part 3 — the firmographic spine as typed columns (0024)** | PRD §3.1 outcome 1. Nine columns added to `outreach_targets` (`sector` already existed from 0013): `headcount`, `headcount_asof`, `ownership_type`, `total_raised_usd`, `last_round_at`, `last_round_type`, `lead_investor`, `founded_year`, `hq_location`. **Typed columns, not a JSONB blob (§3.2 non-goal), because the audit trigger `outreach_log_event()` diffs *columns*** — confirmed against the live function, it walks `jsonb_each(NEW)` and records `{col:{from,to}}` per changed key, so attribute history (outcome 2) is readable with no new table; a blob would log whole-object before/after and be unreadable. **§3.5 open #4 resolved YES:** `ownership_type` carries a CHECK over the five values (`vc_backed`/`pe_backed`/`bootstrapped`/`founder_owned`/`public`), on the 0013 precedent that every invariant is a DB constraint because NocoDB issues raw UPDATEs that bypass app validation; `founded_year` gets a light `1800..2100` range CHECK for the same reason (upper bound deliberately CURRENT_DATE-free so the constraint stays immutable/idempotent across years). Mandatory `v_outreach_scored` DROP+CREATE (the `SELECT t.*` frozen-column-list trap; `verify_schema` view_drift = OK). No provider data stored yet — the Apollo/Crunchbase adapters and the V2 coverage probe come after this; funding will also land as an `outreach_evidence` row (outcome 3) at adapter time, the column being current-state convenience. Verified on the shared DB: audit round-trip (`headcount` change logged `{"to":42,"from":null}`), both CHECKs reject bad values and accept valid ones, idempotent re-apply. Suite unchanged at 796 (pure SQL). | 2026-08-28 |
| **Track O Part 3 — the Apollo V2 coverage probe (read-only)** | PRD §3.3 requires V2 — run real targets through Apollo and count non-null spine coverage — **before** any storing adapter. Built the probe engine (`agents/outreach/apollo.py`) and a read-only CLI (`cli/outreach_enrich.py --probe`); there is deliberately no `--apply` (the storing adapter is gated on this probe's result). Apollo contract read from the docs (2026-08-28): `GET api.apollo.io/api/v1/organizations/enrich?domain=`, auth header `x-api-key`, response fields mapped onto the nine spine fields (`industry`→sector, `estimated_num_employees`→headcount, `total_funding`→total_raised_usd, `latest_funding_round_date`/`latest_funding_stage`, `funding_events[].investors[0]`→lead_investor, `founded_year`, `raw_address`/city-state-country→hq_location). Two honest non-mappings recorded as findings, not gaps: `headcount_asof` is **always** null (Apollo returns no as-of date — the observation date is ours, stamped at storage time), and `ownership_type` is only `public` (via `publicly_traded_symbol`); vc/pe is **not** guessed from a funding stage. The probe also dumps the union of raw Apollo keys so the live run itself teaches the true schema for the adapter. 11 unit tests fixture Apollo's real shape and drive `enrich_organization` through an injected fetch (no network, no key). **Cannot run V2 on the build box** — needs the operator's Apollo key in the keychain as `apollo-api-key` (registered in `scripts/keychain_setup.sh`); barry-agent runs it. Build box confirmed the error path (missing key → graceful exit 2) and argparse (no `--apply`). Suite 796 → 807. | 2026-08-28 |
</decision_log>

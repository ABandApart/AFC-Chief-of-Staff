# Build Order

<doc:layer>implementation — phasing</doc:layer>
<doc:stability>medium — edit when scope or priorities shift</doc:stability>
<doc:depends_on>all prior files</doc:depends_on>
<doc:referenced_by>none</doc:referenced_by>

## Purpose

This file defines the phased implementation plan. Each phase produces a working, useful slice of the system. No phase requires later phases to be valuable on its own.

The phasing is borrowed in spirit from Logan Currie's COS getting-started document, adapted to this stack and scope.

---

## Phase Overview

<phase_overview>

| Phase | Theme | Duration | Outcome |
|-------|-------|----------|---------|
| 1 | Foundation | Week 1 | Brain reachable, repo structure, credentials in place |
| 2 | Telemetry primitives | Week 2 | agent_runs, cost helper, G1 + G2 guards live; outcomes table scaffolded |
| 3 | Capture and recall | Weeks 3–4 | Discord bot live with #capture and #briefing |
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

Total to a fully-functional v1 (Phases 1–12): ~15 weeks of evenings/weekends.

**Why Phase 2 (telemetry primitives) comes second**: every LLM call from Phase 3 onward must go through the cost helper. Building telemetry first means it's never retrofitted; every agent is born observable and rate-limited. This is a small phase (≤1 week) but blocking on everything that follows.

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

| Decision | Rationale | Recorded |
|----------|-----------|----------|
| Hosted Supabase over self-hosted | Hosted security is sufficient; brain reachable from any service | 2026-05-14 |
| **Reversed**: local Postgres 17 on Mac mini for Phase 1–5 | Phase 1–5 do not require external reachability. Local wins on latency (<1ms vs ~50ms), privacy (data never leaves the box), cost (free vs $25/mo Pro tier), and offline-resilience. Phase 6 (Roy Kent WordPress webhook) is the first phase that needs external reach; the decision will be revisited then with three options on the table: Tailscale/Cloudflare tunnel + local, migrate to hosted Supabase via `pg_dump`/`pg_restore`, or pick a different hosted provider. Postgres-to-Postgres migration is mechanically simple, so the option remains cheap. | 2026-05-19 |
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

</decision_log>

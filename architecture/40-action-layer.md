# Action Layer

<doc:layer>implementation</doc:layer>
<doc:stability>medium — edit when agent set or scheduling changes</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md</doc:depends_on>
<doc:referenced_by>35-outreach-crm.md, 50-channel-layer.md, 60-content-pipeline.md, 70-build-order.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file defines how the action layer runs on the Mac mini: agent specifications, scheduling (launchd), supervision, credential handling, and the LLM-provider routing rules. All LLM calls in this layer pass through the cost-emission helper documented in `80-telemetry-layer.md`.

---

## Critical Discipline: The Cost Helper

<cost_helper_reference>

> **Refreshed 2026-08-08.** This file predated the 3.7 pivot and described the
> pre-pivot enforcement model. Corrected throughout; see the as-built note below.

Every LLM call from every agent passes through the cost-emission helper
(`agents/_lib/runs.py`), and **agents do not import provider SDKs directly** —
that rule is unchanged and load-bearing. What changed in the pivot is *what the
helper does*:

- **It labels and records.** One `agent_runs` row per provider call. cognee's own
  calls are captured by the litellm callback (M1) rather than by `agent_run()`.
- **It does not pre-flight refuse.** The per-run token cap (G1) and the hard
  per-day ceiling (G2) were **removed**. The replacement is a **soft post-hoc
  breaker** (`assert_under_ceiling`): accumulated spend is checked after each
  write, and the *next* invocation is blocked once over ceiling. Backstopped by
  monthly `cli/reconcile`.
- **Bounded queries replace the token cap.** With no pre-flight refusal, the
  protection against an oversized prompt is that every prompt-feeding query
  carries an explicit `LIMIT` and per-field truncation *at the query layer*.
  Specs below that say "all rows" are to be read as bounded — this is a standing
  requirement, not a per-agent one.
- **Retrieval has the same rule.** Agents do not call `cognee.search` directly;
  all retrieval goes through `agents/_lib/retrieval.py` with an explicit scope
  (`30-memory-layer.md`).

**Caps and ceilings in the specs below are budgets, not enforcement.** They are
the numbers Ted alerts against and Higgins reports; nothing refuses a call at
runtime on their basis.

Full specification: `80-telemetry-layer.md`.

</cost_helper_reference>

---

## Execution Environment

<environment>

- **Host**: Mac mini (`mini`), running macOS, dedicated agent account (**`barry-agent`**; code authored in `barry-admin` and pulled — the B4 git-gate).
- **Language stack**: Python 3.12 for agents and Discord bot. Node.js only if a specific library is Node-only.
- **Package management**: `uv` for Python (lockfile-based). **One project environment**, not a venv per agent — with optional dependency groups (`cognee`, `gateway`) so the heavy trees stay out of dev/CI. Modules that touch cognee import lazily.
- **Repo location**: the runtime clone, pulled from git, never edited directly.
- **Credentials**: macOS Keychain via `security` CLI, cached per process (`agents/_lib/creds.py`). Never in env files committed to git.

</environment>

<credential_inventory>

| Credential | Keychain item name | Used by |
|------------|---------------------|---------|
| Postgres connection string | `db-url` | All agents and the bot (via the `agents/_lib/db.py` pool); backup script (`pg_dump`, both DBs) |
| Gemini API key | `gemini-api-key` | **Tartt summarization only.** Gemini's lane is news; it is no longer used for embeddings. |
| Anthropic API key | `anthropic-api-key` | **One key.** The per-agent `anthropic-key-<agent>` scheme and the `KEY_BY_AGENT` dispatcher were **removed in the pivot** — provider-side attribution is now the `agent_runs` ledger's job, and a single key was the precondition for routing cognee through litellm (M1). A coarse second key per *subsystem* (cognee vs own-agents) is the only split retained. |
| Buffer access token | `buffer-access-token` | Keeley Distribution |
| Discord bot token | `discord-bot-token` | Discord bot |
| Gateway HMAC secrets | `gateway-hmac-<caller>` (wordpress, shortcut, tools) | Gateway API request signing, per caller (`PRD-b3-tunnel.md` A1) |
| Outreach BCC mailbox | `outreach-imap-url` | Track O send-capture poller (`35-outreach-crm.md` §8) |
| Embeddings | **none — no key** | Local FastEmbed `bge-base-en-v1.5` @768 in-process via ONNX. No API key, no rate limit, and client text never leaves the box (2026-08-03). |

Credential reads are cached per process (`agents/_lib/creds.py`) — keychain lookups spawn a
`security` subprocess, so nothing should call `security` directly in a hot path.

</credential_inventory>

---

## Agent Specifications

<agent_specs>

Each agent below has: trigger, inputs (DB reads), outputs (DB writes), LLM choice and rationale, token caps and ceiling, error handling.

<agent_spec id="Roy_Kent">

**Trigger**: Webhook from WordPress Lead Engine on new prospect profile (event-driven).

**Job**: Qualify inbound prospects against ICP criteria. Score fit, emit icp_signals from scorecard pain-point answers, create task_candidates for high-fit prospects.

**Inputs**:
- Webhook payload (the prospect profile JSON)
- `decisions` filtered by `domain IN ('icp')` for current ICP criteria
- `prospects` (for dedup against existing wordpress_profile_id)

**Outputs**:
- `prospects` (new row with icp_fit_score and fit_reasoning)
- `icp_signals` (one row per pain-point statement in scorecard free-text)
- `task_candidates` (only if `icp_fit_score >= 0.7`)

**LLM choice**: Claude Haiku. Rationale: rubric-based qualification against stored criteria; not deep synthesis.

**Token caps**: 3,000 input / 600 output per qualification call.
**Daily ceiling**: $1.00/day.

**Error handling**:
- Webhook validation failure: respond 400; emit #system alert with payload hash.
- LLM failure: write `prospects` row with status='new' and `icp_fit_score=NULL`; flag for re-qualification at next Tartt window.

**Workflow served**: W1.

</agent_spec>

<agent_spec id="Tartt">

**Trigger**: `launchd` daily at 5:00 AM local time.

**Job**: Discover content from configured sources, score against interest signals, store with embeddings.

**Inputs**:
- `sources` table (filtered by `active = true`, ordered by `last_polled_at`)
- `interest_signals` (all rows for scoring)

**Outputs**:
- `content_items` (new rows, with embeddings and computed interest_score)
- `icp_signals` (one row per pain-point statement extracted from summaries flagged for ICP-relevant interest signals)
- `sources.last_polled_at` updated for polled sources
- `dashboard.last_tartt_run_at`, `dashboard.last_tartt_item_count`

**Pipeline**:
1. For each active source: fetch new items (feedparser for RSS/newsletters, HN Algolia API, ArXiv API, YouTube Data API + youtube-transcript-api)
2. Extract clean text via trafilatura (HTML) or supplied transcript (YouTube)
3. Summarize via Gemini 2.5 Flash — **batched 10–15 extracts per call** with structured output (one call per article was the original spec; batching cuts call count ~10×, which matters most while Gemini is on the free tier, whose *request* caps bite before token caps)
4. Embed summary via **local FastEmbed `bge-base-en-v1.5` @768** (in-process ONNX; vectors are already normalized, so the old L2-normalization step in the cost helper is retired along with M2). Ingest is the Granola-style hybrid: mode-1 text **plus** a typed `ContentItem` DataPoint — see `PRD-phase-4-discovery.md`
5. Score: cosine similarity against each `interest_signal.embedding`, weighted by signal.weight, summed; multiplied by `source.trust_score`
6. Insert into `content_items`
7. For each ICP pain-point mentioned, embed it and insert into `icp_signals` (wide-net pattern, W2 substrate)
8. Emit event for Keeley on items scoring above threshold (initial threshold: top 20% of run)

**LLM choice**:
- Gemini 2.5 Flash for summarization. Rationale: high volume, narrow scope, cost and rate-limit advantages over Claude Sonnet for this task type.
- **Local FastEmbed `bge-base-en-v1.5` @768** for embeddings — no provider, no key, no rate limit (2026-08-03). Replaces `gemini-embedding-001`; the 768-dim commitment is unchanged, so the graph did not need re-embedding.

**Token budget**: 4,000 input / 500 output per *batch* summarization. Embeddings are local and free — they make no ledger row.
**Daily budget**: $5.00/day (a Ted alert threshold, not a runtime refusal).

**Error handling**:
- Single-source failure: log to `#system`, continue with other sources.
- Gemini API failure or free-tier cap: retry with exponential backoff (3 attempts), then fall back to Claude Haiku for that batch. Per the 2026-08-03 free-tier quality trial, **throttle cadence rather than upgrade to paid** — the trial is judging quality, not throughput.
- Embedding failure: local and in-process, so this is a code or model-load failure rather than an API one — alert `#system` and retry on the next run.
- Soft ceiling exceeded: the *next* invocation is blocked and `#system` alerted; the current run completes.

**Status reporting**: posts a one-line summary to `#system` on completion (item count, source count, errors).

**Workflows served**: W3 (primary), W2 (contributes icp_signals).

</agent_spec>

<agent_spec id="Nate_Shelley">

**Trigger**: `launchd` weekly on Sunday at 8:00 PM local time.

**Job**: Cluster the past 7 days of icp_signals into themes. Surface top 5 clusters with frequency, source diversity, and representative quotes.

**Inputs**:
- `icp_signals` (last 7 days, all rows regardless of source)

**Outputs**:
- The weekly synthesis **cognified into the graph** as a `Fact` DataPoint with `domain='icp-intelligence'` (the flat `facts` table was dropped in migration 0006)
- `icp_signals.cluster_id` populated for clustered rows
- A summary posted to #briefing channel (Sunday night) and surfaced in Monday's Higgins dashboard

**Algorithm**:
1. Pull icp_signals from last 7 days
2. Compute pairwise cosine similarity on embeddings; cluster via simple agglomerative or HDBSCAN
3. For each cluster: count signals, count distinct sources, count distinct source_types, sample 3 representative quotes
4. Rank clusters by (signal count × source diversity)
5. LLM (Claude Sonnet) produces a synthesis of the top 5 clusters with theme labels and significance

**LLM choice**: Claude Sonnet. Rationale: clustering and synthesis from many signals is a reasoning-depth task. Runs weekly so cost is bounded.

**Token caps**: 20,000 input / 2,000 output per weekly synthesis.
**Weekly ceiling**: $0.50/week (~$0.07/day average).

**Error handling**:
- Insufficient signal volume (<10 in past 7 days): post a status note in #briefing instead; do not call LLM.
- Clustering failure: post raw signal list to #briefing; alert #system.

**Workflow served**: W2.

</agent_spec>

<agent_spec id="Keeley">

> **Merged 2026-08-08.** Replaces three specs — `Keeley_Strategy`,
> `Keeley_Content`, and `Sam_Obisanya` — with one call. Rationale in
> `60-content-pipeline.md`; decision recorded in `70-build-order.md`.
> **Sam is retired from the roster.**

**Trigger**: Event-driven — invoked after each Tartt run for items scoring above threshold.

**Job**: Triage, draft, and self-check a content item **in a single Sonnet call**. The model decides whether the item fits AI Adaptive's positioning; if it does, drafts the piece; and returns its own evaluation against the style/voice rubric — all in one context, so the draft is written by something that has already reasoned about fit rather than by a second call re-deriving it.

**Inputs**:
- `content_items` (the new item) and its graph `ContentItem` node
- `decisions` filtered by `domain IN ('icp','positioning','style','voice')` — the standing positions and the rubric
- `interest_signals` (top 10 by weight, bounded)
- Relevant background via `agents/_lib/retrieval.py` (`Scope.UNTRUSTED`)

**Outputs** — one write, one state transition:
- `content_pipeline.triage_notes`, `.draft_text`, `.self_check` (JSONB), stage → `drafted` or `declined` (with `declined_reason`)
- On `drafted`, an `approval_queue` row is enqueued immediately (B2)

**LLM choice**: Claude Sonnet, one call, structured output:
`{verdict: 'draft'|'decline', rationale: str, draft: {title, hook, body, cta} | null, self_check: {criterion: pass|fail, ...}, confidence: float}`

Rationale for merging: at ~5 drafts/week the previous three-call chain paid two extra round trips and two extra prompt-overheads to re-establish context the first call already had — and Sam's output (pass/fail against a rubric) was re-performed by the operator seconds later at the approval gate. One call is cheaper, lower-latency, and strictly better-informed. **`self_check` is retained not as a gate but as *reviewer context*** — it renders on the approval card so the operator sees what the model thinks is weak before reading the draft.

**Token budget**: 12,000 input / 2,500 output per item.
**Daily budget**: $1.50/day.

**Error handling**:
- LLM failure: row stays at `discovered`; Ted alerts after 24h with no transition.
- Malformed structured output: one retry with the schema restated, then leave at `discovered` and alert — never a partial draft into the queue.
- `verdict='draft'` with a null draft: treated as malformed.

**Re-add condition for a separate evaluator (falsifiable):** if the operator's rejection rate at `#approvals` exceeds **30% over the first 20 drafts**, reinstate a distinct evaluation step — that would be evidence pre-filtering has value this single call is not providing. Below that, the merged call stands.

**Workflow served**: W3.

</agent_spec>

<agent_spec id="Keeley_Distribution">

**Trigger**: Event-driven — invoked when an approval_queue row transitions to `approved`.

**Job**: Push the approved content to Buffer with rate-limit handling.

**Inputs**:
- `approval_queue` row (approved)
- `content_pipeline.draft_text` (or edited version if `approval_queue.edit_notes` populated)
- `buffer_posts` (for rate-limit state — recent post timestamps)

**Outputs**:
- `buffer_posts` (new row with `buffer_id` after successful API call)
- `content_pipeline` stage transitions to `scheduled`, then `published` on Buffer webhook callback

**LLM choice**: None. This is deterministic API work.

**Rate limiting**: Token-bucket implementation. Buffer's API allows 60 requests per minute per token. Build the limiter with a 50/min ceiling to leave headroom. On 429 response, sleep 60s and retry.

**Channel routing**: Single Buffer account with multiple connected channels (LinkedIn, X, etc.). Default channel determined by content_pipeline.draft_text format; override possible via approval_queue.edit_notes containing `channel: <name>`.

</agent_spec>

<agent_spec id="Briefing">

**Trigger**: `launchd` daily at 6:00 AM local time.

**Job**: Synthesize the state of the brain into a morning briefing posted to Discord.

**Inputs**:
- `prospects` where `status = 'new'` and `received_at >= last briefing` (W1 new prospects)
- `follow_ups` where `status = 'open' AND escalation_level >= 1`
- `content_items` from last 24h, ordered by `interest_score DESC`, top 5
- New graph knowledge from last 24h, **bounded** (`LIMIT`; no unbounded "all")
- `task_candidates` where `status = 'pending'`, top 5 by confidence
- `icp_signals` (top theme this week, sourced from Nate Shelley's most recent synthesis)
- `dashboard` (cadence flags, system health)

**Outputs**:
- Discord message to `#briefing`
- `dashboard.briefing_posted_at` updated

**LLM choice**: Claude Sonnet. Rationale: synthesis quality matters — this is your first input every morning.

**Token caps**: 32,000 input / 3,000 output per briefing.
**Daily ceiling**: $0.50/day.

**Format**: Sections — Priorities (overdue follow-ups), New prospects (W1), New today (top reading), Discovery follow-ups (W4), ICP signal of the week (W2), New knowledge captured, Task candidates (link to #task-tinder), **Outreach (due count, live/cap, packets not ready — Track O)**, System status. **Budget: main message ≤ 1,800 chars, three “do today” items on top, everything else in thread replies** — the Discord 2,000-char limit is otherwise hit on a normal day.

**Workflow served**: W5.

</agent_spec>

<agent_spec id="Ted">

**Trigger**: `launchd` every 6 hours.

**Job**: Health monitoring plus real-time cost guarding. Detect stale processes, missed schedules, error patterns, overdue escalations, ceiling proximity, and cost anomalies.

**Inputs**:
- `dashboard` (timestamps of last runs)
- `agent_runs` (for G3 anomaly detection — pure Python, no LLM)
- launchd logs (parsed for non-zero exits)
- `follow_ups` (escalation_level changes since last check)

**Outputs**:
- `#system` alerts when anything is amiss
- `dashboard` health flags updated
- Pinned status message in `#system` (updated in place)

**LLM choice**: Claude Haiku — but only when summarizing complex alerts. Most 6-hour cycles are pure Python with zero LLM cost.

**Token caps**: 4,000 input / 500 output per alert summarization.
**Daily ceiling**: $0.20/day.

**Alert thresholds**:
- Tartt missed its 5am run by >2h → alert
- Briefing not posted by 6:30am → alert
- Any follow-up advancing to escalation_level 3 → alert with draft message ready
- Any agent at >80% of daily ceiling → alert
- G3 anomaly: any agent's last-24h tokens-per-output >2× rolling 7d median → alert
- Any agent with >3 failed or token_cap_exceeded runs in last 6 hours → alert

**Workflow served**: W7 (telemetry; feeds Higgins).

</agent_spec>

<agent_spec id="Higgins">

**Trigger**: `launchd` weekly on Monday at 7:00 AM local time.

**Job**: Synthesize the past week into a performance dashboard digest posted to #dashboard. Headline metrics are the KRs; operational metrics are evidence.

**Inputs**:
- `agent_runs` (last 7 days, for spend by function and token discipline flags)
- `content_pipeline` (last 7 days, for throughput)
- `tasks`, `task_candidates` (last 7 days, for acceptance and completion)
- `prospects` (last 7 days, for W1 throughput)
- `icp_signals` (last 7 days, for W2 cluster summary)
- `outcomes` (last 7 days, for KR1 attribution)
- `follow_ups`, `dashboard` (system health, stale items)
- `sources` (coverage, staleness)

**Outputs**:
- Discord message to `#dashboard`

**LLM choice**: Claude Sonnet. Rationale: synthesizing many operational metrics into a prioritized, readable narrative.

**Token caps**: 16,000 input / 2,000 output per weekly digest.
**Weekly ceiling**: $0.30/week (~$0.04/day average).

**Format**: See `80-telemetry-layer.md` dashboard_format section.

**Workflow served**: W7.

</agent_spec>

<agent_spec id="Trent_Crimm">

**Trigger**: `outreach-watch` loop, weekly Sunday 7:00 PM local (one hour ahead of Nate Shelley, so its output can land in the same Sunday digest).

**Job**: Watchlist monitoring for the outreach engine (W8). Classify signals detected by the evidence poller against each watchlist target's `watch_trigger`; surface matches as Task Tinder re-engagement cards; archive targets whose `watch_until` has passed.

**Inputs**:
- `outreach_targets` where `status IN ('watchlist','lost_to_hire')` and `watch_until >= CURRENT_DATE`
- `outreach_watch_signals` (new detections since last run, written by the evidence poller)
- `sources` rows of kind `careers_page` / company RSS (shared trust machinery with Tartt)

**Outputs**:
- `outreach_watch_signals.classified_as` / `confidence` / `surfaced_at`
- Task Tinder cards for matches (`classified_as = watch_trigger` OR `'executive_departure'`); everything else stored, never shown
- `outreach_targets.status = 'archived'` on expiry

**LLM choice**: Claude Haiku. Rationale: classification of a short excerpt against a fixed trigger enum — narrow, rubric-shaped. Forced tool call: `{trigger_kind: enum|'none', confidence: float, rationale: str}`. **One call per detected item, not per target** — cost scales with feed volume, not watchlist size. This is the only LLM in the outreach system; packet assembly is a deterministic query.

**Token caps**: 2,000 input / 200 output per classification.
**Daily ceiling**: $0.30/day, `function_label='outreach_watch'`.

**Error handling**:
- Classification failure: signal stays unclassified with `surfaced_at NULL`; retried next run; `#system` alert if any signal is >2 runs old.
- Loop silent >8 days: Ted alerts.
- **Policy (R14, do not relax)**: no LinkedIn scraping under any error path. Departure detection remains open (`35-outreach-crm.md` OQ1); the careers-page proxy and quarterly manual sweep are the fallback.

**Workflow served**: W8.

</agent_spec>

<agent_spec id="Outreach_loops">

**Trigger**: Four scheduled loops (manifests in `loops/`, owned by the scheduler daemon): `outreach-daily` 5:45 AM, `outreach-evidence` every 12h, `outreach-bcc` every 15 min, `outreach-rescore` Sunday 6:00 PM.

**Job**: The no-LLM machinery of W8 — evidence polling (first/last-seen maintenance and close-detection into `outreach_evidence`), packet assembly (deterministic query: evidence + freshness tiers + precomputed arithmetic + bounded graph traversal), the briefing line and calendar refresh, the capacity drain rule, IMAP BCC token-matching for send capture, weekly S1 recomputation with band-change events and stale-signal cards.

**LLM choice**: **None.** No `agent_runs` rows. Deterministic by design — cannot fail from a provider outage. This is a deliberate property, not an economy (`70-build-order.md` decision log, 2026-08-08: no generated prose in the outbound outreach path).

**Error handling**: invariants are database constraints, not loop logic; Ted checks the loop-liveness and invariant set every 6 hours (`80-telemetry-layer.md`). The BCC matcher is idempotent — a second token match on an already-sent row is a no-op logged to `#system`.

**Detail**: `35-outreach-crm.md` §§3, 6–8, 14. This entry exists to acknowledge the loops as part of the action layer.

</agent_spec>

<agent_spec id="Discord_bot">

**Trigger**: Always-on launchd-supervised process; restarts on crash.

**Job**: Long-running event listener. Routes messages and reactions between Discord and the brain.

**Detail**: See `50-channel-layer.md`. This agent_spec entry exists only to acknowledge it as part of the action layer.

</agent_spec>

</agent_specs>

---

## launchd Configuration

<launchd_config>

Each scheduled agent is a separate launchd plist in `~/Library/LaunchAgents/`. Load order doesn't matter; agents are independent and event-driven inter-agent communication happens through the database.

<plist_template name="com.aiadaptive.tartt">

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aiadaptive.tartt</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/agent/agents/venv/bin/python</string>
        <string>/Users/agent/agents/tartt/run.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>5</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/agent/agents/logs/tartt.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/agent/agents/logs/tartt.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AGENT_NAME</key>
        <string>tartt</string>
    </dict>
</dict>
</plist>
```

</plist_template>

<plist_template name="com.aiadaptive.discord-bot">

The Discord bot is the only always-on agent; uses `KeepAlive` rather than `StartCalendarInterval`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aiadaptive.discord-bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/agent/agents/venv/bin/python</string>
        <string>/Users/agent/agents/discord-bot/run.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/Users/agent/agents/logs/discord-bot.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/agent/agents/logs/discord-bot.err</string>
</dict>
</plist>
```

</plist_template>

<scheduled_jobs_summary>

| Agent | Schedule | Plist label |
|-------|----------|-------------|
| Tartt | 5:00 AM daily | `com.aiadaptive.tartt` |
| Briefing | 6:00 AM daily | `com.aiadaptive.briefing` |
| Nate Shelley | 8:00 PM Sunday weekly | `com.aiadaptive.nate-shelley` |
| Higgins | 7:00 AM Monday weekly | `com.aiadaptive.higgins` |
| Ted | Every 6 hours | `com.aiadaptive.ted` |
| Backup | 3:00 AM Sunday weekly | `com.aiadaptive.brain-backup` |
| Buffer status poll | Every 30 minutes | `com.aiadaptive.buffer-status` |
| Discord bot | Always-on (KeepAlive) | `com.aiadaptive.discord-bot` |
| Outreach daily (packets, drain, briefing line) | 5:45 AM daily | loop manifest `outreach-daily` (scheduler daemon) |
| Outreach evidence poller | Every 12 hours | loop manifest `outreach-evidence` |
| Outreach BCC send-capture (IMAP) | Every 15 minutes | loop manifest `outreach-bcc` |
| Outreach re-score sweep | 6:00 PM Sunday weekly | loop manifest `outreach-rescore` |
| Trent Crimm (watchlist) | 7:00 PM Sunday weekly | loop manifest `outreach-watch` |

> Track O jobs are **loop manifests owned by the scheduler daemon** (the
> post-pivot convention from `25-target-state.md` §4), not per-job launchd
> plists. `outreach-daily` runs at 5:45 deliberately — before the 6:00 briefing,
> so the briefing carries the day's counts and link.

</scheduled_jobs_summary>

Event-driven agents (Roy Kent via webhook, Keeley, Keeley Distribution, Fact extraction, Meeting processor) are not in launchd. They are invoked by the agent that fires the event — typically via a Postgres LISTEN/NOTIFY channel or by a parent script that chains them inline after writing the trigger row.

</launchd_config>

---

## LLM Provider Routing Rules

<llm_routing>

| Task type | Provider | Model | Rationale |
|-----------|----------|-------|-----------|
| Bulk summarization (Tartt) | Gemini | 2.5 Flash | High volume, narrow scope, cost/speed |
| Embeddings | **local** | FastEmbed `bge-base-en-v1.5` @768 | In-process ONNX; no key, no rate limit, client text never leaves the box. Pre-normalized, so M2 is retired. |
| Inbound qualification (Roy Kent) | Anthropic | Haiku | Rubric-based qualification, low volume |
| ICP signal clustering (Nate Shelley) | Anthropic | Sonnet | Weekly synthesis from many signals |
| Triage + draft + self-check (Keeley) | Anthropic | Sonnet | **One merged call** — the model triages, drafts, and self-evaluates in a single context (2026-08-08) |
| Briefing synthesis | Anthropic | Sonnet | First input of the day; quality matters |
| Dashboard synthesis (Higgins) | Anthropic | Sonnet | Weekly narrative across many metrics |
| Health monitoring (Ted) | Anthropic | Haiku | Pattern matching; only when summarizing alerts |
| Fact extraction | Anthropic | Haiku | Structured extraction, high volume |
| Meeting processing | Anthropic | Haiku | Structured extraction, periodic |
| Watch-signal classification (Trent Crimm) | Anthropic | Haiku | Enum classification of short excerpts; per detected item, not per target |
| Outreach packet assembly | **None** | — | Deterministic query by design — no generated prose in the outbound path |

<fallback_rules>

- Gemini quota exhausted (free-tier trial) → throttle cadence first; fall back to Claude Haiku for that batch if the read is time-critical. Do not upgrade to paid during the quality trial.
- Anthropic outage → defer event-driven agents (queue up; resume when service returns). Briefing falls back to a simpler template-based summary if Sonnet is unreachable.
- Both providers down → Ted alerts, system degrades to passive (Discord bot still routes, no new content/drafts).

</fallback_rules>

</llm_routing>

---

## Inter-Agent Communication

<inter_agent_communication>

Agents communicate only through the brain. No direct function calls between agent processes. This decouples them and lets each agent be restarted, replaced, or temporarily disabled without breaking others.

**Patterns**:

1. **Database polling** (v1 default): Event-driven agents poll their input table every N seconds for new rows in a triggering stage. Simple, robust, debuggable.

2. **Postgres LISTEN/NOTIFY** (v2 optimization): Tartt issues `NOTIFY content_item_inserted` after batch insert; Keeley LISTENs and processes. Lower latency, lower DB load than polling, but adds connection-handling complexity. Defer to v2.

3. **In-process chaining** (within Tartt's batch): Tartt may inline-call its scoring logic without going through the DB; the DB write is the durable record but doesn't gate execution within the same batch.

</inter_agent_communication>

---

## Observability

<observability>

Logging conventions:
- Each agent writes structured JSON lines to `~/agents/logs/<agent>.log`
- Required fields per line: `timestamp`, `agent`, `level`, `event`, `correlation_id` (matches a content_item_id or candidate_id where applicable)
- Errors also emit a message to `#system` channel

Metrics held in `dashboard`:
- Last successful run timestamp per agent
- Item counts (content_items/day, notes cognified/day, drafts/day)
- Open/overdue follow-ups
- Pending approvals

Ted reads these metrics every 6 hours and alerts on staleness.

</observability>

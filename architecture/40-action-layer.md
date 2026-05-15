# Action Layer

<doc:layer>implementation</doc:layer>
<doc:stability>medium — edit when agent set or scheduling changes</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md</doc:depends_on>
<doc:referenced_by>50-channel-layer.md, 60-content-pipeline.md, 70-build-order.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file defines how the action layer runs on the Mac mini: agent specifications, scheduling (launchd), supervision, credential handling, and the LLM-provider routing rules. All LLM calls in this layer pass through the cost-emission helper documented in `80-telemetry-layer.md`.

---

## Critical Discipline: The Cost Helper

<cost_helper_reference>

Every LLM call from every agent passes through the cost-emission helper (`~/agents/_lib/runs.py`). Agents do not import provider SDKs directly. The helper enforces per-run token caps, per-day spend ceilings, and writes the agent_runs ledger.

Full specification: `80-telemetry-layer.md`.

Agent specs below state their declared caps and daily ceiling. These are starting values; tune from agent_runs data.

</cost_helper_reference>

---

## Execution Environment

<environment>

- **Host**: Mac mini (`mini`), running macOS, dedicated agent account (`agent`).
- **Language stack**: Python 3.12 for agents and Discord bot. Node.js only if a specific library is Node-only.
- **Package management**: `uv` for Python (faster than pip, lockfile-based). One venv per agent module under `~/agents/venv/`.
- **Repo location**: `~/agents/` (pulled from git, never edited directly).
- **Credentials**: macOS Keychain via `security` CLI. Never in env files committed to git.

</environment>

<credential_inventory>

| Credential | Keychain item name | Used by |
|------------|---------------------|---------|
| Supabase service-role key | `supabase-service-key` | All agents (writes) |
| Supabase anon key | `supabase-anon-key` | Laptop Claude Code sessions (reads) |
| Supabase DB password | `supabase-db-password` | Backup script (`pg_dump`) |
| Gemini API key | `gemini-api-key` | Tartt, embedding jobs |
| Anthropic API key | `anthropic-api-key` | Keeley Strategy, Keeley Content, Sam, Briefing, Ted, fact extraction |
| Buffer access token | `buffer-access-token` | Keeley Distribution |
| Discord bot token | `discord-bot-token` | Discord bot |
| Backup encryption pub key | `~/.config/brain-backup.pub` (file) | Backup script |

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
3. Summarize via Gemini 2.5 Flash (target: 3-sentence summary, 1-sentence why-it-matters, list of ICP pain-points mentioned if any)
4. Embed summary via Gemini `text-embedding-004`
5. Score: cosine similarity against each `interest_signal.embedding`, weighted by signal.weight, summed; multiplied by `source.trust_score`
6. Insert into `content_items`
7. For each ICP pain-point mentioned, embed it and insert into `icp_signals` (wide-net pattern, W2 substrate)
8. Emit event for Keeley Strategy on items scoring above threshold (initial threshold: top 20% of run)

**LLM choice**:
- Gemini 2.5 Flash for summarization. Rationale: high volume, narrow scope, cost and rate-limit advantages over Claude Sonnet for this task type.
- Gemini `text-embedding-004` for embeddings. Rationale: consolidate Tartt stack on one provider; 768 dimensions sufficient for similarity tasks at this scale.

**Token caps**: 4,000 input / 500 output per item summarization. 2,000 input per embedding call.
**Daily ceiling**: $5.00/day.

**Error handling**:
- Single-source failure: log to `#system`, continue with other sources.
- Gemini API failure: retry with exponential backoff (3 attempts), fall back to Claude Haiku for that batch.
- Embedding failure: store `content_item` row with `embedding = NULL`; nightly job re-attempts.
- Daily ceiling exceeded: skip remaining items; alert via #system.

**Status reporting**: posts a one-line summary to `#system` on completion (item count, source count, errors).

**Workflows served**: W3 (primary), W2 (contributes icp_signals).

</agent_spec>

<agent_spec id="Nate_Shelley">

**Trigger**: `launchd` weekly on Sunday at 8:00 PM local time.

**Job**: Cluster the past 7 days of icp_signals into themes. Surface top 5 clusters with frequency, source diversity, and representative quotes.

**Inputs**:
- `icp_signals` (last 7 days, all rows regardless of source)

**Outputs**:
- `facts` (the weekly synthesis recorded as a fact with `domain='icp-intelligence'`)
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

<agent_spec id="Keeley_Strategy">

**Trigger**: Event-driven — invoked after each Tartt run for items scoring above threshold.

**Job**: Decide whether an item is relevant to AI Adaptive's ICP and positioning. If yes, promote to drafting.

**Inputs**:
- `content_items` (the new item)
- `decisions` filtered by `domain IN ('icp', 'positioning')` for current standing positions
- `interest_signals` (top 10 by weight)

**Outputs**:
- `content_pipeline` (new row with stage='triaged' or stage='declined' with reason)

**LLM choice**: Claude Sonnet. Rationale: this is a reasoning task — does this content fit our positioning? It runs at most ~10-20 times per day. Cost is bounded and reasoning quality matters.

**Error handling**: Failed triage → row remains in `discovered` stage; Ted alerts after 24h with no triage.

</agent_spec>

<agent_spec id="Keeley_Content">

**Trigger**: Event-driven — invoked after Keeley Strategy promotes an item to `triaged`.

**Job**: Produce a content draft (newsletter snippet, social post, or long-form opening, depending on item type).

**Inputs**:
- `content_pipeline` row (triaged)
- Source `content_items` row
- Relevant `facts` retrieved via hybrid search seeded by content_item.embedding
- `decisions` filtered by `domain IN ('style', 'voice')`

**Outputs**:
- `content_pipeline.draft_text`, stage transitions to `drafted`

**LLM choice**: Claude Sonnet. Rationale: drafting is the highest-quality work in the pipeline; this is where reasoning depth pays off. Cost is justified — one draft per published piece.

**Output format**: Structured — title, hook, body, CTA fields. The draft is not a wall of prose.

</agent_spec>

<agent_spec id="Sam_Obisanya">

**Trigger**: Event-driven — invoked after Keeley Content writes a draft.

**Job**: Evaluate the draft against style, positioning, and quality criteria. Pass or return for revision.

**Inputs**:
- `content_pipeline.draft_text`
- `decisions` filtered by `domain IN ('style', 'voice', 'positioning')`
- Evaluation rubric (loaded from a config file in the repo, not a DB table — it's code-versioned)

**Outputs**:
- `content_pipeline.sam_evaluation` (JSONB: pass/fail per criterion, overall verdict, suggestions)
- On pass: stage transitions to `sam_passed`, then immediately to `approval_queue` post
- On fail: stage returns to `triaged` with sam_evaluation populated; Keeley Content can re-draft with the feedback

**LLM choice**: Claude Haiku. Rationale: evaluation against explicit criteria is a narrower task than drafting. Haiku is sufficient and meaningfully cheaper. If false-negatives emerge, upgrade to Sonnet.

**Maximum re-draft cycles**: 2. After 2 failed evaluations, item goes to approval queue with sam_evaluation visible so the human can decide.

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
- `facts` from last 24h, all
- `task_candidates` where `status = 'pending'`, top 5 by confidence
- `icp_signals` (top theme this week, sourced from Nate Shelley's most recent synthesis)
- `dashboard` (cadence flags, system health)

**Outputs**:
- Discord message to `#briefing`
- `dashboard.briefing_posted_at` updated

**LLM choice**: Claude Sonnet. Rationale: synthesis quality matters — this is your first input every morning.

**Token caps**: 32,000 input / 3,000 output per briefing.
**Daily ceiling**: $0.50/day.

**Format**: Sections — Priorities (overdue follow-ups), New prospects (W1), New today (top reading), Discovery follow-ups (W4), ICP signal of the week (W2), New facts, Task candidates (link to #task-tinder), System status.

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

</scheduled_jobs_summary>

Event-driven agents (Roy Kent via webhook, Keeley Strategy, Keeley Content, Sam, Keeley Distribution, Fact extraction, Meeting processor) are not in launchd. They are invoked by the agent that fires the event — typically via a Postgres LISTEN/NOTIFY channel or by a parent script that chains them inline after writing the trigger row.

</launchd_config>

---

## LLM Provider Routing Rules

<llm_routing>

| Task type | Provider | Model | Rationale |
|-----------|----------|-------|-----------|
| Bulk summarization (Tartt) | Gemini | 2.5 Flash | High volume, narrow scope, cost/speed |
| Embeddings | Gemini | text-embedding-004 | Consolidates Tartt stack |
| Inbound qualification (Roy Kent) | Anthropic | Haiku | Rubric-based qualification, low volume |
| ICP signal clustering (Nate Shelley) | Anthropic | Sonnet | Weekly synthesis from many signals |
| Triage / ICP fit (Keeley Strategy) | Anthropic | Sonnet | Reasoning task, bounded volume |
| Content drafting (Keeley Content) | Anthropic | Sonnet | Quality matters; one call per draft |
| Output evaluation (Sam) | Anthropic | Haiku | Rubric-based eval, narrow task |
| Briefing synthesis | Anthropic | Sonnet | First input of the day; quality matters |
| Dashboard synthesis (Higgins) | Anthropic | Sonnet | Weekly narrative across many metrics |
| Health monitoring (Ted) | Anthropic | Haiku | Pattern matching; only when summarizing alerts |
| Fact extraction | Anthropic | Haiku | Structured extraction, high volume |
| Meeting processing | Anthropic | Haiku | Structured extraction, periodic |

<fallback_rules>

- Gemini quota exhausted → fall back to Claude Haiku for that batch.
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

2. **Postgres LISTEN/NOTIFY** (v2 optimization): Tartt issues `NOTIFY content_item_inserted` after batch insert; Keeley Strategy LISTENs and processes. Lower latency, lower DB load than polling, but adds connection-handling complexity. Defer to v2.

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
- Item counts (content_items/day, facts/day, drafts/day)
- Open/overdue follow-ups
- Pending approvals

Ted reads these metrics every 6 hours and alerts on staleness.

</observability>

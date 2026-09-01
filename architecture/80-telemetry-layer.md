# Telemetry Layer

<doc:layer>implementation — fourth architectural layer</doc:layer>
<doc:stability>medium — evolves as agents are added and metrics tune</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md, 40-action-layer.md</doc:depends_on>
<doc:referenced_by>70-build-order.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file defines the telemetry layer: how the system measures itself, bounds
token spend, and reports against the north star. It sits alongside channel,
action, and memory as the fourth layer — observability for the AFC Richmond agent
swarm.

> **Phase 3.7 re-plumb (M1).** The cognee pivot changed this layer's shape.
> cognee owns the call site for most LLM spend and can't support per-call gating,
> so the old **pre-flight refusal** model (a per-run token cap that aborted a
> call, a hard per-day gate) is gone, and so are per-agent Anthropic keys. The
> model is now uniform: **label + record every call**, bound spend with a **soft
> daily breaker** that blocks the *next* invocation once a ceiling is crossed, and
> verify with a **monthly reconcile** against the provider bills. Attribution
> lives entirely in the `agent_runs` ledger, not in provider dashboards.

The layer has five components:

1. The `agent_runs` ledger — every LLM/embedding call recorded with cost
2. The cost-emission helper (`_lib/runs.py`) — the write path for our **own**
   agents' calls
3. The labeling path (`_lib/telemetry_context.py`, M1) — the write path for
   **cognee's** internal calls, which we don't own the call site of
4. Spend discipline — a soft daily breaker (`assert_under_ceiling`) + a monthly
   `cli/reconcile` backstop, plus retrospective anomaly detection (G3, Ted)
5. Two reporting agents — Ted (reactive, 6-hourly) and Higgins (reflective, weekly)

---

## North Star

<north_star>

**Sustainable long-term contract engagements.**

Three key results:

- **KR1**: New contract engagements per quarter
- **KR2**: Dollar value per engagement
- **KR3**: Project → maintenance conversion rate

Every workflow ultimately serves one or more of these KRs. Every metric ultimately rolls up to enabling the KRs. Operational metrics (cost per item, acceptance rate, tokens per draft) are evidence — useful for tuning the system — but the dashboard headlines the KRs themselves.

</north_star>

---

## The agent_runs Ledger

<agent_runs_table>

Every LLM/embedding call — from our own agents *and* from cognee — writes one
row. This single table feeds spend metrics, cost-per-output calculations,
token-discipline tracking, the soft breaker, reconcile, and anomaly detection.

```sql
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,        -- 'tartt', 'fact-extraction', 'cognee', …
    function_label  TEXT NOT NULL,        -- 'news_aggregation', 'topic_research',
                                          -- 'action_surfacing', 'customer_discovery',
                                          -- 'infrastructure', 'telemetry',
                                          -- 'outreach_watch'
    trigger_kind    TEXT NOT NULL,        -- 'scheduled', 'event', 'manual'
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL,        -- 'success', 'partial', 'failed'
    llm_provider    TEXT,                 -- 'gemini', 'anthropic', null for non-LLM agents
    llm_model       TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    usd_cost        NUMERIC(14,8),  -- widened from (10,4) in migration 0002: 4dp truncated cheap embedding calls to $0.0000
    correlation_id  TEXT,                 -- e.g. content_item_id, prospect_id, capture source_ref
    correlation_kind TEXT,                -- 'content_item', 'prospect', 'cognify_run', …
    error_text      TEXT
);

CREATE INDEX agent_runs_agent_time_idx ON agent_runs (agent_name, started_at DESC);
CREATE INDEX agent_runs_status_idx     ON agent_runs (status) WHERE status != 'success';
CREATE INDEX agent_runs_function_idx   ON agent_runs (function_label, started_at DESC);
```

> `status` no longer includes `token_cap_exceeded` — the per-run cap that produced
> it (G1) was removed in the pivot. cognee's calls carry
> `correlation_kind='cognify_run'`, so `GROUP BY correlation_id` rolls a whole
> cognify (its per-chunk fan-out) up to one logical operation.

**Function labels** — four core swarm functions, two for system work, and one for outreach watch (seven total).
`agent_name` is finer-grained than `function_label`; cognee's work maps in as
below:

| Label | Agents |
|-------|--------|
| `news_aggregation` | Tartt |
| `topic_research` | Keeley, Nate Shelley |
| `action_surfacing` | Briefing, Task extractors, Meeting processor |
| `customer_discovery` | Roy Kent, Nate Shelley, inbound webhooks, **cognee capture** (`agent_name='fact-extraction'`) |
| `infrastructure` | **retrieval** (`_lib/retrieval.py`, all scopes), **playbook-publish**, **cognee** (unlabeled internal calls) |
| `telemetry` | Higgins, Ted's anomaly detection |
| `outreach_watch` | Trent Crimm (watch-signal classification, Haiku). **The only outreach LLM spend** — packet assembly is a deterministic query with no LLM call (`35-outreach-crm.md` v0.3.0), and the retired `outreach` observation label was never deployed. Ceiling $0.30/day. |

Some agents (Nate Shelley) serve more than one function. Each run records the
function label active for that specific run.

</agent_runs_table>

---

## The Cost-Emission Helper (own agents)

<cost_helper>

Our own agents invoke LLMs through a single Python helper, `_lib/runs.py`. No
agent computes its own cost reporting. No agent calls an LLM SDK directly. This is
enforced by convention and by code review at the git-gate. (cognee is the
exception — it *does* call providers itself; that spend is captured by the
labeling path, next section.)

<helper_interface>

```python
# agents/_lib/runs.py

@contextmanager
def agent_run(
    agent_name: str,           # must have a DAILY_CEILINGS entry (else ValueError)
    function_label: str,
    *,
    trigger_kind: str = "manual",
    correlation_id: str | None = None,
    correlation_kind: str | None = None,
) -> Iterator["RunContext"]:
    """Records exactly one agent_runs row per run.
    On entry: soft daily breaker (assert_under_ceiling) — raises
      DailyCeilingExceeded and writes no row if the agent/system is over.
    On exit: writes the row (success or failed) with tokens + cost.
    """


class RunContext:
    # No max_input_tokens anywhere — the per-run input cap (G1) was removed.
    def call_anthropic(self, messages: list, *, model: str,
                       max_output_tokens: int, system: str | None = None) -> str: ...

    def call_anthropic_structured(self, messages: list, *, model: str,
                       max_output_tokens: int, tool_name: str,
                       tool_description: str, input_schema: dict,
                       system: str | None = None) -> dict:
        """Single forced tool → schema-validated output dict."""

    def call_gemini(self, prompt: str, *, model: str,
                    max_output_tokens: int) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]:   # local, free
        """Retired 2026-08-03: embeddings are local FastEmbed bge@768,
        pre-normalized, in-process. No provider call, no ledger row, no M2."""
```

</helper_interface>

<helper_usage_example>

```python
from agents._lib.runs import agent_run

def qualify(prospect_text, prospect_id):
    with agent_run("roy-kent", "customer_discovery",
                   trigger_kind="event",
                   correlation_id=str(prospect_id),
                   correlation_kind="prospect") as run:
        return run.call_anthropic_structured(
            messages=[{"role": "user", "content": prospect_text}],
            model="claude-haiku-4-5", max_output_tokens=600,
            tool_name="qualify", tool_description="Score ICP fit.",
            input_schema=QUALIFY_SCHEMA,
        )
```

</helper_usage_example>

<helper_behaviors>

The helper handles four responsibilities, in order:

1. **Soft breaker on entry**: `assert_under_ceiling(agent_name)` sums today's
   spend (the agent's own and the system-wide total) in one query; if either is
   at/over its ceiling it raises `DailyCeilingExceeded` and writes no row. This
   blocks the *next* invocation — it can't unwind the call that crossed the line
   (see the breaker section).

2. **Price check before the call**: `_price_for(provider, model)` raises
   `ValueError` if the model has no `PRICE_TABLE` entry — *before* the paid call,
   so we never pay for a call whose cost can't be recorded. (This is the only
   pre-call refusal left; it's about bookkeeping integrity, not spend gating.)

3. **Call execution**: makes the API call. Any exception inside the block
   (provider timeouts, 5xx, rate limits, programmer errors) writes a `failed` row
   and re-raises.

4. **Cost capture**: on success, computes `usd_cost` from token counts and the
   price table, and writes the row. Multi-call runs accumulate.

</helper_behaviors>

<price_table_versioning>

The price table is a constant in the helper module — not a database table. Prices
change rarely and deliberately; changes go through git review; no agent should be
able to inflate or deflate its reported cost dynamically.

```python
PRICE_TABLE = {  # (provider, model) -> USD/token
    ("anthropic", "claude-haiku-4-5"):     {"input": 1.0/1e6,   "output": 5.0/1e6},
    ("anthropic", "claude-sonnet-4-6"):    {"input": 3.0/1e6,   "output": 15.0/1e6},
    ("gemini",    "gemini-2.5-flash"):     {"input": 0.075/1e6, "output": 0.30/1e6},
    # ("gemini", "gemini-embedding-001") removed 2026-08-03 — embeddings are
    # local FastEmbed bge@768: no provider call, no cost, no ledger row.
    # … (opus tiers etc.)
}
```

cognee's calls arrive through litellm with provider-prefixed model strings
(`anthropic/claude-haiku-4-5`), so the labeling path keeps its **own** substring
price map (`telemetry_context._PRICE`) rather than reusing these exact keys. Keep
the two in sync when prices change.

</price_table_versioning>

</cost_helper>

---

## The Labeling Path (cognee calls — M1)

<labeling_path>

cognee makes LLM calls we don't own the call site of, so `agent_run` can't wrap
them. Instead (`_lib/telemetry_context.py`):

> **Embeddings are local now (2026-08-03).** cognee embeds via FastEmbed
> (`bge-base-en-v1.5`, in-process ONNX) — no API call, so **no litellm, no ledger
> row, no cost**. The labeling path therefore captures only cognee's **Anthropic
> extraction** spend; a cognify shows Anthropic rows and zero embedding rows.
> (Gemini is reserved for news ingestion — see Provider allocation below.)

- `labeled(agent_name, function_label, *, trigger_kind, correlation_id)` sets a
  **contextvar** around a cognee operation (e.g. `labeled("fact-extraction",
  "customer_discovery", correlation_id=source_ref)` in `ingest.py`, or
  `labeled("granola", …)` in the Granola poller). It propagates into asyncio child
  tasks, so it survives cognee's per-chunk fan-out.
- `install_litellm_callback()` registers a litellm `CustomLogger` that fires on
  every provider call, reads the current label, and writes a conformant
  `agent_runs` row (`correlation_kind='cognify_run'`). Installed by
  `configure_cognee()` at process start.
- **Routing is load-bearing**: cognee must reach Anthropic *through litellm*
  (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…`). cognee's native Anthropic
  adapter calls the raw SDK and bypasses the callback — under that routing the
  ledger would silently lose ~all LLM spend. The spike proved the callback reaches
  100% of cognee's calls under custom routing; MW2 confirmed it in production
  (18 `cognify_run` rows for a 2-doc smoke).
- **Unlabeled fallback**: a cognee call outside any `labeled()` block is still
  recorded, under `agent_name='cognee'`, `function_label='unlabeled'` — so nothing
  cognee spends goes unmeasured. `cognee` has its own `$5/day` ceiling.

This is why the ledger is authoritative for spend attribution: both write paths
land in one table, keyed by `agent_name` / `function_label` / `correlation_id`.

</labeling_path>

---

## Provider Allocation (API-use plan, 2026-08-03)

<provider_allocation>

Which external provider does what — deliberately narrow, so spend and data
exposure are predictable:

| Work | Provider | Where |
|------|----------|-------|
| **Generative LLM** — all of it: cognee extraction/graph-building, and every agent (Roy Kent, Keeley, Trent Crimm, meeting-processor, briefing synthesis, …) | **Anthropic** (`claude-*`, via litellm for cognee; direct SDK for own-agents) | `runs.py` + the M1 labeling path |
| **News ingestion only** — Tartt's content summarization (batched 10–15 extracts/call) | **Gemini** (`gemini-2.5-flash`) | `runs.py` (Phase 4) |
| **All embeddings** — graph vectors, `content_items`, interest similarity | **local FastEmbed** `bge-base-en-v1.5` @768, in-process ONNX | none — no provider call, no ledger row, no cost |
| **Knowledge-graph embeddings** — capture, meetings | **Local FastEmbed** (`bge-base-en-v1.5` @768, ONNX; no key, no rate limit, no cost) | cognee (`cognee_setup.py`) |

Rationale: Gemini is boxed into the news domain (its free-tier embed cap is what
broke the first Granola poll); Anthropic does the reasoning; the knowledge graph
(client meeting content) embeds **on-box** for privacy + zero rate limits.
Two 768-dim embedding spaces coexist but are never compared cross-space: Gemini
for `content_items` (news, in `aiadaptive_cos`), bge for the cognee graph (in
`aiadaptive_cognee`). **Embedding fallback:** Voyage (`voyage-3.5`, cloud,
Anthropic's recommended partner) if the local path proves flaky — commented block
in `cognee_setup.build_cognee_env`.

`PRICE_TABLE`'s `gemini-embedding-001` row therefore prices **news** embeddings
only; cognee's local embeddings never hit the price map (they're free).

</provider_allocation>

---

## Spend Discipline

<spend_discipline>

The old three-guard stack (per-run cap G1, hard per-day gate G2, anomaly G3)
became two-plus-one when cognee took over the call site. **G1 is gone** — a
per-call token cap can't apply to calls made inside cognee's cognify fan-out.
What remains: a **soft daily breaker** (was G2, reframed), a **monthly reconcile**
backstop that makes the soft breaker safe, and **G3** anomaly detection unchanged.

<breaker name="Soft daily breaker (assert_under_ceiling)">

**What it does**: bounds spend per agent and system-wide, but does **not** refuse
the call that crosses the line — that call already happened (or happens inside
cognee). It blocks the *next* invocation.

**Where it lives**: `assert_under_ceiling(agent_name)` in `_lib/runs.py`. Runs on
`agent_run` entry, and is callable directly before a cognee operation. One query
sums today's spend for the agent *and* the system-wide total, so a bug spread
across several agents is still bounded by `GLOBAL_DAILY_CEILING` ($20). "Today"
starts at **local** midnight. Check-then-act: concurrent runs can overshoot by
roughly one call's cost — acceptable at these ceilings, and reconcile catches
sustained drift.

**Starting ceilings** (`DAILY_CEILINGS`, USD/day):

| Agent | Ceiling | Phase |
|-------|---------|-------|
| `cognee` (unlabeled internal) | $5.00 | 3.7 |
| `fact-extraction` (cognee capture) | $2.00 | 3 |
| `recall` (query embeddings) | $0.50 | 3.3 |
| `tartt` | $5.00 | 4 |
| `keeley-strategy` / `keeley-content` | $1.50 each | 8 |
| `sam` | $1.00 | 8 |
| `roy-kent` | $1.00 | 6 |
| `meeting-processor` | $3.00 ($1/transcript) | 7 |
| `briefing` | $0.50 | 3+ |
| `nate-shelley` | $0.07 (~$0.50/wk) | 10 |
| `ted` | $0.20 | 11 |
| `higgins` | $0.04 (~$0.30/wk) | 11 |
| **System-wide (`GLOBAL_DAILY_CEILING`)** | **$20.00** | kill switch |

Labeled cognee calls bill to the labeling agent's ceiling (e.g. capture →
`fact-extraction`); only *unlabeled* cognee internals hit the `cognee` ceiling.
An `agent_name` with no ceiling entry is bounded only by the global one; own
agents must have an entry (`agent_run` raises `ValueError` otherwise).

**Failure mode**: `DailyCeilingExceeded` raised, no row written. The agent's
behavior on it is its choice — Tartt skips the item and continues; Briefing falls
back to template-mode (structured query results, no LLM synthesis); Keeley leaves the
draft at `drafted` and surfaces a #system backlog warning. Ted alerts at 80% of a
ceiling so you can intervene before service degrades.

</breaker>

<backstop name="Monthly reconcile (cli/reconcile)">

Dropping the hard pre-flight gate means a code path that bypassed the ledger could
spend money and log nothing. `cli/reconcile.py` is the backstop that justifies the
trade: it computes authoritative ledger spend by provider for a window and compares
it to the **operator-supplied** provider-dashboard figures (`--anthropic`,
`--gemini` — automating the pull needs org-admin billing creds we don't assume).
It flags any divergence beyond `--tolerance` (default 15%, or a $0.01 absolute
floor for tiny figures) and exits nonzero on divergence, so it can back a monthly
routine/alert. This is what makes "record, don't refuse" safe: if the ledger ever
drifts from the bill, the month-end check catches it.

</backstop>

<guard id="G3" name="Anomaly detection on tokens-per-output">

**What it does**: Catches gradual efficiency regressions — an agent whose spend is still under its ceiling but which is using meaningfully more tokens per output than its historical baseline.

**Where it lives**: Pure Python computation in Ted, running every 6 hours. No LLM calls — this is SQL plus statistics. (If this were an LLM-cost computation we'd hand it to Higgins; since it's free, Ted does it reactively.)

**Algorithm**:

```python
# For each agent, compare last 24h tokens-per-correlation vs. rolling 7-day median.
for agent in active_agents:
    last_24h = db.fetch("""
        SELECT AVG(input_tokens + output_tokens) AS tpc
        FROM agent_runs
        WHERE agent_name = %s
          AND status = 'success'
          AND started_at >= now() - interval '24 hours'
          AND correlation_id IS NOT NULL
    """, agent)
    rolling_7d = db.fetch("""
        SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY tpc) AS median
        FROM (
            SELECT correlation_id,
                   AVG(input_tokens + output_tokens) AS tpc
            FROM agent_runs
            WHERE agent_name = %s
              AND status = 'success'
              AND started_at >= now() - interval '7 days'
              AND started_at <  now() - interval '24 hours'
            GROUP BY correlation_id
        ) sub
    """, agent)
    if last_24h.tpc > 2.0 * rolling_7d.median:
        alert(f"{agent}: tokens-per-output 2× rolling median "
              f"({last_24h.tpc:.0f} vs. {rolling_7d.median:.0f})")
```

**Threshold**: 2× the rolling 7-day median. Tunable; start conservative to avoid alert fatigue.

**Requires**: At least 7 days of agent_runs data with successful runs. Disabled for agents with <20 runs in the rolling window (insufficient data for a stable median).

**Failure mode**: Alert to #system with the agent name and the deviation. Operator investigates: prompt regression, context bloat from accumulated state, model deprecation forcing longer reasoning, poison input.

</guard>

</spend_discipline>

---

## Three Metrics Per Agent

<metrics_per_agent>

Every agent gets at most three metrics. One is token-discipline (catches spirals retrospectively, complements the guards). One is effectiveness (is the agent doing its job well). One is outcome (does the agent's work feed into KRs).

| Agent | Token-discipline | Effectiveness | Outcome |
|-------|------------------|---------------|---------|
| Tartt | Tokens per content_item | Acceptance rate (kept vs. dismissed) | Items contributing to icp_signals or content_pipeline |
| Roy Kent | Tokens per prospect qualified | High-fit % of inbound | Prospects → discovery calls converted |
| Nate Shelley | Tokens per cluster surfaced | Cluster reuse rate (referenced in content/outreach) | Signal density (distinct sources per cluster) |
| Keeley | Tokens per item (one merged call: triage + draft + self-check) | Human approval rate at `#approvals` — **>30% rejection over the first 20 drafts re-adds a separate evaluator** | Approved → published conversion; decline rate calibration |
| Keeley Distribution | API calls per post | Posts scheduled successfully | Posts → outcomes attributed |
| Briefing | Tokens per briefing | Tasks accepted from briefing | Decisions logged per briefing read |
| Task extractors | Tokens per candidate | Acceptance rate (Task Tinder ✅) | Accepted → completed conversion |
| Meeting processor | Tokens per transcript | Follow-ups generated per transcript | Follow-ups → completed conversion |
| Capture (cognee) | Tokens per note cognified | Recall answer quality (spot-checked) | (infrastructure — no outcome metric) |
| Ted | Tokens per check cycle | Mean time to alert on failure | Alert precision (true vs. false positives) |
| Higgins | Tokens per weekly digest | Weeks with KR movement reported | Outcomes recorded per week |
| Trent Crimm | Tokens per detected item classified | Card precision (Gate-4 re-engage vs. dismissed) | Re-engagements → engagements (E1 experiment) |
| Outreach loops (no LLM) | — (deterministic; no `agent_runs` rows) | Touches sent on schedule; touch-five completion (tracked separately) | Conversations opened; calls held; touch-of-first-reply distribution (quarterly, gated at 40 completed sequences) |

</metrics_per_agent>

---

## Ted's Telemetry Responsibilities

<ted_telemetry>

Ted's scope expands from pure health monitoring to include real-time cost guarding. Specifically:

Every 6 hours, Ted:

1. **Reads dashboard** (existing): timestamps of last successful runs per agent.
2. **Computes anomaly detection** (new, G3): rolling-median check on tokens-per-output for each agent. Pure SQL plus Python; zero LLM cost.
3. **Checks ceiling proximity** (new): for each agent, computes today's spend; alerts at 80% of daily ceiling.
4. **Counts failures** (new): agents with >3 `failed` runs in last 6 hours get flagged.
5. **Pings its external check** (`cos-ted`) on the success path — see "Who Watches Ted" below — and writes `dashboard.last_ted_run_at`.
6. **Checks outreach invariants** (Track O, pure SQL over `outreach_*` — no LLM):
   `cold_live > 15` or `reengagement_live > 3` · any touch past `window_closes`
   unsent and unskipped · packets `ready=false` for >48h · drain blocked on a
   missing `stalled_reason` for >7 days (the card is costing a capacity slot) ·
   evidence loop silent >48h · >25% of displayed evidence `ageing` or worse ·
   BCC poller silent >2h · watch loop silent >8 days.
7. **Posts/updates pinned status in #system** (existing): single message showing all agent health states.

Ted does call Claude Haiku for its alert summarization (deciding how to phrase a complex alert) but only when there's something to alert about. Most 6-hour checks are pure Python with no LLM call and no `agent_runs` row.

When Ted does call Haiku, it logs to agent_runs with `function_label='telemetry'` to keep its own spend visible.

</ted_telemetry>

---

## Who Watches Ted — the external witness

<dead_mans_switch>

**The gap.** Ted watches the agents. launchd watches Ted's *process* — but
`KeepAlive` only restarts a process that exited; it cannot detect a process that
is running and doing nothing, and it cannot report anything if the machine is
off. Every monitor in this system lives on the box it monitors. Power cut, full
disk, a macOS update reboot-loop, a wedged scheduler daemon holding its lock —
all fail **silently**, and the operator finds out by noticing no briefing
arrived, which is both slow and easy to rationalise on a busy morning.

This is the only failure class the architecture cannot currently see, and it is
the cheapest one to close.

### The mechanism: push-based, per-loop

Each critical loop **pings an external check on success**. Absence of a ping is
the alert — which is what makes it work when the box is dead, the network is
down, or the process is wedged. A monitoring service that *polls* the box would
miss the wedged-scheduler case entirely (the gateway answers `/health` fine
while nothing is being scheduled), so **liveness must be reported by the work
itself, not by the box.**

Recommended: **healthchecks.io** (free tier ≈ 20 checks, ample). Any dead-man's
switch works; the property required is *alert on absence*.

| Check | Pinged by | Period | Grace |
|-------|-----------|--------|-------|
| `cos-briefing` | morning-briefing loop, after posting | 24h | 1h |
| `cos-ted` | Ted, at the end of each cycle | 6h | 1h |
| `cos-scheduler` | scheduler daemon heartbeat | 1h | 15m |
| `cos-outreach-evidence` | evidence poller (Track O) | 12h | 2h |
| `cos-outreach-bcc` | BCC poller (Track O) | 15m | 10m |
| `cos-backup` | nightly `pg_backup.sh`, on exit 0 | 24h | 2h |

Two lines at the end of each loop:

```python
# only on the success path — a ping in a finally: block reports "alive" while broken
requests.get(f"https://hc-ping.com/{uuid}", timeout=5)
```

**Design rules that make this trustworthy:**

- **Ping only on success**, never in `finally`. A switch that fires on a crashed
  run is worse than none — it manufactures confidence.
- **Ping the `/fail` endpoint on a caught exception** so a broken run alerts
  immediately rather than waiting out the grace window.
- **Alert to a channel that is not Discord.** Email or phone push. Discord is
  where the failing system posts; if the bot is down, `#system` is exactly where
  the alert will not appear. This is the whole point of an *external* witness.
- **Distinct check per loop, not one aggregate.** "Something is wrong" costs a
  debugging session; "the BCC poller stopped 40 minutes ago" does not.

### Second layer: Ted's own timestamp, surfaced daily

Off-box alerting can itself fail (an expired free account, a changed URL, an
unread inbox). So also make staleness **visible in the surface the operator reads
every day**:

- Ted writes `dashboard.last_ted_run_at` at the end of each cycle (the `dashboard`
  table already exists for exactly this pattern).
- The briefing's System line renders it: `✅ Ted 2h ago · scheduler 12m ago` — and
  renders it **in red past two missed cycles**.

This costs one column read and closes the loop by human attention rather than by
infrastructure. Belt and braces: if the dead-man's switch is silently broken, the
briefing still shows a stale timestamp; if the briefing stops entirely, the
dead-man's switch fires.

### As-built (PERF-4, 2026-08-08)

The reusable helper is `agents/_lib/heartbeat.py` — `ping(slug)` on success,
`ping_fail(slug)` on a caught exception, driven by one keychain secret
`healthchecks-ping-key` (the project ping key), addressing each check by slug
(`https://hc-ping.com/<key>/<slug>`). It **no-ops until the key is provisioned**,
so it is safe to ship un-armed. A ping never raises.

Wired: **`cos-briefing`** (`agents/briefing/run.py`, success + `/fail`),
**`cos-backup`** (`scripts/pg_backup.sh`, success + an `ERR` trap `/fail`),
**`cos-scheduler`** (`agents/scheduler/run.py` `Scheduler._maybe_beat`, a
rate-limited liveness ping each daemon cycle — the whole-schedule watchdog: if
the daemon wedges, every loop stops firing and this trips in 1h, faster than any
individual loop's grace), **`cos-outreach-evidence`** (`agents/outreach/
evidence.py`, success + `/fail`), and **`cos-outreach-bcc`**
(`agents/outreach/gmail_capture.py` `run_bcc`, pinged only on a live pass — never
on the intentional skip when the bcc@ credential is absent, so the check stays
honest). Still to wire as its loop lands: `cos-ted` (Phase 11). **Operator/runtime
setup** (once): create the healthchecks.io project, add `healthchecks-ping-key` to
barry-agent's keychain, create the checks per the table above — `cos-briefing`
(24h/1h), `cos-scheduler` (1h/15m), `cos-outreach-evidence` (12h/2h),
`cos-outreach-bcc` (15m/10m, only once bcc@ IMAP is set up), `cos-backup` (24h/2h)
— and point the project's alert at an **off-Discord** channel (email/push). The second layer (Ted's timestamp in the briefing System line) is
not yet built — it lands with the real briefing in Phase 4.

### What this deliberately does not do

No second local watchdog process. A watcher watching the watcher on the same box
adds a failure mode and answers none of the ones that matter — the box being off
is not observable from the box. Fifteen minutes of work, one external dependency,
and the only alerting in the design that survives the machine itself.

</dead_mans_switch>

---

## Higgins's Weekly Dashboard

<higgins_dashboard>

Higgins runs Mondays 7am. One LLM call (Claude Sonnet) to synthesize the structured query results into a readable digest. Posts to #dashboard.

<dashboard_format>

```
## Weekly dashboard — week of YYYY-MM-DD

### North star: sustainable long-term contract engagements

  New engagements this period:    N  (target: M)
  $ per engagement (median):      $X,XXX  (vs. prior quarter $Y,YYY)
  Project → maintenance:          XX% over rolling 90 days

### Cost discipline

  Spend by function (7 days):
    news_aggregation:      $XX.XX  (Tartt)
    topic_research:        $XX.XX  (Keeley, Nate Shelley)
    action_surfacing:      $XX.XX  (Briefing, Task extractors, Meeting processor)
    customer_discovery:    $XX.XX  (Roy Kent, Nate Shelley, webhooks)
    infrastructure:        $XX.XX  (retrieval, Ted, fact extraction)
    telemetry:             $XX.XX  (Higgins, Ted alerting)
    outreach_watch:        $XX.XX  (Trent Crimm — only outreach LLM spend)
    total:                 $XX.XX

  Token discipline flags this week:
    [list of agents that hit G3 anomaly threshold, with deviation]
    [list of agents that crossed 80% daily ceiling, with frequency]
    [list of DailyCeilingExceeded blocks, with agent + count]
    [reconcile: ledger vs provider-bill divergence, if the monthly check ran]

### Workflow throughput

  W1 inbound prospects:        N qualified, M high-fit
  W2 ICP signal clusters:      N surfaced this week, top theme: [theme]
  W3 content pipeline:         N drafted, M approved, P published
  W4 discovery calls:          N processed, M follow-ups extracted
  W5 daily briefings:          7 of 7 posted
  W6 captures:                 N notes captured to the graph
  W7 (this dashboard):         posted
  W8 outreach:                 N/M touches on schedule (window-based) ·
                               touch-five completion X/Y · N conversations ·
                               cold live K/15 · E1 allowance J/3 ·
                               evidence freshness: F fresh / A ageing / S stale
                               (rates — conversation, call, engagement, watchlist
                               conversion, touch-of-first-reply — quarterly only,
                               gated at 40 completed sequences)

### Outcomes recorded (via /outcome)

  N outcomes attributed this week:
    [list with attribution to surfaced item or task]

### Operational

  Source coverage:             N of M sources active in last 7 days
  Stale sources:               [list]
  Median content item age:     X hours

  Agent health:                [N of M healthy]
  Failed runs this week:       N (vs. baseline X)
```

</dashboard_format>

**On-demand `/dashboard` slash command**: ephemeral response with the latest weekly snapshot plus a sparkline trend (text-rendered) for total spend and acceptance rate.

</higgins_dashboard>

---

## Outcomes Capture

<outcomes>

`outcomes` is the scaffolded table for KR1 measurement and metric #11 (outcome
attribution). V1 writes to it; v2+ computes against it. Canonical schema lives in
`30-memory-layer.md`; the shape:

```sql
CREATE TABLE outcomes (
    id                      BIGSERIAL PRIMARY KEY,
    outcome_type            TEXT NOT NULL,
                            -- 'discovery_call_booked', 'proposal_sent',
                            -- 'engagement_signed', 'engagement_renewed',
                            -- 'maintenance_converted', 'newsletter_published',
                            -- 'roundtable_topic_used', 'partnership_explored'
    outcome_value           NUMERIC,        -- nullable; $ where applicable
    description             TEXT NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    attributed_prospect_id  BIGINT REFERENCES prospects(id),
    attributed_content_id   BIGINT REFERENCES content_items(id),
    attributed_task_id      BIGINT REFERENCES tasks(id),
    attributed_signal_id    BIGINT REFERENCES icp_signals(id)
    -- no fact link: the facts table was retired in the cognee pivot and the
    -- operator chose not to link outcomes to graph fact-nodes (migration 0006)
);

CREATE INDEX outcomes_type_time_idx ON outcomes (outcome_type, recorded_at DESC);
```

**Capture mechanism**: Discord slash command `/outcome` opens a modal:

```
Type: [dropdown choice on the command]
Description: [text, required]
Value $ (optional): [number]
```

Submitting writes a row. The optional fact-link was removed in the pivot (graph
facts are auto-extracted nodes, not pickable ids). No automation backfills this
table — it's discipline. Higgins reports the count and surfaces attributions
weekly.

</outcomes>

---

## What This Layer Does NOT Do

<non_goals>

- **No real-time cost dashboard**. Higgins is weekly; Ted is 6-hourly. There is no minute-by-minute meter. The guards are the real-time safety; the dashboard is reflection.
- **No web UI**. Discord remains the sole surface, including for `#dashboard` and `/outcome`.
- **No external monitoring**. No Datadog, no Grafana, no external alerts. Everything is in-system.
- **No prediction**. The dashboard reports; it doesn't forecast.
- **No automated optimization**. If an agent is inefficient, the operator reads the dashboard and decides. Auto-tuning is a v3 concern at earliest.

</non_goals>

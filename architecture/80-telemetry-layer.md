# Telemetry Layer

<doc:layer>implementation — fourth architectural layer</doc:layer>
<doc:stability>medium — evolves as agents are added and metrics tune</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md, 40-action-layer.md</doc:depends_on>
<doc:referenced_by>70-build-order.md, 90-workflows.md</doc:referenced_by>

## Purpose

This file defines the telemetry layer: how the system measures itself, prevents runaway token spend, and reports against the north star. It sits alongside channel, action, and memory as the fourth layer — observability for the AFC Richmond agent swarm.

The layer has four components:

1. The `agent_runs` ledger — every LLM call recorded with cost
2. The cost-emission helper — single source of truth for ledger writes
3. Three runaway-prevention guards — per-run cap, per-day ceiling, anomaly detection
4. Two reporting agents — Ted (reactive, 6-hourly) and Higgins (reflective, weekly)

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

Every LLM call from every agent writes one row. This single table feeds spend metrics, cost-per-output calculations, token-discipline tracking, and anomaly detection.

```sql
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,        -- 'tartt', 'keeley_strategy', etc.
    function_label  TEXT NOT NULL,        -- 'news_aggregation', 'topic_research',
                                          -- 'action_surfacing', 'customer_discovery',
                                          -- 'infrastructure', 'telemetry'
    trigger_kind    TEXT NOT NULL,        -- 'scheduled', 'event', 'manual'
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL,        -- 'success', 'partial', 'failed', 'token_cap_exceeded'
    llm_provider    TEXT,                 -- 'gemini', 'anthropic', null for non-LLM agents
    llm_model       TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    usd_cost        NUMERIC(10,4),
    correlation_id  TEXT,                 -- e.g. content_item_id, prospect_id
    correlation_kind TEXT,                -- 'content_item', 'prospect', 'transcript', etc.
    error_text      TEXT
);

CREATE INDEX agent_runs_agent_time_idx ON agent_runs (agent_name, started_at DESC);
CREATE INDEX agent_runs_status_idx     ON agent_runs (status) WHERE status != 'success';
CREATE INDEX agent_runs_function_idx   ON agent_runs (function_label, started_at DESC);
```

**Function labels** match the four core swarm functions plus two for system work:

| Label | Agents |
|-------|--------|
| `news_aggregation` | Tartt |
| `topic_research` | Keeley Strategy, Nate Shelley |
| `action_surfacing` | Briefing, Task extractors, Meeting processor |
| `customer_discovery` | Roy Kent, Nate Shelley (also contributes), inbound webhooks |
| `infrastructure` | Sam, Ted, Fact extraction, Discord bot fact extraction |
| `telemetry` | Higgins, Ted's anomaly detection |

Some agents (Nate Shelley) serve more than one function. Each run records the function label active for that specific run.

</agent_runs_table>

---

## The Cost-Emission Helper

<cost_helper>

Every agent invokes LLMs through a single Python helper. No agent computes its own cost reporting. No agent calls an LLM SDK directly. This is enforced by convention and by code review at the git-gate.

<helper_interface>

```python
# ~/agents/_lib/runs.py

from contextlib import contextmanager
from typing import Optional, Iterator
import time

@contextmanager
def agent_run(
    agent_name: str,
    function_label: str,
    *,
    trigger_kind: str = "scheduled",
    correlation_id: Optional[str] = None,
    correlation_kind: Optional[str] = None,
) -> Iterator["RunContext"]:
    """
    Context manager that records an agent_run row.
    On entry: checks per-day spend ceiling; refuses if exceeded.
    On exit: writes the row with cost, status, errors.
    LLM calls within the block go through the RunContext methods
    so token caps and cost capture are enforced.
    """
    # ... see implementation below


class RunContext:
    def call_gemini(self, prompt: str, *, model: str,
                    max_input_tokens: int, max_output_tokens: int) -> str:
        """Calls Gemini with enforced caps. Returns response text.
           Raises TokenCapExceeded if caps would be violated."""

    def call_anthropic(self, messages: list, *, model: str,
                       max_input_tokens: int, max_output_tokens: int) -> str:
        """Calls Anthropic with enforced caps. Returns response text."""

    def call_embedding(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Calls embedding provider. Tracks token cost. No output cap (embeddings are bounded)."""
```

</helper_interface>

<helper_usage_example>

```python
# In ~/agents/tartt/run.py
from _lib.runs import agent_run

def summarize_item(item_text, item_id):
    with agent_run("tartt", "news_aggregation",
                   correlation_id=str(item_id),
                   correlation_kind="content_item") as run:
        summary = run.call_gemini(
            prompt=build_summary_prompt(item_text),
            model="gemini-2.5-flash",
            max_input_tokens=4000,
            max_output_tokens=500,
        )
        return summary
```

</helper_usage_example>

<helper_behaviors>

The helper handles four responsibilities, in order:

1. **Pre-call check**: queries `SUM(usd_cost) FROM agent_runs WHERE agent_name = ? AND started_at >= today_start()`. If sum exceeds the agent's daily ceiling, raises `DailyCeilingExceeded` and writes no agent_runs row.

2. **Token cap enforcement**: passes `max_tokens` parameters directly to the provider API. Estimates input token count before the call; if it exceeds `max_input_tokens`, raises `TokenCapExceeded` and writes a `token_cap_exceeded` row.

3. **Call execution**: makes the actual API call. Catches provider errors (timeouts, 5xx, rate limits) and writes an appropriate status to agent_runs.

4. **Cost computation**: on successful response, computes `usd_cost` from token counts using a price table baked into the helper. Writes the row.

</helper_behaviors>

<price_table_versioning>

The price table is a constant in the helper module — not a database table. Rationale: prices change rarely (quarterly at most); changes are deliberate and should go through git review; no agent should be able to inflate or deflate its reported cost dynamically.

Example structure:

```python
PRICE_TABLE = {
    ("gemini", "gemini-2.5-flash"): {"input": 0.075/1_000_000, "output": 0.30/1_000_000},
    ("gemini", "text-embedding-004"): {"input": 0.0/1_000_000, "output": 0.0},  # free tier
    ("anthropic", "claude-sonnet-4-5"): {"input": 3.0/1_000_000, "output": 15.0/1_000_000},
    ("anthropic", "claude-haiku-4-5"): {"input": 1.0/1_000_000, "output": 5.0/1_000_000},
}
```

Update via PR with the changelog entry; the helper version number bumps with each price table change.

</price_table_versioning>

</cost_helper>

---

## Three Runaway-Prevention Guards

<guards>

The cost-emission helper enforces all three. They layer to prevent spirals at different time scales: a single bad call (G1), a stuck agent (G2), and gradual prompt regression (G3).

<guard id="G1" name="Per-run token cap">

**What it does**: Aborts any single LLM call whose input or output would exceed declared caps.

**Where it lives**: Inside `RunContext.call_*` methods. The cap values are required parameters — no caller can call an LLM without specifying them.

**Default caps by call type** (starting points; tune from agent_runs data):

| Agent | Model | Max input tokens | Max output tokens |
|-------|-------|------------------|---------------------|
| Tartt summarize | Gemini Flash | 4,000 | 500 |
| Tartt embed | Gemini text-embedding-004 | 2,000 | n/a |
| Roy Kent qualify | Claude Haiku | 3,000 | 600 |
| Keeley Strategy triage | Claude Sonnet | 8,000 | 1,000 |
| Keeley Content draft | Claude Sonnet | 16,000 | 2,000 |
| Sam evaluation | Claude Haiku | 6,000 | 800 |
| Nate Shelley cluster | Claude Sonnet | 20,000 | 2,000 |
| Briefing synthesis | Claude Sonnet | 32,000 | 3,000 |
| Higgins dashboard | Claude Sonnet | 16,000 | 2,000 |
| Ted alert summary | Claude Haiku | 4,000 | 500 |
| Fact extraction | Claude Haiku | 4,000 | 600 |
| Meeting processor | Claude Haiku | 32,000 | 3,000 |

**Failure mode**: `TokenCapExceeded` raised; agent_runs row written with `status='token_cap_exceeded'` and `error_text` describing which cap was violated; #system alert emitted by Ted on next health check.

</guard>

<guard id="G2" name="Per-day spend ceiling">

**What it does**: Refuses any LLM call that would cause an agent's running daily spend to exceed its ceiling.

**Where it lives**: The `agent_run` context manager checks the ceiling on entry.

**Starting ceilings**:

| Agent | Daily ceiling |
|-------|---------------|
| Tartt | $5.00 |
| Keeley Strategy + Content combined | $3.00 |
| Sam | $1.00 |
| Briefing | $0.50 |
| Ted | $0.20 |
| Roy Kent | $1.00 |
| Nate Shelley | $0.50/week (so ~$0.07/day) |
| Meeting processor | $1.00/transcript (capped at $3/day) |
| Fact extraction | $2.00 |
| Higgins | $0.30/week (so ~$0.04/day) |
| **Total daily blast radius** | **~$15** |
| Steady-state expected | $3–7/day |

**Why combined for Keeley**: Strategy and Content fire sequentially per content item. Splitting their ceilings creates an artificial cut point where Strategy can budget-out before Content runs. Combined ceiling lets the pipeline allocate naturally.

**Failure mode**: `DailyCeilingExceeded` raised. The agent's specific behavior on this exception is its choice:
- Tartt skips the item, logs, continues with the next
- Keeley Strategy defers the item to tomorrow's run
- Briefing falls back to template-mode (no LLM synthesis, just structured query results)
- Sam refuses to evaluate; draft sits at `drafted` and surfaces in #system as a backlog warning

Ted alerts when any agent crosses 80% of its daily ceiling so you can intervene before service degrades.

</guard>

<guard id="G3" name="Anomaly detection on tokens-per-output">

**What it does**: Catches gradual efficiency regressions — an agent that's still under its caps but using meaningfully more tokens per output than its historical baseline.

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

</guards>

---

## Three Metrics Per Agent

<metrics_per_agent>

Every agent gets at most three metrics. One is token-discipline (catches spirals retrospectively, complements the guards). One is effectiveness (is the agent doing its job well). One is outcome (does the agent's work feed into KRs).

| Agent | Token-discipline | Effectiveness | Outcome |
|-------|------------------|---------------|---------|
| Tartt | Tokens per content_item | Acceptance rate (kept vs. dismissed) | Items contributing to icp_signals or content_pipeline |
| Roy Kent | Tokens per prospect qualified | High-fit % of inbound | Prospects → discovery calls converted |
| Nate Shelley | Tokens per cluster surfaced | Cluster reuse rate (referenced in content/outreach) | Signal density (distinct sources per cluster) |
| Keeley Strategy | Tokens per triage | Triage → published conversion | Decline rate (calibration: too high or too low both bad) |
| Keeley Content | Tokens per draft | Sam pass rate on first try | Approved → published conversion |
| Sam | Tokens per evaluation | Median re-draft cycles | Human approval rate on Sam-passed drafts |
| Keeley Distribution | API calls per post | Posts scheduled successfully | Posts → outcomes attributed |
| Briefing | Tokens per briefing | Tasks accepted from briefing | Decisions logged per briefing read |
| Task extractors | Tokens per candidate | Acceptance rate (Task Tinder ✅) | Accepted → completed conversion |
| Meeting processor | Tokens per transcript | Follow-ups generated per transcript | Follow-ups → completed conversion |
| Fact extraction | Tokens per fact | Hybrid search hit rate on extracted facts | (infrastructure — no outcome metric) |
| Ted | Tokens per check cycle | Mean time to alert on failure | Alert precision (true vs. false positives) |
| Higgins | Tokens per weekly digest | Weeks with KR movement reported | Outcomes recorded per week |

</metrics_per_agent>

---

## Ted's Telemetry Responsibilities

<ted_telemetry>

Ted's scope expands from pure health monitoring to include real-time cost guarding. Specifically:

Every 6 hours, Ted:

1. **Reads dashboard** (existing): timestamps of last successful runs per agent.
2. **Computes anomaly detection** (new, G3): rolling-median check on tokens-per-output for each agent. Pure SQL plus Python; zero LLM cost.
3. **Checks ceiling proximity** (new): for each agent, computes today's spend; alerts at 80% of daily ceiling.
4. **Counts failures** (new): agents with >3 `failed` or `token_cap_exceeded` runs in last 6 hours get flagged.
5. **Posts/updates pinned status in #system** (existing): single message showing all agent health states.

Ted does call Claude Haiku for its alert summarization (deciding how to phrase a complex alert) but only when there's something to alert about. Most 6-hour checks are pure Python with no LLM call and no `agent_runs` row.

When Ted does call Haiku, it logs to agent_runs with `function_label='telemetry'` to keep its own spend visible.

</ted_telemetry>

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
    topic_research:        $XX.XX  (Keeley Strategy, Nate Shelley)
    action_surfacing:      $XX.XX  (Briefing, Task extractors, Meeting processor)
    customer_discovery:    $XX.XX  (Roy Kent, Nate Shelley, webhooks)
    infrastructure:        $XX.XX  (Sam, Ted, fact extraction)
    telemetry:             $XX.XX  (Higgins, Ted alerting)
    total:                 $XX.XX

  Token discipline flags this week:
    [list of agents that hit G3 anomaly threshold, with deviation]
    [list of agents that crossed 80% daily ceiling, with frequency]
    [list of token_cap_exceeded events with context]

### Workflow throughput

  W1 inbound prospects:        N qualified, M high-fit
  W2 ICP signal clusters:      N surfaced this week, top theme: [theme]
  W3 content pipeline:         N drafted, M approved, P published
  W4 discovery calls:          N processed, M follow-ups extracted
  W5 daily briefings:          7 of 7 posted
  W6 captures:                 N facts captured to brain
  W7 (this dashboard):         posted

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

`outcomes` is the scaffolded table for KR1 measurement and metric #11 (outcome attribution). V1 writes to it; v2+ computes against it.

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
    attributed_fact_id      BIGINT REFERENCES facts(id),
    attributed_signal_id    BIGINT REFERENCES icp_signals(id)
);

CREATE INDEX outcomes_type_time_idx ON outcomes (outcome_type, recorded_at DESC);
```

**Capture mechanism**: Discord slash command `/outcome` opens a modal:

```
Type: [dropdown]
Description: [text]
Value $ (optional): [number]
Linked to (optional): [recent surfaced item / prospect / task]
```

Submitting writes a row. No automation backfills this table — it's discipline. Higgins reports the count and surfaces attributions weekly.

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

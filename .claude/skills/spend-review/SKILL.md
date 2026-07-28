---
name: spend-review
description: Summarize agent_runs spend by agent, function, and day, and flag anything near a daily ceiling or anomalous. Use when reviewing LLM cost, checking the budget, or preparing a weekly digest.
---

# Spend review

Reviews the `agent_runs` telemetry ledger and reports where the money went.

## Steps

1. Pull the aggregates via the spend CLI:
   ```bash
   uv run python -m cli.spend --since 7d --by agent
   uv run python -m cli.spend --since 7d --by function
   uv run python -m cli.spend --since 7d --by day
   ```
2. Compare each agent's spend to its `DAILY_CEILINGS` entry (in
   `agents/_lib/runs.py`). Flag any agent over ~80% of its ceiling on any day.
3. Flag anomalies: a day whose spend is more than ~2× the trailing median, or an
   agent whose failures (`status != 'success'`) exceed a handful in the window.
4. Report as three short sections — by agent, by function, notable flags — with
   totals and any ceiling/anomaly warnings called out first.

## Notes

- Read-only. This skill never writes to the ledger or changes ceilings.
- After the cognee migration (Phase 3.7), cognee's calls also land in
  `agent_runs` via the telemetry labeling (mitigation M1), so this skill keeps
  working unchanged — it reads the table, not the caller.

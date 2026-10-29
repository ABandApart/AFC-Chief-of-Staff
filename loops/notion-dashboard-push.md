---
name: notion-dashboard-push
schedule: "0 7 * * 1"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.notion.dashboard_push
description: Score every active KR, snapshot the week, push the Notion dashboard, and post the #dashboard summary (PRD-notion-integration.md N4).
---

# Notion dashboard push (N4)

Runs `agents/notion/dashboard_push.py` every Monday at 07:00 for the week that
just ended (Monday 00:00 to Monday 00:00). Each run:

1. builds the quarter's dashboard page if it is missing (quarter rollover);
2. scores every active KR through `agents/_lib/kr_metrics.py`;
3. upserts one `okr_snapshots` row per KR for that week;
4. pushes the KR Scorecard, Initiative Status, and Weekly Snapshots rows, the
   header, and the 5 callouts, skipping anything unchanged by its hash;
5. posts the 5-line summary to #dashboard on the week's first run only.

A re-run in the same week overwrites that week's snapshot rows and changes
nothing else.

**What activation starts:** nothing billable. About 20 SQL queries, a page read
per database, and at most about 40 Notion writes a week, plus one Discord post
and one `agent_runs` row (`agent_name='notion-sync'`, `usd_cost=0`). It ships
`enabled: false` per `loops/README.md`.

Health check any time:

```
uv run python -m agents.notion.dashboard_push --dry-run   # every KR's score and the summary
psql -U barry_agent aiadaptive_cos -c "SELECT week_start, count(*) FROM okr_snapshots GROUP BY 1 ORDER BY 1 DESC LIMIT 3;"
```

The newest `week_start` should be last Monday's week while the loop is enabled.

Trust: the dashboard and the summary carry counts only, never a company name
(N-S8). `tests/test_dashboard.py` pins it.

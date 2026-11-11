---
name: notion-plan-daily
schedule: "0 6 * * 1-5"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.notion.plan_daily
description: Carry forward unfinished plan tasks, create today's, set the dashboard's Daily plan view, and post the #tasks summary (PRD-notion-integration.md N7).
---

# Notion daily plan (N7)

Runs `agents/notion/plan_daily.py` at 06:00 Monday to Friday. On a day of the
active plan (imported with `cli.notion_plan_import`), each run:

1. closes unfinished standing tasks from earlier days as `skipped` and moves every
   other unfinished plan task's due date to today;
2. creates today's plan tasks, which the `notion-todo-sync` loop pushes to the
   To-Do database within 5 minutes;
3. posts a 3-line summary to #tasks on the run that created today's tasks;
4. adds the "📋 Daily plan" view to the quarter's dashboard if it's missing and
   sets its Due window to the last five plan days.

On any other day it does nothing. A re-run the same day creates nothing, carries
nothing, and doesn't post again.

**What activation starts:** nothing billable. A few SQL queries, about 5 Notion
requests a day, one Discord post, and one `agent_runs` row
(`agent_name='notion-sync'`, `function_label='plan_daily'`, `usd_cost=0`). It
shipped `enabled: false` per `loops/README.md` and was enabled 2026-11-11 after
barry-agent's live setup and import (N7 E2) and the operator's go-ahead.

Health check any time:

```
uv run python -m agents.notion.plan_daily --dry-run
psql -U barry_agent aiadaptive_cos -c "SELECT day, count(*), count(task_id) FROM plan_items GROUP BY 1 ORDER BY 1"
```

On a plan day after 06:00, today's row should show every item with a task.

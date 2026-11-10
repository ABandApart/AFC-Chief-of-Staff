---
name: notion-todo-sync
schedule: "*/5 * * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.notion.todo_sync
description: Sync tasks with the Notion To-Do database, where Notion owns Task, Due, Initiative, and Done, and import rows added there (PRD-notion-integration.md N3, N-S17).
---

# Notion To-Do sync (N3)

Runs `agents/notion/todo_sync.py` every 5 minutes. **Notion is the source of
truth for each row's Task, Due, Initiative, and Done** (N-S17). Each cycle merges
those fields one by one against the value both sides last agreed on: a Notion edit
is copied into Postgres and never overwritten, and a Discord change reaches Notion
only where Notion didn't change that field. The CoS writes Source, Created, and
CoS id. A new task gets a full row (neutral title for prospect sources, N-D9), and
a row added by hand in Notion becomes a task.

The PRD schedules the push every 5 minutes and the Done pull every 15. One job
does both every 5 minutes: the Done merge needs the same read of the To-Do
database that the push does, and the shorter cycle meets both outcome times
(S1.3). The PRD's immediate push on accept is not built either. The bot makes no
Notion calls; an accepted card reaches Notion on the next cycle, within 5 minutes.

**What activation starts:** nothing billable. One database lookup and one paged
query per cycle, plus a write only for rows that changed (well under the rate
limit), task and follow-up status changes when Done moves in Notion, and one
`agent_runs` row per cycle (`agent_name='notion-sync'`, `usd_cost=0`).
It shipped `enabled: false` per `loops/README.md`. The first run was also the
backfill of the tasks accepted before N3.

**ACTIVATED 2026-10-28** (operator go-ahead, H1 in `PHASE-NOTION-N3.md`). Flipped
after barry-agent created the To-Do database and backfilled 16 rows, with an
idempotent re-run.

Health check any time:

```
uv run python -m agents.notion.todo_sync --dry-run   # each task's Done merge and row write
psql -U barry_agent aiadaptive_cos -c "SELECT max(last_pulled_at) FROM notion_sync WHERE entity_table = 'tasks';"
```

`last_pulled_at` should never fall more than 5 minutes behind while the loop is
enabled.

Trust: task titles from outreach and inbound sources never leave Postgres
(N-D9; `tests/test_todo_sync.py` pins it). The Done box is the only value read
back from Notion.

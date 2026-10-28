---
name: notion-okr-pull
schedule: "*/15 * * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.notion.okr_pull
description: Pull the Notion master OKR databases into okr_objectives, okr_key_results, okr_initiatives (PRD-notion-integration.md N2).
---

# Notion OKR pull (N2)

Runs `agents/notion/okr_pull.py` every 15 minutes. It reads every page of the
Objectives, Key Results, and Initiatives databases under "OKRs — Master" and
refreshes the Postgres cache. Notion owns the OKRs (N-S1): this loop never writes
to Notion.

The PRD's Components table names one `loops/notion-sync.md` for every Notion
schedule. The loader allows one target per manifest, so each Notion job gets its
own file, `notion-<job>.md`. N3 and N4 add theirs beside this one.

**What activation starts:** nothing billable. Notion reads (3 database lookups and
3 paged queries per cycle, well under the rate limit), writes to the three
`okr_*` tables, one `agent_runs` row per cycle (`agent_name='notion-sync'`,
`usd_cost=0`), and at most one #system notice per malformed page per day.
It shipped `enabled: false` per `loops/README.md`.

**ACTIVATED 2026-10-28** (operator go-ahead, H2 in `PHASE-NOTION-N2.md`). Flipped
after barry-agent's live checks passed: a clean dry run, a real run that cached 4
objectives and 16 KRs, an idempotent re-run, and the add, fix, and delete check on
a test initiative, including its #system notice.

Health check any time:

```
uv run python -m agents.notion.okr_pull --dry-run    # parsed pages and problems; writes nothing
psql aiadaptive_cos -c "SELECT max(pulled_at) FROM okr_key_results;"
```

`pulled_at` should never fall more than 15 minutes behind while the loop is
enabled. A row with a non-NULL `problem` is a malformed page in Notion; consumers
ignore it until the operator fixes the page.

Trust: Notion page content is operator-authored, but it is still read as data
(B1). The pull stores names, selects, and numbers only, never page bodies.

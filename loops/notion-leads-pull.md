---
name: notion-leads-pull
schedule: "30 5 * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.notion.leads_sync pull
description: Take Leads edits into Postgres, start the sequences scheduled with Start on, and create today's outreach tasks (PRD-notion-integration.md N8).
---

# Notion Leads pull (N8)

Runs `agents/notion/leads_sync.py pull` daily at 05:30, before `outreach-daily`
(05:45) builds packets and `outreach-gmail-draft` (06:00) drafts them, so a
sequence started this morning has its first message the same day. Each run:

1. takes the operator's Leads edits: Status (Replied, Call booked, Client, Not a
   fit, Opted out), Start on, and, for candidates, Stage and Function state. A
   Status the CoS sets itself (New, Contacted, Parked) is refused and reported;
2. skips the unsent touches of a sequence the operator ended;
3. starts each candidate whose Start on has come, if it has a Function state and
   capacity has room. A candidate whose trigger is older than slot 1's window is
   re-anchored on today first;
4. creates a To-Do task for each outreach touch whose window is open;
5. posts to #tasks only when something needs the operator.

**What activation starts:** nothing billable. A few SQL queries, one Leads query,
and one `agent_runs` row (`agent_name='notion-sync'`, `function_label='leads_pull'`,
`usd_cost=0`). Ships `enabled: false` per `loops/README.md`; enable after
migration 0038, the Leads columns, and barry-agent's first push (`PHASE-NOTION-N8.md`).

Health check any time:

```
uv run python -m agents.notion.leads_sync pull --dry-run
```

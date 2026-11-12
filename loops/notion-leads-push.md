---
name: notion-leads-push
schedule: "20 6 * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.notion.leads_sync push
description: Mirror every outreach target to the Notion Leads database (PRD-notion-integration.md N8).
---

# Notion Leads push (N8)

Runs `agents/notion/leads_sync.py push` daily at 06:20, after `outreach-daily`
and `outreach-gmail-draft`, so each row's Message is this morning's packet. It
writes one Leads row per `outreach_targets` row with Channel = Outreach, and
skips rows whose CoS-written columns are unchanged. It never writes Notes or Next
step and never touches a Docent row. Edits made in Leads since the 05:30 pull are
taken first.

**What activation starts:** nothing billable. About 1 Notion request per changed
row (44 rows on 2026-11-12; the first run creates them all) and one `agent_runs`
row (`function_label='leads_push'`). Shipped `enabled: false`; enabled 2026-11-12
on the operator's go-ahead.

Health check any time:

```
uv run python -m agents.notion.leads_sync push --dry-run
```

---
name: notion-inputs-sync
schedule: "15 5 * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.notion.inputs_sync
description: Task Tinder cards in To-Do, plus the Outcomes, Capture, and Sources databases (PRD-notion-integration.md N9).
---

# Notion inputs sync (N9)

Runs `agents/notion/inputs_sync.py` daily at 05:15. Each run:

1. **Task Tinder cards in To-Do (I1).** Each undecided candidate is a To-Do row
   with a Card id and no CoS id. Decision = Accept turns that same row into the
   task; Decline trashes it; Defer keeps it until Defer until, when it comes back.
   The 5-minute `notion-todo-sync` never imports a card row as a task.
2. **Outcomes, Capture, and Sources (I5).** New Outcome rows become `outcomes`,
   new Capture rows are ingested, and Sources edits change feeds and interests.

Refusals and failures go to the day's Briefing page. An edit by anyone but the
operator is refused and raised as an urgent alert (a Notion comment mention).

**What activation starts:** no LLM call from this job itself. Capture ingest
uses cognee, as #capture does today. One `agent_runs` row per run
(`function_label='inputs_sync'`). Ships `enabled: false` per `loops/README.md`;
enable after barry-admin adds the To-Do card columns, creates the Outcomes,
Capture, Sources, and Briefings databases, and records their ids
(`PHASE-NOTION-N9.md`).

Health check any time:

```
uv run python -m agents.notion.inputs_sync --manual --date <yyyy-mm-dd>
```

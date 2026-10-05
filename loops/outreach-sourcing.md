---
name: outreach-sourcing
schedule: "0 5 * * 1-5"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach_sourcing.run
description: Research agent that surfaces at least 10 approvable outreach candidates each weekday (PRD-outreach-autonomous-sourcing).
---

# Autonomous outreach sourcing

Runs `agents/outreach_sourcing/run.py` at **05:00 on weekdays**. It completes the
verification of stuck pool rows first, then plans new-firm research with
`claude-opus-5-5` and runs `claude-sonnet-5-5` research workers in parallel, each
with Claude web search and fetch. Every dossier is checked by code before it is
stored; the Gate 0 cog surfaces what passed in `#outreach` from 05:30.

**Spends.** Outreach group ceiling $20.00/day (with `outreach-discover` and
`trent-crimm`); system ceiling $25.00/day. Every model call and web search is in
`agent_runs`, correlated by run id. Yesterday's spend appears in the 06:00 briefing.

**Stops** when N candidates pass (§6.4), the budget is spent, or 07:00 arrives,
and posts one line to `#outreach` if it fell short.

**SHIPS DISABLED.** Turn it on only after:
1. migration `0029_outreach_autonomous_sourcing.sql` is applied;
2. V6, the worker quality probe: a `--dry-run` on the runtime box, reviewed by the
   operator for accuracy and cost per candidate.

Nothing here sends mail (B2, G3).

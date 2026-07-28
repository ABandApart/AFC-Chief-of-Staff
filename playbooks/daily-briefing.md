---
name: daily-briefing
description: What the morning briefing assembles and how it is prioritized.
applies_to: [briefing]
publish_to_memory: false
tags: [briefing, cadence]
---

# Daily briefing

Run by the `morning-briefing` loop at 6:00 local. Produces the good-morning
digest in #briefing.

## Assemble (each section is a bounded query, newest/highest-priority first)

1. **Needs a decision today** — follow-ups at escalation ≥ 1; overdue commitments.
2. **New since yesterday** — prospects (new), meetings processed, high-interest
   content items (top 3 by interest score).
3. **Signal of the week** — the top ICP theme (Nate Shelley), if fresh.
4. **System** — spend vs. ceilings, any agent failures, backup status.

## Rules

- Bound every section (top-N with an explicit order); the briefing's cost must
  not grow with the database. Aggregate in SQL; spend LLM tokens only on the
  narrative.
- If a section is empty, omit it rather than padding.
- Pre-3.7 the briefing posts a static status; this playbook is the target once
  it synthesizes over the graph.

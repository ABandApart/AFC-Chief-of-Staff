---
name: tartt-poll
schedule: "0 */6 * * *"
trigger_kind: scheduled
enabled: true
agent: tartt
description: Poll content sources and turn new items into graph knowledge + reading recs + task candidates (Phase 4).
---

# Tartt content-discovery poll (Phase 4)

Runs `agents/tartt/run.py` on a slow cadence: selects `active` sources whose
per-row watermark (`sources.last_polled_at` + `poll_interval_hours`) says they're
due, and processes each — fetch → summarize (Gemini Flash) → interest-gate →
typed `ContentItem` into the cognee graph (local bge embed) → interest score →
reading recs in the briefing + `task_candidates` for Task Tinder (Phase 5).

**Ships DISABLED** (`enabled: false`). Activate only after: (1) the source seed
is curated, (2) the Task 2–5 pipeline is built and smoked, and (3) a manual
`uv run python -m agents.tartt.run --dry-run` looks right. The `0 */6 * * *`
schedule is a starting point — actual cadence is per-source (`poll_interval_hours`,
seeded at 12h) to keep the Gemini free-tier **quality** trial low-volume.

Trust: content is untrusted ingest → **B1** (the text is data, never
instructions); Tartt *proposes* task candidates, it never acts (no B2 crossing).

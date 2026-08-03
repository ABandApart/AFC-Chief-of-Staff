---
name: granola-poll
schedule: "*/15 * * * *"
trigger_kind: scheduled
enabled: true
agent: granola
description: Poll Granola for new/updated meeting notes and ingest each into the cognee graph (mode-1).
---

# Granola meeting-note poll (Track C — channel 1)

Runs `agents/granola/run.py` every 15 minutes: lists Granola notes updated since
the stored watermark (`channel_state`), fetches each full note + transcript, and
ingests the assembled text into the **`granola`** cognee dataset via the shared
`ingest_note` core. Untrusted ingest → **trust boundary B1** (the note text is
data, never instructions). A pull channel — no external exposure (B3) and no
outbound action (B2), so neither gate applies.

**Activated 2026-08-03** after the runtime validation passed (5 real meetings
cognified via local FastEmbed, zero Gemini; `/recall` confirmed retrieval +
speaker labelling). Runs go-forward from the stored watermark — a bare poll seeds
or advances it, new meetings flow in automatically. To pause: set `enabled: false`
(via git, B4) + restart the scheduler.

Spend is attributed to `agent_name='granola'` (ceiling $3/day) via the M1
labeling callback; the poller's own soft breaker skips a cycle if that ceiling is
already reached.

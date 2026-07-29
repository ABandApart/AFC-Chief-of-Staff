---
name: granola-poll
schedule: "*/15 * * * *"
trigger_kind: scheduled
enabled: false
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

**Ships disabled.** Activation sequence (respecting B4 — control-plane changes
come through git):

1. barry-agent provisions the `granola-api-key` keychain item (operator mints a
   personal API key in the Granola desktop app → Settings → Connectors → API keys;
   verify the plan tier includes transcripts).
2. barry-agent runs one manual poll and confirms it's green:
   `uv run python -m agents.granola.run`.
3. barry-admin flips `enabled: true` here, commits, and barry-agent pulls +
   restarts the scheduler (it reads loop manifests once at startup).

Spend is attributed to `agent_name='granola'` (ceiling $3/day) via the M1
labeling callback; the poller's own soft breaker skips a cycle if that ceiling is
already reached.

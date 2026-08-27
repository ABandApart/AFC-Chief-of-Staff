---
name: outreach-profile
schedule: "30 6 * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach.profile
description: Observe company news into the unclassified queue and the graph (Track O, Part 1).
---

# Outreach news observation (Track O, Part 1)

Runs `agents/outreach/profile.py` at **06:30**, after the briefing and the
discovery run, so the queue it fills is read by the weekly classifier (Part 2),
not on the same tick.

For each profilable firm — an active target or an **accepted discovery** (R1.9) —
it reads Google News and any stored newsroom feed, writes each new story once
into `outreach_watch_signals` (unclassified), and attaches it to the firm's
Organization node in the graph.

**Why the pool is profiled, not just targets.** An accepted discovery has no
trigger, and the only way it gets one is Part 2 classifying a signal about it
(R0.3). So observing the pool is what eventually promotes it — the whole reason
this loop exists ahead of a first send.

**No LLM.** Deterministic GET + dedup + typed-node writes: no `agent_runs`, no
ceiling, no provider-outage failure mode.

**Ships DISABLED.** Two reasons beyond convention: it needs cognee (barry-agent
only — the build box writes the SQL queue but not the graph), and it should be
flipped once the operator has accepted a meaningful number of pool rows so there
is something to watch. Health check by hand:

```
uv run python -m agents.outreach.profile --dry-run              # firms + feeds, writes nothing
uv run python -m agents.outreach.profile --no-graph             # SQL queue only (build box)
uv run python -m agents.outreach.profile                        # both (barry-agent)
```

## What it deliberately does not do

**No classification.** `classified_as` stays NULL — that is Part 2's job. A
keyword classifier here would be a second, worse Trent Crimm.

**No `outreach_evidence` writes.** An unclassified headline is not a typed dated
fact and must not act like one — it never touches scoring, arithmetic, or the
`ready` guard.

**No article fetch, no summary.** Feed metadata only. Fetching the body
(trafilatura) and summarising it (Gemini Flash) is Part 2, gated on the story
being classified as mattering — that is where the deferred spend lands.

**Never re-dates a signal.** A news item is written once. Advancing a last-seen
date on it — the evidence poller's correct behaviour for an open req — would make
an old article read as fresh forever (R19 through a side door), so this uses
insert-or-skip, not upsert.

Trust: reads public news feeds and writes the queue and the graph. It sends
nothing and proposes nothing to a third party, so it never approaches **B2**.
LinkedIn is never fetched (R14).

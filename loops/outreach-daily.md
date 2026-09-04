---
name: outreach-daily
schedule: "45 5 * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.outreach.daily
description: Regenerate due packets, run the drain rule, report the briefing counts (Track O, 35- §14).
---

# Outreach daily (Track O)

Runs `agents/outreach/daily.py` at **05:45**, fifteen minutes before the 06:00
briefing so the counts it quotes are of packets assembled this morning rather
than yesterday's.

Three jobs:

1. **Regenerate packets** for every touch inside its window. *Regenerate, never
   edit* (`35-` §14) — the packet is rebuilt from current state, so evidence that
   aged overnight or a req that closed is reflected rather than remembered, and
   `ready` is re-derived every morning.
2. **The drain rule** (§8) — a sequence with all five touches resolved, no reply,
   and 14 days past its last window is finished. It moves to `watchlist` **only
   once `stalled_reason` is set**; until then it keeps its capacity slot, which is
   the friction that makes "what stalled it?" get answered.
3. **Report the counts** the briefing line reads — including, since Part 0,
   how many verified candidates are waiting at Gate 0. That clause reads *after*
   the Gate 1 cards: a triage queue is not a decision that ages, and the intake
   cards are the ones that actually hold up the pipeline.

**No LLM.** Deterministic queries and substitution, so no `agent_runs` rows, no
ceiling, and nothing that can fail from a provider outage
(`40-action-layer.md`, Outreach_loops).

**Ships DISABLED** per the `loops/README.md` convention. Unlike `tartt-poll`,
activation here costs nothing — no spend, no outbound, and it no-ops cleanly
while no target is `in_sequence`. It is safe to flip as soon as the first Gate 1
card is accepted; there is simply nothing for it to do before that.

```
uv run python -m agents.outreach.daily --dry-run   # reports, writes nothing
```

## What it deliberately does not do

**No calendar write-out.** §14 lists one, and §9 describes five dates written at
sequence start as a "dumb reminder, explicitly non-authoritative". There is no
calendar integration in this system yet, so there is nothing to write to.

**No automatic watch/drop banding.** `37-` D1 shows the system banding 14–19 to
watchlist and below-14 to dropped without a card, and that is genuinely what the
diagram says — but implementing it today would do harm both ways:

- **Auto-watchlisting would trap targets.** The intake poller only cards
  `status='candidate'`, and the route back out of `watchlist` is Gate 4, which
  Trent Crimm surfaces — and Trent Crimm is not built. A target banded to
  watchlist today would become invisible, including one whose score later reaches
  `work`. That is likely rather than hypothetical: **S1's bands are
  non-monotonic**, so a target sitting at 13 in the middle band returns to 15 at
  the day-60 hinge.
- **Auto-dropping acts irreversibly on an oscillating number.** Dropped targets
  stop being polled, so the evidence accrual stops — and posting age cannot be
  rebuilt. Dropping a target at 13 on day 20 that would score 15 on day 58 trades
  a permanent loss for a tidier list.

So banding waits for Gate 4. Until then the briefing line reports how many cards
are open, and the Gate 1 card's own Watchlist and Drop buttons remain the way
either decision gets made — by someone who can see the evidence.

Trust: this loop reads evidence and writes packets and target status. It sends
nothing and proposes nothing to a third party, so it never approaches **B2**. The
`ready` flag it computes is what gates a send later, and the database trigger —
not this loop — is what enforces it.

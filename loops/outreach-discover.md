---
name: outreach-discover
schedule: "0 5 * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.outreach.discover
description: Source, verify and score candidate firms into the Gate 0 pool (Track O, Part 0).
---

# Outreach discovery (Track O, Part 0)

Runs `agents/outreach/discover.py` at **05:00**, before `outreach-daily` at 05:45
so the briefing can count what landed overnight.

For each of the six segments it asks every channel for candidates, drops anything
out of geographic scope or already known, verifies what remains (R0.5), scores it
with ICP v1, and inserts the survivors into `outreach_discoveries` as
**unreviewed**. It surfaces nothing — the Gate 0 cog does that from the pool.

**One LLM path, bounded.** The core loop is deterministic, but the `news_query`
channel makes one bounded Haiku extraction call per batch (R0.21, sanctioned
2026-08-20) — so this loop DOES spend, `outreach_discovery` label, **$0.25/day**
ceiling (`35-` §14). The other channels (`seed_list`, `apollo_search`) and all of
verification/scoring are LLM-free. (This corrects the earlier "No LLM" note, which
predated the R0.21 extraction step.)

**ENABLED 2026-08-28.** It shipped disabled for a real reason beyond convention:
the seed list was empty and `news_query` alone returns few firms, so the loop
would spend and surface almost nothing. That reason is now gone — the
`apollo_search` channel (ICP-refined Apollo company search, Free tier) is a real
source, so the loop has something to do. It runs daily at 05:00; run it by hand
any time with:

```
uv run python -m agents.outreach.discover --dry-run
```

## What it deliberately does not do

**It does not fabricate to hit a number.** A run that finds three verifiable
firms inserts three and says so. The daily 25 is a review ceiling (R0.11), not a
sourcing quota, and padding the pool with unverified firms would defeat the bar
that makes the queue trustworthy.

**It does not skip a firm that fails verification** — it inserts it and lets the
two-kind minimum keep it out of the window. Recording the thin firm is what stops
the next run re-probing it forever, and leaves it eligible to surface later if
evidence improves (R0.19, upward-only).

**It does not enumerate ATS boards, and cannot.** R0.4 assumed a firm with an open
req is discoverable; no ATS provider publishes a board index, so the seven
adapters verify a firm you already have rather than finding one. The package
docstring in `agents/outreach/discovery/` sets out what each named channel can and
cannot do, and why `seed_list` is the workhorse.

Trust: reads public company sites and ATS APIs, writes only `outreach_discoveries`.
It contacts nobody and proposes nothing to a third party, so it never approaches
**B2**. It never fetches LinkedIn — R14 is Policy.

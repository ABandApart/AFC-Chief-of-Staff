---
name: outreach-scoring
description: Scoring a target on S2-S5 — the four signals you set by hand, and what the resulting treatment means.
applies_to: [outreach]
publish_to_memory: true
tags: [track-o, outreach, scoring, rubric]
---

# Scoring a target — S2 to S5

Five signals, each **1, 3, or 5**, totalling out of 25. You set four of them.

**S1 is not yours to set.** It is derived from `trigger_date` by the
`outreach_s1()` function, and **the migration is authoritative — this playbook
deliberately does not restate its bands** (R6: rubric drift between the function
and playbook prose is a real failure mode, and the version that runs wins).

| | Signal | Who sets it | Refresh |
|---|--------|-------------|---------|
| **S1** | Trigger recency | Derived — `outreach_s1()` | Continuously |
| **S2** | Stage fit | You, at intake | Rarely |
| **S3** | Sector match | You, at intake | Rarely |
| **S4** | Leadership gap | You, evidence-informed | 30 days |
| **S5** | Team build below | You, evidence-informed | 30 days |

**Score is NULL until all four are set** — a partial rubric must not read as a
low score. Nothing reaches the intake gate until you have scored it.

> **Draft — 2026-08-14.** `35-` fixes the 1/3/5 values and states one band
> outright ("S4's top band is *posted 45+ days*", §6), but does not write the
> rest down. The bands below are proposed from what the spec does say and are
> meant to be corrected against real cases. **Corrections belong here.**

## S2 — Stage fit

*Is this company at a stage where a fractional leader is the right answer?*

- **5** — squarely in the band you serve, and the stage where the problem bites.
- **3** — adjacent. Plausible, but you would be arguing the case.
- **1** — wrong stage. Too early to afford it, or big enough that they will hire
  permanently and in-house.

Set at intake from `stage`; revisit only if they raise or restructure.

## S3 — Sector match

*Do you have credible standing in their sector?*

- **5** — you have done this in their sector and can name it.
- **3** — an adjacent sector where the mechanics transfer.
- **1** — unfamiliar; you would be learning on their time.

This is the signal that gates several templates: T15 (peer company reference)
and T13 (pattern lead) both **require** you to actually hold this, and `35-` §7
names fabricating them as their documented failure mode. If S3 is 1, those
templates are off the table for this target.

## S4 — Leadership gap

*How visibly is the leadership seat empty?* Evidence-informed, evidence does not
decide it (§4).

- **5** — a leadership req **open 45+ days**, or a seat empty since a departure.
  *(This band is stated in the spec; the poller's `first_seen_at` is what proves
  the 45 days, and `cli/outreach_preview` shows the age.)*
- **3** — the gap is real but less legible: someone stretched across two
  functions, or a recently-posted leadership req without the age behind it yet.
- **1** — the seat is filled and functioning.

Follows from `function_state`: `vacant_seat` usually scores 5, `under_led` 3,
`self_covered` 1 or 3 depending on how much strain is visible.

## S5 — Team build below

*Are they hiring people **below** a leader who does not exist?*

- **5** — multiple IC hires into a function with no leader. They are building the
  team before the leader, which is the pattern the whole pitch is about.
- **3** — some hiring below, or one IC req.
- **1** — no hiring below the gap.

The evidence poller sees these as open roles; whether they sit *below* a missing
leader is your read. `35-` §2 marks `ic_hire` as the fact kind that feeds S5.

## The compound signal

**S4 = 5 and S5 = 5 together** is the ⚡ marker on the intake card. It is the
"three AEs hired and no VP Revenue on the leadership page" shape from §6 — the
company is visibly building a team beneath a seat nobody is in. It is the
strongest pattern the method recognises, and it is why S4 and S5 are scored
separately rather than as one "leadership" number.

## What the total means

| Score | Treatment | What happens |
|-------|-----------|--------------|
| **20–25** | `work` | Posts an intake card. Capacity-gated at 15 cold live. |
| **14–19** | `watch` | Parked; Trent Crimm watches for a trigger that moves it. |
| **below 14** | `drop` | Not pursued. Evidence history is kept. |

Remember S1 moves on its own. A target scoring 18 today can reach 20 as it
crosses the day-60 hinge, without you changing anything — which is the point of
the non-monotonic bands.

## Re-scoring

S4 and S5 go stale on a **30-day cadence** (§4). `v_outreach_scored.signals_stale`
flags it once `signals_observed_at` is older than 30 days, and stamping that
column is what marks a re-check as done.

## Doing it

```bash
uv run python -m cli.outreach_score --list                  # what needs scoring
uv run python -m cli.outreach_score --target 7 --show       # evidence + this rubric
uv run python -m cli.outreach_score --target 7 \
    --s2 5 --s3 3 --s4 5 --s5 5 --function-state vacant_seat
```

The CLI refuses anything but 1, 3, or 5, prints the resulting score and
treatment, and stamps `signals_observed_at` so the 30-day clock starts.

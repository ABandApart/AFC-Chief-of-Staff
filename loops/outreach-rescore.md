---
name: outreach-rescore
schedule: "0 18 * * 0"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach.rescore
description: Weekly band-change record and 30-day stale-signal re-check cards (Track O, 35- §14).
---

# Outreach re-score sweep (Track O)

> **Status: BUILT 2026-08-27, ships disabled.** `agents/outreach/rescore.py`
> exists and its deterministic core is verified on the build box — band-change
> recording and stale-signal raising. The **stale-signal MODAL surface (O2) is not
> built**: the sweep raises a `task_candidates` re-check pointing at
> `cli/outreach_score`; the bespoke S2–S5 modal is the next slice. Flip
> `enabled: true` once that surface is chosen and the first sweep is confirmed.

Runs **Sunday 18:00**, an hour before `outreach-watch` (Trent Crimm), so a band
that moved this week is recorded before the watchlist pass reads it.

## Why this loop needed a spec written first

`35-` §14 describes it in eight words — *"recompute S1, band-change events,
stale-signal cards"* — and `40-action-layer.md` repeats the same phrase twice.
That is the whole specification in the repo. Held against what is actually built,
two of the three turn out to describe work that does not exist:

- **"Recompute S1" is a no-op.** S1 is a `STABLE` function evaluated live inside
  `v_outreach_scored` (`outreach_s1(trigger_date)`), never materialised. There is
  no stored value to recompute and no staleness to correct — every read is already
  as-of-now. The phrase survives from a design where S1 was a stored column, which
  §4 itself explains Postgres will not allow (generated columns must be IMMUTABLE;
  S1 depends on `CURRENT_DATE`). **Recorded as a spec-vs-reality collision rather
  than implemented.**
- **Half of "band-change events" is already automatic.** Because `treatment` is
  derived, a `candidate` that rises into `work` is picked up by the intake cog's
  120-second poll (`list_undelivered`) and carded without anything weekly being
  involved. What is *not* covered is the record of the transition, and every
  direction that does not end in a new card.
- **"Stale-signal cards" is the one genuinely unbuilt piece.** `signals_stale` is
  already computed in `v_outreach_scored` (`signals_observed_at < CURRENT_DATE -
  30`) and **nothing anywhere reads it.** §4's "30-day cadence → Task Tinder card"
  has a detector and no consumer.

So the loop that §14 names is mostly one job, not three.

## S1 — Outcome, stated observably

When this is done, all of the following are true and checkable by someone else:

1. **Every band crossing is recorded.** For each live target whose `treatment`
   differs from its band at the previous sweep, `outreach_events` holds one row
   with `entity_table='outreach_targets'`, the target's id, and a `changed`
   payload naming the old band, the new band, the score on each side, and the
   as-of dates compared. A target whose score moved without crossing a band
   boundary produces **no** row.

   ```sql
   SELECT entity_id, changed FROM outreach_events
   WHERE entity_table = 'outreach_targets' AND changed ? 'treatment'
   ORDER BY occurred_at DESC;
   ```

2. **A stale judgement is surfaced exactly once.** A target with
   `signals_stale = true` and no open re-check has one raised; a target that
   already has one open gets no second one, on this sweep or any later sweep,
   until the first is resolved. Re-running the sweep twice in a row raises nothing
   the second time.

3. **The sweep never changes a target.** No `status`, no `s2`–`s5`, no
   `trigger_date`, no `signals_observed_at`. It records and surfaces; it does not
   band. A row-level diff of `outreach_targets` across a real sweep is empty.

4. **No LLM.** Zero `agent_runs` rows attributable to the loop, so no ceiling and
   nothing that can fail from a provider outage (`40-action-layer.md`,
   Outreach_loops).

## S2 — Non-goals

- **No automatic watch/drop banding.** `37-` D1 specifies it; `loops/outreach-daily.md`
  reasons at length about why it waits for Gate 4, and that reasoning is unchanged
  and load-bearing here: auto-watchlisting *traps* targets (intake only cards
  `candidate`, and the way back out is Trent Crimm, unbuilt), and auto-dropping
  stops evidence polling irreversibly on a number that oscillates by design.
  **This loop is the thing that would be tempted to band. It must not.**
- **No S1 recomputation or materialisation** — see above.
- **Not a card per band change.** Recording is cheap; a card costs operator
  attention. Settled at O4: only upward crossings into `work` card; everything
  else is recorded and reported as a count.
- **Never withdraws a Gate 1 intake card.** Posting belongs to the 120s poll, and
  O3 settles that a card outlives the band that raised it — the decision stays
  available and the history stays reviewable.
- **Does not judge S4/S5.** §4 is explicit that evidence *informs* and does not
  *set* them, and that the two-tab diagnostic is five minutes of human judgement.
  The card asks; `cli/outreach_score` records.

## S3 — Verification

Named before the build, not shaped to it afterwards.

**Unit** — against a fixed as-of pair, no clock dependence:

- a target crossing each boundary in each direction (`work`↔`watch` at 20,
  `watch`↔`drop` at 14) yields exactly one event;
- a score change that does not cross a boundary yields none;
- **the hinge, both edges**: entering at day 58 (band rises) and leaving at day 69
  (band falls) are both detected. This is the transition the widened 58–68 window
  exists to stop a weekly sweep from stepping over, so it is tested explicitly;
- a target with a null score (S2–S5 incomplete) is skipped, not crashed on;
- second consecutive sweep raises no duplicate stale-signal card.

**Runtime, against the live calibration cohort** — this is a dated fixture and
the numbers are exact. On any sweep on or after **2026-08-18**, comparing today's
bands against a week earlier, `--dry-run` must report **8 band changes and write
nothing**:

| Transition | Count | Targets |
|---|---|---|
| `work` → `watch` | 1 | AIIR Consulting #18 (21 → 19) |
| `watch` → `drop` | 7 | #19, #22, #23, #24, #26, #28, #29 (15 → 13) |
| unchanged | 6 | #6, #17, #20, #21, #25 stay `watch`; #27 stays `drop` |

Not 14. All fourteen lose two points as S1 falls 5 → 3, but only eight cross a
band boundary — which is exactly the distinction requirement 1 tests.

**Invariants:**

```sql
-- must be 0 for the loop's function label
SELECT count(*) FROM agent_runs WHERE started_at > CURRENT_DATE;
-- must be identical before and after a real sweep
SELECT md5(string_agg(t::text, '|' ORDER BY id)) FROM outreach_targets t;
```

## S4 — Settled

All four open decisions were settled by the operator on **2026-08-20**. The
original wording of each is preserved in git history (`9270dba`).

**Binding, unchanged:** Sunday 18:00 · no banding · no LLM · records to
`outreach_events` · never mutates `outreach_targets` · stale-signal detection uses
the existing `signals_stale`.

**O1 — the previous band is RECOMPUTED, not stored.**
`outreach_s1(trigger_date, CURRENT_DATE - 7)` recombined with the stored S2–S5.
Stateless, no migration, and no `v_outreach_scored` drop-and-recreate (the 0016
trap). **Known and accepted cost:** last week's band is reconstructed using *this*
week's S2–S5, so an operator who re-judges S4 mid-week sees that edit reported as
an S1 band change. It is cosmetic in a record nothing bands on. The event payload
must therefore record **both as-of dates** so a reader can tell what was compared,
rather than implying the difference was purely S1.

**O2 — the stale-signal card is a BESPOKE MODAL**, not a nudge.
It captures S2–S5 (and optionally `function_state`) in one submit. **This is now
cheaper than when the question was first written:** confirming Discord's modal
rules for Gate 0 (R0.15 in `PRD-outreach-company-profile.md`) established that
modals support `Label` + `RadioGroup`, so four 1/3/5 judgements plus a
function-state selector fit inside the five-child limit. The card no longer has to
wait for build item 5 to establish the pattern, and it no longer has to degrade
into a pointer at `cli/outreach_score.py`.

**O3 — a Gate 1 card OUTLIVES the band that raised it.** No withdrawal, ever.
The operator's reasoning, which is broader than this one card and is recorded
because it governs more than the rescore loop:

> A rejected or deferred record must stay reviewable as history, and a firm can
> change later in ways that make it newly viable.

So the current behaviour — `decide()` gating only on `status = 'candidate'` and
never re-checking `treatment` — becomes **deliberate rather than accidental**, and
a test should pin it so nobody "fixes" it later. This is the same principle
already built elsewhere and now stated once: Gate 1 drop sets `status='dropped'`
and never deletes (evidence history cannot be rebuilt), and Gate 0 rejection
records a reason and keeps the row. **Nothing in this subsystem deletes a
decision.** Live example as of 2026-08-20: AIIR Consulting sits at score 19,
band `watch`, with its Gate 1 card still posted and still clickable.

**O4 — card only UPWARD crossings into `work`; record everything else.**
A downward crossing writes its `outreach_events` row and raises no card. This
kills the flood: the 8 simultaneous changes this cohort produced are 1 upward and
7 downward, so the first sweep would card **nothing**. Downward movement is
visible in the record and in the briefing count, which is where a trend belongs
rather than in a Sunday-evening decision queue.

**Plus a Ted alert when this loop goes silent** — `35-` §14 previously had entries
for the evidence loop (48h), the BCC poller (2h), and the watch loop (8 days), and
none for this one. Added there. A weekly loop should alert after **8 days**, on
the same reasoning as `outreach-watch`: one missed firing is the signal, and the
08-15 incident showed a silently-not-running loop is this system's worst failure
mode because nothing surfaces it.

**The "that includes gate 0" clause, clarified 2026-08-20:** it is the
*principle* that carries across, not a second branch. This loop does **not**
read `outreach_discoveries` and does not card discoveries. Gate 0 applies
upward-only to its own window, which is recorded as R0.19 in
`PRD-outreach-company-profile.md`. Nothing in this loop changes as a result.

## S5 — Forward references

None. Every artifact this spec names exists in the repo today: `outreach_events`
and `v_outreach_scored` (0013, plus the 0016 rebuild), `outreach_s1(date, date)`
with its as-of parameter, `signals_stale` in the view, `cli/outreach_score.py`,
`agents/_lib/outreach_intake.py`, `task_candidates` (0001), and
`loops/outreach-daily.md` for the banding reasoning carried forward. Verified
present, not assumed.

## S6 — Status

Spec written **2026-08-17** against `main`@`f265beb`, `35-` v0.3.0. Not built. The
runtime fixture in S3 is dated and becomes checkable **2026-08-18**; the cohort's
bands are stable from then until day 90 (**2026-09-08**), when S1 falls 3 → 1 and
every remaining target drops two more points.

## The calibration cohort — why the first sweep looks like a mass failure

All 14 targets carry the identical `trigger_date` of 2026-06-10 across seven
different `trigger_kind` values, with no `trigger_source_url` on any row. They came
from the operator's `Outreach_test_data_2.csv` and **the operator has accepted them
as calibration data (2026-08-17)** — the dates are not observed trigger events and
are not being re-based. The next real import is the first scored cohort.

The consequence this loop must not misread: the whole set moves in lockstep. Today
they sit at day 68, the last day of the 58–68 hinge, where S1 returns 5. Tomorrow
they are all at 3. That single shared date is why the first sweep sees eight
simultaneous band drops and why `work` empties completely — **not** a scoring bug,
a poller failure, or evidence going stale. A future reader debugging "why did
everything fall out of `work` on 18 August" should stop here.

It also corrects a prediction in the 2026-08-14 handback: LifeLabs (#17) and Sales
Gravy (#6), both at 19, were said to be about to "tip to `work` on their own as S1
ages." They will not. They were *inside* the hinge and are leaving it — they tip
down, to 17. Nothing in this cohort rises again.

## Trust

Reads `outreach_targets`, `v_outreach_scored`, and `outreach_evidence`; writes
`outreach_events` and raises cards. It sends nothing, contacts nobody, and
proposes no action to a third party, so it never approaches **B2**. Its cards ask
the operator to make a judgement the system is explicitly forbidden from making
for them.

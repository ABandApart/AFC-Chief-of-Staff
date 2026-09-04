# Outreach Daily Surface — Discord Worklist + Contact/Defer — PRD & Build Spec

<doc:meta>
  <doc:phase>Track O — new increment after Gmail channel (0025) and packet assembly</doc:phase>
  <doc:theme>A Discord daily worklist of ~15 items: due touches to contact, backfilled with pending decisions</doc:theme>
  <doc:duration>~2–3 days (worklist builder, cog + cards, migration)</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>DRAFT 2026-09-03 — operator decisions taken (full Discord surface, Contact=mark-working, worklist-of-15). Not built.</doc:status>
  <doc:depends_on>`35-outreach-crm.md` §7 packet, §8 capacity, §9 surfaces; `37-outreach-workflow.md`; `PRD-outreach-gmail-channel.md`; migrations 0013 (touches), 0025 (gmail); `agents/outreach/daily.py`; `agents/discord_bot/cogs/outreach_intake.py`</doc:depends_on>
  <doc:blocks>nothing — NocoDB, Gmail drafts, and Task Tinder all keep working unchanged</doc:blocks>
</doc:meta>

## TL;DR

Bring the daily outreach work into Discord as a single ranked worklist of up to 15 cards. The top
of the list is **today's due touches** — each rendered as its full packet (arithmetic, driving
facts, template body, freshness, the Gmail draft link) with two buttons: **Contact** ("I'm working
this today" — an intent flag) and **Defer** (snooze past today, bounded by the touch window). When
fewer than 15 touches are due, the list **backfills** with pending outreach *decisions* that already
exist — Gate-1 admits, Gate-4 re-engagements, stalled-reason prompts, Gate-0 reviews — reusing their
existing Task Tinder handlers so there is one decision path, not two.

This **revives the `#outreach-today` surface that `35-` §9 dropped.** That reversal is deliberate and
recorded below. Nothing about the method changes: the system still generates no prose, still never
sends, and the 15-live-target capacity cap is untouched — "15 a day" is a *worklist* size, not a send
quota.

## Goal & Non-Goals

**Goal:** open Discord in the morning and see one screen — up to 15 cards, most-urgent first — that
tells you exactly what to do today and lets you act on each without leaving the channel: contact a
due touch (draft already waiting in Gmail), defer it, or make the next pending decision.

**Non-goals:** the system does not send, does not write the observation sentence, does not generate
prose, and does not raise outreach volume. It does not become a second editor of target/contact
fields — NocoDB stays the editor of record (`35-` §9); this surface acts only on the worklist
(mark-working, defer, skip-with-reason, and the existing decision buttons).

---

## 1. Design decisions taken (2026-09-03)

<decisions>

Each of these was put to the operator against the settled decision it touches.

| # | Decision | Touches | Consequence |
|---|----------|---------|-------------|
| **D-A** | **Revive a Discord daily surface** — read the full packet in Discord with Contact/Defer, rather than keeping NocoDB + a briefing link as the only path. | `35-` §9 dropped `#outreach-today`; its invariants became DB constraints. | The surface is re-created but kept **narrow** (worklist actions only) so it does not become a competing editor. Invariants stay in the database; the surface simply never offers an action that would bypass one. §9 is amended (§11). |
| **D-B** | **Contact = "mark working today"** — an intent flag, not a compose-or-send and not a draft trigger. | The 06:00 Gmail loop already auto-creates the draft; B2 forbids the system sending. | Contact writes `marked_working_at`; the auto-draft loop is untouched. The card still shows the Gmail draft as a link, so the draft is one tap away even though the button only records intent. |
| **D-C** | **Defer = snooze past today**, bounded by the touch's window. | `outreach_touches_snooze_ck`: `snoozed_until <= window_closes`; `35-` §9 "snooze never shifts other slots." | Defer sets `snoozed_until = today + 1` (clamped to `window_closes`). When `window_closes = today`, Defer is disabled and the card offers **Skip (with reason)** instead — the only lawful "not sending this" for a touch whose window ends today. |
| **D-D** | **Worklist of ~15**, not "15 sends." Due touches first, then backfill with pending decisions. | The method caps live targets at 15 total (`35-` §8); daily *due* volume is usually a handful. | No arc touch is sent out of its window and no new throughput is created. The 15 is filled with real work already in the system (decisions), giving a full daily queue while the capacity model stays intact. |

</decisions>

### Refinement (2026-09-03) — supersedes D-D and narrows the card

The operator narrowed the surface after review. These override the sections below where they conflict:

- **R1 — due-touch contacts only.** The daily list is exactly today's due touches (however many;
  no fixed 15, no decision backfill). This supersedes **D-D**: Gate-1/Gate-4/stalled/Gate-0 items are
  **not** shown here — they stay in Task Tinder. The "worklist" in §2 is simply the due-touch set.
- **R2 — two buttons only: Contact and Defer.** No Skip on the card. A touch whose window has closed
  with nothing sent is left to the drain (§8), not skipped from this surface.
- **R3 — a note, required on Defer.** Defer opens a modal with a **required** note field; the note is
  stored on the touch (`snooze_note`). Contact stays one-tap (no note) so the intent flag is frictionless.

The build (below) implements the refinement. Where §2/§4 still describe backfill, Skip, or decision
cards, R1–R3 win.

---

## 2. The worklist — what the 15 are

<worklist>

**Assembled deterministically. Zero LLM.** A ranked list of at most 15 typed items, built each
morning from state that already exists. Ordering, highest priority first:

| Rank band | Item kind | Source | Why it ranks here |
|-----------|-----------|--------|-------------------|
| 1 | **`contact`** — a due touch | `daily.py` due-touch query (`outreach_touches` in window, not sent/skipped/snoozed) | The dated work. A missed window is the only item here that silently expires. Ordered by `due_date`, then by `staleness_days` ascending (freshest evidence first). |
| 2 | **`stalled`** — a stalled-reason prompt | `daily.py` drain `awaiting_reason` (a finished arc holding a capacity slot until answered) | Answering it frees a live slot, so it gates new intake. |
| 3 | **`gate4`** — a re-engagement | `outreach_watch_signals` matched by Trent Crimm, re-engagement allowance not full | A detected trigger is perishable and converts above cold (`35-` §8 E1). |
| 4 | **`gate1`** — admit / watchlist / drop | `outreach_targets` `status='candidate'` with `intake_message_id` set (the `cards_open` count) | Top-of-funnel; ages but does not expire. |
| 5 | **`gate0_review`** — a discovery to review | `outreach_discoveries` awaiting review (`awaiting_review` count) | Triage, not a decision that ages. Fills the tail. |

**Cap at 15.** If due `contact` items alone exceed 15 (rare — it means several arcs coincide), show
all of them and no backfill; the count is honest, not truncated to hit a number. If the total is
under 15, that is the real amount of work — the list is short, not padded (mirrors
`format_briefing_line`'s "a clause reading nothing happened is worse than absent").

**Capacity is never bent to reach 15.** Backfill draws only from work the system already holds. It
never pulls a not-yet-due touch forward (that would break the window semantics, D-C) and never admits
a target to create a send.

The builder lives in **`agents/_lib/outreach_worklist.py`** (`build_worklist(conn, *, today,
limit=15) -> list[WorklistItem]`), unit-tested, per the repo's "logic in `_lib`, thin cogs"
convention. The counts it needs are already computed in one consistent query in `daily.py`
(`_COUNTS_SQL`); the builder returns the actual rows, ranked and capped.

</worklist>

---

## 3. Surfaces — reviving `#outreach-today`

<surfaces>

`35-` §9 dropped `#outreach-today` and moved its invariants into database constraints
(skip-requires-reason, watchlist-requires-stalled-reason, sent-XOR-skipped, reply-implies-send,
snooze-cannot-cross-windows, not-ready-cannot-be-sent). **This PRD un-drops it, and those constraints
stay exactly where they are.** The surface is a *view over* the invariants, not a re-home of them:
every action it exposes writes the same columns NocoDB writes and is subject to the same triggers.

**Why this does not recreate the competing-editor risk §9 warned about.** The concern was a second
surface that edits target/contact fields diverging from NocoDB. This surface edits none of those. It
offers exactly four write actions, each already lawful from another path:

| Action | Writes | Already done today via |
|--------|--------|------------------------|
| Contact ("working today") | `outreach_touches.marked_working_at` | new (advisory only) |
| Defer | `outreach_touches.snoozed_until` | NocoDB |
| Skip (with reason) | `outreach_touches.skipped_at`, `skip_reason` | NocoDB / Shortcut |
| Decision buttons (`gate1`/`gate4`/`stalled`/`gate0`) | via the existing Task Tinder handlers | Task Tinder |

**Surface roles after this change:**

| Surface | Role |
|---------|------|
| **`#outreach` (new)** | **The daily worklist.** Read the packet, mark working, defer, skip-with-reason, and make the day's pending decisions. |
| Task Tinder | Still the canonical decision handlers. `#outreach` reuses them for backfill items; both post the same View. |
| Morning briefing | One line; **its link now points at `#outreach`** (which is now populated) instead of NocoDB. |
| NocoDB filtered view | Still the editor of record for bulk correction, contact-field edits, imports, and reply logging. |
| Gmail draft | Unchanged — the auto-created draft the operator writes in and sends. |

</surfaces>

---

## 4. Card types and actions

<cards>

Discord persistent Views (`timeout=None`), re-attached on startup, operator-guarded by
`OPERATOR_DISCORD_ID`, registered in `agents/discord_bot/run.py` — the `outreach_intake` cog's
pattern (`OutreachIntakeView`, `_reattach_views`). Edit-on-write rendering, like the discoveries cog:
a card keeps its look until the next decision on it.

### Contact card (`contact` — a due touch)

Renders the packet from `outreach_packets`, laid out for a 30-second scan (`35-` §7 "layout is a
usability problem"): the arithmetic first, then the two or three driving facts with freshness marks
(`fresh`/`ageing`/`stale` per `v_outreach_evidence_display`), then the template `body_filled`
collapsed, then the failure mode, then the Gmail draft link and BCC address.

- **✍️ Contact** — sets `marked_working_at = now()`; re-renders the card with a ✓ "working" mark.
  Advisory: it does not change touch state, does not gate anything, and does not touch the draft.
- **⏰ Defer** — sets `snoozed_until = min(today + 1, window_closes)`; the card drops from today's
  list. Disabled when `window_closes = today` (the snooze constraint would reject it); the card shows
  **Skip** instead.
- **🚫 Skip** (secondary; opens a reason modal) — sets `skipped_at`/`skip_reason`. Present because a
  work surface must offer the lawful "not this one" and the DB requires a reason.

A not-ready packet (`ready = false`) still appears — the operator needs to see the unresolved
`operator` slots — but its Contact mark carries no send implication, so no guard is needed here. The
`outreach_touch_ready_guard()` trigger still blocks marking-sent on the assertion paths, unchanged.

### Decision cards (`stalled` / `gate4` / `gate1` / `gate0_review`)

These **reuse the existing Task Tinder / intake handlers verbatim** — same View classes, same button
callbacks, same DB writes. `#outreach` posts them; it does not reimplement them. A decision made on
the `#outreach` copy and one made in `#task-tinder` are the same write to the same row (idempotent,
read-current-state-first — the reaction-robustness rules in `50-channel-layer.md`). The stalled-reason
prompt (a free-text modal) is new and is built here, since `daily.py` currently only logs those.

</cards>

---

## 5. Data model — migration 0027

<data_model>

Additive, idempotent, `NOT EXISTS` guarded. barry-agent applies it.

```sql
-- 0027_outreach_daily_surface.sql
ALTER TABLE outreach_touches
    ADD COLUMN IF NOT EXISTS marked_working_at TIMESTAMPTZ;   -- D-B, advisory intent flag
```

- **Audit is automatic.** `outreach_log_event()` fires on any `outreach_touches` write and diffs
  changed columns via `jsonb_each`, so `marked_working_at` lands in `outreach_events` with no extra
  code (`37-` §5, handoff §4).
- **`v_outreach_scored` is not touched** — this column is on `outreach_touches`, not
  `outreach_targets`, so the frozen-column-list gotcha (handoff §7) does not apply. No view rebuild.
- **No new view is required.** The worklist builder queries existing tables/views directly. If a
  `v_outreach_worklist` proves cleaner at build time, it must be created with an explicit column list
  (not `SELECT *`) so a later column add does not silently drop from it.

</data_model>

---

## 6. Loop and cog wiring

<wiring>

- **Assembly stays in `outreach-daily` (05:45).** Packets are regenerated there before the briefing,
  unchanged. No new loop for assembly.
- **New cog `agents/discord_bot/cogs/outreach_today.py`.** On a poll (once after 06:00, then every
  15 min to reflect intraday sends/replies — the `task_tinder` cadence), it calls
  `outreach_worklist.build_worklist` and posts/edits the ≤15 cards in `#outreach`. Persistent Views,
  `_reattach_views` on startup, registered in `run.py`.
- **New channel.** Create `#outreach` in Discord and add `OUTREACH_CHANNEL_ID` to
  `agents/discord_bot/config.py` (a human step — a channel ID, like the others; barry-agent).
- **Briefing link retarget.** The `format_briefing_line` link points at `#outreach`. The one-line
  counts are unchanged.

</wiring>

---

## 7. Trust boundaries

<trust>

- **B2 unchanged and still structural where it matters.** This surface adds no send path and no
  generation. Contact records intent; it does not call Gmail. `tests/test_no_outbound_send.py` still
  passes untouched.
- **B1 unchanged.** The card displays packet content that is already display-hardened upstream
  (500-char excerpt cap, unicode stripping, source URL + date — `35-` §11). No ingested text becomes
  an instruction, and the Discord render adds no new interpretation of it.
- **The invariants remain in the database.** The surface is incapable of an unlawful write because it
  only calls actions the constraints already permit; it cannot, for example, snooze past a window
  (the CHECK rejects it) or mark a touch sent (it never marks sent — the draft is sent from Gmail).

</trust>

---

## 8. What we build

<build>

| Step | Scope | Effort |
|------|-------|--------|
| 1 | **Migration 0027** — `marked_working_at` on `outreach_touches` | 0.25 day |
| 2 | **`agents/_lib/outreach_worklist.py`** — ranked, capped worklist builder over existing state; unit tests (`tests/test_outreach_worklist.py`) covering ordering, the 15 cap, overflow-of-contacts, and empty | 0.75 day |
| 3 | **`cogs/outreach_today.py`** — contact-card rendering from the packet, Contact/Defer/Skip handlers, poll + post/edit, persistent Views + `_reattach_views`, operator guard; register in `run.py` | 1 day |
| 4 | **Backfill decision cards** — reuse `outreach_intake` (Gate-1) and the watch-signal (Gate-4) handlers; build the **stalled-reason free-text modal** (new) and wire the drain's `awaiting_reason` list to it | 0.5 day |
| 5 | **Briefing link retarget** to `#outreach`; **`OUTREACH_CHANNEL_ID`** added to `config.py` (human creates the channel) | 0.25 day |
| 6 | **Doc amendments** (§11) | 0.25 day |

**Sequencing:** depends on packet assembly and the Gmail draft loop (both live). No dependency on
Apollo paid, BCC IMAP, or the loop watchdog (handoff §6).

</build>

---

## 9. Verification

<verification>

```bash
cd ~/code/aiadaptive-cos && uv run pytest -q          # existing ~936 + new worklist/cog tests
uv run ruff check agents/_lib/outreach_worklist.py agents/discord_bot/cogs/outreach_today.py
psql aiadaptive_cos -f migrations/0027_outreach_daily_surface.sql   # apply to build DB, re-run pytest
```

Outcome checks (each independently verifiable):

| # | Outcome | How to verify |
|---|---------|---------------|
| AC1 | `marked_working_at` exists and is audited | `\d outreach_touches` shows it; set it, then `SELECT` the matching `outreach_events` row |
| AC2 | Worklist ranks due `contact` items first, then decisions, capped at 15 | Unit test with seeded touches + candidates + discoveries |
| AC3 | Under-15 days show the real count, not padding; over-15 contacts show all, no backfill | Two unit-test fixtures |
| AC4 | Contact sets `marked_working_at`, changes no touch state, leaves the draft untouched | Click Contact; assert only that column changed; Gmail draft unchanged |
| AC5 | Defer sets `snoozed_until = min(today+1, window_closes)` and the touch drops from today | Click Defer; re-run the builder same day → item absent |
| AC6 | Defer is disabled and Skip is offered when `window_closes = today` | Seed such a touch; card renders Skip, not Defer |
| AC7 | A decision made in `#outreach` and one in `#task-tinder` are the same idempotent write | Click a Gate-1 card in each channel; second is a no-op |
| AC8 | No send path added | `uv run pytest tests/test_no_outbound_send.py` still passes |
| AC9 | Briefing link points at `#outreach` | Inspect the rendered briefing line |

</verification>

---

## 10. Risks

<risks>

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| **DS-R1** | The surface becomes a second editor and diverges from NocoDB | Medium | Scoped to four worklist actions, each writing columns NocoDB already writes; no target/contact-field editing (§3) |
| **DS-R2** | Duplicated decision logic drifts from Task Tinder | Medium | Reuse the existing View classes and handlers verbatim; do not reimplement (§4) |
| **DS-R3** | A card goes stale intraday (evidence closed after 05:45; a reply landed) | Low | 15-min poll re-renders; edit-on-write; the assertion-path guards still hold in the DB |
| **DS-R4** | "Worklist of 15" is misread over time as a send target, pressuring out-of-window sends | Medium | D-D recorded here and in the §9 amendment; the builder cannot pull a not-due touch forward |
| **DS-R5** | Operator acts on a `not-ready` contact card and sends a placeholder from Gmail | Low→Med | Unresolved `operator` slots shown visibly on the card; the ready-guard still blocks assertion-path marking; unchanged from the Gmail PRD §6 posture |

</risks>

---

## 11. Doc amendments (part of the build)

<amendments>

- **`35-` §9 surfaces** — un-drop `#outreach-today` (as `#outreach`); record the narrow scope and the
  four lawful actions; note D-D (worklist ≠ send quota).
- **`37-` §7 "deliberately not here"** — remove/adjust the `#outreach-today` dropped note.
- **`40-action-layer.md`** — add the `#outreach` cog to the Outreach loops/cogs; add `OUTREACH_CHANNEL_ID`.
- **`50-channel-layer.md`** — add `#outreach` to the server layout.

</amendments>

---

## 12. Open sub-questions

<open_questions>

1. **Defer default span.** `today + 1` (re-surfaces tomorrow) vs. a longer default. One day matches
   "not today" and never risks crossing a window silently; longer risks hitting `window_closes`.
   Assumed `today + 1`.
2. **Poll vs. once-daily post.** Post once after 06:00 and refresh every 15 min (assumed), or post
   once and leave it static until tomorrow? Refresh keeps replies/sends reflected but edits messages
   through the day.
3. **`marked_working_at` reset.** Advisory flag; assumed it persists on the touch (history) and the
   card simply shows ✓. Confirm you do not want it auto-cleared nightly.
4. **Stalled-reason modal ownership.** Built here (the drain currently only logs `awaiting_reason`).
   Confirm it belongs to this surface rather than a standalone Task Tinder card.

</open_questions>

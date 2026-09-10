# Task Tinder / Outreach channel refinements — three increments

<doc:meta>
  <doc:from>barry-agent (design capture), 2026-09-10</doc:from>
  <doc:to>barry-admin (build)</doc:to>
  <doc:status>Increments 1–3 BUILT 2026-09-10. Spec-before-build per the working convention.</doc:status>
  <doc:depends_on>`50-channel-layer.md`; `agents/discord_bot/cogs/{task_tinder,outreach_intake,outreach_discovery}.py`; `agents/_lib/task_tinder.py`; `agents/discord_bot/config.py`; tables `task_candidates`, `tasks`, `follow_ups`, `content_items`.</doc:depends_on>
  <doc:not_this>The pool-wedge bug is separate — `HANDOFF-2026-09-09-discord-pool-wedge.md`.</doc:not_this>
</doc:meta>

## Decisions taken (operator, 2026-09-10)

| # | Decision |
|---|----------|
| **D1** | Outreach is separated from the content Task Tinder. **All outreach decision cards move to #outreach** — Gate-0 review, Gate-1 intake, **and the O2 re-score card** (operator, 2026-09-10: rescore added). The routing invariant is *no outreach cards in `#task-tinder`*. `#task-tinder` keeps content suggestions **and inbound leads** (operator, 2026-09-10: inbound leads stay for now — moving them is a later question). |
| **D2** | Content cards **surface the one source link already stored** now. Multi-source research is a **deferred, separate** increment (Tartt is a feed poller — one source per story). |
| **D3** | Accepted Task Tinder items — which already create `tasks` rows — are surfaced as a **bot-maintained pinned checklist** in a new `#tasks` channel. (Discord has no bot-controllable side panel; the editable pinned message is the native equivalent.) |

---

## Increment 1 — Move outreach decisions out of #task-tinder → #outreach

**Status:** BUILT 2026-09-10 (this increment). Increments 2 and 3 remain SPEC.

**Why:** **four** cogs posted to `TASK_TINDER_CHANNEL_ID` — content `task_candidates`
(**and inbound leads**), outreach Gate-1 intake, outreach Gate-0 review, **and the O2
re-score card**. The operator reads `#task-tinder` as content, so the outreach sheets were
effectively invisible there. The daily **Contact/Defer** touch cards were *already* in
`#outreach`; this increment moves the review / intake / re-score cards to join them.

**Outcome (S1):** every outreach decision cog — `outreach_intake` (Gate-1),
`outreach_discovery` (Gate-0), `outreach_rescore` (O2) — posts to `#outreach`
(`OUTREACH_CHANNEL_ID`). `#task-tinder` receives **only non-outreach** cards: content
`discovery` candidates and `inbound_lead`s (the `task_tinder` cog is unchanged; inbound leads
stay per D1). The routing invariant — *no outreach cards in `#task-tinder`* — is enforced by
`tests/test_outreach_card_routing.py`, so a future outreach cog cannot silently regress it. The
daily Contact/Defer surface in `#outreach` is unchanged.

**Changes (done):**
- `agents/discord_bot/cogs/outreach_intake.py` — `TASK_TINDER_CHANNEL_ID` → `OUTREACH_CHANNEL_ID` (import + `:155`).
- `agents/discord_bot/cogs/outreach_discovery.py` — same swap (`:485`, `:597`).
- `agents/discord_bot/cogs/outreach_rescore.py` — same swap (`:170`, `:251`). *(Added per operator 2026-09-10; missing from the first draft — see the review.)*
- `architecture/50-channel-layer.md` — channel table + "Outreach card variants (Track O)" note corrected to the new split.
- `tests/test_outreach_card_routing.py` — new grep-guard pinning the routing invariant.

**Open decisions (surface, not for the builder to pick silently):**
- The 3 already-surfaced Gate-0 sheets (message ids in `#task-tinder`, 8/24–8/25) and any live
  intake cards keep their old ids in `#task-tinder`; reattach uses the stored `review_message_id` /
  `intake_message_id`, so they stay put and still function. Leave them to be decided in place
  (recommended — reposting risks a double-decision), or repost to `#outreach` and retire the old
  ones? Operator's call.
- `#outreach` now carries **four** card types with **different lifecycles** — create-once contact
  touches (a persistent worklist), Gate-1 intake (one-shot decision), Gate-0 review (paginated,
  edit-on-write), and O2 re-score (S4/S5 modal) — interleaved by post time since the polls are
  independent. So `#outreach` is the outreach **workflow** channel, not a single create-once
  worklist (a distinction worth holding, since the daily surface alone *is* create-once). Confirm
  the interleaving is acceptable vs. wanting visual/section separation.

**Verification (S3):** after the change + bot restart, a newly eligible Gate-0/Gate-1 item posts
to `#outreach`; a new content candidate posts to `#task-tinder` only; existing `#outreach` contact
cards are unaffected.

**Non-goals:** no change to the daily contact-surface logic, capacity, or the review rules.

---

## Increment 2 — Show the source link on content cards (surface only)

**Status:** BUILT 2026-09-10. The open decision below was resolved as recommended —
`list_undelivered` LEFT JOINs `content_items`, so a link renders only when one
resolves for that card, and non-matching source types render unchanged.

**Why:** content cards carry `evidence_text` (prose) and `source_ref` (a `content_node` UUID) but
no visible link. The URL **is** stored: `content_items.url`, reachable via
`content_items.content_node = task_candidates.source_ref`. **42/42 discovery cards resolve today.**

**Outcome (S1):** every content card (`source_type='discovery'`) in `#task-tinder` shows its source
URL, taken from `content_items.url` for the matching `content_node`. A card whose URL cannot be
resolved renders exactly as it does today (no error, no empty "Source:" line).

**Changes:**
- `agents/discord_bot/cogs/task_tinder.py:40` (`build_card`) — render the URL when present.
- The card's fetch path (cog `_poll` / its `_lib` query) — join or look up `content_items` by
  `content_node = source_ref`. `content_items.content_node` confirmed present.

**Non-goal — and the deferred increment (D2):** multi-source research. Tartt polls feeds, so each
suggestion has exactly one origin article; there is no set of "all sources the agent found." A
future increment, **"content multi-source corroboration,"** would add an agent step that gathers
additional sources per story before carding. Out of scope here; named so it isn't lost.

**Open decision:** only `discovery` cards have a `content_items` URL; `outreach_stale_signal` and
`inbound_lead` cards have other `source_ref` shapes. Recommended rule: show a link only when one
resolves for that card's source type.

**Verification (S3):** a discovery card renders a clickable link equal to `content_items.url` for
its `content_node`; a card with no resolvable URL renders unchanged.

---

## Increment 3 — Accepted items → pinned checklist in #tasks

**Status:** BUILT 2026-09-10. New files `agents/_lib/tasks_checklist.py` +
`agents/discord_bot/cogs/tasks_checklist.py`; `TASKS_CHANNEL_ID` added
(fail-closed 0); cog registered in `run.py`. The open decisions below were
resolved by the operator (2026-09-10): completed items show **struck-through,
then archive** after 24h (`ARCHIVE_AFTER`); check-off is **reversible** (uncheck
reopens, clearing `completed_at`); ordering is **escalation then due date**
(`follow_ups.escalation_level` desc, `tasks.due_date` asc). Channel name `#tasks`;
component budget handled by a `MAX_ROWS = 25` cap (open-first, so a cap never
hides open work) with an overflow note in the header.

**Component-budget fix (2026-09-10, barry-agent runtime → barry-admin):** the first
build capped at `MAX_ROWS = 25`, above Discord's real Components-v2 ceiling — a
single message holds **40 components** and each task costs 3 (Section + TextDisplay
+ Button), so **12 task sections** is the true maximum; 14 open tasks made the view
raise `maximum number of children exceeded (40)` and render nothing. Fixed:
`MAX_ROWS = 12`, with the header stating how many open tasks are not shown
(checking some off surfaces the rest on the next poll). A queue that routinely
exceeds 12 wants pagination across multiple pinned messages (gate-0's
`ROWS_PER_MESSAGE` pattern) — deferred, operator's call.

**Mechanism:** one pinned **Components-v2** `LayoutView` (Container → one Section
per task + an accessory check/undo Button), per the sketch — discord.py 2.7.1
supports it; first use of Components-v2 in this repo. **Deviation from the sketch
(builder's call, recorded):** the pinned message is rebuilt by a 60s **poll** and
edited immediately on a toggle, *not* event-pushed from `task_tinder.promote`. A
newly accepted task therefore appears within one poll cycle, with no cross-cog
coupling — the same trade the other surface cogs make. The separation invariant
is pinned by `tests/test_tasks_checklist_separation.py` (only
`task_tinder.promote` inserts `tasks`).

**Runtime (barry-agent):** create the `#tasks` channel, grant the bot **Manage
Messages** there (to pin), set `TASKS_CHANNEL_ID`, restart. Then live-verify S3
below. Because this is the repo's first Components-v2 surface and the cog can't be
exercised on the build box, confirm the pinned message renders and the buttons
complete/reopen tasks.

**Context (already built):** accepting a Task Tinder card creates a `follow_up` + a linked `tasks`
row (`agents/_lib/task_tinder.py:173` `promote`, called from `task_tinder.py:185`). `tasks` holds
**14 open rows today**. So the to-do *list exists in Postgres*; this increment adds a **surface** to
view and check it off — it does not create storage.

**Discord capability (recorded so it isn't re-litigated):** a bot cannot create a persistent
side-panel in the Discord client (member list / threads / pins / events are fixed UI). The native
equivalent of a "persistent checklist" is a **pinned message the bot edits in place**, with
Components-v2 check-off controls, re-attached on restart. Not a docked panel. (Discord Activities /
Embedded App SDK could render a real panel app but is an iframe activity built for voice channels —
disproportionate; explicitly not chosen.)

**Outcome (S1):** a `#tasks` channel holds one bot-maintained **pinned** message listing open tasks
(from `tasks WHERE status='open'`), each with a check-off control. Checking an item marks it
complete (`tasks.status`/`completed_at`) and the pinned message updates in place. The message and
its controls survive a bot restart.

**Changes (sketch — builder decides specifics):**
- `agents/discord_bot/config.py` — new `TASKS_CHANNEL_ID`.
- New cog (or extend `task_tinder`) that maintains **one** pinned checklist message rebuilt from
  `tasks`; Components-v2 (Container → Section per task + a check `Button`) with stable `custom_id`s;
  persistent-view reattach on `cog_load`/`before_loop`, mirroring `task_tinder`/`approvals`.
- An `_lib` writer to mark a task complete, idempotently guarded (`WHERE status='open'`), matching
  the repo's DB-guarded-idempotency pattern.
- Re-render the pinned message on two events: an accept in `task_tinder.promote` (new task) and a
  check-off (task completed).
- Bot needs **Manage Messages** in `#tasks` to pin. No schema change expected (`tasks` already has
  `status`, `completed_at`); if one is added, number the migration and update `verify_schema.sql`
  with `ALTER TABLE … OWNER TO barry_agent`.

**Open decisions (operator's, in the build):**
- `#tasks` channel name/placement.
- Completed items: drop off immediately, or show struck-through then archive.
- Ordering/grouping (by `due_date`? by `follow_ups.escalation_level`?).
- Uncheck / undo.
- Component budget: Discord caps components per message. If open tasks exceed it, paginate across
  multiple pinned messages (mirror gate-0's `ROWS_PER_MESSAGE` paging) or cap-with-overflow.

**Separation invariant (keeps `#tasks` clean):** only `task_tinder.promote`
(`agents/_lib/task_tinder.py:186`) inserts `tasks` rows — no outreach path does (outreach state
lives in `outreach_touches`/`outreach_targets`). So `tasks WHERE status='open'` shows only
content-accepted work and cannot leak outreach items into `#tasks`. Verified by grep; worth a
guard test alongside the build.

**Non-goals:** not a real docked side-panel (Discord has none — the pinned message is the native
equivalent); no change to how tasks are *created* (accept still runs `promote`); no schema change
unless one proves needed (then number the migration + update `verify_schema.sql` + `ALTER … OWNER
TO barry_agent`).

**Verification (S3):** accept a content card → its task appears in the `#tasks` pinned checklist;
check it → marked complete, message updates, open count decrements; restart the bot → the checklist
is intact and its buttons are live.

---

## Build notes

- All three are **barry-admin** build increments. 1 and 2 are small; 3 is the largest and could
  graduate to its own PRD, but is specced here so it can start.
- Suggested order: **1** (tiny, removes the channel confusion) → **2** (tiny, data already there) →
  **3** (the checklist surface).
- Standard gates apply: `uv run pytest -q` and `uv run ruff check .` clean (no new ruff errors);
  suite passes without the optional dep groups; logic in `_lib`, thin cogs; commit trailer for the
  authoring model.

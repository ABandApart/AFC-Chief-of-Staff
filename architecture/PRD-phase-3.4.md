# Phase 3.4: `/outcome` Slash Command — PRD & Build Instructions

<doc:meta>
  <doc:phase>3.4</doc:phase>
  <doc:parent_phase>3 — Capture and Recall</doc:parent_phase>
  <doc:theme>Outcome capture — the KR1 measurement substrate starts collecting</doc:theme>
  <doc:duration>~2.5 hours + a few minutes smoke</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — independent of 3.3; buildable in parallel</doc:status>
  <doc:depends_on>3.1 (bot skeleton), outcomes table (migration 0001)</doc:depends_on>
  <doc:blocks>Phase 11 (Higgins reports outcomes weekly)</doc:blocks>
</doc:meta>

## TL;DR

A Discord `/outcome` slash command that records business outcomes
(discovery call booked, proposal sent, engagement signed, …) into the
`outcomes` table. No automation backfills this table — it's operator
discipline, and it's the substrate Higgins (Phase 11) reports against for
KR1. No LLM calls.

---

## Goal & Non-Goals

<goals>

**Goal**: From Discord, run `/outcome`, pick a type, type a description (and
optional dollar value / linked fact), and get a row in `outcomes` with an
ephemeral confirmation.

</goals>

<non_goals>

- **No LLM call** — this is a pure structured write.
- **No automated outcome inference** — outcomes are entered by hand, by design
  (`80-telemetry-layer.md`: "No automation backfills this table — it's
  discipline.").
- **Rich attribution to prospects/tasks/content** — those tables aren't
  populated until Phases 6/5/8. 3.4 supports optional attribution to a
  **fact** (facts exist now); the other `attributed_*` columns stay null until
  their sources exist.
- **Reporting / weekly rollup** — Higgins (Phase 11) reads this table; 3.4
  only writes.
- **Editing / deleting outcomes** — append-only in v1; fix via SQL if needed.

</non_goals>

---

## Design

### Why command-param type + modal (not a modal dropdown)

`80-telemetry-layer.md` describes a modal with a **Type dropdown**. Discord
**modals only support text inputs — no dropdowns/selects**. So the workable
shape is:

- `/outcome type:<choice>` — `type` is an `app_commands` choice parameter
  (renders as a proper dropdown in the command UI).
- Selecting it opens a **modal** (`discord.ui.Modal`) collecting:
  - **Description** — paragraph TextInput (required, multi-line)
  - **Value $** — short TextInput (optional; parsed as float)
  - **Linked fact id** — short TextInput (optional; → `attributed_fact_id`)
- Submit → write the `outcomes` row → ephemeral confirmation.

This preserves the architecture's intent (a guided form with a multi-line
description) while respecting Discord's modal constraints.

### Outcome types (from `80-telemetry-layer.md`)

`discovery_call_booked`, `proposal_sent`, `engagement_signed`,
`engagement_renewed`, `maintenance_converted`, `newsletter_published`,
`roundtable_topic_used`, `partnership_explored`.

### Command sync

Slash commands are **guild-scoped** to `GUILD_ID` (instant; global sync lags
~1h). Synced in `setup_hook` after loading cogs.

---

## Acceptance Criteria

<acceptance>

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | `/outcome` is registered in the AFC Richmond guild | Type `/` in Discord → `/outcome` appears with the type dropdown |
| AC2 | Selecting a type opens the modal | Visual |
| AC3 | Submitting writes an `outcomes` row with type + description (+ value) | `SELECT * FROM outcomes ORDER BY recorded_at DESC LIMIT 1` |
| AC4 | Optional value: valid number stored; blank → null; non-number → friendly ephemeral error, no row | Try each |
| AC5 | Optional fact link: a valid `facts.id` sets `attributed_fact_id`; blank → null; bad id → friendly error | Link a real 3.2 fact id |
| AC6 | Ephemeral confirmation after submit | Visual (only operator sees it) |
| AC7 | Unit test for the value / fact-id parsing helper passes | `uv run pytest tests/test_outcomes.py` |

</acceptance>

---

## Deliverables Manifest

```
aiadaptive-cos/
├── agents/discord_bot/
│   ├── brain.py                      # +insert_outcome(); +fact_exists()
│   ├── run.py                        # sync app commands to guild in setup_hook
│   └── cogs/outcomes.py              # NEW — /outcome command + modal
├── tests/test_outcomes.py            # NEW — parse helpers
├── architecture/PRD-phase-3.4.md     # this file
└── checklists/CHECKLIST-phase-3.4.md
```

No new credentials (no LLM). No `runs.py` / cost-helper changes (no LLM call).

---

## Architectural Decisions

1. **No cost helper.** `/outcome` makes no LLM call, so it does not go through
   `agent_run`. It's a direct `brain.insert_outcome` write. (The cost helper
   is specifically for LLM-call accounting.)
2. **Type on the command, form in the modal.** Works around Discord's
   no-dropdown-in-modal limit while keeping a multi-line description.
3. **Attribution: fact-only in 3.4.** `attributed_fact_id` is wired now;
   prospect/task/content/signal attribution is added as those tables fill
   (Phases 5/6/8/10). Keeps the modal short and relevant.
4. **Parsing helper is pure + unit-tested.** Value and fact-id parsing
   (blank → None, bad → error) is a pure function so it's testable without
   Discord or a DB.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Guild command sync not appearing | Medium | Med — command invisible | Guild-scoped sync is instant; log synced command count on startup; re-sync on `on_ready` if 0 |
| Modal text input for value/id → bad input | Medium | Low | Pure parse helper: blank → null, non-number → friendly ephemeral error, no row written |
| `attributed_fact_id` FK violation on bad id | Medium | Low | `fact_exists()` check before insert; bad id → friendly error |
| `applications.commands` scope missing | Low | High — can't register | Scope was granted at install (3.1 OAuth); verify on first sync |

---

## Task Breakdown (sequenced)

<tasks>

1. **[BARRY-ADMIN]** `brain.py`: `insert_outcome(outcome_type, description,
   value, attributed_fact_id)` + `fact_exists(fact_id)`.
2. **[BARRY-ADMIN]** `cogs/outcomes.py`: `/outcome` app command with type
   choices → modal (description, value, fact id) → parse → write → ephemeral
   confirm.
3. **[BARRY-ADMIN]** `run.py`: load the cog; `await self.tree.sync(guild=...)`
   in `setup_hook`; log synced count.
4. **[BARRY-ADMIN]** `tests/test_outcomes.py`: parse-helper tests
   (blank/valid/invalid value + fact id).
5. **[BARRY-ADMIN]** Commit builder-side, push.
6. **[BARRY-AGENT]** `git pull`, `uv sync`, run bot, invoke `/outcome`, submit,
   verify the `outcomes` row (incl. an optional value + linked fact). Report.
7. **[BARRY-ADMIN]** Close 3.4; create PHASE-3.5.md (briefing skeleton +
   launchd — also where the `run.py` SIGTERM handler gets done + tested).

</tasks>

---

## Definition of Done

<dod>

3.4 is complete when `/outcome` reliably writes a typed, described outcome
(with optional value and fact link) to the `outcomes` table from Discord, with
an ephemeral confirmation and graceful handling of bad input.

</dod>

## What Phase 3.5 Will Do With This

3.5 (briefing skeleton + launchd) is the last 3.x sub-phase: a 6am briefing
posted to `#briefing`, the bot + briefing run under launchd, and the
`run.py` **SIGTERM handler** (carried forward from 3.1) is implemented and
tested under real launchd `SIGTERM`. Higgins (Phase 11) later reads the
`outcomes` rows 3.4 collects.

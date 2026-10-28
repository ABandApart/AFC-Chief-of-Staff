# Phase 3.4 Checklist — /outcome Slash Command

Live tracker for sub-phase 3.4. Each item maps to an AC in
`architecture/PRD-phase-3.4.md`.

Built ahead in parallel while 3.3 was being validated. Runtime-side waits
until 3.3 closes and `PHASE-3.4.md` becomes the active coordination file.

## Pre-flight

- [x] outcomes table exists (migration 0001)
- [x] Phase 3.4 PRD written (`b57988a`)

## Builder-side (barry-admin)

- [x] `brain.py`: `insert_outcome()` + `fact_exists()`
- [x] `cogs/outcomes.py`: `/outcome` app command (8 type choices) → modal
      (description, value, fact id) → parse → write → ephemeral confirm
- [x] `run.py`: load outcomes cog; `tree.copy_global_to(guild)` + `tree.sync(guild)`;
      log synced command count
- [x] `tests/test_outcomes.py`: 8 parse-helper / type tests; suite 42/42
- [x] All modules import clean (discord modal/command classes load without a connection)
- [x] builder-side commit pushed

## Runtime-side (barry-agent — after 3.3 closes)

- [x] **[BARRY-AGENT]** `git pull` + `uv sync`; run bot
- [x] **AC1**: `/outcome` appears in the guild with the type dropdown (check the synced-count log line)
- [x] **AC2**: selecting a type opens the modal
- [x] **AC3**: submit writes an `outcomes` row (type + description [+ value])
- [x] **AC4**: blank value → null; non-number → ephemeral error, no row
- [x] **AC5**: linked fact id → `attributed_fact_id` set; bad id → friendly error
- [x] **AC6**: ephemeral confirmation shown
- [x] **AC7**: `uv run pytest tests/test_outcomes.py` passes

## Done

- [x] All 7 acceptance criteria checked
- [x] **[BARRY-ADMIN]** final commit "Phase 3.4: /outcome online"
- [x] Pushed; memory updated; PHASE-3.5.md created

## Notes / issues encountered

- Discord modals can't hold dropdowns → type is a slash-command choice
  parameter; description/value/fact-id are the modal's text inputs.
- App-command sync is guild-scoped (instant). If `/outcome` doesn't appear,
  check the "synced N app command(s)" startup log line and that the bot was
  installed with the `applications.commands` scope (it was, at 3.1).
- First real test of slash commands + modals in this project — barry-agent:
  if the command doesn't register or the modal misbehaves, capture the exact
  symptom; it's likely a sync/scope detail, fixable quickly.

- **Reconciled 2026-10-28 (barry-admin):** this checklist went stale after the
  phase closed. All 7 ACs passed at the 3.4 close and were re-verified live in
  the 2026-07 refactor re-validation (`PHASE-REFACTOR-2026-07.md` handback:
  /recall + /outcome end to end, value parsing, FK rejection of a bogus fact
  id). Two ACs describe behavior since superseded: the refactor's R12 moved the
  fact link from modal free-text to a command param with autocomplete, and the
  3.7 cognee migration (0006) dropped `facts`, so `/outcome` now records with
  no fact link at all. The ticks record what was true when 3.4 closed.

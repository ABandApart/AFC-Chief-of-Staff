# Phase 3.2 Checklist — Capture Flow

Live tracker for sub-phase 3.2. Each item maps to an AC in
`architecture/PRD-phase-3.2.md`.

## Pre-flight

- [x] Phase 3.1 complete (commit `2e86312`)
- [x] Phase 3.2 PRD written

## Builder-side (barry-admin)

- [x] **Task 1**: `KEY_BY_AGENT["fact-extraction"]` registered in `runs.py`
- [x] **Task 2**: `agents/discord_bot/brain.py` — `insert_fact` with `::vector` cast + dim guard
- [x] **Task 3**: `agents/discord_bot/cogs/capture.py` — listener, extract→embed→insert, edge cases
- [x] **Task 4**: capture cog loaded in `run.py` `setup_hook`
- [x] **Task 5**: `tests/test_capture.py` — 16 parser/summary tests, all pass
- [x] All modules import clean; full suite 24/24 green
- [ ] **Task 6**: builder-side commit pushed  ← in progress

## Runtime-side

- [x] **Task 7 [USER]**: Anthropic key for fact extraction provided (2026-06-16)
- [x] **Task 8 [BARRY-AGENT]**: `anthropic-key-fact-extraction` stored in barry-agent keychain
- [x] **Task 9 [BARRY-AGENT]**: smoke run — 1st pass found the embedding 404; after fix `dc04cc6`, re-test green (2026-06-17)
  - [x] **AC1**: ⏳ then ✅ reaction
  - [x] **AC2**: 2 `facts` rows, source_type='discord', source_ref=message_id
  - [x] **AC3**: embeddings non-null, **768-dim** (vector_dims confirmed)
  - [x] **AC4**: `agent_runs` rows — anthropic/claude-haiku-4-5 + gemini/gemini-embedding-001, both success
  - [x] **AC5**: empty / link-only handled (no facts written on embed-fail; fail-safe verified)
  - [x] **AC6**: reply with one-line summary
  - [x] **AC7**: key present (keychain_verify)

## Done

- [x] All 8 acceptance criteria checked
- [ ] **Task 10 [BARRY-ADMIN]**: final commit "Phase 3.2: capture flow online"  ← this commit
- [ ] Pushed; memory updated; PHASE-3.3.md created

## Notes / issues encountered

- 2026-06-16/17: `call_embedding` (Gemini) was exercised for the FIRST time in
  3.2. The 1st live run surfaced that `text-embedding-004` returns **404** on
  this Gemini key (not served on `v1beta` embedContent). Fixed in `dc04cc6`:
  switched to **`gemini-embedding-001` with `output_dimensionality=768`** and
  added in-helper L2 normalization (the 768-dim Matryoshka truncation isn't
  pre-normalized by the API). Re-test green: 2 facts @ 768 dims, both provider
  rows `status=success`.
- Non-blocking: one 1st-run extraction billed 73–75 output tokens but
  `call_anthropic` yielded empty text → `parse_facts` "char 0" → ⚠️ graceful
  degrade. Did NOT recur on the green re-test. Treating as one-off model
  variance; revisit if it returns.

-

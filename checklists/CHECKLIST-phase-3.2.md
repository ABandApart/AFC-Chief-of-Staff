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

- [ ] **Task 7 [USER]**: provide an Anthropic API key for fact extraction
- [ ] **Task 8 [BARRY-AGENT]**: store `anthropic-key-fact-extraction` in barry-agent keychain
- [ ] **Task 9 [BARRY-AGENT]**: `git pull` + `uv sync`, run bot, post a test capture
  - [ ] **AC1**: ⏳ then ✅ reaction
  - [ ] **AC2**: ≥1 `facts` row, source_type='discord', source_ref=message_id
  - [ ] **AC3**: embedding non-null, 768-dim
  - [ ] **AC4**: `agent_runs` rows for fact-extraction (anthropic + gemini)
  - [ ] **AC5**: empty / link-only message → nudge, no facts
  - [ ] **AC6**: reply with one-line summary
  - [ ] **AC7**: key present (keychain_verify shows it)

## Done

- [ ] All 8 acceptance criteria checked
- [ ] **Task 10 [BARRY-ADMIN]**: final commit "Phase 3.2: capture flow online"
- [ ] Pushed; memory updated; PHASE-3.3.md created

## Notes / issues encountered

- 2026-06-16: `call_embedding` (Gemini text-embedding-004) is exercised for
  the FIRST time in 3.2 — Phase 2's smoke only used generate_content. If the
  google-genai embed API surface differs from `client.models.embed_content(...)
  → result.embeddings[i].values`, the 3.2 smoke will surface it. The fix would
  be a small adjustment in `runs.py` `call_embedding` (and possibly the model
  string `text-embedding-004` → `models/text-embedding-004`). barry-agent:
  capture the exact error if embedding fails.

-

-

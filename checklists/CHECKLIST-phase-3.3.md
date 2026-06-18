# Phase 3.3 Checklist — Recall CLI

Live tracker for sub-phase 3.3. Each item maps to an AC in
`architecture/PRD-phase-3.3.md`.

## Pre-flight

- [x] Phase 3.2 complete (`f018cea`) — facts with 768-dim embeddings exist
- [x] call_embedding validated live (gemini-embedding-001 @ 768)
- [x] Phase 3.3 PRD written (`6cfcb76`)

## Builder-side (barry-admin)

- [x] `DAILY_CEILINGS["recall"] = 0.50` added in runs.py (gemini-only, no KEY_BY_AGENT)
- [x] `cli/recall.py` — argparse (query, --limit, --lex-weight, --sem-weight),
      embed via cost helper, hybrid FTS+vector SQL, ranked output
- [x] `tests/test_recall.py` — 7 tests (format_results, format_result_line,
      _vector_literal); suite 34/34 green
- [x] `--help` works; imports clean
- [ ] builder-side commit pushed  ← in progress

## Runtime-side (barry-agent)

- [x] **[BARRY-AGENT]** `git pull` + `uv sync`
- [x] **[BARRY-AGENT]** Round-trip test (facts from 3.2 already in the brain):
  - [x] **AC1**: `cli.recall "<query>"` prints ranked facts
  - [x] **AC2**: paraphrase ("third-quarter mailing") surfaced the Q3 newsletter fact first
  - [x] **AC3**: exact-term ("Alex") ranked the Alex fact top
  - [x] **AC4**: `agent_runs` logs every query as recall/gemini/gemini-embedding-001 success
  - [x] **AC5**: `--limit 1` capped to a single result
  - [x] **AC6**: gibberish → "No matching facts." (after the `--min-sim` 0.55 floor fix, `1adf182`)

## Done

- [x] All 7 acceptance criteria checked (4/5 on first run; AC6 green after the floor fix)
- [ ] **[BARRY-ADMIN]** final commit "Phase 3.3: recall CLI online"  ← this commit
- [ ] Pushed; memory updated; PHASE-3.4.md created

## AC6 note (added 2026-06-18)

First round-trip found AC6 failing: vector search returned the nearest facts
for *any* query (no similarity floor), so gibberish surfaced something.
barry-agent isolated the data — relevant 0.67, noise ≤0.48 — and a cosine
floor `--min-sim` (default 0.55) was added in `1adf182`. Re-test green: AC6
fixed, AC2/AC3/AC5 unaffected, and the floor also trimmed secondary noise rows
(AC2/AC3 now return just the relevant fact). 0.55 works for the current corpus;
tunable as it grows.

## Notes / issues encountered

- 3.3 inlines the hybrid-search SQL in `cli/recall.py`. Promote to a
  `hybrid_search_facts()` Postgres function (migration 0002) when briefing
  (3.5) becomes the second caller.
- Weight blend 0.4 lex / 0.6 sem is the architecture's starting point; lexical
  `ts_rank_cd` values are typically small vs semantic, so sem tends to
  dominate. Flags `--lex-weight`/`--sem-weight` allow tuning. Revisit defaults
  once there's real fact volume (Phase 4+).
- `_vector_literal` is now duplicated in `brain.py` and `recall.py`. Future
  simplify: consolidate vector formatting + keychain_get into a shared
  `agents/_lib` module.

-

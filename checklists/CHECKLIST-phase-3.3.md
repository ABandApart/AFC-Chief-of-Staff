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

- [ ] **[BARRY-AGENT]** `git pull` + `uv sync`
- [ ] **[BARRY-AGENT]** Round-trip test (facts from 3.2 already in the brain):
  - [ ] **AC1**: `cli.recall "<query>"` prints ranked facts
  - [ ] **AC2**: paraphrase query (no shared exact words) finds a 3.2 fact
        (e.g. recall "third-quarter mailing plan" → the Q3 newsletter fact)
  - [ ] **AC3**: exact-term query (e.g. "Alex") ranks the matching fact high
  - [ ] **AC4**: `agent_runs` shows a `recall` / gemini-embedding-001 row
  - [ ] **AC5**: `--limit N` caps results
  - [ ] **AC6**: gibberish query → "No matching facts."

## Done

- [ ] All 7 acceptance criteria checked
- [ ] **[BARRY-ADMIN]** final commit "Phase 3.3: recall CLI online"
- [ ] Pushed; memory updated; PHASE-3.4.md created

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

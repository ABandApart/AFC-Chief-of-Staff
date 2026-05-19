# Phase 2 Checklist

Use this as the live tracking document during the Phase 2 build. Each item
maps to an acceptance criterion in `architecture/PRD-phase-2.md`.

## Pre-flight

- [x] Phase 1 complete (commit `51be3cb`)
- [x] `claude-api` skill loaded for current model + SDK guidance
- [x] PRD-phase-2.md drafted and reviewed

## Task 1: SDK dependencies

- [x] `anthropic>=0.80.0,<1.0` added to pyproject.toml
- [x] `google-genai>=2.0.0,<3.0` added
- [x] `pytest>=8.0` added (dev)
- [x] `pytest-mock>=3.12` added (dev)
- [x] `uv sync` succeeded
- [x] `uv.lock` updated and ready to commit

## Task 2: Cost helper module

- [x] `agents/_lib/runs.py` created (~370 lines)
- [x] `PRICE_TABLE`, `DAILY_CEILINGS`, `KEY_BY_AGENT` populated
- [x] Exceptions: `TokenCapExceeded`, `DailyCeilingExceeded`, `MissingAgentKeyError`
- [x] `_RunState` dataclass
- [x] `RunContext` with `call_anthropic`, `call_gemini`, `call_embedding`
- [x] `agent_run` context manager with G2 pre-check + finally-block INSERT
- [x] `agents/_lib/__init__.py` re-exports public API
- [x] Module imports cleanly (`uv run python -c "from agents._lib import ..."`)

## Task 3: Unit tests (mocked)

- [x] `tests/__init__.py` and `tests/test_runs.py` created
- [x] **AC1**: `test_successful_anthropic_call_writes_valid_row` passes
- [x] **AC1**: `test_successful_gemini_call_writes_valid_row` passes
- [x] **AC2**: `test_token_cap_exceeded_writes_failure_row` passes
- [x] **AC3**: `test_daily_ceiling_exceeded_writes_no_row` passes
- [x] **AC4**: `test_provider_error_writes_failed_row` passes
- [x] Edge case: `test_missing_agent_key_raises_before_call` passes
- [x] Edge case: `test_unknown_agent_raises_value_error` passes
- [x] Edge case: `test_correlation_fields_persisted` passes
- [x] `uv run pytest tests/test_runs.py -v` reports 8/8 passed
- [x] **AC10 ✓**: unit tests for 4 paths pass

## Task 4: Spend CLI

- [x] `cli/__init__.py` and `cli/spend.py` created
- [x] `--by agent | function | day` modes
- [x] `--since` parses `1h`, `30m`, `7d` formats
- [x] `uv run python -m cli.spend --help` shows usage

## Task 5: Smoke test agent

- [x] `agents/test/__init__.py` and `agents/test/run_smoke.py` created
- [x] 5 Anthropic + 5 Gemini calls with bounded inputs (~$0.005 total budget)
- [x] G1 trigger built in (deliberately tiny `max_input_tokens`)
- [x] DB verification step at the end
- [x] `phase-2-smoke` registered in `DAILY_CEILINGS` ($0.50) and `KEY_BY_AGENT` (uses Ted's key)

## Task 6: README

- [x] "Adding a new agent" section added to README.md
- [x] Documents the 3-step integration: slug → register → use
- [x] Includes example code block
- [x] **AC9 ✓**: README has integration pattern

## Task 7: Smoke test execution (barry-agent — user action)

- [x] As barry-agent: `cd ~/agents && git pull` (pulls Phase 2 changes)
- [x] As barry-agent: `uv sync` (picks up new deps from pyproject.toml)
- [x] As barry-agent: `uv run python -m agents.test.run_smoke` succeeds
- [x] Output includes "✅ Phase 2 smoke test PASSED"
- [x] As barry-agent: `uv run python -m cli.spend` shows phase-2-smoke rows
- [x] As barry-agent: `uv run python -m cli.spend --by function` shows infrastructure
- [x] **AC1 ✓**: 11 rows in agent_runs (10 success + 1 token_cap_exceeded)
- [x] **AC2 ✓**: 1 token_cap_exceeded row recorded with `input_tokens=1208`
- [x] **AC5 ✓**: usd_cost matches PRICE_TABLE (Anthropic 1512/194 → ~$0.0014; Gemini at sub-cent rounds to $0)
- [x] **AC6 ✓**: Anthropic dashboard groups usage by per-agent key (`anthropic-key-ted`)
- [x] **AC7 ✓**: 11 rows for phase-2-smoke (5 Anthropic success + 5 Gemini success + 1 G1)
- [x] **AC8 ✓**: spend CLI works in all three modes (by agent / function / day)

## Done

- [x] All 10 acceptance criteria checked
- [ ] Final commit: "Phase 2: telemetry primitives complete"
- [ ] Pushed to GitHub
- [ ] Memory file updated (Phase 2 done, Phase 3 next)
- [ ] Ready to start Phase 3 (Capture and Recall — Discord bot)

## Notes / issues encountered

- 2026-05-19: Used `google-genai` (new SDK) not `google-generativeai` (old). The new SDK has a different surface area (`genai.Client(...)` instead of `genai.configure()`); the helper reflects this.
- 2026-05-19: Banker's-rounding edge case in test assertion for $0.00035 → 0.0003 or 0.0004 depending on float. Relaxed the test to accept either.
- 2026-05-19: Python 3.14 has a `DeprecationWarning` from google-genai about `_UnionGenericAlias`. Not blocking; google-genai will fix.

-

-

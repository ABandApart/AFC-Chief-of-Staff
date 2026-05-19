# Phase 2: Telemetry Primitives — PRD & Build Instructions

<doc:meta>
  <doc:phase>2</doc:phase>
  <doc:theme>Telemetry primitives — cost helper, G1, G2</doc:theme>
  <doc:duration>~4 hours of work + ~$0.05 in smoke-test API spend</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>ready to execute</doc:status>
  <doc:depends_on>Phase 1 — Postgres + agent_runs table + barry-agent keychain</doc:depends_on>
  <doc:blocks>Phase 3 (Discord bot — first agent to use the helper for real work)</doc:blocks>
</doc:meta>

## TL;DR

A single Python module — `agents/_lib/runs.py` — that every agent uses to
call LLMs. It records one row in `agent_runs` per call, enforces G1
(per-run token cap) and G2 (per-day spend ceiling), and dispatches per-agent
Anthropic keys. Plus a CLI (`cli/spend.py`) for ad-hoc ledger inspection
and a smoke-test agent to prove the substrate works end-to-end.

**No real agents are built yet.** Phase 2 builds the enforcement plumbing
every later phase depends on.

---

## Goal & Non-Goals

<goals>

**Goal**: Every LLM call from every future agent must go through this one
module. Specifically:

1. A working `agent_run()` context manager that writes one `agent_runs` row
   per call with accurate input_tokens, output_tokens, and usd_cost.
2. G1 enforcement: pre-call token counting refuses any call where input >
   `max_input_tokens`, writes a `token_cap_exceeded` row, makes no API call.
3. G2 enforcement: on context entry, today's spend ≥ daily ceiling raises
   `DailyCeilingExceeded`. No agent_runs row written for refused-before-start
   runs (correct, since no call happened).
4. Per-agent Anthropic key dispatch: `call_anthropic` looks up the right
   keychain item via `KEY_BY_AGENT`. Agents not registered fail loudly with
   `MissingAgentKeyError`.
5. Provider error capture: any exception inside the context writes a `failed`
   row with `error_text` populated.
6. `cli/spend.py` for per-agent, per-function, per-day spend inspection.

</goals>

<non_goals>

**Not in this phase**:

- **G3 (anomaly detection)** — Phase 11 (Ted's job; pure SQL + Python on
  agent_runs, no enforcement).
- **Prompt caching support** in the helper interface — Phase 4 work when
  Tartt introduces repeated-prefix call patterns. Phase 2 doesn't need it.
- **Higgins weekly dashboard** — Phase 11.
- **outcomes table writes** — Phase 3 (`/outcome` Discord slash command).
- **Real agents** — Phase 3 (Discord bot + Capture is the first agent).
- **G2 race condition fix** — single-process world in Phase 2/3 makes a
  race possible but bounded. SELECT … FOR UPDATE is Phase 12 hardening.
- **launchd scheduling** — earliest is Phase 3 (briefing skeleton, 6am).

</non_goals>

---

## Acceptance Criteria

<acceptance>

The phase is done when all of the following are true.

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | `agent_run()` writes one valid `agent_runs` row per successful call | Smoke test (or `SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 1`) shows row with accurate columns |
| AC2 | G1 token cap exceeded — over-cap call refused; `token_cap_exceeded` row written; no API call made | Smoke test triggers it; row visible; mocked test asserts `messages.create` not called |
| AC3 | G2 daily ceiling exceeded raises `DailyCeilingExceeded` and writes NO row | Mocked test asserts no INSERT happens |
| AC4 | Provider errors recorded as `status='failed'` with `error_text` | Mocked test asserts row written with status=failed and error captured |
| AC5 | `usd_cost` matches `PRICE_TABLE` for successful calls | Manual check on smoke test rows (input_tokens × rate matches recorded cost) |
| AC6 | Per-agent Anthropic key dispatch works | Anthropic dashboard shows usage on the right key after smoke test (manual check) |
| AC7 | Smoke run: 5 Gemini Flash + 5 Claude Haiku calls; all 10 rows present, non-null tokens/cost; plus 1 token_cap_exceeded row from G1 trigger | `SELECT COUNT(*) FROM agent_runs WHERE agent_name='phase-2-smoke'` returns ≥ 11 |
| AC8 | `cli/spend.py` reports per-agent, per-function, per-day | Run all three modes after smoke test; output shows the smoke rows |
| AC9 | README has "Adding a new agent" section with integration pattern | `grep -A 5 "Adding a new agent" README.md` |
| AC10 | Unit tests for 4 paths pass (mocked, no real API calls) | `uv run pytest tests/test_runs.py` returns exit 0 with ≥4 passes |

</acceptance>

---

## Deliverables Manifest

```
aiadaptive-cos/
├── pyproject.toml                                  # +anthropic, +google-genai, +pytest
├── uv.lock                                         # regenerated
├── agents/
│   ├── _lib/
│   │   ├── __init__.py                             # re-exports public API
│   │   └── runs.py                                 # the cost helper — heart of this phase
│   └── test/
│       ├── __init__.py
│       └── run_smoke.py                            # 5+5 real API smoke test
├── cli/
│   ├── __init__.py
│   └── spend.py                                    # spend query CLI
├── tests/
│   ├── __init__.py
│   └── test_runs.py                                # 8 mocked unit tests
├── architecture/
│   └── PRD-phase-2.md                              # this file
└── checklists/
    └── CHECKLIST-phase-2.md                        # live tracking
```

README's "Adding a new agent" section documents the integration pattern.

---

## Architectural Decisions

These are encoded in the code; called out here for the record.

### 1. Single shared helper module, not a wrapper class

`agent_run()` is a free function returning a context manager; `RunContext`
exposes `call_anthropic`, `call_gemini`, `call_embedding`. No `CostHelper`
class with constructor and config — that would push connection management
into every agent and make per-call decisions harder to enforce.

### 2. Constants in code, not in DB

`PRICE_TABLE`, `DAILY_CEILINGS`, and `KEY_BY_AGENT` are Python dicts in
the helper module. Reasons:

- Prices change rarely (quarterly at most). Code review catches errors.
- No agent can dynamically inflate or deflate its reported cost.
- Adding a new agent forces a touch to `DAILY_CEILINGS` and (if using
  Anthropic) `KEY_BY_AGENT` — the registration is co-located with the
  code, not split across a DB seed file.

### 3. Per-agent Anthropic keys (carry-over from Phase 1)

Single shared `gemini-api-key` for Gemini (no per-agent split — Gemini
usage dashboard groups by API key anyway). For Anthropic, `KEY_BY_AGENT`
maps agent slug → keychain item name. Phase 2 ships with 6 agents wired
(per the keys Barry provided in Phase 1):
`ted`, `keeley-strategy`, `keeley-content`, `roy-kent`, `nate-shelley`,
`higgins`. Plus `phase-2-smoke` (piggybacks on Ted's key for the smoke run).

Agents added in later phases (sam, briefing, capture, meeting-processor,
fact-extraction, tartt) need an entry added when they're built. The
helper fails loudly with `MissingAgentKeyError` if an agent calls Anthropic
without a registered key.

### 4. G2 ceiling check via a fresh SELECT every entry

On context entry, we open a connection, sum today's `usd_cost` for this
agent, then close. Reasons:

- Phase 2 has no concurrent agents — the cost of a SELECT-per-call is
  negligible (<1ms locally).
- A cache would risk staleness; with 6+ agents writing rows, an in-memory
  cache would need invalidation across processes, which we don't have yet.
- Race window: two concurrent calls each see "under ceiling," both proceed,
  both push over. Bounded by G1 (each call's input cap) so total overshoot
  is small. Phase 12 hardening adds SELECT … FOR UPDATE.

### 5. Single connection per ledger write (no pooling)

Each `agent_run` exit opens a connection, writes one row, closes. A
connection pool is a Phase 12 hardening item — premature for Phase 2.

### 6. Ledger write in `finally` — always writes, even on failure

The INSERT happens in a `finally` block. If the agent's logic raises,
the row still lands with `status='failed'` and `error_text` populated.
If the INSERT itself fails (e.g. Postgres down), we log to stderr and
let the original exception propagate — never mask the agent's actual
failure with a ledger-write failure.

### 7. Token cap is the input cap; output cap goes straight to provider

`max_output_tokens` is passed to the provider's `max_tokens` field. The
provider enforces it. We don't pre-check it because there's no way to
predict output length before the call.

### 8. Embedding tokens estimated as `char_count // 4`

Gemini's embedding API doesn't return token counts the same way as
generate-content. A standard rough estimate (4 chars/token) is good enough
for `text-embedding-004` cost tracking, especially since the model is free
tier (cost is always 0).

---

## Risk Register

<risks>

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Anthropic SDK version drift | Low | Low | Minor version pinned in pyproject.toml; bump deliberately |
| google-genai vs google-generativeai confusion | Medium | Medium — would break imports | Use `google-genai` (new SDK); old SDK package name is `google-generativeai` |
| Token count differs from actual usage (provider counter vs SDK estimator) | Low | Low | The G1 check uses Anthropic's `count_tokens` API (same counter the provider uses); Gemini uses native API; embeddings use the char/4 heuristic but cost is 0 |
| Cost computed in float drifts from `NUMERIC(10,4)` schema | Low | Low | Round to 4 decimals at write time; schema enforces 4dp |
| G2 race on concurrent calls (bounded by G1) | Low (currently) | Low | Phase 12 hardening with SELECT … FOR UPDATE if/when we have concurrency |
| MissingAgentKeyError surprise when first running a new agent | Low | Low (developer-time error, not production) | Documented in README; the error message names the missing entry explicitly |
| barry-agent loses access to barry-admin's `/opt/homebrew/bin/uv` | Low | Low | World-executable; PATH was added to barry-agent's .zshrc in Phase 1 |
| New Python 3.14 / dep-resolution edge cases | Low | Low | Tests pass on 3.14; if anything breaks, pin Python in pyproject and revisit |

</risks>

---

## Task Breakdown (sequenced)

<tasks>

### Task 1: Add SDK dependencies

Update `pyproject.toml`:
- `anthropic>=0.80.0,<1.0`
- `google-genai>=2.0.0,<3.0`
- `pytest>=8.0` (dev)
- `pytest-mock>=3.12` (dev)

Run `uv sync`. Commit `uv.lock`.

### Task 2: Write the cost helper

`agents/_lib/runs.py`:
- `PRICE_TABLE`, `DAILY_CEILINGS`, `KEY_BY_AGENT` constants
- `TokenCapExceeded`, `DailyCeilingExceeded`, `MissingAgentKeyError` exceptions
- `_keychain_get`, `_db_url` private helpers
- `_RunState` dataclass for accumulated state
- `RunContext` class with `call_anthropic`, `call_gemini`, `call_embedding`
- `@contextmanager agent_run(...)` with G2 pre-check and finally-block ledger write

`agents/_lib/__init__.py`: re-export public API.

### Task 3: Write mocked unit tests

`tests/test_runs.py`. At minimum:
- `test_successful_anthropic_call_writes_valid_row` (AC1)
- `test_successful_gemini_call_writes_valid_row` (AC1)
- `test_token_cap_exceeded_writes_failure_row` (AC2)
- `test_daily_ceiling_exceeded_writes_no_row` (AC3)
- `test_provider_error_writes_failed_row` (AC4)
- `test_missing_agent_key_raises_before_call` (KEY_BY_AGENT dispatch)
- `test_unknown_agent_raises_value_error` (DAILY_CEILINGS validation)
- `test_correlation_fields_persisted` (round-trip check)

Run `uv run pytest tests/test_runs.py -v` — expect all pass.

### Task 4: Write the spend CLI

`cli/spend.py`:
- `--by agent | function | day` (default: agent)
- `--since 1h | 7d | …` (default: 24h)
- Uses `_keychain_get("db-url")` so no env var fiddling

Sanity check: `uv run python -m cli.spend --help`.

### Task 5: Write the smoke test agent

`agents/test/run_smoke.py`:
- 5 Claude Haiku 4.5 calls, 5 Gemini Flash calls (paragraphs from the
  architecture docs as test input)
- 1 deliberate G1 trigger (impossibly low `max_input_tokens`)
- DB verification step at the end

Add `phase-2-smoke` to `DAILY_CEILINGS` with $0.50/day cap and to
`KEY_BY_AGENT` pointing at `anthropic-key-ted` (smoke reuses Ted's key
to avoid creating a dedicated key for a transient test agent).

### Task 6: Update README

Add "Adding a new agent" section to `README.md`. Documents the three-step
integration: pick slug → register in `runs.py` → use `agent_run()`.

### Task 7: Run the smoke test (barry-agent)

This task is **executed by the user as `barry-agent`**, not by Claude as
`barry-admin`. The keys live in barry-agent's keychain only.

Steps as barry-agent:
```bash
cd ~/agents
git pull
uv sync       # picks up new deps from pyproject.toml
uv run python -m agents.test.run_smoke
```

Expected output: 5+5 summaries, 1 G1 trigger, `✅ Phase 2 smoke test PASSED`.

Verification queries (from barry-agent, after the smoke test):
```bash
uv run python -m cli.spend                # today by agent
uv run python -m cli.spend --by function  # today by function
```

Both should show the `phase-2-smoke` rows with non-zero tokens and cost.

### Task 8: Sanity check + final commit

- `git status` clean
- All ACs marked in CHECKLIST-phase-2.md
- Commit `"Phase 2: telemetry primitives complete"` and push

</tasks>

---

## Definition of Done

<dod>

Phase 2 is complete when:

1. All 10 acceptance criteria pass.
2. The repo's `main` branch contains a commit `"Phase 2: telemetry primitives complete"`.
3. You can answer: where does cost get computed? Where does G1 / G2 run?
   What happens if I add an Anthropic agent without a key? (Answer to all:
   `agents/_lib/runs.py`.)
4. No agent code has been written beyond the smoke test. (Discord bot is Phase 3.)

</dod>

---

## What Phase 3 Will Do With This

Phase 3 (Capture and Recall) builds the Discord bot and the first
production usage of the cost helper. Specifically:

- `agents/discord-bot/cogs/capture.py` — listens to #capture, calls fact
  extraction via `agent_run("fact-extraction", "infrastructure")` →
  `run.call_anthropic(model="claude-haiku-4-5", ...)`.
- `agents/discord-bot/cogs/briefing.py` — daily 6am briefing skeleton via
  `agent_run("briefing", "action_surfacing")`.
- Adds `anthropic-key-fact-extraction` and `anthropic-key-briefing` to
  barry-agent's keychain; adds matching `KEY_BY_AGENT` entries to `runs.py`.

No further changes to the cost helper itself in Phase 3 — only new entries
in the registries.
</doc:meta>

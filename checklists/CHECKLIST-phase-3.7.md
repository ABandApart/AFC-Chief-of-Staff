# Checklist — Phase 3.7: Memory migration (cognee)

Track B of the target-state migration (`architecture/26-cognee-migration-plan.md`).
The cognee pivot is a **GO** (2026-07-28). Mitigations **M1** (telemetry via
litellm routing) and **M2** (embedding normalization) are baked into the
workstreams, not appended.

## W1 — Telemetry re-plumb (M1) · ~1.5–2 days

- [x] **W1.1** — `agents/_lib/telemetry_context.py`: contextvar `labeled()` +
  litellm callback writing conformant `agent_runs` rows
  (`correlation_kind='cognify_run'`). Additive, non-breaking. (commit `7aa632d`)
- [x] **W1.2** — Deprecated pre-flight refusal + per-agent keys in `runs.py`:
  **G1 removed** (no `count_tokens`, no `max_input_tokens`, no `TokenCapExceeded`);
  **`KEY_BY_AGENT`/`MissingAgentKeyError` removed** → one `anthropic-api-key`;
  **G2 reframed as a soft breaker** — `assert_under_ceiling()` (extracted, also
  callable before a cognee op) blocks the next invocation once over. Callers
  updated (`capture.py`, `run_smoke.py`, `_lib/__init__.py`); gate tests rewritten
  (AC2/missing-key deleted, breaker + direct-`assert_under_ceiling` tests added).
  Suite 82/82.
- [x] **W1.3** — `cli/reconcile.py`: ledger-vs-provider spend compare. Ledger
  side automated (by provider, per window); provider side operator-supplied
  (`--anthropic`/`--gemini` from the dashboards — automating the pull needs
  org-admin billing creds we don't assume). Flags divergence beyond `--tolerance`
  (default 15%, or a $0.01 absolute floor for tiny figures); exit 1 on divergence
  so it can back a monthly routine. Pure logic unit-tested (7); ledger query
  verified via socket. Suite 89/89.

**W1 (telemetry re-plumb) COMPLETE.** Next: **W2** — cognee stand-up on local
Postgres with the M1 routing.

> ⚠️ **Runtime prerequisite before deploying W1.2 (barry-agent):** per-agent
> Anthropic keys are gone — the runtime keychain needs a single **`anthropic-api-key`**
> (point it at the existing key or a fresh one). Without it, capture/agents fail
> the keychain lookup. The old `anthropic-key-*` items become unused. Ties into
> the still-open H4 key rotation. Gemini unchanged (`gemini-api-key`).

## W2 — Cognee stand-up on local Postgres · ~1–2 days

**Builder-side DONE.** Runtime smoke pending (barry-agent, `PHASE-3.7-W2.md`).

- [x] Pinned `cognee[postgres]==1.4.0` in a `cognee` dependency group (not
  default-synced; the heavy tree + psycopg2 build stay out of the dev/CI env).
  ⚠️ locking it nudged a shared transitive (`websockets` 16→15.0.1) — tests pass.
- [x] `agents/_lib/cognee_setup.py`: `build_cognee_env` (pure, tested) +
  `configure_cognee()` — dedicated **`aiadaptive_cognee`** DB (created by
  barry-admin, `vector`+`pg_trgm`), all three stores point there,
  `ENABLE_BACKEND_ACCESS_CONTROL=false`, per-store creds, **M1 routing**
  (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/claude-haiku-4-5`), embedder kept
  (`gemini/gemini-embedding-001` @768), installs the litellm callback.
- [x] `agents/test/cognee_smoke.py` — runtime smoke (cognify 2 docs → graph query
  → confirm ledger got `cognify_run` rows). `tests/test_cognee_setup.py` (4).
  Suite 93/93, lint clean, imports without cognee present.
- [x] **Runtime (barry-agent):** `anthropic-api-key` provisioned; `uv sync --group
  cognee` clean (OpenSSL/libpq flags fixed the psycopg2 build); smoke **GREEN**
  (2026-07-28). cognify + `GRAPH_COMPLETION` + M1 ledger all pass.

**W2 COMPLETE — runtime-proven (2026-07-28).** cognee 1.4.0 stands up on local
Postgres (graph provider `postgres`, no AGE). Graph query returned the correct
2-hop answer for both firms. **M1 confirmed in production:** the ledger captured
18 `cognify_run` calls (anthropic 5 = $0.0095, gemini 13 = $0.0001), agent
`cognee`, per-doc correlation intact — the native adapter would have logged 0
Anthropic calls, so the litellm routing is doing its job. Peak RSS ~466 MB.
⚠️ `aiadaptive_cognee` still holds smoke data — barry-admin prunes/recreates it
before W4 go-live. ⚠️ bot still on pre-sync modules; the `websockets==15.0.1`
pin takes effect on its next restart — health-check then. Next: **W3** (DataPoints).

## W3 — Domain modeling as DataPoints · ~2–3 days

**Builder-side DONE.** Runtime shape-check folds into W4 validation.

- [x] `agents/_lib/ontology.py`: 8 knowledge DataPoints — Organization, Person,
  Fact, Decision, Meeting, ICPSignal, ContentItem, InterestSignal — with typed
  relationship fields (edges) and `metadata["index_fields"]`. Added Organization
  beyond the plan's 7 (the entity-resolution target — "everything about Acme").
  cognee-or-pydantic-fallback base so the classes import + test without cognee.
- [x] **Entity↔operational boundary drawn** (ontology docstring): knowledge → the
  graph; operational state (prospects, tasks, follow_ups, content_pipeline,
  approval_queue, buffer_posts, outcomes, agent_runs, dashboard, sources) stays
  SQL; cross-links via a cognee node-id **TEXT** column on the SQL side, joined in
  app code (no cross-DB FK). Those columns land in W4/W5, not W3.
- [x] `tests/test_ontology.py` (9 structural tests incl. a 2-hop chain); suite
  102/102, lint clean.
- [ ] **Runtime shape-check** (`agents/test/ontology_shape.py`): construct a
  structured example → `add_data_points` → 2-hop `GRAPH_COMPLETION`. ⚠️ verify the
  `cognee.low_level.add_data_points` API against 1.4.0. Run with W4 (barry-agent).

## W4 — Capture rewrite · ~1 day

**Builder-side DONE.** Live validation at the W7 deploy.

- [x] **Mode-1 decision:** capture ingests via `cognee.add(text)` + `cognify()`
  (cognee extracts + resolves entities — proven in W2), not our DataPoints.
  Rationale: lowest API risk, plan-literal, free entity resolution. The typed
  ontology (W3) serves the structured agents (meetings/content), not free-text
  capture.
- [x] `cogs/capture.py` rewritten: hash-dedup (kept) → `labeled("fact-extraction")`
  → add + cognify. Dropped the forced-tool extraction, embedding, cosine-0.95
  dedup, facts-table insert, and the parse/validate/format helpers.
- [x] `run.py`: `configure_cognee()` at startup (installs the M1 callback + env
  before any capture).
- [x] `brain.py`: removed `insert_facts`/`find_near_duplicate`; kept the
  capture-hash guard, `insert_outcome`, and `search_facts` (transitional —
  rewired to the graph in W5).
- [x] Migration **0004** (applied): `outcomes.attributed_fact_node TEXT`
  (`attributed_fact_id` kept until W5). The entity↔operational boundary in action.
- [x] `test_capture.py` trimmed to `message_hash`. Suite 86/86, lint clean.
- ⚠️ **UX changes:** reply is now a plain "Captured to memory" (no fact echo);
  no 🤔 "nothing to remember"; ✅ is slower (cognify ~6–40s, but it's a background
  reaction); no near-dup message (cognee resolves nodes).
- [ ] **Live validation (barry-agent, W7):** post in #capture → cognified into
  `aiadaptive_cognee`; exact re-post skipped pre-cognify; ledger shows spend
  under `fact-extraction`. cognee behavior itself already proven in W2.

## W5 — Recall rewrite + M2 · ~1–1.5 days   ← NEXT

Build against these confirmed facts (cognee 1.4.0, verified 2026-07-28):
`from cognee import SearchType` → `SearchType.GRAPH_COMPLETION`; `cognee.search(...)`
and `cognee.add/cognify` are async; `configure_cognee()` (from
`agents/_lib/cognee_setup`) must run once before any search — the bot does it in
`run.py setup_hook`, so a **CLI must call it at startup too**.

- [ ] New `agents/_lib/graph_recall.py`: `async recall(query, limit) ->` results
  via `cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=query)`.
  Replaces `agents/_lib/search.py` (RRF `HYBRID_SQL` over the facts table). No
  query-embedding step (cognee handles retrieval internally).
- [ ] Rewrite `cli/recall.py` (calls `configure_cognee()` then `graph_recall`;
  it's async now) and `cogs/recall.py` (`_search` → the graph call). Drop
  `agents/_lib/search.py` + `tests/test_recall.py` (RRF-specific) — or repoint
  them at `graph_recall`.
- [ ] **M2 — re-scope for mode-1:** cognee owns retrieval, so our pgvector-cosine
  fix doesn't apply directly. Instead **verify cognee's recall quality** with its
  un-normalized 768-dim Gemini vectors (spike found norm ≈ 0.58). If weak,
  configure cognee's embedding normalization or distance metric (env/config), or
  re-embed. This is a runtime quality check, not an on-write code fix.
- [ ] **`/outcome` rewire:** `brain.search_facts` (facts-table autocomplete) →
  a cognee search returning fact node-ids; `OutcomeModal` writes
  `attributed_fact_node` (TEXT, migration 0004 done) not `attributed_fact_id`.
  ⚠️ verify how to search for fact nodes + read their ids in the API.
- [ ] `cli/publish_playbooks.py` — git→cognee publish of `publish_to_memory: true`
  playbooks into a dedicated **trusted `playbooks` dataset** (`cognee.add(..., 
  dataset_name="playbooks")` + `cognify`); agent retrieval scoped to that dataset
  only (B1). Carried from Phase 3.6.
- [ ] **Old facts:** the 2 pre-pivot rows (`facts` #3/#4) are orphaned once recall
  is graph-native — cognify them into the graph or drop the `facts` table (trivial
  at 2 rows). Decide + do at W5 or W7.
- [ ] Tests for the pure bits (result formatting); runtime capture→recall loop is
  W7 live validation.

## W6 — Docs + PRDs · ~2 days

- [ ] Rewrite `30-memory-layer.md`: the `facts`/vectorized-tables schema + RRF
  hybrid search → cognee graph (DataPoints in `agents/_lib/ontology.py`), the
  `aiadaptive_cognee` DB, and the entity↔operational boundary (node-id TEXT
  columns, join in app code, no cross-DB FK).
- [ ] Rewrite `80-telemetry-layer.md`: G1/G2 pre-flight + per-agent keys are gone
  → labeling (`_lib/telemetry_context`, M1) + soft breaker (`assert_under_ceiling`)
  + `cli/reconcile`. Single `anthropic-api-key`.
- [ ] Decision-log entry in `70-build-order.md`: migration complete (mode-1
  capture, graph recall, M1/M2 outcomes).
- [ ] Note: PRDs for Phases 4/7/8/10 don't exist yet — they inherit the graph
  model when written (not a W6 rewrite target).

## W7 — Validate + redeploy · ~1–1.5 days (barry-agent runtime)

Prereqs already done: `anthropic-api-key` provisioned; migration 0004 applied to
`aiadaptive_cos` (socket); `aiadaptive_cognee` pruned empty. Consider bundling
with the **3.6 scheduler cutover** and the **3.5 3c** log check (all pending
runtime).

- [ ] barry-agent: `git pull`; `uv sync --group cognee` (OpenSSL/libpq build
  flags — see PHASE-3.7-W2.md); expect `websockets==15.0.1`.
- [ ] Restart the bot (`launchctl kickstart …discord-bot`) — picks up
  `configure_cognee()` at startup, mode-1 capture, graph recall, and the
  websockets pin. **Health-check** after (pin only applies on restart).
- [ ] Live validation: post in `#capture` → cognified into `aiadaptive_cognee`;
  exact re-post skipped pre-cognify; `/recall` returns a graph answer; `/outcome`
  links a fact node; `agent_runs` shows the spend (agent `fact-extraction`,
  `cognify_run`); `cli/reconcile` matches. Run `agents/test/ontology_shape.py`
  (structured-ingestion path) once too.
- [ ] Migrate/drop the 2 old `facts` rows if not done in W5.

## Still-open runtime items (carry across the migration)

- 3.5 close-out: barry-agent 3c log-line check.
- 3.6 scheduler cutover: bootout briefing+pg-backup plists, bootstrap scheduler
  (CHECKLIST-phase-3.6.md) — not before 3.5 runtime closed; don't run old+new.
- H4: rotate `anthropic-key-*` → the single `anthropic-api-key` (postponed).

## Rollback

If an assumption breaks mid-migration, fall back to **Option C** (entities + join
table in existing Postgres, keep the cost helper — ~3–5 days). W3 modeling largely
transfers.

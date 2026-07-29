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
- ✅ **UX changes ACCEPTED by operator (2026-07-28):** reply is a plain "Captured
  to memory" (no fact echo), no 🤔, slower background ✅. Rationale: Discord isn't
  the primary ingestion channel (it's for status/queries/kickoffs/digest); API +
  tools are primary, so the intake confirmation is an easy trade. **Mode-1 is
  final — no hybrid.**
- [x] **Ingest core extracted** → `agents/_lib/ingest.py ingest_note(text, *,
  source_ref, source_type)` (hash-dedup + capture_messages moved here from
  brain; labeled add+cognify). `cogs/capture.py` is now a thin caller; the API
  endpoint (Track C) will share it.
- [ ] **Live validation (barry-agent, W7):** post in #capture → cognified into
  `aiadaptive_cognee`; exact re-post skipped pre-cognify; ledger shows spend
  under `fact-extraction`. cognee behavior itself already proven in W2.

## W5 — Recall rewrite + M2 · ~1–1.5 days   ← BUILDER-SIDE DONE

**All builder-side W5 done.** Only **M2** remains, and it's a *runtime* recall-
quality check (folds into W7). `/outcome` fact-link and the legacy `facts` table
were **removed** (operator decision, below), not rewired.

Build against these confirmed facts (cognee 1.4.0, verified 2026-07-28):
`from cognee import SearchType` → `SearchType.GRAPH_COMPLETION`; `cognee.search(...)`
and `cognee.add/cognify` are async; `configure_cognee()` (from
`agents/_lib/cognee_setup`) must run once before any search — the bot does it in
`run.py setup_hook`, so a **CLI must call it at startup too**.

- [x] `agents/_lib/graph_recall.py`: `async recall(query, limit) ->` results
  via `cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=query)`.
  Replaces `agents/_lib/search.py` (RRF `HYBRID_SQL` over the facts table). No
  query-embedding step (cognee handles retrieval internally).
- [x] Rewrote `cli/recall.py` (calls `configure_cognee()` then `graph_recall`;
  it's async now) and `cogs/recall.py` (`_search` → the graph call). Drop
  `agents/_lib/search.py` + `tests/test_recall.py` (RRF-specific) — or repoint
  them at `graph_recall`.
- [ ] **M2 — re-scope for mode-1:** cognee owns retrieval, so our pgvector-cosine
  fix doesn't apply directly. Instead **verify cognee's recall quality** with its
  un-normalized 768-dim Gemini vectors (spike found norm ≈ 0.58). If weak,
  configure cognee's embedding normalization or distance metric (env/config), or
  re-embed. This is a runtime quality check, not an on-write code fix.
- [x] **`/outcome` fact-link REMOVED** (operator decision 2026-07-28: "drop the
  link", not rewire). In the graph model facts are auto-extracted nodes (UUIDs),
  not numbered rows — no stable id to autocomplete/link. So: dropped the `fact`
  param + autocomplete + modal `fact_id` from `cogs/outcomes.py`; removed
  `search_facts` and the `attributed_fact_id` arg from `brain.insert_outcome`.
  Outcomes stand on their own description. Both fact-link columns retired in 0006.
- [x] `cli/publish_playbooks.py` — git→cognee publish of `publish_to_memory: true`
  playbooks into a dedicated **trusted `playbooks` dataset** (`cognee.add(...,
  dataset_name="playbooks")` + `cognify`) under `labeled("playbook-publish", …)`;
  agent retrieval scoped to that dataset only (B1). **Hash-idempotent** —
  migration **0005** `playbook_publications(name, content_hash)` in
  `aiadaptive_cos` tracks the last-published hash so re-runs only re-cognify
  changed/new playbooks (`--force` overrides; `--dry-run` previews). Two seeds
  flagged (`discovery-call-to-proposal`, `prospect-qualification`). Pure logic
  tested (8). ⚠️ known gap: a *changed* playbook re-cognifies but old-version
  nodes aren't deleted (cognee dataset node-delete API — verify at W7). ⚠️
  **runtime:** barry-agent applies migration 0005 + runs the CLI once at W7.
  Carried from Phase 3.6.
- [x] **Old facts DROPPED** (operator decision 2026-07-28: "just drop them", not
  cognify — the 2 pre-pivot rows were stale/test data). Migration **0006** drops
  the whole `facts` table + both `outcomes` fact-link columns
  (`attributed_fact_id`, `attributed_fact_node`). No dangling link — the one real
  outcome (#5) referenced no fact. **Dependent cleanups done same-change:** the
  briefing's status line moved off `facts` → `capture_messages` ("Notes
  captured"); `scripts/smoke_test.py` write-test moved off `facts` →
  `capture_messages` + table list synced; `verify_schema.sql` refreshed to 19
  tables (−facts, +capture_messages, +playbook_publications). ⚠️ **runtime:**
  barry-agent applies migration 0006 at W7.
- [x] Pure tests: `test_ingest.py` (message_hash) + `test_graph_recall.py`
  (answer normalizer). Removed `_lib/search.py` + test_recall/test_capture.
  Suite 85/85. Runtime capture→recall loop = W7.

## W6 — Docs + PRDs · ~2 days   ← DONE

- [x] Rewrote `30-memory-layer.md`: recast around the **two-store model** (cognee
  graph `aiadaptive_cognee` for knowledge; operational SQL `aiadaptive_cos`).
  Removed the `facts` table + RRF hybrid-search pattern; added DataPoints
  (`_lib/ontology.py`), the two ingestion modes (mode-1 capture / structured
  `add_data_points`), datasets + B1 trust, GraphRAG recall, M1/M2 notes, and the
  entity↔operational boundary (node-id TEXT columns, no cross-DB FK). Corrected
  the operational schema (dropped fact columns, `usd_cost NUMERIC(14,8)`) and
  flagged the still-SQL knowledge tables (content_items/…/meeting_transcripts)
  as pending migration to the graph by their phases.
- [x] Rewrote `80-telemetry-layer.md`: G1 (per-run cap) removed, G2 reframed as
  the **soft breaker** (`assert_under_ceiling`), per-agent keys gone → single
  `anthropic-api-key`. Added the **labeling path** (`_lib/telemetry_context`, M1,
  litellm-routing rationale) and the **monthly `cli/reconcile`** backstop.
  Corrected the ledger DDL (no `token_cap_exceeded`), function-label table
  (cognee agents), helper interface (no `max_input_tokens`), ceilings table,
  Ted/Higgins references, and the outcomes DDL/modal (fact-link removed).
- [x] Decision-log entry in `70-build-order.md`: "Cognee migration — builder-side
  complete (W1–W6)".
- [x] **Backup gap closed (found during W6):** `scripts/pg_backup.sh` dumped only
  `aiadaptive_cos` — the graph in `aiadaptive_cognee` was unprotected. Now dumps
  **both** DBs (cognee DSN = db-url with dbname swapped); `nightly-backup` loop
  desc updated. Runtime picks it up at the next 2:00 run post-deploy.
- [x] Note: PRDs for Phases 4/7/8/10 don't exist yet — they inherit the graph
  model when written (not a W6 rewrite target).

## W7 — Validate + redeploy · **DEPLOYED (barry-agent, 2026-07-28)**

**Phase 3.7 is live in production** (`main`@`e8369d3`). Handback:
`/Users/Shared/afc-richmond/PHASE-3.7-W7.md`. All tasks green; the load-bearing
order (restart onto new code → smoke → drop `facts`) held.

- [x] `git pull` → `e8369d3`; `uv sync --group cognee` (OpenSSL/libpq flags)
  clean; `websockets==15.0.1`; suite 93 passed.
- [x] Bot restarted (`launchctl kickstart -k …discord-bot`) → **PID 82622**;
  `configure_cognee()` ran at startup (M1 callback line in the log), gateway
  clean on websockets 15.0.1 (closes the W2 open item), no errors.
- [x] Smoke (TASK 3, before the drop): `#capture` → "Captured to memory"
  (mode-1), exact re-post skipped, `/recall` graph answer, ledger shows
  `fact-extraction` + `recall` spend. **Then** migrations 0005 + 0006 applied →
  `facts` dropped, **19 tables**, `playbook_publications` present (pre-drop
  safety dump of the 2 rows at `~/w7_predrop_safety/`).
- [x] `cli.publish_playbooks` seeded 2 playbooks (re-run 0 — hash-idempotent).
  ⚠️ **gotcha:** playbook cognify is slow (~1–2 min/doc) — run it detached, not
  in a short-timeout foreground.
- [x] `/outcome` #8 recorded with no fact-link, no error; `cli.reconcile`
  ledger-only view clean (MTD ~$0.068).
- [x] **M2 recall-quality gate PASSED** — operator judged recall usefully
  accurate; the un-normalized 768-dim Gemini vectors caused no visible retrieval
  problem, so no `cognee_setup.py` normalization/distance change is needed now.
- [x] Both DBs back up (`aiadaptive_cos` + `aiadaptive_cognee` dumps confirmed).

## Still-open runtime items

- [x] **3.5 close-out (3c/F4):** recall log-lines confirmed (query + result count).
- [x] **3.6 scheduler cutover:** `com.aiadaptive.cos.scheduler` bootstrapped
  (PID 85122, running `morning-briefing` + `nightly-backup`); the `briefing` +
  `pg-backup` calendar plists `bootout` **and** `disable`d (durable, no
  double-fire on login); `discord-bot` untouched. Backups now run under the
  scheduler's `nightly-backup` loop (same dual-DB script).
- [ ] **H4** (operator/barry-agent): rotate `anthropic-api-key` + retire the old
  `anthropic-key-*` items, now that mode-1 is live on the single key.
- [ ] **Cleanup (non-blocking):** prune any `w2_smoke` leftover from
  `aiadaptive_cognee`; delete the disabled `briefing`/`pg-backup` plist files
  from `~/Library/LaunchAgents` (they're disabled, so optional).
- [ ] **Monthly:** run `cli.reconcile --anthropic <$> --gemini <$>` with the
  provider-dashboard figures for the real divergence check.

## Rollback

If an assumption breaks mid-migration, fall back to **Option C** (entities + join
table in existing Postgres, keep the cost helper — ~3–5 days). W3 modeling largely
transfers.

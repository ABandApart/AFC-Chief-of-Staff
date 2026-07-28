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
- [ ] **W1.3** — `cli/reconcile.py`: monthly ledger-vs-provider spend compare
  (the safety net for dropping the hard gate). **NEXT.**

> ⚠️ **Runtime prerequisite before deploying W1.2 (barry-agent):** per-agent
> Anthropic keys are gone — the runtime keychain needs a single **`anthropic-api-key`**
> (point it at the existing key or a fresh one). Without it, capture/agents fail
> the keychain lookup. The old `anthropic-key-*` items become unused. Ties into
> the still-open H4 key rotation. Gemini unchanged (`gemini-api-key`).

## W2 — Cognee stand-up on local Postgres · ~1–2 days

- [ ] Add pinned `cognee[postgres]`; handle the `psycopg2` build (openssl@3/libpq
  flags or `psycopg2-binary`). Config: graph provider `postgres`,
  `ENABLE_BACKEND_ACCESS_CONTROL=false`, per-store `VECTOR_DB_*`/`GRAPH_DATABASE_*`
  creds, **M1 routing** (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…`). Cognee
  stores in a dedicated schema/db, isolated from the operational tables.

## W3 — Domain modeling as DataPoints · ~2–3 days

- [ ] Fact/Person/Decision/Meeting/ICPSignal/ContentItem/InterestSignal as
  `DataPoint` classes; draw the entity↔operational boundary (graph node-id as
  TEXT on the SQL side, joined in app code, no cross-boundary FK).

## W4 — Capture rewrite · ~1 day

- [ ] `#capture` → `cognee.add()`+`cognify()` under `labeled()`; keep the
  message-hash short-circuit (`capture_messages`), drop the cosine-0.95 layer.

## W5 — Recall rewrite + M2 · ~1–1.5 days

- [ ] Replace RRF `HYBRID_SQL`/`_lib/search.py` with cognee `GRAPH_COMPLETION`;
  rewrite `cli/recall.py` + `/recall` cog. **M2**: renormalize the 768-dim Gemini
  vectors, or use pgvector cosine `<=>` only (+ regression test).
- [ ] `cli/publish_playbooks.py` — git→cognee publish of `publish_to_memory`
  playbooks into the trusted `playbooks` dataset (carried from Phase 3.6).

## W6 — Docs + PRDs · ~2 days

- [ ] Rewrite `30-memory-layer.md`, `80-telemetry-layer.md`, and the Phase
  4/7/8/10 PRDs to the graph model + new telemetry model.

## W7 — Validate + redeploy · ~1–1.5 days

- [ ] Runtime pull/sync (large dep delta), restart bot, re-drive capture/recall,
  confirm the ledger fills + reconcile matches. Coordination file for barry-agent.

## Rollback

If an assumption breaks mid-migration, fall back to **Option C** (entities + join
table in existing Postgres, keep the cost helper — ~3–5 days). W3 modeling largely
transfers.

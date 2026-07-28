# Checklist — Phase 3.7: Memory migration (cognee)

Track B of the target-state migration (`architecture/26-cognee-migration-plan.md`).
The cognee pivot is a **GO** (2026-07-28). Mitigations **M1** (telemetry via
litellm routing) and **M2** (embedding normalization) are baked into the
workstreams, not appended.

## W1 — Telemetry re-plumb (M1) · ~1.5–2 days

- [~] **W1.1** — `agents/_lib/telemetry_context.py`: contextvar `labeled()` +
  litellm callback that writes conformant `agent_runs` rows
  (`correlation_kind='cognify_run'`). **Additive, non-breaking — STARTED.**
- [ ] **W1.2** — Deprecate pre-flight refusal (G1 token cap, G2 hard ceiling) +
  per-agent keys (`KEY_BY_AGENT`) in `runs.py`; replace with a soft post-hoc
  ceiling + one key per subsystem. Rewrite the AC1–AC4 gate tests.
- [ ] **W1.3** — `cli/reconcile.py`: monthly ledger-vs-provider spend compare
  (the safety net for dropping the hard gate).

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

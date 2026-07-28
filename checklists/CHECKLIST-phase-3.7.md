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

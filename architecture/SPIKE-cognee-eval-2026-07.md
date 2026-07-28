# SPIKE — Cognee viability evaluation (2026-07)

**Purpose:** answer the small number of *empirically measurable* questions that
gate the cognee pivot, before committing the ~9–12 day migration. Convert the
two biggest unknowns (Postgres-graph viability, cognify cost) plus three
supporting ones into numbers, for ~10% of the migration cost.

**Type:** throwaway spike. Nothing here ships. All work in a scratch database
(`cognee_spike`) and a throwaway git branch (`spike/cognee`) — the production
`aiadaptive_cos` DB, the running bot, and `main` are untouched.

**Time-box:** **1 working day.** If a question can't be answered inside the box,
that *is* the answer (Q1/Q3 especially — an adapter that fights you for a day is
a red flag on its own). Stop at the box and record what's known.

**Prereq — profile:** the cognify calls need real provider keys + DB access,
which live in **barry-agent**'s keychain (`gemini-api-key`, an `anthropic-key-*`,
`db-url`). So the *harness* is built in barry-admin on the `spike/cognee` branch,
but the *runs* (G-steps) execute in the barry-agent session. Steps are tagged.

---

## Scope

**In:** stand cognee up on the existing local Postgres; cognify 5 representative
documents; measure cost, latency, RAM, graph-backend behavior, embedding
config, and telemetry-label propagation. Write a one-page findings block back
into this file.

**Out (explicitly not this spike):** domain DataPoint modeling (W3), capture/recall
rewrites (W4/W5), doc rewrites (W6), the telemetry re-plumb itself (W1). Those
are the migration; this only decides whether the migration is worth starting.

---

## The five gating questions, each with a decision threshold

Each question has a **green / yellow / red** threshold. The rule: **any red
kills the full pivot** (fall back to the Option-C in-Postgres entity layer from
the earlier review). **All-green → proceed to the migration.** **Any yellow →
proceed only with the named mitigation, and re-score after W2 of the real work.**

---

### Q1 — Does cognee's Postgres graph backend actually work? *(load-bearing)* — [BARRY-AGENT]

*Why it gates:* the entire premise of decision 2 is "one local Postgres holds
graph + vector + relational." Cognee's *local default* is Kuzu, not Postgres —
the single-Postgres story is newer. If the Postgres graph adapter is immature,
"pivot to cognee on local Postgres" silently becomes "run Kuzu + LanceDB +
Postgres" — three stores and extra processes on a 16GB Mac mini, a materially
different decision.

*Measure:* configure cognee's graph backend = Postgres against `cognee_spike`;
run `add()` + `cognify()` end-to-end on the 5 docs; run one `GRAPH_COMPLETION`
query. Note whether it needs a Postgres graph extension (e.g. Apache AGE) beyond
the pgvector/pg_trgm already installed, and whether install is clean.

| | Threshold |
|---|---|
| 🟢 Green | cognify + a graph-traversal query both succeed on local Postgres using only extensions we can install cleanly (pgvector already present; AGE acceptable if it builds against PG 17 without drama). |
| 🟡 Yellow | Works, but requires a graph extension that's awkward on PG 17 / Homebrew, or the graph is stored relationally with visibly poor traversal ergonomics. → Mitigation: accept Kuzu as the graph store (drop the single-Postgres goal), re-price the extra process. |
| 🔴 Red | Postgres graph adapter errors out, is undocumented/alpha, or can't answer a 2-hop query. → Full pivot off the table; fall back to Option C. |

---

### Q2 — What does cognify cost per document? *(you're removing the meter here)* — [BARRY-AGENT]

*Why it gates:* the telemetry decision drops the pre-flight gate exactly where
the unmeasured cost lives. Today a capture is **one ~$0.001 Haiku call**. Cognify
is 6 stages with per-chunk LLM extraction + summaries — plausibly 10–50×.

*Measure:* cognify the 5 docs (mix: one short Discord-length note, one medium,
one long ~meeting-transcript-sized). Read the **provider dashboard** after, and
the labeled `agent_runs` rows if Q3 works. Compute mean $/short-note and
$/transcript. Model daily cost at a realistic capture rate (assume 30 notes/day).

| | Threshold |
|---|---|
| 🟢 Green | short-note cognify < **$0.02** (≤ ~$0.60/day at 30 notes — comfortable inside the ~$15/day blast radius). |
| 🟡 Yellow | **$0.02–$0.10** per short note. → Mitigation: batch capture + cheaper extraction model, and/or restrict cognify to low-volume inputs (transcripts) while capture stays lightweight. Re-price. |
| 🔴 Red | > **$0.10** per short note (≥ ~$3/day just for capture, a fifth of the whole budget on one flow), or unbounded/unpredictable per doc. → Full pivot too expensive for the capture path; Option C or transcripts-only cognee. |

---

### Q3 — Do telemetry labels survive cognee's async internals? *(the whole telemetry plan rests on this)* — [BARRY-AGENT]

*Why it gates:* the agreed telemetry replacement is contextvar labeling + a
LiteLLM callback. It only works if the label set before `cognify()` reaches
LiteLLM through cognee's `gather`/thread fan-out over chunks.

*Measure:* register the callback, wrap a cognify run in a `labeled("spike",
"customer_discovery", correlation_id="doc-1")` context, then check how many of
the resulting `agent_runs` rows carry the correct label vs. `NULL`.

| | Threshold |
|---|---|
| 🟢 Green | **100%** of cognee's provider calls emit rows carrying the label + correlation id. |
| 🟡 Yellow | Agent/function label propagates but **per-doc correlation is lost** across the chunk fan-out (thread boundary drops it). → Mitigation: accept run-level (not entity-level) attribution; set correlation at the callback via a run-id contextvar and stitch afterward. |
| 🔴 Red | Callback doesn't fire for cognee's calls at all, or **no** rows are labeled (cognee uses a client that bypasses the shared `litellm` module). → Telemetry plan is unworkable as designed; the pivot loses the ledger. Reassess before proceeding. |

---

### Q4 — Can we keep our embedding (gemini-embedding-001 @768, L2-normalized)? — [BARRY-AGENT]

*Why it gates:* if cognee forces its own embedding model, we re-embed everything
(cheap at 2 facts) *and* the `vector(768)` columns + cost basis + provider mix
all change. Also a silent-correctness trap if it embeds at a different dim or
skips the L2 normalization our cosine math assumes.

*Measure:* attempt to configure cognee's embedder = `gemini-embedding-001` at
`output_dimensionality=768`. Confirm stored vectors are 768-dim and unit-norm.

| | Threshold |
|---|---|
| 🟢 Green | cognee accepts the Gemini embedder at 768 dims; stored vectors are unit-norm (or cognee normalizes). |
| 🟡 Yellow | Works only with cognee's default embedder (e.g. OpenAI/FastEmbed). → Mitigation: accept a new embedding model + re-embed; add an OpenAI key or run FastEmbed locally (RAM cost → feeds Q5). Note the added provider. |
| 🔴 Red | No dimensional/normalization control and the default is unusable (wrong dim silently, no local option within RAM). → Embedding stack becomes a fight; downgrade confidence in the pivot. |

---

### Q5 — Does the footprint fit 16GB alongside Postgres + bot? — [BARRY-AGENT]

*Why it gates:* cognee pulls LiteLLM, Instructor, SQLAlchemy, NetworkX, Docling,
LanceDB, possibly Transformers/FastEmbed. The Mac mini is 16GB already running
Postgres, the Discord bot, and launchd jobs.

*Measure:* `import cognee` resident RSS; peak RSS during a cognify of the long
doc; free memory with the normal stack running. (If Q4 pushes to local FastEmbed,
measure with that loaded.)

| | Threshold |
|---|---|
| 🟢 Green | peak cognee RSS leaves **> 3GB** headroom with Postgres + bot running. |
| 🟡 Yellow | **1–3GB** headroom. → Mitigation: run cognify as a short-lived subprocess (not resident in the bot), cap concurrency. |
| 🔴 Red | < **1GB** headroom or swap thrash during cognify. → Not viable co-resident; would need to offload cognee, which defeats the local-single-box premise. |

---

## Non-spike design questions (record a position; don't try to measure)

These don't need the harness — they need a decision, informed by the spike but
not blocked on it. Capture the current lean in the findings block.

- **Q6 — entity ↔ operational boundary.** `outcomes.attributed_fact_id` is an FK
  to a SQL row; if facts become graph nodes that FK can't exist. Where's the
  join, and how does Higgins' KR reporting cross it? *Lean:* keep operational
  tables in SQL; store a stable cognee node-id as TEXT on the SQL side; join in
  app code, not FK.
- **Q7 — upstream churn policy.** Cognee is 1.4.0, 500× YoY. Pin exact version;
  who owns upgrade + breakage? Transitive dependence on LiteLLM's callback
  contract is part of this.
- **Q8 — memify in v1?** Continuous prune/reinforce/reweight is a real reason to
  adopt cognee, but mutates the graph on its own schedule and spends tokens.
  *Lean:* defer until labeled telemetry (Q3) is proven trustworthy in production.

---

## Deliverable

Fill this block at the end of the box. One row per gating question, plus the
go/no-go call.

### FINDINGS (run: barry-agent 2026-07-28, 3 passes; full capture = run3)

Source of record: `/Users/Shared/afc-richmond/SPIKE-cognee.md` TASK 3 result +
`~/spike_cognee_run.log` / `run2.log` / `run3.log` on barry-agent.

| Q | Measured | Score | Note / mitigation |
|---|----------|-------|-------------------|
| Q1 Postgres graph | cognify + `GRAPH_COMPLETION` both succeed on local Postgres; provider value **`postgres`** worked first try (Kuzu/networkx/pgsql never needed). Graph is real: 72 nodes / 147 edges / 44 `Entity_name` / 5 summaries; the 2-hop query returned a correct traversal (workflow → asked-by Elena Ruiz + David Okafor). | 🟢 | **No Apache AGE needed** — cognee's postgres graph is plain SQLAlchemy tables over `pgvector`+`pg_trgm` (already installed). The AGE-absent pre-signal is moot. **Single-Postgres premise holds.** |
| Q2 cognify $/doc | short-note **$0.0043 / $0.0056**; transcript (doc 04) **$0.0139**; all-doc mean **$0.0105**. run3 window total $0.0524 (Haiku $0.0517 + Gemini embed $0.0007). Latency: shorts 6.7–7.0s, transcript 24.9s, longest 43.8s. **Dashboard cross-check (H1) ✅ 2026-07-28: Anthropic today = $0.13 actual vs ~$0.10–0.12 estimated across all 3 runs — within ~15%, no hidden spend.** | 🟢 | Haiku is ~99% of cost; embeddings negligible. **Modeled @30 short notes/day ≈ $0.15/day** — far inside the ~$15/day blast radius. Latency is fine for async capture (not interactive-blocking). |
| Q3 label propagation | cognify calls **45/45 = 100%** labeled **with correlation_id** (LLM *and* embedding), through the async chunk fan-out. The 5 unlabeled calls are non-cognify (2 startup probes + the graph query's calls), intentionally outside a `labeled()` block. | 🟢 **only with mitigation M1** (else 🔴) | **cognee's default `AnthropicAdapter` calls the raw `anthropic` SDK and bypasses litellm entirely → callback captured 0 LLM calls on run1.** Fix: route Anthropic through cognee's litellm path (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/claude-haiku-4-5` → `GenericAPIAdapter` → `litellm.acompletion`). Verified 100% capture in run3. **Mandatory**, and it deepens the LiteLLM-contract dependency (Q7). |
| Q4 embedding | dim **768** ✓ (gemini-embedding-001 accepted — the win); **L2-norm ≈ 0.584, NOT unit-norm**. | 🟡 | cognee doesn't renormalize truncated-768 Gemini output. Fix M2: **renormalize on write, or use pgvector cosine `<=>` (normalization-invariant) and never inner-product `<#>`.** Re-score after W2. |
| Q5 RAM headroom | peak RSS **459 MB** (`/usr/bin/time -l`: 481 MB); install ~67s; `.venv` **837 MB** (+640 MB vs pre-spike). | 🟢 | **>3 GB headroom** on the 16 GB mini even with Postgres + bot resident. |

**Go / no-go call — 🟢 PROCEED WITH MITIGATIONS (barry-admin, 2026-07-28).**
No 🔴. Q1/Q2/Q5 green; Q3 green *conditional on M1*; Q4 yellow (M2). Per the
rule (any yellow → proceed with named mitigations, re-score after W2), this is a
go, not a stop. **Two mitigations carried into the migration:**

- **M1 (telemetry, mandatory):** configure cognee's Anthropic via the litellm
  `GenericAPIAdapter` (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…`) so the
  contextvar+callback telemetry fires for LLM calls, not just embeddings.
  Without it the ledger silently loses ~99% of spend. Pin the litellm version
  (Q7) since telemetry now structurally depends on its callback contract.
- **M2 (embedding):** renormalize the 768-dim Gemini vectors on write, or commit
  to pgvector cosine `<=>` throughout and forbid `<#>`.

**H1 confirmed (2026-07-28):** Anthropic dashboard today = **$0.13** vs the
~$0.10–0.12 local estimate across all three runs (run3 alone $0.052) — within
~15%, corroborating the harness's per-doc cost and confirming no
uninstrumented Anthropic spend. Google side left unverified (spike est <$0.01,
negligible). **Q2 is now dashboard-backed, not just modeled.**

### Run notes / gotchas for the migration (from the run)

- **Access control:** cognee 1.4.0 defaults to multi-user access control ON; in
  that mode the pgvector and postgres-graph adapters need **their own**
  `VECTOR_DB_*` / `GRAPH_DATABASE_*` creds (they don't inherit `DB_*`), and the
  graph adapter defaulted to port 123. Set `ENABLE_BACKEND_ACCESS_CONTROL=false`
  for single-user, or supply per-store creds. (W1/W2 config work.)
- **Install:** `cognee[postgres]` pulls `psycopg2` (source build) even though our
  runtime uses `psycopg` v3; it failed on keg-only OpenSSL until built with
  `LDFLAGS/CPPFLAGS` for `openssl@3`+`libpq`. Pin `psycopg2-binary` or set the
  flags in the real migration.
- **Harness Q4 bug (fixed 2026-07-28):** the embedding probe used an unquoted
  mixed-case table name and mis-reported "NOT FOUND"; barry-agent read the vector
  directly via psql. Probe now quotes identifiers.

---

## Harness steps (order of operations)

1. **[BARRY-ADMIN]** branch `spike/cognee`; add `cognee[postgres]` to a *spike-only*
   dependency group (never merged); create scratch DB `cognee_spike` (`createdb`)
   with pgvector + pg_trgm; pick the 5 sample docs (commit them to the branch).
2. **[BARRY-ADMIN]** write `spike/run_cognee.py`: the labeled-callback shim (Q3),
   a cognify-the-5-docs loop with per-doc wall-clock timing (Q2 latency), and a
   `GRAPH_COMPLETION` query (Q1). Point embedder at Gemini 768 (Q4).
3. **[BARRY-AGENT]** run it against `cognee_spike` with real keys; capture:
   provider-dashboard delta (Q2 $), `agent_runs` label coverage (Q3), stored
   vector dim/norm (Q4), `/usr/bin/time -l` or RSS sampling (Q5), and whether
   the graph query returned a real traversal (Q1).
4. **[BARRY-ADMIN]** fill FINDINGS; make the go/no-go call; **drop the branch and
   `dropdb cognee_spike`** regardless of outcome (nothing here ships).

**Cost of the spike itself:** a few dollars of provider spend (25-ish cognify
runs across 5 docs) + 1 day. Bounded and disposable.

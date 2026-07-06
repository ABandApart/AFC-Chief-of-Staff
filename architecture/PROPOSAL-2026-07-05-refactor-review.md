# Proposal: Refactoring & Architecture Review — 2026-07-05

Scope: full review of built code (Phases 1–3.4) plus the planned-but-not-built
systems (Phases 3.5–13) as described in `architecture/`. Focus areas per the
review request: token efficiency, latency, data structures & logic, code
performance, and functionality gaps.

Priorities: **P0** = correctness/data bug, fix before more data accumulates.
**P1** = high-value, low-effort. **P2** = do when the touching phase comes up.
**P3** = nice-to-have / revisit at scale.

---

## Part 1 — Refactoring what's already built

### R1 (P0) — Cost ledger loses precision on cheap calls
`agent_runs.usd_cost` is `NUMERIC(10,4)` and `runs.py` rounds to 4 decimals
before insert. A recall query embedding costs ~$0.0000015 → stored as
**$0.0000**; a typical Haiku extraction (~$0.001) carries up to ~5% rounding
error per row. The ledger — which G2 sums and Higgins reports against —
systematically undercounts exactly the calls the system makes most.

**Fix:** migration `0002`: `ALTER TABLE agent_runs ALTER COLUMN usd_cost TYPE
NUMERIC(14,8);` and change `round(state.usd_cost, 4)` → `round(..., 8)` in
`agents/_lib/runs.py:504`. One-line code change + tiny migration. Do this
before Tartt (Phase 4) starts writing hundreds of rows/day.

### R2 (P0) — PRICE_TABLE validated *after* the paid API call
In both `call_anthropic` and `call_gemini`, the `PRICE_TABLE` lookup raises
`ValueError` **after** `messages.create()` / `generate_content()` returns.
An unknown model means you pay for the call, then record it as `failed` with
no cost. Move the lookup to the top of each method — fail before spending.
(`runs.py:253` and `runs.py:309`.)

### R3 (P1) — Credential + connection churn dominates per-call latency
Every operation spawns a `security` subprocess and/or opens a fresh Postgres
connection:

- `_keychain_get` runs a subprocess per call — API key fetch on every LLM
  call, `db-url` fetch on every G2 check, row write, and every `brain.py`
  helper.
- `agent_run` opens **two** connections per run (G2 check on entry, row write
  on exit); `insert_fact` opens **one connection per fact**.
- A 3-fact capture ≈ 7+ subprocess spawns + 7 connections + 2 fresh SDK
  clients before any model latency.

**Fix (one small `agents/_lib/creds.py` + pool):**
1. Cache keychain lookups with `functools.lru_cache` (secrets don't rotate
   mid-process; add an explicit `invalidate()` for when they do).
2. Add a module-level `psycopg_pool.ConnectionPool` (min 0/max 2 is plenty)
   and route `runs.py`, `brain.py`, `recall.py` through it. The G2 check and
   the exit write can share one pooled connection.
3. Cache `anthropic.Anthropic` clients per key and the `genai.Client`
   (clients are thread-safe and reusable).

Also: `keychain_get` is duplicated in `run.py` and `runs.py`, and callers
import the private `_keychain_get` — the new `creds.py` fixes both.

### R4 (P1) — Batch fact inserts; make capture atomic
`capture.py` inserts facts in a loop → one connection per fact and **no
atomicity**: a failure on fact 3 of 5 leaves a partial capture with a ✅-less
⚠️ reply, and re-sending the message duplicates facts 1–2. Add
`brain.insert_facts(facts) -> list[int]` doing a single multi-row
`INSERT ... RETURNING id` in one transaction.

### R5 (P1) — G1's `count_tokens` round trip doubles pre-call latency
Both `call_anthropic` and `call_gemini` make a network round trip to count
tokens before every call. For capture, input is bounded by Discord's 4,000-char
message limit (~1,000 tokens) against a 4,000-token cap — the count can
never trip, but the user waits for the extra round trip on every capture.

**Fix:** estimate locally (`len(text) // 4` with a safety factor, e.g. ×1.3)
and only call the real `count_tokens` when the estimate is within ~20% of
`max_input_tokens`. Keeps G1's guarantee for large-input agents
(meeting-processor) while making the common case one round trip. Reconcile
`input_tokens` from the response's real usage either way (already done).

### R6 (P1) — Hybrid search: replace weighted raw scores with RRF
`cli/recall.py` blends `ts_rank_cd` (unbounded, typically 0.01–0.1 on short
facts) with cosine similarity (0.55–0.85 post-floor) via 0.4/0.6 weights.
The two scales are incomparable — in practice the semantic score dominates
and the lexical weight is mostly decorative, which will bite as the corpus
grows. **Reciprocal Rank Fusion** (`score = Σ 1/(60 + rank_i)`) is the
standard fix: scale-free, no weights to tune, one small change to
`HYBRID_SQL` (rank each CTE with `ROW_NUMBER()`, sum reciprocal ranks).
Keep the `min_sim` floor on the vector side — that part is good design.

Secondary note for scale (not urgent at today's corpus size): the
`(1 - (embedding <=> q)) >= min_sim` predicate inside the vector CTE can
defeat the HNSW index plan; fetching top-50 by pure distance and applying
the floor in the outer query preserves index usage.

### R7 (P2) — `facts.expires_at` exists but nothing writes or reads it
Recall returns expired facts forever; no code path sets the column. Either
(a) enforce it — add `AND (expires_at IS NULL OR expires_at > now())` to
`HYBRID_SQL` and let the extraction prompt flag time-bounded facts — or
(b) drop the column until something produces it. Half-implemented TTL is
worse than none: you'll assume recall is filtering and it isn't.

### R8 (P1) — Fact dedup at capture time
Nothing prevents the same thought captured twice from creating near-identical
facts that pollute recall permanently (both will match every related query).
Before insert, run one vector query per fact: if an existing fact scores
cosine ≥ ~0.95, skip the insert (or update `confidence`/`source_ref`).
Costs one indexed lookup per fact, zero LLM tokens, and protects the corpus —
the single highest-leverage data-quality guard available right now.

### R9 (P2) — Structured extraction via tool use instead of prompt-and-parse
`capture.py` asks Haiku for raw JSON, strips markdown fences, and treats
parse failures as "try rephrasing." Anthropic tool use with
`tool_choice={"type": "tool", ...}` returns schema-validated JSON — the
whole `parse_facts` fence-stripping path and the ⚠️-rephrase failure mode
disappear. Small token overhead, meaningful reliability gain; requires
adding tool-call support to `call_anthropic` (worth it — Phases 6–8 all
want structured outputs too: Roy Kent scorecards, Sam evaluations,
meeting-processor extractions).

### R10 (P2) — G2 semantics: timezone, global cap, and race
- **Timezone:** "today" is UTC midnight (`runs.py:445`), so ceilings reset
  at 4–5 PM Pacific. Use local time (or document the UTC choice) so daily
  budgets match the operator's mental model.
- **Global ceiling:** each agent has its own ceiling (~$15/day total blast
  radius) but there's no system-wide cap. One `SELECT SUM(usd_cost)` without
  the agent filter adds a whole-system kill switch for ~3 lines.
- **Race:** G2 is check-then-act. Fine single-process today; once Tartt,
  the bot, and the webhook receiver run concurrently (Phase 6+), parallel
  runs can each pass the check and overshoot. Acceptable overshoot at these
  ceilings — just document it, or do the check and insert in one statement.

### R11 (P2) — Small cleanups
- `runs.py:465,470,477` — `exception_to_reraise` is assigned and never read;
  delete it.
- `_vector_literal` duplicated in `brain.py` and `recall.py` → move to
  `_lib` next to `EMBEDDING_DIM` (which also belongs in `_lib`, not
  `brain.py` — Tartt and the meeting processor will need both).
- `outcomes.py` — `fact_exists()` pre-check duplicates the FK constraint
  and adds a TOCTOU window + extra connection. Insert directly and catch
  `psycopg.errors.ForeignKeyViolation` for the friendly error message.
- `cli/spend.py` — three near-identical query/print pairs could collapse to
  one parameterized dimension; cosmetic, do it whenever the file is next open.

### R12 (P1) — Functionality: `/recall` in Discord
Capture lives in Discord but recall requires a shell on the Mac mini. The
retrieval logic in `recall.py` is already factored (`run_query`,
`format_results` are pure) — a `/recall query:<text>` slash command cog is
~40 lines and turns capture-and-recall into an actual loop you use from your
phone. Highest user-facing value per line of code in this proposal.

Related small win: `/outcome`'s "linked fact id" field asks the user to type
a raw integer they'd have to look up via SQL. Discord slash-command
**autocomplete** against recent/matching facts (fires as the user types the
command, before the modal opens) makes attribution actually usable.

---

## Part 2 — Architecture recommendations for planned systems

### A1 (P1) — Replace polling with Postgres LISTEN/NOTIFY
The docs specify Task Tinder polling `task_candidates` every 15 min, agents
"polling their input table," and Buffer status polling every 30 min — on a
single machine where every producer and consumer shares one Postgres. Add
`NOTIFY` triggers on the handful of hand-off tables (`task_candidates`,
`content_pipeline`, `approval_queue`) and have the always-on bot/scheduler
process `LISTEN`. Sub-second hand-off latency, zero idle queries, and it
resolves the docs' ambiguity about "bot writes vs. agent polls — which is
the source of truth" (answer: the row is the truth; NOTIFY is just the
wake-up). Keep one slow reconciliation sweep (e.g. 15 min) as a catch-all
for missed notifications across restarts. Buffer's *external* status polling
stays a poll — that one's unavoidable.

### A2 (P1) — Batch API + prompt caching for the content pipeline (biggest token lever)
The highest-spend planned agents are not latency-sensitive:

- **Tartt** (5 AM, ~$1/run at 50 items) and **Keeley Strategy triage** run
  as overnight batch work. Anthropic's Batch API and Gemini's batch mode are
  both **50% off**. That roughly halves the two largest line items in the
  $15/day blast radius for zero architectural cost — the 5 AM run just
  becomes "submit batch, collect results."
- **Keeley Strategy / Content / Sam** each carry a large stable prefix
  (voice/style decisions, ICP criteria, Sam's rubric). Mark those blocks
  with `cache_control` and process pipeline items in bursts (which the
  Tartt-triggered fan-out already produces): 90% off cached input tokens
  within the 5-minute TTL. Add caching support to `call_anthropic` once,
  in Phase 8, and every downstream agent inherits it.

Together these plausibly cut planned steady-state LLM spend 40–60%.

### A3 (P1) — Explicit context budgets for synthesis agents
Briefing, Higgins, and Keeley Content are all "read many tables, stuff into
one prompt" designs with no documented truncation (the 40-action-layer.md
input lists are open-ended). Decide the bound in SQL, not in the prompt:
every section is a `top-N by explicit ORDER BY` query with a per-section
token budget, assembled by one shared `build_context(sections, budget)`
helper that drops lowest-priority sections first and **logs what it
dropped**. Do aggregation in SQL (counts, sums, top-Ns) and spend LLM tokens
only on narrative. This converts "briefing cost grows with the database"
into "briefing cost is constant by construction."

### A4 (P2) — Meeting processor: chunked map-reduce, not one 32k call
A 60-minute transcript flirts with the 32k cap and single-call extraction
quality degrades long before that. Chunk at ~8–10k tokens with overlap,
run Haiku extraction per chunk (these can go through the Batch API too),
then one merge/dedup pass (mostly pure Python: exact/near-dup on extracted
items). Cheaper failure mode (retry one chunk, not the whole call), no cap
cliff, better recall on long calls. Also give it idempotency: record the
transcript file hash before processing so a crash mid-extraction doesn't
double-write facts on retry.

### A5 (P2) — Roy Kent: ack-then-process webhook + Cloudflare Tunnel
Two recommendations for Phase 6:
1. **Durability:** the FastAPI handler should write the raw payload to
   `prospects` (status `'new'`, `raw_profile` JSONB — schema already
   supports this) and return 200 immediately; qualification runs from the
   row, not the request. WordPress webhooks time out and retry — the
   existing `prospects_wp_idx` unique index makes retries idempotent for
   free. Never put a Haiku call inside the request/response cycle.
2. **External reach:** use a **Cloudflare Tunnel** (or Tailscale Funnel) to
   the Mac mini rather than migrating the database to hosted Supabase. The
   webhook needs one HTTPS endpoint, not a hosted Postgres; migration cost,
   latency regression, and the RLS question all stay deferred. Revisit
   hosting only if a Phase needs the *database* reachable externally.

### A6 (P2) — Interest-signal decay as a query-time function, not a job
`interest_signals` has `weight` + `last_reinforced_at` but no decay
mechanism is specified. Don't build a maintenance job that rewrites weights —
compute effective weight at scoring time:
`weight * exp(-extract(epoch from now() - last_reinforced_at) / half_life)`.
No job, no drift, tunable by changing one constant, and reinforcement stays
a simple `weight` bump + timestamp touch.

### A7 (P2) — One scheduler daemon instead of a plist per job
Phases 4–12 accumulate ~7 launchd schedules (5 AM Tartt, 6 AM briefing,
6-hourly Ted, Sunday Nate, Monday Higgins, Sunday backup, log rotation).
Recommend **two** supervised processes total: the Discord bot and one
scheduler daemon (APScheduler or a simple cron-table loop) that owns all
timed jobs. launchd's job stays what it's good at — keep-alive on those two
processes. Benefits: shared connection pool and credential cache (see R3),
one place to see "what runs when," one log stream, and adding a job is a
code change rather than a plist deploy. Ted's health checks also get
simpler: one heartbeat per daemon instead of per-job "did launchd fire?"
forensics.

### A8 (P2) — Backpressure: bound work per invocation
The docs chain event-driven agents (Tartt → Strategy → Content → Sam) with
no queue-depth limits, so a fat Tartt morning can slam the Keeley ceiling
by 5:30 AM and starve the rest of the day. Since every hand-off is already
a table row, backpressure is just a bounded dequeue: each agent processes
at most N items per wake-up, ordered by priority (`interest_score`), and
leaves the rest pending. Combined with G2, this turns "ceiling hit =
hard stop mid-pipeline" into "lowest-priority items wait until tomorrow."

### A9 (P2) — Ted/G3: keep it pure Python; add two cheap signals
The G1/G2/G3 layering is sound (G3 catches *quality* anomalies — e.g. a
prompt regression tripling tokens-per-output — that G2's dollar ceiling
won't notice until the ceiling trips). Two additions while building Phase 11:
- **Duration**: `agent_runs` already has `started_at`/`ended_at`; alert on
  p95 duration regression per agent — earliest warning of provider issues.
- **External dead-man's switch**: Ted can't report that Ted is down. A free
  healthchecks.io ping from the scheduler daemon covers "the whole Mac is
  wedged," which is otherwise invisible until the 6:30 AM briefing doesn't
  arrive.

### A10 (P1) — Pull two Phase-12 items forward
- **Backups:** facts and outcomes are already irreplaceable business data,
  and the weekly `pg_dump` is scheduled for Phase 12 (~8 weeks out). A
  nightly `pg_dump | gzip` to `~/backups` + Time Machine pickup is a
  15-minute task. Do it now.
- **SIGTERM handler** for the bot (already flagged for 3.5): required
  before launchd supervision, else every unload risks a mid-capture kill.

### A11 (P2) — Documentation debt (from the docs sweep)
Update before the relevant phases start, or implementers will build the
wrong thing: 30-memory-layer.md + 80-telemetry-layer.md still say
`text-embedding-004` (superseded 2026-06-17 by `gemini-embedding-001` @768
+ L2 norm); 00-INDEX.md/20-architecture-overview.md still describe hosted
Supabase (reversed 2026-05-19); 40-action-layer.md's credential inventory
still lists a single `anthropic-api-key` (per-agent keys shipped in
Phase 1); Roy Kent's spec never defines the HTTP receiver (see A5).

---

## Suggested sequencing

| When | Items | Why |
|---|---|---|
| Now, before 3.5 | R1, R2, A10 (backup + SIGTERM) | Data-correctness fixes get more expensive with every row; backups protect data that already exists |
| With 3.5 (briefing) | R3, R4, R5, A3, A7 | Briefing is the first scheduled agent — right moment for pool/creds refactor, context budgets, and the scheduler decision |
| Quick wins, any time | R6, R8, R12 | RRF, dedup, `/recall` — small, isolated, immediately felt |
| Phase 4 (Tartt) | A2 (batch), A8, R11 | Batch API halves the biggest spender on day one |
| Phase 5–6 | A1 (LISTEN/NOTIFY), A5, R9/R10 | Event plumbing lands with the first event-driven consumers |
| Phase 7+ | A4, A6, A9 | Per the phases that touch them |
| Ongoing | A11 | Update each doc just before its phase starts |

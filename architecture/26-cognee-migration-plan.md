# Migration Plan — to the target-state architecture

<doc:layer>implementation — migration</doc:layer>
<doc:stability>EXECUTED — Tracks A + B complete and deployed; Track C partial</doc:stability>
<doc:depends_on>25-target-state, 30-memory-layer, 80-telemetry-layer, SPIKE-cognee-eval-2026-07</doc:depends_on>
<doc:referenced_by>70-build-order</doc:referenced_by>

## Status

**EXECUTED.** Proposed 2026-07-28; go given the same day; Track A (control
plane) and Track B (MW1–MW7) are complete and deployed to production, and Track C
channel 1 (Granola) is live. Retained as the migration record and as the home of
the **2026-11-01 keep/kill review gate** (see Rollback / fallback). Original text
follows.

**PROPOSED — 2026-07-28.** Concrete path from the current as-built system
(Phases 1–3.5: flat `facts` + Discord + per-agent-keyed cost helper) to the
target state in `25-target-state.md`. Assumes the cognee pivot (spike verdict:
proceed with mitigations M1/M2, `SPIKE-cognee-eval-2026-07.md`). The two
mitigations are baked into the workstreams below, not bolted on.

**Decision status:** the *plan* is drafted; the *go decision* (spend the effort,
accept the external-exposure posture Track C forces) remains the operator's.

---

> **Renamed 2026-08-08 — `W1–W7` → `MW1–MW7`.** The old workstream prefix
> collided with the business workflows in `90-workflows.md` (where W1–W8 are
> *Inbound intake*, *Daily briefing*, *Outreach engine*, …), so "W5" meant both
> "recall rewrite" and "daily briefing" depending on the file. **`M1–M7` was
> considered and rejected: `M1` and `M2` are already this document's mitigation
> IDs** (telemetry routing, embedding normalization) — reusing the prefix would
> have moved the collision inside a single file rather than resolving it.
> `MW` = migration workstream. **The `W` prefix is now reserved permanently for
> business workflows.** Mitigation IDs M1/M2 are unchanged.

## Shape: three tracks

| Track | Scope | Effort | Depends on |
|-------|-------|--------|-----------|
| **A — Control plane** | skills / loops / playbooks conventions; move recurring jobs to loop manifests | ~2–3 days | nothing (do first) |
| **B — Cognee memory pivot** | graph memory replaces flat `facts`; telemetry re-plumbed (M1); embeddings normalized (M2) | ~9–12 days | Track A helpful, not required |
| **C — Channels & exposure** | authenticated tunnel (B3), then email, then Drive — each behind B1/B2 | ~2–4 days per channel | Track B; tunnel before any external channel |

**Sequencing principle:** each step is independently valuable and independently
shippable. Track A pays off with or without the pivot. Track B is the core.
Track C is additive and sequenced one channel at a time.

---

## Track A — Control plane (do first, cognee-independent)

**Goal:** authored skills/loops/playbooks live in git and load into agents,
before the Phase-4+ agents that will read them exist.

1. Create `.claude/skills/`, `loops/`, `playbooks/` with the frontmatter schemas
   from `25-target-state.md` §4.
2. Move the existing recurring jobs (briefing 6am, nightly backup, and the
   Phase-4+ schedules as they land) from per-job launchd plists to **loop
   manifests owned by one scheduler daemon** (this is also refactor item A7 from
   the 2026-07-05 review — one supervised scheduler instead of a plist per job).
3. Write 2–3 seed playbooks against real workflows (`prospect-qualification`,
   `discovery-call-to-proposal`) so the convention is exercised, not theoretical.
4. Point an Obsidian vault at `playbooks/` + `notes/` if desired (authoring only;
   git stays source of truth).

**Exit:** a loop manifest fires the briefing via the scheduler daemon; a skill
and a playbook load into a test agent run; nothing is authored at runtime (B4).
**Defer:** the git→cognee playbook publish path — that's Track B MW5, once the
graph exists to publish into.

---

## Track B — Cognee memory pivot (MW1–MW7)

### MW1 — Telemetry re-plumb (M1 lands here) · ~1.5–2 days
**Goal:** the ledger survives the pivot even though cognee owns the call site.
- Productionize the spike's shim into `_lib/telemetry_context.py`: a contextvar
  `labeled(agent_name, function_label, correlation_id)` manager + a litellm
  `CustomLogger` that writes conformant `agent_runs` rows
  (`correlation_kind='cognify_run'`, run-id in `correlation_id` for per-run
  rollup).
- **Deprecate, per the telemetry decision:** pre-flight refusal (G1 token cap,
  G2 hard ceiling) and per-agent Anthropic keys (`KEY_BY_AGENT`). Replace with
  labeling + a **soft post-hoc ceiling** (breaker checks accumulated spend after
  each write, blocks the *next* invocation) and **one key per subsystem** (cognee
  vs. own-agents) for a coarse provider-side split.
- Keep `agent_run()`/`RunContext` for agents that still call Anthropic/Gemini
  **directly** (Keeley, Higgins, Nate, Trent Crimm) — those don't route through
  cognee.
- Add `cli/reconcile.py`: monthly compare of `SUM(usd_cost)` vs. provider
  billing — the safety net that justifies dropping the hard gate.
- Rewrite `test_runs.py` (the AC1–AC4 gate tests assume pre-flight refusal).

**M1 touchpoint:** the callback only fires if cognee's Anthropic calls go through
litellm — enforced in MW2 config, verified here.
**Exit:** a labeled cognify run writes `agent_runs` rows for **every** LLM +
embedding call (100% coverage, per spike run3); reconcile.py matches the bill.

### MW2 — Cognee stand-up on local Postgres · ~1–2 days
**Goal:** cognee configured against the production Postgres, telemetry-visible.
- Add `cognee[postgres]` (pinned — Q7). Handle the `psycopg2` source build
  (openssl@3/libpq `LDFLAGS`/`CPPFLAGS`, or pin `psycopg2-binary`) — spike gotcha.
- Config: graph provider `postgres` (spike-confirmed, no AGE);
  `ENABLE_BACKEND_ACCESS_CONTROL=false`; per-store `VECTOR_DB_*` /
  `GRAPH_DATABASE_*` creds (they don't inherit `DB_*`); **M1 routing**
  (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…` → GenericAPIAdapter → litellm).
- Cognee's stores live in a **dedicated schema/database**, isolated from the
  operational tables (`prospects`, `agent_runs`, `outcomes`, …).
**Exit:** `cognee.add()` + `cognify()` + a `GRAPH_COMPLETION` query succeed
against production Postgres; ledger shows the calls (MW1 wired).

### MW3 — Domain modeling as DataPoints · ~2–3 days
**Goal:** the knowledge entities modeled as cognee `DataPoint` classes — the
intellectual core.
- Fact, Person, Decision, Meeting, ICPSignal, ContentItem, InterestSignal → typed
  DataPoints with relationship fields (the ontology emerges from the classes).
- Draw the **entity ↔ operational boundary** (target-state Q6): knowledge →
  cognee graph; operational state (`prospects`, `content_pipeline`,
  `approval_queue`, `tasks`, `outcomes`) stays SQL. Cross-references stored as a
  cognee node-id TEXT column on the SQL side, joined in app code (no FK across
  the boundary).
**Exit:** the sample corpus cognifies into the intended entity/edge shapes; a
2-hop query returns a correct traversal.

### MW4 — Capture rewrite · ~1 day
**Goal:** `#capture` writes through cognee, atomically, deduped.
- Replace `brain.insert_facts` path with `cognee.add()` + `cognify()` under a
  `labeled()` context.
- Reconcile dedup: **keep** the message-hash short-circuit (`capture_messages`,
  migration 0003 — cheapest guard, pre-LLM); **drop** the cosine-0.95 per-fact
  layer in favor of cognee's entity resolution.
**Exit:** a captured note becomes graph entities; an exact re-post is skipped
before any LLM call; ledger attributes the spend to `fact-extraction`.

### MW5 — Recall rewrite + M2 · ~1–1.5 days
**Goal:** retrieval is GraphRAG; embeddings are correct.
- Replace the RRF `HYBRID_SQL` / `_lib/search.py` with a cognee `search()`
  (`GRAPH_COMPLETION`); rewrite `cli/recall.py` and the `/recall` cog as thin
  wrappers.
- **M2 lands here:** cognee keeps `gemini-embedding-001`@768 but does **not**
  L2-normalize. Either renormalize on write, or standardize on pgvector cosine
  `<=>` everywhere and forbid inner-product `<#>`. Add a test asserting the
  chosen invariant.
- Build the **git→cognee playbook publish** (`cli/publish_playbooks.py`):
  playbooks with `publish_to_memory: true` cognify into a dedicated `playbooks`
  dataset tagged `trusted`; agent retrieval is scoped to that dataset only (B1).
**Exit:** capture→recall loop works end-to-end via the graph; a published
playbook is retrievable and is never returned from an untrusted ingest query.

### MW6 — Docs + PRDs · ~2 days
- Rewrite `30-memory-layer.md` (schema/hybrid-search → DataPoints/graph),
  `80-telemetry-layer.md` (G1/G2/keys → labeling + soft ceiling + reconcile),
  and the Phase 4/7/8/10 PRDs whose data model changed. Decision-log entry.

### MW7 — Validate + redeploy · ~1–1.5 days
- Runtime pull/sync (large dependency delta), restart the launchd bot, re-drive
  capture/recall, confirm the ledger fills and reconcile.py matches. New
  coordination phase file for barry-agent.

---

## Track C — Channels & exposure (additive, one at a time)

Do **not** start until Track B is stable. **B3 (tunnel) must precede any
externally-reachable channel.**

1. **Exposure (B3):** Cloudflare Tunnel (or Tailscale Funnel) to an authenticated
   API on the Mac mini. Postgres stays on the local socket, never exposed. This
   is the Phase-6 hosting decision arriving early — resolve it here, keep the DB
   local. ~2–3 days.
2. **Email channel:** inbound ingest (thread/attachment → cognee, behind B1) +
   drafted replies (outbound, behind B2). Google OAuth, scoped. ~3–4 days.
3. **Google Drive channel:** ingest shared docs/folders (B1) + document output
   (proposals, briefs → Drive, behind B2). Scoped folders. ~3–4 days.

Each channel: untrusted ingest crosses B1; every outbound action crosses B2
(`#approvals`); no channel reaches the DB directly.

---

## Cross-cutting mitigations (from the spike)

- **M1 — telemetry routing (mandatory):** `LLM_PROVIDER=custom` +
  `LLM_MODEL=anthropic/…` so cognee's LLM calls traverse litellm and hit the
  callback. Pin litellm; the ledger now structurally depends on its callback
  contract. Lands MW1 (shim) + MW2 (config), verified MW7.
- **M2 — embedding normalization:** cognee's 768-dim Gemini vectors aren't
  L2-normalized. Renormalize on write, or use pgvector cosine `<=>` only. Lands
  MW5, with a regression test.

---

## Rollback / fallback

If any assumption breaks during the migration (telemetry coverage regresses,
graph performance disappoints at real corpus size), fall back to **Option C**
from the 2026-07-05 review: an `entities` + `fact_entities` join table in the
existing Postgres, keeping the RRF search and the cost helper — ~3–5 days,
retaining the entity-centric win without the cognee dependency. The DataPoint
modeling work (MW3) largely transfers.

### Scheduled keep/kill review — **2026-11-01**

<review_gate id="COGNEE-2026-11-01">

The migration is deployed and the capability argument is real. The *ongoing* cost
is also real and does not decay: every ingested note is LLM spend; telemetry
depends structurally on a pinned litellm callback contract (M1); recall is
nondeterministic; two databases to back up and restore; and a config surface
(`cognee_setup`) that must be re-verified on every upgrade.

This architecture's own rule is that every element traces to a workflow. Applied
here with a number, so the decision is made on evidence rather than by drift.

**Metric — organic recall usage.** Count invocations of
`agents/_lib/retrieval.py` (all scopes) plus `/recall`, from `agent_runs`,
averaged weekly over the four weeks preceding the review date. **"Organic" means
operator- or agent-initiated in the course of real work** — exclude smoke tests,
CI, and anything run to produce this review.

```sql
-- Weekly organic recall rate, trailing 4 weeks
SELECT date_trunc('week', started_at) AS wk, count(*) AS recalls
FROM agent_runs
WHERE function_label = 'infrastructure'
  AND agent_name IN ('recall','retrieval')
  AND trigger_kind <> 'manual'          -- excludes smoke/CI invocations
  AND started_at >= now() - interval '4 weeks'
GROUP BY 1 ORDER BY 1;
```

| Result | Decision |
|--------|----------|
| **≥ 10 recalls/week** | **Keep.** The graph is load-bearing. Close this gate; revisit only if the cost profile changes materially. |
| **< 10 recalls/week** | **Fall back to Option C.** The capability is not being used enough to justify a standing dependency of this weight. |
| **≥ 10/week but quality is not dependent on graph grounding** | **Keep, but narrow.** Retain cognee for capture/recall; stop graph-grounding the drafting path (`Keeley`) if its output is indistinguishable without it. A cheap A/B over ~10 drafts settles it. |

**Second, qualitative test — run it the same day.** Take five real questions the
operator actually asked in the preceding month and check whether GraphRAG answers
them better than a `pg_trgm` + `ILIKE` search over the same corpus would have. If
it does not, usage volume is beside the point.

**If the fallback fires**, Option C is already scoped above at ~3–5 days. Two
things make it cheap and must therefore be preserved between now and the review
date, or the option quietly expires:

1. **Keep the DataPoint definitions in `agents/_lib/ontology.py` free of
   cognee-specific behaviour.** The existing cognee-or-pydantic fallback base
   already does this — do not let it rot; `tests/test_ontology.py` must keep
   passing without cognee installed.
2. **Keep `agents/_lib/retrieval.py` the only retrieval call site**
   (`30-memory-layer.md`). With the wrapper in place, Option C is a
   reimplementation *behind one interface* rather than a rewrite across every
   agent. **This is now the single largest thing making the fallback affordable.**

**Owner:** operator. **Recorded in:** `70-build-order.md` decision log.

</review_gate>

---

## Effort summary

| Track / phase | Effort |
|---------------|--------|
| A — control plane | ~2–3 days |
| B — cognee pivot (MW1–MW7) | ~9–12 days |
| C — exposure (B3) | ~2–3 days |
| C — email channel | ~3–4 days |
| C — Drive channel | ~3–4 days |
| **Core to a working graph brain (A + B)** | **~11–15 days** |

Data migration itself is trivial — the current corpus is ~2 facts.

# Memory Layer

<doc:layer>implementation</doc:layer>
<doc:stability>medium — schema migrations require versioned migration files; the knowledge model is the cognee graph (Phase 3.7 pivot)</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 25-target-state.md, 26-cognee-migration-plan.md</doc:depends_on>
<doc:referenced_by>35-outreach-crm.md, 36-inbound-leads.md, 40-action-layer.md, 50-channel-layer.md, 60-content-pipeline.md, 80-telemetry-layer.md</doc:referenced_by>

## Purpose

The memory layer is **two stores on one local PostgreSQL 17 instance** (Mac mini):

- **The knowledge graph — `aiadaptive_cognee`.** What the brain *knows*: facts,
  people, organizations, decisions, meetings, ICP signals, content. Managed by
  **cognee** (GraphRAG) — cognee owns extraction, entity resolution, embedding,
  and retrieval. This replaced the hand-rolled `facts` table + hybrid search in
  the Phase 3.7 pivot (see `26-cognee-migration-plan.md`).
- **The operational store — `aiadaptive_cos`.** What the brain is *doing*: status
  machines, queues, ledgers, cadence — prospects, tasks, follow-ups, the content
  pipeline, the telemetry ledger. Plain SQL tables, read/written through the
  shared pool in `agents/_lib/db.py`.

The split is the **entity↔operational boundary** (`25-target-state.md`):
knowledge → the graph, operational state → SQL. They cross-link by cognee
**node-id stored as a TEXT column** on the SQL side, joined in application code —
never a cross-database foreign key (Postgres can't FK across databases).

---

## Deployment Configuration

<deployment>

- **Provider**: Local PostgreSQL 17 on the Mac mini (pivoted from hosted Supabase
  before any infrastructure was provisioned — see `70-build-order.md` decision
  log, 2026-05-19). Migration to hosted is deferred until a phase needs the
  *database* externally reachable; Phase 6's webhook only needs an HTTPS endpoint
  (tunnel), not hosted Postgres.
- **Two databases, one server**:
  - `aiadaptive_cos` — operational tables. Extensions `pgvector`, `pg_trgm`.
  - `aiadaptive_cognee` — cognee's three stores (relational + vector + graph) all
    live here; graph provider is `postgres` (no AGE), vector provider `pgvector`.
    Created by barry-admin with `vector` + `pg_trgm`. Isolated from the
    operational tables so a cognee mishap can't touch the ledger/queues.
- **cognee configuration**: `agents/_lib/cognee_setup.py` (`configure_cognee()`),
  called once at process start (bot `run.py setup_hook`; every CLI at startup).
  It points all three stores at `aiadaptive_cognee`, turns access-control off
  (single-user), and installs the M1 telemetry callback. cognee is an **optional
  dependency** (`uv sync --group cognee`) — the heavy tree stays out of the
  dev/CI env; modules that touch it import lazily.
- **LLM routing (M1, mandatory)**: cognee's LLM goes through litellm as a
  *custom* provider (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/claude-haiku-4-5`)
  so our labeling callback fires on every call; the native Anthropic adapter would
  bypass telemetry. Details in `80-telemetry-layer.md`.
- **Embeddings — local (2026-08-03)**: `EMBEDDING_PROVIDER=fastembed`,
  `BAAI/bge-base-en-v1.5` @ 768, run in-process via ONNX Runtime — **no API key,
  no rate limits, and client transcript text never leaves the box**. This replaced
  `gemini-embedding-001` under the provider plan that reserves **Gemini for news
  ingestion only** (Tartt); it also retired **M2** — bge vectors are normalized,
  so the old un-normalized-Gemini concern is moot. Local embeddings don't traverse
  litellm, so they make no ledger row (they're free). **Fallback:** Voyage
  (`voyage/voyage-3.5`, cloud, Anthropic's recommended partner) — a commented
  config block in `cognee_setup.build_cognee_env`.
- **Dimension lock**: 768 is a hard commitment — switching embedders means
  re-embedding the whole graph. bge-base-en-v1.5 is 768-dim, so the switch off
  Gemini kept the commitment (and the graph was empty at the time — go-forward).
- **Access**: all operational SQL uses the shared connection pool in
  `agents/_lib/db.py` — no per-operation `psycopg.connect()`.

</deployment>

---

## The Knowledge Graph (`aiadaptive_cognee`)

### Domain model — DataPoints

The typed knowledge model lives in **`agents/_lib/ontology.py`**: eight cognee
`DataPoint` classes with typed relationship fields (edges) and
`metadata["index_fields"]` (which text fields get embedded):

`Organization`, `Person`, `Fact`, `Decision`, `Meeting`, `ICPSignal`,
`ContentItem`, `InterestSignal`.

`Organization` is the entity-resolution target ("everything about Acme"). A
cognee-or-pydantic fallback base lets the classes import and be structurally
unit-tested (`tests/test_ontology.py`) without cognee present.

### Two ingestion modes

1. **Free-text capture (mode-1)** — the default path for notes.
   `agents/_lib/ingest.py::ingest_note(text, *, source_ref, source_type)`:
   normalize + hash the text → skip if already ingested (pre-LLM dedup via the
   `capture_messages` table) → `cognee.add(text, dataset_name="capture")` +
   `cognify()`. **cognee does the extraction and entity resolution** — we do not
   pre-extract into DataPoints. The hash is recorded only after a successful
   cognify, so a failed ingest is retriable verbatim. This is the shared core:
   the Discord `#capture` cog (`cogs/capture.py`) is a thin caller, and the
   primary **API ingestion channel** (Track C) reuses the same function.

2. **Structured DataPoints** — for agents that already hold structured knowledge
   (meeting processing, content triage; Phases 4/7/8). Construct the ontology
   objects and call `add_data_points([...])`
   (`from cognee.tasks.storage import add_data_points`, async) — it walks the
   relationship fields into graph nodes/edges. Runtime shape-check:
   `agents/test/ontology_shape.py`.

Knowledge is **not** vectorized in our SQL anymore — cognee owns the vectors
inside the graph store. (The operational tables are queried structurally and
carry no embeddings.)

### Datasets + trust boundary (B1)

cognee content is partitioned into **datasets**:

- **`capture`** — free-text ingest. Untrusted: it's whatever a channel fed in.
- **`playbooks`** — authored, git-tracked playbooks with `publish_to_memory: true`,
  published by `cli/publish_playbooks.py` (hash-idempotent; tracked in
  `playbook_publications`). This is the **trusted** memory region.
- **`outreach_public`** *(planned, Track O)* — scraped company/job content for
  outreach targets. Untrusted, and partitioned so outreach retrieval can be scoped
  to it plus the target subgraph, never touching client-work datasets (H3).

Trust boundary **B1**: agent retrieval that must not be swayed by untrusted
ingest is scoped to the trusted dataset only — an injected "fact" in `#capture`
can't rewrite an operating procedure.

### Recall — GraphRAG

`agents/_lib/graph_recall.py::recall(query)` runs
`cognee.search(query_type=SearchType.GRAPH_COMPLETION, query_text=query)`:
cognee's vector search finds relevant triplets, traverses the graph for context,
and generates a **synthesized answer** (a string, not a ranked row list). There
is **no RRF and no query-embedding step on our side** — cognee owns retrieval.
Consumers: the `/recall` slash command (`cogs/recall.py`) and `cli/recall.py`.
Both run `configure_cognee()` first and execute under `labeled("recall", …)` so
the query/completion spend lands in the ledger.

> This replaced `agents/_lib/search.py` (Reciprocal Rank Fusion over the `facts`
> table), removed in MW5. The old hybrid-search tuning (RRF k=60, a 0.55 cosine
> floor) is gone — those knobs now live inside cognee.

### The retrieval wrapper — B1 as a mechanism, not a convention (2026-08-08)

<retrieval_wrapper>

**The problem it solves.** B1's teeth depend on agents *remembering* to scope
their searches — playbook retrieval "scoped to the trusted dataset only,"
outreach traversal "scoped to permitted datasets." Nothing enforces either. One
forgotten parameter in one agent silently mixes untrusted `capture` content into
a trusted-playbook retrieval, and the failure looks exactly like a good answer.

**The precedent.** This system already solved this shape of problem once:
*agents do not import provider SDKs; every LLM call goes through the cost helper.*
That rule made spend tracking structural instead of aspirational. Retrieval gets
the same treatment.

> **Rule: agents do not call `cognee.search` or `cognee.SearchType` directly.
> All retrieval goes through `agents/_lib/retrieval.py`.**

```python
# agents/_lib/retrieval.py
class Scope(StrEnum):
    UNTRUSTED   = "untrusted"    # capture, granola, outreach_public  ← DEFAULT
    TRUSTED     = "trusted"      # playbooks only
    TARGET      = "target"       # bounded traversal from one node id

DATASETS: dict[Scope, tuple[str, ...]] = {
    Scope.UNTRUSTED: ("capture", "granola", "outreach_public"),
    Scope.TRUSTED:   ("playbooks",),
    Scope.TARGET:    (),          # resolved from the root node's dataset
}

async def recall(query: str, *, scope: Scope = Scope.UNTRUSTED,
                 agent: str, hops: int | None = None,
                 root_node_id: str | None = None) -> RecallResult: ...
```

**Design decisions, and why each one:**

- **Defaults closed to `UNTRUSTED`.** The safe default is the *less* privileged
  scope, so a forgotten argument yields a weaker answer rather than a boundary
  crossing. A caller wanting playbooks must say so explicitly, and that word
  `TRUSTED` is greppable in review.
- **Scopes never union.** There is no `TRUSTED | UNTRUSTED` — mixing is the
  failure being prevented, so it is unrepresentable rather than discouraged. An
  agent needing both makes two calls and keeps the results distinguishable, which
  is also what it needs in order to render provenance (UX-2).
- **`agent` is required, not inferred.** It sets the `labeled()` telemetry
  context, so retrieval spend attributes correctly — closing the same gap M1
  closed for cognify.
- **`Scope.TARGET` is the Track O bounded traversal**: `root_node_id` + `hops`,
  no embedding query, no synthesis (see below).
- **Returns `RecallResult`, not a bare string** — carrying `answer`, `sources`,
  and `scope_used`. Callers get provenance by construction, which is what makes
  the `/recall` citation requirement cheap to honour.

**CI enforcement** — the rule is only real if it is checked:

```bash
# tests/test_no_raw_retrieval.py  (pure, no DB)
# Fails if cognee.search / SearchType appears outside _lib/retrieval.py
rg -n 'cognee\.(search|SearchType)' agents/ cli/ \
  --glob '!agents/_lib/retrieval.py' && exit 1 || exit 0
```

Same pattern as the existing SDK-import rule. Half a day of work total, and B1
stops being a promise about developer memory.

**Migration:** `graph_recall.recall()` becomes a thin call into the wrapper with
`scope=Scope.UNTRUSTED`; `cli/recall.py` and the `/recall` cog are unchanged
apart from rendering `result.sources`. `cli/publish_playbooks.py`-adjacent
retrieval uses `Scope.TRUSTED`.

**As-built (Phase 3.8, 2026-08-10).** `agents/_lib/retrieval.py` implements
`Scope`/`DATASETS`/`RecallResult`/`recall()`; `graph_recall.recall()` now delegates
at `Scope.UNTRUSTED` (the only `cognee.search` call site is the wrapper's
`_graph_completion`). CI grep is `tests/test_no_raw_retrieval.py` (pure Python;
excludes `agents/test/` diagnostic probes). `Scope.TARGET` raises `NotImplementedError`
pending Track O, and a scope resolving to no datasets raises rather than searching
the whole graph. **Runtime-verify (barry-agent):** the installed cognee's
`search(datasets=…)` kwarg is the scoping mechanism and is unverified on the build
box — confirm `/recall` still answers *and* that a `TRUSTED` search cannot return
`capture`/`granola` content (the actual B1 proof). `sources` on `RecallResult` is
carried but not yet populated by `GRAPH_COMPLETION` — the citation fill is a UX-2
follow-up.

</retrieval_wrapper>

**A second retrieval mode — bounded traversal (Track O, 2026-08-08).** The
outreach packet (`35-outreach-crm.md` §7) does **not** use `GRAPH_COMPLETION`.
It runs a bounded N-hop traversal from a known root
(`outreach_targets.cognee_node_id`), scoped to permitted datasets, and displays
the retrieved nodes read-only with their source refs. No embedding query, no
synthesis, no LLM — the design decision is that **no generated text exists in the
outbound outreach path**. Traversal is also the mechanism that makes cross-client
leakage structurally impossible for that surface: the packet cannot semantically
"find" material outside the target's subgraph. `GRAPH_COMPLETION` remains the
right mode for `/recall`, where a synthesized answer for the operator is the
point. Planned alongside: an **`outreach_public` dataset** for scraped company
and job content, keeping client-work datasets unreachable from outreach
retrieval entirely (H3, `35-outreach-crm.md` §11).

**M2 (retrieval quality)** — originally a concern about Gemini's un-normalized
768-dim vectors (norm ≈ 0.58). **Retired 2026-08-03** with the switch to local
FastEmbed (`bge-base-en-v1.5`), whose vectors are normalized. Recall quality was
already judged good at the MW7 deploy; the go-forward graph re-embeds under bge as
notes arrive.

### Telemetry of cognee spend (M1)

cognee makes LLM + embedding calls we don't own the call site of. We label them
with a contextvar (`agents/_lib/telemetry_context.labeled()`) and a litellm
callback that writes a conformant `agent_runs` row per provider call
(`correlation_kind='cognify_run'`). Full treatment in `80-telemetry-layer.md`.

---

## The Operational Store (`aiadaptive_cos`)

Plain SQL — status machines, queues, ledgers, cadence. Queried structurally (by
status, date, owner, id, stage), never vectorized. The schema below is
Postgres-flavored; exact types are pinned in the numbered migrations.

### Dedup + publish tracking

```sql
-- capture_messages: message-level capture dedup (pre-cognify). One row per
-- ingested note; the normalized-text hash is the key. (Migration 0003.)
CREATE TABLE capture_messages (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    TEXT NOT NULL UNIQUE,     -- sha256 hex of normalized text
    message_id      TEXT NOT NULL,            -- source ref (Discord id, API ref, …)
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- playbook_publications: git→cognee publish dedup for the trusted `playbooks`
-- dataset. Skips re-cognifying an unchanged playbook. (Migration 0005.)
CREATE TABLE playbook_publications (
    name            TEXT PRIMARY KEY,         -- playbook name (== filename stem)
    content_hash    TEXT NOT NULL,            -- sha256 hex of published content
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Structured Tables

```sql
-- Follow-ups: open commitments with escalation
CREATE TABLE follow_ups (
    id                  BIGSERIAL PRIMARY KEY,
    owner               TEXT NOT NULL,        -- 'self' or person name
    action              TEXT NOT NULL,
    deadline            DATE,
    source_meeting_id   BIGINT REFERENCES meeting_transcripts(id),
    -- (source_fact_id, the old FK into `facts`, was dropped in 0006; a graph
    --  node-id link is added when the action layer is actually built — Phase 4)
    status              TEXT NOT NULL DEFAULT 'open',  -- 'open', 'done', 'cancelled'
    escalation_level    SMALLINT NOT NULL DEFAULT 0,   -- 0..3
    draft_followup_msg  TEXT,                          -- pre-drafted nudge for level 3
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX follow_ups_status_idx     ON follow_ups (status, escalation_level DESC);
CREATE INDEX follow_ups_owner_idx      ON follow_ups (owner, status);
CREATE INDEX follow_ups_deadline_idx   ON follow_ups (deadline) WHERE status = 'open';

-- Task candidates: Task Tinder queue
CREATE TABLE task_candidates (
    id                  BIGSERIAL PRIMARY KEY,
    proposed_action     TEXT NOT NULL,
    source_type         TEXT NOT NULL,        -- 'meeting', 'email', 'discord', 'discovery'
    source_ref          TEXT,
    evidence_text       TEXT NOT NULL,        -- the snippet that suggested this task
    confidence          REAL NOT NULL DEFAULT 0.5,
    proposed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|accepted|declined|deferred
    decided_at          TIMESTAMPTZ,
    discord_message_id  TEXT                  -- for reaction handler to find the row
);

CREATE INDEX task_candidates_status_idx ON task_candidates (status, proposed_at DESC);

-- Tasks: accepted candidates promoted to active work
CREATE TABLE tasks (
    id                  BIGSERIAL PRIMARY KEY,
    candidate_id        BIGINT REFERENCES task_candidates(id),
    action              TEXT NOT NULL,
    due_date            DATE,
    status              TEXT NOT NULL DEFAULT 'active',  -- active|completed|cancelled
    follow_up_id        BIGINT REFERENCES follow_ups(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

-- People: relationship context
CREATE TABLE people (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,        -- 'client', 'prospect', 'peer', 'mentor', 'family'
    last_contacted_at   TIMESTAMPTZ,
    context_notes       TEXT,
    cadence_days        INTEGER               -- expected contact cadence; null = no cadence
);

CREATE INDEX people_name_trgm_idx ON people USING gin (name gin_trgm_ops);

-- Prospects: inbound qualified leads from WordPress Lead Engine (W1)
CREATE TABLE prospects (
    id                      BIGSERIAL PRIMARY KEY,
    wordpress_profile_id    TEXT NOT NULL,             -- ID from Lead Engine
    person_id               BIGINT REFERENCES people(id),
    name                    TEXT NOT NULL,
    email                   TEXT,
    company                 TEXT,
    role                    TEXT,
    source_form             TEXT NOT NULL,             -- 'scorecard', 'contact', 'newsletter'
    raw_profile             JSONB NOT NULL,            -- the full webhook payload
    icp_segment             TEXT,                      -- inferred or stated segment
    icp_fit_score           REAL,                      -- 0..1, computed by Roy Kent
    fit_reasoning           TEXT,                      -- Roy Kent's stated rationale
    status                  TEXT NOT NULL DEFAULT 'new',
                                                       -- 'new', 'qualified', 'contacted',
                                                       -- 'discovery_booked', 'in_engagement',
                                                       -- 'declined', 'cold'
    received_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    qualified_at            TIMESTAMPTZ,
    last_status_change_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX prospects_status_idx     ON prospects (status, received_at DESC);
CREATE INDEX prospects_fit_idx        ON prospects (icp_fit_score DESC NULLS LAST) WHERE status IN ('new', 'qualified');
CREATE UNIQUE INDEX prospects_wp_idx  ON prospects (wordpress_profile_id);

-- Decisions: key choices with rationale
CREATE TABLE decisions (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    domain          TEXT NOT NULL,
    decided_at      DATE NOT NULL
    -- (related_facts, a BIGINT[] of fact-row ids, was dropped in 0006; supporting
    --  facts become cognee node-ids when the decisions layer is built — Phase 4)
);

-- Sources: trust-scored content origins for Tartt
CREATE TABLE sources (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    url                 TEXT NOT NULL,        -- feed URL or API endpoint
    source_kind         TEXT NOT NULL,        -- 'rss', 'hn', 'arxiv', 'youtube', 'newsletter'
    trust_score         REAL NOT NULL DEFAULT 0.5,
    last_polled_at      TIMESTAMPTZ,
    poll_interval_hours INTEGER NOT NULL DEFAULT 24,
    active              BOOLEAN NOT NULL DEFAULT true
);

-- Dashboard: cadence flags, system metrics (singleton)
CREATE TABLE dashboard (
    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    briefing_posted_at      TIMESTAMPTZ,
    last_tartt_run_at       TIMESTAMPTZ,
    last_tartt_item_count   INTEGER,
    open_followups_count    INTEGER,
    overdue_followups_count INTEGER,
    pending_approvals_count INTEGER,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

> **Note on fact links.** Every column that linked an operational row to a `facts`
> row was dropped in migration 0006 with the table itself: `outcomes` (fact link
> dropped entirely — operator decision, MW5), plus the empty-table columns
> `follow_ups.source_fact_id` and `decisions.related_facts`. The boundary pattern
> stands for the future: when an operational row needs to point at a graph fact,
> it holds the cognee **node-id as a TEXT column**, joined in app code (never a
> cross-DB FK). Those columns are added by the phases that actually populate the
> tables (Phase 4 action/decision layers), not carried as speculative int columns.

### Pipeline Tables

```sql
-- Content pipeline: state machine for discovered → published
CREATE TABLE content_pipeline (
    id                  BIGSERIAL PRIMARY KEY,
    content_item_id     BIGINT NOT NULL REFERENCES content_items(id),
    stage               TEXT NOT NULL,        -- discovered|triaged|drafted|sam_passed|approved|scheduled|published|declined
    triage_notes        TEXT,                 -- Keeley's `rationale` (merged call)
    draft_text          TEXT,                 -- Keeley's draft (merged call)
    sam_evaluation      JSONB,                -- Keeley's self_check; renamed `self_check`
                                              -- in the Phase-8 migration (2026-08-08)
    approval_id         BIGINT REFERENCES approval_queue(id),
    buffer_post_id      BIGINT REFERENCES buffer_posts(id),
    declined_reason     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX content_pipeline_stage_idx ON content_pipeline (stage, updated_at DESC);

-- Approval queue: pending human decisions in Discord (trust boundary B2)
CREATE TABLE approval_queue (
    id                  BIGSERIAL PRIMARY KEY,
    item_type           TEXT NOT NULL,        -- 'content_draft', 'outreach_message', 'other'
    item_ref_id         BIGINT NOT NULL,      -- FK to the relevant table (e.g., content_pipeline.id)
    payload             JSONB NOT NULL,       -- the thing to approve
    discord_message_id  TEXT,                 -- the message posted to #approvals
    status              TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|edited
    posted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at          TIMESTAMPTZ,
    edit_notes          TEXT
);

CREATE INDEX approval_queue_status_idx ON approval_queue (status, posted_at DESC);

-- Buffer posts: Buffer API status tracking
CREATE TABLE buffer_posts (
    id                  BIGSERIAL PRIMARY KEY,
    content_pipeline_id BIGINT NOT NULL REFERENCES content_pipeline(id),
    buffer_id           TEXT UNIQUE,          -- Buffer's own ID for the post
    channel             TEXT NOT NULL,        -- 'linkedin', 'x', etc.
    scheduled_for       TIMESTAMPTZ,
    posted_at           TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'queued',  -- queued|scheduled|posted|failed
    error_text          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX buffer_posts_status_idx ON buffer_posts (status, scheduled_for);
```

### Telemetry Tables

Full semantics in `80-telemetry-layer.md`; the schema lives here.

```sql
-- agent_runs: every LLM/embedding call recorded — own-agent calls AND cognee's
-- (labeled via the M1 callback, correlation_kind='cognify_run'). Feeds spend
-- metrics, the soft breaker, and cli/reconcile.
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    function_label  TEXT NOT NULL,          -- 'customer_discovery', 'infrastructure', …
    trigger_kind    TEXT NOT NULL,          -- 'scheduled', 'event', 'manual'
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL,          -- 'success', 'partial', 'failed'
    llm_provider    TEXT,                   -- 'gemini', 'anthropic', null
    llm_model       TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    usd_cost        NUMERIC(14,8),          -- widened from (10,4) in 0002 for sub-cent per-call precision
    correlation_id  TEXT,                   -- e.g. content_item_id, prospect_id, source_ref
    correlation_kind TEXT,                  -- 'content_item', 'prospect', 'cognify_run', …
    error_text      TEXT
);

CREATE INDEX agent_runs_agent_time_idx ON agent_runs (agent_name, started_at DESC);
CREATE INDEX agent_runs_status_idx     ON agent_runs (status) WHERE status != 'success';
CREATE INDEX agent_runs_function_idx   ON agent_runs (function_label, started_at DESC);

-- outcomes: attributed business outcomes (KR1 measurement substrate). No fact
-- link (dropped MW5 / migration 0006) — outcomes stand on their description.
CREATE TABLE outcomes (
    id                      BIGSERIAL PRIMARY KEY,
    outcome_type            TEXT NOT NULL,
                            -- 'discovery_call_booked', 'proposal_sent',
                            -- 'engagement_signed', 'engagement_renewed',
                            -- 'maintenance_converted', 'newsletter_published',
                            -- 'roundtable_topic_used', 'partnership_explored'
    outcome_value           NUMERIC,        -- nullable; $ where applicable
    description             TEXT NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    attributed_prospect_id  BIGINT REFERENCES prospects(id),
    attributed_content_id   BIGINT REFERENCES content_items(id),
    attributed_task_id      BIGINT REFERENCES tasks(id),
    attributed_signal_id    BIGINT REFERENCES icp_signals(id)
);

CREATE INDEX outcomes_type_time_idx ON outcomes (outcome_type, recorded_at DESC);
```

### Outreach tables (migration 0007 — Track O)

Five tables added by the outreach CRM; **`35-outreach-crm.md` §2 is the
authoritative DDL** and is not restated here. In brief:

| Table | Holds | Note |
|-------|-------|------|
| `outreach_targets` | One row per **company in a function state at a moment** | Deliberately distinct from person-level `prospects`; nullable `prospect_id` FK links the inbound case. Upsert key is `company_domain`. |
| `outreach_evidence` | Typed, sourced, **dated** facts (`first_seen_at` / `last_seen_at` / `closed_at`) | The proprietary longitudinal data — posting age cannot be bought retroactively. SQL rather than graph because the packet needs exact, dated, ordered facts, not fuzzy recall. |
| `outreach_touches` | Five per target, from the Selector; send/skip/reply state + per-touch `bcc_token` | Windows anchor on `trigger_date`. |
| `outreach_packets` | The assembled per-touch work payload | Deterministic query, no LLM; regenerated daily. |
| `outreach_events` | Full audit history | Written by an **AFTER trigger**, not app code — it must survive NocoDB raw UPDATEs. Actor via `current_setting('app.actor')`, falling back to `session_user`. |

Design constraint worth repeating at the schema layer: with no bot mediating
outreach writes, **every invariant is a CHECK constraint or trigger**
(skip-requires-reason, watchlist-requires-`stalled_reason`, sent-XOR-skipped,
reply-implies-send, snooze-window bounds, ready-gates-send). The scoring rubric's
S1 bands live in `outreach_s1()` in the migrations and **the migration is
authoritative** over any prose restatement. `verify_schema.sql` expects 24
operational tables once 0007 lands (19 as of 0006 + these five).

### Knowledge tables pending migration to the graph

`content_items`, `interest_signals`, `icp_signals`, and `meeting_transcripts`
still exist as **vectorized SQL tables** (migration 0001, `vector(768)` +
HNSW/GIN indexes). In the target state they are graph **DataPoints** (`ContentItem`,
`InterestSignal`, `ICPSignal`, `Meeting`), and they migrate to `aiadaptive_cognee`
as their producing phases are built (Tartt content — Phase 4; meetings — Phase 7;
ICP — Phase 8). Until then they remain as originally defined and are referenced by
the operational FKs above (`content_pipeline.content_item_id`,
`outcomes.attributed_content_id/attributed_signal_id`). New free-text knowledge
goes to the graph today via capture; these tables are not a second write target
for it.

---

## Backups

<backup_strategy>

Local Postgres means no managed backups — `pg_dump` is the only line of defense,
pulled forward from Phase 12 because the captured knowledge is irreplaceable.
**Both** databases are dumped (the graph, `aiadaptive_cognee`, holds all captured
knowledge — omitting it would leave the brain unprotected):

- **Nightly `pg_dump | gzip`** of `aiadaptive_cos` *and* `aiadaptive_cognee` to
  `~/agents/backups/nightly` on the Mac mini (Time Machine picks it up), 14 dumps
  retained per DB. Implemented in `scripts/pg_backup.sh`, run by the
  `nightly-backup` loop / `com.aiadaptive.cos.pg-backup` launchd job at 2:00
  (barry-agent). The cognee DSN is the operational db-url with the dbname swapped
  (matches `cognee_setup.cognee_dsn`).
- **Schema versioning in git**: every migration is a numbered SQL file. The
  operational DB rebuilds from migrations + a dump; the graph rebuilds from a dump
  (or, in the worst case, by re-cognifying the source notes).
- **Restore drill**: `gunzip -c <file> | psql <target-db-url>`.

</backup_strategy>

---

## Migration Convention

<migration_convention>

- Numbered SQL files: `migrations/0001_initial_schema.sql`, … (through 0006).
- One migration per logical change. No squashing.
- Forward-only by convention; rollback by writing a forward migration that undoes.
- Apply via `psql aiadaptive_cos -f migrations/NNNN_*.sql` (barry-admin socket
  superuser) or `psql "$DB_URL" -f ...` from barry-agent. New tables are handed to
  `barry_agent` explicitly (migrations run as the socket superuser).
- `migrations/verify_schema.sql` expects the current operational table set (19
  tables as of 0006). Cognee's own schema is managed by cognee, not by our
  migrations.

</migration_convention>

---

## Row Level Security (deferred)

<rls_strategy>

RLS is not enabled in v1 because there is one user. As multi-context use emerges
(sharing with partner, family use cases, future client-scoped data), RLS policies
will scope reads/writes per context on the operational tables; the graph's
isolation is handled by cognee datasets + the B1 trust boundary. Deferring RLS is
intentional — it adds debugging complexity and is not load-bearing for v1.

</rls_strategy>

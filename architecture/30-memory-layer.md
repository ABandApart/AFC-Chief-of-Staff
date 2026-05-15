# Memory Layer

<doc:layer>implementation</doc:layer>
<doc:stability>medium — schema migrations require versioned migration files</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md</doc:depends_on>
<doc:referenced_by>40-action-layer.md, 50-channel-layer.md, 60-content-pipeline.md</doc:referenced_by>

## Purpose

This file defines the Supabase Postgres schema, vectorization rules, and the hybrid search pattern. The brain is the single source of truth; all reads and writes flow through this schema.

---

## Deployment Configuration

<deployment>

- **Provider**: Hosted Supabase
- **Tier**: Pro ($25/mo) — required for daily backups, sufficient compute for projected volume
- **Region**: Whichever has lowest latency to the Mac mini's residential IP (typically us-east or us-west)
- **Extensions enabled**: `pgvector`, `pg_trgm` (for fuzzy text matching on names/titles)
- **Embedding model**: Gemini `text-embedding-004` (768 dimensions)
- **Dimension lock**: Once embeddings are stored, switching models requires re-embedding. Treat 768 as a hard commitment.

</deployment>

---

## Vectorization Decision Rules

<vectorization_rules>

<rule>Vectorize when retrieval is semantic — "find things like this concept" without exact keywords.</rule>
<rule>Do not vectorize when retrieval is structured — by status, date, owner, ID, or state.</rule>
<rule>Do not vectorize when the same record will be updated frequently — embedding cost compounds.</rule>
<rule>Vectorize the searchable representation, not raw text. E.g., embed an article summary, not the full HTML.</rule>

<vectorize_list>

| Table | Vectorize? | Rationale |
|-------|------------|-----------|
| `facts` | Yes | Semantic recall: "what did we discuss about pricing" |
| `content_items` | Yes | Similarity clustering, interest scoring |
| `interest_signals` | Yes | Topic vectors that get compared against content_items |
| `icp_signals` | Yes | Pain-point clustering by Nate Shelley |
| `meeting_transcripts` | Yes | Cross-meeting semantic search |
| `follow_ups` | No | Queried by status, deadline, escalation_level |
| `task_candidates` | No | Queried by status, source, confidence |
| `tasks` | No | Queried by status, owner, due_date |
| `decisions` | No | Queried by domain, date; reference text is short |
| `people` | No | Queried by name, last_contacted_at |
| `prospects` | No | Queried by fit score, source, status |
| `sources` | No | Queried by URL, trust_score |
| `dashboard` | No | Single-row or small-N metrics table |
| `content_pipeline` | No | State machine — queried by stage |
| `approval_queue` | No | Queried by status, posted_at |
| `buffer_posts` | No | Queried by buffer_id, status |
| `agent_runs` | No | Telemetry ledger; queried by agent, time, status |
| `outcomes` | No | Queried by type, attribution, time |

</vectorize_list>

</vectorization_rules>

---

## Schema

<schema>

The schema below is illustrative — exact field types and constraints will be refined during migration writing. Postgres-flavored SQL.

### Vectorized Tables

```sql
-- Facts: atomic claims with provenance and semantic recall
CREATE TABLE facts (
    id              BIGSERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    source_type     TEXT NOT NULL,            -- 'meeting', 'email', 'discord', 'manual'
    source_ref      TEXT,                     -- meeting_id, message_id, etc.
    context         TEXT,                     -- why this matters
    domain          TEXT,                     -- 'ai-adaptive', 'lead-engine', 'personal'
    confidence      REAL NOT NULL DEFAULT 1.0,
    embedding       vector(768),
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ               -- some facts have shelf life
);

CREATE INDEX facts_embedding_idx ON facts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX facts_tsv_idx       ON facts USING gin (content_tsv);
CREATE INDEX facts_domain_idx    ON facts (domain, created_at DESC);

-- Content items: discovered articles, videos, papers
CREATE TABLE content_items (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    source_id       BIGINT REFERENCES sources(id),
    content_type    TEXT NOT NULL,            -- 'article', 'video', 'paper', 'newsletter'
    raw_text        TEXT,                     -- extracted article body (trafilatura output)
    summary         TEXT,                     -- Gemini Flash output
    embedding       vector(768),              -- embedded from summary, not raw_text
    title_tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', title)) STORED,
    cluster_id      BIGINT,                   -- nullable; assigned by clustering job
    interest_score  REAL,                     -- computed at insert time vs. interest_signals
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    engaged_at      TIMESTAMPTZ,
    engagement_type TEXT                      -- 'read', 'saved', 'used-in-draft', 'declined'
);

CREATE INDEX content_items_embedding_idx ON content_items USING hnsw (embedding vector_cosine_ops);
CREATE INDEX content_items_tsv_idx       ON content_items USING gin (title_tsv);
CREATE INDEX content_items_cluster_idx   ON content_items (cluster_id, collected_at DESC);
CREATE INDEX content_items_interest_idx  ON content_items (interest_score DESC, collected_at DESC);

-- Interest signals: topic vectors with weight and decay
CREATE TABLE interest_signals (
    id                  BIGSERIAL PRIMARY KEY,
    topic_label         TEXT NOT NULL,
    embedding           vector(768) NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    origin              TEXT NOT NULL,        -- 'manual', 'inferred', 'engagement-derived'
    last_reinforced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX interest_signals_embedding_idx ON interest_signals USING hnsw (embedding vector_cosine_ops);

-- Meeting transcripts: compiled outputs
CREATE TABLE meeting_transcripts (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    meeting_date    DATE NOT NULL,
    participants    TEXT[],
    raw_path        TEXT,                     -- local path on Mac mini (Granola export)
    summary         TEXT NOT NULL,
    decisions_text  TEXT,                     -- structured extract: decisions made
    actions_text    TEXT,                     -- structured extract: action items
    embedding       vector(768),              -- embedded from summary
    summary_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX meeting_transcripts_embedding_idx ON meeting_transcripts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX meeting_transcripts_tsv_idx       ON meeting_transcripts USING gin (summary_tsv);
CREATE INDEX meeting_transcripts_date_idx      ON meeting_transcripts (meeting_date DESC);

-- ICP signals: pain points and friction observed across sources (W2 substrate)
-- Wide-net pattern: every agent that touches ICP-adjacent input writes here as side effect
CREATE TABLE icp_signals (
    id                  BIGSERIAL PRIMARY KEY,
    source_type         TEXT NOT NULL,    -- 'article', 'scorecard', 'meeting',
                                          -- 'discord_capture', 'email', 'prospect_form'
    source_agent        TEXT NOT NULL,    -- which agent emitted this
    source_ref          TEXT,             -- pointer to original (content_item_id, etc.)
    signal_text         TEXT NOT NULL,    -- the pain/friction language as written
    embedding           vector(768) NOT NULL,
    icp_segment_hint    TEXT,             -- 'l_and_d', 'tech_writing', 'hr_od', 'compliance_elearning', null
    pain_category_hint  TEXT,             -- 'time', 'expertise', 'scale', 'positioning', etc.
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    cluster_id          BIGINT            -- assigned weekly by Nate Shelley
);

CREATE INDEX icp_signals_embedding_idx ON icp_signals USING hnsw (embedding vector_cosine_ops);
CREATE INDEX icp_signals_recency_idx   ON icp_signals (observed_at DESC);
CREATE INDEX icp_signals_cluster_idx   ON icp_signals (cluster_id) WHERE cluster_id IS NOT NULL;
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
    source_fact_id      BIGINT REFERENCES facts(id),
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

-- Decisions: key choices with rationale
CREATE TABLE decisions (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    domain          TEXT NOT NULL,
    decided_at      DATE NOT NULL,
    related_facts   BIGINT[]                  -- fact IDs supporting the decision
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

-- Dashboard: cadence flags, system metrics
CREATE TABLE dashboard (
    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    briefing_posted_at      TIMESTAMPTZ,
    last_tartt_run_at       TIMESTAMPTZ,
    last_tartt_item_count   INTEGER,
    open_followups_count    INTEGER,
    overdue_followups_count INTEGER,
    pending_approvals_count INTEGER,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Pipeline Tables

```sql
-- Content pipeline: state machine for discovered → published
CREATE TABLE content_pipeline (
    id                  BIGSERIAL PRIMARY KEY,
    content_item_id     BIGINT NOT NULL REFERENCES content_items(id),
    stage               TEXT NOT NULL,        -- discovered|triaged|drafted|sam_passed|approved|scheduled|published|declined
    triage_notes        TEXT,                 -- Keeley Strategy output
    draft_text          TEXT,                 -- Keeley Content output
    sam_evaluation      JSONB,                -- Sam's structured eval result
    approval_id         BIGINT REFERENCES approval_queue(id),
    buffer_post_id      BIGINT REFERENCES buffer_posts(id),
    declined_reason     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX content_pipeline_stage_idx ON content_pipeline (stage, updated_at DESC);

-- Approval queue: pending human decisions in Discord
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

```sql
-- agent_runs: every LLM call recorded; feeds spend metrics, anomaly detection, cost-per-output
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    function_label  TEXT NOT NULL,          -- 'news_aggregation', 'topic_research',
                                            -- 'action_surfacing', 'customer_discovery',
                                            -- 'infrastructure', 'telemetry'
    trigger_kind    TEXT NOT NULL,          -- 'scheduled', 'event', 'manual'
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL,          -- 'success', 'partial', 'failed', 'token_cap_exceeded'
    llm_provider    TEXT,                   -- 'gemini', 'anthropic', null
    llm_model       TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    usd_cost        NUMERIC(10,4),
    correlation_id  TEXT,                   -- e.g. content_item_id, prospect_id
    correlation_kind TEXT,                  -- 'content_item', 'prospect', 'transcript', etc.
    error_text      TEXT
);

CREATE INDEX agent_runs_agent_time_idx ON agent_runs (agent_name, started_at DESC);
CREATE INDEX agent_runs_status_idx     ON agent_runs (status) WHERE status != 'success';
CREATE INDEX agent_runs_function_idx   ON agent_runs (function_label, started_at DESC);

-- outcomes: attributed business outcomes (KR1 measurement substrate)
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
    attributed_fact_id      BIGINT REFERENCES facts(id),
    attributed_signal_id    BIGINT REFERENCES icp_signals(id)
);

CREATE INDEX outcomes_type_time_idx ON outcomes (outcome_type, recorded_at DESC);
```

</schema>

---

## Hybrid Search

<hybrid_search>

Hybrid search combines lexical (full-text) and semantic (vector) ranking. Neither alone is sufficient:

- Full-text catches exact matches (names, specific terms, proper nouns) but misses synonyms.
- Vector similarity catches conceptual matches but can be noisy and miss specific terms.

The pattern below shows hybrid search over `facts`. The same pattern applies to `content_items` and `meeting_transcripts` — substitute the table name and embedded column.

<query_pattern lang="sql">

```sql
-- Hybrid search over facts: weighted combination of FTS rank and vector similarity
WITH query_input AS (
    SELECT
        plainto_tsquery('english', $1) AS tsq,
        $2::vector(768) AS query_embedding
),
fts_results AS (
    SELECT
        f.id,
        ts_rank_cd(f.content_tsv, q.tsq) AS lex_score
    FROM facts f, query_input q
    WHERE f.content_tsv @@ q.tsq
),
vec_results AS (
    SELECT
        f.id,
        1 - (f.embedding <=> q.query_embedding) AS sem_score
    FROM facts f, query_input q
    WHERE f.embedding IS NOT NULL
    ORDER BY f.embedding <=> q.query_embedding
    LIMIT 50
),
combined AS (
    SELECT
        COALESCE(fts.id, vec.id) AS id,
        COALESCE(fts.lex_score, 0) * 0.4 + COALESCE(vec.sem_score, 0) * 0.6 AS score
    FROM fts_results fts
    FULL OUTER JOIN vec_results vec USING (id)
)
SELECT f.*, c.score
FROM combined c
JOIN facts f ON f.id = c.id
ORDER BY c.score DESC
LIMIT $3;
```

</query_pattern>

<tuning_notes>

- **Weights (0.4 lex / 0.6 sem)**: Tunable. Sem-heavy for "what did we discuss about X" recall. Lex-heavy for name lookups. Start at 0.4/0.6 and adjust based on result quality.
- **Vector candidate pool**: Limit the vector subquery to 50 candidates before joining. HNSW indexes are fast but unbounded `ORDER BY embedding <=> ...` over a large table is wasteful.
- **Wrap as a Postgres function** for reuse: `hybrid_search_facts(query_text, query_embedding, limit, lex_weight, sem_weight)`.

</tuning_notes>

</hybrid_search>

---

## Row Level Security (deferred)

<rls_strategy>

RLS is not enabled in v1 because there is one user. As multi-context use emerges (sharing with partner, family calendar use cases, future client-scoped data), RLS policies will scope reads/writes per context.

When RLS is enabled:
- A `context_id` column on relevant tables identifies the scope (personal, ai-adaptive-work, family).
- The anon key sees only `context_id = current_setting('app.context_id')`.
- The service-role key bypasses RLS (used by the Mac mini agents).

Deferring this to a later phase is intentional. RLS adds debugging complexity and is not load-bearing for v1.

</rls_strategy>

---

## Backups

<backup_strategy>

Supabase Pro provides daily managed backups with 7-day retention. This is sufficient as a baseline.

Additional belt-and-suspenders:

- **Weekly `pg_dump`** to encrypted local storage on the Mac mini, retained for 90 days.
- **Schema versioning in git**: every migration is a numbered SQL file in the architecture repo. The brain can be rebuilt from migrations + a dump.

<backup_script_sketch>

```bash
# ~/Scripts/brain_backup.sh, run by launchd weekly Sunday 3am
PGPASSWORD="$(security find-generic-password -s supabase-db-password -w)" \
  pg_dump \
    --host=db.PROJECT.supabase.co \
    --username=postgres \
    --dbname=postgres \
    --no-owner --no-acl \
    --file=/tmp/brain_$(date +%Y%m%d).sql

# Encrypt with age (or gpg)
age -r "$(cat ~/.config/brain-backup.pub)" \
  -o /Volumes/Backup/brain/brain_$(date +%Y%m%d).sql.age \
  /tmp/brain_$(date +%Y%m%d).sql

rm /tmp/brain_$(date +%Y%m%d).sql

# Prune > 90 days
find /Volumes/Backup/brain -name 'brain_*.sql.age' -mtime +90 -delete
```

</backup_script_sketch>

</backup_strategy>

---

## Migration Convention

<migration_convention>

- Numbered SQL files: `migrations/0001_initial_schema.sql`, `migrations/0002_add_buffer_posts.sql`, etc.
- One migration per logical change. No squashing.
- Forward-only by convention; rollback by writing a forward migration that undoes.
- Apply via Supabase SQL editor for v1; later, automate via the Supabase CLI.

</migration_convention>

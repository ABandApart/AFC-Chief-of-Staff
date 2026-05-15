-- =============================================================================
-- Migration 0001: Initial schema
-- AI Adaptive Chief of Staff — Phase 1 Foundation
-- =============================================================================
-- Apply via Supabase SQL Editor or:
--   psql "$DB_URL" -f migrations/0001_initial_schema.sql
--
-- Idempotent: safe to re-run (uses IF NOT EXISTS where applicable).
-- Tables are created in dependency order to satisfy foreign-key references.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- -----------------------------------------------------------------------------
-- Base reference tables (no FKs into other app tables)
-- -----------------------------------------------------------------------------

-- Sources: trust-scored content origins for Tartt
CREATE TABLE IF NOT EXISTS sources (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    url                 TEXT NOT NULL,
    source_kind         TEXT NOT NULL,        -- 'rss', 'hn', 'arxiv', 'youtube', 'newsletter'
    trust_score         REAL NOT NULL DEFAULT 0.5,
    last_polled_at      TIMESTAMPTZ,
    poll_interval_hours INTEGER NOT NULL DEFAULT 24,
    active              BOOLEAN NOT NULL DEFAULT true
);

-- People: relationship context
CREATE TABLE IF NOT EXISTS people (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    relationship_type   TEXT NOT NULL,
    last_contacted_at   TIMESTAMPTZ,
    context_notes       TEXT,
    cadence_days        INTEGER
);

CREATE INDEX IF NOT EXISTS people_name_trgm_idx ON people USING gin (name gin_trgm_ops);

-- Decisions: key choices with rationale
CREATE TABLE IF NOT EXISTS decisions (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    rationale       TEXT NOT NULL,
    domain          TEXT NOT NULL,
    decided_at      DATE NOT NULL,
    related_facts   BIGINT[]
);

CREATE INDEX IF NOT EXISTS decisions_domain_idx ON decisions (domain, decided_at DESC);

-- Dashboard: singleton metrics and cadence flags
CREATE TABLE IF NOT EXISTS dashboard (
    id                      INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    briefing_posted_at      TIMESTAMPTZ,
    last_tartt_run_at       TIMESTAMPTZ,
    last_tartt_item_count   INTEGER,
    last_ted_check_at       TIMESTAMPTZ,
    last_higgins_run_at     TIMESTAMPTZ,
    notes                   TEXT
);

-- Seed the dashboard singleton (do nothing if already present)
INSERT INTO dashboard (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Vectorized tables (semantic recall)
-- -----------------------------------------------------------------------------

-- Facts: atomic claims with provenance and semantic recall
CREATE TABLE IF NOT EXISTS facts (
    id              BIGSERIAL PRIMARY KEY,
    content         TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_ref      TEXT,
    context         TEXT,
    domain          TEXT,
    confidence      REAL NOT NULL DEFAULT 1.0,
    embedding       vector(768),
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS facts_embedding_idx ON facts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS facts_tsv_idx       ON facts USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS facts_domain_idx    ON facts (domain, created_at DESC);

-- Content items: discovered articles, videos, papers
CREATE TABLE IF NOT EXISTS content_items (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    source_id       BIGINT REFERENCES sources(id),
    content_type    TEXT NOT NULL,
    raw_text        TEXT,
    summary         TEXT,
    embedding       vector(768),
    title_tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', title)) STORED,
    cluster_id      BIGINT,
    interest_score  REAL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    engaged_at      TIMESTAMPTZ,
    engagement_type TEXT
);

CREATE INDEX IF NOT EXISTS content_items_embedding_idx ON content_items USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS content_items_tsv_idx       ON content_items USING gin (title_tsv);
CREATE INDEX IF NOT EXISTS content_items_cluster_idx   ON content_items (cluster_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS content_items_interest_idx  ON content_items (interest_score DESC, collected_at DESC);

-- Interest signals: topic vectors with weight and decay
CREATE TABLE IF NOT EXISTS interest_signals (
    id                  BIGSERIAL PRIMARY KEY,
    topic_label         TEXT NOT NULL,
    embedding           vector(768) NOT NULL,
    weight              REAL NOT NULL DEFAULT 1.0,
    origin              TEXT NOT NULL,
    last_reinforced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS interest_signals_embedding_idx ON interest_signals USING hnsw (embedding vector_cosine_ops);

-- Meeting transcripts: compiled outputs from Granola
CREATE TABLE IF NOT EXISTS meeting_transcripts (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    meeting_date    DATE NOT NULL,
    participants    TEXT[],
    raw_path        TEXT,
    summary         TEXT NOT NULL,
    decisions_text  TEXT,
    actions_text    TEXT,
    embedding       vector(768),
    summary_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS meeting_transcripts_embedding_idx ON meeting_transcripts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS meeting_transcripts_tsv_idx       ON meeting_transcripts USING gin (summary_tsv);
CREATE INDEX IF NOT EXISTS meeting_transcripts_date_idx      ON meeting_transcripts (meeting_date DESC);

-- ICP signals: pain points and friction observed across sources (W2 substrate)
-- Wide-net pattern: every agent that touches ICP-adjacent input writes here as side effect
CREATE TABLE IF NOT EXISTS icp_signals (
    id                  BIGSERIAL PRIMARY KEY,
    source_type         TEXT NOT NULL,
    source_agent        TEXT NOT NULL,
    source_ref          TEXT,
    signal_text         TEXT NOT NULL,
    embedding           vector(768) NOT NULL,
    icp_segment_hint    TEXT,
    pain_category_hint  TEXT,
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    cluster_id          BIGINT
);

CREATE INDEX IF NOT EXISTS icp_signals_embedding_idx ON icp_signals USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS icp_signals_recency_idx   ON icp_signals (observed_at DESC);
CREATE INDEX IF NOT EXISTS icp_signals_cluster_idx   ON icp_signals (cluster_id) WHERE cluster_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Structured tables (referencing people / meeting_transcripts / facts)
-- -----------------------------------------------------------------------------

-- Follow-ups: open commitments with escalation
CREATE TABLE IF NOT EXISTS follow_ups (
    id                  BIGSERIAL PRIMARY KEY,
    owner               TEXT NOT NULL,
    action              TEXT NOT NULL,
    deadline            DATE,
    source_meeting_id   BIGINT REFERENCES meeting_transcripts(id),
    source_fact_id      BIGINT REFERENCES facts(id),
    status              TEXT NOT NULL DEFAULT 'open',
    escalation_level    SMALLINT NOT NULL DEFAULT 0,
    draft_followup_msg  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS follow_ups_status_idx     ON follow_ups (status, escalation_level DESC);
CREATE INDEX IF NOT EXISTS follow_ups_owner_idx      ON follow_ups (owner, status);
CREATE INDEX IF NOT EXISTS follow_ups_deadline_idx   ON follow_ups (deadline) WHERE status = 'open';

-- Task candidates: Task Tinder queue
CREATE TABLE IF NOT EXISTS task_candidates (
    id                  BIGSERIAL PRIMARY KEY,
    proposed_action     TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    source_ref          TEXT,
    evidence_text       TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0.5,
    status              TEXT NOT NULL DEFAULT 'pending',
    discord_message_id  TEXT,
    decided_at          TIMESTAMPTZ,
    deferred_until      DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS task_candidates_status_idx     ON task_candidates (status, confidence DESC);
CREATE INDEX IF NOT EXISTS task_candidates_deferred_idx   ON task_candidates (deferred_until) WHERE status = 'deferred';

-- Tasks: accepted task candidates
CREATE TABLE IF NOT EXISTS tasks (
    id                  BIGSERIAL PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT,
    owner               TEXT NOT NULL DEFAULT 'self',
    due_date            DATE,
    source_candidate_id BIGINT REFERENCES task_candidates(id),
    status              TEXT NOT NULL DEFAULT 'active',
    follow_up_id        BIGINT REFERENCES follow_ups(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status, due_date);

-- Prospects: inbound qualified leads from WordPress Lead Engine (W1)
CREATE TABLE IF NOT EXISTS prospects (
    id                      BIGSERIAL PRIMARY KEY,
    wordpress_profile_id    TEXT NOT NULL,
    person_id               BIGINT REFERENCES people(id),
    name                    TEXT NOT NULL,
    email                   TEXT,
    company                 TEXT,
    role                    TEXT,
    source_form             TEXT NOT NULL,
    raw_profile             JSONB NOT NULL,
    icp_segment             TEXT,
    icp_fit_score           REAL,
    fit_reasoning           TEXT,
    status                  TEXT NOT NULL DEFAULT 'new',
    received_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    qualified_at            TIMESTAMPTZ,
    last_status_change_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS prospects_status_idx     ON prospects (status, received_at DESC);
CREATE INDEX IF NOT EXISTS prospects_fit_idx        ON prospects (icp_fit_score DESC NULLS LAST) WHERE status IN ('new', 'qualified');
CREATE UNIQUE INDEX IF NOT EXISTS prospects_wp_idx  ON prospects (wordpress_profile_id);

-- -----------------------------------------------------------------------------
-- Pipeline state tables
-- -----------------------------------------------------------------------------

-- Approval queue: pending human decisions in Discord
CREATE TABLE IF NOT EXISTS approval_queue (
    id                  BIGSERIAL PRIMARY KEY,
    item_type           TEXT NOT NULL,
    item_ref_id         BIGINT NOT NULL,
    payload             JSONB NOT NULL,
    discord_message_id  TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    posted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at          TIMESTAMPTZ,
    edit_notes          TEXT
);

CREATE INDEX IF NOT EXISTS approval_queue_status_idx ON approval_queue (status, posted_at DESC);

-- Content pipeline: discovered → published state machine
CREATE TABLE IF NOT EXISTS content_pipeline (
    id                  BIGSERIAL PRIMARY KEY,
    content_item_id     BIGINT NOT NULL REFERENCES content_items(id),
    stage               TEXT NOT NULL,
    triage_notes        TEXT,
    draft_text          TEXT,
    sam_evaluation      JSONB,
    approval_id         BIGINT REFERENCES approval_queue(id),
    buffer_post_id      BIGINT,
    declined_reason     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS content_pipeline_stage_idx ON content_pipeline (stage, updated_at DESC);

-- Buffer posts: tracking status of scheduled and published posts
CREATE TABLE IF NOT EXISTS buffer_posts (
    id                  BIGSERIAL PRIMARY KEY,
    content_pipeline_id BIGINT NOT NULL REFERENCES content_pipeline(id),
    buffer_id           TEXT UNIQUE,
    channel             TEXT NOT NULL,
    scheduled_for       TIMESTAMPTZ,
    posted_at           TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'queued',
    error_text          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS buffer_posts_status_idx ON buffer_posts (status, scheduled_for);

-- Backfill the buffer_post_id FK on content_pipeline now that buffer_posts exists.
-- Wrapped in DO block so re-runs don't error on the duplicate constraint.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'content_pipeline_buffer_post_fk'
    ) THEN
        ALTER TABLE content_pipeline
            ADD CONSTRAINT content_pipeline_buffer_post_fk
            FOREIGN KEY (buffer_post_id) REFERENCES buffer_posts(id);
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- Telemetry tables
-- -----------------------------------------------------------------------------

-- agent_runs: every LLM call recorded; populated by the cost helper (Phase 2)
CREATE TABLE IF NOT EXISTS agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    function_label  TEXT NOT NULL,
    trigger_kind    TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL,
    llm_provider    TEXT,
    llm_model       TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    usd_cost        NUMERIC(10,4),
    correlation_id  TEXT,
    correlation_kind TEXT,
    error_text      TEXT
);

CREATE INDEX IF NOT EXISTS agent_runs_agent_time_idx ON agent_runs (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_status_idx     ON agent_runs (status) WHERE status != 'success';
CREATE INDEX IF NOT EXISTS agent_runs_function_idx   ON agent_runs (function_label, started_at DESC);

-- outcomes: attributed business outcomes (KR1 measurement substrate)
CREATE TABLE IF NOT EXISTS outcomes (
    id                      BIGSERIAL PRIMARY KEY,
    outcome_type            TEXT NOT NULL,
    outcome_value           NUMERIC,
    description             TEXT NOT NULL,
    recorded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    attributed_prospect_id  BIGINT REFERENCES prospects(id),
    attributed_content_id   BIGINT REFERENCES content_items(id),
    attributed_task_id      BIGINT REFERENCES tasks(id),
    attributed_fact_id      BIGINT REFERENCES facts(id),
    attributed_signal_id    BIGINT REFERENCES icp_signals(id)
);

CREATE INDEX IF NOT EXISTS outcomes_type_time_idx ON outcomes (outcome_type, recorded_at DESC);

COMMIT;

-- =============================================================================
-- End of migration 0001
-- =============================================================================

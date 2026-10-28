-- =============================================================================
-- Migration 0033: okr_objectives, okr_key_results, okr_initiatives
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N2 (CoS side).
-- Read-only cache of the Notion master OKR databases. Notion owns every field
-- (N-S1); `agents/notion/okr_pull.py` refreshes these rows every 15 minutes and
-- nothing else writes them.
--
-- Each row is keyed by its Notion page id. `id` is the stable CoS key that later
-- increments reference (N3: tasks.okr_initiative_id; N4: okr_snapshots).
--
-- `problem` holds why a page is malformed (no Objective, more than one Key
-- Result, an unknown Metric, ...), or NULL. A row with a problem is still cached
-- so the operator's page is visible, but consumers treat it as rejected: read
-- `WHERE archived_at IS NULL AND problem IS NULL`. `problem_notified_on` makes the
-- #system notice fire once per page per day.
--
-- `archived_at` is set when the page leaves its database (deleted, trashed, or
-- moved) and, for Objectives and Key Results, when Status is Retired. It clears
-- if the page comes back.
--
-- Apply: psql aiadaptive_cos -f migrations/0033_okr_tables.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS okr_objectives (
    id                  BIGSERIAL PRIMARY KEY,
    notion_page_id      TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    quarter             TEXT,                 -- 'YYYY-Qn'
    sort_order          NUMERIC,
    status              TEXT,                 -- Active | Retired
    problem             TEXT,
    problem_notified_on DATE,
    notion_edited_at    TIMESTAMPTZ,
    pulled_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS okr_key_results (
    id                  BIGSERIAL PRIMARY KEY,
    notion_page_id      TEXT NOT NULL UNIQUE,
    objective_id        BIGINT REFERENCES okr_objectives(id),
    name                TEXT NOT NULL,
    metric              TEXT,                 -- an okr_schema.METRICS id, or 'manual'
    target              NUMERIC,
    unit                TEXT,
    direction           TEXT,                 -- Higher is better | Lower is better
    manual_value        NUMERIC,
    status              TEXT,                 -- Active | Retired
    problem             TEXT,
    problem_notified_on DATE,
    notion_edited_at    TIMESTAMPTZ,
    pulled_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS okr_initiatives (
    id                  BIGSERIAL PRIMARY KEY,
    notion_page_id      TEXT NOT NULL UNIQUE,
    key_result_id       BIGINT REFERENCES okr_key_results(id),
    name                TEXT NOT NULL,
    status              TEXT,                 -- Active | Paused | Done
    owner_name          TEXT,
    problem             TEXT,
    problem_notified_on DATE,
    notion_edited_at    TIMESTAMPTZ,
    pulled_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS okr_key_results_objective_idx ON okr_key_results (objective_id);
CREATE INDEX IF NOT EXISTS okr_initiatives_key_result_idx ON okr_initiatives (key_result_id);

COMMENT ON TABLE okr_objectives IS
    'Cache of the Notion Objectives database (PRD-notion-integration.md N2). Notion owns it; okr_pull refreshes it.';
COMMENT ON TABLE okr_key_results IS
    'Cache of the Notion Key Results database (PRD-notion-integration.md N2). Notion owns it; okr_pull refreshes it.';
COMMENT ON TABLE okr_initiatives IS
    'Cache of the Notion Initiatives database (PRD-notion-integration.md N2). Notion owns it; okr_pull refreshes it.';

ALTER TABLE okr_objectives             OWNER TO barry_agent;
ALTER TABLE okr_key_results            OWNER TO barry_agent;
ALTER TABLE okr_initiatives            OWNER TO barry_agent;
ALTER SEQUENCE okr_objectives_id_seq   OWNER TO barry_agent;
ALTER SEQUENCE okr_key_results_id_seq  OWNER TO barry_agent;
ALTER SEQUENCE okr_initiatives_id_seq  OWNER TO barry_agent;

COMMIT;

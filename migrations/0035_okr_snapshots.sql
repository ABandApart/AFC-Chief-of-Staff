-- =============================================================================
-- Migration 0035: okr_snapshots
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N4 (CoS side).
-- One row per active Key Result per week, written by the Monday dashboard push
-- (`agents/notion/dashboard_push.py`) for the week that just ended. It feeds the
-- Notion Weekly Snapshots database and its trend charts.
--
-- `week_start` is the Monday that starts the week. A re-run in the same week
-- overwrites that week's rows (UNIQUE (week_start, okr_key_result_id)).
-- `metric` is stored because the operator can rebind a KR to another metric
-- mid-quarter. `value` is NULL when the metric returned no_data. `target` and
-- `status` are NULL on rows from the one-time backfill (cli.notion_backfill_snapshots),
-- because no target existed for those weeks.
--
-- Apply: psql aiadaptive_cos -f migrations/0035_okr_snapshots.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS okr_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    week_start        DATE NOT NULL CHECK (extract(isodow FROM week_start) = 1),
    okr_key_result_id BIGINT NOT NULL REFERENCES okr_key_results(id),
    metric            TEXT NOT NULL,
    value             NUMERIC,
    target            NUMERIC,
    status            TEXT,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (week_start, okr_key_result_id)
);

ALTER TABLE okr_snapshots OWNER TO barry_agent;
ALTER SEQUENCE okr_snapshots_id_seq OWNER TO barry_agent;

COMMENT ON TABLE okr_snapshots IS
    'Weekly KR values for the Notion dashboard trend charts (PRD-notion-integration.md N4).';

COMMIT;

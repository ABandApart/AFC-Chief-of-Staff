-- =============================================================================
-- Migration 0037: export schema for the nightly docent push
-- =============================================================================
-- Spec: architecture/PRD-docent-export.md.
--
-- Docent (the operator's web app on Supabase) gets themes and market segments,
-- and nothing else. These two views are the allowlist: the export job reads only
-- `export.*`, so a column added to a source table never reaches docent. Each
-- view names its columns; `agents/docent/export.py` COLUMNS must match.
--
-- Same containment as 0008: the views are owned by the migration runner, and
-- `brain_reader` gets SELECT on the views only, never on the base tables.
--
-- Leaves out on purpose: `outreach_segment_scores.rationale` and `rated_by`
-- (free text), `interest_signals.embedding` and `origin`, and the hypothesis
-- segments (`in_list = false`, open decision OD-2).
--
-- Apply: psql aiadaptive_cos -f migrations/0037_docent_export_views.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS export;

CREATE OR REPLACE VIEW export.themes AS
    SELECT topic_label, weight, last_reinforced_at
    FROM interest_signals;

CREATE OR REPLACE VIEW export.market_segments AS
    SELECT s.label,
           sc.market_size, sc.market_growth, sc.firm_profitability,
           sc.ability_to_pay, sc.urgency_pain, sc.offering_fit,
           sc.rated_at
    FROM outreach_segments s
    LEFT JOIN outreach_segment_scores sc ON sc.segment = s.key
    WHERE s.in_list;

GRANT USAGE ON SCHEMA export TO brain_reader;
GRANT SELECT ON export.themes, export.market_segments TO brain_reader;

COMMIT;

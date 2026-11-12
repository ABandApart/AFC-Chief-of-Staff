-- =============================================================================
-- Migration 0038: outreach leads in Notion, and funding stage dropped (N8)
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N8 (operator
-- 2026-11-12).
--
-- 1. `outreach_targets.start_on`: the Start on date pulled from Notion Leads.
--    `notion-leads-pull` starts a candidate's sequence on or after that day.
-- 2. `outreach_targets_seq_ck` no longer requires `stage` (R1). Funding stage is
--    not an outreach criterion: the ICP is services firms, not startups. Slot 1
--    has a stage-free rule set (`config/outreach/selector.yaml`, `any`).
-- 3. `s2_stage_fit` now holds size fit from headcount (R3). The column keeps its
--    name so `v_outreach_scored` and `outreach/rescore.py` don't change. Existing
--    rows are recomputed here, from `headcount` or else the source discovery's
--    `headcount_band`, matching `outreach.size_fit_from_band`.
-- 4. `opted_out` is a new `status` value. The column has no value CHECK, so it
--    needs no DDL.
--
-- Apply: psql aiadaptive_cos -f migrations/0038_outreach_leads.sql
-- Idempotent.
-- =============================================================================

BEGIN;

ALTER TABLE outreach_targets ADD COLUMN IF NOT EXISTS start_on DATE;

COMMENT ON COLUMN outreach_targets.start_on IS
    'Day to start the sequence, set in Notion Leads (N8). Read by notion-leads-pull.';

CREATE INDEX IF NOT EXISTS outreach_targets_start_on_idx
    ON outreach_targets (start_on) WHERE status = 'candidate' AND start_on IS NOT NULL;

ALTER TABLE outreach_targets DROP CONSTRAINT IF EXISTS outreach_targets_seq_ck;
ALTER TABLE outreach_targets ADD CONSTRAINT outreach_targets_seq_ck
    CHECK (status <> 'in_sequence'
           OR (function_state IS NOT NULL AND sequence_started_at IS NOT NULL));

COMMENT ON COLUMN outreach_targets.stage IS
    'Funding stage, optional and informational (N8 R1). Selects slot-1 rules when set.';

COMMENT ON COLUMN outreach_targets.s2_stage_fit IS
    'S2 = size fit from headcount (N8 R3): 10-100 = 5, 101-250 = 3, unknown = 3, else 1. '
    'Named for its old meaning, funding-stage fit.';

WITH sized AS (
    SELECT t.id,
           COALESCE(
               t.headcount,
               (SELECT CASE
                         WHEN m2 IS NOT NULL THEN (m2[1]::int + m2[2]::int) / 2
                         WHEN m1 IS NOT NULL THEN m1[1]::int
                       END
                FROM outreach_discoveries d,
                     LATERAL (SELECT regexp_match(d.headcount_band, '(\d+)\D+(\d+)') AS m2,
                                     regexp_match(d.headcount_band, '(\d+)')          AS m1) x
                WHERE d.promoted_target_id = t.id
                LIMIT 1)
           ) AS staff
    FROM outreach_targets t
)
UPDATE outreach_targets t
SET s2_stage_fit = CASE
        WHEN s.staff IS NULL              THEN 3
        WHEN s.staff BETWEEN 10 AND 100   THEN 5
        WHEN s.staff BETWEEN 101 AND 250  THEN 3
        ELSE 1
    END
FROM sized s
WHERE s.id = t.id;

COMMIT;

-- =============================================================================
-- Migration 0019: operator-enterable segment scores + extracted-candidate source
-- =============================================================================
-- Two additions, both from operator decisions on 2026-08-20.
--
-- 1. `outreach_segment_scores` — OQ-L / R0.20.
--    The six ICP criteria stop being a Python constant. The operator asked for
--    "an affordance for me to input category ratings when I can provide them",
--    standing open rather than unlocking at a threshold, so the ratings need a
--    home the code can read at runtime.
--
--    `agents/outreach/icp.py` reads this table first and falls back to its
--    hardcoded transcription of the workbook for the three segments already
--    rated. The workbook stays the system of record for the market model
--    (R4.6) — Part 4 reports proposed weights back to it for approval and never
--    writes (OQ-J).
--
--    A row existing is also what makes a segment "scored", which removes it from
--    the unscored bucket of the daily window (R0.18). So rating a category
--    visibly changes the next day's queue, which is the feedback that makes the
--    affordance worth using.
--
-- 2. `outreach_discoveries.source_url` — R0.21.
--    Entity extraction proposes a company name from a news item. The operator
--    validates that name during review, and he can only do that if the item that
--    produced it is in front of him. This is a SAFETY property of sanctioning an
--    unreliable step, not a display nicety, so it gets a column rather than a
--    suffix on the verification note.
--
-- No `v_outreach_scored` rebuild: neither change touches `outreach_targets`.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0019_outreach_segment_scores.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- 1. Operator-entered segment ratings --------------------------------------

CREATE TABLE IF NOT EXISTS outreach_segment_scores (
    segment             TEXT PRIMARY KEY,

    -- The workbook's six criteria, 1-5, same scale and same names the operator
    -- already uses. Weights live in code (they are the model, not the data);
    -- these are the per-segment ratings only.
    market_size         SMALLINT NOT NULL,
    market_growth       SMALLINT NOT NULL,
    firm_profitability  SMALLINT NOT NULL,
    ability_to_pay      SMALLINT NOT NULL,
    urgency_pain        SMALLINT NOT NULL,
    offering_fit        SMALLINT NOT NULL,

    rationale           TEXT,
    rated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    rated_by            TEXT NOT NULL DEFAULT session_user,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Same six-value vocabulary as outreach_discoveries.segment (0018, R0.1).
    CONSTRAINT outreach_segment_scores_segment_ck CHECK (segment IN (
        'corporate_l_and_d',
        'coaching_leadership',
        'instructional_design',
        'engineering_consultancy',
        'product_design_agency',
        'msp_it_consultancy'
    )),

    -- 1/3/5 is the workbook's scale everywhere else in this system
    -- (outreach_targets_scores_ck). Here the sheet uses the full 1-5 range, so
    -- the constraint follows the sheet rather than the other table - noted
    -- because the inconsistency is real and deliberate.
    CONSTRAINT outreach_segment_scores_range_ck CHECK (
        market_size        BETWEEN 1 AND 5
        AND market_growth      BETWEEN 1 AND 5
        AND firm_profitability BETWEEN 1 AND 5
        AND ability_to_pay     BETWEEN 1 AND 5
        AND urgency_pain       BETWEEN 1 AND 5
        AND offering_fit       BETWEEN 1 AND 5
    )
);

COMMENT ON TABLE outreach_segment_scores IS
    'Operator-entered ICP criteria per segment (R0.20). Read first by '
    'agents/outreach/icp.py, which falls back to its hardcoded transcription of '
    'the workbook. A row here makes a segment "scored" and removes it from the '
    'unscored bucket of the daily review window.';

COMMENT ON COLUMN outreach_segment_scores.rated_by IS
    'session_user at insert. Distinguishes an operator rating from a seeded one.';

DROP TRIGGER IF EXISTS outreach_segment_scores_audit ON outreach_segment_scores;
DROP TRIGGER IF EXISTS outreach_segment_scores_touch ON outreach_segment_scores;

CREATE TRIGGER outreach_segment_scores_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_segment_scores
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

CREATE TRIGGER outreach_segment_scores_touch
    BEFORE UPDATE ON outreach_segment_scores
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();

ALTER TABLE outreach_segment_scores OWNER TO barry_agent;

-- --- 2. The article an extracted candidate came from ---------------------------

ALTER TABLE outreach_discoveries
    ADD COLUMN IF NOT EXISTS source_url TEXT;

COMMENT ON COLUMN outreach_discoveries.source_url IS
    'The news or list item an extracted candidate was named from (R0.21). Shown '
    'on the review card beside the name so a wrong entity is visible rather than '
    'inferred - the operator validating the name is what makes a bounded, '
    'unreliable extraction step safe to run at all.';

COMMIT;

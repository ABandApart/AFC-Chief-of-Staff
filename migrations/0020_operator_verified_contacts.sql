-- =============================================================================
-- Migration 0020: operator-verified contacts, on both sides of promotion
-- =============================================================================
-- Testing the review sheet showed what a real dataset always shows: some contact
-- details are wrong or stale. The operator asked for an edit affordance, and two
-- facts about the current schema shape it.
--
-- 1. `email_confidence` had no value for "I checked this myself".
--    The three values came from the operator's workbook - `public`,
--    `inferred_pattern`, `general_inbox` - and none of them means verified. An
--    address the operator personally confirmed is stronger evidence than a
--    pattern guess, so correcting one by hand should RAISE its confidence rather
--    than silently keep the old label. `operator_verified` is added to both
--    tables' CHECK.
--
-- 2. `outreach_targets` had NO `email_confidence` column at all.
--    So a raised confidence would have been invisible exactly where it matters -
--    the packet reads targets, not discoveries, and the send decision happens
--    there. Adding it is what makes the correction reach send time.
--
-- THE VIEW REBUILD IS MANDATORY, NOT INCIDENTAL. `v_outreach_scored` is
-- `SELECT t.*` and a view's column list is FROZEN at creation, so
-- `CREATE OR REPLACE` cannot absorb a new base column - it must be dropped and
-- recreated. This is the trap 0016 hit and `verify_schema.sql` now asserts
-- against.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0020_operator_verified_contacts.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- 1. The new confidence level, on the pool -------------------------------
ALTER TABLE outreach_discoveries
    DROP CONSTRAINT IF EXISTS outreach_discoveries_confidence_ck;

ALTER TABLE outreach_discoveries
    ADD CONSTRAINT outreach_discoveries_confidence_ck CHECK (
        email_confidence IS NULL
        OR email_confidence IN ('public', 'inferred_pattern', 'general_inbox',
                                'operator_verified')
    );

-- --- 2. The same field on targets, so the correction survives promotion ------
ALTER TABLE outreach_targets
    ADD COLUMN IF NOT EXISTS email_confidence TEXT;

ALTER TABLE outreach_targets
    DROP CONSTRAINT IF EXISTS outreach_targets_confidence_ck;

ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_confidence_ck CHECK (
        email_confidence IS NULL
        OR email_confidence IN ('public', 'inferred_pattern', 'general_inbox',
                                'operator_verified')
    );

COMMENT ON COLUMN outreach_targets.email_confidence IS
    'How well the contact address is known. Mirrors outreach_discoveries so a '
    'correction made before promotion is not lost at promotion. '
    'operator_verified means the operator confirmed it by hand.';

-- --- 3. Rebuild the scored view (see the header) -----------------------------
DROP VIEW IF EXISTS v_outreach_scored;

CREATE VIEW v_outreach_scored AS
SELECT id,
    company_name,
    company_domain,
    company_url,
    careers_url,
    sector,
    stage,
    function_state,
    contact_name,
    contact_role,
    contact_email,
    contact_linkedin_url,
    trigger_kind,
    trigger_date,
    trigger_source_url,
    s2_stage_fit,
    s3_sector_match,
    s4_leadership_gap,
    s5_team_build_below,
    signals_observed_at,
    status,
    is_reengagement,
    prospect_id,
    cognee_node_id,
    sequence_started_at,
    sequence_completed_at,
    stalled_reason,
    watch_trigger,
    watch_until,
    created_at,
    updated_at,
    function,
    contact_first_name,
    intake_message_id,
    email_confidence,
    outreach_s1(trigger_date) AS s1_trigger_recency,
    CURRENT_DATE - trigger_date AS days_since_trigger,
        CASE
            WHEN s2_stage_fit IS NULL OR s3_sector_match IS NULL OR s4_leadership_gap IS NULL OR s5_team_build_below IS NULL THEN NULL::smallint
            ELSE outreach_s1(trigger_date) + s2_stage_fit + s3_sector_match + s4_leadership_gap + s5_team_build_below
        END AS score,
        CASE
            WHEN s2_stage_fit IS NULL OR s3_sector_match IS NULL OR s4_leadership_gap IS NULL OR s5_team_build_below IS NULL THEN NULL::text
            WHEN (outreach_s1(trigger_date) + s2_stage_fit + s3_sector_match + s4_leadership_gap + s5_team_build_below) >= 20 THEN 'work'::text
            WHEN (outreach_s1(trigger_date) + s2_stage_fit + s3_sector_match + s4_leadership_gap + s5_team_build_below) >= 14 THEN 'watch'::text
            ELSE 'drop'::text
        END AS treatment,
    s4_leadership_gap = 5 AND s5_team_build_below = 5 AS compound_signal,
    signals_observed_at IS NULL OR signals_observed_at < (CURRENT_DATE - 30) AS signals_stale
   FROM outreach_targets t;

ALTER VIEW v_outreach_scored OWNER TO barry_agent;

COMMIT;

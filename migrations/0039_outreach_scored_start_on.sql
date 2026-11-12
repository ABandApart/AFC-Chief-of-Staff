-- =============================================================================
-- Migration 0039: v_outreach_scored exposes outreach_targets.start_on (N8)
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, N8 build notes.
--
-- A view's column list is frozen when it is created (the 0016/0024/0029 trap), so
-- the `start_on` column 0038 added was missing from v_outreach_scored, and
-- verify_schema.sql's view-drift check failed. This re-creates the view with the
-- same definition plus `start_on`, appended last so CREATE OR REPLACE keeps every
-- existing column in place. Nothing depends on the view.
--
-- Apply: psql aiadaptive_cos -f migrations/0039_outreach_scored_start_on.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE OR REPLACE VIEW v_outreach_scored AS
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
    news_feed_url,
    news_query,
    news_polled_at,
    headcount,
    headcount_asof,
    ownership_type,
    total_raised_usd,
    last_round_at,
    last_round_type,
    lead_investor,
    founded_year,
    hq_location,
    hypothesis_id,
    country,
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
    signals_observed_at IS NULL OR signals_observed_at < (CURRENT_DATE - 30) AS signals_stale,
    start_on
   FROM outreach_targets t;

ALTER VIEW v_outreach_scored OWNER TO barry_agent;

COMMIT;

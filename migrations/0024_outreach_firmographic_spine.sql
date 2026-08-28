-- =============================================================================
-- Migration 0024: the firmographic spine on outreach_targets (Track O Part 3)
-- =============================================================================
-- Part 3 outcome 1 (PRD-outreach-company-profile.md §3.1): every target carries
-- the firmographic spine as TYPED columns, populated where a provider returns
-- them and null where it does not. `sector` already exists (0013); this adds the
-- remaining nine.
--
-- Why typed columns and not a JSONB blob (§3.2 non-goal). Outcome 2 asks that
-- attribute history be readable without a new table - and it is, for free,
-- because `outreach_log_event()` diffs COLUMNS: it walks jsonb_each(NEW) and
-- records {col: {from, to}} per changed key. A blob would log a whole-object
-- before/after on every touch and the history would be unreadable. The column
-- choice is what makes the audit trail legible, so it must not be "simplified"
-- into a blob later without noticing. (Confirmed against the live trigger: new
-- columns are picked up automatically, no trigger change needed here.)
--
-- ownership_type carries a CHECK (§3.5 open decision #4 - resolved YES here). The
-- 0013 precedent is that every invariant is a DB constraint, because NocoDB
-- issues raw UPDATEs that bypass any application-layer validation; the five
-- values are a closed set the packet templates read, so a typo must fail at the
-- database, not render a broken card. founded_year gets a light range CHECK for
-- the same reason - a garbage year from a raw UPDATE should not reach a template.
--
-- What this migration does NOT do. It stores no provider data (there is none yet
-- - the Apollo/Crunchbase adapters and the V2 coverage probe come after this).
-- Funding still lands as an `outreach_evidence` row too (outcome 3); the columns
-- here are the current-state convenience, the evidence row is the dated
-- observation. That wiring is the adapter's job, not the schema's.
--
-- THE VIEW REBUILD IS MANDATORY, NOT INCIDENTAL. `v_outreach_scored` is
-- `SELECT t.*, <computed>` and a view's column list is FROZEN at creation, so
-- CREATE OR REPLACE cannot absorb a new base column - it must be dropped and
-- recreated. verify_schema.sql asserts against this drift (the trap 0016 hit).
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0024_outreach_firmographic_spine.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- 1. The firmographic spine columns --------------------------------------
-- sector already exists (0013). These nine complete the §3.1 spine.
ALTER TABLE outreach_targets
    ADD COLUMN IF NOT EXISTS headcount        INTEGER,
    ADD COLUMN IF NOT EXISTS headcount_asof   DATE,
    ADD COLUMN IF NOT EXISTS ownership_type   TEXT,
    ADD COLUMN IF NOT EXISTS total_raised_usd BIGINT,   -- whole USD, cumulative
    ADD COLUMN IF NOT EXISTS last_round_at    DATE,
    ADD COLUMN IF NOT EXISTS last_round_type  TEXT,     -- provider round label, distinct from `stage`
    ADD COLUMN IF NOT EXISTS lead_investor    TEXT,
    ADD COLUMN IF NOT EXISTS founded_year     SMALLINT,
    ADD COLUMN IF NOT EXISTS hq_location      TEXT;

-- ownership_type is a closed set (§3.1 / §3.5 #4).
ALTER TABLE outreach_targets
    DROP CONSTRAINT IF EXISTS outreach_targets_ownership_ck;

ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_ownership_ck CHECK (
        ownership_type IS NULL
        OR ownership_type IN ('vc_backed', 'pe_backed', 'bootstrapped',
                              'founder_owned', 'public')
    );

-- founded_year: a real firm, not a garbage year from a raw UPDATE. The upper
-- bound is deliberately CURRENT-DATE-free so the CHECK is immutable (a bound of
-- EXTRACT(year FROM now()) would make the constraint non-idempotent across years);
-- 2100 is a comfortable ceiling for "a company that exists today".
ALTER TABLE outreach_targets
    DROP CONSTRAINT IF EXISTS outreach_targets_founded_year_ck;

ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_founded_year_ck CHECK (
        founded_year IS NULL OR (founded_year BETWEEN 1800 AND 2100)
    );

COMMENT ON COLUMN outreach_targets.headcount IS
    'Employee count as most recently reported by a provider. Pair with '
    'headcount_asof - a headcount with no date is a fact with no age.';
COMMENT ON COLUMN outreach_targets.headcount_asof IS
    'The date the headcount was reported/observed. Feeds the ARR band basis (R0.9).';
COMMENT ON COLUMN outreach_targets.ownership_type IS
    'Closed set: vc_backed | pe_backed | bootstrapped | founder_owned | public. '
    'Distinguishes a funding trigger that applies (vc/pe) from one that does not '
    '(bootstrapped/founder_owned). CHECK-constrained because NocoDB issues raw UPDATEs.';
COMMENT ON COLUMN outreach_targets.total_raised_usd IS
    'Cumulative capital raised, whole USD. Current-state convenience; each round '
    'also lands as an outreach_evidence observation (§3.1 outcome 3).';
COMMENT ON COLUMN outreach_targets.last_round_type IS
    'Provider label for the most recent round (e.g. seed, series_a). Distinct from '
    '`stage`, which is the operator-facing scoring stage (S2), not the raw round.';

-- --- 2. Rebuild the scored view (see the header) -----------------------------
-- Mirrors the live definition; the nine spine columns are appended to the base
-- column list, before the computed columns.
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

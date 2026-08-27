-- =============================================================================
-- Migration 0021: news observation plumbing (Track O, Part 0 profiling · Part 1)
-- =============================================================================
-- Part 1 observes company news into a graph and an unclassified queue. Spec:
-- PRD-outreach-company-profile.md Part 1, with corrections C1-C3 / R1.9 recorded
-- there before this migration was written.
--
-- Three changes:
--
-- 1. News config + graph anchor on BOTH profilable tables.
--    A firm is profiled whether it is an accepted discovery or a target, so both
--    need somewhere to store a per-firm feed override and the graph node id. The
--    Organization node is keyed on company_domain and created at discovery, so a
--    firm carries its accumulated news across promotion (R1.6).
--
-- 2. outreach_watch_signals gets a POLYMORPHIC parent (R1.9 / C2).
--    The table was target_id NOT NULL. An accepted discovery has no trigger, and
--    the only way it gets one is Part 2 classifying a signal about it (R0.3) -
--    so a pool firm's signals MUST land here or it can never be promoted, which
--    is the whole reason Part 1 exists. target_id becomes nullable, discovery_id
--    is added, exactly one is required, and dedup holds per-parent. The table is
--    empty, so this is a reshape with no data migration.
--
-- 3. v_outreach_scored rebuild.
--    Adding columns to outreach_targets and it is SELECT t.* with a frozen column
--    list - the 0016 trap. Dropped and recreated; verify_schema asserts no drift.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0021_news_observation.sql
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- 1. News config + graph anchor -------------------------------------------
ALTER TABLE outreach_targets
    ADD COLUMN IF NOT EXISTS news_feed_url  TEXT,          -- company newsroom/blog feed
    ADD COLUMN IF NOT EXISTS news_query     TEXT,          -- Google News query override
    ADD COLUMN IF NOT EXISTS news_polled_at TIMESTAMPTZ;   -- last profile run

-- Discoveries need the same, plus a graph anchor. Targets already carry
-- cognee_node_id (0013); discoveries did not.
ALTER TABLE outreach_discoveries
    ADD COLUMN IF NOT EXISTS news_feed_url  TEXT,
    ADD COLUMN IF NOT EXISTS news_query     TEXT,
    ADD COLUMN IF NOT EXISTS news_polled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cognee_node_id TEXT;          -- Organization node id (R1.6)

-- --- 2. Polymorphic watch-signal parent (R1.9) -------------------------------
ALTER TABLE outreach_watch_signals
    ALTER COLUMN target_id DROP NOT NULL;

ALTER TABLE outreach_watch_signals
    ADD COLUMN IF NOT EXISTS discovery_id BIGINT
        REFERENCES outreach_discoveries(id) ON DELETE CASCADE;

-- Exactly one parent. A signal about a company is about that company once,
-- whether it is currently a pool row or a target.
ALTER TABLE outreach_watch_signals
    DROP CONSTRAINT IF EXISTS outreach_watch_signals_one_parent_ck;
ALTER TABLE outreach_watch_signals
    ADD CONSTRAINT outreach_watch_signals_one_parent_ck CHECK (
        (target_id IS NOT NULL) <> (discovery_id IS NOT NULL)
    );

-- Dedup per parent. The old UNIQUE (target_id, dedup_key) breaks once target_id
-- is nullable - NULLs do not collide, so pool signals could duplicate. Two
-- partial unique indexes, one per parent, restore it on both sides.
ALTER TABLE outreach_watch_signals
    DROP CONSTRAINT IF EXISTS outreach_watch_signals_target_id_dedup_key_key;
DROP INDEX IF EXISTS outreach_watch_signals_target_dedup_idx;
DROP INDEX IF EXISTS outreach_watch_signals_discovery_dedup_idx;
CREATE UNIQUE INDEX outreach_watch_signals_target_dedup_idx
    ON outreach_watch_signals (target_id, dedup_key) WHERE target_id IS NOT NULL;
CREATE UNIQUE INDEX outreach_watch_signals_discovery_dedup_idx
    ON outreach_watch_signals (discovery_id, dedup_key) WHERE discovery_id IS NOT NULL;

COMMENT ON COLUMN outreach_watch_signals.discovery_id IS
    'Parent when the firm is still an unpromoted discovery (R1.9). Exactly one of '
    'target_id / discovery_id is set; promotion re-points the signal from the '
    'discovery to the new target so the history is not orphaned.';

-- --- 3. Rebuild the scored view (the 0016 trap) ------------------------------
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

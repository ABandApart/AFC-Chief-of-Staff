-- =============================================================================
-- Migration 0016: outreach_targets.intake_message_id (Track O, Gate 1)
-- =============================================================================
-- The intake card's delivery marker, mirroring `task_candidates.discord_message_id`
-- (migration 0001). Two jobs, both structural rather than cosmetic:
--
--   * **Post-once.** The poller surfaces targets whose `intake_message_id` is
--     NULL, so a target reaching `treatment='work'` gets exactly one card
--     however many times the poller runs.
--   * **Survive a restart.** Persistent Discord Views are re-bound on startup by
--     message id; without it, every card posted before a bot restart would have
--     dead buttons and the decision would be silently unclickable.
--
-- TEXT, not BIGINT: Discord snowflakes exceed 2^53 and every other id in this
-- schema is stored the same way.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0016_outreach_intake_message.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE outreach_targets
    ADD COLUMN IF NOT EXISTS intake_message_id TEXT;

COMMENT ON COLUMN outreach_targets.intake_message_id IS
    'Discord message id of this target''s Gate 1 intake card. NULL = not yet '
    'surfaced. Set once when the card posts; used to re-attach the persistent '
    'View after a bot restart so the buttons keep working.';

-- The poller's work queue: scored-to-work candidates never surfaced.
CREATE INDEX IF NOT EXISTS outreach_targets_intake_idx
    ON outreach_targets (status) WHERE intake_message_id IS NULL;

-- --- refresh the views over outreach_targets ---------------------------------
--
-- **A view's column list is frozen at creation time.** `v_outreach_scored` is
-- defined as `SELECT t.*, <computed>`, and Postgres expanded that `*` when the
-- view was created in 0013 — so the columns added since (0015's `function` and
-- `contact_first_name`, and `intake_message_id` above) are invisible through it
-- until it is rebuilt. Found the hard way: `list_undelivered` reads the view and
-- failed with "column intake_message_id does not exist" while the column plainly
-- existed on the table.
--
-- `CREATE OR REPLACE VIEW` cannot fix it — new base columns land in the middle
-- of the view's column list (before the computed ones), which replace refuses.
-- So: drop and recreate.
--
-- **Any future migration adding a column to `outreach_targets` or
-- `outreach_evidence` must do this too.** `verify_schema.sql` now asserts the
-- view exposes every base column, so the drift fails a check rather than
-- surfacing as a confusing runtime error weeks later.

DROP VIEW IF EXISTS v_outreach_scored;
CREATE VIEW v_outreach_scored AS
SELECT t.*,
       outreach_s1(t.trigger_date)              AS s1_trigger_recency,
       CURRENT_DATE - t.trigger_date            AS days_since_trigger,
       CASE WHEN t.s2_stage_fit IS NULL OR t.s3_sector_match IS NULL
              OR t.s4_leadership_gap IS NULL OR t.s5_team_build_below IS NULL
            THEN NULL
            ELSE outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below
       END                                      AS score,
       CASE WHEN t.s2_stage_fit IS NULL OR t.s3_sector_match IS NULL
              OR t.s4_leadership_gap IS NULL OR t.s5_team_build_below IS NULL
            THEN NULL
            WHEN outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below >= 20 THEN 'work'
            WHEN outreach_s1(t.trigger_date) + t.s2_stage_fit + t.s3_sector_match
                 + t.s4_leadership_gap + t.s5_team_build_below >= 14 THEN 'watch'
            ELSE 'drop'
       END                                      AS treatment,
       (t.s4_leadership_gap = 5 AND t.s5_team_build_below = 5)
                                                AS compound_signal,
       (t.signals_observed_at IS NULL
        OR t.signals_observed_at < CURRENT_DATE - 30)
                                                AS signals_stale
FROM outreach_targets t;

ALTER VIEW v_outreach_scored OWNER TO barry_agent;

COMMIT;

-- =============================================================================
-- End of migration 0016
-- =============================================================================

-- =============================================================================
-- Migration 0014: outreach stage + trigger vocabulary (Track O)
-- =============================================================================
-- Three corrections, all forced by the first real target list (14 sales-training
-- / L&D firms, imported 2026-08-13) meeting a schema designed around
-- venture-backed startups.
--
-- **(1) `stage` gains `mature`, and stops being mandatory at intake.**
--
-- Migration 0013's `outreach_targets_stage_ck` required a non-NULL stage for
-- every target except `inbound_enquiry`. That exemption was too narrow, and the
-- constraint produced exactly the outcome it was written to prevent: six of the
-- fourteen imported targets are established, non-venture firms with no funding
-- stage, so the import fabricated one (Mature→`series_b_plus`, New→`seed`) to
-- satisfy the constraint. Sales Gravy is now recorded as `seed`. 0013's own
-- comment names the failure — *"a wrong stage scores silently, a missing one
-- does not score at all"* — and the constraint was the thing forcing the wrong
-- value.
--
-- Two changes, together:
--   * `mature` joins the vocabulary (operator, 2026-08-13). Non-startups get an
--     honest slot; the startup ladder stays intact because startup-stage firms
--     are still expected in the mix.
--   * `stage_ck` is dropped outright. `outreach_targets_seq_ck` (0013) already
--     requires `stage IS NOT NULL` before a target can reach `in_sequence`,
--     which is the only place stage is consumed (S2 scoring and the Selector
--     grid). Requiring it at *intake* bought nothing and cost accuracy.
--
-- A genuinely unknown stage is now NULL, which scores as absent rather than
-- scoring wrong.
--
-- **(2) `trigger_kind` is pinned to the eight triggers.**
--
-- `35-` §2 said "the eight triggers, plus 'inbound_enquiry'" in three places and
-- **enumerated them nowhere** — the same forward-reference-to-a-missing-artifact
-- pattern that left `outreach_touches`/`_watch_signals`/`_events` to be
-- reconstructed from prose during increment 1. Unconstrained, the vocabulary was
-- already drifting: the seeded data uses `request_open_past_45_days` while
-- 0013's own example comment used `req_open_45d`. The operator supplied the
-- canonical eight (2026-08-13); they are written into `35-` §2 alongside this.
--
-- `new_executive_hire` is in the set but absent from the seeded data, which is
-- expected — it is a trigger the watch loop detects, not one you import.
--
-- **(3) Column comments** now carry both vocabularies, so the enumeration lives
-- with the column rather than only in prose that can drift from it.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0014_outreach_vocabulary.sql
-- Verify via:
--   psql aiadaptive_cos -f migrations/verify_schema.sql
--
-- Both ADD CONSTRAINTs validate existing rows on apply — a violation here means
-- data predating the vocabulary, which is the point of adding them.
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- --- (1) stage ---------------------------------------------------------------

-- Drop the intake-time requirement. Sequencing still demands a stage
-- (outreach_targets_seq_ck), which is where it is actually used.
ALTER TABLE outreach_targets DROP CONSTRAINT IF EXISTS outreach_targets_stage_ck;

ALTER TABLE outreach_targets DROP CONSTRAINT IF EXISTS outreach_targets_stage_values_ck;
ALTER TABLE outreach_targets ADD CONSTRAINT outreach_targets_stage_values_ck
    CHECK (stage IS NULL OR stage IN ('seed', 'series_a', 'series_b_plus', 'mature'));

COMMENT ON COLUMN outreach_targets.stage IS
    'Funding/maturity stage: seed | series_a | series_b_plus | mature. '
    'NULL = genuinely unknown (scores as absent, never as wrong). '
    'Required before in_sequence — see outreach_targets_seq_ck.';

-- --- (2) trigger_kind --------------------------------------------------------

-- The eight triggers (operator, 2026-08-13) + inbound_enquiry (Roy Kent, D2).
-- Inbound is in the CHECK but never runs the cold arc (36-inbound-leads.md I1).
ALTER TABLE outreach_targets DROP CONSTRAINT IF EXISTS outreach_targets_trigger_kind_ck;
ALTER TABLE outreach_targets ADD CONSTRAINT outreach_targets_trigger_kind_ck
    CHECK (trigger_kind IN (
        'executive_departure',         -- highest-converting trigger in the method (OQ1)
        'request_open_past_45_days',   -- feeds S4's top band and T10's posting-date mechanic
        'new_executive_hire',
        'second_raise',                -- the second-raise mechanic (36- I4)
        'funding_announced',
        'restructuring_or_layoffs',
        'market_or_region_expansion',
        'product_launch',
        'inbound_enquiry'              -- NOT a cold trigger; never materialises the arc
    ));

COMMENT ON COLUMN outreach_targets.trigger_kind IS
    'One of the eight triggers (35- §2, enumerated 2026-08-13), or '
    'inbound_enquiry for a Roy Kent hand-off. Pinned by '
    'outreach_targets_trigger_kind_ck — the arc anchors on this plus trigger_date.';

COMMIT;

-- =============================================================================
-- End of migration 0014
--
-- Follow-up (barry-agent, NOT done here): re-import the target CSV with
-- `Mature` → `mature` now that the vocabulary exists. `stage` is refreshable by
-- the importer's upsert, so a re-import corrects the six fabricated rows in
-- place. Deliberately not patched blind here — the source CSV is authoritative
-- about which rows those are, and this migration cannot tell a real
-- `series_b_plus` from a mapped one.
-- =============================================================================

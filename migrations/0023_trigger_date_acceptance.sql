-- =============================================================================
-- Migration 0023: trigger_date anchors on pipeline acceptance (Track O)
-- =============================================================================
-- SEMANTIC CHANGE, operator decision 2026-08-27. `trigger_date` was "when the
-- market trigger happened"; it becomes "when the operator accepted the firm into
-- the working pipeline." The five-touch arc anchors on trigger_date
-- (packet.touch_windows = trigger_date + N days), so this is what makes the arc
-- run in the operator's real working window rather than on a market-event date.
--
-- Why this does NOT reintroduce the fabricated-date failure R0.3 guarded against:
-- the batch date 2026-06-10 was a stamp on UNOBSERVED data (an import artefact
-- with no real event behind it). An acceptance date is a real, dated decision the
-- operator makes about a specific firm - the opposite of fabricated. Recorded as
-- a deliberate deviation from the source method in
-- PRD-outreach-company-profile.md and the 70- decision log.
--
-- This migration carries ONLY the schema part - a new trigger_kind. The one-time
-- correction of the 14 fake-dated targets (and re-anchoring AIIR's pre-expired
-- arc) is a data operation run once on the shared DB, documented in the handoff,
-- not baked into a migration (it depends on the current date).
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0023_trigger_date_acceptance.sql
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- A firm accepted into the pipeline without a specific observed market trigger
-- carries `operator_selected`: the trigger IS the operator's decision to work it.
-- A real market trigger (the eight kinds), when Part 2 later classifies one, is
-- still preferred and overwrites this.
ALTER TABLE outreach_targets
    DROP CONSTRAINT IF EXISTS outreach_targets_trigger_kind_ck;

ALTER TABLE outreach_targets
    ADD CONSTRAINT outreach_targets_trigger_kind_ck CHECK (
        trigger_kind = ANY (ARRAY[
            'executive_departure', 'request_open_past_45_days', 'new_executive_hire',
            'second_raise', 'funding_announced', 'restructuring_or_layoffs',
            'market_or_region_expansion', 'product_launch', 'inbound_enquiry',
            'operator_selected'
        ])
    );

COMMENT ON COLUMN outreach_targets.trigger_date IS
    'When the operator accepted the firm into the working pipeline (0023). The '
    'five-touch arc anchors here. NOT the market-event date - that was the '
    'source-method reading, changed because the fake batch date pre-expired every '
    'arc (touch windows are trigger_date + N days).';

COMMIT;

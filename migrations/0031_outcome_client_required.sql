-- =============================================================================
-- Migration 0031: contract outcomes must name a client
-- =============================================================================
-- Spec: architecture/PRD-outcome-attribution.md, increment 2 (decision D3).
-- The database backstop for the /outcome handler's refusal: KR3 pairs
-- engagement_signed with maintenance_converted by client_id, so a contract
-- outcome without one is unusable. Mirrors CONTRACT_TYPES in
-- agents/discord_bot/cogs/outcomes.py — change both together.
--
-- DEPLOY ORDER: apply in the SAME deploy as the increment-2 /outcome cog.
-- Applied before it, `/outcome engagement signed` fails (the old form sends no
-- client); applied long after it, there is a window with no backstop.
--
-- Added fully validated, not NOT VALID: on 2026-10-05 `outcomes` holds one row
-- (#8, partnership_explored), which does not violate it.
--
-- Apply: psql aiadaptive_cos -f migrations/0031_outcome_client_required.sql
-- Idempotent.
-- =============================================================================

BEGIN;

ALTER TABLE outcomes DROP CONSTRAINT IF EXISTS outcomes_engagement_has_client_ck;
ALTER TABLE outcomes ADD CONSTRAINT outcomes_engagement_has_client_ck CHECK (
    outcome_type NOT IN ('engagement_signed', 'engagement_renewed', 'maintenance_converted')
    OR client_id IS NOT NULL);

COMMIT;

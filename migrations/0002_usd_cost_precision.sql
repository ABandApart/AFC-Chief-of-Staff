-- =============================================================================
-- Migration 0002: usd_cost precision
-- 2026-07 refactor (see architecture/PROPOSAL-2026-07-05-refactor-review.md R1)
-- =============================================================================
-- NUMERIC(10,4) truncated cheap calls to $0.0000 (a recall query embedding
-- costs ~$0.0000015) and carried ~5% rounding error on typical Haiku calls.
-- The ledger feeds G2 ceilings and Higgins' KR reporting, so widen to 8
-- fractional digits. Existing rows are preserved as-is (their lost precision
-- is unrecoverable); the cost helper writes 8dp from the same commit.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0002_usd_cost_precision.sql
--
-- Idempotent: re-running is a no-op type change.
-- =============================================================================

BEGIN;

ALTER TABLE agent_runs ALTER COLUMN usd_cost TYPE NUMERIC(14,8);

COMMIT;

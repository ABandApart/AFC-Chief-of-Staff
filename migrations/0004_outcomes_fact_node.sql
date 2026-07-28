-- =============================================================================
-- Migration 0004: outcomes.attributed_fact_node (cognee pivot, W4)
-- =============================================================================
-- The facts a /outcome links to are moving from `facts` rows (int PK) to cognee
-- graph nodes (in the aiadaptive_cognee DB). Postgres can't FK across databases,
-- so the link becomes a cognee node-id stored as TEXT, joined in app code — the
-- entity↔operational boundary from architecture/25-target-state.md.
--
-- `attributed_fact_id` (the old int FK) is kept for now: the two facts that
-- predate the pivot still live in `facts`, and the /outcome rewire to the graph
-- lands in W5. Once W5 ships, `attributed_fact_id` is retired in a later
-- migration.
--
-- Apply: psql aiadaptive_cos -f migrations/0004_outcomes_fact_node.sql
-- Idempotent.
-- =============================================================================

BEGIN;

ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS attributed_fact_node TEXT;

COMMENT ON COLUMN outcomes.attributed_fact_node IS
    'cognee graph node id of the linked fact (W4 pivot); replaces attributed_fact_id '
    'for graph-resident facts. Joined in app code — no cross-DB FK.';

COMMIT;

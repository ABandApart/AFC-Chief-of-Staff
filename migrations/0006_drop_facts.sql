-- =============================================================================
-- Migration 0006: retire the legacy `facts` table + fact-link columns (W5)
-- =============================================================================
-- The cognee pivot is complete: knowledge lives in the graph (aiadaptive_cognee),
-- capture writes there (agents/_lib/ingest), recall reads there
-- (agents/_lib/graph_recall). The old `facts` table is no longer written or read
-- by any code path (the briefing's "notes captured" count moved to
-- `capture_messages`; /outcome's fact autocomplete was removed).
--
-- Operator decision (2026-07-28): drop the /outcome→fact link entirely rather
-- than re-point it at graph node ids. So BOTH fact-link columns on `outcomes`
-- go: `attributed_fact_id` (the old int FK, deprecated in 0004) and
-- `attributed_fact_node` (the graph-node TEXT column added in 0004 for a rewire
-- that is no longer happening). Outcomes stand on their own description.
--
-- The two pre-pivot rows in `facts` (#3 newsletter theme, #4 async-updates
-- preference) are dropped with the table — operator chose not to migrate them
-- into the graph. Neither was referenced by the one real outcome (#5), so there
-- is no dangling link.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0006_drop_facts.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

-- Drop the fact-link columns first (attributed_fact_id carries the FK into
-- `facts`, so it must go before the table can be dropped).
ALTER TABLE outcomes DROP COLUMN IF EXISTS attributed_fact_id;
ALTER TABLE outcomes DROP COLUMN IF EXISTS attributed_fact_node;

-- The facts table and its dependent objects (embedding HNSW index, content_tsv
-- generated column) go with it.
DROP TABLE IF EXISTS facts;

COMMIT;

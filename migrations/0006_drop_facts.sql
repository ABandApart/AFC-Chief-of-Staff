-- =============================================================================
-- Migration 0006: retire the legacy `facts` table + all fact-link columns (W5)
-- =============================================================================
-- The cognee pivot is complete: knowledge lives in the graph (aiadaptive_cognee),
-- capture writes there (agents/_lib/ingest), recall reads there
-- (agents/_lib/graph_recall). The old `facts` table is no longer written or read
-- by any code path (the briefing's "notes captured" count moved to
-- `capture_messages`; /outcome's fact autocomplete was removed).
--
-- Operator decision (2026-07-28): drop the outcome→fact link entirely rather
-- than re-point it at graph node ids. More broadly, every column that linked an
-- operational row to a `facts` row is dropped here — the three that exist:
--   * outcomes.attributed_fact_id   — old int FK (deprecated in 0004)
--   * outcomes.attributed_fact_node — the graph-node TEXT column added in 0004
--                                     for a rewire that is no longer happening
--   * follow_ups.source_fact_id     — int FK; also blocks dropping `facts`
--   * decisions.related_facts       — BIGINT[] of fact ids (now-dead references)
-- `follow_ups` and `decisions` are empty (their producing phases aren't built),
-- so these are structural drops with no data loss. When those phases are built
-- they add cognee node-id (TEXT) links as needed — the entity↔operational
-- boundary — rather than inheriting these speculative int columns.
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

-- Drop every fact-link column first (the int FKs into `facts` must go before the
-- table can be dropped; the array column is dead data).
ALTER TABLE outcomes    DROP COLUMN IF EXISTS attributed_fact_id;
ALTER TABLE outcomes    DROP COLUMN IF EXISTS attributed_fact_node;
ALTER TABLE follow_ups  DROP COLUMN IF EXISTS source_fact_id;
ALTER TABLE decisions   DROP COLUMN IF EXISTS related_facts;

-- The facts table and its dependent objects (embedding HNSW index, content_tsv
-- generated column) go with it.
DROP TABLE IF EXISTS facts;

COMMIT;

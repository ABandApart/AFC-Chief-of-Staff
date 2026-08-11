-- =============================================================================
-- Migration 0012: repurpose content_items as Tartt's operational tracker (Phase 4)
-- =============================================================================
-- The pre-pivot `content_items` (0001) was a self-vectorized content store
-- (embedding, title_tsv, cluster_id). Post-pivot the **knowledge** lives in the
-- cognee graph as a typed `ContentItem` (local bge embed); this SQL table becomes
-- the lean **operational tracker** — one row per discovered article, linking the
-- pipeline state to its graph node. Operator decision (2026-08-11): repurpose,
-- not retire (keeps `content_pipeline.content_item_id`'s FK intact).
--
--   - ADD `content_node` — the ContentItem graph node-id (uuid5(url)); the
--     entity↔operational link.
--   - DROP the now graph-owned vectorized columns (embedding, title_tsv,
--     cluster_id). `summary`, `interest_score`, `source_id`, engagement, and
--     `collected_at` stay — the tracker's own fields.
--   - UNIQUE(url) — the dedup key so a re-seen URL is skipped before any fetch /
--     summarize / embed (belt-and-braces with the graph's uuid5(url) upsert).
--
-- content_items is empty pre-Phase-4, so the drops are lossless.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0012_content_items_tracker.sql
-- Idempotent (IF [NOT] EXISTS throughout).
-- =============================================================================

BEGIN;

ALTER TABLE content_items
    ADD COLUMN IF NOT EXISTS content_node text,
    DROP COLUMN IF EXISTS embedding,
    DROP COLUMN IF EXISTS title_tsv,
    DROP COLUMN IF EXISTS cluster_id;

-- Dedup: one tracker row per source URL.
CREATE UNIQUE INDEX IF NOT EXISTS content_items_url_key ON content_items (url);

COMMIT;

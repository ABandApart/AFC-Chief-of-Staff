-- =============================================================================
-- Migration 0036: per-field sync state for the To-Do database
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, N-S17 (Notion is the source of
-- truth for To-Do content, operator 2026-11-10).
--
-- `notion_sync.synced_fields` holds, for each To-Do row, the values Postgres and
-- Notion last agreed on: {"title": …, "due": "YYYY-MM-DD" | null,
-- "initiative": [page id] | [], "done": bool}. The To-Do sync compares both sides
-- with it field by field. Notion wins when it changed; a Discord change reaches
-- Notion only when Notion did not change that field.
--
-- NULL (every row before this migration) means no agreed value yet: any field
-- that differs is taken from Notion. `synced_done` (0034) stays as the Done
-- baseline for those rows until their first sync under 0036.
--
-- Apply: psql aiadaptive_cos -f migrations/0036_todo_synced_fields.sql
-- Idempotent.
-- =============================================================================

BEGIN;

ALTER TABLE notion_sync ADD COLUMN IF NOT EXISTS synced_fields JSONB;

COMMENT ON COLUMN notion_sync.synced_fields IS
    'To-Do rows only: the title, due, initiative, and done values Postgres and Notion last agreed on (N-S17).';

COMMIT;

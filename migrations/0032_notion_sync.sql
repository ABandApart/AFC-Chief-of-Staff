-- =============================================================================
-- Migration 0032: notion_sync — CoS row ↔ Notion page map
-- =============================================================================
-- Spec: architecture/PRD-notion-integration.md, increment N1.
-- One row per CoS entity mirrored to (or pulled from) a Notion page. Later
-- increments write it: N2 (OKR pages), N3 (To-Do rows), N4 (dashboard),
-- N5 (Content Review). `content_hash` lets a push skip a page whose projection
-- has not changed (notion_client.content_hash); the two timestamps make
-- staleness visible per page.
--
-- entity_id is TEXT, not BIGINT: OKR entities are keyed by their Notion page id
-- (Notion owns them), CoS entities by their integer id rendered as text.
--
-- Apply: psql aiadaptive_cos -f migrations/0032_notion_sync.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS notion_sync (
    id              BIGSERIAL PRIMARY KEY,
    entity_table    TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    notion_page_id  TEXT NOT NULL UNIQUE,
    content_hash    TEXT,
    last_pushed_at  TIMESTAMPTZ,
    last_pulled_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_table, entity_id)
);

COMMENT ON TABLE notion_sync IS
    'Map of CoS entities to Notion pages (PRD-notion-integration.md). Postgres '
    'and Notion each own distinct fields; this table only records the pairing.';

ALTER TABLE notion_sync           OWNER TO barry_agent;
ALTER SEQUENCE notion_sync_id_seq OWNER TO barry_agent;

COMMIT;

-- =============================================================================
-- Migration 0005: playbook_publications — git→cognee publish tracking (W5)
-- =============================================================================
-- `cli/publish_playbooks.py` publishes every playbook with
-- `publish_to_memory: true` into cognee's trusted `playbooks` dataset (B1). Like
-- capture, cognee does not dedup on re-ingest, so re-running the publish would
-- re-cognify unchanged playbooks — duplicate graph nodes and wasted LLM spend.
--
-- This table is the publish-side dedup key (mirrors `capture_messages`): one row
-- per published playbook, keyed by name, holding the content hash last pushed to
-- the graph. The CLI skips a playbook whose hash is unchanged and upserts the new
-- hash after a successful cognify. Operational state, so it lives in
-- `aiadaptive_cos` — the graph nodes themselves live in `aiadaptive_cognee`
-- (the entity↔operational boundary, architecture/25-target-state.md).
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0005_playbook_publications.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS playbook_publications (
    name            TEXT PRIMARY KEY,         -- playbook name (== filename stem)
    content_hash    TEXT NOT NULL,            -- sha256 hex of the published content
    published_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The runtime bot/CLI connects as barry_agent; migrations run as the socket
-- superuser, so hand the table over explicitly.
ALTER TABLE playbook_publications OWNER TO barry_agent;

COMMIT;

-- =============================================================================
-- Migration 0003: capture_messages — message-level capture dedup
-- Refactor-phase handback finding 1 (see PHASE-REFACTOR-2026-07.md, 3a)
-- =============================================================================
-- Per-fact cosine dedup (≥0.95) can't stop an identical re-post from writing
-- rows: extraction is non-deterministic, so the same message can mint a new
-- vague fact that clears the bar (observed live 2026-07-06 — fact 7). Dedup
-- the *message* before extraction: the capture cog hashes the normalized raw
-- text and skips the whole pipeline on a hash it has seen.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0003_capture_messages.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS capture_messages (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    TEXT NOT NULL UNIQUE,     -- sha256 hex of normalized message text
    message_id      TEXT NOT NULL,            -- first Discord message that carried it
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The runtime bot connects as barry_agent; migrations run as the socket
-- superuser, so hand the table over explicitly.
ALTER TABLE capture_messages OWNER TO barry_agent;

COMMIT;

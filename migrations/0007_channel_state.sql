-- =============================================================================
-- Migration 0007: channel_state — per-channel poll watermark (Track C)
-- =============================================================================
-- Track C ingestion channels (Granola meeting notes first, Google Drive next)
-- poll an external API on a schedule. To fetch incrementally rather than
-- re-listing everything each cycle, each channel stores a small **watermark**
-- (an opaque cursor or last-seen timestamp) here and advances it after a
-- successful poll. One row per channel.
--
-- This is a coarse fetch-efficiency guard, not the correctness guard: the
-- content-hash dedup in `capture_messages` (migration 0003) remains the
-- authoritative "already ingested this text" check inside `ingest_note`. A
-- lost/blank watermark just means a wider re-list; nothing double-ingests.
--
-- Operational state, so it lives in `aiadaptive_cos` (the graph itself is in
-- `aiadaptive_cognee`).
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0007_channel_state.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS channel_state (
    channel         TEXT PRIMARY KEY,         -- 'granola', 'drive', …
    cursor          TEXT,                     -- opaque watermark (cursor / ISO timestamp)
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The runtime poller connects as barry_agent; migrations run as the socket
-- superuser, so hand the table over explicitly.
ALTER TABLE channel_state OWNER TO barry_agent;

COMMIT;

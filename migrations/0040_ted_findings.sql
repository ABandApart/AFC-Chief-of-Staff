-- =============================================================================
-- Migration 0040: ted_findings
-- =============================================================================
-- Spec: architecture/PRD-ted-ops-monitor.md (Ted, increment 1), D5.
-- One row per finding key. Ted's hourly cycle upserts the findings it sees and
-- raises a finding (inbox entry, and for URGENT/HIGH a Briefing-page alert) when
-- it is new, when it recurs after being clear for two cycles, or when it is
-- unacknowledged and was last raised more than 24 h ago (O7).
--
-- `source` is 'detector' (pure SQL) or 'judgment' (the weekly headless Claude
-- Code session, D8). `acked_at` / `ack_note` come from barry-admin's
-- ACK-admin.md (D4: one writer per file).
--
-- Apply: psql aiadaptive_cos -f migrations/0040_ted_findings.sql
-- Idempotent.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS ted_findings (
    key          TEXT PRIMARY KEY,
    severity     TEXT NOT NULL CHECK (severity IN ('URGENT', 'HIGH', 'MEDIUM', 'INFO')),
    source       TEXT NOT NULL DEFAULT 'detector' CHECK (source IN ('detector', 'judgment')),
    text         TEXT NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_raised  TIMESTAMPTZ,
    raise_count  INTEGER NOT NULL DEFAULT 0,
    acked_at     TIMESTAMPTZ,
    ack_note     TEXT
);

CREATE INDEX IF NOT EXISTS ted_findings_open_idx ON ted_findings (last_seen) WHERE acked_at IS NULL;

ALTER TABLE ted_findings OWNER TO barry_agent;

COMMENT ON TABLE ted_findings IS
    'Ted''s findings: dedupe, re-raise, and acks (PRD-ted-ops-monitor.md, D5).';

COMMIT;

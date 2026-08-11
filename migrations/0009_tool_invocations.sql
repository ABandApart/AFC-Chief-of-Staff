-- =============================================================================
-- Migration 0009: tool_invocations — MCP tool-layer audit log (Track I, Task 2)
-- =============================================================================
-- Every call through the gated core (`agents/_lib/brain_tools.py`), on either
-- transport (local stdio MCP or the Gateway REST surface), writes one row here:
-- which tool, which caller, a hash of the args (never raw PII), the outcome, and
-- latency. This is the brain-side audit trail — the shell's own *reasoning*
-- tokens are out of scope (ADR-0001 accepted limitation), but every call that
-- actually touches the brain is recorded.
--
-- `args_hash` is a sha256 of the canonicalized args so the log is queryable
-- ("how many recall calls today", "which caller hit an error") without storing
-- the query text or payloads. `agent_run_id` links to `agent_runs` when the tool
-- made an LLM call (recall / ingest_note); NULL for pure reads.
--
-- Operational state → `aiadaptive_cos`.
--
-- Apply via:
--   export DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)
--   psql "$DB_URL" -f migrations/0009_tool_invocations.sql
--
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS tool_invocations (
    id           bigserial PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    transport    text NOT NULL CHECK (transport IN ('mcp_stdio', 'gateway_rest')),
    caller       text NOT NULL,               -- 'local:barry-agent' | HMAC caller id ('tools')
    tool         text NOT NULL,
    args_hash    text NOT NULL,               -- sha256 of the canonicalized args (no raw PII)
    outcome      text NOT NULL CHECK (outcome IN ('ok', 'error')),
    error_code   text,
    latency_ms   integer NOT NULL,
    agent_run_id bigint REFERENCES agent_runs (id)  -- set when the tool made an LLM call
);

-- Time-ordered scans (recent activity, anomaly sweeps) and per-tool rollups.
CREATE INDEX IF NOT EXISTS tool_invocations_ts_idx ON tool_invocations (ts DESC);
CREATE INDEX IF NOT EXISTS tool_invocations_tool_ts_idx ON tool_invocations (tool, ts DESC);

COMMIT;

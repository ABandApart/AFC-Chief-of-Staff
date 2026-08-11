-- =============================================================================
-- Migration 0011: fix tool_invocations ownership (Track I audit writes)
-- =============================================================================
-- Migration 0009 created `tool_invocations` while applied by the barry-admin
-- superuser, so it was owned by barry-admin. But the runtime (bot + gateway)
-- connects as **barry_agent**, which owns every other operational table
-- (agent_runs, approval_queue, sources, …). barry_agent had NO grant on the
-- barry-admin-owned audit table, so the tool layer's **best-effort** audit
-- INSERT (which never raises, by design) silently failed — the table stayed
-- empty despite live signed tool calls (found in Track I I4/I5 runtime verify).
--
-- Reassign it (and its identity sequence) to barry_agent so it matches the rest
-- of the schema: audit writes land, and barry_agent can read its own audit (I5).
-- No structural change; the fix is ownership only.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0011_tool_invocations_owner.sql
-- Idempotent: ALTER … OWNER TO is a no-op if already owned by barry_agent.
-- =============================================================================

BEGIN;

ALTER TABLE tool_invocations OWNER TO barry_agent;
ALTER SEQUENCE tool_invocations_id_seq OWNER TO barry_agent;

COMMIT;

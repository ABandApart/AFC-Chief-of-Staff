-- =============================================================================
-- Migration 0008: brain_reader role + v_* read-only views (Track I, Task 1)
-- =============================================================================
-- The MCP tool layer (`PRD-mcp-tool-layer.md`) exposes a small set of bounded
-- brain reads to interactive shells. Defense in depth: those reads run over a
-- dedicated **read-only** role that can see ONLY a curated set of views — never
-- base tables, never a write, never DDL. A bug (or a hostile arg) in a read tool
-- therefore cannot mutate anything or reach an unexposed column.
--
-- How the containment works:
--   * `brain_reader` gets NO base-table grants at all.
--   * The `v_*` views are owned by the migration runner (a superuser) and are
--     ordinary (security-DEFINER) views, so a SELECT through them reads the base
--     tables under the OWNER's privileges — `brain_reader` needs only SELECT on
--     the view. It cannot select the base tables directly, and cannot write
--     (no grant + the views are not updatable).
--   * Curated columns only: no credential/HMAC material, no raw JSONB profile,
--     no full `agent_runs` rows — the spend view exposes just the rollup inputs.
--
-- `brain_reader` is created NOLOGIN here (a pure grant bundle, no password in
-- git). Enabling LOGIN + a password and provisioning `brain-reader-db-url` in
-- barry-agent's keychain is a runtime step (Track I handoff) — the MCP server's
-- read connection authenticates as this role.
--
-- Operational state, so `aiadaptive_cos` (the graph is in `aiadaptive_cognee`,
-- never reached by SQL from this layer — only via `recall`).
--
-- Apply via:
--   export DB_URL=$(security find-generic-password -a "$USER" -s db-url -w)
--   psql "$DB_URL" -f migrations/0008_brain_reader_and_read_views.sql
--
-- Idempotent: safe to re-run (guarded role create; CREATE OR REPLACE views;
-- grants are idempotent).
-- =============================================================================

BEGIN;

-- --- the read-only role (guarded create so re-runs don't error) --------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'brain_reader') THEN
        CREATE ROLE brain_reader NOLOGIN;
    END IF;
END
$$;

-- --- curated read-only views -------------------------------------------------

-- Open follow-ups (not yet completed). Excludes the drafted message body and
-- internal FKs; "open" is structural (completed_at IS NULL), independent of the
-- free-text status vocabulary.
CREATE OR REPLACE VIEW v_open_followups AS
    SELECT id, owner, action, escalation_level, deadline, created_at
    FROM follow_ups
    WHERE completed_at IS NULL
    ORDER BY escalation_level DESC NULLS LAST, deadline ASC NULLS LAST, id;

-- Task candidates awaiting a Task-Tinder decision (decided_at IS NULL).
-- Excludes evidence_text (untrusted ingested body) and the discord message id.
CREATE OR REPLACE VIEW v_pending_task_candidates AS
    SELECT id, proposed_action, source_type, source_ref, confidence, created_at
    FROM task_candidates
    WHERE decided_at IS NULL
    ORDER BY confidence DESC NULLS LAST, created_at DESC, id;

-- Curated prospect projection (used by both get_prospect and list_new_prospects).
-- Excludes raw_profile (arbitrary form JSONB), wordpress_profile_id, person_id.
CREATE OR REPLACE VIEW v_prospect AS
    SELECT id, name, email, company, role, icp_segment, icp_fit_score,
           fit_reasoning, status, received_at, qualified_at, last_status_change_at
    FROM prospects;

-- Newest prospects first; the tool applies the since_hours window + LIMIT.
CREATE OR REPLACE VIEW v_new_prospects AS
    SELECT id, name, email, company, role, icp_segment, icp_fit_score,
           status, received_at
    FROM prospects
    ORDER BY received_at DESC NULLS LAST, id DESC;

-- Spend projection for ad-hoc telemetry Q&A. Only the rollup inputs are exposed
-- (function_label, usd_cost, started_at) — NOT tokens, errors, correlation ids,
-- or provider/model. The tool applies the {today,7d,30d} window + GROUP BY.
CREATE OR REPLACE VIEW v_spend_summary AS
    SELECT function_label, usd_cost, started_at
    FROM agent_runs;

-- --- grants: usage on schema + SELECT on the views ONLY ----------------------
-- No base-table grants: containment relies on the security-definer views above.
GRANT USAGE ON SCHEMA public TO brain_reader;
GRANT SELECT ON
    v_open_followups,
    v_pending_task_candidates,
    v_prospect,
    v_new_prospects,
    v_spend_summary
TO brain_reader;

COMMIT;

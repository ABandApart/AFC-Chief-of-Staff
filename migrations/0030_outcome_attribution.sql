-- =============================================================================
-- Migration 0030: outcome attribution — client key + outreach-target link
-- =============================================================================
-- Spec: architecture/PRD-outcome-attribution.md, increment 1.
-- Closes the schema half of 15-okrs-q4-2026.md gaps:
--   M5  outcomes had no client key, so KR3 (project → maintenance conversion)
--       could not pair a client's engagement_signed with its later
--       maintenance_converted except by free-text description.
--   M1  outcomes.attributed_prospect_id reaches inbound `prospects` only, so a
--       win from cold outreach could not be traced to its outreach_targets row.
--
-- Built against the PROPOSED answer to open decision D1 (a new `clients` table
-- rather than reusing people.id or outreach_targets.id). Do not apply until the
-- operator confirms D1; if it changes, rewrite this file before it runs.
--
-- Deliberately absent (spec S2): the CHECK that engagement-type outcomes carry
-- a client_id. The /outcome form does not send one yet, so the CHECK ships with
-- increment 2 — adding it here would make `/outcome engagement signed` fail.
--
-- Apply: psql aiadaptive_cos -f migrations/0030_outcome_attribution.sql
-- Idempotent.
-- =============================================================================

BEGIN;

-- 1. clients — company identity only, no CRM fields (spec S2) -----------------

CREATE TABLE IF NOT EXISTS clients (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    -- Same shape as outreach.normalize_domain() output, so a client created from
    -- an outreach target matches on domain. Nullable: a client with no web
    -- domain still needs a key. UNIQUE ignores NULLs, so those never collide.
    domain      TEXT UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT clients_domain_normalized_ck CHECK (
        domain IS NULL OR (
            domain = lower(domain)
            AND domain <> ''
            AND domain !~ '^(https?://|www\.)'
            AND domain !~ '[/?]'
        ))
);

COMMENT ON TABLE clients IS
    'One row per client company. Identity only — the key KR3 pairs '
    'engagement_signed with maintenance_converted on (PRD-outcome-attribution.md).';

-- 2. outcomes — the two links --------------------------------------------------

ALTER TABLE outcomes
    ADD COLUMN IF NOT EXISTS client_id BIGINT REFERENCES clients(id);

-- NO ACTION on delete, matching attributed_prospect_id (spec S-2): a target
-- with a recorded outcome cannot be deleted out from under its attribution.
ALTER TABLE outcomes
    ADD COLUMN IF NOT EXISTS attributed_target_id BIGINT REFERENCES outreach_targets(id);

COMMENT ON COLUMN outcomes.client_id IS
    'Client company this outcome belongs to. Required for engagement types once '
    '/outcome sends it (increment 2).';
COMMENT ON COLUMN outcomes.attributed_target_id IS
    'Cold-outreach target this outcome came from (gap M1). Inbound wins use '
    'attributed_prospect_id instead.';

-- KR3 reads outcomes by client and type in time order.
CREATE INDEX IF NOT EXISTS outcomes_client_type_time_idx
    ON outcomes (client_id, outcome_type, recorded_at);
CREATE INDEX IF NOT EXISTS outcomes_attributed_target_idx
    ON outcomes (attributed_target_id);

-- 3. Ownership — the runtime connects as barry_agent (the 0011 bug) ----------

ALTER TABLE clients            OWNER TO barry_agent;
ALTER SEQUENCE clients_id_seq  OWNER TO barry_agent;

COMMIT;

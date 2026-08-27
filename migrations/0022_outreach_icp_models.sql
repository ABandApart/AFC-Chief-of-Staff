-- =============================================================================
-- Migration 0022: versioned ICP models (Track O, Part 4 · R4.1)
-- =============================================================================
-- The selection feedback loop learns per-factor accept rates from the operator's
-- own Gate 0 decisions and proposes an adjusted ICP model. A model is a VERSION,
-- never an in-place edit, so every historical score stays attributable to the
-- arithmetic that produced it (Part 4 outcome 2), and a proposal takes effect
-- only when the operator activates it (outcome 4).
--
-- `factors` is the model as inspectable JSON — v1 is the workbook transcription,
-- v2+ carries the learned per-factor adjustments. Kept as data, not code, so a
-- proposal can be written, diffed and activated without a deploy.
--
-- Spec: PRD-outreach-company-profile.md Part 4.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0022_outreach_icp_models.sql
-- Idempotent: safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS outreach_icp_models (
    version       TEXT PRIMARY KEY,          -- 'v1', 'v2-2026-08-27', …
    active        BOOLEAN NOT NULL DEFAULT false,
    factors       JSONB NOT NULL,            -- the model, inspectable
    notes         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at  TIMESTAMPTZ,
    activated_by  TEXT,                       -- session_user at activation
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Activation is a deliberate act (outcome 4); it stamps who and when.
    CONSTRAINT outreach_icp_models_activation_ck CHECK (
        active = false OR (activated_at IS NOT NULL AND activated_by IS NOT NULL)
    )
);

-- At most one active model. A partial unique index is the standard way to say
-- "exactly one row may have active = true".
DROP INDEX IF EXISTS outreach_icp_models_one_active_idx;
CREATE UNIQUE INDEX outreach_icp_models_one_active_idx
    ON outreach_icp_models (active) WHERE active;

COMMENT ON TABLE outreach_icp_models IS
    'Versioned ICP models (R4.1). A model is data, not code: v1 is the workbook '
    'transcription, v2+ carries learned per-factor adjustments. Never edited in '
    'place - a change is a new version, activated deliberately, so every score '
    'stays attributable (Part 4 outcome 2).';

DROP TRIGGER IF EXISTS outreach_icp_models_audit ON outreach_icp_models;
DROP TRIGGER IF EXISTS outreach_icp_models_touch ON outreach_icp_models;

CREATE TRIGGER outreach_icp_models_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_icp_models
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();

CREATE TRIGGER outreach_icp_models_touch
    BEFORE UPDATE ON outreach_icp_models
    FOR EACH ROW EXECUTE FUNCTION outreach_touch_updated_at();

ALTER TABLE outreach_icp_models OWNER TO barry_agent;

-- --- entity_id becomes nullable ------------------------------------------------
-- Not every audited entity has a bigint surrogate id. A table keyed on TEXT
-- (outreach_icp_models on `version`, outreach_segment_scores on `segment`) is
-- fully identified by entity_table + the row snapshot in `changed`, so entity_id
-- is a convenience for the common id-keyed case, not a requirement. It stays set
-- for every id-keyed table exactly as before.
ALTER TABLE outreach_events ALTER COLUMN entity_id DROP NOT NULL;

-- --- Repair the audit function for non-`id` primary keys -----------------------
-- outreach_log_event() hardcoded NEW.id, so it fails on any table keyed on
-- something else. outreach_icp_models keys on `version` (TEXT), and so does
-- outreach_segment_scores (0019) on `segment` - a latent bug that would fire the
-- moment the operator rated a segment. The id is now extracted from the row's
-- JSON, which is NULL when there is no id column: the entity_table plus the full
-- row in `changed` still identify the row (version/segment lives in that JSON).
CREATE OR REPLACE FUNCTION public.outreach_log_event()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    v_entity_id BIGINT;
    v_changed   JSONB;
    v_old       JSONB;
    v_new       JSONB;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_entity_id := (to_jsonb(OLD) ->> 'id')::BIGINT;   -- NULL if no id column
        v_changed   := jsonb_build_object('deleted', to_jsonb(OLD));
    ELSIF TG_OP = 'INSERT' THEN
        v_entity_id := (to_jsonb(NEW) ->> 'id')::BIGINT;
        v_changed   := jsonb_build_object('inserted', to_jsonb(NEW));
    ELSE
        v_entity_id := (to_jsonb(NEW) ->> 'id')::BIGINT;
        v_old := to_jsonb(OLD);
        v_new := to_jsonb(NEW);
        SELECT jsonb_object_agg(key, jsonb_build_object('from', v_old -> key, 'to', v_new -> key))
          INTO v_changed
          FROM jsonb_each(v_new)
         WHERE key <> 'updated_at'
           AND v_new -> key IS DISTINCT FROM v_old -> key;
        IF v_changed IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;

    INSERT INTO outreach_events (entity_table, entity_id, op, actor, changed)
    VALUES (TG_TABLE_NAME, v_entity_id, TG_OP, session_user, v_changed);
    RETURN NULL;
END;
$function$;

-- --- Seed the v1 baseline as the active model --------------------------------
-- v1 is the workbook transcription already living in agents/outreach/icp.py; the
-- row here is the ACTIVE pointer with empty learned adjustments, so
-- score_with_model(candidate, active) == v1 until the operator activates a
-- learned proposal. Empty `adjustments` is the whole "v2 falls back to v1"
-- contract expressed as data.
INSERT INTO outreach_icp_models (version, active, factors, notes, activated_at, activated_by)
VALUES ('v1', true,
        '{"base_accept_rate": 0.5, "adjustments": {}}'::jsonb,
        'workbook transcription baseline (agents/outreach/icp.py)',
        now(), session_user)
ON CONFLICT (version) DO NOTHING;

COMMIT;

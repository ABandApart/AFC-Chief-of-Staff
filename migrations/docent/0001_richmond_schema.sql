-- =============================================================================
-- Docent 0001: the `richmond` schema that the nightly export fills
-- =============================================================================
-- Spec: architecture/PRD-docent-export.md.
--
-- Runs in docent's Supabase project, NOT in aiadaptive_cos. Apply it once in
-- the Supabase SQL editor (it runs as `postgres`). Idempotent.
--
-- Roles:
--   richmond_export  the Mac mini job. Select, insert, and delete in `richmond`
--                    only. Created without a password; set one afterwards with
--                    ALTER ROLE richmond_export PASSWORD '…' (never in git).
--   authenticated    docent's signed-in users. Read only. To make the data
--                    public, add `anon` to the read grant and policies (OD-1).
--   anon             nothing.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS richmond;

CREATE TABLE IF NOT EXISTS richmond.themes (
    topic_label        text PRIMARY KEY,
    weight             real,
    last_reinforced_at timestamptz,
    exported_at        timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS richmond.market_segments (
    label              text PRIMARY KEY,
    market_size        smallint,
    market_growth      smallint,
    firm_profitability smallint,
    ability_to_pay     smallint,
    urgency_pain       smallint,
    offering_fit       smallint,
    rated_at           timestamptz,
    exported_at        timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS richmond.export_runs (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ran_at               timestamptz NOT NULL,
    themes_rows          integer NOT NULL,
    market_segments_rows integer NOT NULL
);

-- --- writer role --------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'richmond_export') THEN
        CREATE ROLE richmond_export LOGIN;
    END IF;
END
$$;

-- --- grants: start from nothing, then add exactly what each role needs --------
REVOKE ALL ON SCHEMA richmond FROM PUBLIC, anon, authenticated, richmond_export;
REVOKE ALL ON ALL TABLES IN SCHEMA richmond FROM PUBLIC, anon, authenticated, richmond_export;

GRANT USAGE ON SCHEMA richmond TO richmond_export, authenticated;
GRANT SELECT, INSERT, DELETE ON richmond.themes, richmond.market_segments TO richmond_export;
GRANT SELECT, INSERT ON richmond.export_runs TO richmond_export;
GRANT SELECT ON richmond.themes, richmond.market_segments, richmond.export_runs TO authenticated;

-- --- row-level security ---------------------------------------------------------
ALTER TABLE richmond.themes          ENABLE ROW LEVEL SECURITY;
ALTER TABLE richmond.market_segments ENABLE ROW LEVEL SECURITY;
ALTER TABLE richmond.export_runs     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS docent_read ON richmond.themes;
DROP POLICY IF EXISTS docent_read ON richmond.market_segments;
DROP POLICY IF EXISTS docent_read ON richmond.export_runs;
CREATE POLICY docent_read ON richmond.themes          FOR SELECT TO authenticated USING (true);
CREATE POLICY docent_read ON richmond.market_segments FOR SELECT TO authenticated USING (true);
CREATE POLICY docent_read ON richmond.export_runs     FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS export_write ON richmond.themes;
DROP POLICY IF EXISTS export_write ON richmond.market_segments;
DROP POLICY IF EXISTS export_write ON richmond.export_runs;
CREATE POLICY export_write ON richmond.themes          FOR ALL TO richmond_export USING (true) WITH CHECK (true);
CREATE POLICY export_write ON richmond.market_segments FOR ALL TO richmond_export USING (true) WITH CHECK (true);
CREATE POLICY export_write ON richmond.export_runs     FOR ALL TO richmond_export USING (true) WITH CHECK (true);

COMMIT;

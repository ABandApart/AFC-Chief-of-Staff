-- =============================================================================
-- verify_schema.sql — confirms migration 0001 applied cleanly
-- =============================================================================
-- Run via:  psql "$DB_URL" -f migrations/verify_schema.sql
-- Or paste into Supabase SQL Editor.
-- Expects 27 tables and 2 extensions. Reports FAIL lines if anything missing.
-- (Baseline 0001 was 18 with `facts`; 0006 dropped `facts`, and 0003/0005/0007
-- added `capture_messages` + `playbook_publications` + `channel_state`
-- → 18 - 1 + 3 = 20; 0009 added `tool_invocations` → 21; 0013 added the six
-- `outreach_*` tables → 27.)
-- =============================================================================

\echo
\echo '=== Extensions ==='
SELECT
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'vector')  = 1 THEN 'OK   vector'    ELSE 'FAIL vector NOT installed'  END AS check_vector,
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'pg_trgm') = 1 THEN 'OK   pg_trgm'   ELSE 'FAIL pg_trgm NOT installed' END AS check_trgm
FROM pg_extension;

\echo
\echo '=== Expected tables (27) ==='
WITH expected(name) AS (VALUES
    ('agent_runs'),
    ('approval_queue'),
    ('buffer_posts'),
    ('capture_messages'),
    ('channel_state'),
    ('content_items'),
    ('content_pipeline'),
    ('dashboard'),
    ('decisions'),
    ('follow_ups'),
    ('icp_signals'),
    ('interest_signals'),
    ('meeting_transcripts'),
    ('outcomes'),
    ('outreach_evidence'),
    ('outreach_events'),
    ('outreach_packets'),
    ('outreach_targets'),
    ('outreach_touches'),
    ('outreach_watch_signals'),
    ('people'),
    ('playbook_publications'),
    ('prospects'),
    ('sources'),
    ('task_candidates'),
    ('tasks'),
    ('tool_invocations')
)
SELECT
    e.name,
    CASE WHEN t.table_name IS NULL THEN 'FAIL missing' ELSE 'OK' END AS status
FROM expected e
LEFT JOIN information_schema.tables t
       ON t.table_schema = 'public' AND t.table_name = e.name
ORDER BY e.name;

\echo
\echo '=== Index spot-check (should show pgvector HNSW indexes) ==='
SELECT
    schemaname,
    tablename,
    indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname LIKE '%_embedding_idx'
ORDER BY tablename;

\echo
\echo '=== Dashboard singleton row ==='
SELECT
    CASE WHEN COUNT(*) = 1 THEN 'OK   dashboard singleton seeded'
         WHEN COUNT(*) = 0 THEN 'FAIL dashboard row missing — re-run migration'
         ELSE                   'FAIL multiple dashboard rows — schema corrupted'
    END AS status
FROM dashboard;

\echo
\echo '=== Track I read layer (migration 0008): brain_reader role + v_* views ==='
SELECT
    CASE WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = 'brain_reader')
         THEN 'OK   brain_reader role present'
         ELSE 'FAIL brain_reader role missing — apply migration 0008'
    END AS role_status;

WITH expected(name) AS (
    VALUES ('v_open_followups'), ('v_pending_task_candidates'),
           ('v_prospect'), ('v_new_prospects'), ('v_spend_summary')
)
SELECT
    e.name AS view_name,
    CASE WHEN v.table_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS status
FROM expected e
LEFT JOIN information_schema.views v
       ON v.table_schema = 'public' AND v.table_name = e.name
ORDER BY e.name;

\echo
\echo '=== Track O outreach layer (migration 0013): views, S1, audit triggers ==='
WITH expected(name) AS (
    VALUES ('v_outreach_scored'), ('v_outreach_evidence_display'), ('v_outreach_capacity')
)
SELECT
    e.name AS view_name,
    CASE WHEN v.table_name IS NOT NULL THEN 'OK' ELSE 'MISSING' END AS status
FROM expected e
LEFT JOIN information_schema.views v
       ON v.table_schema = 'public' AND v.table_name = e.name
ORDER BY e.name;

-- The S1 bands are AUTHORITATIVE here, not in playbook prose (R6). Non-monotonic
-- by design: 5 inside day 14, 3 through the middle, 5 again across the day-60
-- hinge, 1 past day 90. A naive "older is worse" ladder deletes the
-- highest-converting moment in the method, so assert the shape, not just presence.
SELECT
    CASE WHEN outreach_s1(CURRENT_DATE - 14) = 5
          AND outreach_s1(CURRENT_DATE - 30) = 3
          AND outreach_s1(CURRENT_DATE - 60) = 5
          AND outreach_s1(CURRENT_DATE - 91) = 1
         THEN 'OK   outreach_s1 bands 5/3/5/1 (day-60 hinge intact)'
         ELSE 'FAIL outreach_s1 bands wrong — check migration 0013 against 35- §4'
    END AS s1_status;

WITH expected(tbl) AS (
    VALUES ('outreach_targets'), ('outreach_touches'), ('outreach_evidence')
)
SELECT
    e.tbl AS audited_table,
    CASE WHEN EXISTS (
        SELECT FROM pg_trigger g
        JOIN pg_class c ON c.oid = g.tgrelid
        WHERE c.relname = e.tbl AND g.tgname = e.tbl || '_audit' AND NOT g.tgisinternal
    ) THEN 'OK' ELSE 'FAIL audit trigger missing' END AS status
FROM expected e
ORDER BY e.tbl;

-- The runtime app connects as barry_agent; objects it must write have to be
-- owned by it (the bug 0011 fixed for tool_invocations — silent write failures).
SELECT
    c.relname AS outreach_table,
    CASE WHEN pg_get_userbyid(c.relowner) = 'barry_agent'
         THEN 'OK' ELSE 'FAIL owner is ' || pg_get_userbyid(c.relowner) END AS owner_status
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname LIKE 'outreach%'
ORDER BY c.relname;

\echo
\echo '=== Summary ==='
SELECT
    (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public') AS total_tables,
    (SELECT COUNT(*) FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')) AS expected_extensions;

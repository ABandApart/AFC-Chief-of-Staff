-- =============================================================================
-- verify_schema.sql — confirms migration 0001 applied cleanly
-- =============================================================================
-- Run via:  psql "$DB_URL" -f migrations/verify_schema.sql
-- Or paste into Supabase SQL Editor.
-- Expects 20 tables and 2 extensions. Reports FAIL lines if anything missing.
-- (Baseline 0001 was 18 with `facts`; 0006 dropped `facts`, and 0003/0005/0007
-- added `capture_messages` + `playbook_publications` + `channel_state`
-- → 18 - 1 + 3 = 20.)
-- =============================================================================

\echo
\echo '=== Extensions ==='
SELECT
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'vector')  = 1 THEN 'OK   vector'    ELSE 'FAIL vector NOT installed'  END AS check_vector,
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'pg_trgm') = 1 THEN 'OK   pg_trgm'   ELSE 'FAIL pg_trgm NOT installed' END AS check_trgm
FROM pg_extension;

\echo
\echo '=== Expected tables (20) ==='
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
    ('people'),
    ('playbook_publications'),
    ('prospects'),
    ('sources'),
    ('task_candidates'),
    ('tasks')
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
\echo '=== Summary ==='
SELECT
    (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public') AS total_tables,
    (SELECT COUNT(*) FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')) AS expected_extensions;

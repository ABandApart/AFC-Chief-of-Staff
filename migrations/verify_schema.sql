-- =============================================================================
-- verify_schema.sql — confirms migration 0001 applied cleanly
-- =============================================================================
-- Run via:  psql "$DB_URL" -f migrations/verify_schema.sql
-- Or paste into Supabase SQL Editor.
-- Expects 18 tables and 2 extensions. Reports FAIL lines if anything missing.
-- =============================================================================

\echo
\echo '=== Extensions ==='
SELECT
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'vector')  = 1 THEN 'OK   vector'    ELSE 'FAIL vector NOT installed'  END AS check_vector,
    CASE WHEN COUNT(*) FILTER (WHERE extname = 'pg_trgm') = 1 THEN 'OK   pg_trgm'   ELSE 'FAIL pg_trgm NOT installed' END AS check_trgm
FROM pg_extension;

\echo
\echo '=== Expected tables (18) ==='
WITH expected(name) AS (VALUES
    ('agent_runs'),
    ('approval_queue'),
    ('buffer_posts'),
    ('content_items'),
    ('content_pipeline'),
    ('dashboard'),
    ('decisions'),
    ('facts'),
    ('follow_ups'),
    ('icp_signals'),
    ('interest_signals'),
    ('meeting_transcripts'),
    ('outcomes'),
    ('people'),
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
\echo '=== Summary ==='
SELECT
    (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public') AS total_tables,
    (SELECT COUNT(*) FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')) AS expected_extensions;

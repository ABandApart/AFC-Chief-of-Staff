-- =============================================================================
-- Migration 0010: seed starter content sources for Tartt (Phase 4, Task 1)
-- =============================================================================
-- Tartt (`agents/tartt/run.py`) polls `sources` (table from 0001) on each row's
-- own watermark (`last_polled_at` + `poll_interval_hours`). This seeds a **small,
-- deliberately conservative** starter set so the pipeline has something to poll —
-- kept tiny on purpose for the Gemini free-tier *quality* trial (start small,
-- judge summary depth before scaling; PRD-phase-4-discovery §"Free-tier quality
-- trial"). A 12h cadence keeps volume low.
--
-- OPERATOR: these are placeholders — replace/extend with your real feeds. Both
-- are clean, auth-free RSS. Add rows here (or via SQL) as you curate; each row is
-- independent, so this stays a one-time seed.
--
-- Idempotent: guarded by NOT EXISTS on url (sources has no unique(url)), so a
-- re-run inserts nothing and never duplicates.
--
-- Apply via:
--   psql aiadaptive_cos -f migrations/0010_seed_tartt_sources.sql
-- =============================================================================

BEGIN;

INSERT INTO sources (name, url, source_kind, trust_score, poll_interval_hours, active)
SELECT v.name, v.url, v.source_kind, v.trust_score, v.poll_interval_hours, v.active
FROM (VALUES
    ('Hacker News — front page', 'https://hnrss.org/frontpage', 'rss', 0.50, 12, true),
    ('Hacker News — best',       'https://hnrss.org/best',      'rss', 0.50, 12, true)
) AS v(name, url, source_kind, trust_score, poll_interval_hours, active)
WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.url = v.url);

COMMIT;

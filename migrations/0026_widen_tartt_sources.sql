-- =============================================================================
-- Migration 0026: widen Tartt's source seed to 5 (Phase 4 enablement)
-- =============================================================================
-- 0010 seeded 2 Hacker News feeds — enough to prove the pipeline, too thin to
-- judge summary quality on (a read on HN alone is a read on HN, not on Flash).
-- The operator's Gemini free-tier trial (PRD-phase-4-discovery §"Free-tier
-- quality trial") wants a *handful across source types*, so this adds three:
--
--   arXiv cs.AI  — research recency (arxiv RSS → abstract pages)
--   Simon Willison — a high-signal AI-practitioner blog (Atom, full posts)
--   Import AI    — Jack Clark's curated AI research/policy newsletter (Substack RSS)
--
-- Now: aggregator (HN x2) + research + practitioner blog + curated newsletter —
-- four distinct voices, which is what makes the quality read meaningful.
--
-- Cadence stays conservative on purpose: the two heavier aggregators keep their
-- 12h interval; the three additions poll at 24h so total volume barely rises
-- while the trial runs. If the free cap bites, raise poll_interval_hours (the
-- PRD's throttle) rather than upgrading to paid — the point is to judge quality,
-- not throughput. The `tartt` daily ceiling bounds spend regardless.
--
-- The fetcher is feed-type agnostic (feedparser.parse for any RSS/Atom, then
-- trafilatura on each entry link), so `source_kind` here is descriptive only —
-- it does not change how a feed is parsed.
--
-- Idempotent: guarded by NOT EXISTS on url (sources has no unique(url)), matching
-- 0010, so a re-run inserts nothing and never duplicates. Safe alongside any rows
-- the operator has already curated by hand.
--
-- Apply via:
--   psql "$DB_URL" -f migrations/0026_widen_tartt_sources.sql
-- =============================================================================

BEGIN;

INSERT INTO sources (name, url, source_kind, trust_score, poll_interval_hours, active)
SELECT v.name, v.url, v.source_kind, v.trust_score, v.poll_interval_hours, v.active
FROM (VALUES
    ('arXiv — cs.AI (recent)',    'https://export.arxiv.org/rss/cs.AI',          'arxiv',      0.55, 24, true),
    ('Simon Willison — everything', 'https://simonwillison.net/atom/everything/', 'rss',        0.60, 12, true),
    ('Import AI — Jack Clark',    'https://importai.substack.com/feed',          'newsletter', 0.60, 24, true)
) AS v(name, url, source_kind, trust_score, poll_interval_hours, active)
WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.url = v.url);

COMMIT;

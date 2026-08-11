"""Tartt — content-discovery poller (Phase 4).

Polls the `sources` table on each row's own watermark and turns new items into
graph knowledge + reading recs + task candidates. This file is the **Task-1
skeleton**: source selection, the due-check, iteration, and the watermark
advance. The per-source pipeline — fetch (trafilatura) → summarize (Gemini Flash)
→ interest-gate → typed `ContentItem` into the graph (local bge) → score →
briefing/task_candidates — lands in Tasks 2–5, all behind `process_source`.

Each source carries its own cadence (`poll_interval_hours`), so a slow trial
cadence is just data, not code — matching the free-tier quality trial
(PRD-phase-4-discovery). Runs under the `tartt` telemetry label / ceiling.

Run (barry-agent):
    uv run python -m agents.tartt.run --dry-run   # list sources due now, exit
    uv run python -m agents.tartt.run             # poll due sources
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from agents._lib import cognee_setup, db
from agents.tartt import content_graph, fetch, summarize

# Per-poll cap on NEW items processed per source — bounds the Gemini free-tier
# spend during the quality trial (summarize touches Gemini once per item).
MAX_ITEMS_PER_POLL = 5

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def is_due(
    last_polled_at: datetime | None, interval_hours: float, now: datetime
) -> bool:
    """True if a source is due to be polled (pure — unit-tested).

    Never polled → always due. Otherwise due once `interval_hours` have elapsed
    since the last poll.
    """
    if last_polled_at is None:
        return True
    return now - last_polled_at >= timedelta(hours=interval_hours)


def filter_due(sources: list[dict], now: datetime) -> list[dict]:
    """The subset of `sources` due at `now` (pure)."""
    return [
        s for s in sources
        if is_due(s["last_polled_at"], s["poll_interval_hours"], now)
    ]


def active_sources(conn: object) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT id, name, url, source_kind, poll_interval_hours, last_polled_at "
            "FROM sources WHERE active ORDER BY id"
        )
        return cur.fetchall()


def mark_polled(conn: object, source_id: int, when: datetime) -> None:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("UPDATE sources SET last_polled_at = %s WHERE id = %s", (when, source_id))


def unseen_items(items: list[dict], seen: set[str], cap: int) -> list[dict]:
    """Feed items whose URL isn't tracked yet, capped (pure).

    Dedup skips re-summarizing seen URLs; the cap bounds per-poll Gemini spend.
    """
    fresh = [i for i in items if i["url"] not in seen]
    return fresh[:cap]


def seen_urls(conn: object) -> set[str]:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT url FROM content_items")
        return {row[0] for row in cur.fetchall()}


def record_content(
    conn: object, source_id: int, item: dict, summary: str, content_node: str
) -> None:
    """Write the operational tracker row (one per URL; dedup-safe)."""
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            INSERT INTO content_items (url, title, source_id, summary, content_node,
                                       content_type, collected_at)
            VALUES (%s, %s, %s, %s, %s, 'article', now())
            ON CONFLICT (url) DO NOTHING
            """,
            (item["url"], item.get("title", ""), source_id, summary, content_node),
        )


async def process_source(
    conn: object, source: dict, *, cap: int = MAX_ITEMS_PER_POLL
) -> tuple[str, int]:
    """Fetch → dedup → extract → summarize → typed ContentItem → tracker row.

    Interest scoring, gated mode-1 cognify, and task_candidates are Tasks 4-5;
    this lands each new article as a typed graph node (local embed) + a tracker
    row. A URL with no extractable text is skipped (no summarize, no spend).
    """
    items = await asyncio.to_thread(fetch.list_source_items, source["url"])
    seen = await asyncio.to_thread(seen_urls, conn)
    n = 0
    for item in unseen_items(items, seen, cap):
        text = await asyncio.to_thread(fetch.fetch_article_text, item["url"])
        if not text:
            continue
        summary = await asyncio.to_thread(
            summarize.summarize, item["title"], text, source_url=item["url"]
        )
        node = await content_graph.add_content_item(item["url"], item["title"], summary)
        await asyncio.to_thread(record_content, conn, source["id"], item, summary, node)
        n += 1
    return ("processed", n)


async def poll(now: datetime | None = None) -> int:
    """Poll every due source, advancing its watermark. Returns items processed."""
    now = now or datetime.now(UTC)
    cognee_setup.configure_cognee()
    total = 0
    with db.connection() as conn:
        due = filter_due(await asyncio.to_thread(active_sources, conn), now)
        logger.info("tartt: %d source(s) due", len(due))
        for s in due:
            try:
                status, n = await process_source(conn, s)
                await asyncio.to_thread(mark_polled, conn, s["id"], now)
                total += n
                logger.info("tartt: %s → %s (%d item(s))", s["name"], status, n)
            except Exception:
                # One bad source must not stop the rest; its watermark is left
                # unchanged so it retries next cycle.
                logger.exception("tartt: source %s failed", s["id"])
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll content sources (Tartt, Phase 4).")
    parser.add_argument(
        "--dry-run", action="store_true", help="list sources due now and exit"
    )
    args = parser.parse_args()

    if args.dry_run:
        with db.connection() as conn:
            due = filter_due(active_sources(conn), datetime.now(UTC))
        for s in due:
            print(f"  {s['name']:<28} {s['source_kind']:<6} {s['url']}")
        print(f"{len(due)} source(s) due")
        return 0

    asyncio.run(poll())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_pool()

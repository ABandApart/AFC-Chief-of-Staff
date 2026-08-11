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
import logging
import sys
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from agents._lib import db

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


def process_source(source: dict) -> tuple[str, int]:
    """Fetch → summarize → ingest one source's new items. Returns (status, count).

    **Task-1 stub:** the real pipeline (fetch/extract/summarize/ContentItem/score)
    is Tasks 2–5. For now this just logs what it would poll and reports no items,
    so the skeleton (selection + watermark) is exercisable end to end.
    """
    logger.info(
        "tartt: would poll %s [%s] %s", source["name"], source["source_kind"], source["url"]
    )
    return ("skeleton", 0)


def poll(now: datetime | None = None) -> int:
    """Poll every due source, advancing its watermark. Returns items processed."""
    now = now or datetime.now(UTC)
    total = 0
    with db.connection() as conn:
        due = filter_due(active_sources(conn), now)
        logger.info("tartt: %d source(s) due", len(due))
        for s in due:
            try:
                status, n = process_source(s)
                mark_polled(conn, s["id"], now)
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

    poll()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        db.close_pool()

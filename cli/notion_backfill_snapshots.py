"""One-time backfill of weekly KR snapshots (`PRD-notion-integration.md` N4).

    uv run python -m cli.notion_backfill_snapshots --dry-run   # print the weeks; write nothing
    uv run python -m cli.notion_backfill_snapshots             # write them

For each week from 2026-07-27 up to the last completed week, computes the two
metrics with history worth charting, `agent_run_success_rate` and
`llm_spend_month`, and writes them to `okr_snapshots` and the Notion Weekly
Snapshots database. Each row attaches to the KR currently bound to that metric,
with `target` and `status` NULL, because no target existed then.

Never overwrites: a week that already has a row for that KR (for example one the
Monday push wrote) is left alone, and a week that already has a Notion row is
skipped. Safe to re-run. Needs the N2 OKR pull to have run (the KRs must exist)
and `cli.notion_setup` to have created Weekly Snapshots.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any

from agents._lib import kr_metrics as km
from agents._lib.notion_client import NotionError

FIRST_WEEK = date(2026, 7, 27)
METRICS = ("agent_run_success_rate", "llm_spend_month")


def weeks(today: date) -> list[date]:
    """Mondays from FIRST_WEEK through the last week that has ended before `today`."""
    last = today - timedelta(days=today.weekday() + 7)
    out, w = [], FIRST_WEEK
    while w <= last:
        out.append(w)
        w += timedelta(days=7)
    return out


def bound_krs(conn: Any) -> dict[str, int]:
    """metric → id of the active KR bound to it (the newest, if several)."""
    rows = conn.execute(
        "SELECT metric, max(id) FROM okr_key_results WHERE metric = ANY(%s) "
        "AND archived_at IS NULL AND problem IS NULL GROUP BY metric", (list(METRICS),),
    ).fetchall()
    return dict(rows)


def compute_week(conn: Any, week_start: date, tz: Any) -> dict[str, Any]:
    end = week_start + timedelta(days=7)
    as_of = datetime(end.year, end.month, end.day, tzinfo=tz)
    quarter = km.quarter_of(week_start)
    return {m: km.compute(conn, m, quarter=quarter, as_of=as_of) for m in METRICS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill weekly KR snapshots once.")
    parser.add_argument("--dry-run", action="store_true", help="print the weeks; write nothing")
    args = parser.parse_args()

    from agents._lib import db
    from agents._lib.notion_client import NotionClient
    from agents.notion import dashboard_push as push
    from agents.notion import dashboard_schema as dash

    tz = datetime.now().astimezone().tzinfo
    try:
        with db.connection() as conn:
            krs = bound_krs(conn)
            missing = [m for m in METRICS if m not in krs]
            if missing:
                print(f"FAIL  no active KR is bound to {', '.join(missing)}; "
                      "run the OKR pull first")
                return 1
            plan = [(w, compute_week(conn, w, tz)) for w in weeks(date.today())]
        for w, values in plan:
            print(f"week of {w}: " + ", ".join(f"{m} = {v}" for m, v in values.items()))
        if args.dry_run:
            print(f"PLAN  {len(plan)} week(s)")
            return 0

        client = NotionClient.from_keychain()
        new = 0
        with db.connection() as conn:
            for w, values in plan:
                for metric, value in values.items():
                    row = conn.execute(
                        """
                        INSERT INTO okr_snapshots (week_start, okr_key_result_id, metric, value)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (week_start, okr_key_result_id) DO NOTHING
                        RETURNING id
                        """, (w, krs[metric], metric, value)).fetchone()
                    new += row is not None
            sources = push._sources(conn, client)
            pushed = set(push._synced(conn, dash.SYNC_TABLES[dash.SNAPSHOTS]))
        rows = [(w.isoformat(), dash.snapshot_row(w, km.quarter_of(w), values))
                for w, values in plan]
        counts = push.push_rows(client, db.connection, dash.SNAPSHOTS, sources[dash.SNAPSHOTS],
                                [(e, p) for e, p in rows if e not in pushed])
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    print(f"OK    {new} okr_snapshots row(s) written; Weekly Snapshots: "
          f"{counts['created']} row(s) created ({len(rows) - counts['created']} already there)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

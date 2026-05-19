"""Ad-hoc spend queries against agent_runs.

Usage:
    uv run python -m cli.spend                  # today's spend by agent
    uv run python -m cli.spend --by function    # today's spend by function_label
    uv run python -m cli.spend --by day         # last 7 days
    uv run python -m cli.spend --since 7d       # last 7 days, by agent
    uv run python -m cli.spend --since 30d --by function

Reads `db-url` from the current user's keychain.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

import psycopg

from agents._lib.runs import DAILY_CEILINGS, _keychain_get


def parse_since(s: str) -> timedelta:
    """Parse '1h', '30m', '7d', '24h' into a timedelta."""
    m = re.fullmatch(r"(\d+)([hmd])", s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"Invalid --since value '{s}'. Use e.g. 1h, 30m, 7d."
        )
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def query_by_agent(conn: psycopg.Connection, since: datetime) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                agent_name,
                COUNT(*) AS runs,
                COUNT(*) FILTER (WHERE status != 'success') AS failures,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(usd_cost), 0)::float AS usd
            FROM agent_runs
            WHERE started_at >= %s
            GROUP BY agent_name
            ORDER BY usd DESC, runs DESC
            """,
            (since,),
        )
        return list(cur.fetchall())


def query_by_function(conn: psycopg.Connection, since: datetime) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                function_label,
                COUNT(*) AS runs,
                COUNT(*) FILTER (WHERE status != 'success') AS failures,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(usd_cost), 0)::float AS usd
            FROM agent_runs
            WHERE started_at >= %s
            GROUP BY function_label
            ORDER BY usd DESC, runs DESC
            """,
            (since,),
        )
        return list(cur.fetchall())


def query_by_day(conn: psycopg.Connection, since: datetime) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                DATE_TRUNC('day', started_at)::date AS day,
                COUNT(*) AS runs,
                COUNT(*) FILTER (WHERE status != 'success') AS failures,
                COALESCE(SUM(usd_cost), 0)::float AS usd
            FROM agent_runs
            WHERE started_at >= %s
            GROUP BY 1
            ORDER BY 1 DESC
            """,
            (since,),
        )
        return list(cur.fetchall())


def print_by_agent(rows: list[tuple], since: datetime) -> None:
    print(f"=== Spend by agent since {since.isoformat()} ===")
    if not rows:
        print("  (no runs)")
        return
    print(f"  {'agent':<25} {'runs':>6} {'fails':>6} {'input':>10} {'output':>10} {'$':>10}  ceiling")
    print("  " + "-" * 90)
    total = 0.0
    for agent, runs, fails, inp, out, usd in rows:
        ceiling = DAILY_CEILINGS.get(agent, 0)
        ceiling_note = f"${ceiling:.2f}/day" if ceiling else "(no ceiling)"
        print(
            f"  {agent:<25} {runs:>6} {fails:>6} {inp:>10} {out:>10} {usd:>10.4f}  {ceiling_note}"
        )
        total += usd
    print("  " + "-" * 90)
    print(f"  {'TOTAL':<25} {'':>6} {'':>6} {'':>10} {'':>10} {total:>10.4f}")


def print_by_function(rows: list[tuple], since: datetime) -> None:
    print(f"=== Spend by function since {since.isoformat()} ===")
    if not rows:
        print("  (no runs)")
        return
    print(f"  {'function':<25} {'runs':>6} {'fails':>6} {'input':>10} {'output':>10} {'$':>10}")
    print("  " + "-" * 80)
    total = 0.0
    for func, runs, fails, inp, out, usd in rows:
        print(
            f"  {func:<25} {runs:>6} {fails:>6} {inp:>10} {out:>10} {usd:>10.4f}"
        )
        total += usd
    print("  " + "-" * 80)
    print(f"  {'TOTAL':<25} {'':>6} {'':>6} {'':>10} {'':>10} {total:>10.4f}")


def print_by_day(rows: list[tuple], since: datetime) -> None:
    print(f"=== Spend by day since {since.isoformat()} ===")
    if not rows:
        print("  (no runs)")
        return
    print(f"  {'day':<15} {'runs':>6} {'fails':>6} {'$':>10}")
    print("  " + "-" * 50)
    for day, runs, fails, usd in rows:
        print(f"  {str(day):<15} {runs:>6} {fails:>6} {usd:>10.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query spend from agent_runs ledger.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
        "  uv run python -m cli.spend\n"
        "  uv run python -m cli.spend --by function\n"
        "  uv run python -m cli.spend --since 7d --by day",
    )
    parser.add_argument(
        "--by",
        choices=("agent", "function", "day"),
        default="agent",
        help="Aggregation dimension (default: agent)",
    )
    parser.add_argument(
        "--since",
        type=parse_since,
        default=timedelta(hours=24),
        help="Lookback window (e.g. 1h, 7d). Default: 24h.",
    )
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - args.since
    db_url = _keychain_get("db-url")

    with psycopg.connect(db_url) as conn:
        if args.by == "agent":
            rows = query_by_agent(conn, since)
            print_by_agent(rows, since)
        elif args.by == "function":
            rows = query_by_function(conn, since)
            print_by_function(rows, since)
        else:
            rows = query_by_day(conn, since)
            print_by_day(rows, since)

    return 0


if __name__ == "__main__":
    sys.exit(main())

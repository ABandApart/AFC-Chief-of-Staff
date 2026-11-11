"""Nightly export of themes and market segments to docent (`PRD-docent-export.md`).

    uv run python -m agents.docent.export             # one push (the loop runs this)
    uv run python -m agents.docent.export --dry-run   # read and check; write nothing

Each run:

1. Reads `export.themes` and `export.market_segments` over the read-only
   `brain_reader` pool. A view whose columns differ from `COLUMNS` fails the run.
2. Checks every exported string for PII: email, phone, and URL patterns, and
   whole-word matches against the names in `people`, `prospects`,
   `outreach_targets`, and `clients`. Any match fails the run before any write.
3. In one transaction on docent, replaces both `richmond` tables and adds one
   `richmond.export_runs` row.

Every non-dry run writes one `agent_runs` row (`agent_name='docent-export'`,
`usd_cost=0`) and pings the `cos-docent-export` heartbeat.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

AGENT = "docent-export"
FUNCTION_LABEL = "docent-export"
HEARTBEAT_SLUG = "cos-docent-export"
DOCENT_DB_URL_ITEM = "docent-export-db-url"

# The allowlist. Must match the views in migration 0037 and the tables in
# migrations/docent/0001; tests/test_docent_export.py pins it.
COLUMNS: dict[str, tuple[str, ...]] = {
    "themes": ("topic_label", "weight", "last_reinforced_at"),
    "market_segments": (
        "label", "market_size", "market_growth", "firm_profitability",
        "ability_to_pay", "urgency_pain", "offering_fit", "rated_at",
    ),
}

# Names that must never reach docent. Read over barry_agent: brain_reader
# cannot see these base tables, by design.
_DENY_SQL = """
    SELECT name FROM people
    UNION SELECT name FROM prospects
    UNION SELECT company FROM prospects
    UNION SELECT contact_name FROM outreach_targets
    UNION SELECT company_name FROM outreach_targets
    UNION SELECT name FROM clients
"""
_MIN_TERM_LEN = 3

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+?\d[\d\s().-]{7,}\d"),
    "url": re.compile(r"https?://|www\.|\b[\w-]+\.(com|net|org|io|ai|co)\b", re.I),
}


class ExportError(RuntimeError):
    """The run stopped before writing to docent."""


# --- read ------------------------------------------------------------------------


def read_rows(conn: Any) -> dict[str, list[tuple[Any, ...]]]:
    """Read every export view. Raise ExportError if a view's columns drifted."""
    rows: dict[str, list[tuple[Any, ...]]] = {}
    for table, cols in COLUMNS.items():
        cur = conn.execute(f"SELECT * FROM export.{table} ORDER BY 1")
        got = tuple(d.name for d in cur.description)
        if got != cols:
            raise ExportError(f"export.{table} columns {got} do not match COLUMNS {cols}")
        rows[table] = [tuple(r) for r in cur.fetchall()]
    return rows


def deny_terms(conn: Any) -> set[str]:
    """Lower-cased names of every person and company the CoS knows about."""
    terms = set()
    for (value,) in conn.execute(_DENY_SQL).fetchall():
        if value and len(value.strip()) >= _MIN_TERM_LEN:
            terms.add(value.strip().lower())
    return terms


# --- PII gate --------------------------------------------------------------------


def find_pii(rows: dict[str, list[tuple[Any, ...]]], terms: Iterable[str]) -> list[str]:
    """Return one finding per offending value. Findings never repeat the value."""
    term_res = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)") for t in terms]
    findings = []
    for table, table_rows in rows.items():
        cols = COLUMNS[table]
        for i, row in enumerate(table_rows):
            for col, value in zip(cols, row, strict=True):
                if not isinstance(value, str):
                    continue
                low = value.lower()
                kinds = [k for k, p in _PATTERNS.items() if p.search(value)]
                if any(r.search(low) for r in term_res):
                    kinds.append("known name")
                if kinds:
                    findings.append(f"{table}[{i}].{col}: {', '.join(kinds)}")
    return findings


# --- push ------------------------------------------------------------------------


def push(conn: Any, rows: dict[str, list[tuple[Any, ...]]], now: datetime) -> None:
    """Replace both docent tables and log the run, in one transaction."""
    with conn.transaction(), conn.cursor() as cur:
        for table, cols in COLUMNS.items():
            cur.execute(f"DELETE FROM richmond.{table}")
            if rows[table]:
                names = ", ".join((*cols, "exported_at"))
                marks = ", ".join(["%s"] * (len(cols) + 1))
                cur.executemany(
                    f"INSERT INTO richmond.{table} ({names}) VALUES ({marks})",
                    [(*r, now) for r in rows[table]],
                )
        cur.execute(
            "INSERT INTO richmond.export_runs (ran_at, themes_rows, market_segments_rows) "
            "VALUES (%s, %s, %s)",
            (now, len(rows["themes"]), len(rows["market_segments"])),
        )


def run_once(*, source_ro: Any, source_rw: Any, docent: Callable[[], Any] | None,
             now: datetime) -> str:
    """Read, check, and (unless `docent` is None) push. Return a one-line summary."""
    rows = read_rows(source_ro)
    findings = find_pii(rows, deny_terms(source_rw))
    if findings:
        raise ExportError(f"PII check failed, nothing written: {'; '.join(findings)}")
    counts = f"themes {len(rows['themes'])}, market_segments {len(rows['market_segments'])}"
    if docent is None:
        return f"dry run: {counts}; PII check: clean"
    with docent() as conn:
        push(conn, rows, now)
    return f"pushed {counts}"


# --- runtime wiring ----------------------------------------------------------------


@contextmanager
def _docent_connection() -> Iterator[Any]:
    import psycopg

    from agents._lib import creds

    # prepare_threshold=None: no server-side prepared statements, so this works
    # through either Supabase pooler.
    with psycopg.connect(creds.keychain_get(DOCENT_DB_URL_ITEM), connect_timeout=10,
                         prepare_threshold=None) as conn:
        yield conn


@contextmanager
def _ledger(trigger_kind: str) -> Iterator[None]:
    """Write one agent_runs row when the block exits. Never masks the block's error."""
    from agents._lib import db

    started_at = datetime.now(UTC)
    status, error_text = "success", None
    try:
        yield
    except BaseException as exc:
        status, error_text = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        try:
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_runs (agent_name, function_label, trigger_kind,
                                            started_at, ended_at, status, usd_cost, error_text)
                    VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
                    """,
                    (AGENT, FUNCTION_LABEL, trigger_kind, started_at, datetime.now(UTC),
                     status, error_text),
                )
        except Exception:
            logger.warning("failed to write the agent_runs row", exc_info=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Push themes and market segments to docent.")
    parser.add_argument("--dry-run", action="store_true", help="read and check; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    args = parser.parse_args()

    from agents._lib import db, heartbeat

    now = datetime.now(UTC)
    try:
        with db.ro_connection() as ro, db.connection() as rw:
            if args.dry_run:
                print(run_once(source_ro=ro, source_rw=rw, docent=None, now=now))
                return 0
            with _ledger("manual" if args.manual else "scheduled"):
                print(run_once(source_ro=ro, source_rw=rw, docent=_docent_connection, now=now))
    except Exception as e:
        print(f"FAIL  {e}")
        if not args.dry_run:
            heartbeat.ping_fail(HEARTBEAT_SLUG)
        return 1
    heartbeat.ping(HEARTBEAT_SLUG)
    return 0


if __name__ == "__main__":
    sys.exit(main())

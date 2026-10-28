"""Pull the master OKR databases into Postgres (`PRD-notion-integration.md`, N2).

    uv run python -m agents.notion.okr_pull             # one cycle (the loop runs this)
    uv run python -m agents.notion.okr_pull --dry-run   # read Notion, print, write nothing

Each cycle reads every page of Objectives, Key Results, and Initiatives (ids from
`notion_sync`, entity_table `notion_schema`, written by `cli.notion_setup`) and
upserts them into `okr_objectives`, `okr_key_results`, and `okr_initiatives`.
Notion owns every field (N-S1); this module never writes to Notion.

**Validation.** A page is cached even when it is malformed, with the reason in
`problem`, so consumers can tell "missing" from "broken". Consumers read only
rows with `problem IS NULL AND archived_at IS NULL`. The problems:

- a Key Result with no Objective, or more than one (the API can't enforce the
  relation's "limit 1 page", so the pull does);
- an Initiative with no Key Result, or more than one;
- a link to a page that is not in the linked database;
- a Key Result whose Metric is blank or not an id in `okr_schema.METRICS`
  (Notion lets an editor type a new select option);
- an Objective whose Quarter is blank or not `YYYY-Qn`.

Each malformed page gets one #system notice per day until it is fixed.

**Archiving.** `archived_at` is set when a page leaves its database and, for
Objectives and Key Results, when Status is Retired. It clears if the page returns.
Initiatives keep Paused and Done as statuses: N3 shows them on linked tasks.

Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from agents._lib.notion_client import NotionError, title_of
from agents.notion import okr_schema as s

FUNCTION_LABEL = "okr_pull"
SCHEMA_TABLE = "notion_schema"  # notion_sync.entity_table written by cli.notion_setup
SCHEMA_KEYS = {s.OBJECTIVES: "okr_objectives", s.KEY_RESULTS: "okr_key_results",
               s.INITIATIVES: "okr_initiatives"}
QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
MAX_NOTICE_LINES = 15


@dataclass
class Row:
    """One Notion page, parsed into the columns of its cache table (pure data)."""

    page_id: str
    name: str
    fields: dict[str, Any]
    links: list[str] = field(default_factory=list)  # Objective or Key Result page ids
    edited_at: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def problem(self) -> str | None:
        return "; ".join(self.problems) or None


# --- parsing (pure) ----------------------------------------------------------------

def _prop(page: dict[str, Any], name: str) -> dict[str, Any]:
    return (page.get("properties") or {}).get(name) or {}


def _select(page: dict[str, Any], name: str) -> str | None:
    value = _prop(page, name).get("select")
    return (value or {}).get("name") or None


def _number(page: dict[str, Any], name: str) -> float | None:
    return _prop(page, name).get("number")


def _relation(page: dict[str, Any], name: str) -> list[str]:
    return [r["id"] for r in _prop(page, name).get("relation") or [] if r.get("id")]


def _people(page: dict[str, Any], name: str) -> str | None:
    names = [p.get("name") for p in _prop(page, name).get("people") or [] if p.get("name")]
    return ", ".join(names) or None


def _row(page: dict[str, Any], fields: dict[str, Any], links: list[str] | None = None) -> Row:
    return Row(page_id=page["id"], name=title_of(page), fields=fields, links=links or [],
               edited_at=page.get("last_edited_time"))


def parse_objective(page: dict[str, Any]) -> Row:
    return _row(page, {"quarter": _select(page, "Quarter"), "sort_order": _number(page, "Order"),
                       "status": _select(page, "Status")})


def parse_key_result(page: dict[str, Any]) -> Row:
    return _row(page, {
        "metric": _select(page, "Metric"), "target": _number(page, "Target"),
        "unit": _select(page, "Unit"), "direction": _select(page, "Direction"),
        "manual_value": _number(page, "Manual value"), "status": _select(page, "Status"),
    }, _relation(page, "Objective"))


def parse_initiative(page: dict[str, Any]) -> Row:
    return _row(page, {"status": _select(page, "Status"), "owner_name": _people(page, "Owner")},
                _relation(page, "Key Result"))


# --- validation (pure) -------------------------------------------------------------

def _check_link(row: Row, parents: set[str], parent_label: str, plural: str) -> None:
    if not row.links:
        row.problems.append(f"no {parent_label}")
    elif len(row.links) > 1:
        row.problems.append(f"links to {len(row.links)} {plural} (the limit is 1)")
    elif row.links[0] not in parents:
        row.problems.append(f"its {parent_label} is not in the {plural} database")


def validate(objectives: list[Row], key_results: list[Row], initiatives: list[Row]) -> None:
    """Fill in `problems` on every malformed row, in place."""
    for o in objectives:
        quarter = o.fields["quarter"]
        if not quarter:
            o.problems.append("no Quarter")
        elif not QUARTER_RE.match(quarter):
            o.problems.append(f"Quarter {quarter!r} is not YYYY-Qn")
    objective_ids = {o.page_id for o in objectives}
    for kr in key_results:
        _check_link(kr, objective_ids, "Objective", "Objectives")
        metric = kr.fields["metric"]
        if not metric:
            kr.problems.append("no Metric")
        elif metric not in s.METRICS:
            kr.problems.append(f"unknown Metric {metric!r}")
    kr_ids = {k.page_id for k in key_results}
    for ini in initiatives:
        _check_link(ini, kr_ids, "Key Result", "Key Results")


# --- notices (pure) ----------------------------------------------------------------

def page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def notice_text(items: list[tuple[str, str, str, str]]) -> str:
    """The #system message for (database, page name, problem, page id) items."""
    lines = [f"**OKR pull:** {len(items)} page(s) in OKRs — Master need fixing. "
             "The CoS ignores them until they are fixed."]
    for database, name, problem, page_id in items[:MAX_NOTICE_LINES]:
        lines.append(f"• {database} · {name}: {problem} · <{page_url(page_id)}>")
    if len(items) > MAX_NOTICE_LINES:
        lines.append(f"…and {len(items) - MAX_NOTICE_LINES} more.")
    return "\n".join(lines)


# --- Notion reads ------------------------------------------------------------------

def fetch(client: Any, database_ids: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """Every live page of each master database, keyed by database name."""
    out: dict[str, list[dict[str, Any]]] = {}
    for name in s.DATABASE_ORDER:
        sources = client.retrieve_database(database_ids[name]).get("data_sources") or []
        if not sources:
            raise RuntimeError(f"database {name!r} has no data source")
        out[name] = [p for p in client.iter_query(sources[0]["id"])
                     if not p.get("in_trash") and not p.get("archived")]
    return out


def parse_all(pages: dict[str, list[dict[str, Any]]]) -> tuple[list[Row], list[Row], list[Row]]:
    objectives = [parse_objective(p) for p in pages[s.OBJECTIVES]]
    key_results = [parse_key_result(p) for p in pages[s.KEY_RESULTS]]
    initiatives = [parse_initiative(p) for p in pages[s.INITIATIVES]]
    validate(objectives, key_results, initiatives)
    return objectives, key_results, initiatives


# --- Postgres ----------------------------------------------------------------------

def read_database_ids(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        "SELECT entity_id, notion_page_id FROM notion_sync WHERE entity_table = %s",
        (SCHEMA_TABLE,),
    ).fetchall()
    by_key = {r[0]: r[1] for r in rows}
    missing = [k for k in SCHEMA_KEYS.values() if k not in by_key]
    if missing:
        raise RuntimeError(f"notion_sync has no {', '.join(missing)}; run cli.notion_setup first")
    return {name: by_key[key] for name, key in SCHEMA_KEYS.items()}


# Column lists per table, after notion_page_id and name. `retires` = Status
# Retired archives the row.
_TABLES = {
    "okr_objectives": (["quarter", "sort_order", "status"], None, True),
    "okr_key_results": (["metric", "target", "unit", "direction", "manual_value", "status"],
                        "objective_id", True),
    "okr_initiatives": (["status", "owner_name"], "key_result_id", False),
}


def _upsert(conn: Any, table: str, rows: list[Row], parent_ids: dict[str, int],
            now: datetime) -> int:
    columns, parent_col, retires = _TABLES[table]
    cols = ["notion_page_id", "name", *columns, *([parent_col] if parent_col else []),
            "problem", "notion_edited_at", "pulled_at", "archived_at"]
    retired = "EXCLUDED.status = 'Retired'" if retires else "false"
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:-1])
    sql = f"""
        INSERT INTO {table} ({", ".join(cols)})
        VALUES ({", ".join(["%s"] * len(cols))})
        ON CONFLICT (notion_page_id) DO UPDATE SET {updates},
            problem_notified_on = CASE WHEN EXCLUDED.problem IS NULL THEN NULL
                                       ELSE {table}.problem_notified_on END,
            archived_at = CASE WHEN {retired}
                               THEN COALESCE({table}.archived_at, EXCLUDED.pulled_at)
                               ELSE NULL END
    """  # noqa: S608 — table and column names come from _TABLES, not input
    for row in rows:
        values = [row.page_id, row.name, *(row.fields[c] for c in columns)]
        if parent_col:
            values.append(parent_ids.get(row.links[0]) if len(row.links) == 1 else None)
        archived = now if retires and row.fields.get("status") == "Retired" else None
        values += [row.problem, row.edited_at, now, archived]
        conn.execute(sql, values)
    gone = conn.execute(
        f"UPDATE {table} SET archived_at = %s "  # noqa: S608
        "WHERE archived_at IS NULL AND NOT (notion_page_id = ANY(%s))",
        (now, [r.page_id for r in rows]),
    ).rowcount
    return gone


def _ids(conn: Any, table: str) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT notion_page_id, id FROM {table}").fetchall()}  # noqa: S608


def apply(conn: Any, objectives: list[Row], key_results: list[Row], initiatives: list[Row],
          now: datetime) -> dict[str, int]:
    """Upsert all three tables in one transaction, parents first. Returns counts."""
    with conn.transaction():
        gone_o = _upsert(conn, "okr_objectives", objectives, {}, now)
        gone_k = _upsert(conn, "okr_key_results", key_results, _ids(conn, "okr_objectives"), now)
        gone_i = _upsert(conn, "okr_initiatives", initiatives, _ids(conn, "okr_key_results"), now)
    return {"objectives": len(objectives), "key_results": len(key_results),
            "initiatives": len(initiatives), "left_notion": gone_o + gone_k + gone_i}


_LABELS = {"okr_objectives": s.OBJECTIVES, "okr_key_results": s.KEY_RESULTS,
           "okr_initiatives": s.INITIATIVES}


def due_notices(conn: Any, today: date) -> list[tuple[str, str, str, str]]:
    """Malformed live pages not yet reported today."""
    items = []
    for table, label in _LABELS.items():
        for name, problem, page_id in conn.execute(
            f"SELECT name, problem, notion_page_id FROM {table} "  # noqa: S608
            "WHERE problem IS NOT NULL AND archived_at IS NULL "
            "AND (problem_notified_on IS NULL OR problem_notified_on < %s) ORDER BY name",
            (today,),
        ).fetchall():
            items.append((label, name, problem, page_id))
    return items


def mark_notified(conn: Any, items: list[tuple[str, str, str, str]], today: date) -> None:
    ids = [i[3] for i in items]
    for table in _LABELS:
        conn.execute(f"UPDATE {table} SET problem_notified_on = %s "  # noqa: S608
                     "WHERE notion_page_id = ANY(%s)", (today, ids))


# --- one cycle ---------------------------------------------------------------------

def run_once(client: Any, *, connection: Any, post: Any, now: datetime | None = None,
             today: date | None = None) -> str:
    """Read Notion, refresh the cache, and post any due notice. Returns a summary line.

    `connection` is a context manager factory (`db.connection`) and `post(text)`
    sends to #system and returns True on success; both are injected for testing.
    """
    now = now or datetime.now(UTC)
    today = today or date.today()  # local day, matching the spend breaker's "today"
    with connection() as conn:
        database_ids = read_database_ids(conn)
    objectives, key_results, initiatives = parse_all(fetch(client, database_ids))
    with connection() as conn:
        counts = apply(conn, objectives, key_results, initiatives, now)
        due = due_notices(conn, today)
        if due and post(notice_text(due)):
            mark_notified(conn, due, today)
    bad = sum(1 for r in (*objectives, *key_results, *initiatives) if r.problems)
    return (f"OK    pulled {counts['objectives']} objectives, {counts['key_results']} KRs, "
            f"{counts['initiatives']} initiatives; {bad} malformed; "
            f"{counts['left_notion']} newly gone from Notion; {len(due)} notice line(s)")


def _post_system(text: str) -> bool:
    from agents._lib import discord_rest
    from agents.discord_bot.config import SYSTEM_CHANNEL_ID

    return discord_rest.post(SYSTEM_CHANNEL_ID, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull the master OKR databases from Notion.")
    parser.add_argument("--dry-run", action="store_true",
                        help="read Notion and print the parsed rows; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    args = parser.parse_args()

    from agents._lib import db
    from agents._lib.notion_client import NotionClient
    from agents.notion.ledger import notion_run

    try:
        client = NotionClient.from_keychain()
        if args.dry_run:
            with db.connection() as conn:
                database_ids = read_database_ids(conn)
            for rows in parse_all(fetch(client, database_ids)):
                for r in rows:
                    flag = f"BAD   ({r.problem})" if r.problems else "OK   "
                    print(f"{flag} {r.name}  {r.fields}")
            return 0
        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection, post=_post_system))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

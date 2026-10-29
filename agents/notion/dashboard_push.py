"""Monday dashboard push (`PRD-notion-integration.md`, N4).

    uv run python -m agents.notion.dashboard_push             # one push (the loop runs this)
    uv run python -m agents.notion.dashboard_push --dry-run   # print the scores; write nothing

Each run covers the week that just ended (Monday 00:00 to Monday 00:00, local),
in the quarter its Monday falls in:

1. Builds that quarter's dashboard page if it is missing (quarter rollover: the
   first push whose week starts in a new quarter builds the new page).
2. Scores every active KR of the quarter through `_lib/kr_metrics.py`.
3. Upserts one `okr_snapshots` row per KR for that week.
4. Pushes the KR Scorecard, Initiative Status, and Weekly Snapshots rows, the
   header, and the 5 callouts. An unchanged row or block is skipped by its hash.
5. Posts the 5-line summary to #dashboard, on the week's first run only.

A re-run in the same week overwrites that week's snapshot rows and changes
nothing else, so a second push with no data change makes 0 Notion writes.

Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row

from agents._lib import kr_metrics as km
from agents._lib.notion_client import NotionError, content_hash
from agents.notion import dashboard_page
from agents.notion import dashboard_schema as dash

FUNCTION_LABEL = "dashboard_push"

_KRS_SQL = """
    SELECT kr.id, kr.notion_page_id, kr.name, kr.metric, kr.target, kr.direction,
           kr.manual_value
    FROM okr_key_results kr
    JOIN okr_objectives o ON o.id = kr.objective_id
    WHERE o.quarter = %s AND o.status = 'Active' AND kr.status = 'Active'
      AND o.archived_at IS NULL AND o.problem IS NULL
      AND kr.archived_at IS NULL AND kr.problem IS NULL
    ORDER BY o.sort_order NULLS LAST, kr.id
"""

_INITIATIVES_SQL = """
    SELECT i.id, i.name, i.notion_page_id,
           count(t.id) FILTER (WHERE t.status = 'open') AS open_todos,
           count(t.id) FILTER (WHERE t.status = 'completed') AS done_todos
    FROM okr_initiatives i
    JOIN okr_key_results kr ON kr.id = i.key_result_id
    JOIN okr_objectives o ON o.id = kr.objective_id
    LEFT JOIN tasks t ON t.okr_initiative_id = i.id
    WHERE o.quarter = %s AND i.status = 'Active'
      AND i.archived_at IS NULL AND i.problem IS NULL
      AND kr.archived_at IS NULL AND kr.problem IS NULL
    GROUP BY i.id ORDER BY i.id
"""

# Counts only (N-S8). Each is a number; the page and Discord never name a company.
_ATTENTION_SQL = """
    SELECT
      (SELECT count(*) FROM outreach_discoveries
        WHERE surfaced_at IS NOT NULL AND reviewed_at IS NULL) AS discoveries_to_review,
      (SELECT count(*) FROM outreach_touches
        WHERE window_closes < %(today)s AND sent_at IS NULL AND skipped_at IS NULL)
        AS missed_touches,
      (SELECT count(*) FROM follow_ups
        WHERE status = 'open' AND deadline < %(today)s) AS overdue_followups,
      (SELECT count(*) FROM task_candidates
        WHERE status = 'pending' AND discord_message_id IS NOT NULL) AS cards_pending,
      (SELECT count(DISTINCT agent_name) FROM agent_runs
        WHERE status NOT IN ('success', 'running')
          AND started_at >= %(today)s::date - 7) AS failing_agents,
      (SELECT count(*) FROM tasks WHERE status = 'open') AS open_todos
"""


def week_of(now: datetime) -> tuple[date, datetime]:
    """(Monday that started the week just ended, Monday 00:00 that ended it)."""
    local = now.astimezone()
    monday = local.date() - timedelta(days=local.weekday())
    as_of = datetime(monday.year, monday.month, monday.day, tzinfo=local.tzinfo)
    return monday - timedelta(days=7), as_of


@dataclass
class Week:
    quarter: str
    week_start: date
    as_of: datetime
    krs: list[dict[str, Any]]
    results: list[km.Result]
    initiatives: list[dict[str, Any]]
    attention: dict[str, int]
    rows: dict[str, list[tuple[str, dict[str, Any]]]] = field(default_factory=dict)


def compute(conn: Any, now: datetime) -> Week:
    """Score the week that just ended and build every row (reads only)."""
    week_start, as_of = week_of(now)
    # A week belongs to the quarter its Monday falls in, so the week that spans a
    # quarter's end closes that quarter (its metrics stop at the quarter's last day).
    quarter = km.quarter_of(week_start)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_KRS_SQL, (quarter,))
        krs = cur.fetchall()
        cur.execute(_INITIATIVES_SQL, (quarter,))
        initiatives = cur.fetchall()
        cur.execute(_ATTENTION_SQL, {"today": as_of.date()})
        attention = dict(cur.fetchone())
    results = [km.evaluate(conn, kr, quarter=quarter, as_of=as_of) for kr in krs]
    w = Week(quarter, week_start, as_of, krs, results, initiatives, attention)
    by_id = {kr["id"]: kr for kr in krs}
    w.rows[dash.SCORECARD] = [
        (f"{quarter}:{r.key_result_id}",
         dash.scorecard_row(r, kr_page_id=by_id[r.key_result_id]["notion_page_id"],
                            quarter=quarter, direction=by_id[r.key_result_id]["direction"],
                            updated=as_of.date()))
        for r in results]
    w.rows[dash.INITIATIVE_STATUS] = [
        (f"{quarter}:{i['id']}", dash.initiative_row(i, quarter=quarter, updated=as_of.date()))
        for i in initiatives]
    values = {r.metric: r.current for r in results if r.metric != "manual"}
    w.rows[dash.SNAPSHOTS] = [(week_start.isoformat(),
                               dash.snapshot_row(week_start, quarter, values))]
    return w


def blocks(w: Week) -> dict[str, tuple[str, dict[str, Any]]]:
    """Block key → (block type, PATCH body) for the header and the 5 callouts."""
    from agents._lib.notion_client import rich_text

    out: dict[str, tuple[str, dict[str, Any]]] = {
        "header": ("paragraph", {"paragraph": {"rich_text": rich_text(
            dash.header_text(w.quarter, w.as_of.date(), w.as_of.date()))}})}
    for key, (emoji, text) in zip(dashboard_page.STATS,
                                  dash.stat_callouts(w.results, w.attention), strict=True):
        out[key] = ("callout", {"callout": {"rich_text": rich_text(text),
                                            "icon": {"type": "emoji", "emoji": emoji}}})
    out["attention"] = ("callout", {"callout": {
        "rich_text": rich_text(dash.attention_text(w.attention)),
        "icon": {"type": "emoji", "emoji": "⚠️"}}})
    return out


def summary(w: Week, page_id: str | None) -> str:
    """The 5-line #dashboard post (N-S8): north star, worst KR, attention, health, link."""
    def kr(metric: str) -> str:
        r = next((x for x in w.results if x.metric == metric), None)
        if r is None:
            return "not tracked"
        tgt = "" if r.target is None else f" / {r.target:,.0f}"
        cur = "—" if r.current is None else f"{r.current:,.0f}"
        return f"{cur}{tgt} {r.score.label.split()[0]}"

    worst = km.worst(w.results)
    worst_line = (f"Worst KR: {worst.name} — {worst.score.label}" if worst
                  else "Worst KR: none off track or at risk")
    att = [f"{w.attention[k]} {label}" for k, label in (
        ("discoveries_to_review", "to review"), ("missed_touches", "missed touches"),
        ("overdue_followups", "overdue follow-ups"), ("cards_pending", "cards waiting"))
        if w.attention.get(k)]
    health = next((x for x in w.results if x.metric == "agent_run_success_rate"), None)
    spend = next((x for x in w.results if x.metric == "llm_spend_month"), None)
    link = f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else "(no page yet)"
    runs = "—" if not health or health.current is None else f"{health.current:.0f}%"
    spent = spend.current if spend and spend.current is not None else Decimal(0)
    return "\n".join([
        f"📊 **{dash.page_title(w.quarter)}** · week of {w.week_start:%b %-d}",
        f"North star: engagements {kr('engagements_signed')} · proposals {kr('proposals_sent')}",
        worst_line,
        "Attention: " + (" · ".join(att) if att else "nothing"),
        f"Health: runs OK {runs} · LLM spend ${spent:,.2f} this month · {link}",
    ])


# --- writes -----------------------------------------------------------------------------


def save_snapshots(conn: Any, w: Week) -> int:
    """Upsert the week's okr_snapshots rows. Returns how many were new (0 on a re-run)."""
    new = 0
    for r in w.results:
        row = conn.execute(
            """
            INSERT INTO okr_snapshots (week_start, okr_key_result_id, metric, value, target,
                                       status, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (week_start, okr_key_result_id) DO UPDATE SET
                metric = EXCLUDED.metric, value = EXCLUDED.value, target = EXCLUDED.target,
                status = EXCLUDED.status, computed_at = now()
            RETURNING (xmax = 0)
            """,
            (w.week_start, r.key_result_id, r.metric, r.current, r.target, r.score.status),
        ).fetchone()
        new += bool(row[0])
    return new


def _synced(conn: Any, table: str) -> dict[str, tuple[str, str | None]]:
    rows = conn.execute("SELECT entity_id, notion_page_id, content_hash FROM notion_sync "
                        "WHERE entity_table = %s", (table,)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _mark(conn: Any, table: str, entity_id: str, notion_id: str, digest: str) -> None:
    conn.execute(
        """
        INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                 last_pushed_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (entity_table, entity_id) DO UPDATE SET
            notion_page_id = EXCLUDED.notion_page_id, content_hash = EXCLUDED.content_hash,
            last_pushed_at = now()
        """, (table, entity_id, notion_id, digest))


def push_rows(client: Any, connection: Any, name: str, data_source_id: str,
              rows: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
    """Create or update one database's rows, skipping unchanged ones by hash."""
    table = dash.SYNC_TABLES[name]
    live = {p["id"] for p in client.iter_query(data_source_id)
            if not p.get("in_trash") and not p.get("archived")}
    counts = {"created": 0, "updated": 0, "skipped": 0}
    with connection() as conn:
        synced = _synced(conn, table)
    for entity_id, props in rows:
        digest = content_hash(props)
        page_id, old = synced.get(entity_id, (None, None))
        if page_id in live and digest == old:
            counts["skipped"] += 1
            continue
        if page_id in live:
            client.update_page(page_id, props)
            counts["updated"] += 1
        else:
            page_id = client.create_page(
                {"type": "data_source_id", "data_source_id": data_source_id}, props)["id"]
            counts["created"] += 1
        with connection() as conn:
            _mark(conn, table, entity_id, page_id, digest)
    return counts


def push_blocks(client: Any, connection: Any, quarter: str, block_ids: dict[str, str],
                bodies: dict[str, tuple[str, dict[str, Any]]]) -> int:
    """Update the header and callouts that changed. Returns the number updated."""
    with connection() as conn:
        synced = _synced(conn, dash.SYNC_BLOCKS)
    updated = 0
    for key, (_, body) in bodies.items():
        block_id = block_ids.get(key)
        if not block_id:
            continue
        entity_id = f"{quarter}:{key}"
        digest = content_hash(body)
        if synced.get(entity_id, (None, None))[1] == digest:
            continue
        client.update_block(block_id, body)
        updated += 1
        with connection() as conn:
            _mark(conn, dash.SYNC_BLOCKS, entity_id, block_id, digest)
    return updated


def _sources(conn: Any, client: Any) -> dict[str, str]:
    out = {}
    for name in dash.DATABASES:
        row = conn.execute("SELECT notion_page_id FROM notion_sync WHERE entity_table = "
                           "'notion_schema' AND entity_id = %s",
                           (dash.SCHEMA_KEYS[name],)).fetchone()
        if row is None:
            raise RuntimeError(f"notion_sync has no {dash.SCHEMA_KEYS[name]}; "
                               "run cli.notion_setup first")
        sources = client.retrieve_database(row[0]).get("data_sources") or []
        if not sources:
            raise RuntimeError(f"the {name} database has no data source")
        out[name] = sources[0]["id"]
    return out


def _root(conn: Any) -> str:
    row = conn.execute("SELECT notion_page_id FROM notion_sync WHERE entity_table = "
                       "'notion_schema' AND entity_id = 'todo_database'").fetchone()
    if row is None:
        raise RuntimeError("notion_sync has no todo_database; run cli.notion_setup first")
    return row[0]


def run_once(client: Any, *, connection: Any, now: datetime,
             post: Callable[[str], bool | None], say: Callable[[str], None] = print) -> str:
    """One Monday push. `post` sends the #dashboard summary (None: channel unset)."""
    with connection() as conn:
        w = compute(conn, now)
        sources = _sources(conn, client)
        todo_db = _root(conn)
    root = (client.retrieve_database(todo_db).get("parent") or {}).get("page_id")
    if not root:
        raise RuntimeError("could not find the root page from the To-Do database")
    lookup, record = dashboard_page.recorder(connection, w.quarter)
    block_ids = dashboard_page.ensure_page(client, root=root, quarter=w.quarter,
                                           sources=sources, lookup=lookup, record=record,
                                           say=say)
    with connection() as conn:
        new = save_snapshots(conn, w)
    counts = {name: push_rows(client, connection, name, sources[name], w.rows[name])
              for name in dash.DATABASES}
    n_blocks = push_blocks(client, connection, w.quarter, block_ids, blocks(w))
    posted = "not posted (re-run this week)"
    if new:
        sent = post(summary(w, block_ids.get("page")))
        posted = {True: "posted to #dashboard", False: "post to #dashboard FAILED",
                  None: "not posted (DASHBOARD_CHANNEL_ID is 0)"}[sent]
    writes = sum(c["created"] + c["updated"] for c in counts.values()) + n_blocks
    return (f"OK    week of {w.week_start} ({w.quarter}): {len(w.results)} KRs scored, "
            f"{new} new snapshot row(s); {writes} Notion write(s) "
            f"({', '.join(f'{n}: {c['created']}+{c['updated']}' for n, c in counts.items())}, "
            f"blocks: {n_blocks}); summary {posted}")


def _post(text: str) -> bool | None:
    from agents._lib import discord_rest
    from agents.discord_bot.config import DASHBOARD_CHANNEL_ID

    if not DASHBOARD_CHANNEL_ID:
        return None
    return discord_rest.post(DASHBOARD_CHANNEL_ID, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Push the weekly KR dashboard to Notion.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every KR's score and the summary; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    args = parser.parse_args()

    from agents._lib import db
    from agents._lib.notion_client import NotionClient
    from agents.notion.ledger import notion_run

    try:
        if args.dry_run:
            with db.connection() as conn:
                w = compute(conn, datetime.now().astimezone())
            for r in w.results:
                print(f"KR #{r.key_result_id} {r.name}: {r.metric} = {r.current} "
                      f"(target {r.target}) → {r.score.label}")
            print(f"attention: {w.attention}")
            print("--- #dashboard summary ---")
            print(summary(w, None))
            return 0
        client = NotionClient.from_keychain()
        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection,
                           now=datetime.now().astimezone(), post=_post))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

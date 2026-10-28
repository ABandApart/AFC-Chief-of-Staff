"""Sync tasks with the Notion To-Do database (`PRD-notion-integration.md`, N3).

    uv run python -m agents.notion.todo_sync             # one cycle (the loop runs this)
    uv run python -m agents.notion.todo_sync --dry-run   # print the plan; write nothing

Each cycle, for every open or completed task:

1. **Done.** Compare the task's status with the row's Done box and the value
   both last agreed on (`notion_sync.synced_done`). The side that changed wins
   (N-S16). A change from Notion completes or reopens the task, and its follow-up,
   through the same functions the #tasks checklist uses.
2. **Push.** Write the row's projection (`todo_schema.projection`). An unchanged
   projection is skipped by its hash. A task with no row, or whose row was deleted
   in Notion, gets a new row.

The first run is the backfill: it creates a row for every task accepted before
N3, as Unassigned. Re-running creates none. Rows added by hand in Notion are left
alone; to-dos are added with `/todo` in Discord.

Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row

from agents._lib.notion_client import NotionError, content_hash
from agents.notion import todo_schema as todo

FUNCTION_LABEL = "todo_sync"

_TASKS_SQL = """
    SELECT t.id, t.title, t.status, t.due_date, t.created_at, tc.source_type,
           CASE WHEN i.archived_at IS NULL THEN i.notion_page_id END AS initiative_page_id,
           ns.notion_page_id, ns.content_hash, ns.synced_done
    FROM tasks t
    LEFT JOIN task_candidates tc ON tc.id = t.source_candidate_id
    LEFT JOIN okr_initiatives i ON i.id = t.okr_initiative_id
    LEFT JOIN notion_sync ns ON ns.entity_table = %s AND ns.entity_id = t.id::text
    WHERE t.status IN ('open', 'completed')
    ORDER BY t.id
"""


@dataclass
class Step:
    """What one cycle does for one task (pure data, so a dry run can print it)."""

    task_id: int
    done: str  # merge_done's answer: push | pull | agree
    write: str  # create | update | skip
    page_id: str | None
    props: dict[str, Any]
    digest: str
    final_done: bool


def plan(tasks: list[dict[str, Any]], pages: dict[str, dict[str, Any]]) -> list[Step]:
    """Decide each task's Done merge and row write (pure)."""
    steps = []
    for t in tasks:
        page = pages.get(t["notion_page_id"]) if t["notion_page_id"] else None
        notion_done = None
        if page is not None:
            done_prop = (page.get("properties") or {}).get("Done") or {}
            notion_done = bool(done_prop.get("checkbox"))
        pg_done = t["status"] == "completed"
        merge = todo.merge_done(pg_done, notion_done, t["synced_done"])
        final = notion_done if merge == "pull" else pg_done
        props = todo.projection(t, done=final)
        digest = content_hash(props)
        if page is None:
            write = "create"
        elif digest != t["content_hash"]:  # the hash covers Done, so a merged change lands
            write = "update"
        else:
            write = "skip"
        steps.append(Step(t["id"], merge, write, page["id"] if page else None, props, digest,
                          final))
    return steps


def _database_id(conn: Any) -> str:
    row = conn.execute(
        "SELECT notion_page_id FROM notion_sync WHERE entity_table = 'notion_schema' "
        "AND entity_id = %s", (todo.SCHEMA_KEY,)).fetchone()
    if row is None:
        raise RuntimeError("notion_sync has no todo_database; run cli.notion_setup first")
    return row[0]


def _pages(client: Any, database_id: str) -> tuple[dict[str, dict[str, Any]], str]:
    """Live To-Do rows by page id, and the database's data source id."""
    sources = client.retrieve_database(database_id).get("data_sources") or []
    if not sources:
        raise RuntimeError("the To-Do database has no data source")
    return {p["id"]: p for p in client.iter_query(sources[0]["id"])
            if not p.get("in_trash") and not p.get("archived")}, sources[0]["id"]


def _load(connection: Any, client: Any) -> tuple[list[dict[str, Any]], dict, str]:
    with connection() as conn:
        database_id = _database_id(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_TASKS_SQL, (todo.SYNC_TABLE,))
            tasks = cur.fetchall()
    pages, data_source_id = _pages(client, database_id)
    return tasks, pages, data_source_id


def run_once(client: Any, *, connection: Any, complete: Any, reopen: Any) -> str:
    """One sync cycle. `connection` is `db.connection`; `complete` and `reopen`
    are the checklist's guarded writes (they also move the follow-up)."""
    tasks, pages, data_source_id = _load(connection, client)
    counts = {"pulled": 0, "created": 0, "updated": 0}
    for step in plan(tasks, pages):
        if step.done == "pull":
            (complete if step.final_done else reopen)(step.task_id)
            counts["pulled"] += 1
        page_id = step.page_id
        if step.write == "create":
            page_id = client.create_page(
                {"type": "data_source_id", "data_source_id": data_source_id}, step.props)["id"]
            counts["created"] += 1
        elif step.write == "update":
            client.update_page(page_id, step.props)
            counts["updated"] += 1
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                         synced_done, last_pushed_at, last_pulled_at)
                VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, now())
                ON CONFLICT (entity_table, entity_id) DO UPDATE SET
                    notion_page_id = EXCLUDED.notion_page_id,
                    content_hash = EXCLUDED.content_hash,
                    synced_done = EXCLUDED.synced_done,
                    last_pushed_at = COALESCE(EXCLUDED.last_pushed_at, notion_sync.last_pushed_at),
                    last_pulled_at = now()
                """,
                (todo.SYNC_TABLE, str(step.task_id), page_id, step.digest, step.final_done,
                 step.write != "skip"),
            )
    mapped = {t["notion_page_id"] for t in tasks}
    unmatched = sum(1 for pid in pages if pid not in mapped)
    return (f"OK    {len(tasks)} tasks: {counts['created']} rows created, "
            f"{counts['updated']} updated, {counts['pulled']} Done change(s) taken from Notion; "
            f"{unmatched} row(s) in Notion with no task (left alone)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync tasks with the Notion To-Do database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what each task would do; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    args = parser.parse_args()

    from agents._lib import db, tasks_checklist
    from agents._lib.notion_client import NotionClient
    from agents.notion.ledger import notion_run

    try:
        client = NotionClient.from_keychain()
        if args.dry_run:
            tasks, pages, _ = _load(db.connection, client)
            for step in plan(tasks, pages):
                print(f"task #{step.task_id}: Done {step.done} → {step.final_done}; "
                      f"row {step.write}")
            return 0
        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection,
                           complete=tasks_checklist.complete_task,
                           reopen=tasks_checklist.reopen_task))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

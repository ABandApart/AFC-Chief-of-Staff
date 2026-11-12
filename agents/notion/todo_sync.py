"""Sync tasks with the Notion To-Do database (`PRD-notion-integration.md`, N3, N-S17).

    uv run python -m agents.notion.todo_sync             # one cycle (the loop runs this)
    uv run python -m agents.notion.todo_sync --dry-run   # print the plan; write nothing

**Notion is the source of truth for a row's content** (N-S17): Task, Due,
Initiative, and Done. Each cycle, for every open, completed, or skipped task
(`skipped` is a daily-plan task the plan replaced, N7; it shows as not Done, and
ticking it in Notion completes it):

1. **Merge.** Each of those fields is compared, on its own, between Postgres,
   Notion, and the value both last agreed on (`notion_sync.synced_fields`). An
   edit in Notion wins and is copied into Postgres. A change made in Discord
   (`/initiative`, the #tasks Done button) reaches Notion only if that field
   wasn't edited in Notion since the last sync (`todo_schema.merge_field`). A
   Done change from Notion completes or reopens the task, and its follow-up,
   through the same functions the #tasks checklist uses.
2. **Write.** Only the fields Discord won are written, plus Source, Created, and
   CoS id when they changed. A task with no row, or whose row was deleted in
   Notion, gets a new row.

A pulled Initiative must name one initiative the OKR pull knows; otherwise that
field is held (left alone on both sides) until it does. A blank Notion title is
held the same way, because a task must have a title.

**Rows added by hand in Notion become tasks.** A row with no CoS id and a title
is imported as an open task (completed if Done is ticked) with its follow-up, and
the CoS writes its Source ("notion"), Created, and CoS id back to the row.

Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from psycopg.rows import dict_row

from agents._lib.notion_client import NotionError, content_hash
from agents.notion import todo_schema as todo

FUNCTION_LABEL = "todo_sync"
# The owner Task Tinder gives accepted tasks (cogs/task_tinder.py); not imported
# from there, so this job doesn't load discord.py.
DEFAULT_OWNER = "operator"

_TASKS_SQL = """
    SELECT t.id, t.title, t.status, t.due_date, t.created_at, tc.source_type,
           t.okr_initiative_id, t.source_candidate_id IS NULL AS imported,
           CASE WHEN i.archived_at IS NULL THEN i.notion_page_id END AS initiative_page_id,
           ns.notion_page_id, ns.content_hash, ns.synced_done, ns.synced_fields,
           pi.day AS planned_for, pi.minutes AS plan_minutes, pi.context AS plan_context
    FROM tasks t
    LEFT JOIN task_candidates tc ON tc.id = t.source_candidate_id
    LEFT JOIN plan_items pi ON pi.task_id = t.id
    LEFT JOIN okr_initiatives i ON i.id = t.okr_initiative_id
    LEFT JOIN notion_sync ns ON ns.entity_table = %s AND ns.entity_id = t.id::text
    WHERE t.status IN ('open', 'completed', 'skipped')
    ORDER BY t.id
"""

_INITIATIVES_SQL = "SELECT notion_page_id, id FROM okr_initiatives WHERE archived_at IS NULL"


@dataclass
class Step:
    """What one cycle does for one task (pure data, so a dry run can print it)."""

    task_id: int
    write: str  # create | update | skip
    page_id: str | None
    props: dict[str, Any]
    digest: str  # hash of the CoS-written facts (Source, Created, CoS id)
    agreed: dict[str, Any]  # the values both sides hold after this cycle
    decisions: dict[str, str] = field(default_factory=dict)  # field → push|pull|agree|hold
    pulls: dict[str, Any] = field(default_factory=dict)  # field → Notion value to copy


def _agreed(task: dict[str, Any]) -> dict[str, Any]:
    agreed = dict(task.get("synced_fields") or {})
    if "done" not in agreed and task.get("synced_done") is not None:
        agreed["done"] = task["synced_done"]  # the 0034 baseline, before 0036
    return agreed


def _hold(name: str, value: Any, initiatives: dict[str, int]) -> bool:
    """A Notion value Postgres can't take yet."""
    if name == "initiative":
        return len(value) > 1 or any(v not in initiatives for v in value)
    return name == "title" and not str(value).strip()


def plan(tasks: list[dict[str, Any]], pages: dict[str, dict[str, Any]],
         initiatives: dict[str, int]) -> list[Step]:
    """Decide each task's per-field merge and row write (pure)."""
    steps = []
    for t in tasks:
        page = pages.get(t["notion_page_id"]) if t["notion_page_id"] else None
        pv = todo.pg_values(t, done=t["status"] == "completed")
        facts = todo.fact_props(t)
        digest = content_hash(facts)
        if page is None:
            steps.append(Step(t["id"], "create", None, todo.projection(t, done=pv["done"]),
                              digest, pv))
            continue
        nv, before = todo.notion_values(page), _agreed(t)
        step = Step(t["id"], "skip", page["id"], {}, digest, dict(before))
        pushes = []
        for name in todo.owned_fields(t):
            decision = todo.merge_field(pv[name], nv[name], before.get(name),
                                        has_agreed=name in before)
            if decision == "pull" and name == "title" and todo.replaces_neutral_title(t, before):
                decision = "push"
            if decision == "pull" and _hold(name, nv[name], initiatives):
                decision = "hold"
            step.decisions[name] = decision
            if decision == "pull":
                step.pulls[name] = nv[name]
                step.agreed[name] = nv[name]
            elif decision == "push":
                pushes.append(name)
                step.agreed[name] = pv[name]
            elif decision == "agree":
                step.agreed[name] = pv[name]
        step.props = todo.field_props(pv, pushes)
        if digest != t["content_hash"]:
            step.props.update(facts)
        if step.props:
            step.write = "update"
        steps.append(step)
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


def _load(connection: Any, client: Any) -> tuple[list[dict[str, Any]], dict, str, dict]:
    with connection() as conn:
        database_id = _database_id(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_TASKS_SQL, (todo.SYNC_TABLE,))
            tasks = cur.fetchall()
        initiatives = dict(conn.execute(_INITIATIVES_SQL).fetchall())
    pages, data_source_id = _pages(client, database_id)
    return tasks, pages, data_source_id, initiatives


def new_rows(tasks: list[dict[str, Any]], pages: dict[str, dict[str, Any]]) -> list[dict]:
    """Rows added by hand in Notion: not mapped to a task, and no CoS id (pure)."""
    mapped = {t["notion_page_id"] for t in tasks}
    return [p for pid, p in pages.items() if pid not in mapped
            and ((p.get("properties") or {}).get("CoS id") or {}).get("number") is None]


def _record(conn: Any, task_id: int, page_id: str, digest: str, agreed: dict[str, Any],
            pushed: bool) -> None:
    conn.execute(
        """
        INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                 synced_done, synced_fields, last_pushed_at, last_pulled_at)
        VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, now())
        ON CONFLICT (entity_table, entity_id) DO UPDATE SET
            notion_page_id = EXCLUDED.notion_page_id,
            content_hash = EXCLUDED.content_hash,
            synced_done = EXCLUDED.synced_done,
            synced_fields = EXCLUDED.synced_fields,
            last_pushed_at = COALESCE(EXCLUDED.last_pushed_at, notion_sync.last_pushed_at),
            last_pulled_at = now()
        """,
        (todo.SYNC_TABLE, str(task_id), page_id, digest, agreed.get("done"),
         json.dumps(agreed), pushed),
    )


_PULL_SQL = {
    "title": "UPDATE tasks SET title = %s WHERE id = %s",
    "due": "UPDATE tasks SET due_date = %s WHERE id = %s",
    "initiative": "UPDATE tasks SET okr_initiative_id = %s WHERE id = %s",
}


def _import(connection: Any, client: Any, page: dict[str, Any], initiatives: dict[str, int],
            owner: str, create: Any) -> int:
    """Create a task (and its follow-up) from a row added in Notion; returns its id.
    `create` is `task_tinder.create_operator_task`."""
    nv = todo.notion_values(page)
    rel = nv["initiative"]
    initiative = initiatives.get(rel[0]) if len(rel) == 1 else None
    made = create(title=nv["title"], owner=owner, due_date=nv["due"],
                  okr_initiative_id=initiative, completed=nv["done"])
    task = {"id": made["task_id"], "created_at": made["created_at"], "source_type": None,
            "imported": True, "initiative_page_id": rel[0] if initiative else None}
    facts = todo.fact_props(task)
    client.update_page(page["id"], facts)
    # Agreed = what Postgres now holds. An initiative Postgres couldn't take stays
    # unagreed, so the next cycle holds it rather than clearing it in Notion.
    agreed = {**nv, "initiative": task_initiative(task)}
    with connection() as conn:
        _record(conn, task["id"], page["id"], content_hash(facts), agreed, True)
    return task["id"]


def task_initiative(task: dict[str, Any]) -> list[str]:
    return [task["initiative_page_id"]] if task.get("initiative_page_id") else []


def run_once(client: Any, *, connection: Any, complete: Any, reopen: Any, create: Any,
             owner: str = DEFAULT_OWNER) -> str:
    """One sync cycle. `connection` is `db.connection`; `complete` and `reopen`
    are the checklist's guarded writes (they also move the follow-up); `create` is
    `task_tinder.create_operator_task`, for rows added in Notion."""
    tasks, pages, data_source_id, initiatives = _load(connection, client)
    counts = {"pulled": 0, "created": 0, "updated": 0, "held": 0, "imported": 0}
    for step in plan(tasks, pages, initiatives):
        for name, value in step.pulls.items():
            if name == "done":
                (complete if value else reopen)(step.task_id)
            else:
                if name == "initiative":
                    value = initiatives[value[0]] if value else None
                with connection() as conn:
                    conn.execute(_PULL_SQL[name], (value, step.task_id))
            counts["pulled"] += 1
        counts["held"] += sum(d == "hold" for d in step.decisions.values())
        page_id = step.page_id
        if step.write == "create":
            page_id = client.create_page(
                {"type": "data_source_id", "data_source_id": data_source_id}, step.props)["id"]
            counts["created"] += 1
        elif step.write == "update":
            client.update_page(page_id, step.props)
            counts["updated"] += 1
        with connection() as conn:
            _record(conn, step.task_id, page_id, step.digest, step.agreed,
                    step.write != "skip")
    fresh = new_rows(tasks, pages)
    blank = 0
    for page in fresh:
        if not todo.notion_values(page)["title"].strip():
            blank += 1  # Notion makes empty rows easily; import one once it has a title
            continue
        _import(connection, client, page, initiatives, owner, create)
        counts["imported"] += 1
    mapped = {t["notion_page_id"] for t in tasks}
    unmatched = sum(1 for pid in pages if pid not in mapped) - len(fresh)
    return (f"OK    {len(tasks)} tasks: {counts['created']} rows created, "
            f"{counts['updated']} updated, {counts['pulled']} field(s) taken from Notion, "
            f"{counts['held']} held; {counts['imported']} row(s) imported as tasks, "
            f"{blank} blank row(s) waiting for a title; "
            f"{unmatched} row(s) in Notion with an unknown CoS id (left alone)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync tasks with the Notion To-Do database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what each task would do; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    args = parser.parse_args()

    from agents._lib import db, task_tinder, tasks_checklist
    from agents._lib.notion_client import NotionClient
    from agents.notion.ledger import notion_run

    try:
        client = NotionClient.from_keychain()
        if args.dry_run:
            tasks, pages, _, initiatives = _load(db.connection, client)
            for step in plan(tasks, pages, initiatives):
                merged = ", ".join(f"{k} {v}" for k, v in step.decisions.items()
                                   if v != "agree") or "all agree"
                print(f"task #{step.task_id}: {merged}; row {step.write}")
            for page in new_rows(tasks, pages):
                print(f"new row {page['id']}: import "
                      f"{todo.notion_values(page)['title'][:60]!r}")
            return 0
        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection,
                           complete=tasks_checklist.complete_task,
                           reopen=tasks_checklist.reopen_task,
                           create=task_tinder.create_operator_task))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

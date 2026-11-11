"""The daily plan: each weekday's tasks, carry-forward, dashboard, and summary
(`PRD-notion-integration.md` N7, N-S18).

    uv run python -m agents.notion.plan_daily             # one run (the loop, Mon–Fri 06:00)
    uv run python -m agents.notion.plan_daily --dry-run   # print what it would do

A plan comes from `cli.notion_plan_import`. On a plan day, each run:

1. **Carries forward** every open plan task due before today. A standing item (the
   block that repeats every day) closes as `skipped`, with its follow-up, because
   today's copy replaces it (N-D14). Any other item's due date moves to today. A
   task whose Due the operator moved to a later date in Notion isn't due before
   today, so it's left alone.
2. **Creates today's tasks** through `task_tinder.create_plan_task`. Their
   follow-ups have no deadline (N-D15). The To-Do sync pushes them to Notion
   within 5 minutes; this job writes no To-Do rows itself.
3. **Posts the summary** to #tasks (N-D16), only on the run that created today's
   tasks, so a re-run doesn't post twice.
4. **Updates the dashboard.** It adds the "📋 Daily plan" heading and view to the
   quarter's dashboard page if they're missing, then sets the view's Due filter to
   the last five plan days, ending today.

On a day with no plan items (a weekend, an OFF day, or no active plan) it does
nothing. Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from agents._lib import kr_metrics
from agents.notion import dashboard_page
from agents.notion import dashboard_schema as dash
from agents.notion import todo_schema as todo

FUNCTION_LABEL = "plan_daily"
DEFAULT_OWNER = "operator"  # what Task Tinder gives accepted tasks
BUDGET_MIN = 180  # the operator's daily time budget (3 hours)
WINDOW_DAYS = 5  # today plus the previous four plan days
HEADING = "📋 Daily plan"
VIEW_NAME = "Daily plan"
PLAN_SOURCE = "plan"
CUT_MARK = "🔻"

_ACTIVE = "JOIN plans p ON p.id = pi.plan_id AND p.status = 'active'"

_DAYS_SQL = f"SELECT DISTINCT pi.day FROM plan_items pi {_ACTIVE} WHERE pi.day <= %s ORDER BY 1"

_CARRY_SQL = f"""
    SELECT t.id, pi.standing FROM tasks t
    JOIN plan_items pi ON pi.task_id = t.id {_ACTIVE}
    WHERE t.status = 'open' AND t.due_date < %s
"""

_TODAY_ITEMS_SQL = f"""
    SELECT pi.id, pi.title, pi.okr_initiative_id, pi.context
    FROM plan_items pi {_ACTIVE}
    WHERE pi.day = %s AND pi.task_id IS NULL
    ORDER BY pi.position
"""

_PRIOR_SQL = f"""
    SELECT count(*), count(*) FILTER (WHERE t.status = 'completed'),
           coalesce(sum(pi.minutes), 0),
           coalesce(sum(pi.minutes) FILTER (WHERE t.status = 'completed'), 0)
    FROM plan_items pi {_ACTIVE} JOIN tasks t ON t.id = pi.task_id
    WHERE pi.day = %s
"""

_TODAY_SQL = f"""
    SELECT count(*), coalesce(sum(pi.minutes), 0), count(*) FILTER (WHERE pi.cut_first)
    FROM plan_items pi {_ACTIVE} JOIN tasks t ON t.id = pi.task_id
    WHERE t.status = 'open' AND t.due_date = %s
"""


# --- pure ----------------------------------------------------------------------------------


def window(days: list[date], today: date) -> tuple[date, date]:
    """The dashboard view's Due range: the last WINDOW_DAYS plan days up to today."""
    upto = [d for d in sorted(days) if d <= today][-WINDOW_DAYS:]
    return (upto[0] if upto else today), today


def prior_day(days: list[date], today: date) -> date | None:
    """The latest plan day before today: Friday on a Monday, Wednesday after an OFF Thursday."""
    earlier = [d for d in days if d < today]
    return max(earlier) if earlier else None


def _label(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


@dataclass
class Prior:
    total: int
    done: int
    minutes: int
    minutes_done: int


@dataclass
class Today:
    tasks: int
    minutes: int
    cut_first: int


def summary(today: date, now: Today, prior: date | None, before: Prior | None,
            carried: int, link: str | None) -> str:
    """The #tasks post: the prior plan day, today, and the dashboard link."""
    lines = []
    if prior and before and before.total:
        lines.append(f"{_label(prior)}: {before.done} of {before.total} done · "
                     f"{before.minutes_done} of {before.minutes} min · "
                     f"{carried} carried to today")
    line = (f"Today, {_label(today)}: {_n(now.tasks, 'task')} · {now.minutes} min "
            f"(budget {BUDGET_MIN})")
    if now.cut_first:
        line += f" · {now.cut_first} marked {CUT_MARK}"
    lines.append(line)
    if link:
        lines.append(f"Dashboard: <{link}>")
    return "\n".join(lines)


def view_filter(prop_ids: dict[str, str], start: date, end: date) -> dict[str, Any]:
    """Plan rows due in [start, end]."""
    due = prop_ids.get("Due", "Due")
    return {"and": [
        {"property": prop_ids.get("Source", "Source"), "select": {"equals": PLAN_SOURCE}},
        {"property": due, "date": {"on_or_after": start.isoformat()}},
        {"property": due, "date": {"on_or_before": end.isoformat()}},
    ]}


def page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


# --- database ---------------------------------------------------------------------------------


def plan_days(conn: Any, today: date) -> list[date]:
    return [r[0] for r in conn.execute(_DAYS_SQL, (today,)).fetchall()]


def carry_forward(conn: Any, today: date) -> tuple[int, int]:
    """Skip unfinished standing tasks and move the rest to today. Returns (moved, skipped)."""
    rows = conn.execute(_CARRY_SQL, (today,)).fetchall()
    standing = [tid for tid, s in rows if s]
    others = [tid for tid, s in rows if not s]
    skipped = moved = 0
    with conn.transaction():
        if standing:
            skipped = len(conn.execute(
                """
                WITH t AS (UPDATE tasks SET status = 'skipped'
                           WHERE id = ANY(%s) AND status = 'open' RETURNING id, follow_up_id),
                f AS (UPDATE follow_ups SET status = 'skipped'
                      WHERE id IN (SELECT follow_up_id FROM t) AND status = 'open')
                SELECT id FROM t
                """, (standing,)).fetchall())
        if others:
            moved = len(conn.execute(
                "UPDATE tasks SET due_date = %s WHERE id = ANY(%s) AND status = 'open' "
                "AND due_date < %s RETURNING id", (today, others, today)).fetchall())
    return moved, skipped


def todays_items(conn: Any, today: date) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_TODAY_ITEMS_SQL, (today,))
        return cur.fetchall()


def stats(conn: Any, today: date, prior: date | None) -> tuple[Today, Prior | None]:
    now = Today(*conn.execute(_TODAY_SQL, (today,)).fetchone())
    before = Prior(*conn.execute(_PRIOR_SQL, (prior,)).fetchone()) if prior else None
    return now, before


# --- Notion: the dashboard section ------------------------------------------------------------


def _todo_source(client: Any, conn: Any) -> str:
    row = conn.execute("SELECT notion_page_id FROM notion_sync WHERE entity_table = "
                       "'notion_schema' AND entity_id = %s", (todo.SCHEMA_KEY,)).fetchone()
    if row is None:
        raise RuntimeError("notion_sync has no todo_database; run cli.notion_setup first")
    sources = client.retrieve_database(row[0]).get("data_sources") or []
    if not sources:
        raise RuntimeError("the To-Do database has no data source")
    return sources[0]["id"]


def ensure_section(client: Any, *, page_id: str, attention_id: str, todo_source: str,
                   prop_ids: dict[str, str], start: date, end: date,
                   lookup: Any, record: Any, say: Any) -> str:
    """Add the heading and the view under the attention callout if missing, then set
    the view's Due window. Returns the view id."""
    heading_id = lookup("plan:heading")
    if not heading_id:
        made = client.append_blocks(page_id, [dash.heading(HEADING)], after=attention_id)
        heading_id = made[0]["id"]
        record("plan:heading", heading_id)
        say(f"NEW   added the {HEADING!r} heading to the dashboard")
    view_id = lookup("plan:view")
    flt = view_filter(prop_ids, start, end)
    if not view_id:
        view = client.create_view({
            "data_source_id": todo_source, "name": VIEW_NAME, "type": "table",
            "filter": flt, "sorts": [{"property": "Due", "direction": "descending"}],
            "configuration": {"type": "table"},
            "create_database": {"parent": {"type": "page_id", "page_id": page_id},
                                "position": {"type": "after_block", "block_id": heading_id},
                                "show_data_source_title": False}})
        view_id = view["id"]
        record("plan:view", view_id)
        database_id = (view.get("parent") or {}).get("database_id")
        if database_id:
            record("plan:db", database_id)
        say(f"NEW   added the {VIEW_NAME!r} view to the dashboard")
    else:
        client.update_view(view_id, {"filter": flt})
    saved = client.retrieve_view(view_id)
    if not saved.get("filter"):
        say(f"WARN  view {VIEW_NAME!r} has no filter; set Source = plan and Due "
            f"{start} to {end} on it by hand")
    return view_id


def dashboard(client: Any, connection: Any, today: date, days: list[date], say: Any) -> str:
    quarter = kr_metrics.quarter_of(today)
    lookup, record = dashboard_page.recorder(connection, quarter)
    page_id, attention_id = lookup("page"), lookup("attention")
    if not page_id or not attention_id:
        return f"no {quarter} dashboard page yet (the Monday push builds it); view not set"
    with connection() as conn:
        source = _todo_source(client, conn)
    props = client.retrieve_data_source(source).get("properties") or {}
    prop_ids = {name: p.get("id", name) for name, p in props.items()}
    start, end = window(days, today)
    ensure_section(client, page_id=page_id, attention_id=attention_id, todo_source=source,
                   prop_ids=prop_ids, start=start, end=end, lookup=lookup, record=record,
                   say=say)
    return f"dashboard view set to {start} to {end}"


# --- one run ------------------------------------------------------------------------------------


def run_once(client: Any, *, connection: Any, create: Any, today: date, post: Any,
             say: Any = print, owner: str = DEFAULT_OWNER) -> str:
    """One morning run. `create` is `task_tinder.create_plan_task`; `post` sends the
    #tasks summary and returns True or False."""
    with connection() as conn:
        days = plan_days(conn, today)
        if today not in days:
            return f"OK    {today}: not a plan day; nothing to do"
        moved, skipped = carry_forward(conn, today)
        items = todays_items(conn, today)
    created = 0
    for i in items:
        if create(plan_item_id=i["id"], title=i["title"], owner=owner, due_date=today,
                  okr_initiative_id=i["okr_initiative_id"], description=i["context"]):
            created += 1
    prior = prior_day(days, today)
    with connection() as conn:
        now, before = stats(conn, today, prior)
    page_id = dashboard_page.recorder(connection, kr_metrics.quarter_of(today))[0]("page")
    posted = "not posted (today's tasks already existed)"
    if created:
        sent = post(summary(today, now, prior, before, moved,
                            page_url(page_id) if page_id else None))
        posted = "posted to #tasks" if sent else "post to #tasks FAILED"
    view = dashboard(client, connection, today, days, say)
    return (f"OK    {today}: {created} task(s) created, {moved} carried forward, "
            f"{skipped} standing task(s) skipped; {now.tasks} open today "
            f"({now.minutes} min); summary {posted}; {view}")


def _post(text: str) -> bool:
    from agents._lib import discord_rest
    from agents.discord_bot.config import TASKS_CHANNEL_ID

    return discord_rest.post(TASKS_CHANNEL_ID, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily plan for today.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what today's run would do; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="act as if today were this date (testing only)")
    args = parser.parse_args()
    today = args.date or date.today()

    from agents._lib import db, task_tinder
    from agents._lib.notion_client import NotionClient, NotionError
    from agents.notion.ledger import notion_run

    try:
        if args.dry_run:
            with db.connection() as conn:
                days = plan_days(conn, today)
                if today not in days:
                    print(f"OK    {today}: not a plan day; nothing to do")
                    return 0
                carry = conn.execute(_CARRY_SQL, (today,)).fetchall()
                items = todays_items(conn, today)
            standing = sum(1 for _, s in carry if s)
            print(f"PLAN  carry forward {len(carry) - standing} task(s); "
                  f"skip {standing} standing task(s)")
            for i in items:
                print(f"PLAN  create {i['title'][:70]!r}")
            start, end = window(days, today)
            print(f"PLAN  dashboard view: Due {start} to {end}; prior plan day "
                  f"{prior_day(days, today)}")
            return 0
        client = NotionClient.from_keychain()
        with notion_run(FUNCTION_LABEL, trigger_kind="manual" if args.manual else "scheduled"):
            print(run_once(client, connection=db.connection,
                           create=task_tinder.create_plan_task, today=today, post=_post))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

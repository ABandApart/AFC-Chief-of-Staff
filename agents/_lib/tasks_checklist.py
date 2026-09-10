"""#tasks pinned-checklist core (Track O, `PRD-tasktinder-refinements.md` Increment 3).

The list, the check/uncheck writes, and the pure line formatting for the
bot-maintained pinned checklist in `#tasks`. Discord-free by design (like the
other `_lib` cores): the query and the guarded writes live here and are
unit-tested without a bot or a live channel; the cog owns the pinned message and
the Components-v2 view.

**Separation invariant.** The checklist reads `tasks`, and the *only* writer of
`tasks` rows is `agents/_lib/task_tinder.promote` (content-accepted work). No
outreach path inserts `tasks` (outreach state lives in `outreach_touches` /
`outreach_targets`). So `#tasks` cannot leak outreach items by construction —
pinned by `tests/test_tasks_checklist_separation.py`.

**Operator decisions (2026-09-10):** a completed task shows struck-through for
`ARCHIVE_AFTER`, then drops off the list ("archive"); check-off is reversible
(uncheck reopens); the list is ordered most-escalated then soonest-due.
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from agents._lib import db

# A completed task stays on the list struck-through for this window, then archives
# out (drops on the next rebuild). Reversible while shown: uncheck reopens it.
ARCHIVE_AFTER = "24 hours"

# Cap the rendered rows to Discord's Components-v2 budget: a single message holds
# at most 40 components, and each task costs 3 (Section + its TextDisplay + the
# accessory Button) on top of the container + header + separator overhead — so 12
# task sections is the empirical maximum (13 raises "maximum number of children
# exceeded (40)"). Open tasks sort first, so the cap only ever hides the
# lowest-priority open rows; the header notes how many are not shown, and checking
# some off surfaces the rest on the next poll. A queue that routinely exceeds 12
# wants pagination across multiple pinned messages (PRD Increment 3 open decision).
MAX_ROWS = 12

_COLS = "t.id, t.title, t.status, t.due_date, t.completed_at, fu.escalation_level"


def list_checklist(limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Rows for the pinned checklist: open tasks + tasks completed within
    `ARCHIVE_AFTER`, ordered for display.

    Open tasks first (most-escalated, then soonest-due); recently-completed after,
    newest-completed first (shown struck-through until they archive out). Joined
    to `follow_ups` for the escalation level. Capped at `limit` — open-first
    ordering means the cap only ever trims completed or the longest tail.
    """
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {_COLS} FROM tasks t "
            "LEFT JOIN follow_ups fu ON fu.id = t.follow_up_id "
            "WHERE t.status = 'open' "
            "   OR (t.status = 'completed' AND t.completed_at >= now() - %s::interval) "
            "ORDER BY (t.status = 'open') DESC, "
            "         fu.escalation_level DESC NULLS LAST, "
            "         t.due_date ASC NULLS LAST, "
            "         t.completed_at DESC NULLS LAST, "
            "         t.created_at "
            "LIMIT %s",
            (ARCHIVE_AFTER, limit),
        )
        return cur.fetchall()


def count_open() -> int:
    """Open-task count (for the header, and to flag rows hidden by the cap)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM tasks WHERE status = 'open'")
        return cur.fetchone()[0]


def complete_task(task_id: int) -> int | None:
    """Idempotently mark an open task complete. Returns the id, or None if it was
    not open (the double-click no-op — the `WHERE status='open'` gate is the
    authority, mirroring the Task Tinder decide/promote pattern)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = now() "
            "WHERE id = %s AND status = 'open' RETURNING id",
            (task_id,),
        )
        row = cur.fetchone()
    return None if row is None else row[0]


def reopen_task(task_id: int) -> int | None:
    """Idempotently reopen a completed task (the uncheck / undo). Returns the id,
    or None if it was not completed. Clears `completed_at` so it re-sorts as open
    and leaves the recently-completed window."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tasks SET status = 'open', completed_at = NULL "
            "WHERE id = %s AND status = 'completed' RETURNING id",
            (task_id,),
        )
        row = cur.fetchone()
    return None if row is None else row[0]


# --- pure formatting (unit-tested; the cog wraps these in Components-v2) -------

def is_done(row: dict[str, Any]) -> bool:
    return row.get("status") == "completed"


def format_line(row: dict[str, Any]) -> str:
    """One checklist line's text (the Section's TextDisplay).

    Completed: `☑ ~~title~~` (struck-through). Open: `☐ title` with a trailing
    `· due <date>` / `· esc <n>` when present. Never raises on a missing title.
    """
    title = str(row.get("title") or f"task #{row['id']}")
    if is_done(row):
        return f"☑ ~~{title}~~"
    meta = []
    if row.get("due_date"):
        meta.append(f"due {row['due_date']}")
    if row.get("escalation_level"):
        meta.append(f"esc {row['escalation_level']}")
    return f"☐ {title}" + (f"  · {' · '.join(meta)}" if meta else "")

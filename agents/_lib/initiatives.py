"""Initiatives on Task Tinder cards and tasks (`PRD-notion-integration.md`, N3).

The *list* of initiatives is defined only in Notion and cached in
`okr_initiatives` by the OKR pull. The *assignment* of a card or task to one is
made only in Discord, with `/initiative` or `/todo initiative:` (N-S13). This
module holds the pure pieces (labels, type-ahead choices, input parsing) and the
guarded writes; the `initiatives` cog owns the slash commands.

An initiative is **assignable** when it is Active, not archived, and well formed
(`problem IS NULL`). A task keeps its link when the initiative is later paused,
finished, or removed in Notion; its label then says so until it is reassigned.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.rows import dict_row

from agents._lib import db

MAX_CHOICES = 25  # Discord's autocomplete limit
MAX_CHOICE_CHARS = 100  # Discord's limit on a choice's name and value
UNASSIGNED = "Unassigned"
NONE_VALUE = "none"  # the type-ahead value that clears an assignment

# Columns that label an initiative, for any query that LEFT JOINs
# `okr_initiatives i`. `label()` reads them back.
LABEL_COLS = ("i.name AS initiative_name, i.status AS initiative_status, "
              "(i.archived_at IS NOT NULL OR i.problem IS NOT NULL) AS initiative_gone")

_ASSIGNABLE = "i.status = 'Active' AND i.archived_at IS NULL AND i.problem IS NULL"


# --- pure ----------------------------------------------------------------------------

def label(row: dict[str, Any]) -> str:
    """The initiative's display label for a card or checklist line, or "Unassigned"."""
    name = row.get("initiative_name")
    if not name:
        return UNASSIGNED
    if row.get("initiative_gone"):
        return f"{name} (retired)"
    status = row.get("initiative_status")
    if status == "Paused":
        return f"{name} (paused)"
    if status == "Done":
        return f"{name} (done)"
    return name


def _clip(text: str) -> str:
    return text if len(text) <= MAX_CHOICE_CHARS else text[:MAX_CHOICE_CHARS - 1] + "…"


def initiative_choices(term: str, rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(name, value) pairs for the `initiative` type-ahead: "Unassigned" first, then
    each assignable initiative as "Initiative · KR" whose text contains `term`."""
    typed = (term or "").strip().lower()
    out = [(UNASSIGNED, NONE_VALUE)] if not typed or typed in UNASSIGNED.lower() else []
    for r in rows:
        text = f"{r['name']} · {r['key_result']}"
        if typed in text.lower():
            out.append((_clip(text), str(r["id"])))
    return out[:MAX_CHOICES]


def item_choices(term: str, items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(name, value) pairs for the `item` type-ahead.

    `items` are pending cards (`kind='card'`) and open tasks (`kind='task'`),
    newest first. Values are `c:<id>` and `t:<id>`, so a card and a task with the
    same number stay distinct.
    """
    typed = (term or "").strip().lower().lstrip("#")
    out = []
    for it in items:
        noun = "Card" if it["kind"] == "card" else "Task"
        text = f"{noun} #{it['id']} · {it['title']}"
        if typed in text.lower():
            out.append((_clip(text), f"{noun[0].lower()}:{it['id']}"))
    return out[:MAX_CHOICES]


def parse_item(value: str) -> tuple[str, int]:
    """`c:12` → ("card", 12); `t:5` → ("task", 5). Text not picked from the list → ValueError."""
    kind, _, num = (value or "").partition(":")
    if kind not in ("c", "t") or not num.isdigit():
        raise ValueError("pick a card or task from the list")
    return ("card" if kind == "c" else "task", int(num))


def parse_initiative(value: str | None) -> int | None:
    """The type-ahead value → an initiative id, or None for Unassigned."""
    if value is None or value == NONE_VALUE:
        return None
    if not value.isdigit():
        raise ValueError("pick an initiative from the list")
    return int(value)


def parse_due(text: str | None) -> date | None:
    """`YYYY-MM-DD` → date; blank → None; anything else → ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as e:
        raise ValueError(f"'{text}' isn't a date — use YYYY-MM-DD, e.g. 2026-11-15") from e


# --- reads ---------------------------------------------------------------------------

def assignable() -> list[dict[str, Any]]:
    """Assignable initiatives with their KR name, ordered by KR then initiative."""
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT i.id, i.name, k.name AS key_result FROM okr_initiatives i "
            "JOIN okr_key_results k ON k.id = i.key_result_id "
            f"WHERE {_ASSIGNABLE} ORDER BY k.name, i.name"
        )
        return cur.fetchall()


def open_items(limit: int = 200) -> list[dict[str, Any]]:
    """Pending Task Tinder cards and open tasks, newest first."""
    with db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM ("
            "  SELECT 'card' AS kind, id, proposed_action AS title, created_at "
            "  FROM task_candidates WHERE status = 'pending' "
            "  AND source_type <> 'outreach_stale_signal' "
            "  UNION ALL "
            "  SELECT 'task' AS kind, id, title, created_at FROM tasks WHERE status = 'open'"
            ") x ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


# --- writes (guarded) ----------------------------------------------------------------

def _check_assignable(cur: Any, initiative_id: int | None) -> None:
    if initiative_id is None:
        return
    cur.execute(f"SELECT 1 FROM okr_initiatives i WHERE i.id = %s AND {_ASSIGNABLE}",
                (initiative_id,))
    if cur.fetchone() is None:
        raise ValueError("that initiative isn't active in Notion any more; pick another")


def assign(kind: str, item_id: int, initiative_id: int | None) -> bool:
    """Set or clear the initiative on a pending card or an open task.

    Returns False when the item is no longer pending or open (decided or done
    since the type-ahead was shown). Raises ValueError for an unassignable initiative.
    """
    table, open_status = (("task_candidates", "pending") if kind == "card"
                          else ("tasks", "open"))
    with db.connection() as conn, conn.transaction(), conn.cursor() as cur:
        _check_assignable(cur, initiative_id)
        cur.execute(
            f"UPDATE {table} SET okr_initiative_id = %s "  # noqa: S608 — fixed table names
            "WHERE id = %s AND status = %s RETURNING id",
            (initiative_id, item_id, open_status),
        )
        return cur.fetchone() is not None


def add_todo(text: str, initiative_id: int | None, due: date | None) -> int:
    """Write a `/todo` as a pending card (`source_type='operator'`, confidence 1.0).

    It goes through Task Tinder like any card (N-D10): accepting it is what makes
    it a task.
    """
    with db.connection() as conn, conn.transaction(), conn.cursor() as cur:
        _check_assignable(cur, initiative_id)
        cur.execute(
            "INSERT INTO task_candidates (proposed_action, source_type, evidence_text, "
            "                             confidence, okr_initiative_id, due_date) "
            "VALUES (%s, 'operator', 'Added with /todo.', 1.0, %s, %s) RETURNING id",
            (text, initiative_id, due),
        )
        return cur.fetchone()[0]

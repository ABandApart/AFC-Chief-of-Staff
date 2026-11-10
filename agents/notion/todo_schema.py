"""Notion To-Do database: schema, row projection, and the Done merge (N3). Pure.

One row per task. **Notion is the source of truth for the row's content**
(N-S17, operator 2026-11-10): Task, Due, Initiative, and Done. The CoS writes a
new row in full, then merges each of those fields on its own: an edit in Notion
always wins, and a change made in Discord (`/initiative`, the #tasks Done button)
reaches Notion only if that field wasn't edited in Notion since the last sync.
The CoS still writes Source, Created, and CoS id. A row added by hand becomes a
task. The Initiative relation is one-way (`single_property`), so the master
Initiatives database gains no column.

**Neutral titles (N-D9).** A task from an outreach or inbound source names a
prospect, so its real title never reaches Notion: it pushes as a fixed phrase plus
the CoS id. The real text stays in Postgres and Discord.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agents._lib.notion_client import rich_text

DATABASE_TITLE = "To-Do"
SCHEMA_KEY = "todo_database"  # notion_sync key (entity_table notion_schema)
SYNC_TABLE = "tasks"  # notion_sync.entity_table for To-Do rows

# task_candidates.source_type → the To-Do Source option.
SOURCES = {"discovery": "discovery", "outreach_stale_signal": "outreach",
           "inbound_lead": "inbound", "operator": "operator"}
# A task imported from a row added by hand in Notion has no Task Tinder candidate.
IMPORTED_SOURCE = "notion"
NEUTRAL_TITLES = {"outreach_stale_signal": "Outreach follow-up · open in NocoDB",
                  "inbound_lead": "Inbound lead follow-up · open in Discord"}

DESCRIPTION = (
    "Your to-do list. Edit Task, Due, Initiative, and Done here: your edits win. "
    "Discord's /initiative and the #tasks Done button change a row only where you "
    "haven't edited it since the last sync. A row you add here becomes a task within "
    "5 minutes. Source, Created, and CoS id are written by the CoS."
)


def properties(initiatives_data_source_id: str) -> dict[str, Any]:
    """The To-Do schema at creation. The Key Result rollup is added afterwards
    (`ROLLUP`), because it needs the Initiative relation to exist first."""
    return {
        "Task": {"title": {}},
        "Initiative": {"relation": {"data_source_id": initiatives_data_source_id,
                                    "type": "single_property", "single_property": {}}},
        "Done": {"checkbox": {}},
        "Due": {"date": {}},
        "Source": {"select": {"options": [{"name": n, "color": "default"} for n in
                                          dict.fromkeys((*SOURCES.values(), IMPORTED_SOURCE))]}},
        "Created": {"date": {}},
        "CoS id": {"number": {"format": "number"}},
    }


ROLLUP = {"Key Result": {"rollup": {"relation_property_name": "Initiative",
                                    "rollup_property_name": "Key Result",
                                    "function": "show_original"}}}
REQUIRED = (*properties("x"), *ROLLUP)


def title_for(task: dict[str, Any]) -> str:
    """The row title: the task's own title, or a neutral one for prospect sources."""
    neutral = NEUTRAL_TITLES.get(task.get("source_type") or "")
    if neutral:
        return f"{neutral} · #{task['id']}"
    return str(task.get("title") or f"Task #{task['id']}")


def _date(value: date | datetime | None) -> dict[str, Any] | None:
    if value is None:
        return None
    day = value.date() if isinstance(value, datetime) else value
    return {"start": day.isoformat()}


def projection(task: dict[str, Any], *, done: bool) -> dict[str, Any]:
    """Every To-Do property, for a new row. After that, only the merge decides
    what is written (`merge_field`).

    `task` carries `id`, `title`, `source_type`, `due_date`, `created_at`, and
    `initiative_page_id` (None when unassigned or the initiative left Notion).
    """
    return {**field_props(pg_values(task, done=done), OWNED), **fact_props(task)}


# --- the per-field merge (N-S17) -------------------------------------------------------

# Fields Notion owns. Each is merged on its own against the value both sides last
# agreed on (`notion_sync.synced_fields`). Source, Created, and CoS id stay
# CoS-written facts.
OWNED = ("title", "due", "initiative", "done")
PROPERTY = {"title": "Task", "due": "Due", "initiative": "Initiative", "done": "Done"}


def owned_fields(task: dict[str, Any]) -> tuple[str, ...]:
    """The fields merged for this task. A prospect-source task's title is neither
    pushed nor pulled after the row is made: its Notion title is the neutral
    phrase, and the real title stays in Postgres (N-D9)."""
    if task.get("source_type") in NEUTRAL_TITLES:
        return tuple(f for f in OWNED if f != "title")
    return OWNED


def pg_values(task: dict[str, Any], *, done: bool) -> dict[str, Any]:
    """The task's owned values in the form `notion_values` reads them back."""
    due = task.get("due_date")
    page = task.get("initiative_page_id")
    return {"title": title_for(task),
            "due": (due.date() if isinstance(due, datetime) else due).isoformat() if due else None,
            "initiative": [page] if page else [],
            "done": done}


def notion_values(page: dict[str, Any]) -> dict[str, Any]:
    """The owned values of a live To-Do row."""
    props = page.get("properties") or {}
    title = "".join(t.get("plain_text") or (t.get("text") or {}).get("content", "")
                    for t in (props.get("Task") or {}).get("title") or [])
    due = ((props.get("Due") or {}).get("date") or {}).get("start")
    rel = [r["id"] for r in (props.get("Initiative") or {}).get("relation") or []]
    return {"title": title, "due": due[:10] if due else None, "initiative": rel,
            "done": bool((props.get("Done") or {}).get("checkbox"))}


def merge_field(pg: Any, notion: Any, agreed: Any, *, has_agreed: bool) -> str:
    """Which side's value to keep for one field (N-S17). Returns:

    - "agree": both sides already match;
    - "pull": take Notion's value. Notion changed since the last agreed value, or
      there is no agreed value yet. This includes both sides changing: Notion wins;
    - "push": take Postgres's value. Only Discord changed, so Discord fills the gap.
    """
    if pg == notion:
        return "agree"
    if not has_agreed or notion != agreed:
        return "pull"
    return "push"


def field_props(values: dict[str, Any], fields: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Notion properties for some owned fields."""
    out: dict[str, Any] = {}
    for f in fields:
        v = values[f]
        if f == "title":
            out["Task"] = {"title": rich_text(v)}
        elif f == "due":
            out["Due"] = {"date": {"start": v} if v else None}
        elif f == "initiative":
            out["Initiative"] = {"relation": [{"id": i} for i in v]}
        else:
            out["Done"] = {"checkbox": bool(v)}
    return out


def fact_props(task: dict[str, Any]) -> dict[str, Any]:
    """The CoS-written columns: Source, Created, and CoS id."""
    source = SOURCES.get(task.get("source_type") or "") or \
        (IMPORTED_SOURCE if task.get("imported") else None)
    return {"Source": {"select": {"name": source} if source else None},
            "Created": {"date": _date(task.get("created_at"))},
            "CoS id": {"number": task["id"]}}

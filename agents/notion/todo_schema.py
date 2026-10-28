"""Notion To-Do database: schema, row projection, and the Done merge (N3). Pure.

One row per task. The CoS writes every column except **Done**, which both sides
can change: the operator ticks it in Notion or checks the task off in Discord's
#tasks, and the latest change wins (N-S16). Initiative is display-only here;
assignment happens in Discord (N-S13). The relation is one-way
(`single_property`), so the master Initiatives database gains no column.

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
NEUTRAL_TITLES = {"outreach_stale_signal": "Outreach follow-up · open in NocoDB",
                  "inbound_lead": "Inbound lead follow-up · open in Discord"}

DESCRIPTION = (
    "The CoS keeps this list. Tick Done here or check the task off in Discord's "
    "#tasks: the latest change wins. Add to-dos with /todo and set initiatives with "
    "/initiative in Discord. Edits to the other columns are overwritten."
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
        "Source": {"select": {"options": [{"name": n, "color": "default"}
                                          for n in dict.fromkeys(SOURCES.values())]}},
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
    """Every To-Do property the CoS writes for one task.

    `task` carries `id`, `title`, `source_type`, `due_date`, `created_at`, and
    `initiative_page_id` (None when unassigned or the initiative left Notion).
    """
    source = SOURCES.get(task.get("source_type") or "")
    page = task.get("initiative_page_id")
    return {
        "Task": {"title": rich_text(title_for(task))},
        "Initiative": {"relation": [{"id": page}] if page else []},
        "Done": {"checkbox": done},
        "Due": {"date": _date(task.get("due_date"))},
        "Source": {"select": {"name": source} if source else None},
        "Created": {"date": _date(task.get("created_at"))},
        "CoS id": {"number": task["id"]},
    }


def merge_done(pg_done: bool, notion_done: bool | None, synced: bool | None) -> str:
    """Which side's Done to keep (N-S16, latest change wins). Returns:

    - "push": Postgres wins (a new row, or the task was checked or unchecked in
      Discord since the last sync);
    - "pull": Notion wins (Done was ticked or unticked in Notion since then);
    - "agree": both already match.

    With one boolean per side, the side that differs from the last agreed value
    is the side that changed. Both cannot have changed and still disagree. A side
    flipped and flipped back between two syncs counts as unchanged.
    """
    if notion_done is None:
        return "push"
    if pg_done == notion_done:
        return "agree"
    if synced is None:
        return "push"  # a row with no agreed value yet: Postgres is the record
    return "pull" if notion_done != synced else "push"

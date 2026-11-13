"""Task Tinder cards as rows in the Notion To-Do database (`PRD-notion-integration.md` N9 I1).

Run daily by `notion-inputs-sync` (`agents/notion/inputs_sync.py`), not on its own.

Every undecided candidate (`pending` or `deferred`, confidence at least
`task_tinder.MIN_CONFIDENCE`, excluding stale-signal re-checks and outreach touch
tasks) is a **card row**: a To-Do row with a Card id and no CoS id. The operator
sets its **Decision**:

- **Accept:** the row's Task, Due, and Initiative are taken into the candidate, and
  it is promoted through `task_tinder.decide` and `promote`, the Discord Accept
  path. The **same row** becomes the task: it gets its CoS id, Source, and Created,
  and a `notion_sync ('tasks', id)` mapping, so the 5-minute To-Do sync treats it as
  an existing task and creates no second row.
- **Decline:** the candidate is declined and the row is moved to the trash.
- **Defer:** the candidate is deferred until the row's Defer until (default today
  + 7). When that date passes, it returns to `pending` and Decision is cleared
  (N-D29).

A card decided elsewhere (Discord, during the dual-run) loses its row. Edits by
anyone but the operator or the CoS are refused (`operator.py`, N-D24).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row

from agents._lib.notion_client import content_hash, rich_text
from agents.notion import operator
from agents.notion import todo_schema as todo

SYNC_TABLE = "task_candidates"  # notion_sync.entity_table for card rows
EXCLUDED = ("outreach_stale_signal", "outreach_touch")

_CARDS_SQL = """
    SELECT tc.id, tc.proposed_action, tc.source_type, tc.evidence_text, tc.confidence,
           tc.status, tc.deferred_until, tc.due_date, tc.created_at,
           CASE WHEN i.archived_at IS NULL THEN i.notion_page_id END AS initiative_page_id,
           ns.notion_page_id, ns.content_hash
    FROM task_candidates tc
    LEFT JOIN okr_initiatives i ON i.id = tc.okr_initiative_id
    LEFT JOIN notion_sync ns ON ns.entity_table = %s AND ns.entity_id = tc.id::text
    WHERE tc.status IN ('pending', 'deferred') AND tc.confidence >= %s
      AND tc.source_type <> ALL(%s)
    ORDER BY tc.id
"""
_MAPPED_SQL = """
    SELECT ns.entity_id::bigint AS id, ns.notion_page_id, tc.status
    FROM notion_sync ns JOIN task_candidates tc ON tc.id = ns.entity_id::bigint
    WHERE ns.entity_table = %s
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return (value.date() if hasattr(value, "date") and callable(value.date) else value).isoformat()


def facts(card: dict[str, Any]) -> dict[str, Any]:
    """The CoS-written columns of a card row (pure). Hash-skipped when unchanged."""
    source = todo.SOURCES.get(card.get("source_type") or "")
    return {
        "Source": {"select": {"name": source} if source else None},
        "Created": {"date": {"start": _iso(card.get("created_at"))}
                    if card.get("created_at") else None},
        "Evidence": {"rich_text": rich_text((card.get("evidence_text") or "")[:2000])},
        "Confidence": {"number": card.get("confidence")},
        "Card id": {"number": card["id"]},
    }


def new_row(card: dict[str, Any]) -> dict[str, Any]:
    """Every property of a new card row (pure)."""
    props = {
        "Task": {"title": rich_text(card["proposed_action"])},
        "Done": {"checkbox": False},
        "Due": {"date": {"start": _iso(card["due_date"])} if card.get("due_date") else None},
        "Initiative": {"relation": [{"id": card["initiative_page_id"]}]
                       if card.get("initiative_page_id") else []},
        **facts(card),
    }
    if card["status"] == "deferred":
        props["Decision"] = {"select": {"name": "Defer"}}
        props["Defer until"] = {"date": {"start": _iso(card["deferred_until"])}
                                if card.get("deferred_until") else None}
    return props


def row_values(page: dict[str, Any]) -> dict[str, Any]:
    """What the operator set on a card row (pure)."""
    props = page.get("properties") or {}
    until = ((props.get("Defer until") or {}).get("date") or {}).get("start")
    return {**todo.notion_values(page),
            "decision": ((props.get("Decision") or {}).get("select") or {}).get("name"),
            "defer_until": until[:10] if until else None}


@dataclass
class Step:
    """What one run does for one card (pure data, so a dry run can print it)."""

    card_id: int
    action: str  # create | accept | decline | defer | reopen | refresh | skip | refuse
    page_id: str | None = None
    props: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def plan(cards: list[dict[str, Any]], pages: dict[str, dict[str, Any]],
         allowed: frozenset[str], today: date,
         resurfaced: frozenset[int] = frozenset()) -> list[Step]:
    """Decide each card's action (pure). `resurfaced` are the cards whose deferral
    ended this run: their stale Defer is cleared, not re-applied."""
    steps = []
    for c in cards:
        page = pages.get(c["notion_page_id"]) if c.get("notion_page_id") else None
        if page is None:
            steps.append(Step(c["id"], "create", props=new_row(c)))
            continue
        v = row_values(page)
        if c["id"] in resurfaced and v["decision"] == "Defer":
            steps.append(Step(c["id"], "refresh", page["id"],
                              {"Decision": {"select": None}, "Defer until": {"date": None}},
                              v, note="back from deferral"))
            continue
        if v["decision"] and operator.foreign_edit(page, allowed):
            steps.append(Step(c["id"], "refuse", page["id"], values=v,
                              note=f"card #{c['id']}: Decision set by someone other than "
                                   "the operator; not taken"))
            continue
        digest_changed = content_hash(facts(c)) != c.get("content_hash")
        refresh = facts(c) if digest_changed else {}
        if v["decision"] == "Accept":
            steps.append(Step(c["id"], "accept", page["id"], values=v))
        elif v["decision"] == "Decline":
            steps.append(Step(c["id"], "decline", page["id"], values=v))
        elif v["decision"] == "Defer" and c["status"] == "pending":
            until = v["defer_until"] or (today + timedelta(days=7)).isoformat()
            props = {} if v["defer_until"] else {"Defer until": {"date": {"start": until}}}
            steps.append(Step(c["id"], "defer", page["id"], {**props, **refresh},
                              {**v, "defer_until": until}))
        elif not v["decision"] and c["status"] == "deferred":
            steps.append(Step(c["id"], "reopen", page["id"], refresh, v))
        elif refresh:
            steps.append(Step(c["id"], "refresh", page["id"], refresh, v))
        else:
            steps.append(Step(c["id"], "skip", page["id"], values=v))
    return steps


def _initiative_id(values: dict[str, Any], initiatives: dict[str, int]) -> int | None:
    rel = values.get("initiative") or []
    return initiatives.get(rel[0]) if len(rel) == 1 else None


def _record(conn: Any, card_id: int, page_id: str, digest: str) -> None:
    conn.execute(
        """
        INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                 last_pushed_at, last_pulled_at)
        VALUES (%s, %s, %s, %s, now(), now())
        ON CONFLICT (entity_table, entity_id) DO UPDATE SET
            notion_page_id = EXCLUDED.notion_page_id, content_hash = EXCLUDED.content_hash,
            last_pushed_at = now(), last_pulled_at = now()
        """, (SYNC_TABLE, str(card_id), page_id, digest))


def _forget(conn: Any, card_id: int) -> None:
    conn.execute("DELETE FROM notion_sync WHERE entity_table = %s AND entity_id = %s",
                 (SYNC_TABLE, str(card_id)))


def _trash(client: Any, page_id: str) -> None:
    client.request("PATCH", f"/pages/{page_id}", {"in_trash": True})


def _accept(client: Any, connection: Any, tinder: Any, step: Step,
            initiatives: dict[str, int], owner: str) -> int | None:
    """Promote one card and turn its row into the task's row. Returns the task id."""
    from agents.notion.todo_sync import _record as record_task

    v = step.values
    title = v["title"].strip()
    if not title:
        return None
    initiative = _initiative_id(v, initiatives)
    tinder.reopen_deferred(step.card_id)
    tinder.set_card_fields(step.card_id, title=title, due_date=v["due"],
                           okr_initiative_id=initiative)
    decided = tinder.decide(step.card_id, "accept")
    if decided is None:
        return None
    made = tinder.promote(decided["candidate"], owner=owner)
    task_id = made["task_id"]
    with connection() as conn:
        created = conn.execute("SELECT created_at FROM tasks WHERE id = %s",
                               (task_id,)).fetchone()[0]
    task = {"id": task_id, "created_at": created,
            "source_type": decided["candidate"].get("source_type"),
            "initiative_page_id": v["initiative"][0] if initiative else None}
    task_facts = todo.fact_props(task)
    client.update_page(step.page_id, {**task_facts, "Decision": {"select": None}})
    agreed = {"title": title, "due": v["due"],
              "initiative": v["initiative"] if initiative else [], "done": False}
    with connection() as conn:   # one mapping per page: drop the card's first
        _forget(conn, step.card_id)
        record_task(conn, task_id, step.page_id, content_hash(task_facts), agreed, True)
    return task_id


def run(client: Any, *, connection: Any, tinder: Any, allowed: frozenset[str],
        today: date, owner: str = "operator") -> tuple[str, list[str]]:
    """One daily run. `tinder` is `agents._lib.task_tinder`. Returns a summary line
    and notices for the day's briefing."""
    from agents.notion.todo_sync import _INITIATIVES_SQL, _database_id, _pages

    resurfaced = set(tinder.resurface_deferred(today))
    with connection() as conn:
        database_id = _database_id(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_CARDS_SQL, (SYNC_TABLE, tinder.MIN_CONFIDENCE, list(EXCLUDED)))
            cards = cur.fetchall()
            cur.execute(_MAPPED_SQL, (SYNC_TABLE,))
            mapped = cur.fetchall()
        initiatives = dict(conn.execute(_INITIATIVES_SQL).fetchall())
    pages, source = _pages(client, database_id)
    problems = [n for n in todo.CARD_PROPERTIES
                if n not in (client.retrieve_data_source(source).get("properties") or {})]
    if problems:
        raise RuntimeError("To-Do lacks the card columns: " + ", ".join(problems))

    notices: list[str] = []
    counts = dict.fromkeys(("created", "accepted", "declined", "deferred", "reopened",
                            "cleared"), 0)
    # A card decided elsewhere (Discord, during the dual-run) loses its row.
    for m in mapped:
        if m["status"] not in ("pending", "deferred") and m["notion_page_id"] in pages:
            _trash(client, m["notion_page_id"])
            with connection() as conn:
                _forget(conn, m["id"])
            counts["cleared"] += 1
    for step in plan(cards, pages, allowed, today, frozenset(resurfaced)):
        if step.action == "create":
            page = client.create_page({"type": "data_source_id", "data_source_id": source},
                                      step.props)
            with connection() as conn:
                _record(conn, step.card_id, page["id"],
                        content_hash(facts(next(c for c in cards if c["id"] == step.card_id))))
            counts["created"] += 1
        elif step.action == "accept":
            task_id = _accept(client, connection, tinder, step, initiatives, owner)
            if task_id:
                counts["accepted"] += 1
            else:
                notices.append(f"card #{step.card_id}: Accept not taken (blank Task, or "
                               "already decided)")
        elif step.action == "decline":
            tinder.reopen_deferred(step.card_id)
            tinder.decide(step.card_id, "decline")
            _trash(client, step.page_id)
            with connection() as conn:
                _forget(conn, step.card_id)
            counts["declined"] += 1
        elif step.action == "defer":
            tinder.decide(step.card_id, "defer", defer_until=step.values["defer_until"])
            counts["deferred"] += 1
        elif step.action == "reopen":
            tinder.reopen_deferred(step.card_id)
            counts["reopened"] += 1
        elif step.action == "refuse":
            notices.append(step.note)
        if step.page_id and step.props and step.action in ("defer", "reopen", "refresh",
                                                          "skip"):
            client.update_page(step.page_id, step.props)
            if any(k in step.props for k in ("Evidence", "Card id")):
                card = next(c for c in cards if c["id"] == step.card_id)
                with connection() as conn:
                    _record(conn, step.card_id, step.page_id, content_hash(facts(card)))
    summary = (f"OK    cards: {counts['created']} row(s) created, {counts['accepted']} "
               f"accepted, {counts['declined']} declined, {counts['deferred']} deferred, "
               f"{counts['reopened']} reopened, {len(resurfaced)} back from deferral, "
               f"{counts['cleared']} decided elsewhere and removed")
    return summary, notices

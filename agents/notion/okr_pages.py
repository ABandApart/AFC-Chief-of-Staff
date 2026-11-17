"""The live "Now" callout on every OKR page (`PRD-notion-integration.md` N10 Part A).

Called by the Monday `notion-dashboard-push` after it scores the week. Each
Objective, Key Result, and Initiative page in the master OKR databases gets one
callout at the top of its body, recorded as `notion_sync ('okr_page',
'<notion_page_id>:now')` and updated in place on later runs. The CoS writes
nothing else on these pages: the narrative below the callout is the operator's
(N-D30).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agents._lib import kr_metrics as km
from agents._lib.notion_client import NotionError, rich_text

SYNC_TABLE = "okr_page"

# How each metric is computed, in one sentence (from `15-okrs-q4-2026.md` and
# `kr_metrics._SQL`). A KR with metric `manual` is entered by hand on its page.
METRIC_NOTES = {
    "engagements_signed": "Count of `engagement_signed` outcomes this quarter.",
    "median_engagement_value": "Median value of this quarter's `engagement_signed` outcomes.",
    "project_to_maintenance_rate": "Maintenance conversions ÷ engagements signed, "
                                   "rolling 90 days.",
    "cold_sequences_live": "Cold outreach sequences live now (the capacity count).",
    "touches_on_schedule_rate": "Share of touches whose window closed this quarter that were "
                                "sent by the close (touches skipped before the close don't "
                                "count).",
    "sequences_completed": "Outreach targets whose sequence was closed by the drain rule "
                           "(`sequence_completed_at`) this quarter.",
    "conversations_opened": "Outreach targets moved to `conversation` (Replied) this quarter.",
    "calls_booked": "Outreach targets moved to `call_booked`, plus `discovery_call_booked` "
                    "outcomes not tied to a target, this quarter.",
    "proposals_sent": "Count of `proposal_sent` outcomes this quarter.",
    "followups_on_time_rate": "Share of follow-ups whose deadline fell this quarter that "
                              "were closed by it (plan and outreach follow-ups have none).",
    "agent_run_success_rate": "Share of agent runs that succeeded, rolling 7 days.",
    "llm_spend_month": "LLM spend recorded in `agent_runs` this calendar month.",
    "manual": "Entered by hand: set the value on this Key Result's Notion page.",
}



def status_emoji(result: km.Result) -> str:
    """The emoji that leads the status label (`kr_metrics.LABELS`)."""
    return result.score.label.split(" ", 1)[0]


def _fmt(value: Decimal | float | None, unit: str | None) -> str:
    if value is None:
        return "—"
    number = f"{float(value):,.1f}".rstrip("0").rstrip(".")
    if unit == "$":
        return f"${number}"
    if unit == "%":
        return f"{number}%"
    return f"{number} {unit}" if unit and unit != "count" else number


def kr_text(result: km.Result, unit: str | None, history: list[Decimal | None]) -> str:
    """The Now text for a Key Result (pure)."""
    progress = result.score.progress
    lines = [f"Now: {_fmt(result.current, unit)} of {_fmt(result.target, unit)}"
             + (f" ({progress:.0%})" if progress is not None else "")
             + f" · {result.score.label}"]
    if any(v is not None for v in history):
        lines.append("Last weeks: " + " → ".join(_fmt(v, unit) for v in history))
    lines.append("How it's measured: " + METRIC_NOTES.get(result.metric, result.metric))
    return "\n".join(lines)


def initiative_text(init: dict[str, Any], kr: km.Result | None, unit: str | None,
                    plan_minutes: int) -> str:
    """The Now text for an Initiative (pure)."""
    lines = []
    if kr is not None:
        lines.append(f"Moves: {kr.name}, now {_fmt(kr.current, unit)} of "
                     f"{_fmt(kr.target, unit)} · {kr.score.label}")
    lines.append(f"To-dos: {init['open_todos']} open, {init['done_todos']} done")
    lines.append(f"Plan this week: {plan_minutes} min")
    return "\n".join(lines)


def objective_text(results: list[km.Result], units: dict[int, str | None]) -> str:
    """The Now text for an Objective: one line per KR, then the worst status (pure)."""
    lines = [f"{r.name}: {_fmt(r.current, units.get(r.key_result_id))} of "
             f"{_fmt(r.target, units.get(r.key_result_id))} · {r.score.label}"
             for r in results]
    worst = km.worst(results)
    if worst is not None:
        lines.append(f"Weakest: {worst.name} ({worst.score.label})")
    return "\n".join(lines) or "No active Key Results."


def callout(text: str, emoji: str) -> dict[str, Any]:
    return {"rich_text": rich_text(text), "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background"}


_OBJECTIVES_SQL = """
    SELECT o.id, o.notion_page_id, kr.id AS kr_id, kr.unit
    FROM okr_objectives o JOIN okr_key_results kr ON kr.objective_id = o.id
    WHERE o.quarter = %s AND o.archived_at IS NULL AND kr.archived_at IS NULL
"""
_HISTORY_SQL = """
    SELECT okr_key_result_id, week_start, value FROM okr_snapshots
    WHERE week_start > %s::date - 35 AND week_start <= %s ORDER BY week_start
"""
_INITIATIVE_KR_SQL = "SELECT id, key_result_id FROM okr_initiatives WHERE archived_at IS NULL"
_PLAN_MINUTES_SQL = """
    SELECT pi.okr_initiative_id, sum(pi.minutes)::int
    FROM plan_items pi JOIN plans p ON p.id = pi.plan_id AND p.status = 'active'
    WHERE pi.day >= %s AND pi.day < %s::date + 7 GROUP BY 1
"""


def pages(conn: Any, w: Any) -> list[tuple[str, str, str]]:
    """(page id, emoji, text) for every OKR page, from the scored week `w`
    (`dashboard_push.Week`)."""
    by_kr = {r.key_result_id: r for r in w.results}
    kr_rows = {kr["id"]: kr for kr in w.krs}
    rows = conn.execute(_OBJECTIVES_SQL, (w.quarter,)).fetchall()
    units = {kr_id: unit for _, _, kr_id, unit in rows}
    history: dict[int, list[Decimal | None]] = {}
    for kr_id, _, value in conn.execute(_HISTORY_SQL, (w.week_start, w.week_start)):
        history.setdefault(kr_id, []).append(value)
    init_kr = dict(conn.execute(_INITIATIVE_KR_SQL).fetchall())
    minutes = dict(conn.execute(_PLAN_MINUTES_SQL, (w.as_of.date(), w.as_of.date())))

    out: list[tuple[str, str, str]] = []
    objectives: dict[str, list[km.Result]] = {}
    for _obj_id, obj_page, kr_id, _ in rows:
        objectives.setdefault(obj_page, [])
        if kr_id in by_kr:
            objectives[obj_page].append(by_kr[kr_id])
    for obj_page, results in objectives.items():
        out.append((obj_page, "🎯", objective_text(results, units)))
    for kr_id, r in by_kr.items():
        out.append((kr_rows[kr_id]["notion_page_id"], status_emoji(r),
                    kr_text(r, units.get(kr_id), history.get(kr_id, [])[-4:])))
    for i in w.initiatives:
        kr = by_kr.get(init_kr.get(i["id"]))
        out.append((i["notion_page_id"], "🧭",
                    initiative_text(i, kr, units.get(kr.key_result_id) if kr else None,
                                    minutes.get(i["id"], 0))))
    return out


def push(client: Any, connection: Any, w: Any) -> int:
    """Write or update each page's Now callout. Returns the number of writes."""
    with connection() as conn:
        items = pages(conn, w)
        known = dict(conn.execute(
            "SELECT entity_id, notion_page_id FROM notion_sync WHERE entity_table = %s",
            (SYNC_TABLE,)).fetchall())
    writes = 0
    for page_id, emoji, text in items:
        key = f"{page_id}:now"
        body = callout(text, emoji)
        block = known.get(key)
        if block:
            try:
                client.update_block(block, {"callout": body})
                writes += 1
                continue
            except NotionError:
                pass  # the operator deleted it: write a new one below
        made = client.append_blocks(page_id, [{"type": "callout", "callout": body}],
                                    at_start=True)
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, last_pushed_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (entity_table, entity_id)
                DO UPDATE SET notion_page_id = EXCLUDED.notion_page_id, last_pushed_at = now()
                """, (SYNC_TABLE, key, made[0]["id"]))
        writes += 1
    return writes

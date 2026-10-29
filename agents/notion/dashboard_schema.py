"""Notion dashboard: schemas, row projections, callout text, and views (N4). Pure.

Three CoS-owned mirror databases sit under the root page, beside To-Do. The CoS
writes every column; operator edits are overwritten on the next push.

- **KR Scorecard**: one row per KR per quarter.
- **Initiative Status**: one row per initiative per quarter. Its counts can't go in
  the master Initiatives database, because the operator owns that (N-S1).
- **Weekly Snapshots**: one row per week, one number column per computed metric.
  It mirrors `okr_snapshots` and feeds the trend charts.

Every row carries its Quarter, and each quarter's dashboard page filters its tables
to that quarter. A past quarter's page therefore keeps showing its own rows after
rollover; those rows stop updating. The trend charts span every week.

Every relation into a master database is one-way (`single_property`), so the
operator's databases gain no column (schema check, N4 verification 4).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from agents._lib import kr_metrics as km
from agents._lib.notion_client import rich_text

SCORECARD = "KR Scorecard"
INITIATIVE_STATUS = "Initiative Status"
SNAPSHOTS = "Weekly Snapshots"
DATABASES = (SCORECARD, INITIATIVE_STATUS, SNAPSHOTS)
SCHEMA_KEYS = {SCORECARD: "kr_scorecard_database",
               INITIATIVE_STATUS: "initiative_status_database",
               SNAPSHOTS: "weekly_snapshots_database"}

# notion_sync.entity_table for each database's rows.
SYNC_TABLES = {SCORECARD: "okr_scorecard", INITIATIVE_STATUS: "okr_initiative_status",
               SNAPSHOTS: "okr_snapshot_week"}
SYNC_BLOCKS = "dashboard_block"  # entity_id: '<quarter>:<block key>'

DESCRIPTIONS = {
    SCORECARD: "The CoS fills this every Monday from the KR metrics. Set targets in "
               "OKRs — Master, not here: edits here are overwritten.",
    INITIATIVE_STATUS: "The CoS counts each initiative's to-dos every Monday. "
                       "Edits here are overwritten.",
    SNAPSHOTS: "One row per week, written by the CoS every Monday. It feeds the trend "
               "charts. Edits here are overwritten.",
}

# Weekly Snapshots columns: one per computed metric, keyed by metric id because a
# KR can be renamed and the backfill attaches by metric.
SNAPSHOT_COLUMNS = {
    "engagements_signed": "Engagements signed",
    "median_engagement_value": "Median engagement $",
    "project_to_maintenance_rate": "Project → maintenance %",
    "cold_sequences_live": "Cold sequences live",
    "touches_on_schedule_rate": "Touches on schedule %",
    "sequences_completed": "Sequences completed",
    "conversations_opened": "Conversations opened",
    "calls_booked": "Calls booked",
    "proposals_sent": "Proposals sent",
    "followups_on_time_rate": "Follow-ups on time %",
    "agent_run_success_rate": "Agent run success %",
    "llm_spend_month": "LLM spend (month to date) $",
}
# The trend charts the setup creates. The other columns are there to chart by hand.
CHARTS = (("agent_run_success_rate", "Agent run success, by week"),
          ("llm_spend_month", "LLM spend month to date, by week"),
          ("engagements_signed", "Engagements signed, by week"),
          ("sequences_completed", "Sequences completed, by week"))

_STATUS_COLORS = {km.ON_TRACK: "green", km.AT_RISK: "yellow", km.OFF_TRACK: "red",
                  km.TOO_EARLY: "gray", km.NO_DATA: "gray", km.SET_TARGET: "blue"}


def _one_way(data_source_id: str) -> dict[str, Any]:
    return {"relation": {"data_source_id": data_source_id, "type": "single_property",
                         "single_property": {}}}


def _rollup(relation: str, prop: str) -> dict[str, Any]:
    return {"rollup": {"relation_property_name": relation, "rollup_property_name": prop,
                       "function": "show_original"}}


def scorecard_properties(key_results_ds: str) -> dict[str, Any]:
    return {
        "Key result": {"title": {}},
        "Key Result": _one_way(key_results_ds),
        "Quarter": {"rich_text": {}},
        "Current": {"number": {"format": "number_with_commas"}},
        "Target": {"number": {"format": "number_with_commas"}},
        "Progress": {"number": {"format": "percent"}},
        "Status": {"select": {"options": [{"name": km.LABELS[s], "color": _STATUS_COLORS[s]}
                                          for s in km.SEVERITY]}},
        "Note": {"rich_text": {}},
        "Updated": {"date": {}},
        "CoS id": {"number": {"format": "number"}},
    }


# Added after creation: a rollup needs its relation to exist first.
SCORECARD_ROLLUPS = {"Objective": _rollup("Key Result", "Objective"),
                     "Initiatives": _rollup("Key Result", "Initiatives")}


def initiative_status_properties(initiatives_ds: str) -> dict[str, Any]:
    return {
        "Initiative name": {"title": {}},
        "Initiative": _one_way(initiatives_ds),
        "Quarter": {"rich_text": {}},
        "Open to-dos": {"number": {"format": "number"}},
        "Done to-dos": {"number": {"format": "number"}},
        "Idle": {"checkbox": {}},
        "Updated": {"date": {}},
        "CoS id": {"number": {"format": "number"}},
    }


INITIATIVE_ROLLUPS = {"Key Result": _rollup("Initiative", "Key Result")}


def snapshot_properties() -> dict[str, Any]:
    props: dict[str, Any] = {"Week": {"title": {}}, "Week start": {"date": {}},
                             "Quarter": {"rich_text": {}}}
    for label in SNAPSHOT_COLUMNS.values():
        props[label] = {"number": {"format": "number_with_commas"}}
    return props


def required(name: str) -> tuple[str, ...]:
    if name == SCORECARD:
        return (*scorecard_properties("x"), *SCORECARD_ROLLUPS)
    if name == INITIATIVE_STATUS:
        return (*initiative_status_properties("x"), *INITIATIVE_ROLLUPS)
    return tuple(snapshot_properties())


# --- rows (pure) ----------------------------------------------------------------------


def _num(value: Decimal | float | None) -> dict[str, Any]:
    return {"number": None if value is None else float(value)}


def _text(value: str) -> dict[str, Any]:
    return {"rich_text": rich_text(value)}


def _title(value: str) -> dict[str, Any]:
    return {"title": rich_text(value)}


def _date(value: date | None) -> dict[str, Any]:
    return {"date": {"start": value.isoformat()} if value else None}


def note_for(result: km.Result, direction: str | None) -> str:
    """One short line on why the KR shows its status."""
    s = result.score
    if s.status == km.NO_DATA:
        return "Enter the value in OKRs — Master" if result.metric == "manual" \
            else "Nothing to measure yet"
    if s.status == km.SET_TARGET:
        return "Set the target in OKRs — Master"
    if s.status == km.TOO_EARLY:
        return "Early in the window; scored from day 15"
    if direction == km.LOWER:
        return "Lower is better"
    if km.REGISTRY[result.metric].kind == km.CUMULATIVE and s.score is not None:
        return f"{s.score:.0%} of where it should be by now"
    return ""


def scorecard_row(result: km.Result, *, kr_page_id: str, quarter: str, direction: str | None,
                  updated: date) -> dict[str, Any]:
    return {
        "Key result": _title(result.name),
        "Key Result": {"relation": [{"id": kr_page_id}]},
        "Quarter": _text(quarter),
        "Current": _num(result.current),
        "Target": _num(result.target),
        "Progress": _num(result.score.progress),
        "Status": {"select": {"name": result.score.label}},
        "Note": _text(note_for(result, direction)),
        "Updated": _date(updated),
        "CoS id": _num(result.key_result_id),
    }


def initiative_row(init: dict[str, Any], *, quarter: str, updated: date) -> dict[str, Any]:
    return {
        "Initiative name": _title(init["name"]),
        "Initiative": {"relation": [{"id": init["notion_page_id"]}]},
        "Quarter": _text(quarter),
        "Open to-dos": _num(init["open_todos"]),
        "Done to-dos": _num(init["done_todos"]),
        "Idle": {"checkbox": init["open_todos"] == 0},
        "Updated": _date(updated),
        "CoS id": _num(init["id"]),
    }


def snapshot_row(week_start: date, quarter: str,
                 values: dict[str, Decimal | None]) -> dict[str, Any]:
    props = {"Week": _title(f"Week of {week_start.isoformat()}"),
             "Week start": _date(week_start), "Quarter": _text(quarter)}
    for metric, label in SNAPSHOT_COLUMNS.items():
        props[label] = _num(values.get(metric))
    return props


# --- the dashboard page (pure) ------------------------------------------------------------


def page_title(quarter: str) -> str:
    """'2026-Q4' → 'Q4 2026 Dashboard'."""
    year, q = quarter.split("-")
    return f"{q} {year} Dashboard"


def _by_metric(results: list[km.Result], metric: str) -> km.Result | None:
    return next((r for r in results if r.metric == metric), None)


def _fmt(value: Decimal | None, metric: str) -> str:
    if value is None:
        return "—"
    if metric.endswith("_rate"):
        return f"{value:.0f}%"
    if metric in ("median_engagement_value", "llm_spend_month"):
        return f"${value:,.2f}" if metric == "llm_spend_month" else f"${value:,.0f}"
    return f"{value:,.0f}"


def _status_word(results: list[km.Result]) -> str:
    worst = min((km.SEVERITY.index(r.score.status) for r in results), default=None)
    return km.LABELS[km.SEVERITY[worst]] if worst is not None else km.LABELS[km.NO_DATA]


def stat_callouts(results: list[km.Result], attention: dict[str, int]) -> list[tuple[str, str]]:
    """The 4 stat callouts as (emoji, text): north star, pipeline, follow-through,
    and system health. Counts only; no company names."""
    def line(metric: str, label: str) -> str:
        r = _by_metric(results, metric)
        if r is None:
            return f"{label}: not tracked"
        tgt = f" / {_fmt(r.target, metric)}" if r.target is not None else ""
        return f"{label}: {_fmt(r.current, metric)}{tgt} · {r.score.label}"

    north = [line("engagements_signed", "Engagements signed"),
             line("median_engagement_value", "Median engagement")]
    pipeline = [line("cold_sequences_live", "Cold sequences live"),
                line("sequences_completed", "Sequences completed"),
                line("calls_booked", "Calls booked")]
    follow = [line("touches_on_schedule_rate", "Touches on schedule"),
              line("followups_on_time_rate", "Follow-ups on time"),
              f"Open to-dos: {attention.get('open_todos', 0)}"]
    health = [line("agent_run_success_rate", "Agent runs OK (7 days)"),
              line("llm_spend_month", "LLM spend this month")]
    return [("🤝", "North star\n" + "\n".join(north)),
            ("🔎", "Pipeline\n" + "\n".join(pipeline)),
            ("✅", "Follow-through\n" + "\n".join(follow)),
            ("⚙️", "System health\n" + "\n".join(health))]


# key → (singular, plural) line. Each names where to act, never a company.
ATTENTION_LINES = {
    "discoveries_to_review": ("{n} sourced company awaits review — open #outreach",
                              "{n} sourced companies await review — open #outreach"),
    "missed_touches": ("{n} outreach touch unsent past its window — open NocoDB",
                       "{n} outreach touches unsent past their window — open NocoDB"),
    "overdue_followups": ("{n} follow-up past its deadline — open #tasks",
                          "{n} follow-ups past their deadline — open #tasks"),
    "cards_pending": ("{n} Task Tinder card awaits a decision — open #task-tinder",
                      "{n} Task Tinder cards await a decision — open #task-tinder"),
    "failing_agents": ("{n} agent failed at least once in the last 7 days — see #system",
                       "{n} agents failed at least once in the last 7 days — see #system"),
}


def attention_text(counts: dict[str, int]) -> str:
    """'Needs your attention': counts only (N-S8), no company names."""
    lines = [forms[counts[key] != 1].format(n=counts[key])
             for key, forms in ATTENTION_LINES.items() if counts.get(key)]
    return "Needs your attention\n" + ("\n".join(lines) if lines else "Nothing this week.")


def header_text(quarter: str, as_of_day: date, updated: date) -> str:
    first, end = km.quarter_bounds(quarter)
    total = (end - first).days
    day = min(max((as_of_day - first).days, 1), total)
    return (f"Quarter {quarter} · day {day} of {total} ({day / total:.0%}) · "
            f"{first:%b %-d} – {end - timedelta(days=1):%b %-d}"
            f" · updated {updated:%a %b %-d} by the CoS (Mondays)")


def callout(emoji: str, text: str) -> dict[str, Any]:
    return {"type": "callout", "callout": {"rich_text": rich_text(text),
                                            "icon": {"type": "emoji", "emoji": emoji},
                                            "color": "gray_background"}}


def heading(text: str) -> dict[str, Any]:
    return {"type": "heading_2", "heading_2": {"rich_text": rich_text(text)}}


def paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


# --- views (pure) ------------------------------------------------------------------------


def quarter_filter(quarter: str, quarter_prop_id: str) -> dict[str, Any]:
    return {"property": quarter_prop_id, "rich_text": {"equals": quarter}}


def views(quarter: str, ids: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """The linked views on a quarter's page, in page order. `ids` maps database name
    → {'data_source_id', property name → property id}. The tables show the page's
    quarter only; the trend charts are unfiltered, so a trend reads across quarters
    and includes the backfilled weeks. Each entry names the
    heading it sits under; the first view under a heading creates the linked
    database block, and the rest are added to that block."""
    sc, ini, snap = ids[SCORECARD], ids[INITIATIVE_STATUS], ids[SNAPSHOTS]
    q_sc, q_ini = (quarter_filter(quarter, x["Quarter"]) for x in (sc, ini))
    off = {"and": [q_sc, {"property": sc["Status"],
                          "select": {"equals": km.LABELS[km.OFF_TRACK]}}]}
    out = [
        {"block": "scorecard", "data_source_id": sc["data_source_id"], "name": "By objective",
         "type": "table", "filter": q_sc,
         "sorts": [{"property": "Objective", "direction": "ascending"}],
         "configuration": {"type": "table", "group_by": None}},
        {"block": "scorecard", "data_source_id": sc["data_source_id"], "name": "Off track only",
         "type": "table", "filter": off, "configuration": {"type": "table"}},
        {"block": "initiatives", "data_source_id": ini["data_source_id"],
         "name": "Initiatives", "type": "table", "filter": q_ini,
         "configuration": {"type": "table"}},
    ]
    for metric, name in CHARTS:
        out.append({
            "block": "trends", "data_source_id": snap["data_source_id"], "name": name,
            "type": "chart",
            "configuration": {"type": "chart", "chart_type": "line",
                              "x_axis_property_id": snap["Week start"],
                              "y_axis_property_id": snap[SNAPSHOT_COLUMNS[metric]],
                              "sort": "x_ascending"},
        })
    return out

"""Master OKR document schema (`PRD-notion-integration.md`, increment N2). Pure.

Three linked Notion databases under the page "OKRs — Master": Objectives, Key
Results, and Initiatives. **The operator owns every field** (N-S1): the CoS reads
these databases and never writes them, except when `cli.notion_setup` creates
them and, with `--seed-q4`, enters the Q4 2026 rows once.

The **Metric** select is the binding between a KR and the SQL that measures it
(N-S2). Its options are exactly `METRICS`; `_lib/kr_metrics.py` (N4) must
implement every id here except `manual`. A select cannot hold a typo, so a KR can
never silently miss its query.
"""

from __future__ import annotations

from typing import Any

MASTER_PAGE_TITLE = "OKRs — Master"
OBJECTIVES = "Objectives"
KEY_RESULTS = "Key Results"
INITIATIVES = "Initiatives"
DATABASE_ORDER = (OBJECTIVES, KEY_RESULTS, INITIATIVES)  # each links to the one before

# Metric ids the CoS can compute (15-okrs-q4-2026.md §1). Metrics whose data
# source does not exist yet (selling hours, reply time, recurring pains) are
# deliberately absent: those KRs use `manual` until their gaps close.
METRICS: tuple[str, ...] = (
    "engagements_signed",
    "median_engagement_value",
    "project_to_maintenance_rate",
    "cold_sequences_live",
    "touches_on_schedule_rate",
    "sequences_completed",
    "conversations_opened",
    "calls_booked",
    "proposals_sent",
    "followups_on_time_rate",
    "agent_run_success_rate",
    "llm_spend_month",
    "manual",
)

UNITS = ("count", "$", "%", "hours")
DIRECTIONS = ("Higher is better", "Lower is better")
QUARTERS = ("2026-Q4", "2027-Q1")

# Names for the reverse side of each two-way relation, so Objectives shows a
# "Key Results" column and Key Results shows "Initiatives".
BACK_RELATION_NAMES = {KEY_RESULTS: "Key Results", INITIATIVES: "Initiatives"}

DESCRIPTIONS = {
    OBJECTIVES: "You own this. The CoS reads it every 15 minutes and never edits it.",
    KEY_RESULTS: (
        "You own this. Metric picks the measure the CoS computes; use 'manual' to "
        "type the value yourself. Each KR links to exactly one Objective."
    ),
    INITIATIVES: (
        "You own this. Each initiative links to exactly one Key Result. Discord's "
        "/initiative picks from the Active ones."
    ),
}

# Notion select colors: stable, one per meaning.
_STATUS = {"Active": "green", "Retired": "gray", "Paused": "yellow", "Done": "blue"}


def _select(names: tuple[str, ...], colors: dict[str, str] | None = None) -> dict[str, Any]:
    colors = colors or {}
    return {"select": {"options": [{"name": n, "color": colors.get(n, "default")} for n in names]}}


def _relation(data_source_id: str) -> dict[str, Any]:
    return {"relation": {"data_source_id": data_source_id,
                         "type": "dual_property", "dual_property": {}}}


def properties_for(name: str, related_data_source_id: str | None = None) -> dict[str, Any]:
    """Property schema for one master database.

    `related_data_source_id` is the data source of the database this one links to
    (Key Results → Objectives, Initiatives → Key Results); None for Objectives.
    """
    if name == OBJECTIVES:
        return {
            "Name": {"title": {}},
            "Quarter": _select(QUARTERS),
            "Order": {"number": {"format": "number"}},
            "Status": _select(("Active", "Retired"), _STATUS),
        }
    if related_data_source_id is None:
        raise ValueError(f"{name} links to another database; pass its data source id")
    if name == KEY_RESULTS:
        return {
            "Name": {"title": {}},
            "Objective": _relation(related_data_source_id),
            "Metric": _select(METRICS),
            "Target": {"number": {"format": "number"}},
            "Unit": _select(UNITS),
            "Direction": _select(DIRECTIONS),
            "Manual value": {"number": {"format": "number"}},
            "Status": _select(("Active", "Retired"), _STATUS),
        }
    if name == INITIATIVES:
        return {
            "Name": {"title": {}},
            "Key Result": _relation(related_data_source_id),
            "Status": _select(("Active", "Paused", "Done"), _STATUS),
            "Owner": {"people": {}},
        }
    raise ValueError(f"unknown master database: {name!r}")


def back_relation_rename(
    target_properties: dict[str, Any], source_data_source_id: str, wanted: str
) -> dict[str, Any] | None:
    """The PATCH that renames the auto-created reverse relation (pure).

    Notion names the reverse side of a dual relation itself. Find the property on
    the target data source that points back at `source_data_source_id` and, if it
    isn't already `wanted`, return `{current_name: {"name": wanted}}`. None when
    nothing needs to change or no reverse property exists.
    """
    for current, prop in target_properties.items():
        rel = prop.get("relation") if prop.get("type") == "relation" else None
        if rel and rel.get("data_source_id") == source_data_source_id:
            return None if current == wanted else {current: {"name": wanted}}
    return None


def missing_properties(name: str, existing: dict[str, Any]) -> list[str]:
    """Properties the spec requires that an existing database lacks (pure).

    Setup never rewrites an operator-owned database; it reports drift instead.
    """
    return [p for p in properties_for(name, "x") if p not in existing]


def missing_options(name: str, existing: dict[str, Any]) -> list[str]:
    """Select options the spec requires that an existing database lacks (pure).

    Catches a Metric id added to `METRICS` after the database was created. Extra
    options the operator added are fine and not reported.
    """
    out = []
    for prop, cfg in properties_for(name, "x").items():
        if "select" not in cfg or prop not in existing:
            continue
        have = {o.get("name") for o in (existing[prop].get("select") or {}).get("options", [])}
        out += [f"{prop} option {o['name']!r}" for o in cfg["select"]["options"]
                if o["name"] not in have]
    return out


# --- optional Q4 2026 seed (15-okrs-q4-2026.md §1) --------------------------------
# Targets marked (proposed) there are entered as-is: once in Notion, the operator
# owns and edits them. None = left blank on purpose (the reason is in the comment).

Q4_SEED: list[dict[str, Any]] = [
    {"objective": "Sustainable long-term contract engagements", "order": 1, "krs": [
        ("New engagements signed", "engagements_signed", 3, "count", "Higher is better"),
        ("Median $ per engagement", "median_engagement_value", 15000, "$", "Higher is better"),
        ("Project → maintenance conversion", "project_to_maintenance_rate", 50, "%",
         "Higher is better"),
    ]},
    {"objective": "Keep the pipeline full", "order": 2, "krs": [
        # Blank: 0029 raised the live-sequence cap from 15 to 150; "12 of 15" is void.
        ("Cold sequences live", "cold_sequences_live", None, "count", "Higher is better"),
        ("Touches sent on schedule", "touches_on_schedule_rate", 90, "%", "Higher is better"),
        ("Completed sequences", "sequences_completed", 40, "count", "Higher is better"),
        ("Conversations opened", "conversations_opened", 20, "count", "Higher is better"),
        ("Discovery calls booked", "calls_booked", 20, "count", "Higher is better"),
        ("Proposals sent", "proposals_sent", 10, "count", "Higher is better"),
    ]},
    {"objective": "Use client work to find the product", "order": 3, "krs": [
        ("Recurring client pains (at 3+ companies)", "manual", 2, "count", "Higher is better"),
        ("One-page product spec", "manual", 1, "count", "Higher is better"),
    ]},
    {"objective": "The CoS system buys back selling time", "order": 4, "krs": [
        ("Follow-ups closed on time", "followups_on_time_rate", 90, "%", "Higher is better"),
        ("Agent run success", "agent_run_success_rate", 95, "%", "Higher is better"),
        # Blank: no monthly budget is set yet (gap M9).
        ("LLM spend per month", "llm_spend_month", None, "$", "Lower is better"),
        ("Selling hours per week", "manual", 15, "hours", "Higher is better"),
        ("Reply response time", "manual", 24, "hours", "Lower is better"),
    ]},
]


def _title(text: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def objective_row(name: str, order: int, quarter: str = "2026-Q4") -> dict[str, Any]:
    return {"Name": _title(name), "Quarter": {"select": {"name": quarter}},
            "Order": {"number": order}, "Status": {"select": {"name": "Active"}}}


def kr_row(name: str, objective_page_id: str, metric: str, target: float | None,
           unit: str, direction: str) -> dict[str, Any]:
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    return {"Name": _title(name), "Objective": {"relation": [{"id": objective_page_id}]},
            "Metric": {"select": {"name": metric}}, "Target": {"number": target},
            "Unit": {"select": {"name": unit}}, "Direction": {"select": {"name": direction}},
            "Status": {"select": {"name": "Active"}}}

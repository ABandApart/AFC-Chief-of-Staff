"""The Notion Leads database, as the CoS reads and writes it (`PRD-notion-integration.md` N8).

Pure: no I/O, so every rule here is unit-tested without Notion or Postgres.

Leads is shared with the Docent WordPress plugin, which writes rows with Channel
= Docent and ignores every other row. The CoS writes one row per
`outreach_targets` row, with Channel = Outreach, and never touches a Docent row.

Ownership per column (N8 Leads columns table):
- **Operator, merged:** Status (after-contact values only), Start on, and, while
  the target is a `candidate`, Stage and Function state. Each is merged against
  the value both sides last agreed on (`notion_sync.synced_fields`), as N-S17
  does for To-Do rows: a Notion change wins.
- **Operator only:** Notes and Next step. The CoS never writes them.
- **CoS:** everything else, overwritten from Postgres each push.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agents._lib.notion_client import rich_text

# The Leads database under the CoS root page (created by the operator, 2026-11-10).
DATABASE_ID = "f9c8c348-4c17-4d2c-9af9-aefa9e20fbb8"
SYNC_TABLE = "outreach_targets"  # notion_sync.entity_table for Leads rows
CHANNEL = "Outreach"

STAGES = ("seed", "series_a", "series_b_plus", "mature")
FUNCTION_STATES = ("self_covered", "under_led", "vacant_seat")

# Postgres status → Notion Status.
STATUS_LABEL = {
    "candidate": "New",
    "in_sequence": "Contacted",
    "conversation": "Replied",
    "call_booked": "Call booked",
    "engaged": "Client",
    "dropped": "Not a fit",
    "opted_out": "Opted out",
    "watchlist": "Parked",
    "lost_to_hire": "Parked",
    "archived": "Parked",
}
# The Notion Statuses the operator may set, and what each sets in Postgres.
# New, Contacted, and Parked are CoS-only: a sequence starts from Start on, and
# parking follows the drain rule.
SETTABLE = {
    "Replied": "conversation",
    "Call booked": "call_booked",
    "Client": "engaged",
    "Not a fit": "dropped",
    "Opted out": "opted_out",
}
# Statuses that end a live sequence: its unsent touches are skipped.
ENDS_SEQUENCE = frozenset({"conversation", "dropped", "opted_out"})

# Columns the CoS needs, beyond the ones Docent made. barry-admin adds them once
# through the Notion connector; the sync refuses to run while any is missing.
COLUMNS = {
    "Name": "title", "Channel": "select", "Email": "email", "Role": "rich_text",
    "Status": "status", "Notes": "rich_text", "Next step": "rich_text",
    "Company": "rich_text", "Website": "url", "LinkedIn": "url",
    "Background": "rich_text", "Stage": "select", "Function state": "select",
    "Ready to start": "checkbox", "Start on": "date", "Last contacted": "date",
    "Next touch": "date", "Message": "rich_text", "Message ready": "checkbox",
    "CoS id": "number",
}
# Select and status options the CoS writes; each must exist.
OPTIONS = {
    "Channel": (CHANNEL,),
    "Status": tuple(dict.fromkeys(STATUS_LABEL.values())),
    "Stage": STAGES,
    "Function state": FUNCTION_STATES,
}

# Fields merged with Notion. Stage and Function state only while `candidate`.
MERGED = ("status", "start_on", "stage", "function_state")
PROPERTY = {"status": "Status", "start_on": "Start on", "stage": "Stage",
            "function_state": "Function state"}


def schema_problems(props: dict[str, Any]) -> list[str]:
    """What the live Leads data source lacks (pure). Empty means ready."""
    out = []
    for name, kind in COLUMNS.items():
        have = (props.get(name) or {}).get("type")
        if have != kind:
            out.append(f"column {name!r} ({kind})" + (f" is {have}" if have else " is missing"))
    for name, wanted in OPTIONS.items():
        spec = props.get(name) or {}
        options = (spec.get(spec.get("type") or "") or {}).get("options") or []
        missing = [w for w in wanted if w not in {o.get("name") for o in options}]
        if missing:
            out.append(f"{name} option(s) missing: {', '.join(missing)}")
    return out


def owned_fields(target: dict[str, Any]) -> tuple[str, ...]:
    """The fields merged for this target. Once a sequence starts, Stage and
    Function state are the CoS's: the arc's templates were chosen from them."""
    if target.get("status") == "candidate":
        return MERGED
    return ("status", "start_on")


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return (value.date() if isinstance(value, datetime) else value).isoformat()


def pg_values(target: dict[str, Any]) -> dict[str, Any]:
    """The merged fields as Postgres holds them, in the form `notion_values` reads."""
    return {"status": STATUS_LABEL.get(target["status"], "Parked"),
            "start_on": _iso(target.get("start_on")),
            "stage": target.get("stage"),
            "function_state": target.get("function_state")}


def notion_values(page: dict[str, Any]) -> dict[str, Any]:
    """The merged fields of a live Leads row."""
    props = page.get("properties") or {}
    start = ((props.get("Start on") or {}).get("date") or {}).get("start")

    def option(name: str) -> str | None:
        spec = props.get(name) or {}
        return ((spec.get(spec.get("type") or "select") or {}) or {}).get("name")

    return {"status": option("Status"), "start_on": start[:10] if start else None,
            "stage": option("Stage"), "function_state": option("Function state")}


def refusal(name: str, value: Any) -> str | None:
    """Why a changed Notion value can't be taken into Postgres, or None (pure).

    A refused value is not pulled; the push restores Postgres's value and the
    run reports it."""
    if name == "status" and value not in SETTABLE:
        return (f"Status {value!r} can't be set in Notion "
                f"(use {', '.join(SETTABLE)}; start a sequence with Start on)")
    if name == "stage" and value is not None and value not in STAGES:
        return f"Stage {value!r} is not one of {', '.join(STAGES)}"
    if name == "function_state" and value is not None and value not in FUNCTION_STATES:
        return f"Function state {value!r} is not one of {', '.join(FUNCTION_STATES)}"
    return None


def field_props(values: dict[str, Any], fields: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Notion properties for some merged fields."""
    out: dict[str, Any] = {}
    for f in fields:
        v = values[f]
        if f == "status":
            out["Status"] = {"status": {"name": v}}
        elif f == "start_on":
            out["Start on"] = {"date": {"start": v} if v else None}
        else:
            out[PROPERTY[f]] = {"select": {"name": v} if v else None}
    return out


def _money(usd: Any) -> str:
    n = float(usd)
    return f"${n / 1e6:.0f}M" if n >= 1e6 else f"${n / 1e3:.0f}K"


def background(t: dict[str, Any]) -> str:
    """One line of company background from the firmographic columns (pure)."""
    parts = []
    if t.get("sector"):
        parts.append(str(t["sector"]).replace("_", " "))
    if t.get("headcount"):
        parts.append(f"{t['headcount']} staff")
    if t.get("hq_location"):
        parts.append(str(t["hq_location"]))
    if t.get("ownership_type"):
        parts.append(str(t["ownership_type"]).replace("_", " "))
    if t.get("founded_year"):
        parts.append(f"founded {t['founded_year']}")
    if t.get("total_raised_usd"):
        raised = f"raised {_money(t['total_raised_usd'])}"
        if t.get("last_round_type"):
            raised += f" (last: {t['last_round_type']}"
            raised += f", {_iso(t['last_round_at'])})" if t.get("last_round_at") else ")"
        parts.append(raised)
    return " · ".join(parts)


def message(t: dict[str, Any]) -> str:
    """The current touch's latest packet: subject, blank line, body (pure)."""
    if not t.get("body_filled"):
        return ""
    return f"Subject: {t.get('subject_line') or ''}\n\n{t['body_filled']}"


def website(t: dict[str, Any]) -> str | None:
    if t.get("company_url"):
        return str(t["company_url"])
    return f"https://{t['company_domain']}" if t.get("company_domain") else None


def ready_to_start(t: dict[str, Any], capacity_free: bool) -> bool:
    """A candidate with a Function state, when capacity has room (N8 R1)."""
    return t.get("status") == "candidate" and bool(t.get("function_state")) and capacity_free


def fact_props(t: dict[str, Any], *, capacity_free: bool) -> dict[str, Any]:
    """Every CoS-written column (pure). Hashed to skip unchanged rows. Stage and
    Function state are here only once the target has left `candidate`."""
    name = t.get("contact_name") or t["company_name"]
    out: dict[str, Any] = {
        "Name": {"title": rich_text(str(name))},
        "Channel": {"select": {"name": CHANNEL}},
        "Email": {"email": t.get("contact_email") or None},
        "Role": {"rich_text": rich_text(t.get("contact_role") or "")},
        "Company": {"rich_text": rich_text(t["company_name"])},
        "Website": {"url": website(t)},
        "LinkedIn": {"url": t.get("contact_linkedin_url") or None},
        "Background": {"rich_text": rich_text(background(t))},
        "Ready to start": {"checkbox": ready_to_start(t, capacity_free)},
        "Last contacted": {"date": {"start": _iso(t["last_contacted"])}
                           if t.get("last_contacted") else None},
        "Next touch": {"date": {"start": _iso(t["next_touch"])} if t.get("next_touch") else None},
        "Message": {"rich_text": rich_text(message(t))},
        "Message ready": {"checkbox": bool(t.get("packet_ready"))},
        "CoS id": {"number": t["id"]},
    }
    if "stage" not in owned_fields(t):
        out.update(field_props(pg_values(t), ("stage", "function_state")))
    return out

"""The Notion Outcomes, Capture, and Sources databases (`PRD-notion-integration.md` N9 I5).

Pure: no I/O, so every rule here is unit-tested without Notion or Postgres.

These three databases replace three Discord inputs: `/outcome`, #capture, and
Tartt control in #briefing. barry-admin creates each database once through the
Notion connector and records its id as `notion_sync ('notion_schema',
'<name>_database')`; the sync refuses to touch a database whose columns or
options don't match `COLUMNS` and `OPTIONS` below.

Ownership per database:
- **Outcomes:** the operator writes a row; the CoS imports it once and writes
  CoS id. After that the row is never read again, so KR history can't change
  silently.
- **Capture:** the operator writes Note and Body; the CoS writes Ingested and
  Result.
- **Sources:** the operator owns Active, Every, and URL on a feed row, and
  Active on an interest row. Each is merged against the value both sides last
  agreed on (`notion_sync.synced_fields`): a Notion change wins (N-S17). The CoS
  writes Name, Kind, and CoS id.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agents._lib.notion_client import rich_text
from agents._lib.outcomes import OUTCOME_TYPES, requires_company

# --- shared ------------------------------------------------------------------------

def schema_problems(props: dict[str, Any], columns: dict[str, str],
                    options: dict[str, tuple[str, ...]]) -> list[str]:
    """What a live data source lacks against `columns` and `options` (pure).
    Empty means ready. The same check `leads_schema.schema_problems` makes."""
    out = []
    for name, kind in columns.items():
        have = (props.get(name) or {}).get("type")
        if have != kind:
            out.append(f"column {name!r} ({kind})" + (f" is {have}" if have else " is missing"))
    for name, wanted in options.items():
        spec = props.get(name) or {}
        opts = (spec.get(spec.get("type") or "") or {}).get("options") or []
        missing = [w for w in wanted if w not in {o.get("name") for o in opts}]
        if missing:
            out.append(f"{name} option(s) missing: {', '.join(missing)}")
    return out


def _props(page: dict[str, Any]) -> dict[str, Any]:
    return page.get("properties") or {}


def text_of(page: dict[str, Any], name: str) -> str:
    """Plain text of a title or rich-text property."""
    spec = _props(page).get(name) or {}
    parts = spec.get("title") if "title" in spec else spec.get("rich_text")
    return "".join(t.get("plain_text") or (t.get("text") or {}).get("content", "")
                   for t in parts or []).strip()


def number_of(page: dict[str, Any], name: str) -> float | int | None:
    return (_props(page).get(name) or {}).get("number")


def select_of(page: dict[str, Any], name: str) -> str | None:
    return ((_props(page).get(name) or {}).get("select") or {}).get("name")


def date_of(page: dict[str, Any], name: str) -> str | None:
    start = ((_props(page).get(name) or {}).get("date") or {}).get("start")
    return start[:10] if start else None


def checkbox_of(page: dict[str, Any], name: str) -> bool:
    return bool((_props(page).get(name) or {}).get("checkbox"))


def cos_id(page: dict[str, Any]) -> int | None:
    n = number_of(page, "CoS id")
    return int(n) if n is not None else None


# --- Outcomes ----------------------------------------------------------------------

OUTCOMES_KEY = "outcomes_database"  # notion_sync key (entity_table notion_schema)
OUTCOMES_TABLE = "outcomes"  # notion_sync.entity_table for imported rows

OUTCOMES_COLUMNS = {
    "Outcome": "title", "Type": "select", "Value": "number", "Company": "rich_text",
    "Date": "date", "CoS id": "number",
}
OUTCOMES_OPTIONS = {"Type": tuple(OUTCOME_TYPES)}


def outcome_fields(page: dict[str, Any]) -> dict[str, Any]:
    """The operator's fields of an Outcomes row."""
    return {"description": text_of(page, "Outcome"), "type": select_of(page, "Type"),
            "value": number_of(page, "Value"), "company": text_of(page, "Company"),
            "date": date_of(page, "Date")}


def outcome_refusal(fields: dict[str, Any]) -> str | None:
    """Why an Outcomes row can't be imported before its company is looked up, or
    None (pure). A contract type with a blank Company is refused here; one whose
    Company matches nothing is refused after the lookup (`company_refusal`)."""
    if fields["type"] not in OUTCOME_TYPES:
        return (f"Type {fields['type']!r} is not an outcome type" if fields["type"]
                else "Type is blank")
    if fields["value"] is not None and fields["value"] < 0:
        return f"Value {fields['value']} is negative"
    if requires_company(fields["type"]) and not fields["company"]:
        return f"{fields['type']} needs a Company (a client or outreach target)"
    return None


def company_refusal(outcome_type: str, problem: str | None) -> str | None:
    """Why the company lookup stops the import, or None (pure). Mirrors the
    database CHECK `outcomes_engagement_has_client_ck`: only contract types need
    a company, so another type with an unmatched Company is imported without one."""
    if problem and requires_company(outcome_type):
        return f"{outcome_type} needs a matching company: {problem}"
    return None


def outcome_date(fields: dict[str, Any]) -> date | None:
    return date.fromisoformat(fields["date"]) if fields["date"] else None


def outcome_written(outcome_id: int) -> dict[str, Any]:
    """The one column the CoS writes on an Outcomes row."""
    return {"CoS id": {"number": outcome_id}}


# --- Capture -----------------------------------------------------------------------

CAPTURE_KEY = "capture_database"
CAPTURE_TABLE = "capture"  # notion_sync.entity_table; entity_id is the page id

CAPTURE_COLUMNS = {"Note": "title", "Body": "rich_text", "Ingested": "checkbox",
                   "Result": "rich_text"}
CAPTURE_OPTIONS: dict[str, tuple[str, ...]] = {}

RESULT_TEXT = {
    "captured": "Captured to memory.",
    "repost": "Already captured: the same note was ingested before; nothing new written.",
}


def capture_text(page: dict[str, Any]) -> str:
    """The note to ingest: Note, a blank line, then Body. Empty when both are."""
    return "\n\n".join(t for t in (text_of(page, "Note"), text_of(page, "Body")) if t)


def capture_pending(page: dict[str, Any]) -> bool:
    return not checkbox_of(page, "Ingested") and bool(capture_text(page))


def capture_written(*, ingested: bool, result: str) -> dict[str, Any]:
    """The CoS-written Capture columns."""
    return {"Ingested": {"checkbox": ingested},
            "Result": {"rich_text": rich_text(result[:2000])}}


# --- Sources -----------------------------------------------------------------------

SOURCES_KEY = "sources_database"
SOURCES_TABLE = "sources"  # feeds: entity_id is sources.id
INTERESTS_TABLE = "interests"  # interests: entity_id is the topic text
INTEREST = "interest"
KINDS = ("rss", "newsletter", "arxiv", INTEREST)
DEFAULT_KIND = "rss"

SOURCES_COLUMNS = {
    "Name": "title", "Kind": "select", "URL": "url", "Every": "number",
    "Active": "checkbox", "CoS id": "number",
}
SOURCES_OPTIONS = {"Kind": KINDS}

# The fields the operator owns, per row kind, merged with `todo_schema.merge_field`.
FEED_OWNED = ("active", "every", "url")
INTEREST_OWNED = ("active",)
PROPERTY = {"active": "Active", "every": "Every", "url": "URL"}


def is_interest(page: dict[str, Any]) -> bool:
    return select_of(page, "Kind") == INTEREST


def feed_pg_values(source: dict[str, Any]) -> dict[str, Any]:
    """A `sources` row's owned fields, in the form `feed_notion_values` reads."""
    return {"active": bool(source["active"]), "every": int(source["poll_interval_hours"]),
            "url": source["url"]}


def feed_notion_values(page: dict[str, Any]) -> dict[str, Any]:
    every = number_of(page, "Every")
    url = (_props(page).get("URL") or {}).get("url")
    return {"active": checkbox_of(page, "Active"),
            "every": int(every) if isinstance(every, (int, float)) and every == int(every)
            else every,
            "url": url or None}


def feed_refusal(name: str, value: Any) -> str | None:
    """Why a changed feed value can't be taken into `sources`, or None (pure)."""
    if name == "every" and not (isinstance(value, int) and value >= 1):
        return f"Every {value!r} must be a whole number of hours, 1 or more"
    if name == "url" and not (isinstance(value, str)
                              and value.startswith(("http://", "https://"))):
        return f"URL {value!r} must start with http:// or https://"
    return None


def owned_props(values: dict[str, Any], fields: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Notion properties for some owned Sources fields."""
    out: dict[str, Any] = {}
    for f in fields:
        v = values[f]
        if f == "active":
            out["Active"] = {"checkbox": bool(v)}
        elif f == "every":
            out["Every"] = {"number": v}
        else:
            out["URL"] = {"url": v or None}
    return out


def feed_facts(source: dict[str, Any]) -> dict[str, Any]:
    """Every CoS-written column of a feed row (pure). Hashed to skip unchanged rows."""
    return {"Name": {"title": rich_text(str(source["name"]))},
            "Kind": {"select": {"name": source["source_kind"]}},
            "CoS id": {"number": source["id"]}}


def interest_facts(topic: str) -> dict[str, Any]:
    """Every CoS-written column of an interest row. Interests have no numeric id,
    so CoS id stays blank; the `notion_sync` row keyed by topic maps the page."""
    return {"Name": {"title": rich_text(topic)}, "Kind": {"select": {"name": INTEREST}}}


def new_feed(page: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A hand-added feed row as `tartt_control.add_feed` arguments, or why not (pure)."""
    name, values = text_of(page, "Name"), feed_notion_values(page)
    kind = select_of(page, "Kind") or DEFAULT_KIND
    every = values["every"]
    if not values["url"]:
        return None, f"feed {name or '(untitled)'!r} needs a URL"
    if why := feed_refusal("url", values["url"]):
        return None, f"feed {name or '(untitled)'!r}: {why}"
    if every is not None and (why := feed_refusal("every", every)):
        return None, f"feed {name or '(untitled)'!r}: {why}"
    return {"name": name or values["url"], "url": values["url"], "source_kind": kind,
            "cadence_hours": every}, None

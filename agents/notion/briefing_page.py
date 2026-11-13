"""The day's Briefing page, system notes, and urgent alerts (`PRD-notion-integration.md`
N9 I6).

- **Briefing page.** One row per day in the **Briefings** database, titled like
  "Thu Nov 12" with the day in **Date**. `morning-briefing` (06:00) writes the
  briefing sections to it, and later morning loops `append` theirs. The page is
  mapped as `notion_sync ('briefing', '<day>')`.
- **System notes.** `system_note` appends a warning under a "System" heading on
  the day's page and refreshes a **System** callout on the quarter's dashboard,
  placed right after the attention callout and recorded as `dashboard_block`
  `<quarter>:system`. That row's `synced_fields` holds the last 24 hours of notes
  the callout shows.
- **Urgent alerts.** An urgent note also posts a comment on the day's page that
  @-mentions the operator: a Notion comment mention is the only push notification
  Notion sends to the phone. The same text on the same day is commented once
  (`notion_sync ('briefing_comment', '<day>:<hash>')`).

Barry-admin creates the Briefings database once and records its id as
`notion_sync ('notion_schema', 'briefings_database')`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from agents._lib import kr_metrics
from agents._lib.notion_client import content_hash, mention_user, rich_text
from agents.notion import dashboard_page
from agents.notion import dashboard_schema as dash
from agents.notion.operator import OPERATOR_KEY, schema_id

logger = logging.getLogger(__name__)

SCHEMA_KEY = "briefings_database"
COLUMNS = {"Name": "title", "Date": "date"}
SYNC_TABLE = "briefing"
SYNC_COMMENTS = "briefing_comment"
SYSTEM_HEADING = "System"
SYSTEM_KEY = "system"  # dashboard_block '<quarter>:system'
SYSTEM_EMOJI = "🛠️"
SYSTEM_WINDOW = timedelta(hours=24)
SYSTEM_SHOWN = 10  # notes the dashboard callout lists, newest first
FAILS_IN_A_ROW = 2
LOOP_RECENT = timedelta(hours=48)  # a loop whose latest run is older is not alerted
MAX_CHILDREN = 100  # the API's cap per append


# --- pure --------------------------------------------------------------------------------


def title(day: date) -> str:
    """The page title, e.g. "Thu Nov 12"."""
    return f"{day:%a %b} {day.day}"


def schema_problems(props: dict[str, Any]) -> list[str]:
    """What the live Briefings data source lacks. Empty means ready."""
    out = []
    for name, kind in COLUMNS.items():
        have = (props.get(name) or {}).get("type")
        if have != kind:
            out.append(f"column {name!r} ({kind})" + (f" is {have}" if have else " is missing"))
    return out


def page_properties(day: date) -> dict[str, Any]:
    return {"Name": {"title": rich_text(title(day))},
            "Date": {"date": {"start": day.isoformat()}}}


def heading3(text: str) -> dict[str, Any]:
    return {"type": "heading_3", "heading_3": {"rich_text": rich_text(text)}}


def section_blocks(heading: str, text: str) -> list[dict[str, Any]]:
    """A heading-3 and one paragraph per blank-line-separated chunk of `text`.
    `rich_text` splits a chunk over 2,000 characters."""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return [heading3(heading), *(dash.paragraph(c) for c in chunks)]


def note_line(at: datetime, text: str) -> str:
    return f"{at:%H:%M} {text}"


def mention_text(operator_id: str, text: str) -> list[dict[str, Any]]:
    """The urgent comment: the operator's mention, then the note."""
    return [mention_user(operator_id), *rich_text(f" {text}")]


def recent_notes(notes: Iterable[dict[str, str]], now: datetime) -> list[dict[str, str]]:
    """Notes from the last 24 hours, oldest first, at most SYSTEM_SHOWN."""
    keep = [n for n in notes if now - datetime.fromisoformat(n["at"]) <= SYSTEM_WINDOW]
    keep.sort(key=lambda n: n["at"])
    return keep[-SYSTEM_SHOWN:]


def system_callout_text(notes: list[dict[str, str]]) -> str:
    """The dashboard System callout: newest first."""
    if not notes:
        return "System: no warnings in the last 24 hours."
    lines = [f"System: {len(notes)} warning(s) in the last 24 hours"]
    for n in reversed(notes):
        lines.append("• " + note_line(datetime.fromisoformat(n["at"]), n["text"]))
    return "\n".join(lines)


def urgent_rules(loops: Iterable[tuple[str, str, list[str], str | None]],
                 spend: Iterable[tuple[date, float, dict[str, float]]],
                 *, global_ceiling: float,
                 group_ceilings: dict[str, float]) -> list[str]:
    """The urgent reasons (pure).

    `loops`: (agent_name, function_label, statuses newest first, latest error).
    `spend`: (local day, total USD, group → USD).
    """
    out = []
    for agent, label, statuses, error in loops:
        recent = statuses[:FAILS_IN_A_ROW]
        if len(recent) == FAILS_IN_A_ROW and all(s != "success" for s in recent):
            line = f"{agent} {label} failed {FAILS_IN_A_ROW} runs in a row"
            if error:
                line += f": {error[:200]}"
            out.append(line)
    for day, total, groups in spend:
        if total >= global_ceiling:
            out.append(f"The system spend ceiling was hit on {day:%a %b} {day.day}: "
                       f"${total:.2f} of ${global_ceiling:.2f}; later LLM calls were refused")
        for group, ceiling in group_ceilings.items():
            usd = groups.get(group, 0.0)
            if usd >= ceiling:
                out.append(f"The {group} spend ceiling was hit on {day:%a %b} {day.day}: "
                           f"${usd:.2f} of ${ceiling:.2f}")
    return out


# --- notion_sync ---------------------------------------------------------------------------


def _lookup(connection: Any, table: str, key: str) -> str | None:
    with connection() as conn:
        row = conn.execute("SELECT notion_page_id FROM notion_sync WHERE entity_table = %s "
                           "AND entity_id = %s", (table, key)).fetchone()
    return row[0] if row else None


def _record(connection: Any, table: str, key: str, notion_id: str) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, last_pushed_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (entity_table, entity_id)
            DO UPDATE SET notion_page_id = EXCLUDED.notion_page_id, last_pushed_at = now()
            """, (table, key, notion_id))


# --- the day's page ------------------------------------------------------------------------


def _source(client: Any, connection: Any) -> str:
    with connection() as conn:
        database_id = schema_id(conn, SCHEMA_KEY)
    sources = client.retrieve_database(database_id).get("data_sources") or []
    if not sources:
        raise RuntimeError("the Briefings database has no data source")
    source = sources[0]["id"]
    problems = schema_problems(client.retrieve_data_source(source).get("properties") or {})
    if problems:
        raise RuntimeError("the Briefings database isn't ready: " + "; ".join(problems))
    return source


def today_page(client: Any, connection: Any, day: date) -> str:
    """The day's Briefing page id, found or created, and recorded as
    `notion_sync ('briefing', day)`. A row already in Notion for that Date (made
    by hand, or by a run that failed before recording it) is adopted."""
    key = day.isoformat()
    page_id = _lookup(connection, SYNC_TABLE, key)
    if page_id:
        return page_id
    source = _source(client, connection)
    found = next(iter(client.iter_query(
        source, filter={"property": "Date", "date": {"equals": key}})), None)
    page_id = found["id"] if found else client.create_page(
        {"type": "data_source_id", "data_source_id": source}, page_properties(day))["id"]
    _record(connection, SYNC_TABLE, key, page_id)
    return page_id


def _append_all(client: Any, block_id: str, blocks: list[dict[str, Any]], *,
                after: str | None = None) -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []
    for i in range(0, len(blocks), MAX_CHILDREN):
        made += client.append_blocks(block_id, blocks[i:i + MAX_CHILDREN], after=after)
        if after and made:
            after = made[-1]["id"]
    return made


def append(client: Any, connection: Any, day: date, heading: str, text: str, *,
           key: str | None = None) -> None:
    """Append a heading-3 section to the end of the day's page. With `key`, the
    section is written once per day: a re-run finds `notion_sync ('briefing',
    '<day>:<key>')` and skips it."""
    once = f"{day.isoformat()}:{key}" if key else None
    if once and _lookup(connection, SYNC_TABLE, once):
        return
    made = _append_all(client, today_page(client, connection, day),
                       section_blocks(heading, text))
    if once and made:
        _record(connection, SYNC_TABLE, once, made[0]["id"])


# --- system notes and alerts ---------------------------------------------------------------


def _system_anchor(client: Any, connection: Any, day: date, page_id: str) -> str:
    """The last block of the day's System section, adding the heading if missing."""
    key = f"{day.isoformat()}:{SYSTEM_KEY}"
    anchor = _lookup(connection, SYNC_TABLE, key)
    if not anchor:
        anchor = client.append_blocks(page_id, [heading3(SYSTEM_HEADING)])[0]["id"]
        _record(connection, SYNC_TABLE, key, anchor)
    return anchor


def _load_notes(connection: Any, quarter: str) -> list[dict[str, str]]:
    with connection() as conn:
        row = conn.execute("SELECT synced_fields FROM notion_sync WHERE entity_table = %s "
                           "AND entity_id = %s",
                           (dash.SYNC_BLOCKS, f"{quarter}:{SYSTEM_KEY}")).fetchone()
    value = row[0] if row else None
    if isinstance(value, str):
        value = json.loads(value)
    return list((value or {}).get("notes") or [])


def _save_notes(connection: Any, quarter: str, notes: list[dict[str, str]]) -> None:
    with connection() as conn:
        conn.execute("UPDATE notion_sync SET synced_fields = %s::jsonb WHERE entity_table = %s "
                     "AND entity_id = %s",
                     (json.dumps({"notes": notes}), dash.SYNC_BLOCKS, f"{quarter}:{SYSTEM_KEY}"))


def refresh_system_callout(client: Any, connection: Any, now: datetime,
                           new: dict[str, str] | None = None) -> str:
    """Add `new` to the dashboard's System notes, drop those older than 24 hours, and
    rewrite the callout. Creates the callout after the attention callout if missing.
    Returns a status line."""
    quarter = kr_metrics.quarter_of(now.date())
    lookup, record = dashboard_page.recorder(connection, quarter)
    page_id, attention_id = lookup("page"), lookup("attention")
    if not page_id or not attention_id:
        return f"no {quarter} dashboard page yet; System callout not set"
    callout_id = lookup(SYSTEM_KEY)
    notes = _load_notes(connection, quarter) if callout_id else []
    notes = recent_notes([*notes, *([new] if new else [])], now)
    text = system_callout_text(notes)
    if callout_id:
        client.update_block(callout_id, {"callout": {"rich_text": rich_text(text)}})
    else:
        made = client.append_blocks(page_id, [dash.callout(SYSTEM_EMOJI, text)],
                                    after=attention_id)
        callout_id = made[0]["id"]
        record(SYSTEM_KEY, callout_id)
    _save_notes(connection, quarter, notes)
    return f"System callout shows {len(notes)} note(s)"


def system_note(client: Any, connection: Any, day: date, text: str, *,
                urgent: bool = False, now: datetime | None = None) -> None:
    """Record a warning: under "System" on the day's page, in the dashboard System
    callout, and, when `urgent`, as a comment that @-mentions the operator."""
    now = now or datetime.now().astimezone()
    page_id = today_page(client, connection, day)
    anchor = _system_anchor(client, connection, day, page_id)
    line = ("🚨 " if urgent else "") + note_line(now, text)
    made = client.append_blocks(page_id, [dash.paragraph(line)], after=anchor)
    if made:
        _record(connection, SYNC_TABLE, f"{day.isoformat()}:{SYSTEM_KEY}", made[-1]["id"])
    refresh_system_callout(client, connection, now, {"at": now.isoformat(), "text": text})
    if not urgent:
        return
    key = f"{day.isoformat()}:{content_hash(text)[:16]}"
    if _lookup(connection, SYNC_COMMENTS, key):
        return
    with connection() as conn:
        operator_id = schema_id(conn, OPERATOR_KEY)
    comment = client.create_comment(page_id, mention_text(operator_id, text))
    _record(connection, SYNC_COMMENTS, key, comment["id"])


# --- urgent reasons (database) -------------------------------------------------------------

_LOOPS_SQL = """
    SELECT agent_name, function_label,
           array_agg(status ORDER BY started_at DESC),
           (array_agg(error_text ORDER BY started_at DESC))[1]
    FROM (SELECT agent_name, function_label, status, error_text, started_at,
                 row_number() OVER (PARTITION BY agent_name, function_label
                                    ORDER BY started_at DESC) AS rn,
                 max(started_at) OVER (PARTITION BY agent_name, function_label) AS latest
          FROM agent_runs WHERE started_at > %s) r
    WHERE rn <= %s AND latest > %s
    GROUP BY 1, 2
"""

_SPEND_SQL = """
    SELECT COALESCE(sum(usd_cost), 0),
           COALESCE(sum(usd_cost) FILTER (WHERE agent_name = ANY(%s)), 0)
    FROM agent_runs WHERE started_at >= %s AND started_at < %s
"""


def urgent_reasons(conn: Any, now: datetime) -> list[str]:
    """Loops whose last two `agent_runs` failed, and spend ceilings hit yesterday or
    today (local days). `now` must be timezone-aware."""
    from agents._lib import runs

    since = now - timedelta(days=14)
    loops = [(a, f, list(s), e) for a, f, s, e in conn.execute(
        _LOOPS_SQL, (since, FAILS_IN_A_ROW, now - LOOP_RECENT)).fetchall()]
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spend = []
    for start in (start_today - timedelta(days=1), start_today):
        end = start + timedelta(days=1)
        total, groups = 0.0, {}
        for group, (_ceiling, members) in runs.CEILING_GROUPS.items():
            all_usd, group_usd = conn.execute(_SPEND_SQL, (list(members), start, end)).fetchone()
            total, groups[group] = float(all_usd), float(group_usd)
        if not groups:
            total = float(conn.execute(_SPEND_SQL, ([], start, end)).fetchone()[0])
        spend.append((start.date(), total, groups))
    return urgent_rules(
        sorted(loops), spend, global_ceiling=runs.GLOBAL_DAILY_CEILING,
        group_ceilings={g: c for g, (c, _m) in runs.CEILING_GROUPS.items()})


# --- Discord cutover (I7) ------------------------------------------------------------------


def surface_off(name: str) -> bool:
    """Whether Discord posting for `name` is switched off (`DISCORD_SURFACES_OFF`)."""
    from agents.discord_bot import config

    return name in getattr(config, "DISCORD_SURFACES_OFF", frozenset())
